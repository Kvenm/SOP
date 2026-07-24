#!/usr/bin/env node
import os from "node:os";
import path from "node:path";
import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(0, "utf8") || "{}");
const selfTestMode = String(input.self_test || "").trim();

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
const limit = Math.max(1, Math.min(200, Number(input.limit || 120)));
const nativeFilters = Array.isArray(input.native_filters) ? input.native_filters : [];
const categoryFilters = Array.isArray(input.category_filters) ? input.category_filters : [];
const manualUrlOnly = Boolean(input.manual_url_only);
if (!selfTestMode && !manualUrlOnly && !query && !sourceUrl && !categoryFilters.length) fail("缺少搜索词、类目或 1688 页面 URL，无法打开真实页面");

const profileDir = process.env.TAG_COLLECT_RPA_PROFILE
  || path.join(os.homedir(), ".sop-1688-rpa-profile");
const headless = process.env.TAG_COLLECT_RPA_HEADLESS === "1";
const cdpUrl = process.env.TAG_COLLECT_CDP_URL || "";
const loginWaitMs = Number(process.env.TAG_COLLECT_RPA_LOGIN_WAIT_MS || (cdpUrl ? 15000 : 30000));
const pageTimeoutMs = Number(process.env.TAG_COLLECT_RPA_PAGE_TIMEOUT_MS || 30000);
const manualVerificationWaitMs = Math.max(0, Number(process.env.TAG_COLLECT_RPA_MANUAL_VERIFY_WAIT_MS || 300000));
const pacingMode = String(process.env.TAG_COLLECT_RPA_PACING || "human").toLowerCase();
const categoryScrollMaxSteps = Math.max(
  1,
  Math.min(30, Number(input.category_scroll_max_steps || process.env.TAG_COLLECT_RPA_CATEGORY_SCROLL_STEPS || 18) || 18)
);
const categoryCascadePauseRange = selfTestMode
  ? [20, 45]
  : (pacingMode === "human" ? [120, 320] : [60, 140]);
const pacingProfiles = {
  fast: {
    afterGoto: [1800, 3200],
    beforeSearchInput: [600, 1400],
    afterSearchSubmit: [1800, 3200],
    beforeFilterClick: [500, 1200],
    afterFilterClick: [1600, 2800],
    beforeScroll: [1400, 2600],
    scrollStep: [650, 1500],
    beforeExtract: [1800, 3200],
  },
  human: {
    afterGoto: [4500, 8500],
    beforeSearchInput: [1200, 2800],
    afterSearchSubmit: [3500, 7000],
    beforeFilterClick: [1400, 3600],
    afterFilterClick: [3000, 6500],
    beforeScroll: [2500, 5200],
    scrollStep: [1200, 3200],
    beforeExtract: [3500, 7000],
  },
};
const pacing = pacingProfiles[pacingMode] || pacingProfiles.human;
const productCardSelector = [
  'a[href*="detail.1688.com/offer/"]',
  'a[href*="/offer/"]',
  'a[href*="offerId="]',
  'a[href*="offerIds="]',
  'a[href*="detail.m.1688.com/page/index.html"]',
  'a.search-offer-wrapper',
  'a[class*="search-offer"]',
  '[class*="search-offer-item"]',
  '[data-offer-id]',
  '[data-offerid]',
].join(", ");
const estimatedResultPages = Math.min(12, Math.max(3, Math.ceil(limit / 24) + 1));
const configuredResultPages = Number(input.max_pages || process.env.TAG_COLLECT_RPA_MAX_PAGES || estimatedResultPages);
const maxResultPages = Math.max(
  1,
  manualUrlOnly ? 1 : Math.min(12, configuredResultPages || estimatedResultPages)
);
const rpaProgressPrefix = "SOP_RPA_PROGRESS ";
let lastCategoryPointer = null;

function emitRpaProgress(stage, message, details = {}) {
  process.stderr.write(`${rpaProgressPrefix}${JSON.stringify({
    stage,
    message,
    ...details,
    updated_at: new Date().toISOString(),
  })}\n`);
}

let overlayState = {
  tone: "working",
  title: "1688 选品采集",
  detail: "准备连接采集页面",
  meta: "",
};

async function updateWorkbenchOverlay(page, patch = {}) {
  overlayState = { ...overlayState, ...patch };
  if (!page) return;
  await page.evaluate((state) => {
    const hostId = "sop-1688-workbench-overlay";
    let host = document.getElementById(hostId);
    if (!host) {
      host = document.createElement("div");
      host.id = hostId;
      host.setAttribute("data-sop-workbench-overlay", "true");
      host.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;pointer-events:none;";
      const shadow = host.attachShadow({ mode: "open" });
      shadow.innerHTML = `
        <style>
          .card{box-sizing:border-box;width:min(360px,calc(100vw - 36px));padding:13px 15px;border-radius:12px;background:rgba(23,28,37,.94);color:#fff;box-shadow:0 12px 36px rgba(0,0,0,.28);font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;backdrop-filter:blur(10px)}
          .head{display:flex;align-items:center;gap:9px;font-weight:700}.dot{width:9px;height:9px;border-radius:50%;background:#4ea1ff;box-shadow:0 0 0 4px rgba(78,161,255,.18)}
          .detail{margin-top:7px;color:#eef4ff}.meta{margin-top:5px;color:#aebbd0;font-size:12px;white-space:pre-wrap}.waiting .dot{background:#f5b942;box-shadow:0 0 0 4px rgba(245,185,66,.2)}.done .dot{background:#39c37d;box-shadow:0 0 0 4px rgba(57,195,125,.18)}.error .dot{background:#ff6868;box-shadow:0 0 0 4px rgba(255,104,104,.18)}
        </style>
        <div class="card"><div class="head"><span class="dot"></span><span data-title></span></div><div class="detail" data-detail></div><div class="meta" data-meta></div></div>`;
      (document.body || document.documentElement).appendChild(host);
    }
    const shadow = host.shadowRoot;
    const card = shadow?.querySelector(".card");
    if (card) card.className = `card ${state.tone || "working"}`;
    const title = shadow?.querySelector("[data-title]");
    const detail = shadow?.querySelector("[data-detail]");
    const meta = shadow?.querySelector("[data-meta]");
    if (title) title.textContent = state.title || "1688 选品采集";
    if (detail) detail.textContent = state.detail || "";
    if (meta) meta.textContent = state.meta || "";
  }, overlayState).catch(() => {});
}

function jitter([min, max]) {
  const low = Math.max(0, Number(min) || 0);
  const high = Math.max(low, Number(max) || low);
  return Math.round(low + Math.random() * (high - low));
}

async function humanPause(page, range) {
  await page.waitForTimeout(jitter(range));
}

async function categoryCascadePause(page) {
  await page.waitForTimeout(jitter(categoryCascadePauseRange));
}

async function humanScroll(page) {
  await humanPause(page, pacing.beforeScroll);
  const steps = 2 + Math.floor(Math.random() * 3);
  for (let i = 0; i < steps; i += 1) {
    await page.evaluate(() => {
      const distance = Math.floor(window.innerHeight * (0.45 + Math.random() * 0.45));
      window.scrollBy({ top: distance, left: 0, behavior: "smooth" });
    }).catch(() => {});
    await humanPause(page, pacing.scrollStep);
  }
}

async function waitForProductAnchors(page, query, sourceUrl, context = {}) {
  const maxWaitMs = Math.min(pageTimeoutMs, 30000);
  const deadline = Date.now() + maxWaitMs;
  let lastState = { count: 0, body_head: "" };
  while (Date.now() < deadline) {
    await failIfStillBlocked(page, query, sourceUrl, false);
    lastState = await page.evaluate((selector) => ({
      count: document.querySelectorAll(selector).length,
      title: document.title || "",
      keyword: document.querySelector('input[name="keywords"], #alisearch-input')?.value || "",
      body_head: (document.body?.innerText || "").slice(0, 300),
    }), productCardSelector).catch(() => lastState);
    if (lastState.count > 0) return lastState;
    await page.waitForTimeout(1200);
  }
  const isCategoryNavigation = Boolean(context.category_path);
  const filterResults = Array.isArray(context.filter_results) ? context.filter_results : [];
  const diagnostics = context.diagnostics && typeof context.diagnostics === "object" ? context.diagnostics : {};
  await runtime.close().catch(() => {});
  failWithCode(
    isCategoryNavigation ? "category_navigation_not_loaded" : "search_results_not_loaded",
    isCategoryNavigation
      ? `1688 已执行类目导航，但未进入可解析的商品列表页，未生成任何数据。请在真实 Chrome 中人工确认该类目能看到商品卡片后再查询；当前类目：${context.category_path}`
      : `1688 已打开搜索页，但未在页面中发现商品列表链接，未生成任何数据。请在真实 Chrome 中人工确认该搜索页能看到商品卡片后再查询；当前搜索词：${query || "-"}`,
    {
      source: isCategoryNavigation ? "1688_category_navigation" : "1688_search_page",
      cdp: Boolean(cdpUrl),
      query,
      source_url: sourceUrl,
      page_url: page.url(),
      category_path: context.category_path || "",
      search_result_state: lastState,
      filter_results: filterResults,
      diagnostics,
    }
  );
}

function looksLikeBlockedPage(text, url) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /login\.1688\.com|login\.taobao\.com|login\.tmall\.com/.test(url)
    || /扫码登录|密码登录|手机登录|会员登录/.test(compact)
    || /安全验证|验证一下|滑块|请完成验证|访问受限|访问过于频繁|验证码|拖动下方滑块|验证失败|点击框体重试|error:2eDumg/.test(compact);
}

function looksLikeSecurityPage(text, url) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /安全验证|验证一下|滑块|请完成验证|访问受限|访问过于频繁|验证码|拖动下方滑块|验证失败|点击框体重试|error:2eDumg/.test(compact)
    || /punish|captcha|nocaptcha|sec|verify/.test(url);
}

function looksLikeLoginPage(text, url) {
  const compact = String(text || "").replace(/\s+/g, "");
  return /login\.1688\.com|login\.taobao\.com|login\.tmall\.com/.test(url)
    || /扫码登录|密码登录|手机登录|会员登录/.test(compact);
}

function looksLikeMojibake(value) {
  const text = String(value || "");
  return /[ÃÂ�]|(?:\\u00[0-9a-fA-F]{2})/.test(text)
    || (/[åæçèéäöü]/i.test(text) && /[一-龥]/.test(text) === false && text.length > 8);
}

function cleanProductTitle(value) {
  const text = String(value || "")
    .replace(/\s+/g, " ")
    .replace(/^(找相似|进店|立即订购|加入进货单|收藏|¥|￥)\s*/g, "")
    .trim();
  if (!text || looksLikeMojibake(text)) return "";
  const lines = text.split(/[\n\r|｜]+/).map((item) => item.trim()).filter(Boolean);
  const candidates = (lines.length ? lines : [text])
    .map((item) => item.replace(/^(找相似|进店|立即订购|加入进货单|收藏|¥|￥)\s*/g, "").trim())
    .filter((item) => item.length >= 4 && item.length <= 140)
    .filter((item) => /[一-龥A-Za-z]/.test(item))
    .filter((item) => !/^(¥|￥)?\d+(?:\.\d+)?(?:元|起)?$/.test(item))
    .filter((item) => !/(成交|评价|回头率|复购率|发货|物流|包邮|起批|付款|买家保障|找相似|进店|立即订购)/.test(item.slice(0, 30)))
    .filter((item) => !looksLikeMojibake(item));
  return (candidates[0] || "").slice(0, 120);
}

function blockedMessage(_url, waited) {
  const prefix = waited ? "已等待你处理登录/验证，但当前页面仍需要登录或安全校验。" : "当前页面需要登录或安全校验。";
  return `${prefix}验证页面已保留在采集 Chrome 中。请先在该页面完成人工登录或验证，确认搜索页恢复正常后，再回到工作台重新开始采集。`;
}

function normalizeUrlForMatch(value) {
  return String(value || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
}

function isCollectable1688Url(value) {
  const pageUrl = String(value || "");
  let parsed;
  try {
    parsed = new URL(pageUrl);
  } catch {
    return false;
  }
  const host = parsed.hostname || "";
  const pathName = parsed.pathname || "";
  const queryString = parsed.search || "";
  if (!(host === "1688.com" || host.endsWith(".1688.com"))) return false;
  if (/login|punish|verify|sec|captcha/i.test(pageUrl)) return false;
  if (/detail\.(m\.)?1688\.com/i.test(host)) return true;
  if (host === "s.1688.com" && /selloffer|offer_search/i.test(pathName)) return true;
  if (/offer/i.test(pathName) && /offerId|keywords/i.test(queryString)) return true;
  if (/keywords=|keyword=|offerId=|offerIds=|categoryId=|catId=/i.test(queryString)) return true;
  return false;
}

async function pickManualPage(context, expectedUrl) {
  const pages = context.pages();
  const expected = normalizeUrlForMatch(expectedUrl);
  const candidates = pages.filter((candidate) => /(^|\.)1688\.com/i.test(candidate.url() || ""));
  const readableCandidates = candidates.filter((candidate) => isCollectable1688Url(candidate.url()));
  if (expected) {
    const matched = readableCandidates.find((candidate) => normalizeUrlForMatch(candidate.url()).includes(expected) || expected.includes(normalizeUrlForMatch(candidate.url())));
    if (matched) return matched;
    failWithCode(
      "manual_page_not_open",
      "人工页面读取模式不会自动打开或刷新 1688。请先在真实 Chrome 中手动打开该 1688 页面，并保持标签页打开后再查询。",
      { source: "manual_page", cdp: Boolean(cdpUrl), source_url: expectedUrl }
    );
  }
  if (readableCandidates.length === 1) return readableCandidates[0];
  if (readableCandidates.length > 1) {
    failWithCode(
      "manual_page_ambiguous",
      "采集 Chrome 中检测到多个可读取的 1688 页签。为避免读错页面，请只保留本次要采集的页签，或在本地页面的可选 URL 框粘贴目标页地址后再读取。",
      {
        source: "manual_page",
        cdp: Boolean(cdpUrl),
        source_url: expectedUrl,
        pages: readableCandidates.map((candidate) => ({ title: "", url: candidate.url() })).slice(0, 10),
      }
    );
  }
  failWithCode(
    "manual_page_not_open",
    "人工页面读取模式需要真实 Chrome 中已有一个可访问的 1688 商品列表或详情页。当前没有找到已打开的可读取页面。",
    { source: "manual_page", cdp: Boolean(cdpUrl), source_url: expectedUrl }
  );
}

function securityMessage(_url, waited) {
  const prefix = waited ? "已等待你手动处理 1688 安全验证，但当前页面仍停留在滑块/验证码校验。" : "1688 触发了安全滑块/验证码校验。";
  return `${prefix}系统不会绕过或自动破解验证。已达到本次人工等待时限，验证页面会保留在采集 Chrome 中；已有成功类目的结果仍会按部分完成规则导出。`;
}

async function readBodyText(page, fallback = "") {
  return page.locator("body").innerText({ timeout: 10000 }).catch(() => fallback);
}

async function waitForManualSecurityResolution(page, query, sourceUrl) {
  if (headless || manualVerificationWaitMs <= 0) return { resumed: false, waited_ms: 0 };
  const startedAt = Date.now();
  const deadline = startedAt + manualVerificationWaitMs;
  const alertKey = `security-${startedAt}`;
  emitRpaProgress(
    "manual_intervention",
    "1688 出现滑块或验证码，请在采集 Chrome 中人工完成验证；完成后同一任务会自动继续。",
    { alert_key: alertKey, percent: 35, intervention_type: "security_verification" }
  );
  await page.bringToFront().catch(() => {});
  while (Date.now() < deadline) {
    const elapsed = Date.now() - startedAt;
    const remainingSeconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    await updateWorkbenchOverlay(page, {
      tone: "waiting",
      title: "采集任务等待人工处理",
      detail: "请在当前页面完成人工校验，完成后同一任务会自动继续。",
      meta: `已等待 ${Math.floor(elapsed / 1000)} 秒 · 剩余约 ${remainingSeconds} 秒\n类目/关键词：${categoryFilters.map((item) => item.category_path || item.tag || "").filter(Boolean).join("、") || query || "-"}`,
    });
    await page.waitForTimeout(1200);
    const text = await readBodyText(page);
    if (!looksLikeSecurityPage(text, page.url())) {
      emitRpaProgress(
        "manual_intervention_resolved",
        "人工验证已完成，正在继续同一采集任务。",
        { alert_key: alertKey, percent: 38, intervention_type: "security_verification" }
      );
      await updateWorkbenchOverlay(page, {
        tone: "working",
        title: "人工处理已完成",
        detail: "页面已恢复，正在继续同一采集任务。",
        meta: `本次等待 ${Math.round((Date.now() - startedAt) / 1000)} 秒`,
      });
      await page.waitForTimeout(700);
      return { resumed: true, waited_ms: Date.now() - startedAt, page_url: page.url(), query, source_url: sourceUrl };
    }
  }
  return { resumed: false, waited_ms: Date.now() - startedAt, page_url: page.url() };
}

async function failIfStillBlocked(page, query, sourceUrl, waited) {
  let text = await readBodyText(page);
  let url = page.url();
  if (looksLikeSecurityPage(text, url)) {
    const manualResult = await waitForManualSecurityResolution(page, query, sourceUrl);
    if (manualResult.resumed) return readBodyText(page, text);
    text = await readBodyText(page, text);
    url = page.url();
    await runtime.releaseForManualHandoff().catch(() => {});
    failWithCode("security_verification_required", securityMessage(url, waited), {
      source: "1688_search_page",
      cdp: Boolean(cdpUrl),
      query,
      source_url: sourceUrl,
      page_url: url,
      manual_waited_ms: manualResult.waited_ms,
      manual_wait_timeout_ms: manualVerificationWaitMs,
    });
  }
  if (looksLikeLoginPage(text, url)) {
    emitRpaProgress(
      "manual_intervention",
      "1688 登录状态失效，请在采集 Chrome 中人工登录后重新开始任务。",
      { alert_key: `login-${Date.now()}`, percent: 35, intervention_type: "login" }
    );
    await runtime.releaseForManualHandoff().catch(() => {});
    failWithCode("login_required", blockedMessage(url, waited), {
      source: "1688_search_page",
      cdp: Boolean(cdpUrl),
      query,
      source_url: sourceUrl,
      page_url: url,
    });
  }
  return text;
}

async function openRuntime() {
  if (cdpUrl) {
    let browser;
    try {
      browser = await chromium.connectOverCDP(cdpUrl, { noDefaults: true, timeout: 15000 });
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      failWithCode(
        "cdp_context_unsupported",
        "真实 Chrome CDP 端口能连接，但当前调试端点不支持 Playwright 初始化上下文。请在本地工作台点击“启动采集 Chrome”重启项目专用 Chrome；如果仍失败，先确认 9222 没有被其它浏览器/工具占用。",
        {
          source: "chrome_cdp",
          cdp: Boolean(cdpUrl),
          cdp_url: cdpUrl,
          low_level_error: message,
        }
      );
    }
    const context = browser.contexts()[0] || await browser.newContext({
      viewport: { width: 1440, height: 960 },
      locale: "zh-CN",
    });
    const page = manualUrlOnly ? await pickManualPage(context, sourceUrl) : await context.newPage();
    await page.bringToFront().catch(() => {});
    const runtimeState = { page };
    runtimeState.close = async () => {
      if (manualUrlOnly) {
        await browser.close().catch(() => {});
        return;
      }
      await runtimeState.page?.close().catch(() => {});
      await browser.close().catch(() => {});
    };
    runtimeState.releaseForManualHandoff = async () => {
      if (manualUrlOnly) {
        await browser.close().catch(() => {});
        return;
      }
      await runtimeState.page?.bringToFront().catch(() => {});
      // Do not close the CDP page here. The Node process will exit and release
      // the Playwright connection while the real Chrome tab remains available
      // for the user to complete login, slider, or captcha verification.
    };
    return runtimeState;
  }
  if (manualUrlOnly) {
    failWithCode(
      "manual_page_requires_cdp",
      "人工页面读取模式不会自动访问 1688。请先在本地工作台点击“启动采集 Chrome”，并在该 Chrome 中手动打开 1688 页面。",
      { source: "manual_page", cdp: false, source_url: sourceUrl }
    );
  }
  const context = await chromium.launchPersistentContext(profileDir, {
    headless,
    viewport: { width: 1440, height: 960 },
    locale: "zh-CN",
  });
  const page = context.pages()[0] || await context.newPage();
  return {
    page,
    close: async () => context.close(),
    releaseForManualHandoff: async () => context.close(),
  };
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, "").trim();
}

