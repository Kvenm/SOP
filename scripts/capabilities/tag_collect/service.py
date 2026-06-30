#!/usr/bin/env python3
"""标签选品采集服务 — 复选标签采集、评分、导出"""

import json
import os
import re
import hashlib
import html as html_lib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape as xml_escape

from _const import TAG_COLLECT_DATA_DIR
from _errors import ServiceError


_DATA_DIR_CACHE: Optional[str] = None
MAX_QUERIES = 50
MAX_ITEMS_PER_QUERY = 50
DETAIL_VERIFICATION_PENDING = "待详情页核验"
PUBLISHABLE_SUGGESTIONS = {"可铺"}
PUBLISHABLE_CANDIDATE_SUGGESTIONS = {"可铺", "谨慎"}
DETAIL_ONLY_FIELDS = {
    "min_order_range",
    "dropship_price",
    "wholesale_shipping_fee",
    "dropship_shipping_fee",
    "free_shipping",
    "product_refund_rate",
    "rights_protection",
    "dropship_rights",
    "shipment_rate",
    "shipment_speed",
    "supports_dropship",
    "waybill_support",
    "return_exchange_support",
    "collection_rate_24h",
    "monthly_dropship_orders",
    "favorite_customers",
    "shop_name",
    "location",
    "company_type",
    "seller_member_type",
    "source_factory",
    "shop_url",
    "certificates",
    "trustpass_years",
    "seller_locations",
    "stock",
    "sku_count",
    "video_query",
    "product_rating",
    "review_tags",
}
DETAIL_VERIFICATION_FIELDS = DETAIL_ONLY_FIELDS | {
    "good_rate",
    "comment_count",
}
AUTO_REVIEW_REQUIRED_DETAIL_FIELDS = {
    "wholesale_shipping_fee",
    "dropship_shipping_fee",
    "free_shipping",
    "product_refund_rate",
    "shipment_rate",
    "collection_rate_24h",
    "supports_dropship",
    "return_exchange_support",
}
DETAIL_VERIFICATION_LEVELS = {"P0", "P1"}
VERIFICATION_STATUS_UNVERIFIED = "unverified"
VERIFICATION_STATUS_SAMPLE = "sample_verified"
VERIFICATION_STATUS_VERIFIED = "verified"
VERIFICATION_STATUS_PARTIAL = "partial_verified"
VERIFICATION_STATUS_FAILED = "failed"
VERIFICATION_STATUS_FILTERED_OUT = "filtered_out"
SECURITY_BLOCK_CODE = "security_verification_required"
LOGIN_REQUIRED_CODE = "login_required"
SEARCH_KEYWORD_ENCODING_CODE = "search_keyword_encoding_error"
SEARCH_BOX_NOT_FOUND_CODE = "search_box_not_found"
SEARCH_RESULTS_NOT_LOADED_CODE = "search_results_not_loaded"
CATEGORY_NAVIGATION_NOT_LOADED_CODE = "category_navigation_not_loaded"
CATEGORY_PATH_INVALID_CODE = "category_path_invalid"
DIRECT_URL_REQUIRED_CODE = "direct_url_required"
DIRECT_URL_COLLECT_SOURCES = {"url_direct", "direct_url", "http_url"}
MANUAL_PAGE_COLLECT_SOURCES = {"manual_url", "manual_page", "current_page"}
REMOVED_LIBRARY_FILTER_KEYS = {
    "selection_modes",
    "order_growth_7d",
    "purchase_concentration_7d",
    "repurchase_rate",
    "certificates",
    "listed_time",
    "product_marks",
    "seller_locations",
}
REMOVED_LIBRARY_RANGE_FILTER_PREFIXES = {
    "order_growth_7d",
    "purchase_concentration_7d",
    "repurchase_rate",
}

AUTOMATION_STATUS_LABELS = {
    "empty": "无结果",
    "collected": "列表已采集",
    "pending_detail": "待详情核验",
    "sample_verified": "样例已核验",
    "verified": "详情已核验",
    "partial": "部分核验",
    "paused": "详情核验暂停",
    "failed": "详情核验失败",
}

AUTOMATION_STAGE_LABELS = {
    "list_collect": "列表初筛",
    "detail_verification": "详情补字段",
    "export_ready": "导出待复核",
}

AUTOMATION_ACTION_LABELS = {
    "adjust_filters": "调整筛选条件",
    "continue_detail_verification": "继续详情核验",
    "manual_handoff": "人工接管验证",
    "manual_review": "导出后人工复核",
}

DIANLEIDA_REFERENCE_EXPORT_LABELS = [
    "序号",
    "商品类目",
    "商品主图",
    "商品标题",
    "商品ID",
    "商品链接(点击下方链接可跳转)",
    "上架时间",
    "批发价",
    "起批范围",
    "近30天订单数",
    "近30天件数",
    "近30天销售额",
    "销售趋势(件)",
    "复购率",
    "代发价",
    "权益保障",
    "代发权益",
    "面单支持",
    "商品标识",
    "资质证书",
    "店铺名称",
    "所在地",
    "综合服务",
    "公司类型",
    "卖家会员类型",
    "卖家服务",
    "诚信通年限",
    "店铺链接(点击下方链接可跳转)",
    "主图链接(点击下方链接可跳转)",
    "月代发订单",
    "SKU数量",
    "收藏客户",
    "评论数",
    "库存",
    "查询视频",
    "批发运费",
    "代发运费",
]

PROJECT_EXTENSION_EXPORT_LABELS = [
    "来源关键词",
    "命中标签",
    "是否包邮",
    "标准化价格区间",
    "下游铺货数",
    "同款/相似款数量",
    "红海/蓝海判断",
    "好评率",
    "品退率",
    "好评率区间",
    "品退率区间",
    "评论数区间",
    "复购率区间",
    "商品星级",
    "评价标签",
    "24小时揽收率",
    "发货率",
    "发货时效",
    "是否一件代发",
    "是否支持退换",
    "发货率区间",
    "近30天销量区间",
    "代发订单量区间",
    "是否源头工厂",
    "适合微信小店(规则预判)",
    "适合抖店",
    "适合拼多多",
    "适合小红书",
    "适合淘宝",
    "推荐平台",
    "品牌/侵权风险",
    "低价质损风险",
    "售后风险",
    "数据不足风险",
    "推荐分",
    "推荐等级",
    "推荐理由",
    "风险提示",
    "标签来源",
    "采集批次",
    "采集时间",
    "核验状态",
    "人工复核状态",
    "人工复核备注",
    "微信小店铺货建议(人工复核)",
]

REFERENCE_EXPORT_LABELS = DIANLEIDA_REFERENCE_EXPORT_LABELS + PROJECT_EXTENSION_EXPORT_LABELS

DIANLEIDA_REFERENCE_EXPORT_KEYS = [
    "seq",
    "category_path",
    "image_cell",
    "title",
    "item_id",
    "url",
    "listed_at",
    "wholesale_price",
    "min_order_range",
    "orders_30d",
    "units_30d",
    "sales_amount_30d",
    "sales_trend_units",
    "repurchase_rate",
    "dropship_price",
    "rights_protection",
    "dropship_rights",
    "waybill_support",
    "product_mark",
    "certificates",
    "shop_name",
    "location",
    "service_score",
    "company_type",
    "seller_member_type",
    "seller_services",
    "trustpass_years",
    "shop_url",
    "image_url",
    "monthly_dropship_orders",
    "sku_count",
    "favorite_customers",
    "comment_count",
    "stock",
    "video_query",
    "wholesale_shipping_fee",
    "dropship_shipping_fee",
]

PROJECT_EXTENSION_EXPORT_KEYS = [
    "source_keyword",
    "matched_tags",
    "free_shipping",
    "normalized_price_band",
    "downstream_offer_count",
    "similar_item_count",
    "market_competition",
    "good_rate",
    "product_refund_rate",
    "good_rate_bucket",
    "product_refund_rate_bucket",
    "comment_count_bucket",
    "repurchase_rate_bucket",
    "product_rating",
    "review_tags",
    "collection_rate_24h",
    "shipment_rate",
    "shipment_speed",
    "supports_dropship",
    "return_exchange_support",
    "shipment_rate_bucket",
    "units_30d_bucket",
    "monthly_dropship_orders_bucket",
    "source_factory",
    "wechat_shop_suggestion",
    "douyin_suggestion",
    "pinduoduo_suggestion",
    "xiaohongshu_suggestion",
    "taobao_suggestion",
    "recommended_platform",
    "brand_ip_risk",
    "low_price_quality_risk",
    "after_sales_risk",
    "data_gap_risk",
    "recommendation_score",
    "recommendation_level",
    "recommendation_reason",
    "risk_flags",
    "tag_source",
    "run_id",
    "collected_at",
    "verification_status",
    "manual_review_status",
    "manual_review_note",
    "manual_wechat_shop_suggestion",
]


@dataclass
class Product:
    """采集工作台内部商品结构；真实搜索结果按同名属性鸭子类型读取。"""

    id: str
    title: str
    price: str
    image: str
    url: str
    stats: Optional[Dict[str, Any]] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_writable_dir(path: str) -> Optional[str]:
    try:
        target = Path(path).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_probe"
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            probe.unlink()
        except OSError:
            pass
        return str(target)
    except OSError:
        return None


