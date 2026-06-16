#!/usr/bin/env node
import os from "node:os";
import path from "node:path";
import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(0, "utf8") || "{}");

function fail(message) {
  console.log(JSON.stringify({ success: false, message }));
  process.exit(0);
}

function failWithCode(code, message, extra = {}) {
  console.log(JSON.stringify({ success: false, code, message, ...extra }));
  process.exit(0);
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  fail("未安装 playwright，无法执行真实详情页核验。请在项目目录执行：npm install playwright");
}

const url = String(input.url || "").trim();
const itemId = String(input.item_id || "").trim();
if (!url) fail("缺少商品详情页 URL");

const profileDir = process.env.TAG_COLLECT_RPA_PROFILE
  || path.join(os.homedir(), ".sop-1688-rpa-profile");
const headless = process.env.TAG_COLLECT_RPA_HEADLESS === "1";
const loginWaitMs = Number(process.env.TAG_COLLECT_RPA_LOGIN_WAIT_MS || 90000);
const cdpUrl = process.env.TAG_COLLECT_CDP_URL || "";

function looksLikeBlockedPage(text, currentUrl) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /login\.1688\.com|login\.taobao\.com|login\.tmall\.com/.test(currentUrl)
    || /扫码登录|密码登录|手机登录|会员登录/.test(compact)
    || /安全验证|验证一下|滑块|请完成验证|访问受限|访问过于频繁|验证码|拖动下方滑块|验证失败|点击框体重试|error:2eDumg/.test(compact);
}

function looksLikeSecurityPage(text, currentUrl) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /安全验证|验证一下|滑块|请完成验证|访问受限|访问过于频繁|验证码|拖动下方滑块|验证失败|点击框体重试|error:2eDumg/.test(compact)
    || /punish|captcha|nocaptcha|sec|verify/.test(currentUrl);
}

function looksLikeLoginPage(text, currentUrl) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /login\.1688\.com|login\.taobao\.com|login\.tmall\.com/.test(currentUrl)
    || /扫码登录|密码登录|手机登录|会员登录/.test(compact);
}

function blockedMessage(currentUrl, waited) {
  const prefix = waited ? "已等待你处理登录/验证，但当前详情页仍需要登录或安全校验。" : "当前详情页需要登录或安全校验。";
  return `${prefix}不会写入样例字段。请先在真实 Chrome/1688 页面完成登录和验证后重试。当前页面：${currentUrl}`;
}

function securityMessage(currentUrl, waited) {
  const prefix = waited ? "已等待你手动处理 1688 安全验证，但当前详情页仍停留在滑块/验证码校验。" : "1688 详情页触发了安全滑块/验证码校验。";
  return `${prefix}系统不会绕过或自动破解验证，也不会写入任何详情字段。请在弹出的真实浏览器中手动完成验证，或使用已登录且已通过验证的 Chrome CDP 会话后重试。当前页面：${currentUrl}`;
}

async function openRuntime() {
  if (cdpUrl) {
    const browser = await chromium.connectOverCDP(cdpUrl);
    const context = browser.contexts()[0] || await browser.newContext({
      viewport: { width: 1440, height: 960 },
      locale: "zh-CN",
    });
    const page = await context.newPage();
    return { page, close: async () => page.close().catch(() => {}) };
  }
  const context = await chromium.launchPersistentContext(profileDir, {
    headless,
    viewport: { width: 1440, height: 960 },
    locale: "zh-CN",
  });
  const page = context.pages()[0] || await context.newPage();
  return { page, close: async () => context.close() };
}

const runtime = await openRuntime();
const page = runtime.page;
page.setDefaultTimeout(45000);

