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
if (!query && !sourceUrl) fail("缺少搜索词或 1688 页面 URL，无法打开真实页面");

const profileDir = process.env.TAG_COLLECT_RPA_PROFILE
  || path.join(os.homedir(), ".sop-1688-rpa-profile");
const headless = process.env.TAG_COLLECT_RPA_HEADLESS === "1";
const loginWaitMs = Number(process.env.TAG_COLLECT_RPA_LOGIN_WAIT_MS || 90000);
const cdpUrl = process.env.TAG_COLLECT_CDP_URL || "";
const pacingMode = String(process.env.TAG_COLLECT_RPA_PACING || "human").toLowerCase();
const pacingProfiles = {
  fast: {
    afterGoto: [1800, 3200],
    beforeFilterClick: [500, 1200],
    afterFilterClick: [1600, 2800],
    beforeScroll: [1400, 2600],
    scrollStep: [650, 1500],
    beforeExtract: [1800, 3200],
  },
  human: {
    afterGoto: [4500, 8500],
    beforeFilterClick: [1400, 3600],
    afterFilterClick: [3000, 6500],
    beforeScroll: [2500, 5200],
    scrollStep: [1200, 3200],
    beforeExtract: [3500, 7000],
  },
};
const pacing = pacingProfiles[pacingMode] || pacingProfiles.human;

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

function blockedMessage(url, waited) {
  const prefix = waited ? "已等待你处理登录/验证，但当前页面仍需要登录或安全校验。" : "当前页面需要登录或安全校验。";
  return `${prefix}请先在真实 Chrome/1688 页面完成登录和验证后重试；如果账号一直过不了校验，可以粘贴一个浏览器里能直接打开的 1688 搜索/商品链接做公开页面真实数据测试。当前页面：${url}`;
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

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, "").trim();
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

const runtime = await openRuntime();
const page = runtime.page;
page.setDefaultTimeout(45000);

try {
  const url = sourceUrl || `https://s.1688.com/selloffer/offer_search.htm?keywords=${encodeURIComponent(query)}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await humanPause(page, pacing.afterGoto);

  const pageText = await readBodyText(page);
  const needsManualGate = looksLikeSecurityPage(pageText, page.url()) || looksLikeLoginPage(pageText, page.url());
  if (needsManualGate && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
  }
  await failIfStillBlocked(page, query, sourceUrl, needsManualGate && loginWaitMs > 0 && !headless);

  await humanPause(page, pacing.beforeExtract);
  const filterResults = await applyNativeFilters(page, nativeFilters, query);
  await failIfStillBlocked(page, query, sourceUrl, false);
  await humanScroll(page);
  await humanPause(page, pacing.beforeExtract);
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