def get_tag_collect_data_dir() -> str:
    """返回可写数据目录；本地测试受限时回落到项目内目录。"""
    global _DATA_DIR_CACHE
    if _DATA_DIR_CACHE:
        return _DATA_DIR_CACHE

    candidates = [
        os.environ.get("TAG_COLLECT_DATA_DIR", ""),
        TAG_COLLECT_DATA_DIR,
        str(_repo_root() / ".local-data" / "1688-skill-data" / "tag_collect"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        data_dir = _ensure_writable_dir(candidate)
        if data_dir:
            _DATA_DIR_CACHE = data_dir
            return data_dir
    raise PermissionError("未找到可写的 tag_collect 数据目录")


def _rejected_products_path() -> str:
    return os.path.join(get_tag_collect_data_dir(), "rejected_products.json")


def _empty_rejected_products() -> Dict[str, Any]:
    return {"version": 1, "updated_at": "", "items": {}}


def _load_rejected_products() -> Dict[str, Any]:
    path = _rejected_products_path()
    if not os.path.exists(path):
        return _empty_rejected_products()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return _empty_rejected_products()
    if not isinstance(payload, dict):
        return _empty_rejected_products()
    items = payload.get("items")
    if isinstance(items, list):
        converted: Dict[str, List[Dict[str, Any]]] = {}
        for record in items:
            if not isinstance(record, dict):
                continue
            item_id = str(record.get("item_id") or "").strip()
            if item_id:
                converted.setdefault(item_id, []).append(record)
        payload["items"] = converted
    elif not isinstance(items, dict):
        payload["items"] = {}
    payload["version"] = payload.get("version") or 1
    payload["updated_at"] = payload.get("updated_at") or ""
    return payload


def _save_rejected_products(payload: Dict[str, Any]) -> None:
    path = _rejected_products_path()
    Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now_iso()
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _offer_id_from_url(url: Any) -> str:
    text = str(url or "")
    match = re.search(r"/offer/(\d+)", text) or re.search(r"[?&]offerId=(\d+)", text)
    return match.group(1) if match else ""


def _canonical_item_id(item_id: Any, url: Any = "") -> str:
    return str(item_id or "").strip() or _offer_id_from_url(url)


def _append_rejected_products(records: List[Dict[str, Any]]) -> None:
    normalized = [
        record for record in records
        if isinstance(record, dict) and str(record.get("item_id") or "").strip()
    ]
    if not normalized:
        return
    payload = _load_rejected_products()
    items = payload.setdefault("items", {})
    for record in normalized:
        item_id = str(record.get("item_id") or "").strip()
        existing = items.setdefault(item_id, [])
        if not isinstance(existing, list):
            existing = []
            items[item_id] = existing
        key = (
            str(record.get("filter_signature") or ""),
            str(record.get("target_platform") or ""),
            str(record.get("reason_code") or ""),
        )
        replaced = False
        for index, old in enumerate(existing):
            old_key = (
                str(old.get("filter_signature") or ""),
                str(old.get("target_platform") or ""),
                str(old.get("reason_code") or ""),
            )
            if old_key == key:
                existing[index] = {**old, **record, "updated_at": _now_iso()}
                replaced = True
                break
        if not replaced:
            existing.append({**record, "created_at": record.get("created_at") or _now_iso()})
    _save_rejected_products(payload)


def _target_platform_from_tags(tags: Iterable[Any]) -> str:
    values = [str(item or "").strip() for item in tags if str(item or "").strip()]
    if "微信小店" in values:
        return "微信小店"
    for value in values:
        if value in CHANNEL_TAGS:
            return value
    return "微信小店"


def _filter_signature_from_parts(
    categories: Iterable[Any],
    tags: Iterable[Any],
    exclude_tags: Iterable[Any],
    library_filters: Dict[str, Any],
) -> str:
    payload = {
        "categories": sorted(str(item or "").strip() for item in categories if str(item or "").strip()),
        "tags": sorted(str(item or "").strip() for item in tags if str(item or "").strip()),
        "exclude_tags": sorted(str(item or "").strip() for item in exclude_tags if str(item or "").strip()),
        "library_filters": _sanitize_library_filters(library_filters or {}),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _filter_signature_for_config(config: "TagCollectInput") -> str:
    return _filter_signature_from_parts(
        config.categories,
        config.tags,
        config.exclude_tags,
        config.library_filters,
    )


def _filter_signature_for_payload(payload: Dict[str, Any]) -> str:
    return _filter_signature_from_parts(
        payload.get("categories") or [],
        payload.get("tags") or [],
        payload.get("exclude_tags") or [],
        payload.get("library_filters") or {},
    )


def _matched_rejected_record(
    item_id: Any,
    url: Any,
    rejected_products: Dict[str, Any],
    *,
    filter_signature: str,
    target_platform: str,
) -> Optional[Dict[str, Any]]:
    canonical_id = _canonical_item_id(item_id, url)
    if not canonical_id:
        return None
    records = (rejected_products.get("items") or {}).get(canonical_id, [])
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("expires_at") and str(record.get("expires_at")) < _now_iso():
            continue
        record_platform = str(record.get("target_platform") or "微信小店")
        record_signature = str(record.get("filter_signature") or "")
        if record.get("scope") == "global":
            return record
        if record_platform == target_platform and record_signature == filter_signature:
            return record
    return None


def _rejection_record(
    row: Dict[str, Any],
    *,
    reason_code: str,
    reason_text: str,
    filter_signature: str,
    target_platform: str,
    source_run_id: str = "",
) -> Dict[str, Any]:
    item_id = _canonical_item_id(row.get("item_id"), row.get("url"))
    return {
        "item_id": item_id,
        "url": row.get("url", ""),
        "title": row.get("title", ""),
        "category_path": row.get("category_path", ""),
        "reason_code": reason_code,
        "reason_text": reason_text,
        "target_platform": target_platform,
        "filter_signature": filter_signature,
        "scope": "filter_signature",
        "source_run_id": source_run_id,
        "wechat_shop_suggestion": row.get("wechat_shop_suggestion", ""),
        "recommendation_score": row.get("recommendation_score", ""),
        "created_at": _now_iso(),
    }


def _skip_record_from_product(product: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "item_id": _canonical_item_id(product.id, product.url),
        "url": product.url,
        "title": product.title,
        "reason_code": record.get("reason_code", "historical_rejected"),
        "reason_text": record.get("reason_text", "历史筛除商品，本次跳过"),
        "source_run_id": record.get("source_run_id", ""),
        "filter_signature": record.get("filter_signature", ""),
        "target_platform": record.get("target_platform", ""),
        "skipped_at": _now_iso(),
    }


LEGACY_TAG_CATEGORY_TREE = {
    "女装/女士精品": ["连衣裙", "女式T恤", "女式衬衫", "女式休闲裤", "半身裙", "牛仔裤", "大码女装", "防晒衣"],
    "男装": ["男式T恤", "男式衬衫", "男式休闲裤", "男式牛仔裤", "夹克", "卫衣", "短裤", "商务男装"],
    "内衣": ["文胸", "女士内裤", "男士内裤", "睡衣家居服", "保暖内衣", "袜子", "塑身衣", "吊带背心"],
    "童装": ["童T恤", "童裤", "童裙", "童套装", "校服园服", "婴幼儿服装", "亲子装", "儿童雨衣"],
    "鞋靴": ["女鞋", "男鞋", "童鞋", "拖鞋", "运动鞋", "凉鞋", "靴子", "鞋配件"],
    "箱包皮具": ["女包", "男包", "双肩包", "旅行箱", "钱包卡包", "收纳包", "电脑包", "儿童书包"],
    "配饰/饰品": ["发饰", "项链", "耳饰", "手链", "眼镜", "帽子", "围巾", "腰带"],
    "运动户外": ["户外服装", "运动服饰", "瑜伽用品", "露营装备", "骑行用品", "球类用品", "健身器材", "户外照明"],
    "母婴用品": ["婴童喂养", "纸尿裤", "洗护用品", "孕产用品", "婴儿推车", "安全座椅", "儿童餐具", "母婴收纳"],
    "玩具": ["益智玩具", "毛绒玩具", "积木拼插", "户外玩具", "模型玩具", "遥控玩具", "早教玩具", "儿童手工"],
    "家居日用品": ["雨具", "清洁工具", "一次性用品", "杯壶", "拖鞋", "衣架晾晒", "居家小件", "防护用品"],
    "家纺家饰": ["床品套件", "被芯枕芯", "毛巾浴巾", "地毯地垫", "窗帘", "桌布罩件", "墙贴装饰", "抱枕靠垫"],
    "家装建材": ["厨房卫浴", "五金建材", "灯具灯饰", "墙地面材料", "家具", "装饰材料", "门窗", "智能家居"],
    "收纳清洁": ["收纳盒", "收纳架", "衣物收纳", "厨房收纳", "浴室收纳", "清洁剂", "清洁刷", "垃圾袋"],
    "厨房餐饮": ["锅具", "餐具", "厨房小工具", "烘焙用品", "保鲜用品", "厨房电器配件", "酒店餐饮用品", "茶咖器具"],
    "个护/家清": ["纸品湿巾", "洗衣清洁", "家庭清洁", "身体护理", "口腔护理", "女性护理", "驱蚊除味", "消毒用品"],
    "美妆个护": ["彩妆", "护肤", "美妆工具", "香水香氛", "美容仪器", "洗发护发", "身体护理", "男士护理"],
    "食品酒水": ["休闲零食", "冲调饮品", "粮油调味", "方便速食", "茶叶", "酒水", "生鲜冻品", "保健食品"],
    "宠物及园艺": ["宠物食品", "宠物用品", "宠物玩具", "猫狗出行", "水族用品", "园艺工具", "花盆花架", "种子种苗"],
    "数码电脑": ["数码配件", "电脑配件", "影音设备", "智能设备", "存储设备", "网络设备", "办公数码", "游戏外设"],
    "手机通讯": ["手机壳膜", "充电器", "数据线", "耳机", "移动电源", "手机支架", "直播配件", "通讯设备"],
    "家用电器": ["生活电器", "厨房电器", "个护电器", "季节电器", "大家电配件", "清洁电器", "空气净化", "干衣机"],
    "办公文教": ["办公用品", "学生文具", "本册纸品", "画材画具", "教学用品", "收纳文件", "办公设备", "节庆礼品"],
    "汽摩及配件": ["汽车内饰", "汽车外饰", "车载电器", "维修保养", "摩托车配件", "汽车安全", "清洗美容", "改装用品"],
    "五金工具": ["手动工具", "电动工具", "测量工具", "紧固件", "锁具", "工具箱包", "焊接工具", "园林工具"],
    "电工电气": ["开关插座", "电线电缆", "低压电器", "电源电池", "配电输电", "电工仪表", "连接器", "工业控制"],
    "照明工业": ["室内照明", "户外照明", "商业照明", "LED光源", "灯具配件", "舞台灯", "应急照明", "太阳能灯"],
    "安防劳保": ["劳动防护", "监控设备", "消防用品", "门禁考勤", "交通安全", "防护口罩", "安全标识", "安检设备"],
    "包装": ["纸箱纸盒", "塑料包装", "礼品包装", "食品包装", "胶带", "包装袋", "缓冲材料", "标签标牌"],
    "印刷": ["商业印刷", "包装印刷", "标签印刷", "画册印刷", "不干胶", "数码印刷", "印刷耗材", "印后加工"],
    "纺织皮革": ["面料", "辅料", "纱线", "皮革", "纺织品加工", "无纺布", "蕾丝花边", "织带绳带"],
    "化工": ["日化原料", "涂料油漆", "胶粘剂", "塑料助剂", "化学试剂", "清洗剂", "香精香料", "染料颜料"],
    "橡塑": ["塑料制品", "橡胶制品", "塑料片", "塑料管", "塑料包装", "再生塑料", "泡沫塑料", "橡胶密封"],
    "机械及行业设备": ["包装设备", "食品机械", "农业机械", "清洗设备", "纺织设备", "工业机器人", "泵阀", "通用机械"],
    "冶金矿产": ["金属材料", "钢材", "有色金属", "矿产品", "金属加工材", "磁性材料", "炉料", "金属制品"],
    "农业": ["农资", "园林植物", "畜牧养殖", "农副产品", "水产", "农业工具", "种子种苗", "饲料"],
    "医药保养": ["保健器具", "护理护具", "康复用品", "中医保健", "医疗耗材", "成人用品", "健康监测", "按摩器材"],
    "商务服务": ["设计服务", "营销服务", "物流服务", "检测认证", "软件服务", "摄影摄像", "代运营", "知识产权"],
    "定制/加工": ["服装加工", "饰品加工", "包装定制", "礼品定制", "五金加工", "塑胶加工", "印刷加工", "OEM代工"],
}


def _legacy_to_nested_category_tree(tree: Dict[str, List[str]]) -> Dict[str, Dict[str, List[str]]]:
    return {parent: {child: [] for child in children} for parent, children in tree.items()}


def _load_category_dictionary() -> Dict[str, Any]:
    """加载可追溯类目字典；有1688首页导航字典时不再混入旧运营类目。"""
    dictionary = {
        "version": "legacy-seed",
        "source": "local_legacy_seed",
        "status": "partial_seed",
        "updated_at": "",
        "tree": _legacy_to_nested_category_tree(LEGACY_TAG_CATEGORY_TREE),
    }
    path = Path(__file__).resolve().parent / "category_dict.json"
    if not path.exists():
        return dictionary
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dictionary
    nested: Dict[str, Dict[str, List[str]]] = {}
    for node in data.get("tree", []):
        if not isinstance(node, dict):
            continue
        parent = str(node.get("name") or "").strip()
        if not parent:
            continue
        nested.setdefault(parent, {})
        for child_node in node.get("children", []):
            if not isinstance(child_node, dict):
                continue
            child = str(child_node.get("name") or "").strip()
            if not child:
                continue
            grandchildren = [
                str(grandchild.get("name") or "").strip()
                for grandchild in child_node.get("children", [])
                if isinstance(grandchild, dict) and str(grandchild.get("name") or "").strip()
            ]
            nested[parent][child] = grandchildren
    if not nested:
        return dictionary
    dictionary.update({
        "version": str(data.get("version") or dictionary["version"]),
        "source": str(data.get("source") or dictionary["source"]),
        "status": str(data.get("status") or dictionary["status"]),
        "updated_at": str(data.get("updated_at") or dictionary["updated_at"]),
        "tree": nested,
    })
    return dictionary


CATEGORY_DICTIONARY = _load_category_dictionary()
TAG_CATEGORY_TREE = CATEGORY_DICTIONARY["tree"]
TAG_CATEGORY_OPTIONS = list(TAG_CATEGORY_TREE.keys())


def _normalize_category_path_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _category_path_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for parent, children in TAG_CATEGORY_TREE.items():
        parent_text = str(parent or "").strip()
        if not parent_text:
            continue
        index[_normalize_category_path_text(parent_text)] = parent_text
        for child, grandchildren in (children or {}).items():
            child_text = str(child or "").strip()
            if not child_text:
                continue
            child_path = f"{parent_text}>{child_text}"
            index[_normalize_category_path_text(child_path)] = child_path
            for grandchild in grandchildren or []:
                grand_text = str(grandchild or "").strip()
                if not grand_text:
                    continue
                grand_path = f"{child_path}>{grand_text}"
                index[_normalize_category_path_text(grand_path)] = grand_path
    return index


CATEGORY_PATH_INDEX = _category_path_index()

METRIC_FILTER_GROUPS = {
    "好评率": ["好评率>=90%", "好评率80%-90%", "好评率70%-80%", "好评率<70%"],
    "品退率": ["品退率<2%", "品退率2%-5%", "品退率5%-10%", "品退率>=10%"],
    "发货率": ["发货率>=95%", "发货率90%-95%", "发货率80%-90%", "发货率<80%"],
    "评论数": ["评论数>=1000", "评论数300-999", "评论数100-299", "评论数30-99", "评论数<30"],
    "复购率": ["复购率>=30%", "复购率20%-30%", "复购率10%-20%", "复购率<10%"],
    "近30天销量": ["近30天销量>=5000", "近30天销量1000-4999", "近30天销量500-999", "近30天销量100-499", "近30天销量<100"],
    "代发订单量": ["代发订单量>=1000", "代发订单量500-999", "代发订单量100-499", "代发订单量<100"],
}

TAG_FILTER_GROUPS = {
    "使用场景": [
        "夏季",
        "防晒",
        "收纳",
        "开学季",
        "节日礼品",
        "居家清洁",
        "露营户外",
        "宠物出行",
        "新家装修",
        "母婴护理",
        "办公学习",
        "车载用品",
    ],
    "目标平台": ["微信小店", "抖店", "拼多多", "小红书", "淘宝", "快手", "京东"],
    "价格带": ["10元内", "10-30元", "30-50元", "50-100元", "100-200元", "200元以上", "低客单", "中客单", "高毛利"],
    "经营策略": ["新店破零", "引流款", "利润款", "复购款", "蓝海款", "上新款", "应季款", "内容种草款", "低售后款"],
    "销售表现": ["近30天订单数高", "近30天件数高", "近30天销售额高", "月代发订单高", "销售趋势上升", "复购率高", "收藏客户多"],
    "履约与供应链": [
        "一件代发",
        "一件代发包邮",
        "批发包邮",
        "48小时发货",
        "赠运费险",
        "7天包退货",
        "支持微信小店面单",
        "支持抖音面单",
        "支持拼多多面单",
        "工厂",
        "超级工厂",
        "实力商家",
        "诚信通年限高",
    ],
    "风险控制": ["品牌风险低", "品退率低", "发货率高", "评论数充足", "库存充足", "低价质损风险低", "售后风险低", "数据完整度高"],
    "指标区间": [
        *METRIC_FILTER_GROUPS["好评率"],
        *METRIC_FILTER_GROUPS["品退率"],
        *METRIC_FILTER_GROUPS["发货率"],
        *METRIC_FILTER_GROUPS["评论数"],
        *METRIC_FILTER_GROUPS["复购率"],
        *METRIC_FILTER_GROUPS["近30天销量"],
        *METRIC_FILTER_GROUPS["代发订单量"],
    ],
}

NATIVE_FILTER_SPECS: Dict[str, Dict[str, Any]] = {
    "一件代发": {
        "key": "dropship",
        "label": "一件代发",
        "texts": ["一件代发", "代发", "支持一件代发"],
        "source": "1688_search_filter",
    },
    "一件代发包邮": {
        "key": "dropship_free_shipping",
        "label": "一件代发包邮",
        "texts": ["一件代发包邮", "代发包邮", "包邮"],
        "source": "1688_search_filter",
    },
    "批发包邮": {
        "key": "wholesale_free_shipping",
        "label": "批发包邮",
        "texts": ["批发包邮", "包邮"],
        "source": "1688_search_filter",
    },
    "48小时发货": {
        "key": "ship_48h",
        "label": "48小时发货",
        "texts": ["48小时发货", "48H发货", "48h发货", "48小时内发货", "发货时间"],
        "source": "1688_search_filter",
    },
    "48H发货": {
        "key": "ship_48h",
        "label": "48H发货",
        "texts": ["48H发货", "48h发货", "48小时发货", "48小时内发货", "发货时间"],
        "source": "1688_search_filter",
    },
    "24小时发货": {
        "key": "ship_24h",
        "label": "24小时发货",
        "texts": ["24小时发货", "24H发货", "24h发货", "24小时内发货", "发货时间"],
        "source": "1688_search_filter",
    },
    "24H发货": {
        "key": "ship_24h",
        "label": "24H发货",
        "texts": ["24H发货", "24h发货", "24小时发货", "24小时内发货", "发货时间"],
        "source": "1688_search_filter",
    },
    "72小时发货": {
        "key": "ship_72h",
        "label": "72小时发货",
        "texts": ["72小时发货", "72小时内发货", "发货时间"],
        "source": "1688_search_filter",
    },
    "7天包退货": {
        "key": "seven_day_return",
        "label": "7天包退货",
        "texts": ["7天包退货", "七天无理由", "7天无理由", "7天包退"],
        "source": "1688_search_filter",
    },
    "七天无理由": {
        "key": "seven_day_return",
        "label": "七天无理由",
        "texts": ["七天无理由", "7天无理由", "7天包退货", "7天包退"],
        "source": "1688_search_filter",
    },
    "7天无理由退货": {
        "key": "seven_day_return",
        "label": "7天无理由退货",
        "texts": ["7天无理由退货", "7天无理由", "七天无理由", "7天包退货", "退货"],
        "source": "1688_search_filter",
    },
    "退货包运费": {
        "key": "return_shipping_fee_covered",
        "label": "退货包运费",
        "texts": ["退货包运费", "退货包运费险", "退货运费险", "运费险"],
        "source": "1688_search_filter",
    },
    "赠运费险": {
        "key": "freight_insurance",
        "label": "赠运费险",
        "texts": ["赠运费险", "运费险"],
        "source": "1688_search_filter",
    },
    "工厂": {
        "key": "factory",
        "label": "工厂",
        "texts": ["工厂", "生产厂家", "源头工厂"],
        "source": "1688_search_filter",
    },
    "超级工厂": {
        "key": "super_factory",
        "label": "超级工厂",
        "texts": ["超级工厂"],
        "source": "1688_search_filter",
    },
    "实力商家": {
        "key": "power_seller",
        "label": "实力商家",
        "texts": ["实力商家"],
        "source": "1688_search_filter",
    },
    "诚信通": {
        "key": "trustpass",
        "label": "诚信通",
        "texts": ["诚信通"],
        "source": "1688_search_filter",
    },
    "支持微信小店面单": {
        "key": "waybill_wechat",
        "label": "支持微信小店面单",
        "texts": ["微信小店", "微信面单", "视频号面单", "面单支持"],
        "source": "1688_search_filter",
    },
    "支持淘宝面单": {
        "key": "waybill_taobao",
        "label": "支持淘宝面单",
        "texts": ["淘宝面单", "淘宝", "面单支持"],
        "source": "1688_search_filter",
    },
    "支持抖音面单": {
        "key": "waybill_douyin",
        "label": "支持抖音面单",
        "texts": ["抖音面单", "抖店面单", "抖音", "面单支持"],
        "source": "1688_search_filter",
    },
    "支持拼多多面单": {
        "key": "waybill_pinduoduo",
        "label": "支持拼多多面单",
        "texts": ["拼多多面单", "拼多多", "面单支持"],
        "source": "1688_search_filter",
    },
    "支持小红书面单": {
        "key": "waybill_xiaohongshu",
        "label": "支持小红书面单",
        "texts": ["小红书面单", "小红书", "面单支持"],
        "source": "1688_search_filter",
    },
    "支持快手面单": {
        "key": "waybill_kuaishou",
        "label": "支持快手面单",
        "texts": ["快手面单", "快手", "面单支持"],
        "source": "1688_search_filter",
    },
    "支持京东面单": {
        "key": "waybill_jd",
        "label": "支持京东面单",
        "texts": ["京东面单", "京东", "面单支持"],
        "source": "1688_search_filter",
    },
    "先采后付": {
        "key": "buy_first_pay_later",
        "label": "先采后付",
        "texts": ["先采后付"],
        "source": "1688_search_filter",
    },
    "新品": {
        "key": "new_product",
        "label": "新品",
        "texts": ["新品", "新款"],
        "source": "1688_search_filter",
    },
    "1688严选": {
        "key": "strict_selected_1688",
        "label": "1688严选",
        "texts": ["1688严选", "严选"],
        "source": "1688_search_filter",
    },
    "严选": {
        "key": "strict_selected_1688",
        "label": "严选",
        "texts": ["严选", "1688严选"],
        "source": "1688_search_filter",
    },
    "分销严选": {
        "key": "distribution_selected",
        "label": "分销严选",
        "texts": ["分销严选", "分销"],
        "source": "1688_search_filter",
    },
    "官方物流": {
        "key": "official_logistics",
        "label": "官方物流",
        "texts": ["官方物流", "官方"],
        "source": "1688_search_filter",
    },
    "密文面单": {
        "key": "encrypted_waybill",
        "label": "密文面单",
        "texts": ["密文面单", "隐私面单", "加密面单"],
        "source": "1688_search_filter",
    },
    "晚揽必赔": {
        "key": "late_pickup_compensation",
        "label": "晚揽必赔",
        "texts": ["晚揽必赔", "晚揽必赔付", "揽收必赔"],
        "source": "1688_search_filter",
    },
    "24H支揽率": {
        "key": "support_collection_rate_24h",
        "label": "24H支揽率",
        "texts": ["24H支揽率", "24h支揽率", "24小时支揽率", "24H揽收率"],
        "source": "1688_search_filter",
    },
    "48H支揽率": {
        "key": "support_collection_rate_48h",
        "label": "48H支揽率",
        "texts": ["48H支揽率", "48h支揽率", "48小时支揽率", "48H揽收率"],
        "source": "1688_search_filter",
    },
    "新人首单优惠": {
        "key": "new_customer_discount",
        "label": "新人首单优惠",
        "texts": ["新人首单优惠", "新人价", "新人优惠", "首单优惠"],
        "source": "1688_search_filter",
    },
    "包邮": {
        "key": "free_shipping",
        "label": "包邮",
        "texts": ["包邮"],
        "source": "1688_search_filter",
    },
    "合并供应商": {
        "key": "merge_suppliers",
        "label": "合并供应商",
        "texts": ["合并供应商"],
        "source": "1688_search_filter",
    },
    "跨境Select": {
        "key": "cross_border_select",
        "label": "跨境Select",
        "texts": ["跨境Select", "跨境 Select", "Select"],
        "source": "1688_search_filter",
    },
    "支持定制": {
        "key": "customizable",
        "label": "支持定制",
        "texts": ["支持定制", "定制"],
        "source": "1688_search_filter",
    },
    "镇店之宝": {
        "key": "treasure_product",
        "label": "镇店之宝",
        "texts": ["镇店之宝"],
        "source": "1688_search_filter",
    },
    "跨境专供货源": {
        "key": "cross_border_supply",
        "label": "跨境专供货源",
        "texts": ["跨境专供", "跨境货源", "跨境"],
        "source": "1688_search_filter",
    },
    "授权自有品牌": {
        "key": "authorized_own_brand",
        "label": "授权自有品牌",
        "texts": ["授权自有品牌", "自有品牌", "品牌授权"],
        "source": "1688_search_filter",
    },
    "店铺": {
        "key": "seller_shop",
        "label": "店铺",
        "texts": ["店铺", "商家"],
        "source": "1688_search_filter",
    },
}

SYSTEM_RULE_TAGS = {
    "微信小店",
    "抖店",
    "拼多多",
    "小红书",
    "淘宝",
    "快手",
    "京东",
    "新店破零",
    "引流款",
    "利润款",
    "复购款",
    "蓝海款",
    "上新款",
    "应季款",
    "内容种草款",
    "低售后款",
    "支持微信小店面单",
    "支持抖音面单",
    "支持拼多多面单",
    "品牌风险低",
    "低价质损风险低",
    "售后风险低",
    "数据完整度高",
    "库存充足",
    "诚信通年限高",
    "收藏客户多",
    "销售趋势上升",
}

POST_FILTER_ALIASES = {
    "近30天订单数高": {"field": "orders_30d", "label": "近30天订单数高", "bucket": ">=1000", "status": "legacy_alias"},
    "近30天件数高": {"field": "units_30d", "label": "近30天件数高", "bucket": ">=1000", "status": "legacy_alias"},
    "近30天销售额高": {"field": "sales_amount_30d", "label": "近30天销售额高", "bucket": "needs_manual_threshold", "status": "needs-human-review"},
    "月代发订单高": {"field": "monthly_dropship_orders", "label": "月代发订单高", "bucket": ">=500", "status": "legacy_alias"},
    "复购率高": {"field": "repurchase_rate", "label": "复购率高", "bucket": ">=10%", "status": "legacy_alias"},
    "品退率低": {"field": "product_refund_rate", "label": "品退率低", "bucket": "<5%", "status": "legacy_alias"},
    "发货率高": {"field": "shipment_rate", "label": "发货率高", "bucket": ">=95%", "status": "legacy_alias"},
    "评论数充足": {"field": "comment_count", "label": "评论数充足", "bucket": ">=30", "status": "legacy_alias"},
}

METRIC_TAG_SPECS = {
    "好评率>=90%": {"field": "good_rate", "bucket": ">=90%", "op": ">=", "value": 90},
    "好评率80%-90%": {"field": "good_rate", "bucket": "80%-90%", "op": "range", "min": 80, "max": 90},
    "好评率70%-80%": {"field": "good_rate", "bucket": "70%-80%", "op": "range", "min": 70, "max": 80},
    "好评率<70%": {"field": "good_rate", "bucket": "<70%", "op": "<", "value": 70},
    "品退率<2%": {"field": "product_refund_rate", "bucket": "<2%", "op": "<", "value": 2},
    "品退率2%-5%": {"field": "product_refund_rate", "bucket": "2%-5%", "op": "range", "min": 2, "max": 5},
    "品退率5%-10%": {"field": "product_refund_rate", "bucket": "5%-10%", "op": "range", "min": 5, "max": 10},
    "品退率>=10%": {"field": "product_refund_rate", "bucket": ">=10%", "op": ">=", "value": 10},
    "发货率>=95%": {"field": "shipment_rate", "bucket": ">=95%", "op": ">=", "value": 95},
    "发货率90%-95%": {"field": "shipment_rate", "bucket": "90%-95%", "op": "range", "min": 90, "max": 95},
    "发货率80%-90%": {"field": "shipment_rate", "bucket": "80%-90%", "op": "range", "min": 80, "max": 90},
    "发货率<80%": {"field": "shipment_rate", "bucket": "<80%", "op": "<", "value": 80},
    "评论数>=1000": {"field": "comment_count", "bucket": ">=1000", "op": ">=", "value": 1000},
    "评论数300-999": {"field": "comment_count", "bucket": "300-999", "op": "range", "min": 300, "max": 1000},
    "评论数100-299": {"field": "comment_count", "bucket": "100-299", "op": "range", "min": 100, "max": 300},
    "评论数30-99": {"field": "comment_count", "bucket": "30-99", "op": "range", "min": 30, "max": 100},
    "评论数<30": {"field": "comment_count", "bucket": "<30", "op": "<", "value": 30},
    "复购率>=30%": {"field": "repurchase_rate", "bucket": ">=30%", "op": ">=", "value": 30},
    "复购率20%-30%": {"field": "repurchase_rate", "bucket": "20%-30%", "op": "range", "min": 20, "max": 30},
    "复购率10%-20%": {"field": "repurchase_rate", "bucket": "10%-20%", "op": "range", "min": 10, "max": 20},
    "复购率<10%": {"field": "repurchase_rate", "bucket": "<10%", "op": "<", "value": 10},
    "近30天销量>=5000": {"field": "units_30d", "bucket": ">=5000", "op": ">=", "value": 5000},
    "近30天销量1000-4999": {"field": "units_30d", "bucket": "1000-4999", "op": "range", "min": 1000, "max": 5000},
    "近30天销量500-999": {"field": "units_30d", "bucket": "500-999", "op": "range", "min": 500, "max": 1000},
    "近30天销量100-499": {"field": "units_30d", "bucket": "100-499", "op": "range", "min": 100, "max": 500},
    "近30天销量<100": {"field": "units_30d", "bucket": "<100", "op": "<", "value": 100},
    "代发订单量>=1000": {"field": "monthly_dropship_orders", "bucket": ">=1000", "op": ">=", "value": 1000},
    "代发订单量500-999": {"field": "monthly_dropship_orders", "bucket": "500-999", "op": "range", "min": 500, "max": 1000},
    "代发订单量100-499": {"field": "monthly_dropship_orders", "bucket": "100-499", "op": "range", "min": 100, "max": 500},
    "代发订单量<100": {"field": "monthly_dropship_orders", "bucket": "<100", "op": "<", "value": 100},
}

CHANNEL_TAGS = {
    "微信小店": "weixin",
    "视频号小店": "weixin",
    "抖店": "douyin",
    "抖音": "douyin",
    "拼多多": "pinduoduo",
    "小红书": "xiaohongshu",
    "淘宝": "taobao",
}

SCENE_HINTS = {
    "夏季": "夏季",
    "防晒": "防晒",
    "收纳": "收纳",
    "开学季": "开学",
    "节日礼品": "礼品",
    "居家清洁": "清洁",
    "露营户外": "露营",
    "宠物出行": "宠物",
    "新家装修": "装修",
    "母婴护理": "母婴",
    "办公学习": "办公",
    "车载用品": "车载",
}

EXPORT_FIELD_GROUPS: List[Tuple[str, str, List[Dict[str, str]]]] = [
    ("1", "商品基础", [
        {"number": "1.1", "key": "seq", "label": "序号", "source": "系统生成", "verify": "否"},
        {"number": "1.2", "key": "category_path", "label": "商品类目", "source": "搜索/详情", "verify": "是"},
        {"number": "1.3", "key": "image_cell", "label": "商品主图", "source": "搜索/详情", "verify": "否"},
        {"number": "1.4", "key": "title", "label": "商品标题", "source": "搜索/详情", "verify": "是"},
        {"number": "1.5", "key": "item_id", "label": "商品ID", "source": "搜索/详情", "verify": "是"},
        {"number": "1.6", "key": "url", "label": "商品链接(点击下方链接可跳转)", "source": "搜索/详情", "verify": "是"},
        {"number": "1.7", "key": "image_url", "label": "主图链接(点击下方链接可跳转)", "source": "搜索/详情", "verify": "否"},
        {"number": "1.8", "key": "source_keyword", "label": "来源关键词", "source": "任务上下文", "verify": "否"},
        {"number": "1.9", "key": "matched_tags", "label": "命中标签", "source": "任务上下文", "verify": "否"},
    ]),
    ("2", "价格与成本", [
        {"number": "2.1", "key": "wholesale_price", "label": "批发价", "source": "搜索/详情", "verify": "是"},
        {"number": "2.2", "key": "min_order_range", "label": "起批范围", "source": "详情", "verify": "是"},
        {"number": "2.3", "key": "dropship_price", "label": "代发价", "source": "详情", "verify": "是"},
        {"number": "2.4", "key": "wholesale_shipping_fee", "label": "批发运费", "source": "详情/下单页", "verify": "是"},
        {"number": "2.5", "key": "dropship_shipping_fee", "label": "代发运费", "source": "详情/下单页", "verify": "是"},
        {"number": "2.6", "key": "free_shipping", "label": "是否包邮", "source": "详情/下单页", "verify": "是"},
        {"number": "2.7", "key": "normalized_price_band", "label": "标准化价格区间", "source": "计算", "verify": "否"},
    ]),
    ("3", "销售表现", [
        {"number": "3.1", "key": "listed_at", "label": "上架时间", "source": "搜索/详情", "verify": "建议核验"},
        {"number": "3.2", "key": "orders_30d", "label": "近30天订单数", "source": "搜索/详情", "verify": "否"},
        {"number": "3.3", "key": "units_30d", "label": "近30天件数", "source": "搜索/详情", "verify": "否"},
        {"number": "3.4", "key": "sales_amount_30d", "label": "近30天销售额", "source": "搜索/详情", "verify": "否"},
        {"number": "3.5", "key": "sales_trend_units", "label": "销售趋势(件)", "source": "搜索/详情", "verify": "否"},
        {"number": "3.6", "key": "monthly_dropship_orders", "label": "月代发订单", "source": "详情/可信来源", "verify": "建议核验"},
        {"number": "3.7", "key": "sku_count", "label": "SKU数量", "source": "详情", "verify": "建议核验"},
        {"number": "3.8", "key": "favorite_customers", "label": "收藏客户", "source": "详情/可信来源", "verify": "建议核验"},
    ]),
    ("4", "竞争信息", [
        {"number": "4.1", "key": "downstream_offer_count", "label": "下游铺货数", "source": "搜索 stats/详情", "verify": "否"},
        {"number": "4.2", "key": "similar_item_count", "label": "同款/相似款数量", "source": "后续扩展", "verify": "否"},
        {"number": "4.3", "key": "product_mark", "label": "商品标识", "source": "搜索/详情", "verify": "建议核验"},
        {"number": "4.4", "key": "market_competition", "label": "红海/蓝海判断", "source": "计算", "verify": "否"},
    ]),
    ("5", "口碑与质量", [
        {"number": "5.1", "key": "repurchase_rate", "label": "复购率", "source": "搜索/详情", "verify": "建议核验"},
        {"number": "5.2", "key": "comment_count", "label": "评论数", "source": "详情/可信来源", "verify": "建议核验"},
        {"number": "5.3", "key": "good_rate", "label": "好评率", "source": "搜索 stats/详情", "verify": "建议核验"},
        {"number": "5.4", "key": "product_refund_rate", "label": "品退率", "source": "详情/商家页/可信来源", "verify": "是"},
        {"number": "5.5", "key": "certificates", "label": "资质证书", "source": "详情/商家页", "verify": "建议核验"},
        {"number": "5.6", "key": "good_rate_bucket", "label": "好评率区间", "source": "计算", "verify": "建议核验"},
        {"number": "5.7", "key": "product_refund_rate_bucket", "label": "品退率区间", "source": "计算", "verify": "是"},
        {"number": "5.8", "key": "comment_count_bucket", "label": "评论数区间", "source": "计算", "verify": "建议核验"},
        {"number": "5.9", "key": "repurchase_rate_bucket", "label": "复购率区间", "source": "计算", "verify": "建议核验"},
        {"number": "5.10", "key": "product_rating", "label": "商品星级", "source": "详情/商品评价", "verify": "建议核验"},
        {"number": "5.11", "key": "review_tags", "label": "评价标签", "source": "详情/商品评价", "verify": "建议核验"},
    ]),
    ("6", "履约能力", [
        {"number": "6.1", "key": "rights_protection", "label": "权益保障", "source": "详情", "verify": "是"},
        {"number": "6.2", "key": "dropship_rights", "label": "代发权益", "source": "详情", "verify": "是"},
        {"number": "6.3", "key": "waybill_support", "label": "面单支持", "source": "详情", "verify": "是"},
        {"number": "6.4", "key": "collection_rate_24h", "label": "24小时揽收率", "source": "搜索 stats/详情", "verify": "是"},
        {"number": "6.5", "key": "shipment_rate", "label": "发货率", "source": "详情/商家页/可信来源", "verify": "是"},
        {"number": "6.6", "key": "shipment_speed", "label": "发货时效", "source": "详情", "verify": "是"},
        {"number": "6.7", "key": "supports_dropship", "label": "是否一件代发", "source": "详情", "verify": "是"},
        {"number": "6.8", "key": "return_exchange_support", "label": "是否支持退换", "source": "详情", "verify": "建议核验"},
        {"number": "6.9", "key": "shipment_rate_bucket", "label": "发货率区间", "source": "计算", "verify": "是"},
        {"number": "6.10", "key": "units_30d_bucket", "label": "近30天销量区间", "source": "计算", "verify": "建议核验"},
        {"number": "6.11", "key": "monthly_dropship_orders_bucket", "label": "代发订单量区间", "source": "计算", "verify": "建议核验"},
    ]),
    ("7", "商家信息", [
        {"number": "7.1", "key": "shop_name", "label": "店铺名称", "source": "详情", "verify": "是"},
        {"number": "7.2", "key": "location", "label": "所在地", "source": "详情/商家页", "verify": "建议核验"},
        {"number": "7.3", "key": "service_score", "label": "综合服务", "source": "详情/商家页", "verify": "建议核验"},
        {"number": "7.4", "key": "company_type", "label": "公司类型", "source": "详情/商家页", "verify": "是"},
        {"number": "7.5", "key": "seller_member_type", "label": "卖家会员类型", "source": "详情/商家页", "verify": "是"},
        {"number": "7.6", "key": "seller_services", "label": "卖家服务", "source": "详情/商家页", "verify": "建议核验"},
        {"number": "7.7", "key": "trustpass_years", "label": "诚信通年限", "source": "详情/商家页", "verify": "建议核验"},
        {"number": "7.8", "key": "shop_url", "label": "店铺链接(点击下方链接可跳转)", "source": "详情/商家页", "verify": "是"},
        {"number": "7.9", "key": "source_factory", "label": "是否源头工厂", "source": "详情/商家页", "verify": "是"},
    ]),
    ("8", "平台适配", [
        {"number": "8.1", "key": "wechat_shop_suggestion", "label": "适合微信小店(规则预判)", "source": "规则/人工", "verify": "人工复核"},
        {"number": "8.2", "key": "douyin_suggestion", "label": "适合抖店", "source": "规则/人工", "verify": "否"},
        {"number": "8.3", "key": "pinduoduo_suggestion", "label": "适合拼多多", "source": "规则/人工", "verify": "否"},
        {"number": "8.4", "key": "xiaohongshu_suggestion", "label": "适合小红书", "source": "规则/人工", "verify": "否"},
        {"number": "8.5", "key": "taobao_suggestion", "label": "适合淘宝", "source": "规则/人工", "verify": "否"},
        {"number": "8.6", "key": "recommended_platform", "label": "推荐平台", "source": "规则/人工", "verify": "否"},
        {"number": "8.7", "key": "video_query", "label": "查询视频", "source": "详情/可信来源", "verify": "建议核验"},
    ]),
    ("9", "风险信息", [
        {"number": "9.1", "key": "brand_ip_risk", "label": "品牌/侵权风险", "source": "规则/详情", "verify": "建议核验"},
        {"number": "9.2", "key": "low_price_quality_risk", "label": "低价质损风险", "source": "规则", "verify": "否"},
        {"number": "9.3", "key": "after_sales_risk", "label": "售后风险", "source": "规则/详情", "verify": "是"},
        {"number": "9.4", "key": "data_gap_risk", "label": "数据不足风险", "source": "规则", "verify": "否"},
        {"number": "9.5", "key": "stock", "label": "库存", "source": "详情/可信来源", "verify": "建议核验"},
    ]),
    ("10", "推荐结果与导出信息", [
        {"number": "10.1", "key": "recommendation_score", "label": "推荐分", "source": "计算", "verify": "否"},
        {"number": "10.2", "key": "recommendation_level", "label": "推荐等级", "source": "计算", "verify": "否"},
        {"number": "10.3", "key": "recommendation_reason", "label": "推荐理由", "source": "规则/AI总结", "verify": "否"},
        {"number": "10.4", "key": "risk_flags", "label": "风险提示", "source": "规则/AI总结", "verify": "否"},
        {"number": "10.5", "key": "tag_source", "label": "标签来源", "source": "任务上下文", "verify": "否"},
        {"number": "10.6", "key": "run_id", "label": "采集批次", "source": "系统生成", "verify": "否"},
        {"number": "10.7", "key": "collected_at", "label": "采集时间", "source": "系统生成", "verify": "否"},
        {"number": "10.8", "key": "verification_status", "label": "核验状态", "source": "系统生成", "verify": "是"},
        {"number": "10.9", "key": "manual_review_status", "label": "人工复核状态", "source": "人工", "verify": "人工复核"},
        {"number": "10.10", "key": "manual_review_note", "label": "人工复核备注", "source": "人工", "verify": "人工复核"},
        {"number": "10.11", "key": "manual_wechat_shop_suggestion", "label": "微信小店铺货建议(人工复核)", "source": "规则/人工", "verify": "人工复核"},
    ]),
]

EXPORT_FIELD_DEFINITIONS: List[Dict[str, str]] = [
    dict(field, group_number=group_no, group=group_name)
    for group_no, group_name, fields in EXPORT_FIELD_GROUPS
    for field in fields
]
_EXPORT_KEYS = DIANLEIDA_REFERENCE_EXPORT_KEYS + PROJECT_EXTENSION_EXPORT_KEYS
if len(_EXPORT_KEYS) != len(REFERENCE_EXPORT_LABELS):
    raise RuntimeError("导出字段键和值数量不一致，请检查店雷达基线和扩展字段配置。")
EXPORT_COLUMNS: List[Tuple[str, str]] = list(zip(_EXPORT_KEYS, REFERENCE_EXPORT_LABELS))


def get_numbered_export_columns() -> List[Dict[str, Any]]:
    return [dict(field) for field in EXPORT_FIELD_DEFINITIONS]


APPAREL_CATEGORY_ROOTS = [
    root for root in TAG_CATEGORY_OPTIONS
    if any(token in root for token in ("女装", "男装", "内衣", "鞋", "箱包", "配饰", "运动户外", "玩具童装"))
]
PHYSICAL_GOODS_CATEGORY_ROOTS = [
    *TAG_CATEGORY_OPTIONS,
]


LIBRARY_FILTER_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "scope",
        "title": "类目范围",
        "description": "按1688首页左侧导航类目选择采集范围，避免运营类目映射导致错配。",
        "fields": [
            {
                "key": "category_paths",
                "label": "选择类目",
                "type": "category_tree",
                "multi": True,
                "status": "supported",
                "mapping": "categories",
            },
            {
                "key": "template_name",
                "label": "我的模板",
                "type": "text",
                "status": "reserved",
                "mapping": "template_metadata",
            },
        ],
    },
    {
        "key": "precise_search",
        "title": "精准搜索",
        "description": "生成 1688 搜索词和采集入口。",
        "fields": [
            {
                "key": "search_keyword",
                "label": "商品关键词",
                "type": "text",
                "status": "supported",
                "mapping": "keywords",
            },
            {
                "key": "match_type",
                "label": "匹配方式",
                "type": "radio",
                "options": ["模糊匹配", "精准匹配"],
                "default": "模糊匹配",
                "status": "reserved",
                "mapping": "search_metadata",
            },
            {
                "key": "history_keyword",
                "label": "历史搜索",
                "type": "text",
                "status": "reserved",
                "mapping": "search_metadata",
            },
            {
                "key": "source_urls",
                "label": "1688页面URL",
                "type": "textarea",
                "status": "supported",
                "mapping": "source_urls",
            },
        ],
    },
    {
        "key": "advanced",
        "title": "高级筛选",
        "description": "平台、地区、跨境、自有品牌等筛选。",
        "fields": [
            {
                "key": "downstream_platforms",
                "label": "主营下游平台",
                "type": "multi_chip",
                "options": ["淘宝", "抖店", "拼多多", "小红书", "快手", "京东", "微信小店"],
                "status": "supported",
                "mapping": "platform_tags",
            },
            {
                "key": "sales_regions",
                "label": "主要销售地区",
                "type": "multi_chip",
                "options": ["华东", "华南", "华北", "华中", "西南", "东北", "西北", "港澳台", "海外"],
                "status": "reserved",
                "mapping": "detail_or_seller_region",
            },
            {
                "key": "cross_border_supply",
                "label": "跨境专供货源",
                "type": "boolean",
                "status": "supported",
                "mapping": "native_filter",
            },
            {
                "key": "authorized_own_brand",
                "label": "授权自有品牌",
                "type": "boolean",
                "status": "supported",
                "mapping": "native_filter",
            },
        ],
    },
    {
        "key": "sales",
        "title": "销售信息",
        "description": "可从列表 stats 初筛的字段会直接过滤，详情字段保留核验记录。",
        "fields": [
            {"key": "sales_orders", "label": "销售订单数", "type": "range", "field": "orders_30d", "status": "supported", "mapping": "post_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "sales_units", "label": "销售件数", "type": "range", "field": "units_30d", "status": "supported", "mapping": "post_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "sales_amount", "label": "销售额", "type": "range", "field": "sales_amount_30d", "status": "supported", "mapping": "post_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
        ],
    },
    {
        "key": "product",
        "title": "商品信息",
        "description": "价格、起批、代发、资质和履约权益。",
        "fields": [
            {"key": "wholesale_price", "label": "批发价", "type": "range", "field": "wholesale_price", "status": "supported", "mapping": "post_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "min_order", "label": "最低起批量", "type": "range", "field": "min_order_range", "status": "detail_required", "mapping": "post_filter_after_verify", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "shop_product_count", "label": "店铺商品数", "type": "range", "field": "shop_product_count", "status": "reserved", "mapping": "search_filter_reserved", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "dropship_price", "label": "代发价", "type": "range", "field": "dropship_price", "status": "detail_required", "mapping": "post_filter_after_verify", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "platform_service_filters", "label": "平台服务", "type": "multi_chip", "options": ["新人首单优惠", "新品", "包邮", "严选", "分销严选", "一件代发", "退货包运费", "7天无理由退货", "24H发货", "48H发货"], "status": "supported", "mapping": "native_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "fulfillment_service_filters", "label": "履约服务", "type": "multi_chip", "options": ["官方物流", "密文面单", "晚揽必赔", "24H支揽率", "48H支揽率"], "status": "supported", "mapping": "native_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "rights_protection", "label": "权益保障", "type": "multi_chip", "options": ["批发包邮", "7天包退货", "退货包运费", "7天无理由退货", "赠运费险"], "status": "supported", "mapping": "native_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "fulfillment_times", "label": "发货时间", "type": "multi_chip", "options": ["24小时", "48小时", "72小时"], "status": "supported", "mapping": "native_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "waybill_support", "label": "面单支持", "type": "multi_chip", "options": ["淘宝", "抖音", "拼多多", "小红书", "快手", "京东", "微信小店"], "status": "supported", "mapping": "native_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "dropship_rights", "label": "代发权益", "type": "multi_chip", "options": ["一件代发", "一件代发包邮", "先采后付"], "status": "partial_supported", "mapping": "native_filter", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
        ],
    },
    {
        "key": "seller",
        "title": "卖家信息",
        "description": "卖家能力字段多数需要详情页或商家页核验。",
        "fields": [
            {"key": "trustpass_years", "label": "诚信通年限", "type": "range", "field": "trustpass_years", "status": "detail_required", "mapping": "detail_verify"},
            {"key": "shop_fans", "label": "店铺粉丝数", "type": "range", "field": "shop_fans", "status": "reserved", "mapping": "seller_page_metric"},
            {"key": "seller_location_regions", "label": "所在地", "type": "multi_chip", "options": ["广东", "浙江", "江苏", "山东", "福建", "河北", "河南", "上海", "北京", "安徽"], "status": "partial_supported", "mapping": "native_filter_dropdown", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "seller_features", "label": "商家特色", "type": "multi_chip", "options": ["实力商家", "超级工厂", "诚信通", "源头工厂", "工厂直营"], "status": "partial_supported", "mapping": "native_filter_dropdown", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "business_modes", "label": "经营模式", "type": "multi_chip", "options": ["生产加工", "经销批发", "招商代理", "商业服务", "个体经营"], "status": "partial_supported", "mapping": "native_filter_dropdown", "applicable_roots": PHYSICAL_GOODS_CATEGORY_ROOTS},
            {"key": "merge_suppliers", "label": "合并供应商", "type": "boolean", "status": "supported", "mapping": "native_filter"},
            {"key": "company_type", "label": "公司类型", "type": "radio", "options": ["不限", "店铺", "工厂"], "status": "supported", "mapping": "native_filter"},
            {"key": "seller_services", "label": "卖家服务", "type": "multi_chip", "options": ["深度验厂", "买家保障", "极速退款", "破损包赔", "材质保障"], "status": "reserved", "mapping": "seller_service"},
            {"key": "seller_member_types", "label": "卖家会员类型", "type": "multi_chip", "options": ["实力商家", "超级工厂", "诚信通"], "status": "supported", "mapping": "native_filter"},
        ],
    },
    {
        "key": "review",
        "title": "评价口碑",
        "description": "截图中的星级、好评率、评价数和评价标签来自商品详情页，导出前必须二次核验。",
        "fields": [
            {"key": "product_rating", "label": "商品星级", "type": "range", "field": "product_rating", "status": "detail_required", "mapping": "post_filter_after_verify"},
            {"key": "good_rate", "label": "好评率", "type": "range", "field": "good_rate", "status": "detail_required", "mapping": "post_filter_after_verify"},
            {"key": "comment_count", "label": "评价数", "type": "range", "field": "comment_count", "status": "detail_required", "mapping": "post_filter_after_verify"},
            {"key": "review_tags", "label": "评价标签", "type": "multi_chip", "options": ["客服态度超好", "质感不错", "购买推荐", "质量很好", "发货很快", "包装完好", "性价比高"], "status": "detail_required", "mapping": "detail_review_tags"},
        ],
    },
    {
        "key": "batch_export",
        "title": "批量与导出",
        "description": "对齐店雷达列表工具栏，当前实现导出和详情核验，铺货为 dry-run 预留。",
        "fields": [
            {"key": "stat_period", "label": "统计周期", "type": "select", "options": ["近7天", "近30天", "近90天"], "default": "近30天", "status": "reserved", "mapping": "run_metadata"},
            {"key": "sort_by", "label": "排序", "type": "select", "options": ["推荐分", "销售订单数", "销售件数", "销售额", "复购率", "批发价"], "default": "推荐分", "status": "reserved", "mapping": "result_sort"},
            {"key": "actions", "label": "批量操作", "type": "actions", "options": ["全选", "关注商品", "导出", "铺货Temu", "铺货dry-run"], "status": "partial_supported", "mapping": "ui_action_contract"},
        ],
    },
]


def get_library_filter_schema() -> List[Dict[str, Any]]:
    return json.loads(json.dumps(LIBRARY_FILTER_SCHEMA, ensure_ascii=False))


def _filter_status_label(status: str) -> str:
    return {
        "supported": "已接入",
        "partial_supported": "部分接入",
        "detail_required": "详情核验",
        "reserved": "预留",
    }.get(status, status or "预留")


def _filter_status_message(field: Dict[str, Any]) -> str:
    status = str(field.get("status") or "reserved")
    mapping = str(field.get("mapping") or "")
    if status == "supported":
        if mapping in ("native_filter", "tags_and_system_rules"):
            return "运行采集时会转成 1688 原生筛选或系统规则；RPA 会记录点击结果。"
        if mapping in ("post_filter", "platform_tags", "categories", "keywords", "source_urls"):
            return "运行采集时进入搜索词、列表初筛或后置指标筛选。"
        return "当前采集链路已接入。"
    if status == "partial_supported":
        return "当前可转译为部分原生筛选或规则；缺失能力会进入执行记录，不伪造结果。"
    if status == "detail_required":
        return "列表页不可信，采集后进入详情页/商家页核验，再重新判断筛选结果。"
    return "接口已预留，当前只记录筛选意图，不参与真实过滤。"


def get_library_filter_coverage() -> List[Dict[str, Any]]:
    """返回前端筛选项到采集链路的覆盖状态，用于验收每个筛选是否真的接入。"""
    coverage: List[Dict[str, Any]] = []
    for section in LIBRARY_FILTER_SCHEMA:
        for field in section.get("fields", []):
            status = str(field.get("status") or "reserved")
            coverage.append({
                "section_key": section.get("key", ""),
                "section_title": section.get("title", ""),
                "field_key": field.get("key", ""),
                "label": field.get("label", ""),
                "type": field.get("type", ""),
                "status": status,
                "status_label": _filter_status_label(status),
                "mapping": field.get("mapping", ""),
                "options": field.get("options", []),
                "message": _filter_status_message(field),
            })
    return coverage


def get_library_capabilities() -> Dict[str, Any]:
    return {
        "source": "dianleida_1688_category_library_reference",
        "status_values": {
            "supported": "已接入当前采集/后筛/导出链路",
            "partial_supported": "部分接入，剩余字段进入预留或详情核验",
            "detail_required": "详情页或商家页核验后才可信",
            "reserved": "接口已预留，当前不伪造筛选结果",
        },
        "implemented": [
            "类目范围",
            "商品关键词",
            "1688页面URL",
            "主营下游平台",
            "跨境专供货源",
            "授权自有品牌",
            "销售订单数/件数/销售额后筛",
            "批发价后筛",
            "权益保障",
            "发货时间",
            "平台服务筛选",
            "履约服务筛选",
            "面单支持",
            "代发权益",
            "所在地/商家特色/经营模式部分下拉筛选",
            "公司类型",
            "卖家会员类型",
            "商品星级/评价数/评价标签详情核验",
            "导出与详情核验",
        ],
        "reserved": [
            "筛选模板持久化",
            "历史搜索",
            "店铺粉丝数",
            "卖家服务",
            "关注商品",
            "铺货Temu",
            "在线文档同步",
        ],
    }


SAMPLE_PRODUCTS = [
    Product(
        id="932994257210",
        title="儿童雨衣男童女孩小学生上学专用防暴雨时尚新款青少年书包位雨披",
        price="21.0",
        image="https://cbu01.alicdn.com/img/ibank/O1CN01dz22YI1jGDHHXKCSF_!!2209630284520-0-cib.jpg",
        url="https://detail.1688.com/offer/932994257210.html",
        stats={
            "categoryListName": "居家日用品>挡风、遮阳、防雨工具>连体雨衣、雨披",
            "earliestListingTime": "2025-06-01 10:20:00",
            "last30DaysSales": 1901,
            "totalOrder": 1035,
            "totalSales": 5839,
            "repurchaseRate": 0.31,
            "goodRates": 0.94,
            "remarkCnt": 128,
            "collectionRate24h": 0.96,
            "downstreamOffer": 136,
            "last30DaysDropShippingSales": 820,
        },
    ),
    Product(
        id="1016967552907",
        title="卡其色阔腿裤女2026新款夏季老钱风裤子休闲直筒小个子西装裤长裤",
        price="26.0",
        image="https://cbu01.alicdn.com/img/ibank/O1CN01pP8R5S1xSwyxN4LDk_!!2212655386443-0-cib.jpg",
        url="https://detail.1688.com/offer/1016967552907.html",
        stats={
            "categoryListName": "女装>女式休闲裤>休闲裤",
            "earliestListingTime": "2026-01-23 19:20:00",
            "last30DaysSales": 1298,
            "totalOrder": 1240,
            "totalSales": 5185,
            "repurchaseRate": 0.20,
            "goodRates": 0.91,
            "remarkCnt": 86,
            "collectionRate24h": 0.88,
            "downstreamOffer": 420,
            "last30DaysDropShippingSales": 650,
        },
    ),
    Product(
        id="658275444569",
        title="志高烘干机家用干衣机主机省电风干机速干烘衣机适配大部分干衣机",
        price="65.0",
        image="https://cbu01.alicdn.com/img/ibank/O1CN01qnVIk928WZpFL3Hph_!!2867367940-0-cib.jpg",
        url="https://detail.1688.com/offer/658275444569.html",
        stats={
            "categoryListName": "家用电器>生活电器>干衣机",
            "earliestListingTime": "2021-10-26 15:42:00",
            "last30DaysSales": 1268,
            "totalOrder": 1228,
            "totalSales": 6292,
            "repurchaseRate": 0.27,
            "goodRates": 0.89,
            "remarkCnt": 42,
            "collectionRate24h": 0.92,
            "downstreamOffer": 610,
            "last30DaysDropShippingSales": 310,
        },
    ),
]

SAMPLE_DETAIL_VERIFICATIONS: Dict[str, Dict[str, Any]] = {
    "932994257210": {
        "min_order_range": "2件起批",
        "dropship_price": "23.8",
        "wholesale_shipping_fee": "首重8元，续重4元",
        "dropship_shipping_fee": "全国大部分地区6元，偏远地区另计",
        "free_shipping": "否",
        "product_refund_rate": "1.8%",
        "product_rating": "4.9",
        "good_rate": "96.0%",
        "comment_count": "128",
        "review_tags": "客服态度超好,质量很好,发货很快",
        "rights_protection": "7天无理由/破损包赔",
        "dropship_rights": "支持一件代发",
        "waybill_support": "微信小店,抖店",
        "collection_rate_24h": "96.0%",
        "shipment_rate": "98.6%",
        "shipment_speed": "24-48小时发货",
        "supports_dropship": "是",
        "return_exchange_support": "支持退换",
        "monthly_dropship_orders": "820",
        "sku_count": "18",
        "favorite_customers": "356",
        "shop_name": "义乌童雨户外用品厂",
        "shop_url": "https://yuwu-factory.1688.com/",
        "location": "浙江 金华",
        "company_type": "生产厂家",
        "seller_member_type": "诚信通",
        "source_factory": "是",
        "stock": "现货 12000 件",
        "video_query": "待人工确认素材可用性",
    },
    "1016967552907": {
        "min_order_range": "1件起批",
        "dropship_price": "29.9",
        "wholesale_shipping_fee": "首重7元，续重3元",
        "dropship_shipping_fee": "5元起",
        "free_shipping": "部分地区包邮",
        "product_refund_rate": "3.6%",
        "product_rating": "4.8",
        "good_rate": "95.0%",
        "comment_count": "86",
        "review_tags": "质感不错,购买推荐,性价比高",
        "rights_protection": "7天包退换",
        "dropship_rights": "支持一件代发",
        "waybill_support": "微信小店",
        "collection_rate_24h": "88.0%",
        "shipment_rate": "95.2%",
        "shipment_speed": "48小时内发货",
        "supports_dropship": "是",
        "return_exchange_support": "支持退换",
        "monthly_dropship_orders": "650",
        "sku_count": "24",
        "favorite_customers": "218",
        "shop_name": "广州轻熟女装供应链",
        "shop_url": "https://gz-womenswear.1688.com/",
        "location": "广东 广州",
        "company_type": "经销批发",
        "seller_member_type": "实力商家",
        "source_factory": "否",
        "stock": "现货 8600 件",
        "video_query": "待人工确认素材可用性",
    },
    "658275444569": {
        "min_order_range": "1件起批",
        "dropship_price": "69.0",
        "wholesale_shipping_fee": "首重12元，按地区计费",
        "dropship_shipping_fee": "12-22元",
        "free_shipping": "否",
        "product_refund_rate": "6.8%",
        "product_rating": "4.6",
        "good_rate": "89.0%",
        "comment_count": "42",
        "review_tags": "包装完好,发货很快",
        "rights_protection": "质保一年",
        "dropship_rights": "支持一件代发",
        "waybill_support": "抖店,淘宝",
        "collection_rate_24h": "92.0%",
        "shipment_rate": "93.4%",
        "shipment_speed": "48-72小时发货",
        "supports_dropship": "是",
        "return_exchange_support": "质量问题支持退换",
        "monthly_dropship_orders": "310",
        "sku_count": "12",
        "favorite_customers": "96",
        "shop_name": "佛山小家电供应链",
        "shop_url": "https://fs-appliance.1688.com/",
        "location": "广东 佛山",
        "company_type": "生产厂家",
        "seller_member_type": "诚信通",
        "source_factory": "是",
        "stock": "现货 3200 件",
        "video_query": "待人工确认素材可用性",
    },
}


@dataclass
class TagCollectInput:
    categories: List[str]
    tags: List[str]
    keywords: List[str]
    source_urls: List[str]
    exclude_tags: List[str]
    max_queries: int
    max_items_per_query: int
    sample_data: bool
    output_format: str
    collect_source: str
    library_filters: Dict[str, Any]
    auto_verify_details: bool = False
    auto_verify_max_items: int = 0
    target_publishable_count: int = 0
    skip_historical_rejected: bool = True


def _split_csv(value: str) -> List[str]:
    items: List[str] = []
    for raw in (value or "").replace("，", ",").split(","):
        item = raw.strip()
        if item:
            items.append(item)
    return items


def _parse_library_filters(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return _sanitize_library_filters(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return _sanitize_library_filters(parsed) if isinstance(parsed, dict) else {}
    return {}


def _is_removed_library_filter_key(key: str) -> bool:
    if key in REMOVED_LIBRARY_FILTER_KEYS:
        return True
    return any(key in (f"{prefix}_min", f"{prefix}_max") for prefix in REMOVED_LIBRARY_RANGE_FILTER_PREFIXES)


def _sanitize_library_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(filters or {}).items()
        if not _is_removed_library_filter_key(str(key))
    }


def parse_input(
    categories: str = "",
    tags: str = "",
    keywords: str = "",
    source_urls: str = "",
    exclude_tags: str = "",
    max_queries: int = 20,
    max_items_per_query: int = 20,
    sample_data: bool = False,
    output_format: str = "xlsx",
    collect_source: str = "rpa",
    library_filters: Any = None,
    auto_verify_details: bool = False,
    auto_verify_max_items: int = 0,
    target_publishable_count: int = 0,
    skip_historical_rejected: bool = True,
) -> TagCollectInput:
    parsed_library_filters = _parse_library_filters(library_filters)
    parsed_collect_source = (collect_source or "rpa").lower()
    categories = _validate_category_paths(
        _dedupe_strings(_split_csv(categories) + _library_categories(parsed_library_filters))
    )
    parsed_source_urls = _dedupe_strings(_split_csv(source_urls) + _library_source_urls(parsed_library_filters))
    if parsed_collect_source == "rpa" and categories:
        parsed_source_urls = []
    normalized_max_items = min(MAX_ITEMS_PER_QUERY, max(1, max_items_per_query))
    normalized_target = min(
        MAX_ITEMS_PER_QUERY,
        max(1, int(target_publishable_count or normalized_max_items)),
    )
    return TagCollectInput(
        categories=categories,
        tags=_dedupe_strings(_split_csv(tags) + _library_tags(parsed_library_filters)),
        keywords=_dedupe_strings(_split_csv(keywords) + _library_keywords(parsed_library_filters)),
        source_urls=parsed_source_urls,
        exclude_tags=_split_csv(exclude_tags),
        max_queries=min(MAX_QUERIES, max(1, max_queries)),
        max_items_per_query=normalized_max_items,
        sample_data=sample_data,
        output_format=(output_format or "xlsx").lower(),
        collect_source=parsed_collect_source,
        library_filters=parsed_library_filters,
        auto_verify_details=bool(auto_verify_details),
        auto_verify_max_items=max(0, min(20, int(auto_verify_max_items or 0))),
        target_publishable_count=normalized_target,
        skip_historical_rejected=bool(skip_historical_rejected),
    )


def _dedupe_strings(items: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _category_leaf_terms(leaf: str) -> Dict[str, List[str]]:
    normalized = _normalize_category_path_text(leaf)
    priority_tokens = [
        "男士",
        "女士",
        "男式",
        "女式",
        "儿童",
        "童装",
        "婴童",
        "孕妇",
        "中老年",
        "大码",
        "成人",
        "宠物",
    ]
    priority = [token for token in priority_tokens if token in normalized]
    words = [
        word
        for word in re.split(r"[>、/／,，\s\-]+", str(leaf or ""))
        if len(_normalize_category_path_text(word)) >= 2
    ]
    grams = [
        normalized[index:index + 2]
        for index in range(max(0, len(normalized) - 1))
        if len(normalized[index:index + 2]) >= 2
    ]
    generic = {"男装", "女装", "内衣", "内裤", "类目", "商品", "用品"}
    return {
        "priority": _dedupe_strings(priority),
        "words": _dedupe_strings(words),
        "grams": _dedupe_strings([gram for gram in grams if gram not in generic]),
    }


def _category_suggestion_score(canonical: str, parts: List[str], leaf: str) -> int:
    candidate = _normalize_category_path_text(canonical)
    normalized = _normalize_category_path_text(">".join(parts))
    leaf_text = _normalize_category_path_text(leaf)
    score = 0
    if normalized and normalized in candidate:
        score += 500
    if leaf_text and leaf_text in candidate:
        score += 300
    parent_context = parts[-2] if len(parts) >= 2 else ""
    for part in parts[:-1]:
        if part and part in candidate:
            score += 130 if part == parent_context else 20
    terms = _category_leaf_terms(leaf)
    for token in terms["priority"]:
        if token in candidate:
            score += 220
    for word in terms["words"]:
        word_text = _normalize_category_path_text(word)
        if word_text and word_text not in terms["priority"] and word_text in candidate:
            score += 70
    for gram in terms["grams"]:
        if gram and gram in candidate:
            score += 12
    return score


def _suggest_category_paths(category: str, limit: int = 8) -> List[str]:
    normalized = _normalize_category_path_text(category)
    parts = [_normalize_category_path_text(part) for part in _category_parts(category)]
    leaf = parts[-1] if parts else normalized
    candidates: Dict[str, Tuple[int, int]] = {}
    original_parts = _category_parts(category)

    def add_candidate(canonical: str, score: int, order: int) -> None:
        text = str(canonical or "").strip()
        if not text:
            return
        current = candidates.get(text)
        if current is None or score > current[0] or (score == current[0] and order < current[1]):
            candidates[text] = (score, order)

    for depth in range(len(original_parts) - 1, 0, -1):
        prefix = ">".join(original_parts[:depth])
        canonical_prefix = CATEGORY_PATH_INDEX.get(_normalize_category_path_text(prefix))
        if not canonical_prefix:
            continue
        for order, canonical in enumerate(CATEGORY_PATH_INDEX.values()):
            if canonical.startswith(f"{canonical_prefix}>") and canonical.count(">") == canonical_prefix.count(">") + 1:
                add_candidate(
                    canonical,
                    80 + depth * 20 + _category_suggestion_score(canonical, parts, leaf),
                    order,
                )
    for order, (key, canonical) in enumerate(CATEGORY_PATH_INDEX.items()):
        if key == normalized:
            continue
        score = _category_suggestion_score(canonical, parts, leaf)
        if normalized and normalized in key:
            score += 500
        elif leaf and leaf in key:
            score += 300
        elif parts and all(part and part in key for part in parts[-2:]):
            score += 200
        if score > 0:
            add_candidate(canonical, score, order)
    ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[1][1]))
    return _dedupe_strings([canonical for canonical, _ in ranked])[:limit]


def _validate_category_paths(categories: Iterable[str]) -> List[str]:
    valid: List[str] = []
    invalid: List[Dict[str, Any]] = []
    for category in categories:
        text = str(category or "").strip()
        if not text:
            continue
        canonical = CATEGORY_PATH_INDEX.get(_normalize_category_path_text(text))
        if canonical:
            valid.append(canonical)
            continue
        invalid.append({
            "category_path": text,
            "suggestions": _suggest_category_paths(text),
        })
    if invalid:
        first = invalid[0]
        suggestions = first.get("suggestions") or []
        suggestion_text = f"可选相近类目：{'、'.join(suggestions[:6])}。" if suggestions else "请从页面类目树重新选择 1688 当前存在的路径。"
        error = ServiceError(
            f"{CATEGORY_PATH_INVALID_CODE}: 类目路径不在当前 1688 首页类目字典中：{first.get('category_path')}。"
            f"{suggestion_text}系统不会把该类目改成关键词搜索或宽泛父类采集。"
        )
        error.data = {
            "code": CATEGORY_PATH_INVALID_CODE,
            "source": "category_dictionary",
            "invalid_categories": invalid,
            "category_dictionary": {
                "version": CATEGORY_DICTIONARY.get("version", ""),
                "source": CATEGORY_DICTIONARY.get("source", ""),
                "status": CATEGORY_DICTIONARY.get("status", ""),
                "updated_at": CATEGORY_DICTIONARY.get("updated_at", ""),
            },
        }
        raise error
    return _dedupe_strings(valid)


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return _split_csv(value)
    if value in (None, False):
        return []
    return [str(value).strip()]


def _library_categories(filters: Dict[str, Any]) -> List[str]:
    return _as_list(filters.get("category_paths") or filters.get("categories"))


def _library_keywords(filters: Dict[str, Any]) -> List[str]:
    keywords = _as_list(filters.get("search_keyword") or filters.get("keyword"))
    keywords.extend(_as_list(filters.get("history_keyword")))
    return keywords


def _library_source_urls(filters: Dict[str, Any]) -> List[str]:
    return _as_list(filters.get("source_urls"))


def _library_tags(filters: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    tags.extend(_as_list(filters.get("downstream_platforms")))
    if filters.get("cross_border_supply"):
        tags.append("跨境专供货源")
    if filters.get("authorized_own_brand"):
        tags.append("授权自有品牌")
    tags.extend(_as_list(filters.get("rights_protection")))
    tags.extend([f"{item}发货" for item in _as_list(filters.get("fulfillment_times"))])
    tags.extend([
        f"支持{item}面单" if item not in ("微信小店", "抖音", "拼多多") else {
            "微信小店": "支持微信小店面单",
            "抖音": "支持抖音面单",
            "拼多多": "支持拼多多面单",
        }[item]
        for item in _as_list(filters.get("waybill_support"))
    ])
    tags.extend(_as_list(filters.get("dropship_rights")))
    tags.extend(_as_list(filters.get("platform_service_filters")))
    tags.extend(_as_list(filters.get("fulfillment_service_filters")))
    if filters.get("merge_suppliers"):
        tags.append("合并供应商")
    company_type = str(filters.get("company_type") or "").strip()
    if company_type and company_type != "不限":
        tags.append(company_type)
    tags.extend(_as_list(filters.get("seller_member_types")))
    return _dedupe_strings(tags)


def _channel_from_tags(tags: Iterable[str]) -> str:
    for tag in tags:
        channel = CHANNEL_TAGS.get(tag)
        if channel and channel != "weixin":
            return channel
    return ""


def _query_part_from_tag(tag: str) -> str:
    return SCENE_HINTS.get(tag, tag)


def _dedupe_dicts(items: Iterable[Dict[str, Any]], key_name: str) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []
    for item in items:
        key = str(item.get(key_name) or item.get("label") or item.get("tag") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_native_filters(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in items:
        key = str(item.get("key") or item.get("label") or item.get("tag") or "").strip()
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(item)
            aliases = _dedupe_strings([item.get("label"), item.get("tag")])
            merged[key]["aliases"] = aliases
            order.append(key)
            continue
        current = merged[key]
        current["texts"] = _dedupe_strings([
            *(current.get("texts") or []),
            *(item.get("texts") or []),
        ])
        current["aliases"] = _dedupe_strings([
            *(current.get("aliases") or []),
            item.get("label"),
            item.get("tag"),
        ])
        if item.get("mode") and not current.get("mode"):
            current["mode"] = item.get("mode")
        if item.get("group_label") and not current.get("group_label"):
            current["group_label"] = item.get("group_label")
        if item.get("value") and not current.get("value"):
            current["value"] = item.get("value")
        aliases = current.get("aliases") or []
        if len(aliases) > 1:
            current["label"] = "/".join(aliases)
    return [merged[key] for key in order]


def _category_leaf(category: str) -> str:
    parts = _category_parts(category)
    return parts[-1] if parts else ""


def _category_parts(category: str) -> List[str]:
    return [part.strip() for part in str(category or "").split(">") if part.strip()]


def _category_native_filter(category: str, index: int) -> Dict[str, Any]:
    parts = _category_parts(category)
    return {
        "key": f"category_path_{index}",
        "tag": category,
        "label": f"类目:{category}",
        "category_path": category,
        "texts": parts,
        "source": "category_paths",
        "status": "planned",
        "mode": "category_path",
    }


def build_filter_plan(config: TagCollectInput) -> Dict[str, Any]:
    """把复选标签拆成搜索词、1688页面原生筛选、后置指标筛选和系统规则。"""
    search_terms: List[str] = []
    category_filters: List[Dict[str, Any]] = [
        _category_native_filter(category, index)
        for index, category in enumerate(config.categories, 1)
        if _category_parts(category)
    ]
    native_filters: List[Dict[str, Any]] = []
    post_filters: List[Dict[str, Any]] = []
    system_rules: List[Dict[str, Any]] = []
    unmapped_tags: List[str] = []

    for tag in config.tags:
        if tag in CHANNEL_TAGS:
            system_rules.append({
                "tag": tag,
                "type": "target_platform",
                "field": "recommended_platform",
                "value": CHANNEL_TAGS[tag],
                "status": "configured",
            })
            continue
        if tag in NATIVE_FILTER_SPECS:
            spec = dict(NATIVE_FILTER_SPECS[tag])
            spec["tag"] = tag
            spec["status"] = "planned"
            native_filters.append(spec)
            continue
        if tag in METRIC_TAG_SPECS:
            spec = dict(METRIC_TAG_SPECS[tag])
            spec.update({
                "tag": tag,
                "label": tag,
                "type": "metric_bucket",
                "status": "planned",
            })
            post_filters.append(spec)
            continue
        if tag in POST_FILTER_ALIASES:
            spec = dict(POST_FILTER_ALIASES[tag])
            spec.update({
                "tag": tag,
                "type": "metric_alias",
            })
            post_filters.append(spec)
            continue
        if tag in SYSTEM_RULE_TAGS:
            system_rules.append({
                "tag": tag,
                "type": "system_rule",
                "status": "planned",
            })
            continue
        if tag in SCENE_HINTS:
            search_terms.append(_query_part_from_tag(tag))
            continue
        unmapped_tags.append(tag)
        search_terms.append(_query_part_from_tag(tag))

    library_plan = build_library_filter_plan(config.library_filters)
    search_terms.extend(library_plan["search_terms"])
    native_filters.extend(library_plan["native_filters"])
    post_filters.extend(library_plan["post_filters"])
    system_rules.extend(library_plan["system_rules"])

    return {
        "search_terms": list(dict.fromkeys(term for term in search_terms if term)),
        "category_filters": category_filters,
        "native_filters": _dedupe_native_filters(native_filters),
        "post_filters": _dedupe_dicts(post_filters, "tag"),
        "system_rules": _dedupe_dicts(system_rules, "tag"),
        "unmapped_tags": list(dict.fromkeys(unmapped_tags)),
        "library_filters": config.library_filters,
        "library_filter_results": library_plan["results"],
        "library_reserved_fields": library_plan["reserved_fields"],
        "notes": (
            "标签已拆为搜索词、1688页面原生筛选、后置指标筛选和系统规则。"
            "原生筛选必须由RPA尝试点击；未找到会记录 not_found，不再静默拼回搜索词。"
        ),
    }


def _range_rule(
    filters: Dict[str, Any],
    prefix: str,
    field: str,
    label: str,
    *,
    status: str = "planned",
    detail_required: bool = False,
) -> Optional[Dict[str, Any]]:
    min_value = filters.get(f"{prefix}_min")
    max_value = filters.get(f"{prefix}_max")
    if min_value in (None, "") and max_value in (None, ""):
        value = filters.get(prefix)
        if not isinstance(value, dict):
            return None
        min_value = value.get("min")
        max_value = value.get("max")
    has_min = min_value not in (None, "")
    has_max = max_value not in (None, "")
    if not has_min and not has_max:
        return None
    rule: Dict[str, Any] = {
        "tag": f"{label}{min_value or ''}-{max_value or ''}",
        "label": label,
        "field": field,
        "type": "library_range",
        "status": "detail_required" if detail_required else status,
        "bucket": f"{min_value or '-∞'}~{max_value or '+∞'}",
        "source": "library_filters",
    }
    try:
        if has_min and has_max:
            rule.update({"op": "range", "min": float(min_value), "max": float(max_value)})
        elif has_min:
            rule.update({"op": ">=", "value": float(min_value)})
        else:
            rule.update({"op": "<=", "value": float(max_value)})
    except (TypeError, ValueError):
        rule.update({"op": "", "status": "manual_review_required"})
    return rule


def _library_result(field_key: str, label: str, status: str, mapping: str, message: str = "") -> Dict[str, Any]:
    return {
        "field_key": field_key,
        "label": label,
        "status": status,
        "mapping": mapping,
        "message": message,
    }


def _native_filter_for_tag(tag: str, *, source: str = "library_filters") -> Optional[Dict[str, Any]]:
    if tag not in NATIVE_FILTER_SPECS:
        return None
    spec = dict(NATIVE_FILTER_SPECS[tag])
    spec["tag"] = tag
    spec["status"] = "planned"
    spec["source"] = source
    return spec


def _dropdown_native_filter(
    key: str,
    label: str,
    option: str,
    texts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    value = str(option or "").strip()
    text_candidates = texts or [label, value]
    return {
        "key": f"{key}_{value}",
        "tag": value,
        "label": f"{label}:{value}",
        "group_label": label,
        "value": value,
        "texts": text_candidates,
        "source": "library_filters",
        "status": "planned",
        "mode": "dropdown_option",
    }


def _text_contains_rule(field_key: str, label: str, expected: str) -> Dict[str, Any]:
    value = str(expected or "").strip()
    return {
        "tag": value,
        "label": label,
        "field": field_key,
        "type": "detail_text_contains",
        "status": "detail_required",
        "expected": value,
        "bucket": value,
        "op": "contains",
        "source": "library_filters",
    }


def build_library_filter_plan(filters: Dict[str, Any]) -> Dict[str, Any]:
    """把店雷达选品库字段转成当前采集链路可执行/待核验/预留的计划。"""
    filters = _sanitize_library_filters(filters)
    if not filters:
        return {
            "search_terms": [],
            "native_filters": [],
            "post_filters": [],
            "system_rules": [],
            "results": [],
            "reserved_fields": [],
        }

    search_terms: List[str] = []
    native_filters: List[Dict[str, Any]] = []
    post_filters: List[Dict[str, Any]] = []
    system_rules: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    reserved_fields: List[Dict[str, Any]] = []

    for tag in _library_tags(filters):
        spec = _native_filter_for_tag(tag)
        if spec:
            native_filters.append(spec)
        elif tag in CHANNEL_TAGS:
            system_rules.append({
                "tag": tag,
                "type": "target_platform",
                "field": "recommended_platform",
                "value": CHANNEL_TAGS[tag],
                "status": "configured",
                "source": "library_filters",
            })
        else:
            system_rules.append({
                "tag": tag,
                "type": "library_rule",
                "status": "planned",
                "source": "library_filters",
            })

    range_specs = [
        ("sales_orders", "orders_30d", "销售订单数", False),
        ("sales_units", "units_30d", "销售件数", False),
        ("sales_amount", "sales_amount_30d", "销售额", False),
        ("wholesale_price", "wholesale_price", "批发价", False),
        ("min_order", "min_order_range", "最低起批量", True),
        ("dropship_price", "dropship_price", "代发价", True),
        ("trustpass_years", "trustpass_years", "诚信通年限", True),
        ("product_rating", "product_rating", "商品星级", True),
        ("good_rate", "good_rate", "好评率", True),
        ("comment_count", "comment_count", "评价数", True),
    ]
    for prefix, field, label, detail_required in range_specs:
        rule = _range_rule(filters, prefix, field, label, detail_required=detail_required)
        if rule:
            post_filters.append(rule)
            results.append(_library_result(
                prefix,
                label,
                "detail_required" if detail_required else "supported",
                "post_filter",
                "详情页核验后判断" if detail_required else "列表字段初筛",
            ))

    list_field_map = [
        ("category_paths", "选择类目", "categories", _library_categories(filters)),
        ("search_keyword", "商品关键词", "keywords", _library_keywords(filters)),
        ("source_urls", "1688页面URL", "source_urls", _library_source_urls(filters)),
        ("downstream_platforms", "主营下游平台", "platform_tags", _as_list(filters.get("downstream_platforms"))),
        ("rights_protection", "权益保障", "native_filter", _as_list(filters.get("rights_protection"))),
        ("fulfillment_times", "发货时间", "native_filter", _as_list(filters.get("fulfillment_times"))),
        ("platform_service_filters", "平台服务", "native_filter", _as_list(filters.get("platform_service_filters"))),
        ("fulfillment_service_filters", "履约服务", "native_filter", _as_list(filters.get("fulfillment_service_filters"))),
        ("waybill_support", "面单支持", "native_filter", _as_list(filters.get("waybill_support"))),
        ("dropship_rights", "代发权益", "native_filter", _as_list(filters.get("dropship_rights"))),
        ("seller_location_regions", "所在地", "native_filter_dropdown", _as_list(filters.get("seller_location_regions"))),
        ("seller_features", "商家特色", "native_filter_dropdown", _as_list(filters.get("seller_features"))),
        ("business_modes", "经营模式", "native_filter_dropdown", _as_list(filters.get("business_modes"))),
        ("seller_member_types", "卖家会员类型", "native_filter", _as_list(filters.get("seller_member_types"))),
        ("review_tags", "评价标签", "detail_review_tags", _as_list(filters.get("review_tags"))),
    ]
    for key, label, mapping, values in list_field_map:
        if values:
            results.append(_library_result(key, label, "supported", mapping, ",".join(values)))

    if filters.get("cross_border_supply"):
        results.append(_library_result("cross_border_supply", "跨境专供货源", "supported", "native_filter"))
    if filters.get("authorized_own_brand"):
        results.append(_library_result("authorized_own_brand", "授权自有品牌", "supported", "native_filter"))
    if filters.get("merge_suppliers"):
        results.append(_library_result("merge_suppliers", "合并供应商", "supported", "native_filter"))
    company_type = str(filters.get("company_type") or "").strip()
    if company_type and company_type != "不限":
        results.append(_library_result("company_type", "公司类型", "supported", "native_filter", company_type))

    for value in _as_list(filters.get("seller_location_regions")):
        native_filters.append(_dropdown_native_filter("seller_location", "所在地", value, ["所在地", value]))
    for value in _as_list(filters.get("seller_features")):
        if not _native_filter_for_tag(value):
            native_filters.append(_dropdown_native_filter("seller_feature", "商家特色", value, ["商家特色", value]))
    for value in _as_list(filters.get("business_modes")):
        if not _native_filter_for_tag(value):
            native_filters.append(_dropdown_native_filter("business_mode", "经营模式", value, ["经营模式", value]))
    for value in _as_list(filters.get("review_tags")):
        post_filters.append(_text_contains_rule("review_tags", "评价标签", value))

    reserved_specs = [
        ("template_name", "我的模板", "模板管理待持久化"),
        ("match_type", "匹配方式", "当前记录元数据，RPA 暂不区分 1688 搜索匹配模式"),
        ("history_keyword", "历史搜索", "历史搜索列表待持久化"),
        ("sales_regions", "主要销售地区", "需商家页或店雷达数据源支撑"),
        ("shop_product_count", "店铺商品数", "1688 搜索页有筛选入口，但当前未稳定读取/回填店铺商品数"),
        ("shop_fans", "店铺粉丝数", "需商家页采集"),
        ("seller_services", "卖家服务", "需商家页服务标签"),
        ("stat_period", "统计周期", "当前导出为近30天基线"),
        ("sort_by", "排序", "当前按推荐分排序"),
    ]
    reserved_defaults = {
        "match_type": "模糊匹配",
        "stat_period": "近30天",
        "sort_by": "推荐分",
    }
    for key, label, message in reserved_specs:
        value = filters.get(key)
        if str(value or "").strip() == reserved_defaults.get(key):
            continue
        has_range_value = filters.get(f"{key}_min") not in (None, "") or filters.get(f"{key}_max") not in (None, "")
        has_value = bool(_as_list(value)) if not isinstance(value, dict) else any(v not in (None, "") for v in value.values())
        if has_value:
            record = _library_result(key, label, "reserved", "interface_reserved", message)
            reserved_fields.append(record)
            results.append(record)
        elif has_range_value:
            record = _library_result(key, label, "reserved", "interface_reserved", message)
            record["bucket"] = f"{filters.get(f'{key}_min') or '-∞'}~{filters.get(f'{key}_max') or '+∞'}"
            reserved_fields.append(record)
            results.append(record)

    return {
        "search_terms": search_terms,
        "native_filters": native_filters,
        "post_filters": post_filters,
        "system_rules": system_rules,
        "results": _dedupe_dicts(results, "field_key"),
        "reserved_fields": _dedupe_dicts(reserved_fields, "field_key"),
    }


def build_queries(config: TagCollectInput) -> List[str]:
    filter_plan = build_filter_plan(config)
    bases = config.keywords or [""]
    bases = [base for base in bases if base]
    hints = filter_plan["search_terms"]

    queries: List[str] = []
    if bases:
        for base in bases:
            if hints:
                for hint in hints:
                    q = " ".join(part for part in [base, hint] if part).strip()
                    if q and q not in queries:
                        queries.append(q)
            elif base and base not in queries:
                queries.append(base)
    elif hints:
        queries = [hint for hint in hints if hint]

    if not queries:
        queries = [""] if config.categories else ["1688 选品"]
    return queries[: config.max_queries]


def build_filter_rule_summary(config: TagCollectInput) -> Dict[str, Any]:
    filter_plan = build_filter_plan(config)
    return {
        "and_tags": config.tags,
        "or_categories": config.categories,
        "source_urls": config.source_urls,
        "exclude_tags": config.exclude_tags,
        "category_filters": filter_plan["category_filters"],
        "search_terms": filter_plan["search_terms"],
        "native_filters": filter_plan["native_filters"],
        "post_filters": filter_plan["post_filters"],
        "system_rules": filter_plan["system_rules"],
        "unmapped_tags": filter_plan["unmapped_tags"],
        "library_filters": config.library_filters,
        "library_filter_results": filter_plan.get("library_filter_results", []),
        "library_reserved_fields": filter_plan.get("library_reserved_fields", []),
        "category_dictionary": {
            "version": CATEGORY_DICTIONARY.get("version", ""),
            "source": CATEGORY_DICTIONARY.get("source", ""),
            "status": CATEGORY_DICTIONARY.get("status", ""),
        },
        "notes": filter_plan["notes"],
    }


def _sample_products_for_query(query: str, limit: int) -> List[Product]:
    query_lower = query.lower()
    matched = [
        p for p in SAMPLE_PRODUCTS
        if query_lower in p.title.lower()
        or query_lower in str((p.stats or {}).get("categoryListName", "")).lower()
    ]
    return (matched or SAMPLE_PRODUCTS)[:limit]


def _default_filter_results(filter_plan: Dict[str, Any], *, source: str, query: str = "") -> List[Dict[str, Any]]:
    status = "sample_skipped" if source == "sample" else "not_applicable"
    message = "样例模式未打开1688页面" if source == "sample" else "当前采集来源不支持页面点击筛选"
    return [
        {
            "filter_key": spec.get("key", ""),
            "label": spec.get("label", spec.get("tag", "")),
            "tag": spec.get("tag", ""),
            "status": status,
            "source": source,
            "query": query,
            "page_url": "",
            "message": message,
            "matched_text": "",
        }
        for spec in filter_plan.get("native_filters", [])
    ]


def _html_to_text(value: Any) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", str(value or ""), flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_lib.unescape(re.sub(r"\s+", " ", text)).strip()


def _looks_like_1688_blocked(text: str, url: str = "") -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return (
        "_____tmd_____" in compact
        or "x5secdata" in compact
        or "punish" in str(url or "").lower()
        or "punish" in compact.lower()
        or any(term in compact for term in ("访问被拒绝", "访问受限", "安全验证", "验证码", "拖动下方滑块", "滑块", "验证失败"))
    )


def _looks_like_1688_login(text: str, url: str = "") -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return (
        "login.1688.com" in str(url or "")
        or "login.taobao.com" in str(url or "")
        or any(term in compact for term in ("扫码登录", "密码登录", "手机登录", "会员登录"))
    )


def _fetch_1688_url_direct(source_url: str) -> Tuple[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "close",
    }
    req = Request(source_url, headers=headers)
    try:
        with urlopen(req, timeout=15) as response:
            final_url = response.geturl()
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(2_500_000).decode(charset, "replace")
            return body, final_url
    except HTTPError as exc:
        body = exc.read(2000).decode("utf-8", "replace")
        error = ServiceError(f"direct_url_fetch_failed: URL 直连读取失败，HTTP {exc.code}。")
        error.data = {
            "code": "direct_url_fetch_failed",
            "source": "url_direct",
            "page_url": source_url,
            "status": exc.code,
            "body_head": body[:500],
        }
        raise error
    except URLError as exc:
        error = ServiceError(f"direct_url_fetch_failed: URL 直连读取失败：{exc.reason}")
        error.data = {
            "code": "direct_url_fetch_failed",
            "source": "url_direct",
            "page_url": source_url,
        }
        raise error


def _product_id_from_url(url: str) -> str:
    patterns = [
        r"/offer/(\d+)\.html",
        r"(?:offerId|offerid|offer_id)=(\d+)",
        r"/(\d{8,})(?:\.html|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(url or ""))
        if match:
            return match.group(1)
    return ""


def _title_near_link(html: str, start: int, end: int) -> str:
    window = html[max(0, start - 900): min(len(html), end + 1200)]
    attr_candidates = []
    for pattern in (
        r'(?:title|alt|aria-label)=["\']([^"\']{4,160})["\']',
        r'"(?:title|subject|offerTitle|name)"\s*:\s*"([^"]{4,160})"',
    ):
        attr_candidates.extend(re.findall(pattern, window, flags=re.I))
    text_candidates = re.split(r"[\n\r。；;<>]+", _html_to_text(window))
    for candidate in [*attr_candidates, *text_candidates]:
        title = _clean_product_title(candidate)
        if title:
            return title
    return ""


def _price_near_link(html: str, start: int, end: int) -> str:
    window = html[max(0, start - 700): min(len(html), end + 900)]
    for pattern in (
        r'(?:¥|￥)\s*(\d+(?:\.\d+)?)',
        r'"(?:price|priceRange|discountPrice|salePrice)"\s*:\s*"?(\d+(?:\.\d+)?)',
    ):
        match = re.search(pattern, window, flags=re.I)
        if match:
            return match.group(1)
    return "-"


def _parse_direct_url_products(html: str, final_url: str, limit: int) -> List[Product]:
    products: List[Product] = []
    seen_ids: set[str] = set()
    link_pattern = re.compile(
        r'https?:\\/\\/detail\.1688\.com\\/offer\\/\d+\.html'
        r'|https?://detail\.1688\.com/offer/\d+\.html'
        r'|//detail\.1688\.com/offer/\d+\.html'
        r'|/offer/\d+\.html',
        re.I,
    )
    for match in link_pattern.finditer(html):
        raw_url = html_lib.unescape(match.group(0)).replace("\\/", "/")
        url = urljoin(final_url, raw_url if not raw_url.startswith("//") else f"https:{raw_url}")
        item_id = _product_id_from_url(url)
        if not item_id or item_id in seen_ids:
            continue
        title = _title_near_link(html, match.start(), match.end()) or f"1688商品 {item_id}"
        if not _clean_product_title(title):
            continue
        seen_ids.add(item_id)
        products.append(Product(
            id=item_id,
            title=_clean_product_title(title),
            price=_price_near_link(html, match.start(), match.end()),
            image="",
            url=url,
            stats={
                "rawText": "URL直连读取候选",
                "sourceUrl": final_url,
                "categoryListName": "",
            },
        ))
        if len(products) >= limit:
            break

    if not products:
        detail_id = _product_id_from_url(final_url)
        if detail_id:
            title = _title_near_link(html, 0, min(len(html), 2000)) or f"1688商品 {detail_id}"
            products.append(Product(
                id=detail_id,
                title=_clean_product_title(title) or f"1688商品 {detail_id}",
                price=_price_near_link(html, 0, min(len(html), 2000)),
                image="",
                url=final_url,
                stats={
                    "rawText": "URL直连读取详情页候选",
                    "sourceUrl": final_url,
                    "categoryListName": "",
                },
            ))
    return products[:limit]


def collect_products_from_direct_url(source_url: str, limit: int) -> Dict[str, Any]:
    html, final_url = _fetch_1688_url_direct(source_url)
    visible_text = _html_to_text(html[:8000])
    if _looks_like_1688_login(visible_text + html[:2000], final_url):
        error = ServiceError(
            f"{LOGIN_REQUIRED_CODE}: URL 直连读取被 1688 登录页拦截。"
            "该模式不能继承你的浏览器登录态，请改用人工页面读取/真实 Chrome，或先换一个无需登录即可访问的 1688 URL。"
        )
        error.data = {"code": LOGIN_REQUIRED_CODE, "source": "url_direct", "page_url": final_url, "cdp": False}
        raise error
    if _looks_like_1688_blocked(visible_text + html[:3000], final_url):
        error = ServiceError(
            f"{SECURITY_BLOCK_CODE}: URL 直连读取被 1688 风控拦截，页面返回 punish/安全验证。"
            "这不是项目代码报错；无需 Chrome 模式不能绕过平台风控，也不会继续采集或导出不可信数据。"
        )
        error.data = {"code": SECURITY_BLOCK_CODE, "source": "url_direct", "page_url": final_url, "cdp": False}
        raise error
    products = _parse_direct_url_products(html, final_url, limit)
    if not products:
        error = ServiceError(
            f"{SEARCH_RESULTS_NOT_LOADED_CODE}: URL 直连读取成功，但页面中没有发现可解析的 1688 商品链接。"
            "请确认粘贴的是商品详情页或商品列表页，且不是登录/空结果/脚本占位页。"
        )
        error.data = {"code": SEARCH_RESULTS_NOT_LOADED_CODE, "source": "url_direct", "page_url": final_url, "cdp": False}
        raise error
    return {
        "products": products,
        "filter_results": [],
        "final_url": final_url,
    }


def _normalize_filter_results(results: Any, filter_plan: Dict[str, Any], *, source: str, query: str) -> List[Dict[str, Any]]:
    if not isinstance(results, list):
        return _default_filter_results(filter_plan, source=source, query=query)
    normalized: List[Dict[str, Any]] = []
    for record in results:
        if not isinstance(record, dict):
            continue
        normalized_record = {
            "filter_key": str(record.get("filter_key") or record.get("key") or ""),
            "label": str(record.get("label") or record.get("tag") or ""),
            "tag": str(record.get("tag") or record.get("label") or ""),
            "status": str(record.get("status") or "unknown"),
            "source": str(record.get("source") or source),
            "query": str(record.get("query") or query),
            "page_url": str(record.get("page_url") or ""),
            "message": str(record.get("message") or ""),
            "matched_text": str(record.get("matched_text") or record.get("text") or ""),
        }
        for extra_key in (
            "expected_path",
            "matched_path",
            "expected_depth",
            "matched_depth",
            "final_url",
            "missing_text",
            "category_steps",
            "diagnostics",
        ):
            if extra_key in record:
                normalized_record[extra_key] = record.get(extra_key)
        normalized.append(normalized_record)
    planned_keys = {
        str(spec.get("key") or spec.get("label") or "")
        for spec in [
            *(filter_plan.get("category_filters", []) or []),
            *(filter_plan.get("native_filters", []) or []),
        ]
    }
    seen_keys = {str(record.get("filter_key") or record.get("label") or "") for record in normalized}
    missing = [
        spec for spec in [
            *(filter_plan.get("category_filters", []) or []),
            *(filter_plan.get("native_filters", []) or []),
        ]
        if str(spec.get("key") or spec.get("label") or "") not in seen_keys
    ]
    normalized.extend([
        {
            "filter_key": spec.get("key", ""),
            "label": spec.get("label", spec.get("tag", "")),
            "tag": spec.get("tag", ""),
            "status": "not_reported",
            "source": source,
            "query": query,
            "page_url": "",
            "message": "RPA未返回该筛选项执行结果",
            "matched_text": "",
        }
        for spec in missing
    ])
    return [record for record in normalized if record.get("filter_key") or record.get("label") or planned_keys]


def _blocking_category_result(filter_results: List[Dict[str, Any]], filter_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    planned_keys = {
        str(spec.get("key") or spec.get("label") or "")
        for spec in filter_plan.get("category_filters", []) or []
    }
    if not planned_keys:
        return None
    blocking_statuses = {"not_found", "click_failed", "not_reported", "partial_clicked", "not_confirmed"}
    for record in filter_results:
        key = str(record.get("filter_key") or record.get("key") or record.get("label") or "")
        source = str(record.get("source") or "")
        status = str(record.get("status") or "")
        if (key in planned_keys or source == "1688_category_navigation") and status in blocking_statuses:
            return record
    return None


def _raise_category_navigation_failed(record: Dict[str, Any]) -> None:
    label = str(record.get("tag") or record.get("label") or record.get("filter_key") or "-")
    message = str(record.get("message") or "1688 类目导航未完成")
    error = ServiceError(f"{CATEGORY_NAVIGATION_NOT_LOADED_CODE}: 1688 类目导航未完成，未生成任何数据。当前类目：{label}。{message}")
    error.data = {
        "code": CATEGORY_NAVIGATION_NOT_LOADED_CODE,
        "source": "1688_category_navigation",
        "page_url": str(record.get("final_url") or record.get("page_url") or ""),
        "category_path": label,
        "filter_results": [record],
        "diagnostics": record.get("diagnostics") if isinstance(record.get("diagnostics"), dict) else {},
    }
    raise error


def _filter_plan_for_category(filter_plan: Dict[str, Any], category_filter: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    job_plan = dict(filter_plan)
    job_plan["category_filters"] = [category_filter] if category_filter else []
    return job_plan


def _candidate_scan_limit(config: TagCollectInput) -> int:
    if config.sample_data:
        return config.max_items_per_query
    target = max(config.max_items_per_query, config.target_publishable_count or 0)
    if config.auto_verify_details:
        target = max(target, config.auto_verify_max_items or 0)
    return min(MAX_ITEMS_PER_QUERY, max(1, target * 3))


def _row_has_pending_filter(row: Dict[str, Any]) -> bool:
    return any(
        isinstance(record, dict) and record.get("status") in ("pending_detail", "manual_review_required")
        for record in row.get("filter_match_records", [])
    )


def _row_pending_detail_fields(row: Dict[str, Any], fields: Optional[Iterable[str]] = None) -> List[str]:
    target_fields = fields or DETAIL_ONLY_FIELDS
    return [
        key for key in sorted(target_fields)
        if row.get(key) in ("", DETAIL_VERIFICATION_PENDING, "待品退率核验", None)
    ]


def is_publishable_row(row: Dict[str, Any], *, include_cautious: bool = False) -> bool:
    suggestions = PUBLISHABLE_CANDIDATE_SUGGESTIONS if include_cautious else PUBLISHABLE_SUGGESTIONS
    if row.get("wechat_shop_suggestion") not in suggestions:
        return False
    if row.get("filter_verification_status") == "filtered_out":
        return False
    if row.get("verification_status") in (VERIFICATION_STATUS_FAILED, VERIFICATION_STATUS_FILTERED_OUT):
        return False
    if _row_has_pending_filter(row):
        return False
    return True


def _publishable_rows(rows: List[Dict[str, Any]], *, include_cautious: bool = False) -> List[Dict[str, Any]]:
    return [
        row for row in rows
        if isinstance(row, dict) and is_publishable_row(row, include_cautious=include_cautious)
    ]


def _collection_stop_reason(
    rows: List[Dict[str, Any]],
    *,
    target_publishable_count: int,
    candidate_count: int,
    candidate_scan_limit: int,
) -> str:
    publishable_count = len(_publishable_rows(rows))
    if publishable_count >= target_publishable_count:
        return "target_met"
    if candidate_count >= candidate_scan_limit:
        return "candidate_budget_exhausted"
    return "source_exhausted_or_filters_too_strict"


def _shortfall_reason(
    *,
    target_publishable_count: int,
    publishable_count: int,
    collection_stop_reason: str,
    skipped_rejected_count: int,
) -> str:
    if publishable_count >= target_publishable_count:
        return ""
    shortfall = target_publishable_count - publishable_count
    if collection_stop_reason == "candidate_budget_exhausted":
        base = "候选预算已用完"
    elif collection_stop_reason == "source_exhausted_or_filters_too_strict":
        base = "当前来源候选不足或筛选条件偏严"
    else:
        base = collection_stop_reason or "未达到目标可铺数量"
    skip_text = f"，历史筛除跳过 {skipped_rejected_count} 个" if skipped_rejected_count else ""
    return f"{base}，距离目标可铺数量还差 {shortfall} 个{skip_text}"


def collect_products(config: TagCollectInput) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    filter_plan = build_filter_plan(config)
    manual_page_mode = config.collect_source in MANUAL_PAGE_COLLECT_SOURCES
    direct_url_mode = config.collect_source in DIRECT_URL_COLLECT_SOURCES
    direct_urls = config.source_urls if (
        not config.sample_data
        and config.collect_source in {"rpa", *MANUAL_PAGE_COLLECT_SOURCES, *DIRECT_URL_COLLECT_SOURCES}
    ) else []
    if direct_url_mode and not direct_urls and not config.sample_data:
        error = ServiceError(
            f"{DIRECT_URL_REQUIRED_CODE}: URL 直连读取模式不会自动搜索或点击类目，请先粘贴 1688 商品列表页或详情页 URL。"
        )
        error.data = {"code": DIRECT_URL_REQUIRED_CODE, "source": "url_direct", "cdp": False}
        raise error
    queries = (direct_urls or [""]) if manual_page_mode else (direct_urls or build_queries(config))
    category_filters = filter_plan.get("category_filters", []) or []
    channel = _channel_from_tags(config.tags)
    target_platform = _target_platform_from_tags(config.tags)
    filter_signature = _filter_signature_for_config(config)
    rejected_products = _load_rejected_products() if (config.skip_historical_rejected and not config.sample_data) else _empty_rejected_products()
    candidate_scan_limit = _candidate_scan_limit(config)
    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []
    filter_results: List[Dict[str, Any]] = []
    rejected_records: List[Dict[str, Any]] = []
    skipped_rejected_records: List[Dict[str, Any]] = []
    candidate_count = 0

    for query in queries:
        if config.sample_data:
            products = _sample_products_for_query(query, candidate_scan_limit)
            filter_results.extend(_default_filter_results(filter_plan, source="sample", query=query))
        elif config.collect_source == "api":
            from capabilities.search.service import search_products
            products = search_products(query, channel=channel)[:candidate_scan_limit]
            filter_results.extend(_default_filter_results(filter_plan, source="api", query=query))
        elif direct_url_mode:
            direct_result = collect_products_from_direct_url(query, candidate_scan_limit)
            products = direct_result.get("products", [])
            filter_results.extend(_default_filter_results(filter_plan, source="url_direct", query=query))
        else:
            from capabilities.tag_collect.rpa import collect_products_from_1688_page
            rpa_jobs = []
            if manual_page_mode:
                selected_category = str((category_filters[0] if category_filters else {}).get("category_path") or "").strip()
                rpa_jobs.append((query, query if direct_urls else "", _filter_plan_for_category(filter_plan, None), [], selected_category))
            elif direct_urls:
                rpa_jobs.append((query, query, filter_plan, category_filters, ""))
            elif category_filters:
                for category_filter in category_filters:
                    category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "").strip()
                    rpa_jobs.append((query, "", _filter_plan_for_category(filter_plan, category_filter), [category_filter], category_path))
            else:
                rpa_jobs.append((query, "", filter_plan, [], ""))

            products = []
            for job_query, job_source_url, job_plan, job_category_filters, job_category_path in rpa_jobs:
                rpa_result = collect_products_from_1688_page(
                    "" if job_source_url else job_query,
                    candidate_scan_limit,
                    source_url=job_source_url,
                    native_filters=[] if manual_page_mode else filter_plan["native_filters"],
                    category_filters=job_category_filters,
                    manual_url_only=manual_page_mode,
                    return_meta=True,
                )
                raw_products = rpa_result.get("products", []) if isinstance(rpa_result, dict) else rpa_result
                current_filter_results = _normalize_filter_results(
                    rpa_result.get("filter_results") if isinstance(rpa_result, dict) else [],
                    job_plan,
                    source="rpa",
                    query=job_query,
                )
                filter_results.extend(current_filter_results)
                if not direct_urls and not manual_page_mode:
                    blocking_category = _blocking_category_result(current_filter_results, job_plan)
                    if blocking_category:
                        _raise_category_navigation_failed(blocking_category)
                for item in raw_products if isinstance(raw_products, list) else []:
                    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
                    if job_category_path and job_category_filters and not stats.get("categoryListName"):
                        stats = dict(stats)
                        stats["categoryListName"] = job_category_path
                        stats["sourceCategoryPath"] = job_category_path
                    if str(item.get("id", "")).strip() and _clean_product_title(item.get("title", "")):
                        products.append(Product(
                            id=str(item.get("id", "")),
                            title=_clean_product_title(item.get("title", "")),
                            price=str(item.get("price", "-")),
                            image=str(item.get("image", "")),
                            url=str(item.get("url", "")),
                            stats=stats,
                        ))
        for product in products:
            product_id = _canonical_item_id(product.id, product.url)
            if product_id in seen:
                continue
            seen.add(product_id)
            candidate_count += 1
            historical_rejected = _matched_rejected_record(
                product.id,
                product.url,
                rejected_products,
                filter_signature=filter_signature,
                target_platform=target_platform,
            )
            if historical_rejected:
                skipped_rejected_records.append(_skip_record_from_product(product, historical_rejected))
                continue
            row = product_to_export_row(product, query, config)
            keep_by_filters, post_records = _evaluate_post_filters(row, filter_plan.get("post_filters", []))
            row["filter_match_records"] = post_records
            if not keep_by_filters:
                rejected_records.append(_rejection_record(
                    row,
                    reason_code="post_filter_failed",
                    reason_text="列表字段未满足筛选条件",
                    filter_signature=filter_signature,
                    target_platform=target_platform,
                ))
                continue
            if _excluded_by_tags(row, config.exclude_tags):
                rejected_records.append(_rejection_record(
                    row,
                    reason_code="exclude_tag_matched",
                    reason_text="命中排除标签",
                    filter_signature=filter_signature,
                    target_platform=target_platform,
                ))
                continue
            rows.append(row)
            if len(_publishable_rows(rows)) >= config.target_publishable_count:
                break
        if len(_publishable_rows(rows)) >= config.target_publishable_count:
            break

    ranked = sorted(rows, key=lambda item: item["recommendation_score"], reverse=True)
    for index, row in enumerate(ranked, 1):
        row["seq"] = index
    filter_plan["filter_results"] = filter_results
    filter_plan["filter_warnings"] = [
        record for record in filter_results
        if str(record.get("status") or "") in ("not_found", "click_failed", "ambiguous", "not_reported", "partial_clicked", "not_confirmed")
    ]
    publishable_count = len(_publishable_rows(ranked))
    if rejected_records and not config.sample_data:
        _append_rejected_products(rejected_records)
    filter_plan["target_publishable_count"] = config.target_publishable_count
    filter_plan["publishable_count"] = publishable_count
    filter_plan["strict_publishable_count"] = publishable_count
    filter_plan["publishable_candidate_count"] = len(_publishable_rows(ranked, include_cautious=True))
    filter_plan["candidate_count"] = candidate_count
    filter_plan["candidate_scan_limit"] = candidate_scan_limit
    filter_plan["rejection_records"] = rejected_records
    filter_plan["rejected_count"] = len(rejected_records)
    filter_plan["skipped_rejected_records"] = skipped_rejected_records
    filter_plan["skipped_rejected_count"] = len(skipped_rejected_records)
    filter_plan["persistent_excluded_count"] = len(skipped_rejected_records)
    filter_plan["filter_signature"] = filter_signature
    filter_plan["target_platform"] = target_platform
    filter_plan["collection_stop_reason"] = _collection_stop_reason(
        ranked,
        target_publishable_count=config.target_publishable_count,
        candidate_count=candidate_count,
        candidate_scan_limit=candidate_scan_limit,
    )
    filter_plan["shortfall_reason"] = _shortfall_reason(
        target_publishable_count=config.target_publishable_count,
        publishable_count=publishable_count,
        collection_stop_reason=filter_plan["collection_stop_reason"],
        skipped_rejected_count=len(skipped_rejected_records),
    )
    return ranked, queries, filter_plan


def friendly_collect_error(error: Exception) -> str:
    """把 RPA/真实采集底层异常转成运营可理解的提示。"""
    message = str(error)
    detail = getattr(error, "data", {}) or {}
    use_cdp = bool(detail.get("cdp")) or bool(os.environ.get("TAG_COLLECT_CDP_URL"))
    if f"{SECURITY_BLOCK_CODE}:" in message:
        return message.split(f"{SECURITY_BLOCK_CODE}:", 1)[1].strip()
    if f"{LOGIN_REQUIRED_CODE}:" in message:
        return message.split(f"{LOGIN_REQUIRED_CODE}:", 1)[1].strip()
    if "browser_closed:" in message:
        return message.split("browser_closed:", 1)[1].strip()
    if f"{SEARCH_KEYWORD_ENCODING_CODE}:" in message:
        return message.split(f"{SEARCH_KEYWORD_ENCODING_CODE}:", 1)[1].strip()
    if f"{SEARCH_BOX_NOT_FOUND_CODE}:" in message:
        return message.split(f"{SEARCH_BOX_NOT_FOUND_CODE}:", 1)[1].strip()
    if f"{SEARCH_RESULTS_NOT_LOADED_CODE}:" in message:
        return message.split(f"{SEARCH_RESULTS_NOT_LOADED_CODE}:", 1)[1].strip()
    if f"{CATEGORY_NAVIGATION_NOT_LOADED_CODE}:" in message:
        return message.split(f"{CATEGORY_NAVIGATION_NOT_LOADED_CODE}:", 1)[1].strip()
    if f"{CATEGORY_PATH_INVALID_CODE}:" in message:
        return message.split(f"{CATEGORY_PATH_INVALID_CODE}:", 1)[1].strip()
    if "navigation_timeout:" in message or "页面加载超时" in message:
        return (
            "1688 页面加载超时，未生成数据。请先在真实 Chrome 中人工打开对应搜索页/商品页，"
            "确认不是登录、滑块、访问拒绝或网络卡顿后，再回到本工具查询。"
        )
    if "cdp_context_unsupported:" in message or "Browser.setDownloadBehavior" in message or "Browser context management is not supported" in message:
        return (
            "真实 Chrome 调试端口能连接，但当前 CDP 会话不可用，无法创建采集页面。"
            "通常是调试 Chrome 状态异常、端口被其它工具占用，或 Chrome/Playwright 版本兼容问题。"
        )
    if any(term in message for term in ("拖动下方滑块", "验证失败", "点击框体重试", "error:2eDumg", "安全滑块", "验证码")):
        if use_cdp:
            return (
                "1688 在已连接的真实 Chrome 中触发了安全滑块/验证码校验。"
                "这通常是账号、IP 或当前浏览器环境已被风控，人工拖动仍失败时不要反复刷新。"
                "请先在这个 Chrome 中确认 1688 搜索页能人工正常打开；恢复后再回到本工具查询。"
            )
        return (
            "1688 触发了安全滑块/验证码校验，系统不会绕过或自动破解验证，也不会继续采集以免导出不可信数据。"
            "请在弹出的真实浏览器中手动完成验证，或使用已登录且已通过验证的 Chrome CDP 会话后重试。"
        )
    if "Target page, context or browser has been closed" in message:
        return (
            "真实采集窗口已关闭或登录/验证未完成，未生成任何数据。"
            "请保持弹出的 1688/淘宝登录窗口打开并完成扫码验证后重试；"
            "如果账号仍登录不上，可以粘贴浏览器里能打开的 1688 搜索页或商品详情页 URL 做公开页面真实数据测试。"
        )
    if "真实页面 RPA 返回格式异常" in message:
        return "真实页面 RPA 返回异常，未生成任何数据。请重试一次；如果仍失败，优先使用 1688 页面 URL 模式测试真实页面解析。"
    if f"{DIRECT_URL_REQUIRED_CODE}:" in message:
        return message.split(f"{DIRECT_URL_REQUIRED_CODE}:", 1)[1].strip()
    if "direct_url_fetch_failed:" in message:
        return message.split("direct_url_fetch_failed:", 1)[1].strip()
    return message


def collect_error_state(error: Exception) -> Dict[str, Any]:
    """返回 Web 可识别的采集阻断状态，风控/登录场景不进入普通失败重试。"""
    message = str(error)
    friendly = friendly_collect_error(error)
    detail = getattr(error, "data", {}) or {}
    filter_results = detail.get("filter_results", [])
    if not isinstance(filter_results, list):
        filter_results = []
    diagnostics = detail.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    use_cdp = bool(detail.get("cdp")) or bool(os.environ.get("TAG_COLLECT_CDP_URL"))
    runtime = {
        "use_cdp": use_cdp,
        "cdp_url": os.environ.get("TAG_COLLECT_CDP_URL", ""),
        "cdp_connected": True if use_cdp else False,
        "cdp_ready": True if use_cdp else False,
        "page_url": detail.get("page_url", ""),
        "source": detail.get("source", ""),
    }
    if use_cdp:
        security_suggestion = (
            "当前已连接真实 Chrome，说明不是 testin/临时浏览器问题；请先在这个 Chrome 中人工确认 1688 搜索页可正常访问，"
            "若仍停留在 punish/验证码页，请停止反复刷新 1688，暂停一段时间或更换正常账号/网络后再测。"
        )
    else:
        security_suggestion = (
            "已暂停真实采集。请停止反复刷新 1688，由人工在正常浏览器中完成登录/验证；"
            "验证恢复后，可粘贴已打开的搜索页/商品详情页 URL，或使用已登录 Chrome CDP 会话继续。"
        )
    if f"{SEARCH_KEYWORD_ENCODING_CODE}:" in message or "搜索词校验失败" in message or "疑似乱码" in message:
        return {
            "code": SEARCH_KEYWORD_ENCODING_CODE,
            "action": "manual_open_page",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "系统已停止本次采集，避免按错误关键词导出数据。请在真实 Chrome 里确认 1688 搜索框能人工输入中文；"
                "如果人工搜索正常，再重新查询。也可以粘贴一个人工打开后确认无乱码的 1688 搜索页 URL 进行真实页面解析。"
            ),
            "runtime": runtime,
        }
    if f"{DIRECT_URL_REQUIRED_CODE}:" in message:
        return {
            "code": DIRECT_URL_REQUIRED_CODE,
            "action": "paste_url",
            "retryable": False,
            "message": friendly,
            "suggestion": "请在“1688 页面 URL”输入框粘贴一个你能打开的 1688 搜索页、类目页或商品详情页链接，再点击查询。",
            "runtime": runtime,
        }
    if f"{CATEGORY_PATH_INVALID_CODE}:" in message:
        return {
            "code": CATEGORY_PATH_INVALID_CODE,
            "action": "select_valid_category",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "请只从本页 1688 类目树选择类目。当前项目不会把不存在的类目路径改成搜索框关键词，"
                "也不会自动扩大到父类后继续导出，避免出现男士内裤采成内衣这类错配。"
            ),
            "runtime": runtime,
            "invalid_categories": detail.get("invalid_categories", []),
            "category_dictionary": detail.get("category_dictionary", {}),
        }
    if "direct_url_fetch_failed:" in message:
        return {
            "code": "direct_url_fetch_failed",
            "action": "check_url",
            "retryable": True,
            "message": friendly,
            "suggestion": "请确认 URL 可以在浏览器中打开；如果浏览器能打开但直连失败，说明该页面需要浏览器上下文，请改用人工页面读取。",
            "runtime": runtime,
        }
    if f"{SEARCH_BOX_NOT_FOUND_CODE}:" in message:
        return {
            "code": SEARCH_BOX_NOT_FOUND_CODE,
            "action": "manual_open_page",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "系统没有在当前 1688 页面找到可输入的搜索框。请在真实 Chrome 中人工打开 1688 首页或搜索页，"
                "确认搜索框可见且能输入中文后再查询；也可以直接粘贴已经打开的 1688 搜索页 URL。"
            ),
            "runtime": runtime,
        }
    if f"{SEARCH_RESULTS_NOT_LOADED_CODE}:" in message or "未在页面中发现商品列表链接" in message:
        return {
            "code": SEARCH_RESULTS_NOT_LOADED_CODE,
            "action": "manual_open_page",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "1688 搜索词已进入页面，但当前页没有加载出可解析的商品卡片。请在真实 Chrome 中人工确认能看到商品列表；"
                "如果页面停在搜索建议、图搜提示、空白或需要登录/验证，请处理后再查询。"
            ),
            "runtime": runtime,
        }
    if f"{CATEGORY_NAVIGATION_NOT_LOADED_CODE}:" in message or "未进入可解析的商品列表页" in message:
        return {
            "code": CATEGORY_NAVIGATION_NOT_LOADED_CODE,
            "action": "manual_open_page",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "系统已尝试按类目入口进入 1688 商品结果页，但没有成功进入可解析商品列表。"
                "这通常是 1688 首页类目入口结构变化、该类目在当前页面没有直达入口，或页面仍停留在登录/验证/首页菜单。"
                "请先看下方筛选执行记录里的命中层级和可见类目文本；如果 1688 页面确实没有该入口，需修正本地类目字典，"
                "或在采集 Chrome 中人工进入对应类目后切到“当前页兜底读取”，但兜底读取不作为自动类目命中验收。"
            ),
            "runtime": runtime,
            "filter_results": filter_results,
            "diagnostics": diagnostics,
            "category_path": detail.get("category_path", ""),
        }
    if f"{SECURITY_BLOCK_CODE}:" in message or any(
        term in message
        for term in ("拖动下方滑块", "验证失败", "点击框体重试", "error:", "安全滑块", "验证码", "访问被拒绝", "访问受限")
    ):
        return {
            "code": SECURITY_BLOCK_CODE,
            "action": "manual_handoff",
            "retryable": False,
            "message": friendly,
            "suggestion": security_suggestion,
            "runtime": runtime,
        }
    if "navigation_timeout:" in message or "页面加载超时" in message:
        return {
            "code": "navigation_timeout",
            "action": "manual_open_page",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "当前真实 Chrome 已连接但 1688 页面没有在限定时间内加载完成。"
                "请先在该 Chrome 中人工打开 1688 搜索页/详情页，确认能正常看到商品列表后再查询。"
                if use_cdp
                else "请先在浏览器中人工打开 1688 搜索页/详情页确认可访问，再重试。"
            ),
            "runtime": runtime,
        }
    if "cdp_context_unsupported:" in message or "Browser.setDownloadBehavior" in message or "Browser context management is not supported" in message:
        return {
            "code": "cdp_context_unsupported",
            "action": "restart_chrome_debug",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "这不是 1688 页面本身报错，也不一定是窗口 tab 被关。请先完全关闭由项目启动的 Chrome 调试窗口，"
                "确认 9222 没有被其它 Chrome/工具占用，然后重新运行 scripts/capabilities/tag_collect/start_chrome_debug.sh，"
                "再重启本地工作台。"
            ),
            "runtime": runtime,
        }
    if f"{LOGIN_REQUIRED_CODE}:" in message:
        return {
            "code": LOGIN_REQUIRED_CODE,
            "action": "manual_login",
            "retryable": False,
            "message": friendly,
            "suggestion": (
                "当前已连接真实 Chrome，请在这个 Chrome 窗口内完成 1688 登录，并确认搜索页可以人工正常打开后再查询。"
                if use_cdp
                else "请人工扫码登录并确认页面能正常访问后，再从 URL 或已登录浏览器会话继续采集。"
            ),
            "runtime": runtime,
        }
    return {
        "code": "collect_failed",
        "action": "review_error",
        "retryable": True,
        "message": friendly,
        "suggestion": "请检查筛选条件、URL 或采集来源后再重试。",
        "runtime": runtime,
    }


def _excluded_by_tags(row: Dict[str, Any], exclude_tags: List[str]) -> bool:
    if not exclude_tags:
        return False
    haystack = " ".join(
        str(row.get(key, ""))
        for key in (
            "title",
            "category_path",
            "risk_flags",
            "brand_ip_risk",
            "low_price_quality_risk",
            "after_sales_risk",
            "data_gap_risk",
            "matched_tags",
        )
    )
    return any(tag and tag in haystack for tag in exclude_tags)


def _fmt_rate(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f * 100:.1f}%" if f <= 1 else f"{f:.1f}%"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_number(value: Any) -> Optional[float]:
    if value in (None, "", DETAIL_VERIFICATION_PENDING, "待品退率核验"):
        return None
    text = str(value).replace(",", "").replace("，", "").replace("￥", "").replace("¥", "").strip()
    if "万" in text:
        multiplier = 10000.0
        text = text.replace("万", "")
    else:
        multiplier = 1.0
    is_percent = "%" in text
    text = text.replace("%", "")
    import re
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0)) * multiplier
    except ValueError:
        return None
    if is_percent:
        return number
    return number * 100 if 0 < number <= 1 and any(key in str(value) for key in ("0.", ".0")) else number


