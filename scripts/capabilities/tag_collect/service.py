#!/usr/bin/env python3
"""标签选品采集服务 — 复选标签采集、评分、导出"""

import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

from _const import TAG_COLLECT_DATA_DIR


_DATA_DIR_CACHE: Optional[str] = None
MAX_QUERIES = 50
MAX_ITEMS_PER_QUERY = 50
DETAIL_VERIFICATION_PENDING = "待详情页核验"
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
    "stock",
    "video_query",
}
DETAIL_VERIFICATION_LEVELS = {"P0", "P1"}
VERIFICATION_STATUS_UNVERIFIED = "unverified"
VERIFICATION_STATUS_SAMPLE = "sample_verified"
VERIFICATION_STATUS_VERIFIED = "verified"
VERIFICATION_STATUS_PARTIAL = "partial_verified"
VERIFICATION_STATUS_FAILED = "failed"


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


TAG_CATEGORY_TREE = {
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

TAG_CATEGORY_OPTIONS = list(TAG_CATEGORY_TREE.keys())

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
        {"number": "3.7", "key": "favorite_customers", "label": "收藏客户", "source": "详情/可信来源", "verify": "建议核验"},
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
EXPORT_COLUMNS: List[Tuple[str, str]] = [
    (field["key"], field["label"]) for field in EXPORT_FIELD_DEFINITIONS
]


def get_numbered_export_columns() -> List[Dict[str, Any]]:
    return [dict(field) for field in EXPORT_FIELD_DEFINITIONS]


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
        "rights_protection": "7天无理由/破损包赔",
        "dropship_rights": "支持一件代发",
        "waybill_support": "微信小店,抖店",
        "collection_rate_24h": "96.0%",
        "shipment_rate": "98.6%",
        "shipment_speed": "24-48小时发货",
        "supports_dropship": "是",
        "return_exchange_support": "支持退换",
        "monthly_dropship_orders": "820",
        "favorite_customers": "356",
        "shop_name": "义乌童雨户外用品厂",
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
        "rights_protection": "7天包退换",
        "dropship_rights": "支持一件代发",
        "waybill_support": "微信小店",
        "collection_rate_24h": "88.0%",
        "shipment_rate": "95.2%",
        "shipment_speed": "48小时内发货",
        "supports_dropship": "是",
        "return_exchange_support": "支持退换",
        "monthly_dropship_orders": "650",
        "favorite_customers": "218",
        "shop_name": "广州轻熟女装供应链",
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
        "rights_protection": "质保一年",
        "dropship_rights": "支持一件代发",
        "waybill_support": "抖店,淘宝",
        "collection_rate_24h": "92.0%",
        "shipment_rate": "93.4%",
        "shipment_speed": "48-72小时发货",
        "supports_dropship": "是",
        "return_exchange_support": "质量问题支持退换",
        "monthly_dropship_orders": "310",
        "favorite_customers": "96",
        "shop_name": "佛山小家电供应链",
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


def _split_csv(value: str) -> List[str]:
    items: List[str] = []
    for raw in (value or "").replace("，", ",").split(","):
        item = raw.strip()
        if item:
            items.append(item)
    return items


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
) -> TagCollectInput:
    return TagCollectInput(
        categories=_split_csv(categories),
        tags=_split_csv(tags),
        keywords=_split_csv(keywords),
        source_urls=_split_csv(source_urls),
        exclude_tags=_split_csv(exclude_tags),
        max_queries=min(MAX_QUERIES, max(1, max_queries)),
        max_items_per_query=min(MAX_ITEMS_PER_QUERY, max(1, max_items_per_query)),
        sample_data=sample_data,
        output_format=(output_format or "xlsx").lower(),
        collect_source=(collect_source or "rpa").lower(),
    )


def _channel_from_tags(tags: Iterable[str]) -> str:
    for tag in tags:
        channel = CHANNEL_TAGS.get(tag)
        if channel and channel != "weixin":
            return channel
    return ""


