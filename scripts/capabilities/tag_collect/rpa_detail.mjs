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

const url = String(input.url || "").trim();
const itemId = String(input.item_id || "").trim();
const testHtml = String(input.test_html || "").trim();
const testDomEvidence = input.test_dom_evidence && typeof input.test_dom_evidence === "object"
  ? input.test_dom_evidence
  : {};
const testDomEvidenceSequence = Array.isArray(input.test_dom_evidence_sequence)
  ? input.test_dom_evidence_sequence.filter((item) => item && typeof item === "object")
  : [];
if (!url && !testHtml) fail("缺少商品详情页 URL");

let chromium;
if (!testHtml) {
  try {
    ({ chromium } = await import("playwright"));
  } catch {
    fail("未安装 playwright，无法执行真实详情页核验。请在项目目录执行：npm install playwright");
  }
}

const profileDir = process.env.TAG_COLLECT_RPA_PROFILE
  || path.join(os.homedir(), ".sop-1688-rpa-profile");
const headless = process.env.TAG_COLLECT_RPA_HEADLESS === "1";
const cdpUrl = process.env.TAG_COLLECT_CDP_URL || "";
const loginWaitMs = Number(process.env.TAG_COLLECT_RPA_LOGIN_WAIT_MS || (cdpUrl ? 15000 : 30000));
const pageTimeoutMs = Number(process.env.TAG_COLLECT_RPA_PAGE_TIMEOUT_MS || 30000);
const manualVerificationWaitMs = Math.max(0, Number(process.env.TAG_COLLECT_RPA_MANUAL_VERIFY_WAIT_MS || 300000));
const detailEvidenceWaitMs = Math.max(
  0,
  Math.min(30000, Number(process.env.TAG_COLLECT_RPA_DETAIL_EVIDENCE_WAIT_MS || 12000))
);
const pacingMode = String(process.env.TAG_COLLECT_RPA_PACING || "human").toLowerCase();
const pacingProfiles = {
  fast: {
    afterGoto: [1800, 3200],
    beforeRead: [1800, 3200],
    scrollStep: [650, 1500],
  },
  human: {
    afterGoto: [4500, 8500],
    beforeRead: [3500, 7000],
    scrollStep: [1200, 3200],
  },
};
const pacing = pacingProfiles[pacingMode] || pacingProfiles.human;
const rpaProgressPrefix = "SOP_RPA_PROGRESS ";

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
  title: "1688 商品详情核验",
  detail: "准备打开商品详情页",
  meta: itemId ? `商品ID：${itemId}` : "",
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
          .head{display:flex;align-items:center;gap:9px;font-weight:700}.dot{width:9px;height:9px;border-radius:50%;background:#4ea1ff;box-shadow:0 0 0 4px rgba(78,161,255,.18)}.detail{margin-top:7px;color:#eef4ff}.meta{margin-top:5px;color:#aebbd0;font-size:12px;white-space:pre-wrap}.waiting .dot{background:#f5b942;box-shadow:0 0 0 4px rgba(245,185,66,.2)}.done .dot{background:#39c37d;box-shadow:0 0 0 4px rgba(57,195,125,.18)}
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
    if (title) title.textContent = state.title || "1688 商品详情核验";
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

async function humanScroll(page) {
  const steps = 1 + Math.floor(Math.random() * 3);
  for (let i = 0; i < steps; i += 1) {
    await page.evaluate(() => {
      const distance = Math.floor(window.innerHeight * (0.35 + Math.random() * 0.45));
      window.scrollBy({ top: distance, left: 0, behavior: "smooth" });
    }).catch(() => {});
    await humanPause(page, pacing.scrollStep);
  }
}

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

function blockedMessage(_currentUrl, waited) {
  const prefix = waited ? "已等待你处理登录/验证，但当前详情页仍需要登录或安全校验。" : "当前详情页需要登录或安全校验。";
  return `${prefix}不会写入样例字段。验证页面已保留在采集 Chrome 中，请人工完成登录或验证；确认详情页恢复正常后，再回到工作台继续核验。`;
}

function securityMessage(_currentUrl, waited) {
  const prefix = waited ? "已等待你手动处理 1688 安全验证，但当前详情页仍停留在滑块/验证码校验。" : "1688 详情页触发了安全滑块/验证码校验。";
  return `${prefix}系统不会绕过或自动破解验证，也不会写入未经核验的详情字段。已达到本次人工等待时限，页面会保留在采集 Chrome 中。`;
}

async function readPageText(page, fallback = "") {
  return page.locator("body").innerText({ timeout: 10000 }).catch(() => fallback);
}

async function waitForManualSecurityResolution(page) {
  if (headless || manualVerificationWaitMs <= 0) return { resumed: false, waited_ms: 0 };
  const startedAt = Date.now();
  const deadline = startedAt + manualVerificationWaitMs;
  const alertKey = `detail-security-${itemId || "unknown"}-${startedAt}`;
  emitRpaProgress(
    "manual_intervention",
    "商品详情页出现滑块或验证码，请在采集 Chrome 中人工完成验证；完成后会继续核验当前商品。",
    { alert_key: alertKey, percent: 86, intervention_type: "security_verification", item_id: itemId }
  );
  await page.bringToFront().catch(() => {});
  while (Date.now() < deadline) {
    const remainingSeconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    await updateWorkbenchOverlay(page, {
      tone: "waiting",
      detail: "请在当前详情页完成人工校验，完成后会继续核验同一商品。",
      meta: `已等待 ${Math.floor((Date.now() - startedAt) / 1000)} 秒 · 剩余约 ${remainingSeconds} 秒\n商品ID：${itemId || "-"}`,
    });
    await page.waitForTimeout(1200);
    const text = await readPageText(page);
    if (!looksLikeSecurityPage(text, page.url())) {
      emitRpaProgress(
        "manual_intervention_resolved",
        "商品详情页人工验证已完成，正在继续核验当前商品。",
        { alert_key: alertKey, percent: 87, intervention_type: "security_verification", item_id: itemId }
      );
      await updateWorkbenchOverlay(page, {
        tone: "working",
        detail: "人工处理已完成，继续读取当前商品详情。",
        meta: `本次等待 ${Math.round((Date.now() - startedAt) / 1000)} 秒`,
      });
      await page.waitForTimeout(700);
      return { resumed: true, waited_ms: Date.now() - startedAt };
    }
  }
  return { resumed: false, waited_ms: Date.now() - startedAt };
}

async function openRuntime() {
  if (cdpUrl) {
    let browser;
    try {
      browser = await chromium.connectOverCDP(cdpUrl, { noDefaults: true });
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      failWithCode(
        "cdp_context_unsupported",
        "真实 Chrome CDP 端口能连接，但当前调试端点不支持 Playwright 初始化上下文。请关闭该调试 Chrome 后重新运行 scripts/capabilities/tag_collect/start_chrome_debug.sh；如果仍失败，先确认 9222 没有被其它浏览器/工具占用。",
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
    const page = await context.newPage();
    return {
      page,
      close: async () => {
        await page.close().catch(() => {});
        await browser.close().catch(() => {});
      },
      releaseForManualHandoff: async () => {
        await page.bringToFront().catch(() => {});
        // Keep the external Chrome tab open for the user. Exiting this Node
        // process releases the CDP connection without removing the page.
      },
    };
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

function firstMatch(text, patterns) {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return (match[1] || match[0] || "").trim();
  }
  return "";
}

function cleanExtractedValue(value, maxLength = 80) {
  return String(value || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^[：:，,；;\-—\s]+/, "")
    .trim()
    .slice(0, maxLength);
}

function stopAtNextLabel(value, maxLength = 80) {
  return cleanExtractedValue(value, maxLength)
    .split(/(?=\s*(?:起批|起订|品退率|品质退款率|退款率|发货率|准时发货率|24\s*小时|24h|24H|SKU|规格|收藏|诚信通|生产厂家|经销批发|招商代理|商业服务|个体经营|密文面单|电子面单|面单|\{|"shopName"|'shopName'))/)[0]
    .trim();
}

function cleanShopName(value) {
  const text = cleanExtractedValue(value, 60)
    .replace(/^(店铺|店铺名称|供应商|公司|公司名称|进入店铺|进店)[:：]?\s*/, "")
    .replace(/(进入店铺|进店|联系供应商|收藏店铺).*$/, "")
    .trim();
  if (!text || /^(回头率|复购率|发货率|好评率|店铺|公司|供应商|联系|全部商品)$/.test(text)) return "";
  if (!/[一-龥A-Za-z]/.test(text)) return "";
  return text;
}

function firstJsonValue(rawText, keys) {
  const text = String(rawText || "");
  for (const key of keys) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const patterns = [
      new RegExp(`"${escaped}"\\s*:\\s*"([^"]{1,120})"`, "i"),
      new RegExp(`'${escaped}'\\s*:\\s*'([^']{1,120})'`, "i"),
      new RegExp(`"${escaped}"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?%?)`, "i"),
    ];
    const value = firstMatch(text, patterns);
    if (value) return cleanExtractedValue(value);
  }
  return "";
}

function normalizeEvidenceText(value, maxLength = 180) {
  return String(value || "")
    .replace(/[\uE000-\uF8FF]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

function normalizeCountMetric(value) {
  return cleanExtractedValue(value, 40)
    .replace(/[＜﹤]/g, "<")
    .replace(/[＞﹥]/g, ">")
    .replace(/\s+/g, "")
    .replace(/[件单笔]$/, "")
    .trim();
}

function normalizeShippingFee(value) {
  const compact = cleanExtractedValue(value, 80)
    .replace(/[＜﹤]/g, "<")
    .replace(/[＞﹥]/g, ">")
    .replace(/\s+/g, "")
    .replace(/[¥￥]/g, "")
    .trim();
  if (!compact) return "";
  if (/^(包邮|免运费)$/.test(compact)) return "包邮";
  if (/^(另计|到付)$/.test(compact)) return compact;
  if (/^首重/.test(compact)) return compact;
  const numeric = compact.match(/^([0-9]+(?:\.[0-9]+)?)(元)?(起|以上|以内)?$/);
  if (numeric) return `${numeric[1]}元${numeric[3] || ""}`;
  return compact;
}

function domEvidenceCandidates(domEvidence, key) {
  const values = Array.isArray(domEvidence?.[key]) ? domEvidence[key] : [];
  return values.map((item) => {
    if (item && typeof item === "object") {
      return {
        text: normalizeEvidenceText(item.text),
        source: cleanExtractedValue(item.source || "short_dom_label", 80),
        selector_hint: cleanExtractedValue(item.selector_hint || "", 160),
      };
    }
    return { text: normalizeEvidenceText(item), source: "short_dom_label", selector_hint: "" };
  }).filter((item) => item.text);
}

function firstCandidateMatch(candidates, patterns, normalizer, fieldKey) {
  for (const candidate of candidates) {
    const raw = firstMatch(candidate.text, patterns);
    const value = normalizer(raw);
    if (!value) continue;
    return {
      value,
      evidence: {
        field_key: fieldKey,
        kind: "short_dom_label",
        text: candidate.text,
        source: candidate.source || "detail_dom",
        selector_hint: candidate.selector_hint || "",
      },
    };
  }
  return { value: "", evidence: null };
}

function fallbackMatch(text, patterns, normalizer, fieldKey, kind = "page_text_fallback") {
  const raw = firstMatch(text, patterns);
  const value = normalizer(raw);
  return {
    value,
    evidence: value ? {
      field_key: fieldKey,
      kind,
      text: normalizeEvidenceText(raw),
      source: kind,
      selector_hint: "",
    } : null,
  };
}

const explicitDropshipShippingPatterns = [
  /(?:^|[\s，,；;|])(?:一件)?代发运费[:：]?\s*([¥￥]?\s*[0-9]+(?:\.[0-9]+)?\s*(?:元)?\s*(?:起|以上|以内)?)/,
  /(?:^|[\s，,；;|])(?:一件)?代发运费[:：]?\s*(首重\s*[0-9]+(?:\.[0-9]+)?元(?:\s*(?:续重|续件)\s*[0-9]+(?:\.[0-9]+)?元)?)/,
];
const generalShippingPatterns = [
  /(?:^|[\s，,；;|])(?:运费|快递费)[:：]?\s*([¥￥]?\s*[0-9]+(?:\.[0-9]+)?\s*(?:元)?\s*(?:起|以上|以内)?)/,
  /(?:^|[\s，,；;|])(?:运费|快递费)[:：]?\s*(首重\s*[0-9]+(?:\.[0-9]+)?元(?:\s*(?:续重|续件)\s*[0-9]+(?:\.[0-9]+)?元)?)/,
  /(?:^|[\s，,；;|])(?:运费|快递费)[:：]?\s*(包邮|免运费|另计|到付)/,
  /(首重\s*[0-9]+(?:\.[0-9]+)?元(?:\s*(?:续重|续件)\s*[0-9]+(?:\.[0-9]+)?元)?)/,
];
const generalShippingDomPatterns = [
  ...generalShippingPatterns,
  /(包邮|免运费)/,
];
const monthlyDropshipOrderPatterns = [
  /月代发订单[:：]?\s*([<＜≤]?\s*[0-9]+(?:\.[0-9]+)?\s*万?\s*(?:\+|以上|以内)?)/,
  /代发订单[:：]?\s*([<＜≤]?\s*[0-9]+(?:\.[0-9]+)?\s*万?\s*(?:\+|以上|以内)?)/,
];
const monthlyDistributionSalesPatterns = [
  /月代销[:：]?\s*([<＜≤]?\s*[0-9]+(?:\.[0-9]+)?\s*万?\s*(?:\+|以上|以内)?)/,
  /([<＜≤]?\s*[0-9]+(?:\.[0-9]+)?\s*万?\s*(?:\+|以上|以内)?)\s*(?:件)?\s*月代销/,
  /近\s*30\s*天(?:成交)?件数[:：]?\s*([<＜≤]?\s*[0-9]+(?:\.[0-9]+)?\s*万?\s*(?:\+|以上|以内)?)/,
  /近\s*30\s*天销量[:：]?\s*([<＜≤]?\s*[0-9]+(?:\.[0-9]+)?\s*万?\s*(?:\+|以上|以内)?)/,
];

function normalizeOfficialCategoryId(value) {
  const text = String(value || "").trim();
  return /^\d{1,32}$/.test(text) ? text : "";
}

function extractCategoryId(...values) {
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
}

function extractStructuredCategoryId(value) {
  const text = String(value || "");
  const dataAttribute = text.match(/\b(?:data-product-category-id|data-productcategoryid|data-category-id|data-categoryid|data-cat-id|data-catid|data-cate-id|data-cateid)\s*=\s*["']?(\d{1,32})["']?/i);
  if (dataAttribute) return normalizeOfficialCategoryId(dataAttribute[1]);
  const property = text.match(/(?:^|[\[{,;\s])(?:["']?(?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)["']?)\s*[:=]\s*["']?(\d{1,32})["']?/i);
  return property ? normalizeOfficialCategoryId(property[1]) : "";
}

function decodeStructuredString(value) {
  return String(value || "")
    .replace(/\\u([0-9a-fA-F]{4})/g, (_, code) => String.fromCharCode(Number.parseInt(code, 16)))
    .replace(/\\\//g, "/")
    .replace(/\\n|\\r|\\t/g, " ")
    .replace(/\\(["'])/g, "$1");
}

function cleanCategoryName(value) {
  return decodeStructuredString(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&gt;|&rsaquo;|&raquo;/gi, ">")
    .replace(/[\uE000-\uF8FF]/g, "")
    .replace(/\s+/g, " ")
    .replace(/^[>›»→|\-\s]+|[>›»→|\-\s]+$/g, "")
    .trim()
    .slice(0, 160);
}

function normalizeCategoryBreadcrumb(value) {
  const items = Array.isArray(value) ? value : [];
  const names = [];
  for (const item of items) {
    const rawName = typeof item === "string"
      ? item
      : (item?.name || item?.title || item?.text || item?.label || item?.item?.name || "");
    const name = cleanCategoryName(rawName);
    if (!name || /^(首页|1688|阿里巴巴|全部分类|商品详情)$/.test(name)) continue;
    if (names[names.length - 1] !== name) names.push(name);
  }
  return names.slice(0, 10);
}

function categoryBreadcrumbFromPath(value) {
  return normalizeCategoryBreadcrumb(
    cleanCategoryName(value).split(/\s*(?:>|›|»|→)\s*/).filter(Boolean)
  );
}

function normalizeCategoryPath(value) {
  return categoryBreadcrumbFromPath(value).join(">");
}

function firstStructuredString(text, keys, maxLength = 480) {
  const source = String(text || "");
  for (const key of keys) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const doubleQuoted = source.match(new RegExp(`"${escaped}"\\s*:\\s*"((?:\\\\.|[^"\\\\]){1,${maxLength}})"`, "i"));
    if (doubleQuoted) return decodeStructuredString(doubleQuoted[1]);
    const singleQuoted = source.match(new RegExp(`'${escaped}'\\s*:\\s*'((?:\\\\.|[^'\\\\]){1,${maxLength}})'`, "i"));
    if (singleQuoted) return decodeStructuredString(singleQuoted[1]);
  }
  return "";
}

function structuredCategoryBreadcrumb(text) {
  const source = String(text || "");
  const match = source.match(/["'](?:productCategoryBreadcrumb|categoryBreadcrumb|breadcrumbs?)["']\s*:\s*(\[[\s\S]{1,2400}?\])/i);
  if (!match) return [];
  try {
    return normalizeCategoryBreadcrumb(JSON.parse(match[1]));
  } catch {
    const quoted = Array.from(match[1].matchAll(/["']((?:\\.|[^"']){1,160})["']/g)).map((item) => item[1]);
    return normalizeCategoryBreadcrumb(quoted);
  }
}

function emptyCategoryEvidence() {
  return {
    category_id: "",
    category_path: "",
    category_breadcrumb: [],
    source: "",
  };
}

function categoryEvidenceFromStructuredText(text, source = "structured_data", offerId = "") {
  const sourceText = String(text || "");
  const offerIndex = offerId ? sourceText.indexOf(String(offerId)) : -1;
  const anchorMatch = /productCategoryPath|categoryPath|categoryListName|categoryBreadcrumb|productCategoryBreadcrumb|productCategoryName|leafCategoryName/i.exec(sourceText);
  const anchorIndex = offerIndex >= 0 ? offerIndex : (anchorMatch?.index ?? -1);
  const scopedText = anchorIndex >= 0
    ? sourceText.slice(Math.max(0, anchorIndex - 1400), anchorIndex + 2400)
    : sourceText;
  let categoryId = extractStructuredCategoryId(scopedText);
  const hasExplicitProductCategoryKey = /productCategoryId|productCategoryPath|productCategoryName|productCategoryBreadcrumb|leafCategoryId|leafCategoryName/i.test(scopedText);
  const structuredPath = firstStructuredString(scopedText, [
    "productCategoryPath",
    "categoryPath",
    "categoryListName",
    "catPath",
  ]);
  const explicitProductName = firstStructuredString(scopedText, [
    "productCategoryName",
    "leafCategoryName",
  ]);
  const genericCategoryName = firstStructuredString(scopedText, [
    "categoryName",
    "catName",
  ]);
  let breadcrumb = structuredCategoryBreadcrumb(scopedText);
  if (offerId && offerIndex < 0 && !hasExplicitProductCategoryKey && !structuredPath && !breadcrumb.length) {
    categoryId = "";
  }
  const structuredName = explicitProductName || (categoryId ? genericCategoryName : "");
  let categoryPath = normalizeCategoryPath(structuredPath);
  if (!breadcrumb.length && categoryPath) breadcrumb = categoryBreadcrumbFromPath(categoryPath);
  if (!categoryPath && breadcrumb.length) categoryPath = breadcrumb.join(">");
  if (!categoryPath && structuredName) {
    breadcrumb = normalizeCategoryBreadcrumb([structuredName]);
    categoryPath = breadcrumb.join(">");
  }
  if (!categoryId && !categoryPath && !breadcrumb.length) return emptyCategoryEvidence();
  return {
    category_id: categoryId,
    category_path: categoryPath,
    category_breadcrumb: breadcrumb,
    source,
  };
}

function categoryEvidenceFromJsonLdHtml(html) {
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
  const scripts = String(html || "").matchAll(/<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi);
  for (const match of scripts) {
    try {
      const parsed = JSON.parse(match[1]);
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
        const breadcrumb = normalizeCategoryBreadcrumb(categoryElements.map((entry) => (
          entry?.name || entry?.item?.name || entry?.item?.title || ""
        )));
        if (!breadcrumb.length) continue;
        const last = categoryElements[categoryElements.length - 1] || {};
        const itemUrl = typeof last.item === "string" ? last.item : (last.item?.["@id"] || last.item?.url || last.url || "");
        return {
          category_id: extractCategoryId(itemUrl),
          category_path: breadcrumb.join(">"),
          category_breadcrumb: breadcrumb,
          source: "json_ld_breadcrumb",
        };
      }
    } catch {
      // Ignore malformed JSON-LD and continue with structured page data.
    }
  }
  return emptyCategoryEvidence();
}

function mergeCategoryEvidence(...values) {
  const candidates = values.filter((item) => item && (
    item.category_id || item.category_path || (Array.isArray(item.category_breadcrumb) && item.category_breadcrumb.length)
  ));
  if (!candidates.length) return emptyCategoryEvidence();
  const pathCandidate = candidates.find((item) => item.category_path || item.category_breadcrumb?.length) || candidates[0];
  const idCandidate = candidates.find((item) => item.category_id);
  const breadcrumb = normalizeCategoryBreadcrumb(
    pathCandidate.category_breadcrumb?.length
      ? pathCandidate.category_breadcrumb
      : categoryBreadcrumbFromPath(pathCandidate.category_path)
  );
  return {
    category_id: idCandidate?.category_id || "",
    category_path: normalizeCategoryPath(pathCandidate.category_path) || breadcrumb.join(">"),
    category_breadcrumb: breadcrumb,
    source: pathCandidate.source || idCandidate?.source || "",
  };
}

function categoryFieldsFromEvidence(evidence) {
  if (!evidence || (!evidence.category_id && !evidence.category_path && !evidence.category_breadcrumb?.length)) return {};
  return {
    product_category_id: evidence.category_id || "",
    product_category_path: evidence.category_path || "",
    product_category_breadcrumb: evidence.category_breadcrumb || [],
    category_evidence_source: evidence.source || "",
  };
}

function extractDetailFieldsFromText(text, structuredText = "", domEvidence = {}) {
  const combined = `${structuredText || ""} ${text || ""}`.replace(/\s+/g, " ").trim();
  const fields = {};
  const fieldEvidence = {};
  fields.product_rating = firstMatch(combined, [
    /商品评价[^0-9]{0,20}([0-5](?:\.[0-9])?)(?:\s*\(|\s*好评率|\s*$)/,
    /([0-5](?:\.[0-9])?)\s*(?:分)?\s*\(?\s*[0-9万+]*条?评价/,
    /([0-5](?:\.[0-9])?)\s*好评率/,
  ]);
  fields.good_rate = firstMatch(combined, [
    /好评率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /([0-9]+(?:\.[0-9]+)?%)\s*好评/,
  ]) || firstJsonValue(structuredText, ["goodRate", "goodRates", "positiveRate"]);
  fields.comment_count = firstMatch(combined, [
    /[\(（]\s*([0-9]+(?:\.[0-9]+)?万?\+?)\s*条?评价\s*[\)）]/,
    /([0-9]+(?:\.[0-9]+)?万?\+?)\s*条?评价/,
    /([0-9]+(?:\.[0-9]+)?万?\+?)\s*人?好评/,
  ]) || firstJsonValue(structuredText, ["remarkCount", "remarkCnt", "commentCount"]);
  const knownReviewTags = [
    "客服态度超好",
    "质感不错",
    "购买推荐",
    "质量很好",
    "发货很快",
    "包装完好",
    "性价比高",
  ];
  fields.review_tags = knownReviewTags.filter((tag) => combined.includes(tag)).join(",");
  fields.product_refund_rate = firstMatch(combined, [
    /品退率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /品质退款率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /退款率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
  ]) || firstJsonValue(structuredText, ["refundRate", "qualityRefundRate"]);
  fields.shipment_rate = firstMatch(combined, [
    /发货率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /准时发货率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
  ]) || firstJsonValue(structuredText, ["shipmentRate", "deliveryRate", "onTimeDeliveryRate"]);
  fields.collection_rate_24h = firstMatch(combined, [
    /24\s*小时揽收率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /24h揽收率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/i,
    /24H支揽率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/i,
  ]) || firstJsonValue(structuredText, ["collectionRate24h", "collectRate24h"]);
  fields.collection_rate_48h = firstMatch(combined, [
    /48\s*小时揽收率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/,
    /48h揽收率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/i,
    /48H支揽率[:：]?\s*([0-9]+(?:\.[0-9]+)?%)/i,
  ]) || firstJsonValue(structuredText, ["collectionRate48h", "collectRate48h"]);
  fields.shipment_speed = firstMatch(combined, [
    /(24小时内发货|48小时内发货|72小时内发货|[0-9]+小时发货|[0-9]+天内发货)/,
  ]);
  const shippingCandidates = domEvidenceCandidates(domEvidence, "shipping");
  const explicitDropshipShipping = firstCandidateMatch(
    shippingCandidates,
    explicitDropshipShippingPatterns,
    normalizeShippingFee,
    "dropship_shipping_fee"
  );
  const generalShipping = firstCandidateMatch(
    shippingCandidates,
    generalShippingDomPatterns,
    normalizeShippingFee,
    "wholesale_shipping_fee"
  );
  const fallbackExplicitDropshipShipping = explicitDropshipShipping.value
    ? explicitDropshipShipping
    : fallbackMatch(combined, explicitDropshipShippingPatterns, normalizeShippingFee, "dropship_shipping_fee");
  const fallbackGeneralShipping = generalShipping.value
    ? generalShipping
    : fallbackMatch(combined, generalShippingPatterns, normalizeShippingFee, "wholesale_shipping_fee");
  fields.wholesale_shipping_fee = fallbackGeneralShipping.value || fallbackExplicitDropshipShipping.value;
  fields.dropship_shipping_fee = fallbackExplicitDropshipShipping.value || fallbackGeneralShipping.value;
  if (fields.wholesale_shipping_fee) {
    fieldEvidence.wholesale_shipping_fee = fallbackGeneralShipping.evidence
      || { ...fallbackExplicitDropshipShipping.evidence, field_key: "wholesale_shipping_fee", kind: "dropship_label_fallback" };
  }
  if (fields.dropship_shipping_fee) {
    fieldEvidence.dropship_shipping_fee = fallbackExplicitDropshipShipping.evidence
      || { ...fallbackGeneralShipping.evidence, field_key: "dropship_shipping_fee", kind: "product_shipping_label_fallback" };
  }
  fields.free_shipping = /^(包邮|免运费)$/.test(fields.dropship_shipping_fee)
    ? "是"
    : (fields.dropship_shipping_fee
      ? "否"
      : (/不包邮|运费另计|运费到付/.test(combined)
        ? "否"
        : (/包邮|免运费|卖家承担运费/.test(combined) ? "是" : "")));
  const dropshipDenied = /不支持一件代发|不提供一件代发|暂不支持(?:一件)?代发|不可一件代发/.test(combined);
  const dropshipConfirmed = /(?:支持|可|提供|享受)?\s*一件代发|一件起批[^。；，,]{0,20}(?:支持|可)?代发|支持代发(?!价|订单|销量)/.test(combined);
  fields.supports_dropship = dropshipDenied ? "否" : (dropshipConfirmed ? "是" : "");
  fields.dropship_rights = fields.supports_dropship === "是" ? "页面明确显示支持一件代发" : "";
  fields.dropship_price = firstMatch(combined, [
    /(?:一件代发价|代发价|分销价)[:：]?\s*[¥￥]?\s*([0-9]+(?:\.[0-9]+)?(?:\s*[-~至]\s*[0-9]+(?:\.[0-9]+)?)?\s*元?)/,
    /[¥￥]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:一件代发|代发)/,
  ]) || firstJsonValue(structuredText, ["dropshipPrice", "onePiecePrice", "distributionPrice", "retailPrice"]);
  fields.return_exchange_support = firstMatch(combined, [
    /(7天无理由(?:退货|退换货)?|七天无理由(?:退货|退换货)?)/,
    /(支持退换(?:货)?)/,
  ]);
  fields.rights_protection = firstMatch(combined, [
    /(买家保障[^。；，,]{0,40})/,
    /(保障服务[^。；，,]{0,40})/,
    /(7天无理由(?:退货|退换货)?|七天无理由(?:退货|退换货)?)/,
  ]);
  fields.min_order_range = firstMatch(combined, [
    /([0-9]+件起批)/,
    /起批(?:量)?[:：]?\s*([0-9]+[^ ]{0,8})/,
    /起订(?:量)?[:：]?\s*([0-9]+[^ ]{0,8})/,
  ]);
  fields.sku_count = firstMatch(combined, [
    /SKU(?:数量|数)?[:：]?\s*([0-9]+(?:\.[0-9]+)?万?\+?)/i,
    /([0-9]+(?:\.[0-9]+)?万?\+?)\s*(?:个|款)?SKU/i,
    /规格(?:数量|数)?[:：]?\s*([0-9]+(?:\.[0-9]+)?万?\+?)/,
  ]) || firstJsonValue(structuredText, ["skuCount", "skuNum", "skuQuantity"]);
  fields.shop_name = cleanShopName(firstJsonValue(structuredText, ["shopName", "sellerName", "companyName", "loginId"])) || cleanShopName(firstMatch(combined, [
    /(?:店铺名称|店铺|供应商|公司名称|公司)[:：]\s*([^。；，,\n\r]{2,60})/,
    /进入店铺\s*([^。；，,\n\r]{2,60})/,
  ]));
  fields.location = firstMatch(combined, [
    /所在地[:：]?\s*([^ ]{2,20})/,
    /货源地[:：]?\s*([^ ]{2,20})/,
  ]);
  fields.company_type = firstMatch(combined, [
    /(生产厂家|经销批发|招商代理|商业服务|个体经营)/,
  ]);
  fields.seller_member_type = firstMatch(combined, [
    /(实力商家|超级工厂|诚信通)/,
  ]);
  fields.source_factory = /源头工厂|生产厂家|超级工厂/.test(combined) ? "是" : "";
  fields.trustpass_years = firstMatch(combined, [
    /诚信通\s*([0-9]+年)/,
    /([0-9]+年)\s*诚信通/,
  ]);
  fields.certificates = firstMatch(combined, [
    /(营业执照|生产许可证|质检报告|认证证书|资质证书)/,
  ]);
  const monthlyDropshipCandidates = domEvidenceCandidates(domEvidence, "monthly_dropship_orders");
  const monthlyDropshipFromDom = firstCandidateMatch(
    monthlyDropshipCandidates,
    monthlyDropshipOrderPatterns,
    normalizeCountMetric,
    "monthly_dropship_orders"
  );
  const monthlyDropshipFromText = monthlyDropshipFromDom.value
    ? monthlyDropshipFromDom
    : fallbackMatch(combined, monthlyDropshipOrderPatterns, normalizeCountMetric, "monthly_dropship_orders");
  const monthlyDropshipFromStructured = normalizeCountMetric(firstJsonValue(
    structuredText,
    ["monthlyDropshipOrders", "monthDropshipOrders", "monthDistributionOrders"]
  ));
  fields.monthly_dropship_orders = monthlyDropshipFromText.value || monthlyDropshipFromStructured;
  if (fields.monthly_dropship_orders) {
    fieldEvidence.monthly_dropship_orders = monthlyDropshipFromText.evidence || {
      field_key: "monthly_dropship_orders",
      kind: "structured_data",
      text: fields.monthly_dropship_orders,
      source: "structured_data",
      selector_hint: "",
    };
  }
  const monthlySalesCandidates = domEvidenceCandidates(domEvidence, "monthly_sales");
  const monthlySalesFromDom = firstCandidateMatch(
    monthlySalesCandidates,
    monthlyDistributionSalesPatterns,
    normalizeCountMetric,
    "units_30d"
  );
  const monthlySalesFromText = monthlySalesFromDom.value
    ? monthlySalesFromDom
    : fallbackMatch(combined, monthlyDistributionSalesPatterns, normalizeCountMetric, "units_30d");
  const monthlySalesFromStructured = normalizeCountMetric(firstJsonValue(
    structuredText,
    ["last30DaysSales", "sales30Days", "monthDistributionSales"]
  ));
  fields.units_30d = monthlySalesFromText.value || monthlySalesFromStructured;
  if (fields.units_30d) {
    fieldEvidence.units_30d = monthlySalesFromText.evidence || {
      field_key: "units_30d",
      kind: "structured_data",
      text: fields.units_30d,
      source: "structured_data",
      selector_hint: "",
    };
  }
  fields.orders_30d = firstMatch(combined, [
    /(?:近\s*30\s*天订单数|近\s*30\s*天成交订单)[:：]?\s*([0-9]+(?:\.[0-9]+)?万?\+?)/,
  ]) || firstJsonValue(structuredText, ["orders30Days", "last30DaysOrders"]);
  fields.favorite_customers = firstMatch(combined, [
    /收藏(?:客户|人数)?[:：]?\s*([0-9]+(?:\.[0-9]+)?万?\+?)/,
  ]);
  fields.stock = stopAtNextLabel(firstMatch(combined, [
    /库存[:：]?\s*([^。；，,\s<]{1,30})/,
    /现货[:：]?\s*([^。；，,\s<]{1,30})/,
  ]), 40);
  fields.waybill_support = firstMatch(combined, [
    /(微信小店面单|抖音面单|拼多多面单|电子面单|密文面单)/,
    /(面单[^。；，,\s<]{0,12})/,
  ]);
  fields.video_query = /视频|主图视频/.test(combined) ? "页面出现视频信息，待人工确认素材可用性" : "";
  return { fields, fieldEvidence };
}

async function collectDetailFieldDomEvidence(page) {
  const merged = { shipping: [], monthly_sales: [], monthly_dropship_orders: [] };
  const seen = new Set();
  const frames = page.frames().slice(0, 12);
  for (let frameIndex = 0; frameIndex < frames.length; frameIndex += 1) {
    const frame = frames[frameIndex];
    const frameSource = frame === page.mainFrame() ? "document" : `frame_${frameIndex}`;
    const partial = await frame.evaluate((sourceLabel) => {
      const result = { shipping: [], monthly_sales: [], monthly_dropship_orders: [] };
      const seenLocal = new Set();
      const roots = [{ root: document, source: sourceLabel }];
      const rootSeen = new Set([document]);
      const normalize = (value) => String(value || "")
        .replace(/[\uE000-\uF8FF]/g, "")
        .replace(/\s+/g, " ")
        .trim();
      const visible = (node) => {
        if (!node || typeof node.getBoundingClientRect !== "function") return false;
        const rect = node.getBoundingClientRect();
        const style = node.ownerDocument?.defaultView?.getComputedStyle(node);
        if (!style) return false;
        return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
      };
      const selectorHint = (node) => {
        if (!node || node.nodeType !== 1) return "";
        const id = node.id ? `#${String(node.id).slice(0, 80)}` : "";
        const classes = Array.from(node.classList || []).slice(0, 3).join(".");
        return `${String(node.tagName || "").toLowerCase()}${id}${classes ? `.${classes}` : ""}`.slice(0, 160);
      };
      const add = (bucket, text, node, source) => {
        const normalized = normalize(text);
        if (!normalized || normalized.length > 180) return;
        const key = `${bucket}\u0000${normalized}`;
        if (seenLocal.has(key) || result[bucket].length >= 50) return;
        seenLocal.add(key);
        result[bucket].push({ text: normalized, source, selector_hint: selectorHint(node) });
      };
      let scanned = 0;
      while (roots.length && scanned < 12000) {
        const current = roots.shift();
        let nodes = [];
        try {
          nodes = Array.from(current.root.querySelectorAll("*"));
        } catch {
          continue;
        }
        for (const node of nodes) {
          scanned += 1;
          if (scanned > 12000) break;
          if (node.id === "sop-1688-workbench-overlay" || node.closest?.("[data-sop-workbench-overlay='true']")) continue;
          if (node.shadowRoot && !rootSeen.has(node.shadowRoot)) {
            rootSeen.add(node.shadowRoot);
            roots.push({ root: node.shadowRoot, source: `${sourceLabel}_open_shadow_dom` });
          }
          const rawText = normalize(node.textContent || node.getAttribute?.("aria-label") || node.getAttribute?.("title") || "");
          if (!rawText || rawText.length > 180 || !visible(node)) continue;
          if (/月代销|近\s*30\s*天(?:成交)?件数|近\s*30\s*天销量/.test(rawText)) {
            add("monthly_sales", rawText, node, current.source);
          }
          if (/月代发订单|(?:^|\s)代发订单/.test(rawText)) {
            add("monthly_dropship_orders", rawText, node, current.source);
          }
          const shippingModule = node.closest?.("#shippingServices, [data-module='od_shipping_services']");
          if (/(?:^|[\s，,；;|])(?:一件)?代发运费|(?:^|[\s，,；;|])(?:运费|快递费)[:：]?\s*[¥￥]?[0-9]|首重\s*[0-9]/.test(rawText)
            && !/退货包运费|运费险|运费查询/.test(rawText)) {
            add("shipping", rawText, node, current.source);
          }
          if (shippingModule && /包邮|免运费/.test(rawText) && !/退货包运费|运费险|运费查询/.test(rawText)) {
            add("shipping", rawText, node, current.source);
          }
        }
      }
      return result;
    }, frameSource).catch(() => ({ shipping: [], monthly_sales: [], monthly_dropship_orders: [] }));
    for (const bucket of Object.keys(merged)) {
      for (const candidate of partial[bucket] || []) {
        const key = `${bucket}\u0000${candidate.text}`;
        if (seen.has(key) || merged[bucket].length >= 50) continue;
        seen.add(key);
        merged[bucket].push(candidate);
      }
    }
  }
  for (const key of Object.keys(merged)) {
    merged[key].sort((a, b) => a.text.length - b.text.length);
  }
  return merged;
}

function mergeDetailDomEvidence(...values) {
  const merged = { shipping: [], monthly_sales: [], monthly_dropship_orders: [] };
  for (const value of values) {
    for (const bucket of Object.keys(merged)) {
      for (const candidate of value?.[bucket] || []) {
        const normalized = candidate && typeof candidate === "object"
          ? {
            text: normalizeEvidenceText(candidate.text),
            source: cleanExtractedValue(candidate.source || "detail_dom", 80),
            selector_hint: cleanExtractedValue(candidate.selector_hint || "", 160),
          }
          : { text: normalizeEvidenceText(candidate), source: "detail_dom", selector_hint: "" };
        if (!normalized.text) continue;
        if (merged[bucket].some((item) => item.text === normalized.text)) continue;
        merged[bucket].push(normalized);
      }
      merged[bucket].sort((a, b) => a.text.length - b.text.length);
    }
  }
  return merged;
}

async function waitForDetailFieldDomEvidence(page, initialEvidence = {}, testSequence = []) {
  let merged = mergeDetailDomEvidence(initialEvidence);
  const sequence = [...testSequence];
  const deadline = Date.now() + (sequence.length ? 1000 : detailEvidenceWaitMs);
  do {
    const current = sequence.length
      ? sequence.shift()
      : await collectDetailFieldDomEvidence(page);
    merged = mergeDetailDomEvidence(merged, current);
    if (merged.monthly_sales.length && merged.shipping.length) return merged;
    if (sequence.length) continue;
    if (!page || Date.now() >= deadline) break;
    await page.waitForTimeout(700);
  } while (sequence.length || Date.now() < deadline);
  return merged;
}

if (testHtml) {
  const plainText = testHtml.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ");
  const categoryEvidence = mergeCategoryEvidence(
    categoryEvidenceFromStructuredText(testHtml),
    categoryEvidenceFromJsonLdHtml(testHtml)
  );
  const effectiveDomEvidence = testDomEvidenceSequence.length
    ? await waitForDetailFieldDomEvidence(null, testDomEvidence, testDomEvidenceSequence)
    : testDomEvidence;
  const extracted = extractDetailFieldsFromText(plainText, testHtml, effectiveDomEvidence);
  const fields = Object.fromEntries(Object.entries({
    ...extracted.fields,
    ...categoryFieldsFromEvidence(categoryEvidence),
  }).filter(([, value]) => Array.isArray(value) ? value.length : value));
  console.log(JSON.stringify({
    success: true,
    source: "test_html",
    fields,
    field_evidence: extracted.fieldEvidence,
  }));
  process.exit(0);
}

const runtime = await openRuntime();
const page = runtime.page;
page.setDefaultTimeout(Math.max(15000, Math.min(45000, pageTimeoutMs)));
await page.bringToFront().catch(() => {});
await updateWorkbenchOverlay(page, {
  tone: "working",
  detail: "详情核验已启动，正在打开商品页面。",
  meta: itemId ? `商品ID：${itemId}` : "",
});

async function ensureDetailPageReady(currentText = "", waited = false) {
  let pageText = currentText || await readPageText(page);
  const securityBlocked = looksLikeSecurityPage(pageText, page.url());
  if (securityBlocked) {
    const manualResult = await waitForManualSecurityResolution(page);
    if (manualResult.resumed) return readPageText(page, pageText);
    await runtime.releaseForManualHandoff();
    failWithCode("security_verification_required", securityMessage(page.url(), waited), {
      source: "1688_detail_page",
      cdp: Boolean(cdpUrl),
      item_id: itemId,
      url,
      page_url: page.url(),
      manual_waited_ms: manualResult.waited_ms,
      manual_wait_timeout_ms: manualVerificationWaitMs,
    });
  }
  if (looksLikeLoginPage(pageText, page.url())) {
    emitRpaProgress(
      "manual_intervention",
      "1688 登录状态失效，请在采集 Chrome 中人工登录后重新开始详情核验。",
      { alert_key: `detail-login-${itemId || "unknown"}-${Date.now()}`, percent: 86, intervention_type: "login", item_id: itemId }
    );
    await runtime.releaseForManualHandoff();
    failWithCode("login_required", blockedMessage(page.url(), waited), {
      source: "1688_detail_page",
      cdp: Boolean(cdpUrl),
      item_id: itemId,
      url,
      page_url: page.url(),
    });
  }
  return pageText;
}

try {
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: pageTimeoutMs });
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    await runtime.close().catch(() => {});
    failWithCode(
      "navigation_timeout",
      `1688 详情页加载超时，未写入任何详情字段。请先在真实 Chrome 中人工打开该商品详情页确认能正常访问，再回到本工具核验。${message}`,
      { source: "1688_detail_page", cdp: Boolean(cdpUrl), item_id: itemId, url, page_url: page.url() }
    );
  }
  await page.bringToFront().catch(() => {});
  await humanPause(page, pacing.afterGoto);
  await updateWorkbenchOverlay(page, {
    tone: "working",
    detail: "商品详情页已打开，正在检查登录和页面状态。",
    meta: page.url(),
  });
  let pageText = await readPageText(page);
  const needsManualGate = looksLikeSecurityPage(pageText, page.url()) || looksLikeLoginPage(pageText, page.url());
  if (looksLikeLoginPage(pageText, page.url()) && loginWaitMs > 0 && !headless) {
    await page.waitForTimeout(loginWaitMs);
    pageText = await readPageText(page, pageText);
  }
  pageText = await ensureDetailPageReady(pageText, needsManualGate && loginWaitMs > 0 && !headless);

  await updateWorkbenchOverlay(page, {
    tone: "working",
    detail: "页面状态正常，正在滚动并读取详情字段。",
    meta: itemId ? `商品ID：${itemId}` : page.url(),
  });
  const initialDomEvidence = await collectDetailFieldDomEvidence(page);
  await humanScroll(page);
  await humanPause(page, pacing.beforeRead);
  pageText = await readPageText(page, pageText);
  pageText = await ensureDetailPageReady(pageText, false);
  const text = String(pageText || "").replace(/\s+/g, " ").trim();
  const structuredText = await page.evaluate(() => {
    const scripts = Array.from(document.querySelectorAll("script"))
      .map((script) => script.textContent || "")
      .filter((content) => /shopName|sellerName|companyName|skuCount|refundRate|shipmentRate|collectionRate|dropshipPrice|onePiecePrice|distributionPrice|monthlyDropship|monthDistribution|last30DaysSales|orders30Days|productCategory|leafCategory|categoryId|categoryPath|categoryListName|categoryBreadcrumb|catId|cateId/i.test(content))
      .slice(0, 12)
      .join("\n");
    const meta = Array.from(document.querySelectorAll("[content], [title], [aria-label]"))
      .flatMap((node) => [node.getAttribute("content"), node.getAttribute("title"), node.getAttribute("aria-label")])
      .filter(Boolean)
      .join(" ");
    return `${scripts}\n${meta}`.slice(0, 300000);
  }).catch(() => "");
  const domEvidence = await waitForDetailFieldDomEvidence(page, initialDomEvidence);
  const extracted = extractDetailFieldsFromText(text, structuredText, domEvidence);
  const fields = extracted.fields;
  const fieldEvidence = extracted.fieldEvidence;
  const pageCategoryEvidence = await page.evaluate(() => {
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
        try {
          const parsed = new URL(raw, location.href);
          for (const [key, candidate] of parsed.searchParams.entries()) {
            if (["productcategoryid", "leafcategoryid", "categoryid", "catid", "cateid", "category_id", "cat_id", "cate_id"].includes(key.toLowerCase())) {
              const normalized = normalizeId(candidate);
              if (normalized) return normalized;
            }
          }
        } catch {
          // Non-URL values may still contain a category key below.
        }
        const match = raw.match(/(?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)[^0-9]{0,16}(\d{1,32})/i)
          || raw.match(/[?&#](?:productCategoryId|leafCategoryId|categoryId|catId|cateId|category_id|cat_id|cate_id)=(\d{1,32})/i);
        if (match) return match[1];
      }
      return "";
    };
    const cleanName = (value) => String(value || "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .replace(/\s+/g, " ")
      .replace(/^[>›»→|\-\s]+|[>›»→|\-\s]+$/g, "")
      .trim()
      .slice(0, 160);
    const normalizeBreadcrumb = (values) => {
      const names = [];
      for (const value of Array.isArray(values) ? values : []) {
        const rawName = typeof value === "string"
          ? value
          : (value?.name || value?.title || value?.text || value?.label || value?.item?.name || "");
        const name = cleanName(rawName);
        if (!name || /^(首页|1688|阿里巴巴|全部分类|商品详情)$/.test(name)) continue;
        if (names[names.length - 1] !== name) names.push(name);
      }
      return names.slice(0, 10);
    };
    const visible = (node) => {
      if (!node || typeof node.getBoundingClientRect !== "function") return false;
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const empty = () => ({ category_id: "", category_path: "", category_breadcrumb: [], source: "" });
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
            category_id: categoryIdFromValue(itemUrl),
            category_path: breadcrumb.join(">"),
            category_breadcrumb: breadcrumb,
            source: "json_ld_breadcrumb",
          };
        }
      } catch {
        // Ignore malformed JSON-LD and continue with visible breadcrumbs.
      }
    }
    const roots = Array.from(document.querySelectorAll([
      "nav[aria-label*='breadcrumb' i]",
      "[class*='breadcrumb' i]",
      "[class*='crumb' i]",
      "[data-testid*='breadcrumb' i]",
    ].join(", "))).filter(visible);
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
      if (lastName.length >= 18 && cleanName(document.title).includes(lastName)) breadcrumb.pop();
      if (!breadcrumb.length) continue;
      const categoryId = breadcrumbNodes.map((node) => categoryIdFromValue(
        node.getAttribute?.("data-category-id"),
        node.getAttribute?.("data-categoryid"),
        node.getAttribute?.("data-cat-id"),
        node.getAttribute?.("data-catid"),
        node.getAttribute?.("data-cate-id"),
        node.getAttribute?.("data-cateid"),
        node.href,
        node.getAttribute?.("href")
      )).filter(Boolean).at(-1) || "";
      const candidate = {
        category_id: categoryId,
        category_path: breadcrumb.join(">"),
        category_breadcrumb: breadcrumb,
        source: "dom_breadcrumb",
      };
      if (!best || candidate.category_breadcrumb.length > best.category_breadcrumb.length) best = candidate;
    }
    if (best) return best;
    return empty();
  }).catch(() => emptyCategoryEvidence());
  const detailOfferId = itemId || (String(url || "").match(/offer\/(\d{8,})\.html/i)?.[1] || "");
  const categoryEvidence = mergeCategoryEvidence(
    categoryEvidenceFromStructuredText(structuredText, "structured_data", detailOfferId),
    pageCategoryEvidence
  );
  Object.assign(fields, categoryFieldsFromEvidence(categoryEvidence));
  const shopInfo = await page.evaluate(() => {
    const normalizeUrl = (value) => {
      const text = String(value || "").trim();
      if (!text || /^javascript:/i.test(text)) return "";
      if (text.startsWith("//")) return `https:${text}`;
      if (/^https?:\/\//i.test(text)) return text;
      try {
        return new URL(text, location.href).toString();
      } catch {
        return "";
      }
    };
    const anchors = Array.from(document.querySelectorAll("a[href]"));
    const preferred = anchors.find((anchor) => {
      const href = String(anchor.getAttribute("href") || anchor.href || "");
      const text = String(anchor.innerText || anchor.textContent || anchor.getAttribute("title") || "");
      return /shop|supplier|company|winport|1688\.com/i.test(href)
        && /店铺|进店|供应商|公司|首页/.test(text);
    }) || anchors.find((anchor) => /shop|supplier|company|winport/i.test(String(anchor.getAttribute("href") || anchor.href || "")));
    return {
      url: normalizeUrl(preferred?.getAttribute("href") || preferred?.href || ""),
      name: preferred ? String(preferred.innerText || preferred.textContent || preferred.getAttribute("title") || "") : "",
    };
  }).catch(() => ({ url: "", name: "" }));
  fields.shop_url = shopInfo.url || "";
  if (!fields.shop_name) fields.shop_name = cleanShopName(shopInfo.name || "");

  const cleaned = Object.fromEntries(Object.entries(fields).filter(([, value]) => value));
  await updateWorkbenchOverlay(page, {
    tone: "done",
    detail: "当前商品详情核验完成，正在返回字段结果。",
    meta: `已提取 ${Object.keys(fields).length} 个字段`,
  });
  await page.waitForTimeout(350).catch(() => {});
  await runtime.close();
  console.log(JSON.stringify({
    success: true,
    source: "1688_detail_page",
    cdp: Boolean(cdpUrl),
    item_id: itemId,
    url,
    fields: cleaned,
    field_evidence: fieldEvidence,
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