def metric_bucket(field_key: str, value: Any) -> str:
    number = _parse_number(value)
    if number is None:
        return DETAIL_VERIFICATION_PENDING
    if field_key in ("good_rate", "repurchase_rate", "shipment_rate"):
        if number >= 90 and field_key == "good_rate":
            return ">=90%"
        if field_key == "good_rate" and number >= 80:
            return "80%-90%"
        if field_key == "good_rate" and number >= 70:
            return "70%-80%"
        if field_key == "good_rate":
            return "<70%"
        if field_key == "shipment_rate":
            if number >= 95:
                return ">=95%"
            if number >= 90:
                return "90%-95%"
            if number >= 80:
                return "80%-90%"
            return "<80%"
        if number >= 30:
            return ">=30%"
        if number >= 20:
            return "20%-30%"
        if number >= 10:
            return "10%-20%"
        return "<10%"
    if field_key == "product_refund_rate":
        if number < 2:
            return "<2%"
        if number < 5:
            return "2%-5%"
        if number < 10:
            return "5%-10%"
        return ">=10%"
    if field_key == "comment_count":
        if number >= 1000:
            return ">=1000"
        if number >= 300:
            return "300-999"
        if number >= 100:
            return "100-299"
        if number >= 30:
            return "30-99"
        return "<30"
    if field_key in ("units_30d", "monthly_dropship_orders"):
        if number >= 5000 and field_key == "units_30d":
            return ">=5000"
        if field_key == "units_30d" and number >= 1000:
            return "1000-4999"
        if field_key == "units_30d" and number >= 500:
            return "500-999"
        if field_key == "units_30d" and number >= 100:
            return "100-499"
        if field_key == "units_30d":
            return "<100"
        if number >= 1000:
            return ">=1000"
        if number >= 500:
            return "500-999"
        if number >= 100:
            return "100-499"
        return "<100"
    return ""