function normalizeCategoryText(value) {
  return String(value || "")
    .replace(/[\uE000-\uF8FF]/g, "")
    .replace(/[\s、，,\/／|｜·•\-]+/g, "")
    .trim();
}

function normalizeOfficialCategoryId(value) {
  const text = String(value || "").trim();
  return /^\d{1,32}$/.test(text) ? text : "";
}

function extractOfficialCategoryId(...values) {
  for (const value of values) {
    const raw = String(value || "").trim();
    if (!raw) continue;
    const direct = normalizeOfficialCategoryId(raw);
    if (direct) return direct;
    const variants = [raw];
    try {
      variants.push(decodeURIComponent(raw));
    } catch {
      // Keep the raw value only.
    }
    for (const text of variants) {
      try {
        const parsed = new URL(text, "https://www.1688.com/");
        for (const [key, candidate] of parsed.searchParams.entries()) {
          if (["categoryid", "catid", "cateid", "category_id", "cat_id", "cate_id"].includes(key.toLowerCase())) {
            const normalized = normalizeOfficialCategoryId(candidate);
            if (normalized) return normalized;
          }
        }
      } catch {
        // Non-URL values may still contain a category key below.
      }
      const match = text.match(/(?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)[^0-9]{0,12}(\d{1,32})/i)
        || text.match(/[?&#](?:categoryId|catId|cateId|category_id|cat_id|cate_id)=(\d{1,32})/i);
      if (match) return match[1];
    }
  }
  return "";
}

function withOfficialCategoryId(result = {}) {
  return {
    ...result,
    category_id: extractOfficialCategoryId(
      result.category_id,
      result.category_id_hint,
      result.href,
      result.page_url,
      result.page_url_after
    ),
  };
}

async function gotoWithTimeout(page, url, query, sourceUrl, purpose) {
  try {
    await updateWorkbenchOverlay(page, {
      tone: "working",
      detail: `正在打开${purpose}`,
      meta: query ? `关键词：${query}` : "",
    });
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: pageTimeoutMs });
    await updateWorkbenchOverlay(page, {
      tone: "working",
      detail: `${purpose}已打开，正在检查页面状态`,
      meta: page.url(),
    });
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    await runtime.close().catch(() => {});
    failWithCode(
      "navigation_timeout",
      `1688 页面加载超时，未生成任何数据。请先在真实 Chrome 中人工打开${purpose}确认能正常访问，再回到本工具查询。${message}`,
      { source: "1688_search_page", cdp: Boolean(cdpUrl), query, source_url: sourceUrl, page_url: page.url() }
    );
  }
}

async function findSearchInput(page) {
  return page.evaluate(() => {
    const compact = (value) => String(value || "").replace(/\s+/g, "").trim().toLowerCase();
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 20 && rect.height > 10 && style.visibility !== "hidden" && style.display !== "none";
    };
    const inputs = Array.from(document.querySelectorAll("input, textarea"))
      .filter((node) => {
        const type = compact(node.getAttribute("type"));
        return !["hidden", "password", "checkbox", "radio", "file", "submit", "button"].includes(type)
          && !node.disabled
          && !node.readOnly
          && visible(node);
      })
      .map((node, index) => {
        const attrs = compact([
          node.getAttribute("name"),
          node.getAttribute("id"),
          node.getAttribute("class"),
          node.getAttribute("placeholder"),
          node.getAttribute("aria-label"),
          node.getAttribute("title"),
          node.value,
        ].filter(Boolean).join(" "));
        let score = 0;
        if (/keyword|keywords|q|search|query/.test(attrs)) score += 50;
        if (/搜索|搜货源|找货源|关键词|商品/.test(attrs)) score += 60;
        if (compact(node.getAttribute("name")) === "keywords") score += 100;
        if (compact(node.getAttribute("id")).includes("search")) score += 20;
        if (node.tagName.toLowerCase() === "textarea") score -= 10;
        return { node, index, score, attrs };
      })
      .sort((a, b) => b.score - a.score || a.index - b.index);
    const selected = inputs.find((item) => item.score > 0) || inputs[0];
    if (!selected) return { ok: false, reason: "未找到可输入的 1688 搜索框" };
    selected.node.setAttribute("data-sop-search-input", "1");
    selected.node.scrollIntoView({ block: "center", inline: "center" });
    selected.node.focus();
    selected.node.value = "";
    selected.node.dispatchEvent(new Event("input", { bubbles: true }));
    selected.node.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, score: selected.score, attrs: selected.attrs };
  });
}

async function readSearchKeywordState(page, expectedQuery) {
  return page.evaluate(({ expectedQuery }) => {
    const compact = (value) => String(value || "").replace(/\s+/g, "").trim();
    const looksLikeMojibake = (value) => {
      const text = String(value || "");
      return /[ÃÂ�]|(?:\\u00[0-9a-fA-F]{2})/.test(text)
        || (/[åæçèéäöü]/i.test(text) && /[一-龥]/.test(text) === false && text.length > 8);
    };
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 20 && rect.height > 10 && style.visibility !== "hidden" && style.display !== "none";
    };
    const inputValues = Array.from(document.querySelectorAll("input, textarea"))
      .filter((node) => visible(node))
      .map((node) => String(node.value || node.getAttribute("value") || "").trim())
      .filter(Boolean)
      .slice(0, 12);
    const urlValues = [];
    try {
      const current = new URL(location.href);
      for (const key of ["keywords", "keyword", "q", "key", "searchText"]) {
        const value = current.searchParams.get(key);
        if (value) urlValues.push(value);
      }
    } catch {
      // Ignore malformed URLs; page_url is returned separately for diagnosis.
    }
    const title = document.title || "";
    // 1688 search URLs may keep Chinese keywords in GBK-style percent encoding.
    // Treat visible input/title as the source of truth for mojibake checks.
    const visibleValues = [...inputValues, title].filter(Boolean);
    const values = [...visibleValues, ...urlValues].filter(Boolean);
    const expected = compact(expectedQuery);
    return {
      page_url: location.href,
      title,
      input_values: inputValues,
      url_values: urlValues,
      has_expected_query: Boolean(expected) && values.some((value) => compact(value).includes(expected)),
      mojibake_values: visibleValues.filter((value) => looksLikeMojibake(value)).slice(0, 5),
    };
  }, { expectedQuery });
}

async function failIfSearchKeywordMismatch(page, expectedQuery, sourceUrl, stage) {
  if (!expectedQuery || sourceUrl) return;
  const state = await readSearchKeywordState(page, expectedQuery);
  const hasCjkQuery = /[一-龥]/.test(expectedQuery);
  if (state.mojibake_values.length || (hasCjkQuery && !state.has_expected_query)) {
    await runtime.close().catch(() => {});
    failWithCode(
      "search_keyword_encoding_error",
      `1688 搜索词校验失败：期望搜索「${expectedQuery}」，但页面没有确认到原始中文关键词，或出现了疑似乱码。已停止采集，避免导出错误关键词的数据。`,
      {
        source: "1688_search_page",
        cdp: Boolean(cdpUrl),
        query: expectedQuery,
        source_url: sourceUrl,
        page_url: state.page_url,
        search_stage: stage,
        search_keyword_state: state,
      }
    );
  }
}

async function clickSearchSubmitIfNeeded(page) {
  return page.evaluate(() => {
    const compact = (value) => String(value || "").replace(/\s+/g, "").trim();
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const active = document.activeElement;
    const form = active && active.closest ? active.closest("form") : null;
    if (form) {
      const buttons = Array.from(form.querySelectorAll("button, input[type='submit'], a"))
        .filter((node) => visible(node));
      const button = buttons.find((node) => /搜索|搜货源|找货源/.test(compact(node.innerText || node.value || node.getAttribute("aria-label") || "")))
        || buttons.find((node) => compact(node.innerText || node.value || node.getAttribute("aria-label") || ""));
      if (button) {
        button.click();
        return { clicked: true, method: "form_button" };
      }
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
        return { clicked: true, method: "request_submit" };
      }
      form.submit();
      return { clicked: true, method: "form_submit" };
    }
    const nodes = Array.from(document.querySelectorAll("button, input[type='submit'], a, span, div"))
      .filter((node) => visible(node));
    const button = nodes.find((node) => /^(搜索|搜货源|找货源)$/.test(compact(node.innerText || node.value || node.getAttribute("aria-label") || "")))
      || nodes.find((node) => /搜索|搜货源|找货源/.test(compact(node.innerText || node.value || node.getAttribute("aria-label") || "")));
    if (button) {
      const clickable = button.closest("button, a, [role='button']") || button;
      clickable.click();
      return { clicked: true, method: "global_button" };
    }
    return { clicked: false, method: "none" };
  });
}

async function pickSearchResultPage(context, pagesBefore, expectedQuery, fallbackPage) {
  const expected = normalizeText(expectedQuery);
  const candidates = context.pages().filter((item) => !pagesBefore.has(item) || item !== fallbackPage);
  for (const candidate of candidates.reverse()) {
    const url = candidate.url();
    const title = await candidate.title().catch(() => "");
    if (
      /s\.1688\.com\/selloffer\/offer_search\.htm/.test(url)
      || (expected && normalizeText(title).includes(expected))
    ) {
      return candidate;
    }
  }
  return fallbackPage;
}

async function runSearchActionAndFollowResult(page, action) {
  const context = page.context();
  const pagesBefore = new Set(context.pages());
  const popupPromise = context.waitForEvent("page", { timeout: 6000 }).catch(() => null);
  await action();
  const popup = await popupPromise;
  const nextPage = popup || await pickSearchResultPage(context, pagesBefore, query, page);
  await nextPage.waitForLoadState("domcontentloaded", { timeout: Math.min(pageTimeoutMs, 15000) }).catch(() => {});
  if (nextPage !== page) {
    await nextPage.bringToFront().catch(() => {});
    await page.close().catch(() => {});
    runtime.page = nextPage;
    nextPage.setDefaultTimeout(Math.max(15000, Math.min(45000, pageTimeoutMs)));
  }
  return nextPage;
}

function categoryNeedles(part) {
  const raw = String(part || "").trim();
  const pieces = raw.includes("/") || raw.includes("／")
    ? raw.split(/[\/／]/).map((item) => item.trim()).filter(Boolean)
    : [];
  const aliases = {
    "睡衣家居服": ["睡衣/家居服"],
    "男包/双肩": ["男包双肩"],
    "杂粮/油": ["杂粮油"],
    "防护、包装": ["防护包装"],
    "电工电气": ["电工电气"],
  };
  const aliasItems = aliases[raw] || [];
  return [...new Set([raw, ...pieces, ...aliasItems].filter(Boolean))];
}

function categoryNeedleSets(parts) {
  const original = Array.isArray(parts) ? parts.map((item) => String(item || "").trim()).filter(Boolean) : [];
  if (!original.length) return [];
  const sets = original.map(categoryNeedles);
  return sets;
}

