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
    };
  }
  const context = await chromium.launchPersistentContext(profileDir, {
    headless,
    viewport: { width: 1440, height: 960 },
    locale: "zh-CN",
  });
  const page = context.pages()[0] || await context.newPage();
  return { page, close: async () => context.close() };
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

function extractDetailFieldsFromText(text, structuredText = "") {
  const combined = `${structuredText || ""} ${text || ""}`.replace(/\s+/g, " ").trim();
  const fields = {};
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
  fields.shipment_speed = firstMatch(combined, [
    /(24小时内发货|48小时内发货|72小时内发货|[0-9]+小时发货|[0-9]+天内发货)/,
  ]);
  fields.wholesale_shipping_fee = stopAtNextLabel(firstMatch(combined, [
    /(首重\s*[0-9]+(?:\.[0-9]+)?元(?:\s*(?:续重|续件)\s*[0-9]+(?:\.[0-9]+)?元)?)/,
    /(运费[:：]?\s*(?:包邮|免运费|另计|到付|首重\s*[0-9]+(?:\.[0-9]+)?元(?:\s*(?:续重|续件)\s*[0-9]+(?:\.[0-9]+)?元)?|最高\s*[0-9]+(?:\.[0-9]+)?元))/,
    /(快递[:：]?\s*(?:包邮|免运费|另计|到付|[0-9]+(?:\.[0-9]+)?元))/,
  ]), 60);
  fields.dropship_shipping_fee = fields.wholesale_shipping_fee;
  fields.free_shipping = /不包邮|运费另计|运费到付/.test(combined)
    ? "否"
    : (/包邮|免运费|卖家承担运费/.test(combined) ? "是" : "");
  fields.supports_dropship = /一件代发|代发/.test(combined) ? "是" : "";
  fields.dropship_rights = fields.supports_dropship ? "页面出现一件代发/代发信息" : "";
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
  fields.monthly_dropship_orders = firstMatch(combined, [
    /月代发(?:订单|销量)?[:：]?\s*([0-9]+(?:\.[0-9]+)?万?\+?)/,
    /代发订单[:：]?\s*([0-9]+(?:\.[0-9]+)?万?\+?)/,
  ]);
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
  return fields;
}

if (testHtml) {
  const plainText = testHtml.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ");
  const categoryEvidence = mergeCategoryEvidence(
    categoryEvidenceFromStructuredText(testHtml),
    categoryEvidenceFromJsonLdHtml(testHtml)
  );
  const fields = Object.fromEntries(Object.entries({
    ...extractDetailFieldsFromText(plainText, testHtml),
    ...categoryFieldsFromEvidence(categoryEvidence),
  }).filter(([, value]) => Array.isArray(value) ? value.length : value));
  console.log(JSON.stringify({ success: true, source: "test_html", fields }));
  process.exit(0);
}

const runtime = await openRuntime();
const page = runtime.page;
page.setDefaultTimeout(Math.max(15000, Math.min(45000, pageTimeoutMs)));

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
  await humanPause(page, pacing.afterGoto);
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

  await humanScroll(page);
  await humanPause(page, pacing.beforeRead);
  pageText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => pageText);
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
        ? securityMessage(page.url(), false)
        : blockedMessage(page.url(), false),
    }));
    process.exit(0);
  }
  const text = String(pageText || "").replace(/\s+/g, " ").trim();
  const structuredText = await page.evaluate(() => {
    const scripts = Array.from(document.querySelectorAll("script"))
      .map((script) => script.textContent || "")
      .filter((content) => /shopName|sellerName|companyName|skuCount|refundRate|shipmentRate|collectionRate|productCategory|leafCategory|categoryId|categoryPath|categoryListName|categoryBreadcrumb|catId|cateId/i.test(content))
      .slice(0, 12)
      .join("\n");
    const meta = Array.from(document.querySelectorAll("[content], [title], [aria-label]"))
      .flatMap((node) => [node.getAttribute("content"), node.getAttribute("title"), node.getAttribute("aria-label")])
      .filter(Boolean)
      .join(" ");
    return `${scripts}\n${meta}`.slice(0, 300000);
  }).catch(() => "");
  const fields = extractDetailFieldsFromText(text, structuredText);
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