def _match_metric_rule(value: Any, rule: Dict[str, Any]) -> Optional[bool]:
    number = _parse_number(value)
    if number is None:
        return None
    op = rule.get("op")
    if op == ">=":
        return number >= float(rule.get("value", 0))
    if op == "<":
        return number < float(rule.get("value", 0))
    if op == "<=":
        return number <= float(rule.get("value", 0))
    if op == "range":
        return float(rule.get("min", 0)) <= number < float(rule.get("max", 0))
    return None


def _detail_field_verified(row: Dict[str, Any], field_key: str) -> bool:
    if field_key in DETAIL_ONLY_FIELDS:
        return row.get(field_key) not in ("", DETAIL_VERIFICATION_PENDING, "待品退率核验", None)
    if field_key in DETAIL_VERIFICATION_FIELDS:
        verified_fields = row.get("detail_verified_fields")
        if isinstance(verified_fields, list) and field_key in verified_fields:
            return True
        return False
    return True


def _evaluate_post_filters(row: Dict[str, Any], post_filters: List[Dict[str, Any]]) -> Tuple[bool, List[Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []
    should_keep = True
    for rule in post_filters:
        field_key = str(rule.get("field") or "")
        if not field_key:
            continue
        raw_value = row.get(field_key, "")
        needs_verified_detail = (
            rule.get("status") == "detail_required"
            and not _detail_field_verified(row, field_key)
            and field_key in DETAIL_VERIFICATION_FIELDS
        )
        if needs_verified_detail:
            matched = None
            status = "pending_detail"
        elif rule.get("op") == "contains":
            expected = str(rule.get("expected") or rule.get("bucket") or "").strip()
            if raw_value in ("", DETAIL_VERIFICATION_PENDING, None):
                matched = None
                status = "pending_detail"
            else:
                matched = expected in str(raw_value)
                status = "matched" if matched else "filtered_out"
                if not matched:
                    should_keep = False
        elif rule.get("op"):
            matched = _match_metric_rule(raw_value, rule)
            if matched is None:
                status = "pending_detail" if field_key in DETAIL_VERIFICATION_FIELDS else "manual_review_required"
            elif matched:
                status = "matched"
            else:
                status = "filtered_out"
                should_keep = False
        else:
            matched = None
            status = "pending_detail" if field_key in DETAIL_VERIFICATION_FIELDS else str(rule.get("status") or "manual_review_required")
        records.append({
            "tag": rule.get("tag", ""),
            "field_key": field_key,
            "field_label": _field_label(field_key),
            "expected": rule.get("bucket", rule.get("label", "")),
            "raw": raw_value,
            "bucket": metric_bucket(field_key, raw_value),
            "status": status,
            "message": "字段需详情页核验后判断" if status == "pending_detail" else "",
        })
    return should_keep, records


def reevaluate_row_filters_after_verification(row: Dict[str, Any], filter_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """详情页核验后，重新判断依赖详情字段的筛选命中状态。"""
    if not isinstance(filter_plan, dict):
        return []
    post_filters = filter_plan.get("post_filters") or []
    if not isinstance(post_filters, list):
        return []
    _, records = _evaluate_post_filters(row, post_filters)
    row["filter_match_records"] = records
    filtered = [record for record in records if record.get("status") == "filtered_out"]
    blocking = [
        record for record in records
        if record.get("status") in ("pending_detail", "manual_review_required")
    ]
    if filtered:
        row["filter_verification_status"] = "filtered_out"
        row["filter_verification_note"] = "；".join(
            f"{record.get('field_label') or record.get('field_key')}未满足{record.get('expected') or ''}"
            for record in filtered
        )
    elif blocking:
        row["filter_verification_status"] = "needs_review"
        row["filter_verification_note"] = "；".join(
            f"{record.get('field_label') or record.get('field_key')}={record.get('status')}"
            for record in blocking
        )
    else:
        row["filter_verification_status"] = "matched"
        row["filter_verification_note"] = "详情相关筛选已重新评估并命中"
    return records


def _refresh_metric_buckets(row: Dict[str, Any]) -> None:
    row["good_rate_bucket"] = metric_bucket("good_rate", row.get("good_rate", ""))
    row["product_refund_rate_bucket"] = metric_bucket("product_refund_rate", row.get("product_refund_rate", ""))
    row["shipment_rate_bucket"] = metric_bucket("shipment_rate", row.get("shipment_rate", ""))
    row["comment_count_bucket"] = metric_bucket("comment_count", row.get("comment_count", ""))
    row["repurchase_rate_bucket"] = metric_bucket("repurchase_rate", row.get("repurchase_rate", ""))
    row["units_30d_bucket"] = metric_bucket("units_30d", row.get("units_30d", ""))
    row["monthly_dropship_orders_bucket"] = metric_bucket("monthly_dropship_orders", row.get("monthly_dropship_orders", ""))


def _apply_detail_filter_exclusions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return []
    kept_rows: List[Dict[str, Any]] = []
    newly_excluded: List[Dict[str, Any]] = []
    existing_excluded = [
        row for row in payload.get("filter_excluded_rows", [])
        if isinstance(row, dict)
    ]
    existing_ids = {str(row.get("item_id", "")) for row in existing_excluded}
    filter_signature = str(payload.get("filter_signature") or _filter_signature_for_payload(payload))
    target_platform = str(payload.get("target_platform") or _target_platform_from_tags(payload.get("tags") or []))
    persistent_records: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("filter_verification_status") == "filtered_out":
            excluded = dict(row)
            excluded["verification_status"] = VERIFICATION_STATUS_FILTERED_OUT
            excluded["data_gap_risk"] = "详情核验后未满足筛选条件"
            excluded["manual_review_status"] = "系统剔除"
            excluded["manual_wechat_shop_suggestion"] = "不建议"
            if str(excluded.get("item_id", "")) not in existing_ids:
                newly_excluded.append(excluded)
                existing_ids.add(str(excluded.get("item_id", "")))
            if not payload.get("sample_data"):
                persistent_records.append(_rejection_record(
                    excluded,
                    reason_code="detail_filter_failed",
                    reason_text=excluded.get("filter_verification_note") or "详情核验后未满足筛选条件",
                    filter_signature=filter_signature,
                    target_platform=target_platform,
                    source_run_id=str(payload.get("run_id", "")),
                ))
            continue
        kept_rows.append(row)
    for index, row in enumerate(kept_rows, 1):
        row["seq"] = index
    payload["rows"] = kept_rows
    payload["row_count"] = len(kept_rows)
    payload["filter_excluded_rows"] = [*existing_excluded, *newly_excluded]
    payload["filter_excluded_count"] = len(payload["filter_excluded_rows"])
    if persistent_records:
        _append_rejected_products(persistent_records)
        payload["rejection_records"] = [
            *(payload.get("rejection_records") or []),
            *persistent_records,
        ]
    return newly_excluded


def _persist_non_publishable_rejections(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if payload.get("sample_data"):
        return []
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return []
    filter_signature = str(payload.get("filter_signature") or _filter_signature_for_payload(payload))
    target_platform = str(payload.get("target_platform") or _target_platform_from_tags(payload.get("tags") or []))
    records: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("wechat_shop_suggestion") != "不建议":
            continue
        if row.get("verification_status") not in (VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL):
            continue
        if _row_has_pending_filter(row):
            continue
        records.append(_rejection_record(
            row,
            reason_code="wechat_not_recommended",
            reason_text=row.get("risk_flags") or row.get("recommendation_reason") or "详情核验后系统判断不适合微信小店投放",
            filter_signature=filter_signature,
            target_platform=target_platform,
            source_run_id=str(payload.get("run_id", "")),
        ))
    if records:
        _append_rejected_products(records)
        payload["rejection_records"] = [
            *(payload.get("rejection_records") or []),
            *records,
        ]
        payload["rejected_count"] = len(payload["rejection_records"])
    return records


def _score_product(stats: Dict[str, Any], tags: List[str]) -> Tuple[int, List[str], List[str]]:
    score = 50
    reasons: List[str] = []
    risks: List[str] = []

    sales_30d = _safe_int(stats.get("last30DaysSales"))
    dropship_sales = _safe_int(stats.get("last30DaysDropShippingSales"))
    downstream = _safe_int(stats.get("downstreamOffer"))
    remark_cnt = _safe_int(stats.get("remarkCnt"))
    good_rate = float(stats.get("goodRates") or 0)
    repurchase = float(stats.get("repurchaseRate") or 0)
    collection = float(stats.get("collectionRate24h") or 0)

    if sales_30d >= 1000:
        score += 15
        reasons.append(f"近30天件数{sales_30d}，热度较高")
    elif sales_30d >= 500:
        score += 10
        reasons.append(f"近30天件数{sales_30d}，具备基础热度")
    elif sales_30d < 100:
        score -= 10
        risks.append("近30天销量偏低")

    if dropship_sales >= 500:
        score += 10
        reasons.append(f"近30天代发销量{dropship_sales}，代发活跃")

    if good_rate >= 0.9:
        score += 10
        reasons.append(f"好评率{_fmt_rate(good_rate)}")
    elif good_rate and good_rate < 0.85:
        score -= 15
        risks.append(f"好评率{_fmt_rate(good_rate)}偏低")

    if repurchase >= 0.1:
        score += 8
        reasons.append(f"复购率{_fmt_rate(repurchase)}")

    if collection >= 0.9:
        score += 8
        reasons.append(f"24小时揽收率{_fmt_rate(collection)}")
    elif collection and collection < 0.8:
        score -= 12
        risks.append(f"24小时揽收率{_fmt_rate(collection)}偏低")

    if downstream and downstream < 200:
        score += 8
        reasons.append(f"下游铺货数{downstream}，竞争相对低")
    elif downstream > 500:
        score -= 10
        risks.append(f"下游铺货数{downstream}，疑似红海")

    if remark_cnt and remark_cnt < 30:
        score -= 8
        risks.append("评论数不足，样本偏小")

    if "微信小店" in tags:
        score += 3
        reasons.append("已按微信小店目标保留复核列")

    score = max(0, min(100, score))
    return score, reasons, risks


def _level(score: int) -> str:
    if score >= 80:
        return "P0"
    if score >= 65:
        return "P1"
    if score >= 50:
        return "P2"
    return "不建议"


def _wechat_suggestion(row: Dict[str, Any], risks: List[str]) -> str:
    if row["recommendation_score"] >= 75 and len(risks) <= 1:
        return "可铺"
    if row["recommendation_score"] >= 55:
        return "谨慎"
    return "不建议"


def _split_risks(value: Any) -> List[str]:
    return [part.strip() for part in str(value or "").split("；") if part.strip()]


def _auto_review_blocking_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if row.get("filter_verification_status") == "filtered_out":
        reasons.append("详情筛选未通过")
    if _row_has_pending_filter(row) or row.get("filter_verification_status") == "needs_review":
        reasons.append("筛选规则待详情确认")
    if row.get("verification_status") == VERIFICATION_STATUS_FAILED:
        reasons.append(row.get("verification_fail_reason") or "详情核验失败")
    if row.get("verification_status") == VERIFICATION_STATUS_FILTERED_OUT:
        reasons.append("已被详情筛选剔除")
    if row.get("verification_status") not in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL):
        reasons.append("关键字段尚未完成详情核验")
    verified_fields = set(row.get("detail_verified_fields") or [])
    missing_evidence = [
        key for key in sorted(AUTO_REVIEW_REQUIRED_DETAIL_FIELDS)
        if key not in verified_fields
    ]
    if missing_evidence:
        reasons.append("关键字段缺少详情核验证据：" + "、".join(_field_label(key) for key in missing_evidence))
    if _has_blocking_detail_risk(row):
        reasons.append("品退率或发货率存在履约/售后风险")
    for risk in _split_risks(row.get("risk_flags")):
        if any(token in risk for token in ("过高", "过低", "偏低", "偏高", "红海", "不足", "未确认", "退换支持弱")):
            reasons.append(risk)
    return list(dict.fromkeys([reason for reason in reasons if reason]))


def apply_auto_review_status(row: Dict[str, Any]) -> Dict[str, Any]:
    suggestion = str(row.get("wechat_shop_suggestion") or "")
    pending_core_fields = _row_pending_detail_fields(row, AUTO_REVIEW_REQUIRED_DETAIL_FIELDS)
    blocking_reasons = _auto_review_blocking_reasons(row)
    if row.get("filter_verification_status") == "filtered_out":
        row["manual_review_status"] = "系统剔除"
        row["manual_wechat_shop_suggestion"] = "不建议"
        row["manual_review_note"] = row.get("filter_verification_note") or "详情核验后未满足筛选条件"
    elif suggestion == "不建议":
        row["manual_review_status"] = "系统不建议"
        row["manual_wechat_shop_suggestion"] = "不建议"
        row["manual_review_note"] = row.get("risk_flags") or row.get("after_sales_risk") or row.get("recommendation_reason") or "系统规则判断不适合投放"
    elif suggestion == "可铺" and not pending_core_fields and not blocking_reasons:
        row["manual_review_status"] = "系统预通过"
        row["manual_wechat_shop_suggestion"] = "可铺"
        row["manual_review_note"] = "关键履约/售后字段已核验，未命中阻断风险"
    elif suggestion == "谨慎":
        row["manual_review_status"] = "待复核"
        row["manual_wechat_shop_suggestion"] = "谨慎"
        row["manual_review_note"] = "系统判断为谨慎，需人工确认是否铺货"
    else:
        row["manual_review_status"] = "待复核"
        row["manual_wechat_shop_suggestion"] = suggestion
        if pending_core_fields:
            row["manual_review_note"] = "关键字段待详情页核验：" + "、".join(_field_label(key) for key in pending_core_fields)
        elif blocking_reasons:
            row["manual_review_note"] = "；".join(blocking_reasons)
        else:
            row["manual_review_note"] = row.get("manual_review_note") or "系统规则暂无法自动确认"
    return row


def apply_auto_review_statuses(rows: Iterable[Dict[str, Any]]) -> None:
    for row in rows:
        if isinstance(row, dict):
            apply_auto_review_status(row)


def _manual_review_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    summary = {
        "system_preapproved": 0,
        "system_not_recommended": 0,
        "system_excluded": 0,
        "manual_pending": 0,
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = row.get("manual_review_status")
        if status == "系统预通过":
            summary["system_preapproved"] += 1
        elif status == "系统不建议":
            summary["system_not_recommended"] += 1
        elif status == "系统剔除":
            summary["system_excluded"] += 1
        elif status == "待复核":
            summary["manual_pending"] += 1
    return summary


def _row_needs_exception_review(row: Dict[str, Any]) -> bool:
    if row.get("manual_review_status") == "待复核":
        return True
    if row.get("verification_status") in ("verify_failed", "failed", VERIFICATION_STATUS_FILTERED_OUT):
        return True
    if row.get("filter_verification_status") == "filtered_out":
        return True
    data_gap = str(row.get("data_gap_risk") or "")
    return bool(data_gap and data_gap != "关键字段待详情页核验")


def _detail_score_adjustments(row: Dict[str, Any]) -> Tuple[int, List[str], List[str]]:
    delta = 0
    reasons: List[str] = []
    risks: List[str] = []
    refund_rate = _parse_number(row.get("product_refund_rate"))
    shipment_rate = _parse_number(row.get("shipment_rate"))
    product_rating = _parse_number(row.get("product_rating"))
    monthly_dropship_orders = _parse_number(row.get("monthly_dropship_orders"))

    if refund_rate is not None:
        if refund_rate < 2:
            delta += 8
            reasons.append(f"品退率{row.get('product_refund_rate')}较低")
        elif refund_rate < 5:
            delta += 3
            reasons.append(f"品退率{row.get('product_refund_rate')}可接受")
        elif refund_rate >= 10:
            delta -= 25
            risks.append(f"品退率{row.get('product_refund_rate')}过高")
        else:
            delta -= 15
            risks.append(f"品退率{row.get('product_refund_rate')}偏高")

    if shipment_rate is not None:
        if shipment_rate >= 98:
            delta += 8
            reasons.append(f"发货率{row.get('shipment_rate')}较稳")
        elif shipment_rate >= 95:
            delta += 3
            reasons.append(f"发货率{row.get('shipment_rate')}达标")
        elif shipment_rate < 90:
            delta -= 25
            risks.append(f"发货率{row.get('shipment_rate')}过低")
        else:
            delta -= 12
            risks.append(f"发货率{row.get('shipment_rate')}偏低")

    if product_rating is not None:
        if product_rating >= 4.8:
            delta += 5
            reasons.append(f"商品星级{row.get('product_rating')}较高")
        elif product_rating < 4.5:
            delta -= 10
            risks.append(f"商品星级{row.get('product_rating')}偏低")

    if monthly_dropship_orders is not None:
        if monthly_dropship_orders >= 500:
            delta += 5
            reasons.append(f"月代发订单{row.get('monthly_dropship_orders')}较活跃")
        elif monthly_dropship_orders < 100:
            delta -= 5
            risks.append("月代发订单偏低")

    if "是" in str(row.get("supports_dropship") or ""):
        delta += 3
        reasons.append("详情确认支持一件代发")
    elif row.get("supports_dropship") not in ("", DETAIL_VERIFICATION_PENDING, None):
        delta -= 12
        risks.append("详情未确认一件代发")

    if any(token in str(row.get("return_exchange_support") or "") for token in ("不支持", "无退", "不退")):
        delta -= 10
        risks.append("退换支持弱")
    elif row.get("return_exchange_support") not in ("", DETAIL_VERIFICATION_PENDING, None):
        delta += 2
        reasons.append("详情确认退换支持")

    return delta, reasons, risks


def rescore_row_after_detail(row: Dict[str, Any]) -> Dict[str, Any]:
    before_score = int(row.get("list_recommendation_score") or row.get("recommendation_score") or 0)
    before_level = row.get("recommendation_level", "")
    before_suggestion = row.get("wechat_shop_suggestion", "")
    delta, reasons, risks = _detail_score_adjustments(row)
    after_score = max(0, min(100, before_score + delta))
    merged_risks = list(dict.fromkeys([*_split_risks(row.get("risk_flags")), *risks]))
    row["recommendation_score"] = after_score
    row["recommendation_level"] = _level(after_score)
    row["risk_flags"] = "；".join(merged_risks)
    if risks:
        row["after_sales_risk"] = "；".join(risks)
    row["wechat_shop_suggestion"] = _wechat_suggestion(row, merged_risks)
    if reasons or risks:
        row["recommendation_reason"] = "；".join(
            part for part in [
                str(row.get("recommendation_reason") or ""),
                *(reasons + risks),
            ]
            if part
        )
    apply_auto_review_status(row)
    return {
        "item_id": row.get("item_id", ""),
        "title": row.get("title", ""),
        "score_before": before_score,
        "score_after": after_score,
        "score_delta": after_score - before_score,
        "level_before": before_level,
        "level_after": row.get("recommendation_level", ""),
        "wechat_suggestion_before": before_suggestion,
        "wechat_suggestion_after": row.get("wechat_shop_suggestion", ""),
        "reasons": reasons,
        "risks": risks,
    }


def _price_band(price: str) -> str:
    try:
        value = float(str(price).replace("¥", ""))
    except (TypeError, ValueError):
        return ""
    if value < 10:
        return "10元内"
    if value < 30:
        return "10-30元"
    if value < 50:
        return "30-50元"
    if value < 100:
        return "50-100元"
    if value < 200:
        return "100-200元"
    return "200元以上"


def _market_competition(stats: Dict[str, Any]) -> str:
    downstream = _safe_int(stats.get("downstreamOffer"))
    if downstream and downstream < 200:
        return "偏蓝海"
    if downstream > 500:
        return "偏红海"
    return "待观察"


def _looks_like_mojibake(value: Any) -> bool:
    text = str(value or "")
    if not text:
        return False
    return bool(re.search(r"[ÃÂ�]|(?:\\u00[0-9a-fA-F]{2})", text)) or (
        bool(re.search(r"[åæçèéäöü]", text, re.I))
        and not bool(re.search(r"[\u4e00-\u9fff]", text))
        and len(text) > 8
    )


def _clean_product_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^(找相似|进店|立即订购|加入进货单|收藏|¥|￥)\s*", "", text).strip()
    if not text or _looks_like_mojibake(text):
        return ""
    candidates = [
        re.sub(r"^(找相似|进店|立即订购|加入进货单|收藏|¥|￥)\s*", "", item).strip()
        for item in re.split(r"[\n\r|｜]+", text)
        if item.strip()
    ] or [text]
    for item in candidates:
        if len(item) < 4 or len(item) > 140:
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", item):
            continue
        if re.match(r"^(¥|￥)?\d+(?:\.\d+)?(?:元|起)?$", item):
            continue
        if re.search(r"(成交|评价|回头率|复购率|发货|物流|包邮|起批|付款|买家保障|找相似|进店|立即订购)", item[:30]):
            continue
        if _looks_like_mojibake(item):
            continue
        return item[:120]
    return ""


def _default_platform_suggestion(target: str, tags: List[str]) -> str:
    return "待人工复核" if target in tags else ""


def product_to_export_row(product: Product, query: str, config: TagCollectInput) -> Dict[str, Any]:
    stats = product.stats or {}
    title = _clean_product_title(product.title)
    score, reasons, risks = _score_product(stats, config.tags)
    category_path = (
        stats.get("categoryListName")
        or stats.get("categoryName")
        or _match_category_from_config(config.categories, title)
    )
    row: Dict[str, Any] = {
        "seq": 0,
        "run_id": "",
        "category_path": category_path,
        "image_cell": "",
        "title": title,
        "item_id": product.id,
        "url": product.url,
        "listed_at": stats.get("earliestListingTime", ""),
        "wholesale_price": product.price,
        "min_order_range": DETAIL_VERIFICATION_PENDING,
        "free_shipping": DETAIL_VERIFICATION_PENDING,
        "normalized_price_band": _price_band(product.price),
        "orders_30d": stats.get("totalOrder", ""),
        "units_30d": stats.get("last30DaysSales", ""),
        "sales_amount_30d": _estimate_sales_amount(product.price, stats.get("last30DaysSales")),
        "sales_trend_units": f"90天:{stats.get('totalSales')}" if stats.get("totalSales") else "",
        "downstream_offer_count": stats.get("downstreamOffer", ""),
        "similar_item_count": "后续扩展",
        "market_competition": _market_competition(stats),
        "repurchase_rate": _fmt_rate(stats.get("repurchaseRate")),
        "good_rate": _fmt_rate(stats.get("goodRates")),
        "product_rating": DETAIL_VERIFICATION_PENDING,
        "review_tags": DETAIL_VERIFICATION_PENDING,
        "product_refund_rate": DETAIL_VERIFICATION_PENDING,
        "dropship_price": DETAIL_VERIFICATION_PENDING,
        "rights_protection": DETAIL_VERIFICATION_PENDING,
        "dropship_rights": DETAIL_VERIFICATION_PENDING,
        "waybill_support": DETAIL_VERIFICATION_PENDING,
        "collection_rate_24h": DETAIL_VERIFICATION_PENDING,
        "shipment_rate": DETAIL_VERIFICATION_PENDING,
        "shipment_speed": DETAIL_VERIFICATION_PENDING,
        "supports_dropship": DETAIL_VERIFICATION_PENDING,
        "return_exchange_support": DETAIL_VERIFICATION_PENDING,
        "product_mark": "",
        "certificates": "",
        "shop_name": DETAIL_VERIFICATION_PENDING,
        "location": DETAIL_VERIFICATION_PENDING,
        "service_score": "",
        "company_type": DETAIL_VERIFICATION_PENDING,
        "seller_member_type": DETAIL_VERIFICATION_PENDING,
        "seller_services": "",
        "trustpass_years": "",
        "shop_url": "",
        "source_factory": DETAIL_VERIFICATION_PENDING,
        "image_url": product.image,
        "monthly_dropship_orders": DETAIL_VERIFICATION_PENDING,
        "sku_count": DETAIL_VERIFICATION_PENDING,
        "favorite_customers": DETAIL_VERIFICATION_PENDING,
        "comment_count": stats.get("remarkCnt", ""),
        "stock": DETAIL_VERIFICATION_PENDING,
        "video_query": DETAIL_VERIFICATION_PENDING,
        "wholesale_shipping_fee": DETAIL_VERIFICATION_PENDING,
        "dropship_shipping_fee": DETAIL_VERIFICATION_PENDING,
        "douyin_suggestion": _default_platform_suggestion("抖店", config.tags),
        "pinduoduo_suggestion": _default_platform_suggestion("拼多多", config.tags),
        "xiaohongshu_suggestion": _default_platform_suggestion("小红书", config.tags),
        "taobao_suggestion": _default_platform_suggestion("淘宝", config.tags),
        "recommended_platform": "微信小店" if "微信小店" in config.tags else (config.tags[0] if config.tags else ""),
        "brand_ip_risk": "待规则核验",
        "low_price_quality_risk": "低价需复核" if _price_band(product.price) == "10元内" else "",
        "after_sales_risk": "待品退率核验",
        "data_gap_risk": "",
        "source_keyword": query,
        "matched_tags": ",".join(config.tags + config.categories),
        "tag_source": ",".join(config.tags + config.categories),
        "list_source": "sample" if config.sample_data else config.collect_source,
        "list_recommendation_score": score,
        "recommendation_score": score,
        "recommendation_level": _level(score),
        "recommendation_reason": "；".join(reasons) or "待人工复核",
        "risk_flags": "；".join(risks),
        "verification_status": "unverified",
        "manual_review_status": "待复核",
        "manual_review_note": "",
        "wechat_shop_suggestion": "",
        "manual_wechat_shop_suggestion": "",
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    }
    for key in DETAIL_ONLY_FIELDS:
        row[key] = DETAIL_VERIFICATION_PENDING
    row["wechat_shop_suggestion"] = _wechat_suggestion(row, risks)
    if any(row.get(key) in ("", DETAIL_VERIFICATION_PENDING, "待品退率核验") for key in DETAIL_ONLY_FIELDS):
        row["data_gap_risk"] = "关键字段待详情页核验"
    _refresh_metric_buckets(row)
    apply_auto_review_status(row)
    return row


def _match_category_from_config(categories: List[str], title: str) -> str:
    for category in categories:
        if category and category in title:
            return category
    return categories[0] if categories else ""


def _estimate_sales_amount(price: str, units: Any) -> str:
    try:
        value = float(str(price).replace("¥", "")) * _safe_int(units)
    except (TypeError, ValueError):
        return ""
    return f"￥{value:.2f}"


def _waybill_support(tags: List[str]) -> str:
    support = []
    if "微信小店" in tags:
        support.append("微信小店")
    for label in ("淘宝", "抖店", "拼多多", "小红书"):
        if label in tags:
            support.append(label)
    return ",".join(support) if support else DETAIL_VERIFICATION_PENDING


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _field_label(key: str) -> str:
    for field in EXPORT_FIELD_DEFINITIONS:
        if field["key"] == key:
            return field["label"]
    return key


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_verification_evidence(
    item_id: str,
    fields: Dict[str, Any],
    *,
    source: str,
    source_url: str,
    mode: str,
    status: str,
    fail_reason: str = "",
) -> List[Dict[str, Any]]:
    verified_at = _now_iso()
    records: List[Dict[str, Any]] = []
    for key in sorted(DETAIL_VERIFICATION_FIELDS):
        raw_value = fields.get(key, DETAIL_VERIFICATION_PENDING)
        field_status = status if raw_value not in ("", None, DETAIL_VERIFICATION_PENDING) else VERIFICATION_STATUS_FAILED
        records.append({
            "item_id": item_id,
            "field_key": key,
            "field_label": _field_label(key),
            "raw": raw_value,
            "normalized": raw_value,
            "source": source,
            "source_url": source_url,
            "mode": mode,
            "status": field_status,
            "verified_at": verified_at,
            "fail_reason": "" if field_status != VERIFICATION_STATUS_FAILED else (
                fail_reason or ("样例详情数据缺失" if mode == "sample" else "真实详情页未提取到该字段")
            ),
        })
    return records


def _queue_reason(row: Dict[str, Any]) -> str:
    reasons = []
    if row.get("recommendation_level") in DETAIL_VERIFICATION_LEVELS:
        reasons.append(f"{row.get('recommendation_level')} 高潜商品")
    if row.get("list_source") in ("rpa", "manual_url", "url_direct"):
        reasons.append("真实页面候选商品")
    if row.get("wechat_shop_suggestion") in ("可铺", "谨慎"):
        reasons.append(f"微信小店预判{row.get('wechat_shop_suggestion')}")
    if row.get("data_gap_risk"):
        reasons.append(str(row.get("data_gap_risk")))
    return "；".join(reasons) or "关键字段待详情页核验"


def build_verification_queue(rows: List[Dict[str, Any]], max_items: int = 20) -> List[Dict[str, Any]]:
    queue: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("verification_status") not in ("", VERIFICATION_STATUS_UNVERIFIED):
            continue
        if row.get("recommendation_level") not in DETAIL_VERIFICATION_LEVELS and row.get("list_source") not in ("rpa", "manual_url", "url_direct"):
            continue
        if not any(row.get(key) in ("", DETAIL_VERIFICATION_PENDING, "待品退率核验") for key in DETAIL_ONLY_FIELDS):
            continue
        queue.append({
            "seq": row.get("seq"),
            "item_id": row.get("item_id", ""),
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "recommendation_level": row.get("recommendation_level", ""),
            "recommendation_score": row.get("recommendation_score", ""),
            "wechat_shop_suggestion": row.get("wechat_shop_suggestion", ""),
            "reason": _queue_reason(row),
            "pending_fields": [
                {"key": key, "label": _field_label(key)}
                for key in sorted(DETAIL_ONLY_FIELDS)
                if row.get(key) in ("", DETAIL_VERIFICATION_PENDING, "待品退率核验")
            ],
        })
        if len(queue) >= max_items:
            break
    return queue


def _auto_verify_limit(config: TagCollectInput, rows: List[Dict[str, Any]]) -> int:
    configured = int(config.auto_verify_max_items or 0)
    if configured > 0:
        return max(1, min(20, configured))
    target = int(config.target_publishable_count or config.max_items_per_query or 0)
    desired = max(target, min(len(rows), MAX_ITEMS_PER_QUERY))
    return max(1, min(20, desired))


def _has_blocking_detail_risk(row: Dict[str, Any]) -> bool:
    refund_value = row.get("product_refund_rate", "")
    shipment_value = row.get("shipment_rate", "")
    refund_rate_text = str(refund_value).replace("%", "").strip()
    shipment_rate_text = str(shipment_value).replace("%", "").strip()
    refund_rate = _parse_number(refund_value)
    shipment_rate = _parse_number(shipment_value)
    if refund_rate_text and refund_rate is None and refund_value != DETAIL_VERIFICATION_PENDING:
        return True
    if shipment_rate_text and shipment_rate is None and shipment_value != DETAIL_VERIFICATION_PENDING:
        return True
    refund_rate = refund_rate if refund_rate is not None else 0.0
    shipment_rate = shipment_rate if shipment_rate is not None else 100.0
    return refund_rate >= 5.0 or shipment_rate < 95.0


def _apply_verified_fields(
    row: Dict[str, Any],
    fields: Dict[str, Any],
    *,
    status: str,
    source: str,
    source_url: str,
    mode: str,
    fail_reason: str = "",
) -> List[Dict[str, Any]]:
    item_id = str(row.get("item_id", ""))
    evidence = _build_verification_evidence(
        item_id,
        fields,
        source=source,
        source_url=source_url,
        mode=mode,
        status=status,
        fail_reason=fail_reason,
    )
    if status in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL):
        verified_fields = set(row.get("detail_verified_fields") or [])
        for record in evidence:
            if record["status"] != VERIFICATION_STATUS_FAILED:
                row[record["field_key"]] = record["normalized"]
                verified_fields.add(record["field_key"])
        row["detail_verified_fields"] = sorted(verified_fields)
        if status == VERIFICATION_STATUS_VERIFIED and any(record["status"] == VERIFICATION_STATUS_FAILED for record in evidence):
            row["verification_status"] = VERIFICATION_STATUS_PARTIAL
            fail_reason = fail_reason or "真实详情页仅提取到部分关键字段"
        else:
            row["verification_status"] = status
        row["data_gap_risk"] = "" if not _has_blocking_detail_risk(row) else "详情核验存在履约/售后风险"
        row["after_sales_risk"] = "" if not _has_blocking_detail_risk(row) else "品退率或发货率需人工复核"
        row["risk_flags"] = "；".join(
            part for part in [str(row.get("risk_flags", "")), row.get("after_sales_risk", "")]
            if part
        )
        row["recommendation_reason"] = "；".join(
            part for part in [str(row.get("recommendation_reason", "")), f"详情字段已通过{source}补充"]
            if part
        )
        _refresh_metric_buckets(row)
    else:
        row["verification_status"] = VERIFICATION_STATUS_FAILED
        row["data_gap_risk"] = fail_reason or "详情页核验失败"
    row["verified_at"] = evidence[0]["verified_at"] if evidence else _now_iso()
    row["verification_source"] = source
    row["verification_mode"] = mode
    row["verification_fail_reason"] = fail_reason
    return evidence


def _sample_verify_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    item_id = str(row.get("item_id", ""))
    fields = SAMPLE_DETAIL_VERIFICATIONS.get(item_id)
    if not fields:
        return _apply_verified_fields(
            row,
            {},
            status=VERIFICATION_STATUS_FAILED,
            source="sample_detail",
            source_url=str(row.get("url", "")),
            mode="sample",
            fail_reason="样例详情库未覆盖该商品",
        )
    return _apply_verified_fields(
        row,
        fields,
        status=VERIFICATION_STATUS_SAMPLE,
        source="sample_detail",
        source_url=str(row.get("url", "")),
        mode="sample",
    )


def _real_verify_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from capabilities.tag_collect.rpa import collect_detail_fields_from_1688_page
        fields = collect_detail_fields_from_1688_page(str(row.get("url", "")), str(row.get("item_id", "")))
    except Exception as exc:
        return _apply_verified_fields(
            row,
            {},
            status=VERIFICATION_STATUS_FAILED,
            source="1688_detail_page_rpa",
            source_url=str(row.get("url", "")),
            mode="real",
            fail_reason=f"真实详情页核验失败：{exc}",
        )

    if not fields:
        return _apply_verified_fields(
            row,
            {},
            status=VERIFICATION_STATUS_FAILED,
            source="1688_detail_page_rpa",
            source_url=str(row.get("url", "")),
            mode="real",
            fail_reason="真实详情页未提取到关键字段",
        )

    return _apply_verified_fields(
        row,
        fields,
        status=VERIFICATION_STATUS_VERIFIED,
        source="1688_detail_page_rpa",
        source_url=str(row.get("url", "")),
        mode="real",
    )


def _should_stop_detail_verification(row: Dict[str, Any]) -> bool:
    reason = str(row.get("verification_fail_reason") or row.get("data_gap_risk") or "")
    return any(
        token in reason
        for token in (
            SECURITY_BLOCK_CODE,
            LOGIN_REQUIRED_CODE,
            "安全滑块",
            "验证码",
            "访问被拒绝",
            "访问受限",
            "登录",
        )
    )


def _automation_state(
    payload: Dict[str, Any],
    *,
    verification_stopped_reason: str = "",
) -> Dict[str, Any]:
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    queue = payload.get("verification_queue", [])
    queue_count = len(queue) if isinstance(queue, list) else 0
    stopped_reason = verification_stopped_reason or str(payload.get("verification_stopped_reason") or "")
    sample_data = bool(payload.get("sample_data"))
    verified_statuses = {
        VERIFICATION_STATUS_SAMPLE,
        VERIFICATION_STATUS_VERIFIED,
        VERIFICATION_STATUS_PARTIAL,
    }
    verified_count = len([row for row in rows if row.get("verification_status") in verified_statuses])
    failed_count = len([row for row in rows if row.get("verification_status") == VERIFICATION_STATUS_FAILED])
    partial_count = len([row for row in rows if row.get("verification_status") == VERIFICATION_STATUS_PARTIAL])
    target_publishable_count = int(payload.get("target_publishable_count") or 0)
    publishable_count = len(_publishable_rows(rows))
    shortfall_reason = str(payload.get("shortfall_reason") or "")

    if not rows:
        status = "empty"
        stage = "list_collect"
        action = "adjust_filters"
        message = "当前没有候选商品。请调整类目、标签或搜索词后重新采集。"
    elif stopped_reason:
        status = "paused"
        stage = "detail_verification"
        action = "manual_handoff"
        message = f"列表采集已完成，但详情核验已暂停：{stopped_reason}"
    elif failed_count and verified_count == 0:
        status = "failed"
        stage = "detail_verification"
        action = "manual_review"
        message = "列表采集已完成，但详情补字段失败；请人工复核后再使用导出结果。"
    elif queue_count:
        status = "pending_detail"
        stage = "detail_verification"
        action = "continue_detail_verification"
        message = (
            f"已筛出 {publishable_count}/{target_publishable_count} 个可铺候选，仍有高潜商品等待进入详情页核验。"
            if target_publishable_count
            else "列表字段仅完成初筛，仍有高潜商品等待进入详情页核验。"
        )
    elif partial_count or failed_count:
        status = "partial"
        stage = "export_ready"
        action = "manual_review"
        message = shortfall_reason or "部分详情字段未完全核验，导出后重点复核异常/边界商品。"
    elif verified_count:
        status = "sample_verified" if sample_data else "verified"
        stage = "export_ready"
        action = "manual_review"
        message = (
            "样例流程已核验，不能当作真实 1688 数据。"
            if sample_data
            else (
                f"详情字段已完成本轮核验，已筛出 {publishable_count}/{target_publishable_count} 个可铺候选；导出后重点复核异常/边界商品。"
                if target_publishable_count
                else "详情字段已完成本轮核验，导出后重点复核异常/边界商品。"
            )
        )
    else:
        status = "collected"
        stage = "export_ready"
        action = "manual_review"
        message = shortfall_reason or "列表初筛已完成，关键字段仍未进入详情页核验。"

    return {
        "status": status,
        "status_label": AUTOMATION_STATUS_LABELS.get(status, status),
        "stage": stage,
        "stage_label": AUTOMATION_STAGE_LABELS.get(stage, stage),
        "action": action,
        "action_label": AUTOMATION_ACTION_LABELS.get(action, action),
        "message": message,
        "row_count": len(rows),
        "queue_count": queue_count,
        "verified_count": verified_count,
        "failed_count": failed_count,
        "partial_count": partial_count,
        "target_publishable_count": target_publishable_count,
        "publishable_count": publishable_count,
        "strict_publishable_count": len(_publishable_rows(rows, include_cautious=False)),
        "publishable_candidate_count": len(_publishable_rows(rows, include_cautious=True)),
        "shortfall_reason": shortfall_reason,
        "stopped_reason": stopped_reason,
        "retryable": not bool(stopped_reason),
        "real_data": not sample_data,
    }


def update_automation_state(payload: Dict[str, Any], *, verification_stopped_reason: str = "") -> Dict[str, Any]:
    payload["automation_state"] = _automation_state(
        payload,
        verification_stopped_reason=verification_stopped_reason,
    )
    return payload["automation_state"]


def refresh_run_artifacts(payload: Dict[str, Any]) -> Dict[str, Any]:
    output_path = payload.get("output_path")
    rows = payload.get("rows", [])
    if isinstance(rows, list):
        apply_auto_review_statuses(rows)
    excluded_rows = payload.get("filter_excluded_rows", [])
    if isinstance(excluded_rows, list):
        apply_auto_review_statuses(excluded_rows)
    update_automation_state(payload)
    if isinstance(output_path, str) and output_path:
        if output_path.endswith(".csv"):
            export_csv(payload.get("rows", []), output_path)
        else:
            export_xlsx(payload.get("rows", []), output_path, payload)
    save_run_payload(payload, str(payload.get("run_id", "")))
    return payload


def verify_run_details(run_id: str, *, sample_data: bool = True, max_items: int = 20) -> Dict[str, Any]:
    payload = get_run_payload(run_id)
    if not payload:
        return {
            "success": False,
            "markdown": "未找到采集批次，无法执行详情核验。",
            "data": {"run_id": run_id, "verified_count": 0, "rows": [], "verification_queue": []},
        }

    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        rows = []
        payload["rows"] = rows
    queue = build_verification_queue(rows, max_items=max_items)
    evidence: List[Dict[str, Any]] = list(payload.get("verification_records") or [])
    filter_plan = payload.get("filter_plan") or {}
    filter_reevaluation_records: List[Dict[str, Any]] = []
    rescore_records: List[Dict[str, Any]] = []
    verified_count = 0
    failed_count = 0
    stopped_reason = ""
    row_by_id = {str(row.get("item_id", "")): row for row in rows if isinstance(row, dict)}

    for item in queue:
        item_id = str(item.get("item_id", ""))
        row = row_by_id.get(item_id)
        if not row:
            continue
        records = _sample_verify_row(row) if sample_data else _real_verify_row(row)
        evidence.extend(records)
        filter_records = reevaluate_row_filters_after_verification(row, filter_plan)
        if row.get("verification_status") in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL):
            rescore_records.append(rescore_row_after_detail(row))
        for record in filter_records:
            filter_reevaluation_records.append({
                "item_id": item_id,
                "title": row.get("title", ""),
                **record,
            })
        if row.get("verification_status") in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL):
            verified_count += 1
        else:
            failed_count += 1
            if not sample_data and _should_stop_detail_verification(row):
                stopped_reason = row.get("verification_fail_reason") or "详情页触发登录/风控，已停止后续核验"
                break

    payload["verification_records"] = evidence
    payload["filter_reevaluation_records"] = [
        *(payload.get("filter_reevaluation_records") or []),
        *filter_reevaluation_records,
    ]
    payload["rescore_records"] = [
        *(payload.get("rescore_records") or []),
        *rescore_records,
    ]
    excluded_rows = _apply_detail_filter_exclusions(payload)
    rows = payload.get("rows", [])
    if isinstance(rows, list):
        apply_auto_review_statuses(rows)
    non_publishable_rejections = _persist_non_publishable_rejections(payload)
    rows = payload.get("rows", [])
    publishable_count = len(_publishable_rows(rows))
    payload["publishable_count"] = publishable_count
    payload["strict_publishable_count"] = publishable_count
    payload["publishable_candidate_count"] = len(_publishable_rows(rows, include_cautious=True))
    payload["collection_stop_reason"] = "target_met" if publishable_count >= int(payload.get("target_publishable_count") or 0) else payload.get("collection_stop_reason", "")
    payload["shortfall_reason"] = _shortfall_reason(
        target_publishable_count=int(payload.get("target_publishable_count") or 0),
        publishable_count=publishable_count,
        collection_stop_reason=str(payload.get("collection_stop_reason") or "source_exhausted_or_filters_too_strict"),
        skipped_rejected_count=int(payload.get("skipped_rejected_count") or 0),
    )
    payload["verification_queue"] = build_verification_queue(rows, max_items=max_items)
    payload["verified_count"] = len([
        row for row in rows
        if isinstance(row, dict) and row.get("verification_status") in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL)
    ])
    payload["verification_failed_count"] = len([
        row for row in rows
        if isinstance(row, dict) and row.get("verification_status") == VERIFICATION_STATUS_FAILED
    ])
    payload["verification_field_failed_count"] = len([
        record for record in evidence
        if isinstance(record, dict) and record.get("status") == VERIFICATION_STATUS_FAILED
    ])
    payload["last_verified_at"] = _now_iso() if queue else payload.get("last_verified_at", "")
    payload["verification_stopped_reason"] = stopped_reason
    automation_state = update_automation_state(payload, verification_stopped_reason=stopped_reason)
    refresh_run_artifacts(payload)

    markdown = (
        "## 详情页核验结果\n\n"
        f"- 采集批次：`{run_id}`\n"
        f"- 本次进入核验队列：{len(queue)} 个商品\n"
        f"- 本次核验成功：{verified_count} 个商品\n"
        f"- 本次核验失败：{failed_count} 个商品\n"
        f"- 数据模式：{'样例详情核验（未调用 1688）' if sample_data else '真实详情核验'}"
        + (f"\n- 已停止后续核验：{stopped_reason}" if stopped_reason else "")
    )
    return {
        "success": True,
        "markdown": markdown,
        "data": {
            "run_id": run_id,
            "verified_count": verified_count,
            "failed_count": failed_count,
            "field_failed_count": payload.get("verification_field_failed_count", 0),
            "filter_excluded_count": len(excluded_rows),
            "rejected_count": payload.get("rejected_count", 0),
            "non_publishable_rejected_count": len(non_publishable_rejections),
            "row_count": len(rows),
            "rows": rows,
            "filter_excluded_rows": payload.get("filter_excluded_rows", []),
            "verification_queue": payload["verification_queue"],
            "verification_records": evidence,
            "filter_reevaluation_records": payload["filter_reevaluation_records"],
            "rescore_records": payload.get("rescore_records", []),
            "publishable_count": payload.get("publishable_count", 0),
            "strict_publishable_count": payload.get("strict_publishable_count", 0),
            "publishable_candidate_count": payload.get("publishable_candidate_count", 0),
            "target_publishable_count": payload.get("target_publishable_count", 0),
            "shortfall_reason": payload.get("shortfall_reason", ""),
            "download_url": f"/download?run_id={run_id}",
            "output_path": payload.get("output_path", ""),
            "verification_stopped_reason": stopped_reason,
            "automation_state": automation_state,
        },
    }


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _sheet_xml(rows: List[List[Any]]) -> str:
    xml_rows = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for col_index, value in enumerate(row, 1):
            ref = f"{_column_name(col_index)}{row_index}"
            text = xml_escape(_cell_text(value))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<sheetData>{"".join(xml_rows)}</sheetData>'
        '</worksheet>'
    )