def _query_part_from_tag(tag: str) -> str:
    return SCENE_HINTS.get(tag, tag)


def build_queries(config: TagCollectInput) -> List[str]:
    bases = config.keywords or config.categories or [""]
    hints = [_query_part_from_tag(t) for t in config.tags if t not in CHANNEL_TAGS]

    queries: List[str] = []
    for base in bases:
        if hints:
            for hint in hints:
                q = " ".join(part for part in [base, hint] if part).strip()
                if q and q not in queries:
                    queries.append(q)
        elif base and base not in queries:
            queries.append(base)

    if not queries:
        queries = ["1688 选品"]
    return queries[: config.max_queries]


def build_filter_rule_summary(config: TagCollectInput) -> Dict[str, Any]:
    return {
        "and_tags": config.tags,
        "or_categories": config.categories,
        "source_urls": config.source_urls,
        "exclude_tags": config.exclude_tags,
        "notes": "MVP：类目/标签用于生成查询词或辅助 URL 采集评分；source_urls 可直接指定真实 1688 页面；exclude_tags 对标题、类目、风险提示、标签来源做命中过滤。详情页字段仍需后续核验。",
    }


def _sample_products_for_query(query: str, limit: int) -> List[Product]:
    query_lower = query.lower()
    matched = [
        p for p in SAMPLE_PRODUCTS
        if query_lower in p.title.lower()
        or query_lower in str((p.stats or {}).get("categoryListName", "")).lower()
    ]
    return (matched or SAMPLE_PRODUCTS)[:limit]


def collect_products(config: TagCollectInput) -> Tuple[List[Dict[str, Any]], List[str]]:
    direct_urls = config.source_urls if (not config.sample_data and config.collect_source == "rpa") else []
    queries = direct_urls or build_queries(config)
    channel = _channel_from_tags(config.tags)
    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []

    for query in queries:
        if config.sample_data:
            products = _sample_products_for_query(query, config.max_items_per_query)
        elif config.collect_source == "api":
            from capabilities.search.service import search_products
            products = search_products(query, channel=channel)[: config.max_items_per_query]
        else:
            from capabilities.tag_collect.rpa import collect_products_from_1688_page
            products = [
                Product(
                    id=str(item.get("id", "")),
                    title=str(item.get("title", "")),
                    price=str(item.get("price", "-")),
                    image=str(item.get("image", "")),
                    url=str(item.get("url", "")),
                    stats=item.get("stats") if isinstance(item.get("stats"), dict) else {},
                )
                for item in collect_products_from_1688_page(
                    query if not direct_urls else "",
                    config.max_items_per_query,
                    source_url=query if direct_urls else "",
                )
                if str(item.get("id", "")).strip()
            ]
        for product in products:
            if product.id in seen:
                continue
            seen.add(product.id)
            row = product_to_export_row(product, query, config)
            if _excluded_by_tags(row, config.exclude_tags):
                continue
            rows.append(row)

    ranked = sorted(rows, key=lambda item: item["recommendation_score"], reverse=True)
    for index, row in enumerate(ranked, 1):
        row["seq"] = index
    return ranked, queries


def friendly_collect_error(error: Exception) -> str:
    """把 RPA/真实采集底层异常转成运营可理解的提示。"""
    message = str(error)
    if "login_required:" in message:
        return message.split("login_required:", 1)[1].strip()
    if "browser_closed:" in message:
        return message.split("browser_closed:", 1)[1].strip()
    if "Target page, context or browser has been closed" in message:
        return (
            "真实采集窗口已关闭或登录/验证未完成，未生成任何数据。"
            "请保持弹出的 1688/淘宝登录窗口打开并完成扫码验证后重试；"
            "如果账号仍登录不上，可以粘贴浏览器里能打开的 1688 搜索页或商品详情页 URL 做公开页面真实数据测试。"
        )
    if "真实页面 RPA 返回格式异常" in message:
        return "真实页面 RPA 返回异常，未生成任何数据。请重试一次；如果仍失败，优先使用 1688 页面 URL 模式测试真实页面解析。"
    return message


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


