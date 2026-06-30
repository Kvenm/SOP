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
const nativeFilters = Array.isArray(input.native_filters) ? input.native_filters : [];
const categoryFilters = Array.isArray(input.category_filters) ? input.category_filters : [];
const manualUrlOnly = Boolean(input.manual_url_only);
if (!manualUrlOnly && !query && !sourceUrl && !categoryFilters.length) fail("缺少搜索词、类目或 1688 页面 URL，无法打开真实页面");

const profileDir = process.env.TAG_COLLECT_RPA_PROFILE
  || path.join(os.homedir(), ".sop-1688-rpa-profile");
const headless = process.env.TAG_COLLECT_RPA_HEADLESS === "1";
const cdpUrl = process.env.TAG_COLLECT_CDP_URL || "";
const loginWaitMs = Number(process.env.TAG_COLLECT_RPA_LOGIN_WAIT_MS || (cdpUrl ? 15000 : 30000));
const pageTimeoutMs = Number(process.env.TAG_COLLECT_RPA_PAGE_TIMEOUT_MS || 30000);
const pacingMode = String(process.env.TAG_COLLECT_RPA_PACING || "human").toLowerCase();
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
const maxResultPages = Math.max(
  1,
  manualUrlOnly ? 1 : Math.min(8, Number(input.max_pages || process.env.TAG_COLLECT_RPA_MAX_PAGES || 3) || 3)
);

function jitter([min, max]) {
  const low = Math.max(0, Number(min) || 0);
  const high = Math.max(low, Number(max) || low);
  return Math.round(low + Math.random() * (high - low));
}

async function humanPause(page, range) {
  await page.waitForTimeout(jitter(range));
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

function blockedMessage(url, waited) {
  const prefix = waited ? "已等待你处理登录/验证，但当前页面仍需要登录或安全校验。" : "当前页面需要登录或安全校验。";
  return `${prefix}请先在真实 Chrome/1688 页面完成登录和验证后重试；如果账号一直过不了校验，可以粘贴一个浏览器里能直接打开的 1688 搜索/商品链接做公开页面真实数据测试。当前页面：${url}`;
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

function securityMessage(url, waited) {
  const prefix = waited ? "已等待你手动处理 1688 安全验证，但当前页面仍停留在滑块/验证码校验。" : "1688 触发了安全滑块/验证码校验。";
  return `${prefix}系统不会绕过或自动破解验证，也不会继续采集以免导出不可信数据。请在弹出的真实浏览器中手动拖动滑块/完成验证，或使用已登录且已通过验证的 Chrome CDP 会话后重试。当前页面：${url}`;
}

async function readBodyText(page, fallback = "") {
  return page.locator("body").innerText({ timeout: 10000 }).catch(() => fallback);
}

async function failIfStillBlocked(page, query, sourceUrl, waited) {
  const text = await readBodyText(page);
  const url = page.url();
  if (looksLikeSecurityPage(text, url)) {
    await runtime.close().catch(() => {});
    failWithCode("security_verification_required", securityMessage(url, waited), {
      source: "1688_search_page",
      cdp: Boolean(cdpUrl),
      query,
      source_url: sourceUrl,
      page_url: url,
    });
  }
  if (looksLikeLoginPage(text, url)) {
    await runtime.close().catch(() => {});
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
  return { page, close: async () => context.close() };
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

async function gotoWithTimeout(page, url, query, sourceUrl, purpose) {
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: pageTimeoutMs });
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
    const matchedExpectedParts = expected.filter((part) => part && (bodyText.includes(part) || compact(title).includes(part)));
    const anchors = Array.from(document.querySelectorAll(selector));
    return {
      page_url: url,
      title,
      product_anchor_count: anchors.length,
      leaf_in_url: leaf ? compact(decodedUrl).includes(leaf) : false,
      leaf_in_page: leaf ? bodyText.includes(leaf) || compact(title).includes(leaf) : false,
      matched_expected_parts: matchedExpectedParts,
    };
  }, { expectedParts, selector: productCardSelector }).catch(() => ({
    page_url: page.url(),
    title: "",
    product_anchor_count: 0,
    leaf_in_url: false,
    leaf_in_page: false,
    matched_expected_parts: [],
  }));
}

function categoryNavigationConfirmed(state, expectedParts = []) {
  const parts = Array.isArray(expectedParts) ? expectedParts.filter(Boolean) : [];
  if (!parts.length) return false;
  const onResultUrl = /s\.1688\.com\/selloffer|offer_search/.test(String(state.page_url || ""));
  const matchedCount = (state.matched_expected_parts || []).length;
  return Boolean(
    onResultUrl
    && (state.leaf_in_url || state.leaf_in_page || matchedCount >= Math.min(parts.length, 2))
  );
}