function firstMatch(text, patterns) {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return (match[1] || match[0] || "").trim();
  }
  return "";
}

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  let pageText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => "");
  const needsManualGate = looksLikeSecurityPage(pageText, page.url()) || looksLikeLoginPage(pageText, page.url());
  if (needsManualGate && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
    pageText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => pageText);
  }
  if (looksLikeBlockedPage(pageText, page.url())) {
    await runtime.close();
    console.log(JSON.stringify({
      success: false,
      code: looksLikeSecurityPage(pageText, page.url()) ? "security_verification_required" : "login_required",
      source: "1688_detail_page",
      cdp: Boolean(cdpUrl),
      item_id: itemId,
      url,
      page_url: page.url(),
      message: looksLikeSecurityPage(pageText, page.url())
        ? securityMessage(page.url(), needsManualGate && loginWaitMs > 0 && !headless)
        : blockedMessage(page.url(), needsManualGate && loginWaitMs > 0 && !headless),
    }));
    process.exit(0);
  }

  const text = String(pageText || "").replace(/\s+/g, " ").trim();
  const fields = {};
  fields.product_refund_rate = firstMatch(text, [
    /品退率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /品质退款率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
  ]);
  fields.shipment_rate = firstMatch(text, [
    /发货率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /准时发货率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
  ]);
  fields.collection_rate_24h = firstMatch(text, [
    /24\s*小时揽收率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /24h揽收率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/i,
  ]);
  fields.shipment_speed = firstMatch(text, [
    /(24小时内发货|48小时内发货|72小时内发货|[0-9]+小时发货|[0-9]+天内发货)/,
  ]);
  fields.wholesale_shipping_fee = firstMatch(text, [
    /(运费[^。；，,]{0,40}(?:元|包邮|另计))/,
    /(首重[^。；，,]{0,40})/,
  ]);
  fields.dropship_shipping_fee = fields.wholesale_shipping_fee;
  fields.free_shipping = /包邮/.test(text) ? "是" : "";
  fields.supports_dropship = /一件代发|代发/.test(text) ? "是" : "";
  fields.dropship_rights = fields.supports_dropship ? "页面出现一件代发/代发信息" : "";
  fields.return_exchange_support = firstMatch(text, [
    /(7天无理由[^。；，,]{0,20})/,
    /(支持退换[^。；，,]{0,20})/,
  ]);
  fields.rights_protection = firstMatch(text, [
    /(买家保障[^。；，,]{0,40})/,
    /(保障服务[^。；，,]{0,40})/,
    /(7天无理由[^。；，,]{0,20})/,
  ]);
  fields.min_order_range = firstMatch(text, [
    /([0-9]+件起批)/,
    /起批量[:：]?\s*([0-9]+[^ ]{0,8})/,
  ]);
  fields.shop_name = firstMatch(text, [
    /店铺[:：]?\s*([^ ]{2,40})/,
    /公司[:：]?\s*([^ ]{2,40})/,
  ]);
  fields.location = firstMatch(text, [
    /所在地[:：]?\s*([^ ]{2,20})/,
    /货源地[:：]?\s*([^ ]{2,20})/,
  ]);
  fields.company_type = firstMatch(text, [
    /(生产厂家|经销批发|招商代理|商业服务|个体经营)/,
  ]);
  fields.seller_member_type = firstMatch(text, [
    /(实力商家|超级工厂|诚信通)/,
  ]);
  fields.source_factory = /源头工厂|生产厂家|超级工厂/.test(text) ? "是" : "";
  fields.stock = firstMatch(text, [
    /库存[:：]?\s*([^ ]{1,30})/,
    /现货[:：]?\s*([^ ]{1,30})/,
  ]);
  fields.waybill_support = firstMatch(text, [
    /(电子面单[^。；，,]{0,30})/,
    /(面单[^。；，,]{0,30})/,
  ]);
  fields.video_query = /视频|主图视频/.test(text) ? "页面出现视频信息，待人工确认素材可用性" : "";

  const cleaned = Object.fromEntries(Object.entries(fields).filter(([, value]) => value));
  await runtime.close();
  console.log(JSON.stringify({
    success: true,
    source: "1688_detail_page",
    cdp: Boolean(cdpUrl),
    item_id: itemId,
    url,
    fields: cleaned,
  }));
} catch (error) {
  await runtime.close().catch(() => {});
  const message = error && error.message ? error.message : String(error);
  if (/Target page, context or browser has been closed|Browser has been closed|context.*closed|page.*closed/i.test(message)) {
    failWithCode(
      "browser_closed",
      "真实详情核验窗口已关闭或登录/验证未完成，未写入任何样例字段。请保持弹出的 1688/淘宝登录窗口打开并完成扫码验证后重试。",
      { source: "1688_detail_page", cdp: Boolean(cdpUrl), item_id: itemId, url }
    );
  }
  fail(message);
}