function categoryDisplayNeedles(part) {
  return categoryNeedles(part)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function followCategoryClick(page, action) {
  const context = page.context();
  const pagesBefore = new Set(context.pages());
  const popupPromise = context.waitForEvent("page", { timeout: 5000 }).catch(() => null);
  const beforeUrl = page.url();
  const actionResult = await action();
  if (!actionResult?.ok) {
    return {
      page,
      action_result: actionResult,
      url_changed: false,
      opened_page: false,
      before_url: beforeUrl,
    };
  }
  const popup = await popupPromise;
  const nextPage = popup || page;
  await nextPage.waitForLoadState("domcontentloaded", { timeout: Math.min(pageTimeoutMs, 15000) }).catch(() => {});
  await humanPause(nextPage, pacing.afterFilterClick);

  const openedPage = popup || context.pages().find((item) => !pagesBefore.has(item) && item !== page);
  if (openedPage && openedPage !== page) {
    await openedPage.bringToFront().catch(() => {});
    await page.close().catch(() => {});
    runtime.page = openedPage;
    openedPage.setDefaultTimeout(Math.max(15000, Math.min(45000, pageTimeoutMs)));
    return { page: openedPage, action_result: actionResult, url_changed: true, opened_page: true, before_url: beforeUrl };
  }
  return { page: nextPage, action_result: actionResult, url_changed: nextPage.url() !== beforeUrl, opened_page: false, before_url: beforeUrl };
}

async function collectCategoryDiagnostics(page, needles = []) {
  return page.evaluate(({ needles }) => {
    const compact = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/[\s、，,\/／|｜·•\-]+/g, "")
      .trim();
    const visible = (el) => {
      if (!el || typeof el.getBoundingClientRect !== "function") return false;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const roots = Array.from(document.querySelectorAll(
      "[class*='category'], [class*='cate'], [class*='menu'], [class*='nav'], [class*='filter'], [class*='industry'], [class*='lv1'], [class*='lv2'], [class*='cBox'], [class*='cTitle'], aside, nav"
    )).filter((node) => visible(node));
    const sourceRoots = roots.length ? roots : [document.body].filter(Boolean);
    const texts = [];
    const seen = new Set();
    for (const root of sourceRoots) {
      const nodes = [root, ...Array.from(root.querySelectorAll?.("a, button, li, span, div") || [])];
      for (const node of nodes) {
        if (!visible(node)) continue;
        const text = compact(
          node.innerText
          || node.textContent
          || node.getAttribute?.("title")
          || node.getAttribute?.("aria-label")
          || ""
        );
        if (!text || text.length > 60 || seen.has(text)) continue;
        seen.add(text);
        texts.push(text);
        if (texts.length >= 40) break;
      }
      if (texts.length >= 40) break;
    }
    return {
      page_url: location.href,
      title: document.title || "",
      needles,
      visible_category_texts: texts,
      body_head: compact(document.body?.innerText || "").slice(0, 260),
    };
  }, { needles }).catch((error) => ({
    page_url: page.url(),
    title: "",
    needles,
    visible_category_texts: [],
    body_head: "",
    error: error.message || String(error),
  }));
}

async function readCategoryPageState(page, expectedParts = []) {
  return page.evaluate(({ expectedParts, selector }) => {
    const compact = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/[\s、，,\/／|｜·•\-]+/g, "")
      .trim();
    const normalizeId = (value) => {
      const text = String(value || "").trim();
      return /^\d{1,32}$/.test(text) ? text : "";
    };
    const categoryIdFromValue = (...values) => {
      for (const value of values) {
        const raw = String(value || "").trim();
        if (!raw) continue;
        const direct = normalizeId(raw);
        if (direct) return direct;
        const variants = [raw];
        try {
          variants.push(decodeURIComponent(raw));
        } catch {
          // Keep the raw value only.
        }
        for (const text of variants) {
          try {
            const parsed = new URL(text, location.href);
            for (const [key, candidate] of parsed.searchParams.entries()) {
              if (["categoryid", "catid", "cateid", "category_id", "cat_id", "cate_id"].includes(key.toLowerCase())) {
                const normalized = normalizeId(candidate);
                if (normalized) return normalized;
              }
            }
          } catch {
            // Non-URL values may still contain a category key below.
          }
          const match = text.match(/(?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)[^0-9]{0,12}(\d{1,32})/i)
            || text.match(/[?&#](?:categoryId|catId|cateId|category_id|cat_id|cate_id)=(\d{1,32})/i);
          if (match) return match[1];
        }
      }
      return "";
    };
    const cleanBreadcrumbLabel = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/\s+/g, " ")
      .replace(/^[>›»/|\-\s]+|[>›»/|\-\s]+$/g, "")
      .trim();
    const visible = (el) => {
      if (!el || typeof el.getBoundingClientRect !== "function") return false;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const breadcrumbRoots = Array.from(document.querySelectorAll([
      "nav[aria-label*='breadcrumb' i]",
      "[class*='breadcrumb' i]",
      "[class*='crumb' i]",
      "[data-testid*='breadcrumb' i]",
      "[data-spm*='breadcrumb' i]",
    ].join(", "))).filter(visible);
    const breadcrumbCandidates = breadcrumbRoots.map((root) => {
      const nodes = Array.from(root.querySelectorAll("a[href], li, span, [itemprop='name']"));
      const items = [];
      const seen = new Set();
      for (const node of nodes) {
        if (!visible(node)) continue;
        const name = cleanBreadcrumbLabel(
          node.innerText
          || node.textContent
          || node.getAttribute?.("title")
          || node.getAttribute?.("aria-label")
          || ""
        );
        if (!name || name.length > 80 || /^(首页|1688|阿里巴巴|全部分类|商品详情)$/.test(name) || seen.has(name)) continue;
        const href = String(node.getAttribute?.("href") || node.href || "");
        const categoryId = categoryIdFromValue(
          node.getAttribute?.("data-category-id"),
          node.getAttribute?.("data-categoryid"),
          node.getAttribute?.("data-cat-id"),
          node.getAttribute?.("data-catid"),
          node.getAttribute?.("data-cate-id"),
          node.getAttribute?.("data-cateid"),
          href
        );
        seen.add(name);
        items.push({ name, category_id: categoryId, href });
      }
      return items.slice(0, 10);
    }).filter((items) => items.length);
    const breadcrumb = breadcrumbCandidates.sort((a, b) => b.length - a.length)[0] || [];
    const bodyText = compact(document.body?.innerText || "");
    const title = document.title || "";
    const url = location.href;
    let decodedUrl = url;
    try {
      decodedUrl = decodeURIComponent(url);
    } catch {
      decodedUrl = url;
    }
    const expected = expectedParts.map(compact).filter(Boolean);
    const leaf = expected[expected.length - 1] || "";
    const compactTitle = compact(title);
    const matchedExpectedParts = expected.filter((part) => part && (bodyText.includes(part) || compact(title).includes(part)));
    const anchors = Array.from(document.querySelectorAll(selector));
    let structuredCategoryId = "";
    if (leaf) {
      const scripts = Array.from(document.querySelectorAll("script"))
        .map((script) => script.textContent || "")
        .filter((content) => content.includes(leaf) && /categoryId|catId|cateId|category_id/i.test(content))
        .slice(0, 8);
      for (const content of scripts) {
        const leafIndex = content.indexOf(leaf);
        const windowText = leafIndex >= 0
          ? content.slice(Math.max(0, leafIndex - 1600), leafIndex + 1600)
          : content.slice(0, 3200);
        structuredCategoryId = categoryIdFromValue(windowText);
        if (structuredCategoryId) break;
      }
    }
    const urlCategoryId = categoryIdFromValue(url);
    const breadcrumbCategoryId = [...breadcrumb].reverse().map((item) => item.category_id).find(Boolean) || "";
    const categoryId = urlCategoryId || breadcrumbCategoryId || structuredCategoryId;
    const categoryBreadcrumb = breadcrumb.map((item) => item.name);
    const compactBreadcrumb = categoryBreadcrumb.map(compact).filter(Boolean);
    const matchedBreadcrumbParts = expected.filter((part) => compactBreadcrumb.some((segment) => (
      segment === part
      || (part.length >= 4 && segment.includes(part))
      || (segment.length >= 4 && part.includes(segment))
    )));
    return {
      page_url: url,
      title,
      product_anchor_count: anchors.length,
      leaf_in_url: leaf ? compact(decodedUrl).includes(leaf) : false,
      leaf_in_title: leaf ? compactTitle.includes(leaf) : false,
      leaf_in_page: leaf ? bodyText.includes(leaf) || compactTitle.includes(leaf) : false,
      matched_expected_parts: matchedExpectedParts,
      matched_breadcrumb_parts: matchedBreadcrumbParts,
      category_id: categoryId,
      category_path: categoryBreadcrumb.join(">"),
      category_breadcrumb: categoryBreadcrumb,
      category_source: categoryBreadcrumb.length
        ? "result_page_breadcrumb"
        : (urlCategoryId ? "result_page_url" : (structuredCategoryId ? "result_page_structured_data" : "")),
    };
  }, { expectedParts, selector: productCardSelector }).catch(() => ({
    page_url: page.url(),
    title: "",
    product_anchor_count: 0,
    leaf_in_url: false,
    leaf_in_title: false,
    leaf_in_page: false,
    matched_expected_parts: [],
    matched_breadcrumb_parts: [],
    category_id: "",
    category_path: "",
    category_breadcrumb: [],
    category_source: "",
  }));
}

function categoryNavigationConfirmed(state, expectedParts = [], clickedCategoryId = "") {
  const parts = Array.isArray(expectedParts) ? expectedParts.filter(Boolean) : [];
  if (!parts.length) return false;
  const onResultUrl = /s\.1688\.com\/selloffer|offer_search/.test(String(state.page_url || ""));
  const breadcrumbMatchedCount = (state.matched_breadcrumb_parts || []).length;
  const clickedId = String(clickedCategoryId || "").trim();
  const resultId = String(state.category_id || "").trim();
  const categoryIdConfirmed = Boolean(clickedId && resultId && clickedId === resultId);
  const breadcrumbConfirmed = breadcrumbMatchedCount >= Math.min(parts.length, 2);
  return Boolean(
    onResultUrl
    && Number(state.product_anchor_count || 0) > 0
    && (categoryIdConfirmed || state.leaf_in_url || state.leaf_in_title || breadcrumbConfirmed)
  );
}

async function clickCategoryPart(page, needleSet, mode = "click", options = {}) {
  const marker = `sop-cat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const strictMatch = Boolean(options.strict);
  const expectedDepth = Number(options.expectedDepth || 0);
  const locateCandidate = () => page.evaluate(async ({ needles, mode, marker, strictMatch, expectedDepth }) => {
    const compact = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/[\s、，,\/／|｜·•\-]+/g, "")
      .trim();
    const visible = (el) => {
      if (!el || typeof el.getBoundingClientRect !== "function") return false;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const disabled = (el) => {
      const className = String(el.className || "");
      return el.disabled || el.getAttribute?.("aria-disabled") === "true" || /disabled|disable/.test(className);
    };
    const scrollWithinNearestContainer = async (el) => {
      const pageScrollBefore = window.scrollY;
      let container = el?.parentElement || null;
      while (container && container !== document.body && container !== document.documentElement) {
        const style = window.getComputedStyle(container);
        const canScrollY = /(auto|scroll|overlay)/.test(style.overflowY) && container.scrollHeight > container.clientHeight + 2;
        const canScrollX = /(auto|scroll|overlay)/.test(style.overflowX) && container.scrollWidth > container.clientWidth + 2;
        if (canScrollY || canScrollX) break;
        container = container.parentElement;
      }
      if (!container || container === document.body || container === document.documentElement) {
        return { container: "none", page_scroll_before: pageScrollBefore, page_scroll_after: window.scrollY };
      }
      const targetRect = el.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      const scrollTopBefore = container.scrollTop;
      const scrollLeftBefore = container.scrollLeft;
      const requestedTop = container.scrollHeight > container.clientHeight + 2
        ? Math.max(0, Math.min(
          container.scrollHeight - container.clientHeight,
          container.scrollTop + targetRect.top - containerRect.top - (container.clientHeight - targetRect.height) / 2
        ))
        : container.scrollTop;
      const requestedLeft = container.scrollWidth > container.clientWidth + 2
        ? Math.max(0, Math.min(
          container.scrollWidth - container.clientWidth,
          container.scrollLeft + targetRect.left - containerRect.left - (container.clientWidth - targetRect.width) / 2
        ))
        : container.scrollLeft;
      const previousInlineScrollBehavior = container.style.getPropertyValue("scroll-behavior");
      const previousInlineScrollBehaviorPriority = container.style.getPropertyPriority("scroll-behavior");
      container.style.setProperty("scroll-behavior", "auto", "important");
      if (container.scrollHeight > container.clientHeight + 2) {
        container.scrollTop = requestedTop;
      }
      if (container.scrollWidth > container.clientWidth + 2) {
        container.scrollLeft = requestedLeft;
      }
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (previousInlineScrollBehavior) {
        container.style.setProperty("scroll-behavior", previousInlineScrollBehavior, previousInlineScrollBehaviorPriority);
      } else {
        container.style.removeProperty("scroll-behavior");
      }
      const settledTargetRect = el.getBoundingClientRect();
      const settledContainerRect = container.getBoundingClientRect();
      return {
        container: String(container.className || container.id || container.tagName || "scroll-container").slice(0, 120),
        container_scroll_top_before: scrollTopBefore,
        container_scroll_top: container.scrollTop,
        container_scroll_left_before: scrollLeftBefore,
        container_scroll_left: container.scrollLeft,
        requested_scroll_top: requestedTop,
        requested_scroll_left: requestedLeft,
        container_rect: {
          left: Math.round(settledContainerRect.left),
          top: Math.round(settledContainerRect.top),
          right: Math.round(settledContainerRect.right),
          bottom: Math.round(settledContainerRect.bottom),
          width: Math.round(settledContainerRect.width),
          height: Math.round(settledContainerRect.height),
        },
        target_within_container: settledTargetRect.left >= settledContainerRect.left
          && settledTargetRect.right <= settledContainerRect.right
          && settledTargetRect.top >= settledContainerRect.top
          && settledTargetRect.bottom <= settledContainerRect.bottom,
        page_scroll_before: pageScrollBefore,
        page_scroll_after: window.scrollY,
      };
    };
    const compactNeedles = needles.map(compact).filter(Boolean);
    const textOf = (node) => compact(
      node.innerText
      || node.textContent
      || node.getAttribute?.("title")
      || node.getAttribute?.("aria-label")
      || ""
    );
    const directTextOf = (node) => compact([
      ...Array.from(node.childNodes || [])
        .filter((child) => child.nodeType === Node.TEXT_NODE)
        .map((child) => child.textContent || ""),
      node.getAttribute?.("title") || "",
      node.getAttribute?.("aria-label") || "",
    ].join(" "));
    const hrefOf = (node) => node.href || node.getAttribute?.("href") || "";
    const categoryIdHintOf = (node) => {
      if (!node || typeof node.getAttribute !== "function") return "";
      const values = [
        node.getAttribute("data-category-id"),
        node.getAttribute("data-categoryid"),
        node.getAttribute("data-cat-id"),
        node.getAttribute("data-catid"),
        node.getAttribute("data-cate-id"),
        node.getAttribute("data-cateid"),
      ];
      return values.map((value) => String(value || "").trim()).find((value) => /^\d{1,32}$/.test(value)) || "";
    };
    const hoverTargetFor = (node) => {
      if (expectedDepth >= 2) {
        const preciseHoverTarget = node.matches?.("a, button, [role='button'], [class*='cTitle']")
          ? node
          : node.closest?.("a, button, [role='button'], [class*='cTitle']");
        if (preciseHoverTarget && visible(preciseHoverTarget) && !disabled(preciseHoverTarget)) {
          return preciseHoverTarget;
        }
      }
      return node.closest?.("li, [class*='category'], [class*='cate'], [class*='menu'], [class*='industry'], [class*='cBox']") || node;
    };
    const clickableFor = (node) => {
      if (mode === "hover") return hoverTargetFor(node);
      return node.closest?.("a[href], a, button, [role='button'], li, [class*='category'], [class*='cate'], [class*='menu']") || node;
    };
    const roots = Array.from(document.querySelectorAll(
      "[class*='category'], [class*='cate'], [class*='menu'], [class*='nav'], [class*='filter'], [class*='industry'], [class*='lv1'], [class*='lv2'], [class*='cBox'], [class*='cTitle'], aside, nav"
    )).filter((node) => visible(node));
    const scopedNodes = (roots.length ? roots : [document.body]).flatMap((root) => [
      root,
      ...Array.from(root.querySelectorAll("a[href], a, button, li, span, div")),
    ]);
    const nodes = [...new Set(scopedNodes)]
      .filter((node) => visible(node) && !disabled(node));

    let best = null;
    for (const node of nodes) {
      const text = textOf(node);
      const directText = directTextOf(node);
      if (!text) continue;
      for (const needle of compactNeedles) {
        if (!needle) continue;
        const directExact = directText === needle;
        const exact = text === needle || directExact;
        const shortContains = needle.length >= 4 && text.includes(needle) && text.length <= Math.max(needle.length + 12, 28);
        const reverseContains = needle.includes(text) && text.length >= 4 && text.length <= Math.max(needle.length, 8);
        if (strictMatch && !exact) continue;
        if (!exact && !shortContains && !reverseContains) continue;
        const clickable = clickableFor(node);
        if (!clickable || !visible(clickable) || disabled(clickable)) continue;
        const href = hrefOf(clickable) || hrefOf(node);
        if (mode === "click" && !/s\.1688\.com\/selloffer|offer_search|keywords=|categoryId=|catId=|offer/i.test(href)) {
          continue;
        }
        const score = (directExact ? 120 : exact ? 110 : reverseContains ? 72 : 64)
          + (mode === "click" && /s\.1688\.com\/selloffer|offer_search/.test(href) ? 30 : 0)
          + (mode === "click" && (clickable.tagName === "A" || node.tagName === "A") ? 10 : 0)
          + (mode === "hover" && text.length > needle.length && text.includes(needle) ? 18 : 0)
          - Math.max(0, text.length - needle.length);
        if (!best || score > best.score) {
          best = {
            node,
            clickable,
            href,
            text,
            directText,
            needle,
            score,
            matchType: directExact ? "direct_exact" : exact ? "exact" : reverseContains ? "reverse_contains" : "contains",
            categoryIdHint: categoryIdHintOf(clickable) || categoryIdHintOf(node),
          };
        }
      }
    }
    const diagnosticTexts = nodes
      .map((node) => textOf(node))
      .filter((text) => text && text.length <= 60)
      .filter((text, index, arr) => arr.indexOf(text) === index)
      .slice(0, 40);
    if (!best) {
      const hiddenMatches = Array.from(document.querySelectorAll("a[href], a, button, li, span, div"))
        .filter((node) => {
          const text = textOf(node);
          const directText = directTextOf(node);
          return compactNeedles.some((needle) => needle && (text === needle || directText === needle));
        })
        .slice(0, 12)
        .map((node) => {
          const rect = node.getBoundingClientRect();
          const style = window.getComputedStyle(node);
          const menuAncestor = node.closest?.("[class*='category'], [class*='cate'], [class*='menu'], [class*='industry'], [class*='cBox']");
          const menuStyle = menuAncestor ? window.getComputedStyle(menuAncestor) : null;
          return {
            text: textOf(node).slice(0, 80),
            tag: String(node.tagName || "").toLowerCase(),
            class_name: String(node.className || "").slice(0, 120),
            display: style.display,
            visibility: style.visibility,
            rect: {
              left: Math.round(rect.left),
              top: Math.round(rect.top),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            },
            menu_ancestor: menuAncestor ? String(menuAncestor.className || menuAncestor.id || menuAncestor.tagName || "").slice(0, 120) : "",
            menu_display: menuStyle?.display || "",
          };
        });
      return {
        ok: false,
        matched_text: "",
        missing_text: needles[0] || "",
        href: "",
        diagnostics: {
          page_url: location.href,
          title: document.title || "",
          needles,
          strict_match: strictMatch,
          expected_depth: expectedDepth,
          visible_category_texts: diagnosticTexts,
          hidden_exact_matches: hiddenMatches,
        },
      };
    }
    document.querySelectorAll("[data-sop-category-candidate]").forEach((node) => {
      node.removeAttribute("data-sop-category-candidate");
    });
    best.clickable.setAttribute("data-sop-category-candidate", marker);
    const scrollEvidence = await scrollWithinNearestContainer(best.clickable);
    const targetRect = best.clickable.getBoundingClientRect();
    const textHitBoxFor = (node, needle) => {
      const candidates = [node, ...Array.from(node.querySelectorAll?.("span, strong, em, b, i") || [])];
      for (const candidate of candidates) {
        for (const child of Array.from(candidate.childNodes || [])) {
          if (child.nodeType !== Node.TEXT_NODE) continue;
          const text = compact(child.textContent || "");
          if (!text || !(text === needle || text.includes(needle) || needle.includes(text))) continue;
          const range = document.createRange();
          range.selectNodeContents(child);
          const rect = range.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            return {
              left: Math.round(rect.left),
              top: Math.round(rect.top),
              right: Math.round(rect.right),
              bottom: Math.round(rect.bottom),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            };
          }
        }
      }
      return null;
    };
    const textHitBox = textHitBoxFor(best.node, best.needle) || textHitBoxFor(best.clickable, best.needle);
    return {
      ok: true,
      matched_text: best.text.slice(0, 80),
      matched_needle: best.needle,
      href: best.href || "",
      category_id_hint: best.categoryIdHint || "",
      score: best.score,
      match_type: best.matchType,
      strict_match: strictMatch,
      expected_depth: expectedDepth,
      mode,
      selector: `[data-sop-category-candidate="${marker}"]`,
      diagnostics: {
        page_url: location.href,
        title: document.title || "",
        needles,
        strict_match: strictMatch,
        expected_depth: expectedDepth,
        visible_category_texts: diagnosticTexts,
        category_scroll: scrollEvidence,
        pointer_text_hit_box: textHitBox,
        pointer_target_element: {
          tag: String(best.clickable.tagName || "").toLowerCase(),
          class_name: String(best.clickable.className || "").slice(0, 140),
          text: textOf(best.clickable).slice(0, 100),
          rect: {
            left: Math.round(targetRect.left),
            top: Math.round(targetRect.top),
            width: Math.round(targetRect.width),
            height: Math.round(targetRect.height),
          },
        },
      },
    };
  }, { needles: needleSet, mode, marker, strictMatch, expectedDepth }).catch((error) => ({
    ok: false,
    error: error.message || String(error),
    matched_text: "",
    missing_text: needleSet[0] || "",
    href: "",
  }));
  const scanPageScrollBefore = await page.evaluate(() => window.scrollY).catch(() => 0);
  const categoryScrollRecords = [];
  let categoryScrollStopReason = "target_visible_without_scan";
  let result = await locateCandidate();
  while (!result.ok && !result.error && categoryScrollRecords.length < categoryScrollMaxSteps) {
    const scrollResult = await page.evaluate(({ expectedDepth, stepIndex }) => {
      const pageScrollBefore = window.scrollY;
      const visible = (el) => {
        if (!el || typeof el.getBoundingClientRect !== "function") return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0
          && rect.height > 0
          && rect.bottom > 0
          && rect.right > 0
          && rect.top < (window.innerHeight || document.documentElement.clientHeight || 1)
          && rect.left < (window.innerWidth || document.documentElement.clientWidth || 1)
          && style.visibility !== "hidden"
          && style.display !== "none";
      };
      const describe = (el) => ({
        tag: String(el.tagName || "").toLowerCase(),
        id: String(el.id || "").slice(0, 80),
        class_name: String(el.className || "").slice(0, 140),
      });
      const categorySelector = [
        "[class*='category']",
        "[class*='cate']",
        "[class*='menu']",
        "[class*='nav']",
        "[class*='industry']",
        "[class*='lv1']",
        "[class*='lv2']",
        "[class*='cBox']",
        "[class*='cTitle']",
        "aside",
        "nav",
      ].join(", ");
      const categoryRoots = Array.from(document.querySelectorAll(categorySelector)).filter(visible);
      const previousCandidate = document.querySelector("[data-sop-category-candidate]");
      const previousRect = previousCandidate?.getBoundingClientRect?.() || null;
      const candidates = new Set();
      const addWithAncestors = (node) => {
        let current = node;
        for (let depth = 0; current && depth < 6; depth += 1) {
          if (current !== document.body && current !== document.documentElement) candidates.add(current);
          current = current.parentElement;
        }
      };
      categoryRoots.forEach(addWithAncestors);
      if (previousCandidate) addWithAncestors(previousCandidate);
      const viewportWidth = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
      const viewportHeight = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1);
      const scored = [];
      for (const container of candidates) {
        if (!visible(container)) continue;
        const style = window.getComputedStyle(container);
        const canScrollY = /(auto|scroll|overlay)/.test(style.overflowY)
          && container.scrollHeight > container.clientHeight + 2;
        if (!canScrollY) continue;
        const remaining = Math.max(0, container.scrollHeight - container.clientHeight - container.scrollTop);
        if (remaining <= 2) continue;
        const rect = container.getBoundingClientRect();
        const identity = `${container.id || ""} ${container.className || ""} ${container.getAttribute?.("role") || ""}`.toLowerCase();
        const categoryLinkCount = container.querySelectorAll?.(
          "a[href*='1688.com'], a[href*='categoryId'], a[href*='catId'], [data-category-id], [data-catid]"
        ).length || 0;
        const categoryNodeCount = container.querySelectorAll?.(categorySelector).length || 0;
        let score = 0;
        if (/category|cate|menu|nav|industry|cbox|ctitle|lv1|lv2/.test(identity)) score += 55;
        score += Math.min(36, categoryLinkCount * 4);
        score += Math.min(24, categoryNodeCount * 2);
        if (previousCandidate && container.contains(previousCandidate)) score += 95;
        if (previousCandidate && container.parentElement?.contains(previousCandidate)) score += 35;
        if (previousRect) {
          const verticalOverlap = Math.min(rect.bottom, previousRect.bottom) - Math.max(rect.top, previousRect.top);
          if (verticalOverlap > 0) score += 24;
          if (rect.left >= previousRect.left - 24) score += 18;
        }
        if (expectedDepth <= 1) score += Math.max(0, 28 - Math.round(rect.left / 24));
        if (rect.width >= viewportWidth * 0.9 && rect.height >= viewportHeight * 0.8) score -= 100;
        scored.push({ container, score, remaining, rect, categoryLinkCount, categoryNodeCount });
      }
      scored.sort((left, right) => right.score - left.score || right.remaining - left.remaining);
      const chosen = scored[0];
      if (!chosen) {
        const pageScrollAfter = window.scrollY;
        if (pageScrollAfter !== pageScrollBefore) {
          window.scrollTo({ top: pageScrollBefore, left: window.scrollX, behavior: "auto" });
        }
        return {
          scrolled: false,
          stop_reason: "no_remaining_category_scroll_container",
          step_index: stepIndex,
          candidate_count: scored.length,
          page_scroll_before: pageScrollBefore,
          page_scroll_after: pageScrollAfter,
          page_scroll_restored: pageScrollAfter !== pageScrollBefore,
        };
      }
      const before = chosen.container.scrollTop;
      const scrollStep = Math.min(
        chosen.remaining,
        Math.max(96, Math.min(420, Math.round(chosen.container.clientHeight * 0.72)))
      );
      const previousInlineScrollBehavior = chosen.container.style.getPropertyValue("scroll-behavior");
      const previousInlineScrollBehaviorPriority = chosen.container.style.getPropertyPriority("scroll-behavior");
      chosen.container.style.setProperty("scroll-behavior", "auto", "important");
      chosen.container.scrollTop = Math.min(
        chosen.container.scrollHeight - chosen.container.clientHeight,
        before + scrollStep
      );
      const after = chosen.container.scrollTop;
      if (previousInlineScrollBehavior) {
        chosen.container.style.setProperty("scroll-behavior", previousInlineScrollBehavior, previousInlineScrollBehaviorPriority);
      } else {
        chosen.container.style.removeProperty("scroll-behavior");
      }
      const pageScrollAfter = window.scrollY;
      if (pageScrollAfter !== pageScrollBefore) {
        window.scrollTo({ top: pageScrollBefore, left: window.scrollX, behavior: "auto" });
      }
      return {
        scrolled: after > before + 1,
        stop_reason: after > before + 1 ? "scrolled_category_container" : "category_container_did_not_move",
        step_index: stepIndex,
        candidate_count: scored.length,
        container: describe(chosen.container),
        container_scroll_before: before,
        container_scroll_after: after,
        container_scroll_max: Math.max(0, chosen.container.scrollHeight - chosen.container.clientHeight),
        scroll_step: scrollStep,
        score: chosen.score,
        category_link_count: chosen.categoryLinkCount,
        category_node_count: chosen.categoryNodeCount,
        page_scroll_before: pageScrollBefore,
        page_scroll_after: pageScrollAfter,
        page_scroll_restored: pageScrollAfter !== pageScrollBefore,
      };
    }, { expectedDepth, stepIndex: categoryScrollRecords.length + 1 }).catch((error) => ({
      scrolled: false,
      stop_reason: "category_scroll_evaluate_failed",
      error: error.message || String(error),
    }));
    categoryScrollRecords.push(scrollResult);
    categoryScrollStopReason = scrollResult.stop_reason || "category_scroll_stopped";
    if (!scrollResult.scrolled) break;
    await page.waitForTimeout(jitter(selfTestMode ? [20, 45] : (pacingMode === "human" ? [240, 520] : [90, 220])));
    result = await locateCandidate();
    if (result.ok) categoryScrollStopReason = "target_found_after_category_scroll";
  }
  if (!result.ok && !result.error && categoryScrollRecords.length >= categoryScrollMaxSteps) {
    categoryScrollStopReason = "category_scroll_max_steps_reached";
  }
  const scanPageScrollAfter = await page.evaluate((before) => {
    const after = window.scrollY;
    if (after !== before) window.scrollTo({ top: before, left: window.scrollX, behavior: "auto" });
    return after;
  }, scanPageScrollBefore).catch(() => scanPageScrollBefore);
  result = {
    ...result,
    diagnostics: {
      ...(result.diagnostics || {}),
      category_scroll_scan: {
        attempts: categoryScrollRecords.length,
        max_steps: categoryScrollMaxSteps,
        stop_reason: categoryScrollStopReason,
        records: categoryScrollRecords,
        page_scroll_before: scanPageScrollBefore,
        page_scroll_after: scanPageScrollAfter,
        page_scroll_restored: scanPageScrollAfter !== scanPageScrollBefore,
      },
    },
  };
  const enrichedResult = withOfficialCategoryId(result);
  if (!enrichedResult.ok || !enrichedResult.selector) return enrichedResult;
  const locator = page.locator(enrichedResult.selector).first();
  try {
    const pageScrollBefore = await page.evaluate(() => window.scrollY).catch(() => 0);
    const box = await locator.boundingBox();
    const viewport = await page.evaluate(() => ({
      width: Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1),
      height: Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1),
    }));
    if (!box || box.width <= 0 || box.height <= 0) {
      throw new Error("category_target_has_no_visible_box");
    }
    if (enrichedResult.diagnostics?.category_scroll?.target_within_container === false) {
      throw new Error("category_target_clipped_by_menu_container");
    }
    const textHitBox = enrichedResult.diagnostics?.pointer_text_hit_box || null;
    const usableTextHitBox = textHitBox
      && Number(textHitBox.width || 0) > 0
      && Number(textHitBox.height || 0) > 0
      && Number(textHitBox.left) >= box.x - 1
      && Number(textHitBox.right) <= box.x + box.width + 1
      && Number(textHitBox.top) >= box.y - 1
      && Number(textHitBox.bottom) <= box.y + box.height + 1;
    const targetX = usableTextHitBox
      ? Number(textHitBox.left) + Number(textHitBox.width) / 2
      : box.x + Math.min(Math.max(3, box.width / 2), Math.max(3, box.width - 3));
    const targetY = usableTextHitBox
      ? Number(textHitBox.top) + Number(textHitBox.height) / 2
      : box.y + Math.min(Math.max(3, box.height / 2), Math.max(3, box.height - 3));
    if (targetX < 0 || targetY < 0 || targetX >= viewport.width || targetY >= viewport.height) {
      throw new Error("category_target_outside_viewport_without_page_scroll");
    }
    const containerRect = enrichedResult.diagnostics?.category_scroll?.container_rect || null;
    const canUseCategoryCorridor = expectedDepth > 1
      && lastCategoryPointer?.page === page
      && containerRect
      && Number(containerRect.width || 0) > 0
      && Number(containerRect.height || 0) > 0
      && targetX >= Number(containerRect.left)
      && targetX <= Number(containerRect.right)
      && targetY >= Number(containerRect.top)
      && targetY <= Number(containerRect.bottom);
    let pointerRoute = "direct_approach";
    const pointerWaypoints = [];
    if (canUseCategoryCorridor) {
      const corridorX = Math.max(2, Math.min(viewport.width - 2, Number(containerRect.left) + Math.min(12, Math.max(6, Number(containerRect.width) / 20))));
      const corridorStartY = Math.max(
        Number(containerRect.top) + 6,
        Math.min(Number(containerRect.bottom) - 6, Number(lastCategoryPointer.y))
      );
      const corridorTargetY = Math.max(
        Number(containerRect.top) + 6,
        Math.min(Number(containerRect.bottom) - 6, targetY)
      );
      await page.mouse.move(corridorX, corridorStartY, { steps: 4 });
      pointerWaypoints.push({ x: Math.round(corridorX), y: Math.round(corridorStartY) });
      await page.mouse.move(corridorX, corridorTargetY, { steps: 8 });
      pointerWaypoints.push({ x: Math.round(corridorX), y: Math.round(corridorTargetY) });
      await page.mouse.move(targetX, targetY, { steps: 5 });
      pointerRoute = "category_container_corridor";
    } else {
      const approachX = Math.max(2, Math.min(viewport.width - 2, targetX - Math.min(36, Math.max(12, box.width / 2))));
      const approachY = Math.max(2, Math.min(viewport.height - 2, targetY - Math.min(12, Math.max(4, box.height / 4))));
      await page.mouse.move(approachX, approachY, { steps: 2 });
      pointerWaypoints.push({ x: Math.round(approachX), y: Math.round(approachY) });
      await page.mouse.move(targetX, targetY, { steps: 6 });
    }
    lastCategoryPointer = { page, x: targetX, y: targetY };
    await page.waitForTimeout(jitter([220, 650]));
    if (mode === "click") {
      await page.mouse.click(targetX, targetY, { delay: jitter([45, 130]) });
    }
    const pageScrollAfter = await page.evaluate((before) => {
      const after = window.scrollY;
      if (after !== before) window.scrollTo({ top: before, left: window.scrollX, behavior: "auto" });
      return after;
    }, pageScrollBefore).catch(() => pageScrollBefore);
    return {
      ...enrichedResult,
      diagnostics: {
        ...(enrichedResult.diagnostics || {}),
        page_scroll_before_action: pageScrollBefore,
        page_scroll_after_action: pageScrollAfter,
        page_scroll_restored: pageScrollAfter !== pageScrollBefore,
        pointer_interaction: "playwright_mouse",
        pointer_route: pointerRoute,
        pointer_waypoints: pointerWaypoints,
        pointer_target_strategy: usableTextHitBox ? "direct_text_range" : "element_center",
        pointer_target: { x: Math.round(targetX), y: Math.round(targetY) },
      },
    };
  } catch (error) {
    lastCategoryPointer = null;
    if (
      mode === "click"
      && enrichedResult.href
      && /(?:^https?:)?\/\/(?:[^/]+\.)?1688\.com\/.*(?:selloffer|offer_search)/i.test(enrichedResult.href)
    ) {
      try {
        await page.goto(enrichedResult.href, {
          waitUntil: "domcontentloaded",
          timeout: Math.min(pageTimeoutMs, 30000),
        });
        return {
          ...enrichedResult,
          ok: true,
          fallback_mode: "direct_href",
          diagnostics: {
            ...(enrichedResult.diagnostics || {}),
            direct_href_fallback: true,
            locator_error: error.message || String(error),
          },
        };
      } catch (navigationError) {
        const diagnostics = await collectCategoryDiagnostics(page, needleSet);
        return {
          ...enrichedResult,
          ok: false,
          error: navigationError.message || String(navigationError),
          diagnostics: {
            ...(enrichedResult.diagnostics || {}),
            ...diagnostics,
            direct_href_fallback: true,
            locator_error: error.message || String(error),
          },
        };
      }
    }
    const diagnostics = await collectCategoryDiagnostics(page, needleSet);
    return {
      ...enrichedResult,
      ok: false,
      error: error.message || String(error),
      diagnostics: {
        ...(enrichedResult.diagnostics || {}),
        ...diagnostics,
      },
    };
  }
}

async function inspectCategoryTargetVisibility(page, needleSet) {
  return page.evaluate(({ needles }) => {
    const compact = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/[\s、，,\/／|｜·•\-]+/g, "")
      .trim();
    const expected = needles.map(compact).filter(Boolean);
    const matches = Array.from(document.querySelectorAll("a[href], a, button, [role='button'], li, span, div"))
      .filter((node) => {
        const fullText = compact(node.innerText || node.textContent || node.getAttribute?.("title") || node.getAttribute?.("aria-label") || "");
        const directText = compact([
          ...Array.from(node.childNodes || [])
            .filter((child) => child.nodeType === Node.TEXT_NODE)
            .map((child) => child.textContent || ""),
          node.getAttribute?.("title") || "",
          node.getAttribute?.("aria-label") || "",
        ].join(" "));
        return expected.some((needle) => needle && (fullText === needle || directText === needle));
      })
      .slice(0, 20)
      .map((node) => {
        const rect = node.getBoundingClientRect();
        const style = window.getComputedStyle(node);
        const isVisible = rect.width > 0
          && rect.height > 0
          && style.display !== "none"
          && style.visibility !== "hidden";
        return {
          visible: isVisible,
          tag: String(node.tagName || "").toLowerCase(),
          class_name: String(node.className || "").slice(0, 140),
          text: compact(node.innerText || node.textContent || "").slice(0, 100),
          display: style.display,
          visibility: style.visibility,
          rect: {
            left: Math.round(rect.left),
            top: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
        };
      });
    return {
      visible: matches.some((item) => item.visible),
      needles,
      matches,
    };
  }, { needles: needleSet }).catch((error) => ({
    visible: false,
    needles: needleSet,
    matches: [],
    error: error.message || String(error),
  }));
}

async function reopenCategoryParents(page, needleSets, parts, parentCount) {
  const records = [];
  for (let index = 0; index < parentCount; index += 1) {
    const needleSet = needleSets[index] || [];
    const applied = await clickCategoryPart(page, needleSet, "hover", {
      strict: index === 0,
      expectedDepth: index + 1,
    });
    records.push({
      depth: index + 1,
      expected_text: parts[index] || needleSet[0] || "",
      status: applied.ok ? "matched" : (applied.error ? "click_failed" : "not_found"),
      matched_text: normalizeText(applied.matched_text || ""),
      match_type: applied.match_type || "",
      diagnostics: applied.diagnostics || {},
      message: applied.error || "",
    });
    if (!applied.ok) return { ok: false, records };
    if (index < parentCount - 1) await categoryCascadePause(page);
  }
  const nextNeedleSet = needleSets[parentCount] || [];
  if (!nextNeedleSet.length) return { ok: true, status: "reopened", records };
  await page.waitForTimeout(jitter(selfTestMode ? [30, 60] : [80, 180]));
  const nextTarget = await inspectCategoryTargetVisibility(page, nextNeedleSet);
  if (!nextTarget.visible) {
    return {
      ok: false,
      status: "reopen_not_visible",
      records,
      next_target: nextTarget,
    };
  }
  return {
    ok: true,
    status: "reopened",
    records,
    next_target: nextTarget,
  };
}

async function clickCategoryLeafLink(page, parts) {
  const result = await page.evaluate(({ parts }) => {
    const compact = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/[\s、，,\/／|｜·•\-]+/g, "")
      .trim();
    const hrefOf = (node) => node.href || node.getAttribute?.("href") || "";
    const categoryIdHintOf = (node) => {
      if (!node || typeof node.getAttribute !== "function") return "";
      return [
        node.getAttribute("data-category-id"),
        node.getAttribute("data-categoryid"),
        node.getAttribute("data-cat-id"),
        node.getAttribute("data-catid"),
        node.getAttribute("data-cate-id"),
        node.getAttribute("data-cateid"),
      ].map((value) => String(value || "").trim()).find((value) => /^\d{1,32}$/.test(value)) || "";
    };
    const expected = (Array.isArray(parts) ? parts : []).map(compact).filter(Boolean);
    const leaf = expected[expected.length - 1] || "";
    if (!leaf) return { ok: false, missing_text: "" };
    let best = null;
    const anchors = Array.from(document.querySelectorAll("a[href]"));
    for (const anchor of anchors) {
      const text = compact(anchor.innerText || anchor.textContent || anchor.getAttribute?.("title") || anchor.getAttribute?.("aria-label") || "");
      const href = hrefOf(anchor);
      if (!href || !/s\.1688\.com\/selloffer|offer_search|keywords=|categoryId=|catId=/i.test(href)) continue;
      const exact = text === leaf;
      const contains = leaf.length >= 2 && text.includes(leaf) && text.length <= Math.max(leaf.length + 12, 32);
      if (!exact && !contains) continue;
      const ancestors = [];
      let current = anchor;
      for (let depth = 0; current && depth < 8; depth += 1) {
        ancestors.push(compact(current.innerText || current.textContent || ""));
        current = current.parentElement;
      }
      const context = ancestors.join("");
      const matchedParts = expected.filter((part) => part && context.includes(part));
      if (matchedParts.length < Math.min(expected.length, 2)) continue;
      const score = (exact ? 100 : 70) + matchedParts.length * 20 - Math.max(0, text.length - leaf.length);
      if (!best || score > best.score) {
        best = { text, href, score, matchedParts, categoryIdHint: categoryIdHintOf(anchor) };
      }
    }
    if (!best) return { ok: false, missing_text: leaf };
    return {
      ok: true,
      matched_text: best.text || leaf,
      href: best.href,
      category_id_hint: best.categoryIdHint || "",
      score: best.score,
      mode: "direct_link",
      diagnostics: {
        page_url: location.href,
        title: document.title || "",
        matched_expected_parts: best.matchedParts,
        hidden_leaf_link: true,
      },
    };
  }, { parts }).catch((error) => ({
    ok: false,
    error: error.message || String(error),
    missing_text: parts[parts.length - 1] || "",
  }));
  const enrichedResult = withOfficialCategoryId(result);
  if (!enrichedResult.ok || !enrichedResult.href) return enrichedResult;
  try {
    await page.goto(enrichedResult.href, { waitUntil: "domcontentloaded", timeout: Math.min(pageTimeoutMs, 30000) });
    return enrichedResult;
  } catch (error) {
    return {
      ...enrichedResult,
      ok: false,
      error: error.message || String(error),
    };
  }
}

async function submitHumanSearch(page, query, sourceUrl) {
  await gotoWithTimeout(page, "https://www.1688.com/", query, sourceUrl, "1688 首页");
  await humanPause(page, pacing.afterGoto);

  const pageText = await readBodyText(page);
  const needsManualGate = looksLikeSecurityPage(pageText, page.url()) || looksLikeLoginPage(pageText, page.url());
  if (needsManualGate && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
  }
  await failIfStillBlocked(page, query, sourceUrl, needsManualGate && loginWaitMs > 0 && !headless);

  await humanPause(page, pacing.beforeSearchInput);
  const inputResult = await findSearchInput(page);
  if (!inputResult.ok) {
    await runtime.close().catch(() => {});
    failWithCode(
      "search_box_not_found",
      `${inputResult.reason || "未找到 1688 搜索框"}。请先在真实 Chrome 中人工打开 1688 首页或搜索页，确认搜索框可见后再查询。`,
      { source: "1688_search_page", cdp: Boolean(cdpUrl), query, source_url: sourceUrl, page_url: page.url() }
    );
  }

  await page.keyboard.type(query, { delay: jitter([45, 130]) });
  await failIfSearchKeywordMismatch(page, query, sourceUrl, "typed");

  const beforeUrl = page.url();
  page = await runSearchActionAndFollowResult(page, () => page.keyboard.press("Enter").catch(() => {}));
  await humanPause(page, pacing.afterSearchSubmit);
  if (page.url() === beforeUrl) {
    page = await runSearchActionAndFollowResult(page, () => clickSearchSubmitIfNeeded(page).catch(() => ({ clicked: false, method: "failed" })));
    await humanPause(page, pacing.afterSearchSubmit);
  }
  await failIfStillBlocked(page, query, sourceUrl, false);
  await failIfSearchKeywordMismatch(page, query, sourceUrl, "submitted");
  await waitForProductAnchors(page, query, sourceUrl);
  return page;
}

async function openHomeForCategory(page, query, sourceUrl) {
  await gotoWithTimeout(page, "https://www.1688.com/", query, sourceUrl, "1688 首页");
  await humanPause(page, pacing.afterGoto);
  const pageText = await readBodyText(page);
  const needsManualGate = looksLikeSecurityPage(pageText, page.url()) || looksLikeLoginPage(pageText, page.url());
  if (needsManualGate && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
  }
  await failIfStillBlocked(page, query, sourceUrl, needsManualGate && loginWaitMs > 0 && !headless);
  return page;
}

async function applyNativeFilters(page, filters, query) {
  if (!filters.length) return [];
  const results = [];
  const isDetailPage = /detail\.1688\.com\/offer\/\d+\.html/.test(page.url());
  for (const filter of filters) {
    const label = String(filter.label || filter.tag || filter.key || "").trim();
    const texts = Array.isArray(filter.texts) && filter.texts.length ? filter.texts : [label];
    const mode = String(filter.mode || "").trim();
    const groupLabel = String(filter.group_label || "").trim();
    const optionValue = String(filter.value || filter.tag || "").trim();
    const strictText = Boolean(filter.strict_text);
    const base = {
      filter_key: String(filter.key || label),
      tag: String(filter.tag || label),
      label,
      source: "1688_search_filter",
      query,
      page_url: page.url(),
      matched_text: "",
    };
    await updateWorkbenchOverlay(page, {
      tone: "working",
      detail: `正在应用 1688 筛选：${label || "-"}`,
      meta: `已完成 ${results.length}/${filters.length} 个筛选`,
    });
    if (isDetailPage) {
      results.push({
        ...base,
        status: "not_applicable",
        message: "当前为商品详情页URL，无法执行搜索页原生筛选",
      });
      continue;
    }
    await humanPause(page, pacing.beforeFilterClick);
    const applied = await page.evaluate(({ candidateTexts, mode, groupLabel, optionValue, strictText }) => {
      const compact = (value) => String(value || "").replace(/\s+/g, "").trim();
      const directText = (node) => compact([
        ...Array.from(node.childNodes || [])
          .filter((child) => child.nodeType === Node.TEXT_NODE)
          .map((child) => child.textContent || ""),
        node.getAttribute?.("aria-label") || "",
        node.getAttribute?.("title") || "",
      ].join(" "));
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
      };
      const nodes = Array.from(document.querySelectorAll("a, button, label, span, div, li"));
      if (mode === "dropdown_option" && groupLabel && optionValue) {
        const groupNeedle = compact(groupLabel);
        const optionNeedle = compact(optionValue);
        const filterRootFor = (node) => {
          let current = node;
          for (let depth = 0; current && depth < 6; depth += 1) {
            const text = compact(current.innerText || current.textContent || "");
            const className = String(current.className || "");
            const role = String(current.getAttribute && current.getAttribute("role") || "");
            if (
              /filter|筛选|condition|dropdown|search|offer/.test(className)
              || role === "listbox"
              || (text.includes(groupNeedle) && text.length < 600)
            ) {
              return current;
            }
            current = current.parentElement;
          }
          return node.parentElement || document.body;
        };
        const groupNode = nodes.find((node) => {
          if (!visible(node)) return false;
          const text = compact(node.innerText || node.textContent || "");
          return text.includes(groupNeedle) && text.length < 120;
        });
        if (groupNode) {
          const groupClickable = groupNode.closest("a, button, label, li, [role='button'], [class*='filter'], [class*='dropdown']") || groupNode;
          groupClickable.scrollIntoView({ block: "center", inline: "center" });
          groupClickable.click();
        }
        const searchRoot = groupNode ? filterRootFor(groupNode) : document.body;
        const optionNodes = Array.from(searchRoot.querySelectorAll("a, button, label, span, div, li"));
        const optionNode = optionNodes.find((node) => visible(node) && compact(node.innerText || node.textContent || "").includes(optionNeedle));
        if (optionNode) {
          const optionClickable = optionNode.closest("a, button, label, li, [role='button'], [class*='filter'], [class*='checkbox'], [class*='dropdown']") || optionNode;
          optionClickable.scrollIntoView({ block: "center", inline: "center" });
          optionClickable.click();
          return { ok: true, matched_text: compact(optionNode.innerText || optionNode.textContent || "").slice(0, 80) };
        }
      }
      for (const text of candidateTexts) {
        const needle = compact(text);
        if (!needle) continue;
        const matches = nodes
          .filter((node) => {
            if (!visible(node)) return false;
            const normalized = compact(node.innerText || node.textContent || "");
            const direct = directText(node);
            return strictText ? (normalized === needle || direct === needle) : normalized.includes(needle);
          })
          .slice(0, 8);
        for (const node of matches) {
          const clickable = node.closest("a, button, label, li, [role='button'], [class*='filter'], [class*='checkbox']") || node;
          if (!clickable || !visible(clickable)) continue;
          clickable.scrollIntoView({ block: "center", inline: "center" });
          clickable.click();
          return { ok: true, matched_text: compact(node.innerText || node.textContent || "").slice(0, 80) };
        }
      }
      return { ok: false, matched_text: "" };
    }, { candidateTexts: texts, mode, groupLabel, optionValue, strictText }).catch((error) => ({ ok: false, error: error.message || String(error), matched_text: "" }));

      if (applied.ok) {
        await humanPause(page, pacing.afterFilterClick);
        results.push({
        ...base,
        page_url: page.url(),
        matched_text: normalizeText(applied.matched_text),
        status: "clicked",
        message: "已在1688页面尝试点击/勾选该筛选项",
      });
    } else {
      results.push({
        ...base,
        status: applied.error ? "click_failed" : "not_found",
        message: applied.error ? `点击失败：${applied.error}` : "页面无此筛选项或当前类目未展示该筛选",
      });
    }
  }
  return results;
}

async function applyCategoryFilters(page, filters, query) {
  if (!filters.length) return [];
  const results = [];
  for (const filter of filters) {
    const isDetailPage = /detail\.(m\.)?1688\.com/.test(page.url());
    const categoryPath = String(filter.category_path || filter.tag || filter.label || "").trim();
    const parts = Array.isArray(filter.texts) && filter.texts.length
      ? filter.texts.map((item) => String(item || "").trim()).filter(Boolean)
      : categoryPath.split(">").map((item) => item.trim()).filter(Boolean);
    const base = {
      filter_key: String(filter.key || categoryPath),
      tag: categoryPath,
      label: String(filter.label || `类目:${categoryPath}`),
      source: "1688_category_navigation",
      query,
      page_url: page.url(),
      matched_text: "",
      expected_path: parts.join(">"),
      expected_depth: parts.length,
      matched_path: "",
      matched_depth: 0,
      category_id: "",
      final_url: page.url(),
      category_steps: [],
      diagnostics: {},
    };
    await updateWorkbenchOverlay(page, {
      tone: "working",
      detail: `正在定位类目：${categoryPath || "-"}`,
      meta: `类目任务 ${results.length + 1}/${filters.length}`,
    });
    if (isDetailPage) {
      results.push({
        ...base,
        status: "not_applicable",
        message: "当前为商品详情页URL，无法执行左侧类目点击",
      });
      continue;
    }
    if (!parts.length) {
      results.push({
        ...base,
        status: "not_found",
        message: "类目路径为空，未执行类目点击",
      });
      continue;
    }

    const needleSets = categoryNeedleSets(parts);
    const clickedParts = [];
    const matchedTexts = [];
    const categorySteps = [];
    let applied = { ok: false, matched_text: "", missing_text: parts[0] || "", href: "" };
    let navigationState = {};

    for (let index = 0; index < needleSets.length; index += 1) {
      const needleSet = needleSets[index];
      if (index === 0) {
        await humanPause(page, pacing.beforeFilterClick);
      } else {
        await categoryCascadePause(page);
      }
      const shouldHoverOnly = index < needleSets.length - 1 && /^https?:\/\/(www\.)?1688\.com\/?/.test(page.url());
      const matchOptions = { strict: index === 0, expectedDepth: index + 1 };
      if (shouldHoverOnly) {
        const beforeUrl = page.url();
        applied = await clickCategoryPart(page, needleSet, "hover", matchOptions);
        let recovery = { attempted: false, ok: false, records: [] };
        if (!applied.ok && index > 0) {
          const trigger = {
            status: applied.error ? "click_failed" : "not_found",
            missing_text: applied.missing_text || parts[index] || "",
            error: applied.error || "",
            diagnostics: applied.diagnostics || {},
          };
          recovery = {
            attempted: true,
            trigger,
            ...await reopenCategoryParents(page, needleSets, parts, index),
          };
          if (recovery.ok) {
            await categoryCascadePause(page);
            applied = await clickCategoryPart(page, needleSet, "hover", matchOptions);
          }
        }
        categorySteps.push({
          depth: index + 1,
          expected_text: parts[index] || "",
          needles: needleSet,
          mode: "hover",
          status: applied.ok ? "matched" : (applied.error ? "click_failed" : "not_found"),
          matched_text: normalizeText(applied.matched_text || ""),
          category_id: applied.category_id || "",
          match_type: applied.match_type || "",
          fallback_mode: applied.fallback_mode || "",
          page_url: page.url(),
          page_url_before: beforeUrl,
          page_url_after: page.url(),
          diagnostics: applied.diagnostics || {},
          recovery,
          message: applied.error || "",
        });
        if (!applied.ok) {
          const followed = await followCategoryClick(page, () => clickCategoryLeafLink(page, parts));
          const leafApplied = followed.action_result || {};
          categorySteps.push({
            depth: parts.length,
            expected_text: parts[parts.length - 1] || "",
            needles: categoryNeedles(parts[parts.length - 1] || ""),
            mode: "direct_link",
            status: leafApplied.ok ? "clicked" : (leafApplied.error ? "click_failed" : "not_found"),
            matched_text: normalizeText(leafApplied.matched_text || ""),
            category_id: leafApplied.category_id || "",
            match_type: leafApplied.match_type || "",
            fallback_mode: leafApplied.fallback_mode || "",
            page_url: page.url(),
            page_url_before: beforeUrl,
            page_url_after: followed.page?.url?.() || page.url(),
            href: leafApplied.href || "",
            diagnostics: leafApplied.diagnostics || {},
            message: leafApplied.error || "",
          });
          if (!leafApplied.ok) break;
          clickedParts.splice(0, clickedParts.length, ...parts);
          matchedTexts.splice(0, matchedTexts.length, parts.join(">"));
          page = followed.page;
          runtime.page = page;
          await failIfStillBlocked(page, query, sourceUrl, false);
          navigationState = await readCategoryPageState(page, parts);
          applied = leafApplied;
          break;
        }
        clickedParts.push(parts[index] || needleSet[0] || "");
        matchedTexts.push(applied.matched_text || parts[index] || needleSet[0] || "");
        continue;
      }

      const beforeUrl = page.url();
      let followed = await followCategoryClick(page, () => clickCategoryPart(page, needleSet, "click", matchOptions));
      applied = followed.action_result || {};
      let recovery = { attempted: false, ok: false, records: [] };
      if (!applied.ok && index > 0) {
        const trigger = {
          status: applied.error ? "click_failed" : "not_found",
          missing_text: applied.missing_text || parts[index] || "",
          error: applied.error || "",
          diagnostics: applied.diagnostics || {},
        };
        recovery = {
          attempted: true,
          trigger,
          ...await reopenCategoryParents(page, needleSets, parts, index),
        };
        if (recovery.ok) {
          await categoryCascadePause(page);
          followed = await followCategoryClick(page, () => clickCategoryPart(page, needleSet, "click", matchOptions));
          applied = followed.action_result || {};
        }
      }
      categorySteps.push({
        depth: index + 1,
        expected_text: parts[index] || "",
        needles: needleSet,
        mode: "click",
        status: applied.ok ? "clicked" : (applied.error ? "click_failed" : "not_found"),
        matched_text: normalizeText(applied.matched_text || ""),
        category_id: applied.category_id || "",
        match_type: applied.match_type || "",
        fallback_mode: applied.fallback_mode || "",
        page_url: page.url(),
        page_url_before: beforeUrl,
        page_url_after: followed.page?.url?.() || page.url(),
        href: applied.href || "",
        diagnostics: applied.diagnostics || {},
        recovery,
        message: applied.error || "",
      });
      if (!applied.ok) break;

      const clickedLabel = parts[index] || needleSet[0] || "";
      clickedParts.push(clickedLabel);
      matchedTexts.push(applied.matched_text || clickedLabel);
      page = followed.page;
      runtime.page = page;
      await failIfStillBlocked(page, query, sourceUrl, false);

      navigationState = await readCategoryPageState(page, parts);
      const navigatedToResults = followed.url_changed || followed.opened_page || navigationState.product_anchor_count > 0 || /s\.1688\.com\/selloffer/.test(page.url());
      if (navigatedToResults && categoryNavigationConfirmed(navigationState, parts, applied.category_id) && clickedParts.length >= parts.length) {
        break;
      }
    }

    const matchedText = matchedTexts.join(">");
    const lastDiagnostics = applied.diagnostics
      || categorySteps.slice().reverse().find((step) => step.diagnostics && Object.keys(step.diagnostics).length)?.diagnostics
      || await collectCategoryDiagnostics(page, needleSets[clickedParts.length] || []);
    const clickedCategoryId = applied.category_id
      || categorySteps.slice().reverse().map((step) => step.category_id).find(Boolean)
      || "";
    const confirmed = categoryNavigationConfirmed(navigationState, parts, clickedCategoryId);
    const resolvedCategoryId = navigationState.category_id || clickedCategoryId;
    if (clickedParts.length) {
      applied = {
        ...applied,
        ok: clickedParts.length === parts.length && confirmed,
        partial: clickedParts.length < parts.length,
        matched_text: matchedText,
        missing_text: clickedParts.length < parts.length ? parts[clickedParts.length] : "",
        navigation_state: navigationState,
      };
    }

    if (applied.ok) {
      await humanPause(page, pacing.afterFilterClick);
      results.push({
        ...base,
        page_url: page.url(),
        matched_text: normalizeText(applied.matched_text),
        matched_path: normalizeText(applied.matched_text),
        matched_depth: clickedParts.length,
        category_id: resolvedCategoryId,
        final_url: page.url(),
        category_steps: categorySteps,
        diagnostics: lastDiagnostics,
        navigation_state: navigationState,
        status: applied.partial ? "partial_clicked" : "clicked",
        message: applied.partial
          ? `已点击部分类目并进入1688结果页，未找到下一层：${applied.missing_text || "-"}`
          : "已在1688页面按类目入口点击并进入商品结果页",
      });
    } else {
      const status = clickedParts.length === parts.length && !confirmed && !applied.error
        ? "not_confirmed"
        : (applied.error ? "click_failed" : "not_found");
      results.push({
        ...base,
        status,
        matched_text: normalizeText(applied.matched_text || ""),
        matched_path: normalizeText(matchedText || applied.matched_text || ""),
        matched_depth: clickedParts.length,
        category_id: resolvedCategoryId,
        final_url: page.url(),
        category_steps: categorySteps,
        diagnostics: lastDiagnostics,
        navigation_state: navigationState,
        message: applied.error
          ? `类目点击失败：${applied.error}`
          : clickedParts.length === parts.length && !confirmed
            ? "已点击目标类目，但未确认进入对应商品结果页，已停止避免导出错类目数据"
          : `页面左侧类目中未找到：${applied.missing_text || parts.join(">")}`,
      });
    }
  }
  return results;
}

async function extractProductsFromCurrentPage(page, maxItems, pageIndex) {
  return page.evaluate(({ maxItems, selector, pageIndex }) => {
    const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const normalizeUrl = (value) => {
      const text = String(value || "").trim();
      if (!text) return "";
      if (text.startsWith("//")) return `https:${text}`;
      return text;
    };
    const normalizeOfficialCategoryId = (value) => {
      const text = String(value || "").trim();
      return /^\d{1,32}$/.test(text) ? text : "";
    };
    const extractCategoryId = (...values) => {
      for (const value of values) {
        const raw = String(value || "").trim();
        if (!raw) continue;
        const direct = normalizeOfficialCategoryId(raw);
        if (direct) return direct;
        const variants = [raw];
        try {
          variants.push(decodeURIComponent(raw));
        } catch {
          // Keep the raw value only.
        }
        for (const text of variants) {
          try {
            const parsed = new URL(text, location.href);
            for (const [key, candidate] of parsed.searchParams.entries()) {
              if (["productcategoryid", "leafcategoryid", "categoryid", "catid", "cateid", "category_id", "cat_id", "cate_id"].includes(key.toLowerCase())) {
                const normalized = normalizeOfficialCategoryId(candidate);
                if (normalized) return normalized;
              }
            }
          } catch {
            // Non-URL values may still contain a category key below.
          }
          const match = text.match(/(?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)[^0-9]{0,16}(\d{1,32})/i)
            || text.match(/[?&#](?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)=(\d{1,32})/i);
          if (match) return match[1];
        }
      }
      return "";
    };
    const extractStructuredCategoryId = (value) => {
      const text = String(value || "");
      const dataAttribute = text.match(/\b(?:data-product-category-id|data-productcategoryid|data-category-id|data-categoryid|data-cat-id|data-catid|data-cate-id|data-cateid)\s*=\s*["']?(\d{1,32})["']?/i);
      if (dataAttribute) return normalizeOfficialCategoryId(dataAttribute[1]);
      const property = text.match(/(?:^|[\[{,;\s])(?:["']?(?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)["']?)\s*[:=]\s*["']?(\d{1,32})["']?/i);
      return property ? normalizeOfficialCategoryId(property[1]) : "";
    };
    const decodeStructuredString = (value) => String(value || "")
      .replace(/\\u([0-9a-fA-F]{4})/g, (_, code) => String.fromCharCode(Number.parseInt(code, 16)))
      .replace(/\\\//g, "/")
      .replace(/\\n|\\r|\\t/g, " ")
      .replace(/\\(["'])/g, "$1");
    const cleanCategoryName = (value) => decodeStructuredString(value)
      .replace(/<[^>]*>/g, " ")
      .replace(/&gt;|&rsaquo;|&raquo;/gi, ">")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/\s+/g, " ")
      .replace(/^[>›»→|\-\s]+|[>›»→|\-\s]+$/g, "")
      .trim()
      .slice(0, 120);
    const normalizeBreadcrumb = (value) => {
      const rawItems = Array.isArray(value) ? value : [];
      const names = [];
      for (const item of rawItems) {
        const rawName = typeof item === "string"
          ? item
          : (item?.name || item?.title || item?.text || item?.label || item?.item?.name || "");
        const name = cleanCategoryName(rawName);
        if (!name || /^(首页|1688|阿里巴巴|全部分类|商品详情)$/.test(name)) continue;
        if (names[names.length - 1] !== name) names.push(name);
      }
      return names.slice(0, 10);
    };
    const breadcrumbFromPath = (value) => normalizeBreadcrumb(
      cleanCategoryName(value).split(/\s*(?:>|›|»|→)\s*/).filter(Boolean)
    );
    const normalizeCategoryPath = (value) => breadcrumbFromPath(value).join(">");
    const firstStructuredString = (text, keys, maxLength = 360) => {
      const source = String(text || "");
      for (const key of keys) {
        const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const doubleQuoted = source.match(new RegExp(`"${escaped}"\\s*:\\s*"((?:\\\\.|[^"\\\\]){1,${maxLength}})"`, "i"));
        if (doubleQuoted) return decodeStructuredString(doubleQuoted[1]);
        const singleQuoted = source.match(new RegExp(`'${escaped}'\\s*:\\s*'((?:\\\\.|[^'\\\\]){1,${maxLength}})'`, "i"));
        if (singleQuoted) return decodeStructuredString(singleQuoted[1]);
      }
      return "";
    };
    const structuredBreadcrumb = (text) => {
      const source = String(text || "");
      const match = source.match(/["'](?:productCategoryBreadcrumb|categoryBreadcrumb|breadcrumbs?)["']\s*:\s*(\[[\s\S]{1,2400}?\])/i);
      if (!match) return [];
      try {
        return normalizeBreadcrumb(JSON.parse(match[1]));
      } catch {
        const quoted = Array.from(match[1].matchAll(/["']((?:\\.|[^"']){1,120})["']/g)).map((item) => item[1]);
        return normalizeBreadcrumb(quoted);
      }
    };
    const emptyCategoryEvidence = () => ({
      category_id: "",
      category_path: "",
      category_breadcrumb: [],
      source: "",
    });
    const categoryEvidenceFromText = (text, source) => {
      const sourceText = String(text || "");
      const categoryId = extractStructuredCategoryId(sourceText);
      const structuredPath = firstStructuredString(text, [
        "productCategoryPath",
        "categoryPath",
        "categoryListName",
        "catPath",
      ]);
      const explicitProductName = firstStructuredString(text, [
        "productCategoryName",
        "leafCategoryName",
      ]);
      const genericCategoryName = firstStructuredString(text, [
        "categoryName",
        "catName",
      ]);
      const structuredName = explicitProductName || (categoryId ? genericCategoryName : "");
      let breadcrumb = structuredBreadcrumb(text);
      let categoryPath = normalizeCategoryPath(structuredPath);
      if (!breadcrumb.length && categoryPath) breadcrumb = breadcrumbFromPath(categoryPath);
      if (!categoryPath && breadcrumb.length) categoryPath = breadcrumb.join(">");
      if (!categoryPath && structuredName) {
        breadcrumb = normalizeBreadcrumb([structuredName]);
        categoryPath = breadcrumb.join(">");
      }
      if (!categoryId && !categoryPath && !breadcrumb.length) return emptyCategoryEvidence();
      return {
        category_id: categoryId,
        category_path: categoryPath,
        category_breadcrumb: breadcrumb,
        source,
      };
    };
    const mergeCategoryEvidence = (...values) => {
      const candidates = values.filter((item) => item && (
        item.category_id || item.category_path || (Array.isArray(item.category_breadcrumb) && item.category_breadcrumb.length)
      ));
      if (!candidates.length) return emptyCategoryEvidence();
      const primary = candidates.find((item) => item.category_path || item.category_breadcrumb?.length) || candidates[0];
      const idCandidate = candidates.find((item) => item.category_id);
      const pathCandidate = candidates.find((item) => item.category_path || item.category_breadcrumb?.length) || primary;
      const breadcrumb = normalizeBreadcrumb(
        pathCandidate.category_breadcrumb?.length
          ? pathCandidate.category_breadcrumb
          : breadcrumbFromPath(pathCandidate.category_path)
      );
      return {
        category_id: idCandidate?.category_id || "",
        category_path: normalizeCategoryPath(pathCandidate.category_path) || breadcrumb.join(">"),
        category_breadcrumb: breadcrumb,
        source: primary.source || idCandidate?.source || "",
      };
    };
    const categoryEvidenceFromElement = (root) => {
      if (!root || typeof root.querySelectorAll !== "function") return emptyCategoryEvidence();
      const scoped = [root, ...Array.from(root.querySelectorAll([
        "[data-category-id]",
        "[data-categoryid]",
        "[data-cat-id]",
        "[data-catid]",
        "[data-cate-id]",
        "[data-cateid]",
        "[data-category-path]",
        "[data-categorypath]",
        "[data-category-name]",
        "[data-categoryname]",
        "[data-product-category-id]",
        "[data-productcategoryid]",
        "[data-product-category-path]",
        "[data-productcategorypath]",
        "[data-product-category-name]",
        "[data-productcategoryname]",
      ].join(", "))).slice(0, 40)];
      let directId = "";
      let directPath = "";
      let directName = "";
      const serializedValues = [];
      for (const node of scoped) {
        directId ||= extractCategoryId(
          node.getAttribute?.("data-category-id"),
          node.getAttribute?.("data-categoryid"),
          node.getAttribute?.("data-cat-id"),
          node.getAttribute?.("data-catid"),
          node.getAttribute?.("data-cate-id"),
          node.getAttribute?.("data-cateid"),
          node.getAttribute?.("data-product-category-id"),
          node.getAttribute?.("data-productcategoryid")
        );
        directPath ||= node.getAttribute?.("data-category-path")
          || node.getAttribute?.("data-categorypath")
          || node.getAttribute?.("data-product-category-path")
          || node.getAttribute?.("data-productcategorypath")
          || "";
        directName ||= node.getAttribute?.("data-category-name")
          || node.getAttribute?.("data-categoryname")
          || node.getAttribute?.("data-product-category-name")
          || node.getAttribute?.("data-productcategoryname")
          || "";
        for (const attribute of Array.from(node.attributes || [])) {
          if (/trace|log/i.test(attribute.name)) continue;
          const explicitCategoryAttribute = /^(?:data[-_:])?(?:product[-_:]?)?(?:category(?:[-_:]?(?:id|path|name|breadcrumb))?|cat[-_:]?id|cate[-_:]?id)$/i.test(attribute.name);
          const explicitProductAttribute = /^(?:data[-_:])?(?:product|offer)(?:[-_:]|$)/i.test(attribute.name);
          const structuredCategoryValue = /productCategoryId|productCategoryPath|productCategoryName|productCategoryBreadcrumb|leafCategoryId|leafCategoryName|categoryId|categoryPath|categoryName|categoryBreadcrumb|catId|cateId/i.test(attribute.value);
          if (explicitCategoryAttribute || (explicitProductAttribute && structuredCategoryValue)) {
            serializedValues.push(attribute.value);
          }
        }
      }
      const directBreadcrumb = breadcrumbFromPath(directPath || directName);
      const directEvidence = (directId || directBreadcrumb.length)
        ? {
          category_id: directId,
          category_path: directBreadcrumb.join(">"),
          category_breadcrumb: directBreadcrumb,
          source: "card_data_attribute",
        }
        : emptyCategoryEvidence();
      let hrefEvidence = emptyCategoryEvidence();
      const categoryAnchors = Array.from(root.querySelectorAll(
        "a[href*='categoryId' i], a[href*='catId' i], a[href*='cateId' i], a[href*='category_id' i], a[href*='cat_id' i]"
      )).slice(0, 12);
      for (const anchor of categoryAnchors) {
        const href = anchor.href || anchor.getAttribute?.("href") || "";
        const categoryId = extractCategoryId(href);
        if (!categoryId) continue;
        if (/detail\.(m\.)?1688\.com|\/offer\/\d+/i.test(href)) continue;
        let parsedHref = null;
        try {
          parsedHref = new URL(href, location.href);
        } catch {
          parsedHref = null;
        }
        const linkSemantics = [
          anchor.getAttribute?.("class"),
          anchor.getAttribute?.("id"),
          anchor.getAttribute?.("aria-label"),
          anchor.getAttribute?.("data-testid"),
          anchor.getAttribute?.("data-role"),
          anchor.getAttribute?.("rel"),
        ].filter(Boolean).join(" ");
        const categoryPathLink = parsedHref
          ? /\/(?:category|catalog|cate)(?:\/|\.|$)/i.test(parsedHref.pathname)
          : false;
        const categorySearchLink = parsedHref
          ? parsedHref.hostname === "s.1688.com" && /selloffer|offer_search/i.test(parsedHref.pathname)
          : false;
        const semanticCategoryLink = /category|breadcrumb|crumb|(?:^|[-_:\s])cate(?:[-_:\s]|$)/i.test(linkSemantics);
        if (!categoryPathLink && !categorySearchLink && !semanticCategoryLink) continue;
        const name = cleanCategoryName(anchor.innerText || anchor.textContent || anchor.getAttribute?.("title") || anchor.getAttribute?.("aria-label") || "");
        if (!name || name.length > 60 || /^(更多|查看更多|全部|进入|搜索|商品|详情)$/.test(name)) continue;
        const breadcrumb = normalizeBreadcrumb(name ? [name] : []);
        if (!breadcrumb.length) continue;
        hrefEvidence = {
          category_id: categoryId,
          category_path: breadcrumb.join(">"),
          category_breadcrumb: breadcrumb,
          source: "card_category_href",
        };
        break;
      }
      const serializedEvidence = serializedValues
        .map((value) => categoryEvidenceFromText(value, "card_structured_data"))
        .find((item) => item.category_id || item.category_path || item.category_breadcrumb.length)
        || emptyCategoryEvidence();
      return mergeCategoryEvidence(directEvidence, hrefEvidence, serializedEvidence);
    };
    const structuredScripts = Array.from(document.querySelectorAll("script"))
      .map((script) => script.textContent || "")
      .filter((content) => /categoryId|catId|cateId|categoryPath|categoryListName|categoryBreadcrumb/i.test(content))
      .slice(0, 24);
    const structuredCategoryForOffer = (offerId, source = "card_structured_data") => {
      if (!offerId) return emptyCategoryEvidence();
      for (const content of structuredScripts) {
        let index = content.indexOf(offerId);
        let attempts = 0;
        while (index >= 0 && attempts < 3) {
          const windowText = content.slice(Math.max(0, index - 1400), index + 1800);
          const evidence = categoryEvidenceFromText(windowText, source);
          if (evidence.category_id || evidence.category_path || evidence.category_breadcrumb.length) return evidence;
          index = content.indexOf(offerId, index + offerId.length);
          attempts += 1;
        }
      }
      return emptyCategoryEvidence();
    };
    const jsonLdBreadcrumbEvidence = () => {
      const findBreadcrumbLists = (value, output = []) => {
        if (!value || typeof value !== "object") return output;
        if (Array.isArray(value)) {
          value.forEach((item) => findBreadcrumbLists(item, output));
          return output;
        }
        const type = String(value["@type"] || value.type || "");
        if (/BreadcrumbList/i.test(type)) output.push(value);
        Object.values(value).forEach((item) => findBreadcrumbLists(item, output));
        return output;
      };
      for (const script of Array.from(document.querySelectorAll("script[type='application/ld+json']")).slice(0, 12)) {
        try {
          const parsed = JSON.parse(script.textContent || "null");
          for (const list of findBreadcrumbLists(parsed)) {
            const elements = Array.isArray(list.itemListElement) ? list.itemListElement : [];
            const sorted = [...elements].sort((a, b) => Number(a?.position || 0) - Number(b?.position || 0));
            const categoryElements = [...sorted];
            const productEntry = categoryElements[categoryElements.length - 1] || {};
            const productEntryUrl = typeof productEntry.item === "string"
              ? productEntry.item
              : (productEntry.item?.["@id"] || productEntry.item?.url || productEntry.url || "");
            if (/detail\.(m\.)?1688\.com|\/offer\/\d+/i.test(String(productEntryUrl || ""))) {
              categoryElements.pop();
            }
            const breadcrumb = normalizeBreadcrumb(categoryElements.map((entry) => (
              entry?.name || entry?.item?.name || entry?.item?.title || ""
            )));
            if (!breadcrumb.length) continue;
            const last = categoryElements[categoryElements.length - 1] || {};
            const itemUrl = typeof last.item === "string" ? last.item : (last.item?.["@id"] || last.item?.url || last.url || "");
            return {
              category_id: extractCategoryId(itemUrl),
              category_path: breadcrumb.join(">"),
              category_breadcrumb: breadcrumb,
              source: "detail_json_ld_breadcrumb",
            };
          }
        } catch {
          // Ignore malformed JSON-LD and continue with visible DOM evidence.
        }
      }
      return emptyCategoryEvidence();
    };
    const domBreadcrumbEvidence = () => {
      const roots = Array.from(document.querySelectorAll([
        "nav[aria-label*='breadcrumb' i]",
        "[class*='breadcrumb' i]",
        "[class*='crumb' i]",
        "[data-testid*='breadcrumb' i]",
      ].join(", "))).filter((node) => visible(node));
      let best = null;
      for (const root of roots) {
        const nodes = Array.from(root.querySelectorAll("a[href], li, span, [itemprop='name']")).slice(0, 30);
        const breadcrumbNodes = nodes.filter(visible).filter((node) => {
          const href = String(node.href || node.getAttribute?.("href") || "");
          return !/detail\.(m\.)?1688\.com|\/offer\/\d+/i.test(href);
        });
        const breadcrumb = normalizeBreadcrumb(breadcrumbNodes.map((node) => (
          node.innerText || node.textContent || node.getAttribute?.("title") || ""
        )));
        const lastName = breadcrumb[breadcrumb.length - 1] || "";
        if (lastName.length >= 18 && cleanCategoryName(document.title).includes(lastName)) breadcrumb.pop();
        if (!breadcrumb.length) continue;
        const categoryId = breadcrumbNodes.map((node) => extractCategoryId(
          node.getAttribute?.("data-category-id"),
          node.getAttribute?.("data-categoryid"),
          node.getAttribute?.("data-cat-id"),
          node.getAttribute?.("data-catid"),
          node.href,
          node.getAttribute?.("href")
        )).filter(Boolean).at(-1) || "";
        const candidate = {
          category_id: categoryId,
          category_path: breadcrumb.join(">"),
          category_breadcrumb: breadcrumb,
          source: "detail_dom_breadcrumb",
        };
        if (!best || candidate.category_breadcrumb.length > best.category_breadcrumb.length) best = candidate;
      }
      return best || emptyCategoryEvidence();
    };
    const looksLikeMojibake = (value) => {
      const text = String(value || "");
      return /[ÃÂ�]|(?:\\u00[0-9a-fA-F]{2})/.test(text)
        || (/[åæçèéäöü]/i.test(text) && /[一-龥]/.test(text) === false && text.length > 8);
    };
    const extractOfferId = (value) => {
      const raw = String(value || "");
      if (/^\d{8,}$/.test(raw.trim())) return raw.trim();
      const variants = [raw];
      try {
        variants.push(decodeURIComponent(raw));
      } catch {
        // Keep the raw value only.
      }
      for (const text of variants) {
        const direct = text.match(/offer\/(\d{8,})\.html/i);
        if (direct) return direct[1];
        const fromParam = text.match(/[?&#](?:offerId|offerIds|offer_id)=([0-9]{8,})/i)
          || text.match(/(?:offerId|offerIds|offer_id)[^0-9]{0,8}([0-9]{8,})/i);
        if (fromParam) return fromParam[1];
      }
      return "";
    };
    const cleanTitle = (value) => {
      const raw = String(value || "").replace(/\u00a0/g, " ");
      if (!raw || looksLikeMojibake(raw)) return "";
      const pieces = raw
        .split(/[\n\r|｜]+/)
        .map((item) => normalize(item))
        .filter(Boolean);
      const compactBeforePrice = normalize(raw).replace(/(?:¥|￥).*$/, "").trim();
      const candidates = [...pieces, compactBeforePrice]
        .map((item) => item
          .replace(/^(找相似|进店|立即订购|加入进货单|收藏|广告|¥|￥)\s*/g, "")
          .replace(/(?:¥|￥).*$/, "")
          .replace(/\s*(品类店铺|商机组货|比下游低|全网|退货包运费|限时价|实力商家|源头工厂|先采后付|回头率).*$/, "")
          .trim())
        .filter((item) => item.length >= 4 && item.length <= 160)
        .filter((item) => /[一-龥A-Za-z]/.test(item))
        .filter((item) => !/^(¥|￥)?\d+(?:\s*\.\s*\d+)?(?:元|起)?$/.test(item))
        .filter((item) => !/(成交|评价|回头率|复购率|发货|物流|包邮|起批|付款|买家保障|找相似|进店|立即订购|相似货源|点此可以|卖家交流|网购体验|语音视频)/.test(item.slice(0, 50)))
        .filter((item) => !looksLikeMojibake(item));
      return (candidates[0] || "").slice(0, 120);
    };
    const cleanPrice = (value) => {
      const text = normalize(value);
      const match = text.match(/(?:¥|￥)\s*([0-9]+(?:\s*\.\s*[0-9]+)?(?:\s*[-~]\s*[0-9]+(?:\s*\.\s*[0-9]+)?)?)/)
        || text.match(/\b([0-9]+(?:\s*\.\s*[0-9]+)?)\s*元/);
      return match ? match[1].replace(/\s+/g, "") : "-";
    };
    const visible = (el) => {
      if (!el || typeof el.getBoundingClientRect !== "function") return false;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 1 && rect.height > 1 && style.visibility !== "hidden" && style.display !== "none";
    };
    const cardRootFor = (node) => {
      const preferred = node.closest?.(
        "a.search-offer-wrapper, [class*='search-offer-item'], [class*='search-offer-wrapper'], [class*='major-offer'], [class*='offer-card'], [class*='offer-item'], [class*='cardui'], [data-offer-id], [data-offerid]"
      );
      if (preferred) return preferred;
      return node.closest?.("[class*='offer'], [class*='item'], [class*='card'], [class*='product'], li")
        || node.parentElement
        || node;
    };
    const imageFrom = (root) => {
      const imageNodes = Array.from(root.querySelectorAll?.("img") || []);
      for (const imageNode of imageNodes) {
        const src = normalizeUrl(
          imageNode.currentSrc
          || imageNode.src
          || imageNode.getAttribute("data-src")
          || imageNode.getAttribute("data-lazy-src")
          || imageNode.getAttribute("data-img")
          || ""
        );
        if (src && !/^data:/i.test(src)) return src;
      }
      return "";
    };

    const pageUrl = location.href;
    const detailId = extractOfferId(pageUrl);
    const rows = [];
    const seen = new Set();

    if (detailId && /detail\.(m\.)?1688\.com/.test(pageUrl)) {
      const bodyText = normalize(document.body?.innerText || "");
      const productCategoryEvidence = mergeCategoryEvidence(
        jsonLdBreadcrumbEvidence(),
        domBreadcrumbEvidence(),
        structuredCategoryForOffer(detailId, "detail_structured_data")
      );
      const titleNode = document.querySelector("h1, [class*='title'], [title]");
      const title = cleanTitle(
        titleNode?.getAttribute?.("title")
        || titleNode?.innerText
        || document.title.replace(/[-_].*$/, "")
      );
      const imageNode = document.querySelector("img[src], img[data-src], img[data-lazy-src]");
      rows.push({
        id: detailId,
        title: title || `1688商品 ${detailId}`,
        price: cleanPrice(bodyText),
        image: imageNode
          ? normalizeUrl(imageNode.currentSrc || imageNode.src || imageNode.getAttribute("data-src") || imageNode.getAttribute("data-lazy-src") || "")
          : "",
        url: `https://detail.1688.com/offer/${detailId}.html`,
        stats: {
          rawText: bodyText.slice(0, 500),
          rawHref: pageUrl,
          categoryId: productCategoryEvidence.category_id,
          categoryName: productCategoryEvidence.category_breadcrumb.at(-1) || "",
          categoryListName: productCategoryEvidence.category_path,
          productCategoryId: productCategoryEvidence.category_id,
          productCategoryPath: productCategoryEvidence.category_path,
          productCategoryBreadcrumb: productCategoryEvidence.category_breadcrumb,
          productCategorySource: productCategoryEvidence.source,
          pageIndex,
        },
      });
      return rows.slice(0, maxItems);
    }

    const nodes = Array.from(document.querySelectorAll(selector));
    for (const node of nodes) {
      const anchor = node.matches?.("a")
        ? node
        : node.querySelector?.('a[href*="detail.1688.com/offer/"], a[href*="/offer/"], a[href*="offerId="], a[href*="offerIds="], a[href*="detail.m.1688.com/page/index.html"]');
      const root = cardRootFor(node);
      const hrefCandidates = [
        node.href,
        node.getAttribute?.("href"),
        anchor?.href,
        anchor?.getAttribute?.("href"),
        node.getAttribute?.("data-offer-id"),
        node.getAttribute?.("data-offerid"),
        node.dataset?.offerId,
        node.dataset?.offerid,
        root.getAttribute?.("data-offer-id"),
        root.getAttribute?.("data-offerid"),
        root.dataset?.offerId,
        root.dataset?.offerid,
        ...Array.from(root.querySelectorAll?.("a[href]") || []).map((item) => item.href || item.getAttribute("href") || ""),
      ].filter(Boolean);
      const id = hrefCandidates.map(extractOfferId).find(Boolean) || "";
      if (!id || seen.has(id) || !visible(root)) continue;

      const rootText = root.innerText || anchor?.innerText || node.innerText || "";
      const titleCandidates = [
        rootText,
        anchor?.innerText,
        node.innerText,
        anchor?.getAttribute?.("title"),
        anchor?.getAttribute?.("aria-label"),
        node.getAttribute?.("title"),
        node.getAttribute?.("aria-label"),
        ...Array.from(root.querySelectorAll?.("[title], img[alt]") || []).flatMap((item) => [
          item.getAttribute("title"),
          item.getAttribute("alt"),
        ]),
      ].filter(Boolean);
      const title = titleCandidates.map(cleanTitle).find(Boolean) || "";
      if (!title || title.length < 4) continue;

      const rawHref = normalizeUrl(hrefCandidates.find((item) => /detail|offerId|offerIds|offer\//i.test(String(item))) || "");
      const productCategoryEvidence = mergeCategoryEvidence(
        categoryEvidenceFromElement(root),
        structuredCategoryForOffer(id)
      );
      seen.add(id);
      rows.push({
        id,
        title,
        price: cleanPrice(rootText),
        image: imageFrom(root),
        url: `https://detail.1688.com/offer/${id}.html`,
        stats: {
          rawText: normalize(rootText).slice(0, 500),
          rawHref,
          categoryId: productCategoryEvidence.category_id,
          categoryName: productCategoryEvidence.category_breadcrumb.at(-1) || "",
          categoryListName: productCategoryEvidence.category_path,
          productCategoryId: productCategoryEvidence.category_id,
          productCategoryPath: productCategoryEvidence.category_path,
          productCategoryBreadcrumb: productCategoryEvidence.category_breadcrumb,
          productCategorySource: productCategoryEvidence.source,
          pageIndex,
        },
      });
      if (rows.length >= maxItems) break;
    }
    return rows;
  }, { maxItems, selector: productCardSelector, pageIndex });
}

async function resultPageFingerprint(page) {
  return page.evaluate(({ selector }) => {
    const values = [];
    const seen = new Set();
    for (const node of Array.from(document.querySelectorAll(selector))) {
      const href = String(node.href || node.getAttribute?.("href") || "");
      const offerId = String(
        node.getAttribute?.("data-offer-id")
        || node.getAttribute?.("data-offerid")
        || href.match(/(?:offer\/|offerId=|offerIds=)(\d{6,})/i)?.[1]
        || ""
      ).trim();
      const value = offerId || href.split("#")[0];
      if (!value || seen.has(value)) continue;
      seen.add(value);
      values.push(value);
      if (values.length >= 12) break;
    }
    return {
      url: location.href,
      key: values.join("|"),
      offer_count: values.length,
    };
  }, { selector: productCardSelector }).catch(() => ({
    url: page.url(),
    key: "",
    offer_count: 0,
  }));
}

async function goToNextResultPage(page, query, sourceUrl, nextPageNumber) {
  if (!/s\.1688\.com\/selloffer|1688\.com/.test(page.url()) || /detail\.(m\.)?1688\.com/.test(page.url())) {
    return { ok: false, method: "not_search_page", message: "当前不是可翻页的搜索结果页" };
  }
  await humanPause(page, pacing.beforeFilterClick);
  const beforeUrl = page.url();
  const beforeFingerprint = await resultPageFingerprint(page);
  let clicked = await page.evaluate(({ nextPageNumber }) => {
    const compact = (value) => String(value || "").replace(/\s+/g, "").trim();
    const visible = (el) => {
      if (!el || typeof el.getBoundingClientRect !== "function") return false;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const disabled = (el) => {
      const className = String(el.className || "");
      return el.disabled
        || el.getAttribute?.("aria-disabled") === "true"
        || /disabled|disable|current/.test(className);
    };
    const paginationRoots = Array.from(document.querySelectorAll([
      "[class*='pagination' i]",
      "[class*='pager' i]",
      "[aria-label*='pagination' i]",
      "[aria-label*='分页']",
      "nav",
    ].join(", "))).filter(visible);
    const sourceRoots = paginationRoots.length ? paginationRoots : [document.body];
    const nodes = [...new Set(sourceRoots.flatMap((root) => Array.from(root.querySelectorAll("a, button, li, span, div"))))]
      .filter((node) => visible(node) && !disabled(node));
    const textOf = (node) => compact(
      node.innerText
      || node.textContent
      || node.getAttribute?.("aria-label")
      || node.getAttribute?.("title")
      || ""
    );
    const nextNode = nodes.find((node) => {
      const text = textOf(node);
      const attrs = compact([
        node.getAttribute?.("aria-label"),
        node.getAttribute?.("title"),
        node.getAttribute?.("rel"),
        node.getAttribute?.("class"),
      ].filter(Boolean).join(" "));
      return /^(下一页|下页|›|»|>)$/.test(text)
        || /下一页|下页|next|pager-next|pagination-next/.test(attrs);
    });
    const numberNode = nodes.find((node) => textOf(node) === String(nextPageNumber));
    const target = numberNode || nextNode;
    if (!target) return { ok: false, method: "dom_click", message: "页面未找到下一页按钮或页码" };
    const clickable = target.closest?.("a, button, [role='button'], li") || target;
    clickable.scrollIntoView({ block: "center", inline: "center" });
    clickable.click();
    return {
      ok: true,
      method: target === numberNode ? "page_number" : "next_button",
      matched_text: textOf(target).slice(0, 50),
      href: clickable.href || clickable.getAttribute?.("href") || "",
    };
  }, { nextPageNumber }).catch((error) => ({ ok: false, method: "dom_click", message: error.message || String(error) }));

  if (clicked.ok) {
    await humanPause(page, pacing.afterSearchSubmit);
    await page.waitForLoadState("domcontentloaded", { timeout: Math.min(pageTimeoutMs, 15000) }).catch(() => {});
    await failIfStillBlocked(page, query, sourceUrl, false);
    let afterFingerprint = await resultPageFingerprint(page);
    const deadline = Date.now() + Math.min(pageTimeoutMs, 15000);
    while (
      Date.now() < deadline
      && page.url() === beforeUrl
      && beforeFingerprint.key
      && afterFingerprint.key === beforeFingerprint.key
    ) {
      await page.waitForTimeout(500);
      afterFingerprint = await resultPageFingerprint(page);
    }
    const pageChanged = page.url() !== beforeUrl
      || (Boolean(afterFingerprint.key) && afterFingerprint.key !== beforeFingerprint.key);
    if (pageChanged) {
      return {
        ...clicked,
        page_url: page.url(),
        url_changed: page.url() !== beforeUrl,
        fingerprint_changed: afterFingerprint.key !== beforeFingerprint.key,
      };
    }
    clicked = {
      ...clicked,
      ok: false,
      method: "page_not_changed",
      message: "点击分页控件后商品列表没有变化，已停止避免重复采集同一页",
    };
  }

  if (/s\.1688\.com\/selloffer/.test(beforeUrl)) {
    try {
      const nextUrl = new URL(beforeUrl);
      nextUrl.searchParams.set("beginPage", String(nextPageNumber));
      await gotoWithTimeout(page, nextUrl.toString(), query, sourceUrl, `1688 搜索第 ${nextPageNumber} 页`);
      await humanPause(page, pacing.afterSearchSubmit);
      await failIfStillBlocked(page, query, sourceUrl, false);
      return { ok: true, method: "beginPage_url", page_url: page.url(), url_changed: page.url() !== beforeUrl };
    } catch (error) {
      return { ok: false, method: "beginPage_url", message: error.message || String(error) };
    }
  }
  return clicked;
}

async function collectProductsAcrossPages(page, maxItems, query, sourceUrl, categoryContext = {}) {
  const products = [];
  const seen = new Set();
  const pageMeta = [];
  const isDetailPage = () => /detail\.(m\.)?1688\.com/.test(page.url());
  let stopReason = "max_pages_reached";

  for (let pageIndex = 1; pageIndex <= maxResultPages && products.length < maxItems; pageIndex += 1) {
    await updateWorkbenchOverlay(page, {
      tone: "working",
      detail: `正在采集第 ${pageIndex}/${maxResultPages} 页商品`,
      meta: `当前已采集 ${products.length}/${maxItems} 个候选\n${categoryContext.category_path ? `类目：${categoryContext.category_path}` : `关键词：${query || "-"}`}`,
    });
    if (!isDetailPage() && pageIndex === 1) {
      await waitForProductAnchors(page, query, sourceUrl, categoryContext);
    }
    await humanScroll(page);
    await humanPause(page, pacing.beforeExtract);

    const rows = await extractProductsFromCurrentPage(page, maxItems - products.length, pageIndex);
    let acceptedCount = 0;
    for (const item of rows) {
      const id = String(item.id || "").trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      products.push(item);
      acceptedCount += 1;
      if (products.length >= maxItems) break;
    }

    const meta = {
      page_index: pageIndex,
      page_url: page.url(),
      extracted_count: rows.length,
      accepted_count: acceptedCount,
    };
    pageMeta.push(meta);
    await updateWorkbenchOverlay(page, {
      tone: "working",
      detail: `第 ${pageIndex} 页已读取 ${acceptedCount} 个新商品`,
      meta: `累计 ${products.length}/${maxItems} 个候选`,
    });

    if (products.length >= maxItems) {
      stopReason = "requested_limit_reached";
      break;
    }
    if (isDetailPage()) {
      stopReason = "detail_page_single_item";
      break;
    }
    if (acceptedCount === 0 && pageIndex > 1) {
      stopReason = "duplicate_or_empty_page";
      break;
    }
    if (pageIndex >= maxResultPages) {
      stopReason = "max_pages_reached";
      break;
    }

    const nextResult = await goToNextResultPage(page, query, sourceUrl, pageIndex + 1);
    meta.next_page = nextResult;
    if (!nextResult.ok) {
      stopReason = nextResult.method || "next_page_unavailable";
      break;
    }
  }

  return {
    products,
    page_meta: pageMeta,
    pagination: {
      requested_limit: maxItems,
      max_pages: maxResultPages,
      pages_visited: pageMeta.length,
      collected_count: products.length,
      stop_reason: stopReason,
    },
  };
}

async function runCategoryScrollSelfTest() {
  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    browser = await chromium.launch({ headless: true });
  }
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  const menuHtml = ({ includeLazyTarget }) => `
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body{margin:0;height:2400px;font:14px sans-serif;background:#f5f5f5}
          .cate_content--self-test.category-menu{position:fixed;left:40px;top:80px;width:280px;height:130px;overflow-y:auto;background:#fff;border:1px solid #ddd}
          .category-spacer{position:relative;height:1050px;padding:8px;box-sizing:border-box}
          .category-row{height:38px;line-height:38px}
          #lazy-target{position:absolute;left:12px;width:180px;height:32px;line-height:32px;background:#e8f3ff;color:#1677ff;text-decoration:none;display:none}
        </style>
      </head>
      <body>
        <div id="menu" class="cate_content--self-test category-menu">
          <div class="category-spacer">
            <div class="category-row">餐具</div>
            <div class="category-row">杯具茶具</div>
            ${includeLazyTarget ? '<a id="lazy-target" data-category-id="123456" href="https://s.1688.com/selloffer/offer_search.htm?categoryId=123456">碗</a>' : ''}
          </div>
        </div>
        <script>
          window.__categoryClickCount = 0;
          const menu = document.getElementById("menu");
          const target = document.getElementById("lazy-target");
          if (target) {
            target.addEventListener("click", (event) => {
              event.preventDefault();
              window.__categoryClickCount += 1;
            });
            const revealLazyTarget = () => {
              if (menu.scrollTop >= 210) {
                target.style.top = (menu.scrollTop + 42) + "px";
                target.style.display = "block";
              }
            };
            menu.addEventListener("scroll", revealLazyTarget);
            window.setInterval(revealLazyTarget, 20);
          }
        </script>
      </body>
    </html>`;
  const cascadeHtml = `
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body{margin:0;height:900px;font:14px sans-serif;background:#f5f5f5}
          .category-l1{position:fixed;left:40px;top:80px;width:220px;height:44px;line-height:44px;background:#fff;border:1px solid #ddd;padding-left:12px}
          .cate_content--cascade-test{position:absolute;left:220px;top:0;width:360px;height:180px;overflow-y:auto;scroll-behavior:smooth;background:#fff;border:1px solid #ccc;display:none;padding:14px;box-sizing:border-box;line-height:38px}
          .category-cascade-spacer{position:relative;height:520px}
          .category-l2-group{position:relative;margin-top:260px;height:96px;background:#f7f9fc;list-style:none}
          .cTitle--cascade-test{display:block;width:90px;height:96px;line-height:34px;background:#f2f7ff;color:#222;text-decoration:none}
          #cascade-leaf-panel{position:absolute;left:110px;top:0;width:180px;height:80px;display:none;background:#fff}
          #plate-target{display:block;width:120px;height:34px;line-height:34px;color:#1677ff}
        </style>
      </head>
      <body>
        <div id="cascade-l1" class="category-l1">日用餐厨、居家日用
          <div id="cascade-flyout" class="cate_content--cascade-test category-menu">
            <div class="category-cascade-spacer">
              <li id="cascade-l2-group" class="category-l2-group">
                <a id="cascade-l2" class="cTitle--cascade-test" href="javascript:void(0)">餐具</a>
                <div id="cascade-leaf-panel" class="category-menu">
                  <a id="plate-target" data-category-id="654321" href="https://s.1688.com/selloffer/offer_search.htm?categoryId=654321">盘</a>
                </div>
              </li>
            </div>
          </div>
        </div>
        <script>
          window.__plateClickCount = 0;
          const l1 = document.getElementById("cascade-l1");
          const l2 = document.getElementById("cascade-l2");
          const flyout = document.getElementById("cascade-flyout");
          const leafPanel = document.getElementById("cascade-leaf-panel");
          const plate = document.getElementById("plate-target");
          let closeTimer = 0;
          window.__l2HoverCount = 0;
          window.__suppressLeafOpen = false;
          l1.addEventListener("mouseenter", () => {
            clearTimeout(closeTimer);
            flyout.style.display = "block";
          });
          l1.addEventListener("mouseleave", () => {
            flyout.style.display = "none";
            leafPanel.style.display = "none";
          });
          l2.addEventListener("mouseenter", () => {
            window.__l2HoverCount += 1;
            clearTimeout(closeTimer);
            if (window.__suppressLeafOpen) return;
            leafPanel.style.display = "block";
            closeTimer = window.setTimeout(() => {
              leafPanel.style.display = "none";
            }, 1800);
          });
          plate.addEventListener("click", (event) => {
            event.preventDefault();
            window.__plateClickCount += 1;
          });
        </script>
      </body>
    </html>`;
  try {
    await page.setContent(menuHtml({ includeLazyTarget: true }), { waitUntil: "domcontentloaded" });
    await page.evaluate(() => window.scrollTo(0, 360));
    const pageScrollBeforeFound = await page.evaluate(() => window.scrollY);
    const found = await clickCategoryPart(page, ["碗"], "click", { strict: true, expectedDepth: 3 });
    const foundState = await page.evaluate(() => ({
      page_scroll_y: window.scrollY,
      click_count: window.__categoryClickCount,
      menu_scroll_top: document.getElementById("menu")?.scrollTop || 0,
    }));

    await page.setContent(menuHtml({ includeLazyTarget: false }), { waitUntil: "domcontentloaded" });
    await page.evaluate(() => window.scrollTo(0, 420));
    const pageScrollBeforeMissing = await page.evaluate(() => window.scrollY);
    const missing = await clickCategoryPart(page, ["不存在的类目"], "click", { strict: true, expectedDepth: 3 });
    const missingState = await page.evaluate(() => ({
      page_scroll_y: window.scrollY,
      menu_scroll_top: document.getElementById("menu")?.scrollTop || 0,
      menu_scroll_max: Math.max(
        0,
        (document.getElementById("menu")?.scrollHeight || 0) - (document.getElementById("menu")?.clientHeight || 0)
      ),
    }));
    const foundScan = found.diagnostics?.category_scroll_scan || {};
    const missingScan = missing.diagnostics?.category_scroll_scan || {};

    await page.setContent(cascadeHtml, { waitUntil: "domcontentloaded" });
    const cascadeParts = ["日用餐厨、居家日用", "餐具", "盘"];
    const cascadeNeedles = categoryNeedleSets(cascadeParts);
    const openedL1 = await clickCategoryPart(page, cascadeNeedles[0], "hover", { strict: true, expectedDepth: 1 });
    await categoryCascadePause(page);
    const openedL2 = await clickCategoryPart(page, cascadeNeedles[1], "hover", { strict: false, expectedDepth: 2 });
    await page.waitForTimeout(1900);
    const closedLeaf = await clickCategoryPart(page, cascadeNeedles[2], "click", { strict: false, expectedDepth: 3 });
    const recoveredParents = await reopenCategoryParents(page, cascadeNeedles, cascadeParts, 2);
    await categoryCascadePause(page);
    const recoveredLeaf = await clickCategoryPart(page, cascadeNeedles[2], "click", { strict: false, expectedDepth: 3 });
    await page.evaluate(() => {
      window.__suppressLeafOpen = true;
      document.getElementById("cascade-leaf-panel").style.display = "none";
    });
    const nonVisibleRecovery = await reopenCategoryParents(page, cascadeNeedles, cascadeParts, 2);
    const cascadeState = await page.evaluate(() => ({
      click_count: window.__plateClickCount,
      flyout_display: window.getComputedStyle(document.getElementById("cascade-flyout")).display,
      leaf_panel_display: window.getComputedStyle(document.getElementById("cascade-leaf-panel")).display,
      l2_hover_count: window.__l2HoverCount,
      flyout_scroll_top: document.getElementById("cascade-flyout").scrollTop,
    }));
    const hiddenLeafMatches = closedLeaf.diagnostics?.hidden_exact_matches || [];
    const l2PointerTarget = openedL2.diagnostics?.pointer_target_element || {};
    const l2TextHitBox = openedL2.diagnostics?.pointer_text_hit_box || {};
    const l2CategoryScroll = openedL2.diagnostics?.category_scroll || {};
    const success = Boolean(
      found.ok
      && foundState.click_count === 1
      && foundState.menu_scroll_top > 0
      && foundScan.attempts > 0
      && foundState.page_scroll_y === pageScrollBeforeFound
      && !missing.ok
      && missingScan.attempts > 0
      && missingScan.attempts <= categoryScrollMaxSteps
      && ["no_remaining_category_scroll_container", "category_scroll_max_steps_reached"].includes(missingScan.stop_reason)
      && missingState.page_scroll_y === pageScrollBeforeMissing
      && openedL1.ok
      && openedL2.ok
      && l2PointerTarget.tag === "a"
      && Number(l2PointerTarget.rect?.height || 0) >= 90
      && Number(l2TextHitBox.height || 0) > 0
      && Number(l2TextHitBox.height || 0) < Number(l2PointerTarget.rect?.height || 0)
      && Number(l2CategoryScroll.container_scroll_top || 0) > 0
      && l2CategoryScroll.target_within_container
      && openedL2.diagnostics?.pointer_route === "category_container_corridor"
      && openedL2.diagnostics?.pointer_target_strategy === "direct_text_range"
      && !closedLeaf.ok
      && hiddenLeafMatches.some((item) => item.text === "盘" && item.menu_display === "none")
      && recoveredParents.ok
      && recoveredParents.status === "reopened"
      && recoveredParents.next_target?.visible
      && recoveredLeaf.ok
      && !nonVisibleRecovery.ok
      && nonVisibleRecovery.status === "reopen_not_visible"
      && !nonVisibleRecovery.next_target?.visible
      && cascadeState.click_count === 1
      && cascadeState.l2_hover_count >= 3
    );
    console.log(JSON.stringify({
      success,
      self_test: "category_scroll",
      found: {
        ok: Boolean(found.ok),
        click_count: foundState.click_count,
        menu_scroll_top: foundState.menu_scroll_top,
        page_scroll_before: pageScrollBeforeFound,
        page_scroll_after: foundState.page_scroll_y,
        attempts: foundScan.attempts || 0,
        stop_reason: foundScan.stop_reason || "",
      },
      missing: {
        ok: Boolean(missing.ok),
        menu_scroll_top: missingState.menu_scroll_top,
        menu_scroll_max: missingState.menu_scroll_max,
        page_scroll_before: pageScrollBeforeMissing,
        page_scroll_after: missingState.page_scroll_y,
        attempts: missingScan.attempts || 0,
        max_steps: missingScan.max_steps || categoryScrollMaxSteps,
        stop_reason: missingScan.stop_reason || "",
      },
      reopen: {
        parents_opened_initially: Boolean(openedL1.ok && openedL2.ok),
        l2_pointer_target: l2PointerTarget,
        l2_text_hit_box: l2TextHitBox,
        l2_menu_scroll_top: Number(l2CategoryScroll.container_scroll_top || 0),
        l2_target_within_container: Boolean(l2CategoryScroll.target_within_container),
        l2_pointer_route: openedL2.diagnostics?.pointer_route || "",
        l2_pointer_target_strategy: openedL2.diagnostics?.pointer_target_strategy || "",
        leaf_failed_after_timeout: !closedLeaf.ok,
        hidden_match_count: hiddenLeafMatches.length,
        parents_reopened: Boolean(recoveredParents.ok),
        recovery_status: recoveredParents.status || "",
        next_target_visible: Boolean(recoveredParents.next_target?.visible),
        non_visible_recovery_ok: Boolean(nonVisibleRecovery.ok),
        non_visible_recovery_status: nonVisibleRecovery.status || "",
        non_visible_recovery_target_visible: Boolean(nonVisibleRecovery.next_target?.visible),
        leaf_clicked_after_reopen: Boolean(recoveredLeaf.ok),
        click_count: cascadeState.click_count,
        flyout_display: cascadeState.flyout_display,
        leaf_panel_display: cascadeState.leaf_panel_display,
        l2_hover_count: cascadeState.l2_hover_count,
        flyout_scroll_top: cascadeState.flyout_scroll_top,
      },
    }));
  } finally {
    await browser.close().catch(() => {});
  }
}

if (selfTestMode === "category_scroll") {
  await runCategoryScrollSelfTest();
  process.exit(0);
}

const runtime = await openRuntime();
let page = runtime.page;
page.setDefaultTimeout(Math.max(15000, Math.min(45000, pageTimeoutMs)));
await updateWorkbenchOverlay(page, {
  tone: "working",
  detail: "采集任务已启动，正在准备 1688 页面",
  meta: `目标候选：${limit} 个 · 最多 ${maxResultPages} 页`,
});

try {
  const hasCategoryNavigation = categoryFilters.length > 0 && !sourceUrl;
  if (manualUrlOnly) {
    await humanPause(page, pacing.beforeExtract);
  } else if (sourceUrl) {
    await gotoWithTimeout(page, sourceUrl, query, sourceUrl, "1688 页面");
    await humanPause(page, pacing.afterGoto);
  } else if (hasCategoryNavigation) {
    page = await openHomeForCategory(page, query, sourceUrl);
  } else if (query) {
    page = await submitHumanSearch(page, query, sourceUrl);
  } else {
    page = await openHomeForCategory(page, query, sourceUrl);
  }

  const pageText = await readBodyText(page);
  const needsManualGate = looksLikeSecurityPage(pageText, page.url()) || looksLikeLoginPage(pageText, page.url());
  if (needsManualGate && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
  }
  await failIfStillBlocked(page, query, sourceUrl, needsManualGate && loginWaitMs > 0 && !headless);

  await humanPause(page, pacing.beforeExtract);
  const categoryResults = manualUrlOnly
    ? categoryFilters.map((filter) => {
      const categoryPath = String(filter.category_path || filter.tag || filter.label || "").trim();
      const parts = Array.isArray(filter.texts) && filter.texts.length
        ? filter.texts.map((item) => String(item || "").trim()).filter(Boolean)
        : categoryPath.split(">").map((item) => item.trim()).filter(Boolean);
      return {
        filter_key: String(filter.key || categoryPath),
        tag: categoryPath,
        label: String(filter.label || `类目:${categoryPath}`),
        source: "manual_page",
        query,
        page_url: page.url(),
        matched_text: "",
        expected_path: parts.join(">"),
        expected_depth: parts.length,
        matched_path: "",
        matched_depth: 0,
        category_id: "",
        final_url: page.url(),
        status: "manual_skipped",
        message: "人工页面读取模式不自动点击 1688 类目；请确认当前 1688 页面已由人工进入对应类目。",
      };
    })
    : await applyCategoryFilters(page, categoryFilters, query);
  page = runtime.page || page;
  await failIfStillBlocked(page, query, sourceUrl, false);
  const blockingCategoryResult = categoryResults.find((record) => (
    record.source === "1688_category_navigation"
    && !["clicked", "not_applicable"].includes(String(record.status || ""))
  ));
  if (categoryFilters.length && blockingCategoryResult) {
    await runtime.close().catch(() => {});
    failWithCode(
      "category_navigation_not_loaded",
      `1688 类目导航未完成，未生成任何数据。当前类目：${blockingCategoryResult.tag || blockingCategoryResult.label || "-"}`,
      {
        source: "1688_category_navigation",
        cdp: Boolean(cdpUrl),
        query,
        source_url: sourceUrl,
        page_url: page.url(),
        category_path: blockingCategoryResult.tag || blockingCategoryResult.label || "",
        filter_results: categoryResults,
        diagnostics: blockingCategoryResult.diagnostics || {},
      }
    );
  }
  const filterResults = [
    ...categoryResults,
    ...(
      manualUrlOnly
        ? nativeFilters.map((filter) => ({
          filter_key: String(filter.key || filter.label || filter.tag || ""),
          label: String(filter.label || filter.tag || filter.key || ""),
          tag: String(filter.tag || filter.label || ""),
          source: "manual_page",
          query,
          page_url: page.url(),
          matched_text: "",
          status: "manual_skipped",
          message: "人工页面读取模式不自动点击 1688 筛选；请确认当前页面已由人工完成筛选。",
        }))
        : await applyNativeFilters(page, nativeFilters, query)
    ),
  ];
  await failIfStillBlocked(page, query, sourceUrl, false);
  const categoryContext = categoryFilters.length
    ? {
      category_path: categoryFilters.map((item) => item.category_path || item.tag || item.label || "").filter(Boolean).join("、"),
      filter_results: categoryResults,
      diagnostics: categoryResults.find((item) => item.diagnostics)?.diagnostics || {},
    }
    : {};
  const collectResult = await collectProductsAcrossPages(page, limit, query, sourceUrl, categoryContext);
  const products = collectResult.products;
  const navigationCategoryId = categoryResults
    .map((record) => String(record.category_id || record.navigation_state?.category_id || "").trim())
    .find(Boolean) || "";

  const latestText = await readBodyText(page, pageText);
  if (looksLikeBlockedPage(latestText, page.url())) {
    await failIfStillBlocked(page, query, sourceUrl, needsManualGate && loginWaitMs > 0 && !headless);
  }

  await updateWorkbenchOverlay(page, {
    tone: "done",
    detail: "列表采集完成，正在把结果返回工作台",
    meta: `共读取 ${collectResult.pagination.pages_visited} 页 · ${products.length} 个候选`,
  });
  await page.waitForTimeout(500).catch(() => {});
  await runtime.close();
  console.log(JSON.stringify({
    success: true,
    source: "1688_search_page",
    cdp: Boolean(cdpUrl),
    query,
    source_url: sourceUrl,
    page_url: page.url(),
    category_id: navigationCategoryId,
    max_pages: maxResultPages,
    page_meta: collectResult.page_meta,
    pagination: collectResult.pagination,
    filter_results: filterResults,
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