def _default_platform_suggestion(target: str, tags: List[str]) -> str:
    return "待人工复核" if target in tags else ""


def product_to_export_row(product: Product, query: str, config: TagCollectInput) -> Dict[str, Any]:
    stats = product.stats or {}
    score, reasons, risks = _score_product(stats, config.tags)
    category_path = (
        stats.get("categoryListName")
        or stats.get("categoryName")
        or _match_category_from_config(config.categories, product.title)
    )
    row: Dict[str, Any] = {
        "seq": 0,
        "run_id": "",
        "category_path": category_path,
        "image_cell": "",
        "title": product.title,
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
    for key in sorted(DETAIL_ONLY_FIELDS):
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
    if row.get("list_source") == "rpa":
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
        if row.get("recommendation_level") not in DETAIL_VERIFICATION_LEVELS and row.get("list_source") != "rpa":
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


def _has_blocking_detail_risk(row: Dict[str, Any]) -> bool:
    refund_rate_text = str(row.get("product_refund_rate", "")).replace("%", "").strip()
    shipment_rate_text = str(row.get("shipment_rate", "")).replace("%", "").strip()
    try:
        refund_rate = float(refund_rate_text) if refund_rate_text else 0.0
    except ValueError:
        refund_rate = 0.0
    try:
        shipment_rate = float(shipment_rate_text) if shipment_rate_text else 100.0
    except ValueError:
        shipment_rate = 100.0
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
        for record in evidence:
            if record["status"] != VERIFICATION_STATUS_FAILED:
                row[record["field_key"]] = record["normalized"]
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


def refresh_run_artifacts(payload: Dict[str, Any]) -> Dict[str, Any]:
    output_path = payload.get("output_path")
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
    verified_count = 0
    failed_count = 0
    row_by_id = {str(row.get("item_id", "")): row for row in rows if isinstance(row, dict)}

    for item in queue:
        item_id = str(item.get("item_id", ""))
        row = row_by_id.get(item_id)
        if not row:
            continue
        records = _sample_verify_row(row) if sample_data else _real_verify_row(row)
        evidence.extend(records)
        if row.get("verification_status") in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL):
            verified_count += 1
        else:
            failed_count += 1

    payload["verification_queue"] = build_verification_queue(rows, max_items=max_items)
    payload["verification_records"] = evidence
    payload["verified_count"] = len([
        row for row in rows
        if isinstance(row, dict) and row.get("verification_status") in (VERIFICATION_STATUS_SAMPLE, VERIFICATION_STATUS_VERIFIED, VERIFICATION_STATUS_PARTIAL)
    ])
    payload["verification_failed_count"] = len([
        row for row in rows
        if isinstance(row, dict) and row.get("verification_status") == VERIFICATION_STATUS_FAILED
    ])
    payload["last_verified_at"] = _now_iso() if queue else payload.get("last_verified_at", "")
    refresh_run_artifacts(payload)

    markdown = (
        "## 详情页核验结果\n\n"
        f"- 采集批次：`{run_id}`\n"
        f"- 本次进入核验队列：{len(queue)} 个商品\n"
        f"- 本次核验成功：{verified_count} 个商品\n"
        f"- 本次核验失败：{failed_count} 个商品\n"
        f"- 数据模式：{'样例详情核验（未调用 1688）' if sample_data else '真实详情核验'}"
    )
    return {
        "success": True,
        "markdown": markdown,
        "data": {
            "run_id": run_id,
            "verified_count": verified_count,
            "failed_count": failed_count,
            "row_count": len(rows),
            "rows": rows,
            "verification_queue": payload["verification_queue"],
            "verification_records": evidence,
            "download_url": f"/download?run_id={run_id}",
            "output_path": payload.get("output_path", ""),
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
    keys = [key for key, _ in EXPORT_COLUMNS]
    labels = [label for _, label in EXPORT_COLUMNS]
    result_rows = [labels] + [[row.get(key, "") for key in keys] for row in rows]
    field_rows = [["编号", "分组", "字段", "字段键", "来源", "是否必须核验"]] + [
        [field["number"], field["group"], field["label"], field["key"], field["source"], field["verify"]]
        for field in EXPORT_FIELD_DEFINITIONS
    ]
    payload = payload or {}
    config_rows = [
        ["配置项", "值"],
        ["采集批次", payload.get("run_id", "")],
        ["查询词", ", ".join(payload.get("queries", []))],
        ["类目标签", ", ".join(payload.get("categories", []))],
        ["运营标签", ", ".join(payload.get("tags", []))],
        ["排除标签", ", ".join(payload.get("exclude_tags", []))],
        ["过滤规则", json.dumps(payload.get("filter_rules", {}), ensure_ascii=False)],
        ["数据模式", "样例数据" if payload.get("sample_data") else "真实采集"],
        ["采集来源", payload.get("collect_source", "")],
        ["说明", "列表字段仅用于初筛；运费、品退率、发货率等关键字段需详情页核验后才可信。"],
    ]
    failed_rows = [labels] + [
        [row.get(key, "") for key in keys]
        for row in rows
        if row.get("verification_status") in ("verify_failed", "failed") or row.get("data_gap_risk")
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

    sheets = [
        ("选品结果", result_rows),
        ("字段说明", field_rows),
        ("标签配置", config_rows),
        ("核验失败", failed_rows),
        ("核验记录", verification_rows),
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
    rows, queries = collect_products(config)
    now = datetime.now()
    run_id = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    for row in rows:
        row["run_id"] = run_id
    ext = "csv" if config.output_format == "csv" else "xlsx"
    output_path = os.path.join(get_tag_collect_data_dir(), f"tag_collect_{run_id}.{ext}")
    payload = {
        "run_id": run_id,
        "queries": queries,
        "filter_rules": build_filter_rule_summary(config),
        "categories": config.categories,
        "tags": config.tags,
        "source_urls": config.source_urls,
        "exclude_tags": config.exclude_tags,
        "sample_data": config.sample_data,
        "collect_source": config.collect_source,
        "row_count": len(rows),
        "output_path": output_path,
        "rows": rows,
        "verification_queue": build_verification_queue(rows),
        "verification_records": [],
        "verified_count": 0,
        "verification_failed_count": 0,
        "last_verified_at": "",
    }
    if ext == "csv":
        export_csv(rows, output_path)
    else:
        export_xlsx(rows, output_path, payload)
    snapshot_path = save_run_payload(payload, run_id)
    markdown = build_markdown(payload, snapshot_path)
    return {
        "success": True,
        "markdown": markdown,
        "data": {
            "run_id": run_id,
            "queries": queries,
            "source_urls": config.source_urls,
            "row_count": len(rows),
            "output_path": output_path,
            "snapshot_path": snapshot_path,
            "sample_data": config.sample_data,
            "collect_source": config.collect_source,
            "columns": [label for _, label in EXPORT_COLUMNS],
            "top_items": rows[:10],
            "verification_queue": payload["verification_queue"],
            "verified_count": 0,
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
    lines.append(f"- 商品数：{payload['row_count']}")
    lines.append(f"- 待详情页核验商品：{len(payload.get('verification_queue', []))}")
    lines.append(f"- 导出文件：`{payload['output_path']}`")
    lines.append(f"- 快照文件：`{snapshot_path}`")
    if payload.get("sample_data"):
        lines.append("- 数据模式：样例数据（未调用 1688 接口）")
    else:
        lines.append(f"- 数据模式：真实数据（来源：{payload.get('collect_source') or 'rpa'}）")
    lines.append("\n### 人工复核说明")
    lines.append("导出后请在表格中更新 `人工复核状态`、`人工复核备注`、`微信小店铺货建议`。")
    lines.append("列表字段只做初筛，运费、品退率、发货率等关键字段仍需进入详情页核验。")
    return "\n".join(lines)