async function clickCategoryPart(page, needleSet, mode = "click") {
  const marker = `sop-cat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const result = await page.evaluate(({ needles, mode, marker }) => {
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
    const compactNeedles = needles.map(compact).filter(Boolean);
    const textOf = (node) => compact(
      node.innerText
      || node.textContent
      || node.getAttribute?.("title")
      || node.getAttribute?.("aria-label")
      || ""
    );
    const hrefOf = (node) => node.href || node.getAttribute?.("href") || "";
    const hoverTargetFor = (node) => node.closest?.("li, [class*='category'], [class*='cate'], [class*='menu'], [class*='industry'], [class*='cBox']") || node;
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
      if (!text) continue;
      for (const needle of compactNeedles) {
        if (!needle) continue;
        const exact = text === needle;
        const shortContains = needle.length >= 4 && text.includes(needle) && text.length <= Math.max(needle.length + 12, 28);
        const reverseContains = needle.includes(text) && text.length >= 4 && text.length <= Math.max(needle.length, 8);
        if (!exact && !shortContains && !reverseContains) continue;
        const clickable = clickableFor(node);
        if (!clickable || !visible(clickable) || disabled(clickable)) continue;
        const href = hrefOf(clickable) || hrefOf(node);
        if (mode === "click" && !/s\.1688\.com\/selloffer|offer_search|keywords=|categoryId=|catId=|offer/i.test(href)) {
          continue;
        }
        const score = (exact ? 100 : reverseContains ? 72 : 64)
          + (mode === "click" && /s\.1688\.com\/selloffer|offer_search/.test(href) ? 30 : 0)
          + (mode === "click" && (clickable.tagName === "A" || node.tagName === "A") ? 10 : 0)
          + (mode === "hover" && text.length > needle.length && text.includes(needle) ? 18 : 0)
          - Math.max(0, text.length - needle.length);
        if (!best || score > best.score) {
          best = { node, clickable, href, text, needle, score };
        }
      }
    }
    const diagnosticTexts = nodes
      .map((node) => textOf(node))
      .filter((text) => text && text.length <= 60)
      .filter((text, index, arr) => arr.indexOf(text) === index)
      .slice(0, 40);
    if (!best) {
      return {
        ok: false,
        matched_text: "",
        missing_text: needles[0] || "",
        href: "",
        diagnostics: {
          page_url: location.href,
          title: document.title || "",
          needles,
          visible_category_texts: diagnosticTexts,
        },
      };
    }
    document.querySelectorAll("[data-sop-category-candidate]").forEach((node) => {
      node.removeAttribute("data-sop-category-candidate");
    });
    best.clickable.setAttribute("data-sop-category-candidate", marker);
    best.clickable.scrollIntoView({ block: "center", inline: "center" });
    return {
      ok: true,
      matched_text: best.text.slice(0, 80),
      matched_needle: best.needle,
      href: best.href || "",
      score: best.score,
      mode,
      selector: `[data-sop-category-candidate="${marker}"]`,
      diagnostics: {
        page_url: location.href,
        title: document.title || "",
        needles,
        visible_category_texts: diagnosticTexts,
      },
    };
  }, { needles: needleSet, mode, marker }).catch((error) => ({
    ok: false,
    error: error.message || String(error),
    matched_text: "",
    missing_text: needleSet[0] || "",
    href: "",
  }));
  if (!result.ok || !result.selector) return result;
  const locator = page.locator(result.selector).first();
  try {
    await locator.hover({ timeout: Math.min(pageTimeoutMs, 10000) });
    await page.waitForTimeout(jitter([220, 650]));
    if (mode === "click") {
      await locator.click({ timeout: Math.min(pageTimeoutMs, 10000) });
    }
    return result;
  } catch (error) {
    const diagnostics = await collectCategoryDiagnostics(page, needleSet);
    return {
      ...result,
      ok: false,
      error: error.message || String(error),
      diagnostics,
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
    const base = {
      filter_key: String(filter.key || label),
      tag: String(filter.tag || label),
      label,
      source: "1688_search_filter",
      query,
      page_url: page.url(),
      matched_text: "",
    };
    if (isDetailPage) {
      results.push({
        ...base,
        status: "not_applicable",
        message: "当前为商品详情页URL，无法执行搜索页原生筛选",
      });
      continue;
    }
    await humanPause(page, pacing.beforeFilterClick);
    const applied = await page.evaluate(({ candidateTexts, mode, groupLabel, optionValue }) => {
      const compact = (value) => String(value || "").replace(/\s+/g, "").trim();
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
          .filter((node) => visible(node) && compact(node.innerText || node.textContent || "").includes(needle))
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
    }, { candidateTexts: texts, mode, groupLabel, optionValue }).catch((error) => ({ ok: false, error: error.message || String(error), matched_text: "" }));

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
      final_url: page.url(),
      category_steps: [],
      diagnostics: {},
    };
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
      await humanPause(page, pacing.beforeFilterClick);
      const shouldHoverOnly = index < needleSets.length - 1 && /^https?:\/\/(www\.)?1688\.com\/?/.test(page.url());
      if (shouldHoverOnly) {
        const beforeUrl = page.url();
        applied = await clickCategoryPart(page, needleSet, "hover");
        categorySteps.push({
          depth: index + 1,
          expected_text: parts[index] || "",
          needles: needleSet,
          mode: "hover",
          status: applied.ok ? "matched" : (applied.error ? "click_failed" : "not_found"),
          matched_text: normalizeText(applied.matched_text || ""),
          page_url: page.url(),
          page_url_before: beforeUrl,
          page_url_after: page.url(),
          diagnostics: applied.diagnostics || {},
          message: applied.error || "",
        });
        if (!applied.ok) break;
        clickedParts.push(parts[index] || needleSet[0] || "");
        matchedTexts.push(applied.matched_text || parts[index] || needleSet[0] || "");
        await humanPause(page, pacing.afterFilterClick);
        continue;
      }

      const beforeUrl = page.url();
      const followed = await followCategoryClick(page, () => clickCategoryPart(page, needleSet, "click"));
      applied = followed.action_result || {};
      categorySteps.push({
        depth: index + 1,
        expected_text: parts[index] || "",
        needles: needleSet,
        mode: "click",
        status: applied.ok ? "clicked" : (applied.error ? "click_failed" : "not_found"),
        matched_text: normalizeText(applied.matched_text || ""),
        page_url: page.url(),
        page_url_before: beforeUrl,
        page_url_after: followed.page?.url?.() || page.url(),
        href: applied.href || "",
        diagnostics: applied.diagnostics || {},
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
      if (navigatedToResults && categoryNavigationConfirmed(navigationState, parts) && clickedParts.length >= parts.length) {
        break;
      }
    }

    const matchedText = matchedTexts.join(">");
    const lastDiagnostics = applied.diagnostics
      || categorySteps.slice().reverse().find((step) => step.diagnostics && Object.keys(step.diagnostics).length)?.diagnostics
      || await collectCategoryDiagnostics(page, needleSets[clickedParts.length] || []);
    const confirmed = categoryNavigationConfirmed(navigationState, parts);
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
          categoryListName: "",
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
          categoryListName: "",
          pageIndex,
        },
      });
      if (rows.length >= maxItems) break;
    }
    return rows;
  }, { maxItems, selector: productCardSelector, pageIndex });
}

async function goToNextResultPage(page, query, sourceUrl, nextPageNumber) {
  if (!/s\.1688\.com\/selloffer|1688\.com/.test(page.url()) || /detail\.(m\.)?1688\.com/.test(page.url())) {
    return { ok: false, method: "not_search_page", message: "当前不是可翻页的搜索结果页" };
  }
  await humanPause(page, pacing.beforeFilterClick);
  const beforeUrl = page.url();
  const clicked = await page.evaluate(({ nextPageNumber }) => {
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
    const nodes = Array.from(document.querySelectorAll("a, button, li, span, div"))
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
      return /下一页|下页|next|pager-next|pagination-next|›|>/.test(text)
        || /下一页|下页|next|pager-next|pagination-next/.test(attrs);
    });
    const numberNode = nodes.find((node) => textOf(node) === String(nextPageNumber));
    const target = nextNode || numberNode;
    if (!target) return { ok: false, method: "dom_click", message: "页面未找到下一页按钮或页码" };
    const clickable = target.closest?.("a, button, [role='button'], li") || target;
    clickable.scrollIntoView({ block: "center", inline: "center" });
    clickable.click();
    return {
      ok: true,
      method: nextNode ? "next_button" : "page_number",
      matched_text: textOf(target).slice(0, 50),
      href: clickable.href || clickable.getAttribute?.("href") || "",
    };
  }, { nextPageNumber }).catch((error) => ({ ok: false, method: "dom_click", message: error.message || String(error) }));

  if (clicked.ok) {
    await humanPause(page, pacing.afterSearchSubmit);
    await page.waitForLoadState("domcontentloaded", { timeout: Math.min(pageTimeoutMs, 15000) }).catch(() => {});
    await failIfStillBlocked(page, query, sourceUrl, false);
    return { ...clicked, page_url: page.url(), url_changed: page.url() !== beforeUrl };
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

  for (let pageIndex = 1; pageIndex <= maxResultPages && products.length < maxItems; pageIndex += 1) {
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

    if (products.length >= maxItems || isDetailPage()) break;
    if (acceptedCount === 0 && pageIndex > 1) break;

    const nextResult = await goToNextResultPage(page, query, sourceUrl, pageIndex + 1);
    meta.next_page = nextResult;
    if (!nextResult.ok) break;
  }

  return { products, page_meta: pageMeta };
}

const runtime = await openRuntime();
let page = runtime.page;
page.setDefaultTimeout(Math.max(15000, Math.min(45000, pageTimeoutMs)));

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

  const latestText = await readBodyText(page, pageText);
  if (looksLikeBlockedPage(latestText, page.url())) {
    await runtime.close();
    console.log(JSON.stringify({
      success: false,
      code: looksLikeSecurityPage(latestText, page.url()) ? "security_verification_required" : "login_required",
      source: "1688_search_page",
      cdp: Boolean(cdpUrl),
      query,
      source_url: sourceUrl,
      page_url: page.url(),
      message: looksLikeSecurityPage(latestText, page.url())
        ? securityMessage(page.url(), needsManualGate && loginWaitMs > 0 && !headless)
        : blockedMessage(page.url(), needsManualGate && loginWaitMs > 0 && !headless),
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
    max_pages: maxResultPages,
    page_meta: collectResult.page_meta,
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
