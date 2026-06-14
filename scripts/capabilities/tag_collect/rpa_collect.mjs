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
  fail("未安装 playwright，无法执行真实页面 RPA。请在项目目录执行：npm install playwright");
}

const query = String(input.query || "").trim();
const sourceUrl = String(input.source_url || input.url || "").trim();
const limit = Math.max(1, Math.min(50, Number(input.limit || 20)));
if (!query && !sourceUrl) fail("缺少搜索词或 1688 页面 URL，无法打开真实页面");

const profileDir = process.env.TAG_COLLECT_RPA_PROFILE
  || path.join(os.homedir(), ".sop-1688-rpa-profile");
const headless = process.env.TAG_COLLECT_RPA_HEADLESS === "1";
const loginWaitMs = Number(process.env.TAG_COLLECT_RPA_LOGIN_WAIT_MS || 90000);
const cdpUrl = process.env.TAG_COLLECT_CDP_URL || "";

function looksLikeBlockedPage(text, url) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /login\.1688\.com|login\.taobao\.com|login\.tmall\.com/.test(url)
    || /扫码登录|密码登录|手机登录|会员登录/.test(compact)
    || /安全验证|验证一下|滑块|请完成验证|访问受限|访问过于频繁|验证码/.test(compact);
}

function looksLikeSecurityPage(text, url) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /login\.1688\.com|login\.taobao\.com|login\.tmall\.com/.test(url)
    || /安全验证|验证一下|滑块|请完成验证|访问受限|访问过于频繁|验证码/.test(compact);
}

function blockedMessage(url, waited) {
  const prefix = waited ? "已等待你处理登录/验证，但当前页面仍需要登录或安全校验。" : "当前页面需要登录或安全校验。";
  return `${prefix}请先在真实 Chrome/1688 页面完成登录和验证后重试；如果账号一直过不了校验，可以粘贴一个浏览器里能直接打开的 1688 搜索/商品链接做公开页面真实数据测试。当前页面：${url}`;
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

try {
  const url = sourceUrl || `https://s.1688.com/selloffer/offer_search.htm?keywords=${encodeURIComponent(query)}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);

  const pageText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => "");
  const needsLogin = looksLikeSecurityPage(pageText, page.url());
  if (needsLogin && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
  }

  await page.waitForTimeout(2500);
  await page.evaluate(() => window.scrollBy(0, Math.floor(window.innerHeight * 1.5))).catch(() => {});
  await page.waitForTimeout(1500);
  const products = await page.evaluate((maxItems) => {
    const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const pageUrl = location.href;
    const detailIdMatch = pageUrl.match(/offer\/(\d+)\.html/);
    const rows = [];
    const seen = new Set();

    if (detailIdMatch) {
      const bodyText = normalize(document.body?.innerText || "");
      const titleNode = document.querySelector("h1, [class*='title'], [title]");
      const title = normalize(
        titleNode?.getAttribute?.("title")
        || titleNode?.innerText
        || document.title.replace(/[-_].*$/, "")
      ).slice(0, 120);
      const priceMatch = bodyText.match(/(?:¥|￥)\s*(\d+(?:\.\d+)?(?:\s*[-~]\s*\d+(?:\.\d+)?)?)/)
        || bodyText.match(/\b(\d+(?:\.\d+)?)\s*元/);
      const imageNode = document.querySelector("img[src], img[data-src], img[data-lazy-src]");
      rows.push({
        id: detailIdMatch[1],
        title: title || `1688商品 ${detailIdMatch[1]}`,
        price: priceMatch ? priceMatch[1].replace(/\s+/g, "") : "-",
        image: imageNode
          ? (imageNode.currentSrc || imageNode.src || imageNode.getAttribute("data-src") || imageNode.getAttribute("data-lazy-src") || "")
          : "",
        url: `https://detail.1688.com/offer/${detailIdMatch[1]}.html`,
        stats: {
          rawText: bodyText.slice(0, 500),
          categoryListName: "",
        },
      });
      return rows.slice(0, maxItems);
    }

    const anchors = Array.from(document.querySelectorAll('a[href*="detail.1688.com/offer/"], a[href*="/offer/"]'));

    for (const anchor of anchors) {
      const hrefRaw = anchor.href || anchor.getAttribute("href") || "";
      const idMatch = hrefRaw.match(/offer\/(\d+)\.html/);
      if (!idMatch) continue;
      const id = idMatch[1];
      if (seen.has(id)) continue;

      const root = anchor.closest('[class*="offer"], [class*="item"], [class*="card"], [class*="product"], li')
        || anchor.parentElement
        || anchor;
      const rootText = normalize(root.innerText || anchor.innerText || "");
      const titleNode = root.querySelector("[title]") || anchor;
      const title = normalize(titleNode.getAttribute("title") || anchor.getAttribute("title") || anchor.innerText || rootText)
        .replace(/^(找相似|进店|立即订购|¥|￥).*/, "")
        .slice(0, 120);
      if (!title || title.length < 4) continue;

      const priceMatch = rootText.match(/(?:¥|￥)\s*(\d+(?:\.\d+)?(?:\s*[-~]\s*\d+(?:\.\d+)?)?)/)
        || rootText.match(/\b(\d+(?:\.\d+)?)\s*元/);
      const imageNode = root.querySelector("img");
      const image = imageNode
        ? (imageNode.currentSrc || imageNode.src || imageNode.getAttribute("data-src") || imageNode.getAttribute("data-lazy-src") || "")
        : "";

      seen.add(id);
      rows.push({
        id,
        title,
        price: priceMatch ? priceMatch[1].replace(/\s+/g, "") : "-",
        image,
        url: `https://detail.1688.com/offer/${id}.html`,
        stats: {
          rawText: rootText.slice(0, 500),
          categoryListName: "",
        },
      });
      if (rows.length >= maxItems) break;
    }
    return rows;
  }, limit);

  const latestText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => pageText);
  if (products.length === 0 && looksLikeBlockedPage(latestText, page.url())) {
    await runtime.close();
    console.log(JSON.stringify({
      success: false,
      code: "login_required",
      source: "1688_search_page",
      cdp: Boolean(cdpUrl),
      query,
      source_url: sourceUrl,
      page_url: page.url(),
      message: blockedMessage(page.url(), needsLogin && loginWaitMs > 0 && !headless),
    }));
    process.exit(0);
  }

  await runtime.close();
  console.log(JSON.stringify({
    success: true,
    source: "1688_search_page",
    cdp: Boolean(cdpUrl),
    query,
    source_url: sourceUrl,
    page_url: page.url(),
    products,
  }));
} catch (error) {
  await runtime.close().catch(() => {});
  const message = error && error.message ? error.message : String(error);
  if (/Target page, context or browser has been closed|Browser has been closed|context.*closed|page.*closed/i.test(message)) {
    failWithCode(
      "browser_closed",
      "真实采集窗口已关闭或登录/验证未完成，未生成任何数据。请保持弹出的 1688/淘宝登录窗口打开并完成扫码验证后重试；如果账号仍登录不上，可以粘贴浏览器里能打开的 1688 搜索页或商品详情页 URL 做公开页面真实数据测试。",
      { source: "1688_search_page", cdp: Boolean(cdpUrl), query, source_url: sourceUrl }
    );
  }
  fail(message);
}