def _workbook_xml(sheet_names: List[str]) -> str:
    sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheets}</sheets>'
        '</workbook>'
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    rels += f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{rels}</Relationships>'
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )


def _content_types_xml(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{overrides}</Types>'
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Arial"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
    )


def export_xlsx(rows: List[Dict[str, Any]], output_path: str, payload: Optional[Dict[str, Any]] = None) -> str:
    Path(os.path.dirname(output_path)).mkdir(parents=True, exist_ok=True)
    apply_auto_review_statuses(rows)
    if payload:
        excluded_rows = payload.get("filter_excluded_rows", [])
        if isinstance(excluded_rows, list):
            apply_auto_review_statuses(excluded_rows)
    keys = [key for key, _ in EXPORT_COLUMNS]
    labels = [label for _, label in EXPORT_COLUMNS]
    result_rows = [labels] + [[row.get(key, "") for key in keys] for row in rows]
    review_summary = _manual_review_summary([
        *rows,
        *[
            row for row in payload.get("filter_excluded_rows", [])
            if isinstance(row, dict)
        ],
    ])
    field_rows = [["编号", "分组", "字段", "字段键", "来源", "是否必须核验"]] + [
        [field["number"], field["group"], field["label"], field["key"], field["source"], field["verify"]]
        for field in EXPORT_FIELD_DEFINITIONS
    ]
    payload = payload or {}
    config_rows = [
        ["配置项", "值"],
        ["采集批次", payload.get("run_id", "")],
        ["任务状态", (payload.get("automation_state") or {}).get("status_label", "")],
        ["当前阶段", (payload.get("automation_state") or {}).get("stage_label", "")],
        ["建议动作", (payload.get("automation_state") or {}).get("action_label", "")],
        ["状态说明", (payload.get("automation_state") or {}).get("message", "")],
        ["详情核验暂停原因", payload.get("verification_stopped_reason", "")],
        ["自动详情核验", "开启" if payload.get("auto_verify_details") else "关闭"],
        ["自动核验上限", payload.get("auto_verify_max_items", "")],
        ["目标可铺数量", payload.get("target_publishable_count", "")],
        ["目标达标数量(可铺)", payload.get("publishable_count", 0)],
        ["可铺/谨慎候选数量", payload.get("publishable_candidate_count", 0)],
        ["候选扫描数量", payload.get("candidate_count", 0)],
        ["候选扫描预算", payload.get("candidate_scan_limit", "")],
        ["历史筛除跳过", payload.get("skipped_rejected_count", 0)],
        ["本次筛除记录", payload.get("rejected_count", 0)],
        ["采集停止原因", payload.get("collection_stop_reason", "")],
        ["目标短缺原因", payload.get("shortfall_reason", "")],
        ["已核验商品数", payload.get("verified_count", 0)],
        ["核验失败商品数", payload.get("verification_failed_count", 0)],
        ["核验失败字段数", payload.get("verification_field_failed_count", 0)],
        ["详情筛选剔除商品数", payload.get("filter_excluded_count", 0)],
        ["待详情核验商品数", len(payload.get("verification_queue", []) or [])],
        ["系统预通过数量", review_summary["system_preapproved"]],
        ["系统不建议数量", review_summary["system_not_recommended"]],
        ["系统剔除数量", review_summary["system_excluded"]],
        ["待人工复核数量", review_summary["manual_pending"]],
        ["查询词", ", ".join(payload.get("queries", []))],
        ["类目标签", ", ".join(payload.get("categories", []))],
        ["运营标签", ", ".join(payload.get("tags", []))],
        ["排除标签", ", ".join(payload.get("exclude_tags", []))],
        ["搜索词标签", ", ".join((payload.get("filter_plan") or {}).get("search_terms", []))],
        ["1688原生筛选", json.dumps((payload.get("filter_plan") or {}).get("native_filters", []), ensure_ascii=False)],
        ["指标区间筛选", json.dumps((payload.get("filter_plan") or {}).get("post_filters", []), ensure_ascii=False)],
        ["系统规则", json.dumps((payload.get("filter_plan") or {}).get("system_rules", []), ensure_ascii=False)],
        ["未映射标签", ", ".join((payload.get("filter_plan") or {}).get("unmapped_tags", []))],
        ["店雷达选品库筛选", json.dumps(payload.get("library_filters", {}), ensure_ascii=False)],
        ["店雷达筛选映射结果", json.dumps((payload.get("filter_plan") or {}).get("library_filter_results", []), ensure_ascii=False)],
        ["预留筛选字段", json.dumps((payload.get("filter_plan") or {}).get("library_reserved_fields", []), ensure_ascii=False)],
        ["筛选覆盖状态", json.dumps(payload.get("library_filter_coverage", get_library_filter_coverage()), ensure_ascii=False)],
        ["详情核验后筛选重评估", json.dumps(payload.get("filter_reevaluation_records", []), ensure_ascii=False)],
        ["详情核验后重评分", json.dumps(payload.get("rescore_records", []), ensure_ascii=False)],
        ["历史筛除跳过记录", json.dumps(payload.get("skipped_rejected_records", []), ensure_ascii=False)],
        ["本次筛除记录", json.dumps(payload.get("rejection_records", []), ensure_ascii=False)],
        ["过滤规则", json.dumps(payload.get("filter_rules", {}), ensure_ascii=False)],
        ["筛选执行记录", json.dumps(payload.get("filter_results", []), ensure_ascii=False)],
        ["数据模式", "样例数据" if payload.get("sample_data") else "真实采集"],
        ["采集来源", payload.get("collect_source", "")],
        ["说明", "列表字段仅用于初筛；运费、品退率、发货率等关键字段需详情页核验后才可信。"],
    ]
    failed_candidates = [
        *rows,
        *[
            row for row in payload.get("filter_excluded_rows", [])
            if isinstance(row, dict)
        ],
    ]
    failed_rows = [labels] + [
        [row.get(key, "") for key in keys]
        for row in failed_candidates
        if _row_needs_exception_review(row)
    ]
    verification_rows = [[
        "商品ID",
        "字段键",
        "字段名",
        "原始值",
        "标准化值",
        "来源",
        "来源链接",
        "模式",
        "状态",
        "核验时间",
        "失败原因",
    ]] + [
        [
            record.get("item_id", ""),
            record.get("field_key", ""),
            record.get("field_label", ""),
            record.get("raw", ""),
            record.get("normalized", ""),
            record.get("source", ""),
            record.get("source_url", ""),
            record.get("mode", ""),
            record.get("status", ""),
            record.get("verified_at", ""),
            record.get("fail_reason", ""),
        ]
        for record in payload.get("verification_records", [])
        if isinstance(record, dict)
    ]
    filter_rows = [[
        "筛选键",
        "筛选标签",
        "显示名称",
        "状态",
        "来源",
        "查询/页面",
        "页面URL",
        "匹配文本",
        "提示",
    ]] + [
        [
            record.get("filter_key", ""),
            record.get("tag", ""),
            record.get("label", ""),
            record.get("status", ""),
            record.get("source", ""),
            record.get("query", ""),
            record.get("page_url", ""),
            record.get("matched_text", ""),
            record.get("message", ""),
        ]
        for record in payload.get("filter_results", [])
        if isinstance(record, dict)
    ]

    sheets = [
        ("选品结果", result_rows),
        ("字段说明", field_rows),
        ("标签配置", config_rows),
        ("异常复核", failed_rows),
        ("核验记录", verification_rows),
        ("筛选执行记录", filter_rows),
    ]
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml(len(sheets)))
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("xl/workbook.xml", _workbook_xml([name for name, _ in sheets]))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(sheets)))
        zf.writestr("xl/styles.xml", _styles_xml())
        for idx, (_, sheet_rows) in enumerate(sheets, 1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", _sheet_xml(sheet_rows))
    return output_path


def export_csv(rows: List[Dict[str, Any]], output_path: str) -> str:
    import csv

    Path(os.path.dirname(output_path)).mkdir(parents=True, exist_ok=True)
    apply_auto_review_statuses(rows)
    keys = [key for key, _ in EXPORT_COLUMNS]
    labels = [label for _, label in EXPORT_COLUMNS]
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(labels)
        for row in rows:
            writer.writerow([row.get(key, "") for key in keys])
    return output_path


def save_run_payload(payload: Dict[str, Any], run_id: str) -> str:
    data_dir = get_tag_collect_data_dir()
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(data_dir, f"tag_collect_{run_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def save_error_payload(payload: Dict[str, Any]) -> str:
    data_dir = get_tag_collect_data_dir()
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    error_id = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    path = os.path.join(data_dir, f"tag_collect_error_{error_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"error_id": error_id, "created_at": now.isoformat(timespec="seconds"), **payload}, f, ensure_ascii=False, indent=2)
    return path


def get_run_payload(run_id: str) -> Optional[Dict[str, Any]]:
    if not run_id or "/" in run_id or "\\" in run_id:
        return None
    path = os.path.join(get_tag_collect_data_dir(), f"tag_collect_{run_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def get_export_path(run_id: str) -> Optional[str]:
    payload = get_run_payload(run_id)
    if not payload:
        return None
    output_path = payload.get("output_path")
    if not isinstance(output_path, str):
        return None
    root = os.path.abspath(get_tag_collect_data_dir())
    candidate = os.path.abspath(output_path)
    if candidate == root or not candidate.startswith(root + os.sep):
        return None
    return candidate if os.path.exists(candidate) else None


def run_tag_collect(config: TagCollectInput) -> Dict[str, Any]:
    rows, queries, filter_plan = collect_products(config)
    now = datetime.now()
    run_id = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    for row in rows:
        row["run_id"] = run_id
    apply_auto_review_statuses(rows)
    auto_verify_limit = _auto_verify_limit(config, rows) if config.auto_verify_details else config.auto_verify_max_items
    ext = "csv" if config.output_format == "csv" else "xlsx"
    output_path = os.path.join(get_tag_collect_data_dir(), f"tag_collect_{run_id}.{ext}")
    payload = {
        "run_id": run_id,
        "queries": queries,
        "filter_rules": build_filter_rule_summary(config),
        "filter_plan": filter_plan,
        "filter_results": filter_plan.get("filter_results", []),
        "filter_warnings": filter_plan.get("filter_warnings", []),
        "library_filter_coverage": get_library_filter_coverage(),
        "category_dictionary": {
            "version": CATEGORY_DICTIONARY.get("version", ""),
            "source": CATEGORY_DICTIONARY.get("source", ""),
            "status": CATEGORY_DICTIONARY.get("status", ""),
            "updated_at": CATEGORY_DICTIONARY.get("updated_at", ""),
        },
        "categories": config.categories,
        "tags": config.tags,
        "source_urls": config.source_urls,
        "exclude_tags": config.exclude_tags,
        "library_filters": config.library_filters,
        "sample_data": config.sample_data,
        "collect_source": config.collect_source,
        "row_count": len(rows),
        "target_publishable_count": filter_plan.get("target_publishable_count", config.target_publishable_count),
        "publishable_count": filter_plan.get("publishable_count", len(_publishable_rows(rows))),
        "strict_publishable_count": filter_plan.get("strict_publishable_count", len(_publishable_rows(rows, include_cautious=False))),
        "publishable_candidate_count": filter_plan.get("publishable_candidate_count", len(_publishable_rows(rows, include_cautious=True))),
        "candidate_count": filter_plan.get("candidate_count", len(rows)),
        "candidate_scan_limit": filter_plan.get("candidate_scan_limit", _candidate_scan_limit(config)),
        "collection_stop_reason": filter_plan.get("collection_stop_reason", ""),
        "shortfall_reason": filter_plan.get("shortfall_reason", ""),
        "rejection_records": filter_plan.get("rejection_records", []),
        "rejected_count": filter_plan.get("rejected_count", 0),
        "skipped_rejected_records": filter_plan.get("skipped_rejected_records", []),
        "skipped_rejected_count": filter_plan.get("skipped_rejected_count", 0),
        "persistent_excluded_count": filter_plan.get("persistent_excluded_count", 0),
        "filter_signature": filter_plan.get("filter_signature", ""),
        "target_platform": filter_plan.get("target_platform", _target_platform_from_tags(config.tags)),
        "output_path": output_path,
        "rows": rows,
        "verification_queue": build_verification_queue(rows, max_items=auto_verify_limit or 20),
        "verification_records": [],
        "filter_reevaluation_records": [],
        "rescore_records": [],
        "filter_excluded_rows": [],
        "filter_excluded_count": 0,
        "verified_count": 0,
        "verification_failed_count": 0,
        "verification_field_failed_count": 0,
        "auto_verify_details": config.auto_verify_details,
        "auto_verify_max_items": auto_verify_limit,
        "auto_verify_result": {},
        "last_verified_at": "",
        "verification_stopped_reason": "",
    }
    update_automation_state(payload)
    if ext == "csv":
        export_csv(rows, output_path)
    else:
        export_xlsx(rows, output_path, payload)
    snapshot_path = save_run_payload(payload, run_id)
    auto_verify_result: Optional[Dict[str, Any]] = None
    if config.auto_verify_details and payload["verification_queue"]:
        auto_verify_result = verify_run_details(
            run_id,
            sample_data=config.sample_data,
            max_items=auto_verify_limit,
        )
        if auto_verify_result.get("success"):
            payload = get_run_payload(run_id) or payload
            payload["auto_verify_details"] = config.auto_verify_details
            payload["auto_verify_max_items"] = auto_verify_limit
            payload["auto_verify_result"] = auto_verify_result.get("data", {})
            refresh_run_artifacts(payload)
            snapshot_path = os.path.join(get_tag_collect_data_dir(), f"tag_collect_{run_id}.json")
    markdown = build_markdown(payload, snapshot_path)
    if auto_verify_result and auto_verify_result.get("success"):
        auto_data = auto_verify_result.get("data", {})
        stopped_reason = str(auto_data.get("verification_stopped_reason") or "")
        markdown += (
            "\n\n## 自动详情核验\n"
            f"- 已自动进入详情页核验：{auto_data.get('verified_count', 0)} 个商品\n"
            f"- 核验失败：{auto_data.get('failed_count', 0)} 个商品\n"
            + (f"- 已暂停后续详情核验：{stopped_reason}\n" if stopped_reason else "")
            + "- 导出文件已刷新，系统已自动标记预通过/不建议/剔除；仅异常和边界商品需要人工复核。"
        )
    return {
        "success": True,
        "markdown": markdown,
        "data": {
            "run_id": run_id,
            "queries": queries,
            "source_urls": config.source_urls,
            "row_count": payload.get("row_count", len(payload.get("rows", rows))),
            "target_publishable_count": payload.get("target_publishable_count", config.target_publishable_count),
            "publishable_count": payload.get("publishable_count", len(_publishable_rows(payload.get("rows", rows)))),
            "strict_publishable_count": payload.get("strict_publishable_count", len(_publishable_rows(payload.get("rows", rows), include_cautious=False))),
            "publishable_candidate_count": payload.get("publishable_candidate_count", len(_publishable_rows(payload.get("rows", rows), include_cautious=True))),
            "candidate_count": payload.get("candidate_count", 0),
            "candidate_scan_limit": payload.get("candidate_scan_limit", 0),
            "collection_stop_reason": payload.get("collection_stop_reason", ""),
            "shortfall_reason": payload.get("shortfall_reason", ""),
            "rejection_records": payload.get("rejection_records", []),
            "rejected_count": payload.get("rejected_count", 0),
            "skipped_rejected_records": payload.get("skipped_rejected_records", []),
            "skipped_rejected_count": payload.get("skipped_rejected_count", 0),
            "persistent_excluded_count": payload.get("persistent_excluded_count", 0),
            "output_path": output_path,
            "snapshot_path": snapshot_path,
            "sample_data": config.sample_data,
            "collect_source": config.collect_source,
            "library_filters": config.library_filters,
            "columns": [label for _, label in EXPORT_COLUMNS],
            "top_items": payload.get("rows", rows)[:10],
            "rows": payload.get("rows", rows),
            "filter_excluded_rows": payload.get("filter_excluded_rows", []),
            "filter_excluded_count": payload.get("filter_excluded_count", 0),
            "filter_plan": filter_plan,
            "filter_results": payload["filter_results"],
            "filter_warnings": payload["filter_warnings"],
            "library_filter_coverage": payload["library_filter_coverage"],
            "category_dictionary": payload["category_dictionary"],
            "verification_queue": payload["verification_queue"],
            "verification_records": payload["verification_records"],
            "filter_reevaluation_records": payload["filter_reevaluation_records"],
            "rescore_records": payload.get("rescore_records", []),
            "verified_count": payload.get("verified_count", 0),
            "verification_failed_count": payload.get("verification_failed_count", 0),
            "verification_stopped_reason": payload.get("verification_stopped_reason", ""),
            "automation_state": payload.get("automation_state", {}),
            "auto_verify_details": config.auto_verify_details,
            "auto_verify_result": auto_verify_result.get("data", {}) if auto_verify_result else {},
        },
    }


def build_markdown(payload: Dict[str, Any], snapshot_path: str) -> str:
    lines = ["## 标签选品采集结果\n"]
    lines.append(f"- 采集批次：`{payload['run_id']}`")
    lines.append(f"- 查询词：{', '.join(payload['queries'])}")
    if payload.get("categories"):
        lines.append(f"- 类目标签：{', '.join(payload['categories'])}")
    if payload.get("tags"):
        lines.append(f"- 运营标签：{', '.join(payload['tags'])}")
    filter_plan = payload.get("filter_plan") or {}
    if filter_plan.get("native_filters"):
        labels = [str(item.get("label") or item.get("tag") or "") for item in filter_plan.get("native_filters", [])]
        lines.append(f"- 1688页面原生筛选：{', '.join(label for label in labels if label)}")
    if filter_plan.get("post_filters"):
        labels = [str(item.get("label") or item.get("tag") or "") for item in filter_plan.get("post_filters", [])]
        lines.append(f"- 指标区间筛选：{', '.join(label for label in labels if label)}")
    if payload.get("filter_warnings"):
        warning_text = "；".join(
            f"{record.get('label') or record.get('tag')}={record.get('status')} {record.get('message', '')}".strip()
            for record in payload.get("filter_warnings", [])
            if isinstance(record, dict)
        )
        lines.append(f"- 筛选提示：{warning_text}")
    category_dictionary = payload.get("category_dictionary") or {}
    if category_dictionary:
        lines.append(
            "- 类目字典："
            f"{category_dictionary.get('source', '')}/{category_dictionary.get('version', '')}"
            f"（{category_dictionary.get('status', '')}）"
        )
    lines.append(f"- 商品数：{payload['row_count']}")
    if payload.get("target_publishable_count"):
        lines.append(
            "- 可铺目标："
            f"{payload.get('publishable_count', 0)}/{payload.get('target_publishable_count', 0)}"
            f"（可铺/谨慎候选 {payload.get('publishable_candidate_count', 0)}）"
        )
    if payload.get("candidate_count") is not None:
        lines.append(
            f"- 候选扫描：{payload.get('candidate_count', 0)}/{payload.get('candidate_scan_limit', '')}"
            f"，历史筛除跳过 {payload.get('skipped_rejected_count', 0)}"
        )
    if payload.get("shortfall_reason"):
        lines.append(f"- 未达目标原因：{payload.get('shortfall_reason')}")
    lines.append(f"- 待详情页核验商品：{len(payload.get('verification_queue', []))}")
    automation_state = payload.get("automation_state") or _automation_state(payload)
    lines.append(f"- 任务状态：{automation_state.get('status_label', '')} / {automation_state.get('stage_label', '')}")
    if automation_state.get("message"):
        lines.append(f"- 状态说明：{automation_state.get('message')}")
    lines.append(f"- 导出文件：`{payload['output_path']}`")
    lines.append(f"- 快照文件：`{snapshot_path}`")
    if payload.get("sample_data"):
        lines.append("- 数据模式：样例数据（未调用 1688 接口）")
    else:
        lines.append(f"- 数据模式：真实数据（来源：{payload.get('collect_source') or 'rpa'}）")
    lines.append("\n### 人工复核说明")
    lines.append("系统会优先自动筛除明确不满足条件的商品，并把历史失败商品跳过；导出后重点复核异常、边界和 `谨慎` 商品。")
    lines.append("列表字段只做初筛，运费、品退率、发货率等关键字段仍需进入详情页核验；触发风控时不会继续刷新或绕过验证。")
    return "\n".join(lines)
