#!/usr/bin/env python3
"""tag_collect 最小回归测试：字段、导出、Web token、真实模式开关、开发样例采集。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import types
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from capabilities.tag_collect import service, web
from capabilities.tag_collect.service import (
    DETAIL_ONLY_FIELDS,
    DETAIL_VERIFICATION_PENDING,
    EXPORT_COLUMNS,
    Product,
    REFERENCE_EXPORT_LABELS,
    build_filter_plan,
    build_library_filter_plan,
    build_queries,
    build_verification_queue,
    export_xlsx,
    friendly_collect_error,
    get_library_capabilities,
    get_library_filter_coverage,
    get_library_filter_schema,
    get_numbered_export_columns,
    metric_bucket,
    product_to_export_row,
    parse_input,
    run_tag_collect,
    verify_run_details,
    collect_error_state,
)
from _errors import ServiceError


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _get_bytes(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.headers, response.read()


def _native_has(native_filters, label):
    for item in native_filters:
        values = [
            item.get("label", ""),
            item.get("tag", ""),
            *(item.get("aliases") or []),
            *(item.get("texts") or []),
        ]
        if label in values or label in str(item.get("label", "")):
            return True
    return False


class _temporary_tag_data_dir:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = os.environ.get("TAG_COLLECT_DATA_DIR")
        self.old_cache = service._DATA_DIR_CACHE
        os.environ["TAG_COLLECT_DATA_DIR"] = self.tmp.name
        service._DATA_DIR_CACHE = None
        return Path(self.tmp.name)

    def __exit__(self, exc_type, exc, tb):
        if self.old_env is None:
            os.environ.pop("TAG_COLLECT_DATA_DIR", None)
        else:
            os.environ["TAG_COLLECT_DATA_DIR"] = self.old_env
        service._DATA_DIR_CACHE = self.old_cache
        self.tmp.cleanup()


def _xlsx_first_row_labels(workbook: zipfile.ZipFile, sheet_index: int = 1):
    xml = workbook.read(f"xl/worksheets/sheet{sheet_index}.xml")
    root = ET.fromstring(xml)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    row = root.find("m:sheetData/m:row", ns)
    if row is None:
        return []
    values = []
    for cell in row.findall("m:c", ns):
        inline = cell.find("m:is/m:t", ns)
        value = cell.find("m:v", ns)
        values.append((inline.text if inline is not None else value.text if value is not None else "") or "")
    return values


def test_category_dictionary_expanded_tree():
    tree = service.TAG_CATEGORY_TREE
    dictionary = service.CATEGORY_DICTIONARY
    root = "女装、男装、内衣"
    appliance_root = "家用电器、数码电脑"
    daily_root = "日用餐厨、居家日用"
    _assert(dictionary["status"] == "synced_from_1688_homepage_left_nav", "类目字典应来自1688首页左侧导航同步")
    _assert(len(tree) >= 10, "类目字典应覆盖1688首页左侧主要组合一级类目")
    for parent in {
        root,
        "配饰、鞋、箱包",
        "运动户外、玩具童装",
        "办公文化、宠物园艺",
        "美妆个护、收纳清洁",
        "食品酒水、餐饮生鲜",
        daily_root,
        appliance_root,
        "家装灯饰、家纺家饰",
        "汽车用品、工业用品",
    }:
        _assert(parent in tree, f"类目字典应包含一级类目：{parent}")
    _assert("女装/女士精品" not in tree, "1688左侧类目模式不应混入旧的女装/女士精品一级类目")
    _assert("鞋靴" not in tree, "1688左侧类目模式不应混入旧的鞋靴一级类目")
    _assert("箱包皮具" not in tree, "1688左侧类目模式不应混入旧的箱包皮具一级类目")
    _assert("新中式" in tree[root], "女装组合入口下应包含新中式二级分组")
    _assert("下装裤" in tree[root], "女装组合入口下应包含下装裤二级分组")
    _assert("内裤" in tree[root], "女装组合入口下应包含内裤二级分组")
    _assert("男士平角裤" in tree[root]["内裤"], "组合入口>内裤下应包含男士平角裤三级类目")
    _assert("男士内裤" not in tree[root]["内裤"], "当前1688首页没有男士内裤三级时不应在本地伪造")
    _assert("汉服套装" in tree[root]["新中式"], "组合入口>新中式下应包含汉服套装三级类目")
    _assert("生活电器" in tree[appliance_root], "家用电器组合入口下应包含生活电器二级类目")
    _assert("厨房电器" in tree[appliance_root], "家用电器组合入口下应包含厨房电器二级类目")
    _assert("雨衣雨披" in tree[daily_root]["遮阳防雨"], "居家日用组合入口下应包含1688真实雨衣雨披三级类目")


def test_field_numbers():
    fields = get_numbered_export_columns()
    numbers = [field["number"] for field in fields]
    keys = {field["key"] for field in fields}
    labels = [label for _, label in EXPORT_COLUMNS]
    _assert(numbers[0] == "1.1", "字段编号应从 1.1 开始")
    _assert(numbers[-1] == "10.13", "字段编号应到 10.13 结束")
    _assert(len(labels) == 84, "导出字段应包含店雷达37列基线和项目扩展字段的84列")
    _assert(labels == REFERENCE_EXPORT_LABELS, "导出字段顺序应与用户确认的附件表头一致")
    _assert(labels[:37] == service.DIANLEIDA_REFERENCE_EXPORT_LABELS, "导出前37列必须严格对齐店雷达附件表头")
    _assert(labels[27] == "店铺链接(点击下方链接可跳转)", "店铺链接应保留在店雷达原表第28列")
    _assert(labels[28] == "主图链接(点击下方链接可跳转)", "主图链接应保留在店雷达原表第29列")
    _assert(labels[30] == "SKU数量", "SKU数量应保留在店雷达原表第31列")
    _assert("product_refund_rate" in keys, "字段应包含品退率")
    _assert("shipment_rate" in keys, "字段应包含发货率")
    _assert("wholesale_shipping_fee" in keys, "字段应包含批发运费")
    _assert("product_rating" in keys, "字段应包含商品星级")
    _assert("review_tags" in keys, "字段应包含评价标签")
    _assert("shop_url" in keys, "字段应包含店铺链接")
    _assert("sku_count" in keys, "字段应包含SKU数量")
    _assert("good_rate_bucket" in keys, "字段应包含好评率区间")
    _assert("data_mode" in keys, "字段应包含数据模式")
    _assert("data_truth_note" in keys, "字段应包含数据真实性说明")
    _assert("shipment_rate_bucket" in keys, "字段应包含发货率区间")


def test_filter_plan_splits_native_and_metric_tags():
    config = parse_input(
        categories="女装、男装、内衣>新中式>汉服套装",
        tags="微信小店,一件代发,48小时发货,好评率>=90%,评论数30-99,防晒",
        sample_data=True,
    )
    plan = build_filter_plan(config)
    queries = build_queries(config)
    query_text = " ".join(queries)
    post_tags = {item["tag"] for item in plan["post_filters"]}
    category_filters = plan.get("category_filters", [])
    _assert(category_filters, "类目应进入1688左侧类目点击计划")
    _assert(category_filters[0]["mode"] == "category_path", "类目筛选应使用 category_path 点击模式")
    _assert(category_filters[0]["texts"] == ["女装、男装、内衣", "新中式", "汉服套装"], "类目应按1688一级/二级/三级拆分点击")
    _assert(_native_has(plan["native_filters"], "一件代发"), "一件代发应进入1688原生筛选")
    _assert(_native_has(plan["native_filters"], "48小时发货"), "48小时发货应进入1688原生筛选")
    _assert("好评率>=90%" in post_tags, "好评率区间应进入后置指标筛选")
    _assert("评论数30-99" in post_tags, "评论数区间应进入后置指标筛选")
    _assert("防晒" in query_text, "场景词防晒应保留为搜索词")
    _assert("汉服套装" not in queries, "只有类目时不应把类目叶子直接放进搜索框")
    _assert("一件代发" not in query_text, "原生筛选不应拼进搜索词")
    _assert("48小时发货" not in query_text, "原生筛选不应拼进搜索词")

    category_only = parse_input(categories="女装、男装、内衣>新中式>汉服套装", sample_data=True)
    _assert(build_queries(category_only) == [""], "只选类目时应由RPA点击类目，不应生成类目搜索词")


def test_rpa_category_ignores_stale_source_url():
    config = parse_input(
        categories="女装、男装、内衣>新中式>汉服套装",
        source_urls="https://s.1688.com/selloffer/offer_search.htm?keywords=%E5%86%85%E8%A1%A3",
        library_filters={
            "source_urls": "https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%97%A7%E9%A1%B5%E9%9D%A2",
        },
        sample_data=False,
        collect_source="rpa",
    )
    _assert(config.source_urls == [], "自动批量+已选类目时应忽略URL输入框残留，避免跳过1688左侧类目点击")

    calls = []

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
    ):
        calls.append({
            "query": query,
            "source_url": source_url,
            "category_filters": category_filters or [],
        })
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        return {
            "products": [{
                "id": "stale-url-category-1",
                "title": "汉服套装 类目点击测试商品",
                "price": "19.9",
                "image": "",
                "url": "https://detail.1688.com/offer/1234567892.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%B1%89%E6%9C%8D%E5%A5%97%E8%A3%85",
                "final_url": "https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%B1%89%E6%9C%8D%E5%A5%97%E8%A3%85",
                "expected_path": category_path,
                "matched_path": category_path,
                "expected_depth": 3,
                "matched_depth": 3,
                "category_steps": [
                    {"depth": 1, "expected_text": "女装、男装、内衣", "mode": "hover", "status": "matched"},
                    {"depth": 2, "expected_text": "新中式", "mode": "hover", "status": "matched"},
                    {"depth": 3, "expected_text": "汉服套装", "mode": "click", "status": "clicked"},
                ],
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        result = run_tag_collect(config)
        _assert(result["success"], "残留URL被忽略后应能按类目采集成功")
        _assert(len(calls) == 1, "只选一个类目时应只发起一个RPA类目任务")
        _assert(calls[0]["source_url"] == "", "类目RPA任务不应携带残留source_url")
        _assert(calls[0]["query"] == "", "只有类目时不应把类目词放进搜索框")
        _assert(calls[0]["category_filters"], "类目RPA任务必须携带category_filters")
        _assert(calls[0]["category_filters"][0]["category_path"] == "女装、男装、内衣>新中式>汉服套装", "RPA应接收当前选中的1688类目路径")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_library_filter_contract_and_mapping():
    schema = get_library_filter_schema()
    capabilities = get_library_capabilities()
    coverage = get_library_filter_coverage()
    section_keys = {section["key"] for section in schema}
    removed_filter_keys = {
        "selection_modes",
        "order_growth_7d",
        "purchase_concentration_7d",
        "repurchase_rate",
        "certificates",
        "listed_time",
        "product_marks",
        "seller_locations",
    }
    _assert("selection_mode" not in section_keys, "用户确认不需要的选品模式分组不应出现在筛选契约")
    _assert("advanced" in section_keys, "店雷达筛选契约应包含高级筛选")
    _assert("sales" in section_keys, "店雷达筛选契约应包含销售信息")
    _assert("product" in section_keys, "店雷达筛选契约应包含商品信息")
    _assert("seller" in section_keys, "店雷达筛选契约应包含卖家信息")
    _assert("review" in section_keys, "店雷达筛选契约应包含评价口碑")
    _assert(capabilities["implemented"], "能力清单应标记已接入项目")
    schema_field_keys = {
        field["key"]
        for section in schema
        for field in section.get("fields", [])
    }
    coverage_keys = {item["field_key"] for item in coverage}
    capability_text = json.dumps(capabilities, ensure_ascii=False)
    _assert(schema_field_keys == coverage_keys, "筛选覆盖状态应覆盖 schema 中每一个字段")
    _assert(not (schema_field_keys & removed_filter_keys), "用户确认不需要的筛选字段不应下发到前端 schema")
    _assert(not (coverage_keys & removed_filter_keys), "用户确认不需要的筛选字段不应出现在覆盖状态")
    for expected_key in {
        "platform_service_filters",
        "fulfillment_service_filters",
        "seller_location_regions",
        "seller_features",
        "business_modes",
        "merge_suppliers",
        "product_rating",
        "good_rate",
        "comment_count",
        "review_tags",
    }:
        _assert(expected_key in schema_field_keys, f"截图新增筛选字段应下发到前端 schema：{expected_key}")
    _assert("选品模式转译" not in capability_text, "能力清单不应保留已移除的选品模式入口")
    _assert("7日增长率" not in capability_text, "能力清单不应保留已移除的7日增长率筛选")
    _assert("采购集中率" not in capability_text, "能力清单不应保留已移除的采购集中率筛选")
    _assert("商品标识" not in capability_text, "能力清单不应保留已移除的商品标识筛选")
    _assert(any(item["status"] == "detail_required" for item in coverage), "覆盖状态应包含详情核验字段")
    _assert(any(item["status"] == "reserved" for item in coverage), "覆盖状态应包含预留字段")

    library_filters = {
        "category_paths": ["女装、男装、内衣>新中式>汉服套装"],
        "search_keyword": "连衣裙",
        "match_type": "模糊匹配",
        "selection_modes": ["源头工厂", "无货源选品"],
        "downstream_platforms": ["抖店", "拼多多"],
        "sales_regions": ["华南"],
        "cross_border_supply": True,
        "authorized_own_brand": True,
        "sales_orders_min": "100",
        "wholesale_price_max": "50",
        "repurchase_rate_min": "10",
        "product_marks": ["新品"],
        "certificates": ["CE"],
        "listed_time": "近30天",
        "seller_locations": ["广东"],
        "fulfillment_times": ["48小时"],
        "waybill_support": ["抖音", "拼多多"],
        "rights_protection": ["7天包退货", "赠运费险"],
        "platform_service_filters": ["严选", "分销严选", "退货包运费", "7天无理由退货", "24H发货", "48H发货"],
        "fulfillment_service_filters": ["官方物流", "密文面单", "晚揽必赔", "24H支揽率", "48H支揽率"],
        "dropship_rights": ["一件代发", "一件代发包邮"],
        "seller_location_regions": ["广东"],
        "seller_features": ["源头工厂"],
        "business_modes": ["生产加工"],
        "merge_suppliers": True,
        "product_rating_min": "4.8",
        "good_rate_min": "95",
        "comment_count_min": "40",
        "review_tags": ["客服态度超好", "质感不错"],
        "seller_member_types": ["实力商家"],
        "order_growth_7d_min": "20",
    }
    config = parse_input(sample_data=True, library_filters=library_filters)
    plan = build_filter_plan(config)
    native_keys = {item["key"] for item in plan["native_filters"]}
    post_fields = {item["field"] for item in plan["post_filters"]}
    post_tags = {item["tag"] for item in plan["post_filters"]}
    reserved_keys = {item["field_key"] for item in plan["library_reserved_fields"]}
    _assert(config.categories == ["女装、男装、内衣>新中式>汉服套装"], "library_filters 类目应进入 config.categories")
    _assert(config.keywords == ["连衣裙"], "library_filters 关键词应进入 config.keywords")
    _assert(not (set(config.library_filters) & removed_filter_keys), "旧 payload 中已移除字段应在服务端被清洗")
    _assert(not _native_has(plan["native_filters"], "新品"), "已移除的商品标识不应继续转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "一件代发包邮"), "代发权益应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "一件代发"), "一件代发应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "严选"), "严选应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "分销严选"), "分销严选应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "退货包运费"), "退货包运费应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "7天无理由退货"), "7天无理由退货应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "24H发货"), "24H发货应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "48H发货"), "48H发货应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "官方物流"), "官方物流应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "密文面单"), "密文面单应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "晚揽必赔"), "晚揽必赔应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "24H支揽率"), "24H支揽率应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "48H支揽率"), "48H支揽率应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "合并供应商"), "合并供应商应转译为原生筛选")
    _assert(any(key.startswith("seller_location_") for key in native_keys), "所在地应生成下拉原生筛选计划")
    _assert(any(key.startswith("seller_feature_") for key in native_keys), "商家特色应生成下拉原生筛选计划")
    _assert(any(key.startswith("business_mode_") for key in native_keys), "经营模式应生成下拉原生筛选计划")
    _assert(_native_has(plan["native_filters"], "48小时发货"), "发货时间应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "支持抖音面单"), "面单支持应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "支持拼多多面单"), "面单支持应转译为原生筛选")
    _assert("orders_30d" in post_fields, "销售订单数应进入后置指标筛选")
    _assert("wholesale_price" in post_fields, "批发价应进入后置指标筛选")
    _assert("product_rating" in post_fields, "商品星级应进入详情后置筛选")
    _assert("good_rate" in post_fields, "好评率应进入详情后置筛选")
    _assert("comment_count" in post_fields, "评价数应进入详情后置筛选")
    _assert("客服态度超好" in post_tags, "评价标签应进入详情文本匹配筛选")
    _assert("质感不错" in post_tags, "评价标签应支持多选详情文本匹配筛选")
    _assert("repurchase_rate" not in post_fields, "已移除的复购率不应进入筛选计划")
    _assert("sales_regions" in reserved_keys, "显式选择的预留字段应进入 reserved_fields")
    _assert("order_growth_7d" not in reserved_keys, "已移除的7日订单增长率不应标记为预留字段")
    _assert("certificates" not in reserved_keys, "已移除的资质证书不应标记为预留字段")
    _assert("seller_locations" not in reserved_keys, "已移除的卖家所属地不应标记为预留字段")

    direct_plan = build_library_filter_plan(library_filters)
    _assert(direct_plan["results"], "店雷达字段映射应有结果记录")
    _assert(direct_plan["reserved_fields"], "预留字段应进入 reserved_fields")

    default_plan = build_library_filter_plan({
        "company_type": "不限",
        "match_type": "模糊匹配",
        "stat_period": "近30天",
        "sort_by": "推荐分",
    })
    _assert(not default_plan["native_filters"], "公司类型不限不应进入 1688 原生筛选")
    _assert(not default_plan["post_filters"], "默认筛选元数据不应进入后置指标筛选")
    _assert(not default_plan["results"], "公司类型不限和默认元数据不应生成字段映射结果")
    _assert(not default_plan["reserved_fields"], "默认统计周期/排序不应污染预留字段记录")


def test_metric_bucket_ranges():
    _assert(metric_bucket("good_rate", "96.2%") == ">=90%", "好评率应按 >=90% 分桶")
    _assert(metric_bucket("good_rate", "85%") == "80%-90%", "好评率应按 80%-90% 分桶")
    _assert(metric_bucket("good_rate", "75%") == "70%-80%", "好评率应按 70%-80% 分桶")
    _assert(metric_bucket("good_rate", "69%") == "<70%", "好评率应按 <70% 分桶")
    _assert(metric_bucket("shipment_rate", "93.4%") == "90%-95%", "发货率应细化分桶")
    _assert(metric_bucket("product_refund_rate", "6.8%") == "5%-10%", "品退率应细化分桶")
    _assert(metric_bucket("comment_count", "86") == "30-99", "评论数应细化分桶")


def test_export_xlsx_and_exclude():
    config = parse_input(
        categories="女装、男装、内衣,家用电器、数码电脑",
        tags="微信小店,一件代发,48小时发货",
        exclude_tags="红海",
        sample_data=True,
        output_format="xlsx",
        max_queries=999,
        max_items_per_query=999,
    )
    result = run_tag_collect(config)
    data = result["data"]
    _assert(result["success"], "样例采集应成功")
    _assert(data["row_count"] == 2, "exclude_tags=红海 应过滤红海样例商品")
    _assert(data["output_path"].endswith(".xlsx"), "默认导出应为 xlsx")
    for row in data["top_items"]:
        for key in DETAIL_ONLY_FIELDS:
            _assert(
                row.get(key) == DETAIL_VERIFICATION_PENDING,
                f"{key} 在详情页核验前应保持待核验占位",
            )
        _assert(row.get("verification_status") == "unverified", "导出前应保持未核验状态")

    with zipfile.ZipFile(data["output_path"]) as workbook:
        names = workbook.namelist()
        sheets = [name for name in names if name.startswith("xl/worksheets/sheet")]
        _assert(len(sheets) == 7, "样例 xlsx 应包含 7 个工作表，额外包含样例说明")
        workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
        result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        field_xml = workbook.read("xl/worksheets/sheet2.xml").decode("utf-8")
        result_labels = _xlsx_first_row_labels(workbook, 1)
        _assert(result_labels == REFERENCE_EXPORT_LABELS, "选品结果 sheet 表头应严格等于店雷达37列基线加项目扩展字段")
        _assert("选品结果" in workbook_xml, "应包含选品结果 sheet")
        _assert("字段说明" in workbook_xml, "应包含字段说明 sheet")
        _assert("标签配置" in workbook_xml, "应包含标签配置 sheet")
        _assert("异常复核" in workbook_xml, "应包含异常复核 sheet")
        _assert("核验记录" in workbook_xml, "应包含核验记录 sheet")
        _assert("筛选执行记录" in workbook_xml, "应包含筛选执行记录 sheet")
        _assert("样例说明" in workbook_xml, "样例导出必须包含样例说明 sheet")
        _assert("10.10" in field_xml, "字段说明应包含 10.10 核验状态")
        _assert("数据真实性说明" in field_xml, "字段说明应包含数据真实性说明")
        _assert("品退率" in field_xml, "字段说明应包含品退率")
        _assert("发货率" in field_xml, "字段说明应包含发货率")
        for label in REFERENCE_EXPORT_LABELS:
            _assert(label in result_xml, f"选品结果表头应包含附件基线字段：{label}")
        _assert("开发样例" in result_xml, "样例结果行必须标注开发样例")
        _assert("禁止作为真实选品" in result_xml, "样例结果行必须标注不能作为真实选品依据")
        filter_xml = workbook.read("xl/worksheets/sheet6.xml").decode("utf-8")
        _assert("一件代发" in filter_xml, "筛选执行记录应包含一件代发")
        _assert("sample_skipped" in filter_xml, "样例模式应标记原生筛选未执行")
        sample_notice_xml = workbook.read("xl/worksheets/sheet7.xml").decode("utf-8")
        _assert("未访问 1688" in sample_notice_xml, "样例说明 sheet 应明确未访问 1688")


def test_export_normalizes_legacy_sample_rows():
    config = parse_input(categories="女装、男装、内衣", tags="微信小店", sample_data=True)
    legacy_row = product_to_export_row(
        Product(
            id="legacy-sample",
            title="旧样例批次商品",
            price="19.9",
            image="",
            url="https://detail.1688.com/offer/legacy-sample.html",
            stats={"last30DaysSales": 30},
        ),
        "女装、男装、内衣",
        config,
    )
    legacy_row.pop("data_mode", None)
    legacy_row.pop("data_truth_note", None)
    legacy_row["list_source"] = "sample"
    legacy_excluded = dict(legacy_row)
    legacy_excluded["item_id"] = "legacy-sample-excluded"
    legacy_excluded["filter_verification_status"] = "filtered_out"
    legacy_excluded["filter_verification_note"] = "样例筛除"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "legacy_sample.xlsx")
        export_xlsx(
            [legacy_row],
            output_path,
            {
                "run_id": "legacy-sample",
                "sample_data": False,
                "collect_source": "rpa",
                "filter_excluded_rows": [legacy_excluded],
            },
        )
        with zipfile.ZipFile(output_path) as workbook:
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            config_xml = workbook.read("xl/worksheets/sheet3.xml").decode("utf-8")
            failed_xml = workbook.read("xl/worksheets/sheet4.xml").decode("utf-8")
            _assert("样例说明" in workbook_xml, "含旧样例行的导出也必须追加样例说明")
            _assert("开发样例" in result_xml, "旧样例行缺少新字段时导出前必须回填开发样例")
            _assert("禁止作为真实选品" in result_xml, "旧样例行必须回填真实性说明")
            _assert("混合数据" in config_xml, "工作簿级配置应提示当前导出含样例行")
            _assert("样例剔除" in failed_xml, "旧样例筛除行不能混同为真实系统剔除")


def test_export_marks_unknown_source_rows_as_needing_verification():
    unknown_row = {
        "item_id": "legacy-unknown",
        "title": "旧批次来源缺失商品",
        "url": "https://detail.1688.com/offer/legacy-unknown.html",
        "wechat_shop_suggestion": "可铺",
        "verification_status": "unverified",
        "manual_review_status": "待复核",
    }
    real_row = {
        "item_id": "legacy-real",
        "title": "旧批次真实来源商品",
        "url": "https://detail.1688.com/offer/legacy-real.html",
        "list_source": "rpa",
        "wechat_shop_suggestion": "可铺",
        "verification_status": "unverified",
        "manual_review_status": "待复核",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "truth_modes.xlsx")
        export_xlsx(
            [unknown_row, real_row],
            output_path,
            {
                "run_id": "truth-modes",
                "sample_data": False,
            },
        )
        with zipfile.ZipFile(output_path) as workbook:
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            config_xml = workbook.read("xl/worksheets/sheet3.xml").decode("utf-8")
            _assert("来源未知/需核验" in result_xml, "无法证明真实来源的旧行不能标为真实数据")
            _assert("禁止直接作为真实选品" in result_xml, "来源未知行必须说明不能直接铺货")
            _assert("真实数据" in result_xml, "真实页面来源行仍应标为真实数据")
            _assert("混合数据（含来源未知/需核验行）" in config_xml, "工作簿级配置应提示含来源未知行")


def test_rpa_detail_extracts_structured_fields_without_bad_shop_name():
    html = """
    <html>
      <body>
        店铺 回头率 35%
        运费 首重8元续重4元
        2件起批 品退率 1.8% 发货率 98.6% 24小时揽收率 96%
        48小时内发货 一件代发 7天无理由
        规格数量: 18 SKU 收藏客户 200+ 诚信通 5年 生产厂家 密文面单
      </body>
      <script>
        window.__INIT__ = {"shopName":"义乌童雨户外用品厂","skuCount":"18","shipmentRate":"98.6%"};
      </script>
    </html>
    """
    proc = subprocess.run(
        ["node", str(Path(__file__).resolve().parent / "rpa_detail.mjs")],
        input=json.dumps({"test_html": html}, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(proc.returncode == 0, f"详情抽取脚本应执行成功：{proc.stderr or proc.stdout}")
    payload = json.loads((proc.stdout or "").splitlines()[-1])
    fields = payload.get("fields", {})
    _assert(fields.get("shop_name") == "义乌童雨户外用品厂", "店铺名应优先取结构化字段，不能误抓回头率")
    _assert(fields.get("shop_name") != "回头率", "店铺名不能被抽成回头率")
    _assert(fields.get("wholesale_shipping_fee") == "首重8元续重4元", "运费不应吞入后续品退率/发货率文本")
    _assert(fields.get("product_refund_rate") == "1.8%", "详情抽取应提取品退率")
    _assert(fields.get("shipment_rate") == "98.6%", "详情抽取应提取发货率")
    _assert(fields.get("sku_count") == "18", "详情抽取应提取 SKU 数量")
    _assert(fields.get("waybill_support") == "密文面单", "面单字段不应吞入 HTML/script 文本")


def test_security_verification_error_message():
    message = friendly_collect_error(
        Exception(
            "security_verification_required: 已等待你手动处理 1688 安全验证，"
            "但当前页面仍停留在滑块/验证码校验。系统不会绕过或自动破解验证。"
        )
    )
    _assert("安全验证" in message or "滑块" in message, "滑块验证应给出明确人工处理提示")
    state = collect_error_state(ServiceError(message))
    _assert(state["code"] == "security_verification_required", "滑块验证应返回结构化风控状态")
    _assert(state["action"] == "manual_handoff", "滑块验证应进入人工接管")
    _assert(state["retryable"] is False, "滑块验证不应提示自动重试")
    _assert("不会绕过" in message or "不会继续采集" in message, "提示应明确不会绕过验证码")


def test_cdp_security_error_context():
    error = ServiceError(
        "security_verification_required: 1688 在已连接的真实 Chrome 中触发了安全滑块/验证码校验。"
    )
    error.data = {
        "cdp": True,
        "page_url": "https://s.1688.com/selloffer/offer_search.htm?__tmd__/punish",
        "source": "1688_search_page",
    }
    state = collect_error_state(error)
    _assert(state["code"] == "security_verification_required", "CDP 风控仍应返回结构化风控状态")
    _assert(state["runtime"]["use_cdp"] is True, "CDP 风控应保留真实 Chrome 上下文")
    _assert("不是 testin" in state["suggestion"] or "真实 Chrome" in state["suggestion"], "CDP 风控提示应明确不是临时浏览器问题")
    _assert("punish" in state["runtime"]["page_url"], "CDP 风控应保留拦截页 URL")


def test_navigation_timeout_error_context():
    error = ServiceError("navigation_timeout: 1688 页面加载超时，未生成任何数据。")
    error.data = {
        "cdp": True,
        "page_url": "https://s.1688.com/selloffer/offer_search.htm?keywords=%E9%9B%A8%E8%A1%A3",
        "source": "1688_search_page",
    }
    state = collect_error_state(error)
    _assert(state["code"] == "navigation_timeout", "页面加载超时应返回结构化超时状态")
    _assert(state["retryable"] is False, "真实页面超时不应提示自动重试")
    _assert(state["action"] == "manual_open_page", "页面加载超时应要求人工打开页面确认")
    _assert("真实 Chrome" in state["suggestion"], "CDP 超时提示应指向真实 Chrome 人工确认")


def test_cdp_context_unsupported_error_context():
    error = ServiceError(
        "cdp_context_unsupported: 真实 Chrome CDP 端口能连接，但当前调试端点不支持 Playwright 初始化上下文。"
    )
    error.data = {
        "cdp": True,
        "cdp_url": "http://127.0.0.1:9222",
        "source": "chrome_cdp",
        "low_level_error": "Browser.setDownloadBehavior: Browser context management is not supported.",
    }
    state = collect_error_state(error)
    _assert(state["code"] == "cdp_context_unsupported", "CDP 上下文不可用应返回专用错误码")
    _assert(state["retryable"] is False, "CDP 上下文不可用不应提示自动重试")
    _assert(state["action"] == "restart_chrome_debug", "CDP 上下文不可用应要求重启调试 Chrome")
    _assert("9222" in state["suggestion"], "提示应说明排查 9222 端口占用")


def test_dirty_product_title_is_cleaned():
    config = parse_input(categories="日用餐厨、居家日用", tags="微信小店", sample_data=True)
    dirty = Product(
        id="dirty-1",
        title="Ã¥ÂÂ¿Ã§Â«Â¥Ã©ÂÂ¨Ã¨Â¡Â£",
        price="9.9",
        image="",
        url="https://detail.1688.com/offer/dirty-1.html",
        stats={},
    )
    row = product_to_export_row(dirty, "雨衣", config)
    _assert(row["title"] == "", "疑似乱码标题不应进入导出标题；真实采集入口会进一步过滤该商品")


def test_search_keyword_encoding_error_context():
    error = ServiceError(
        "search_keyword_encoding_error: 1688 搜索词校验失败：期望搜索「雨衣」，"
        "但页面没有确认到原始中文关键词，或出现了疑似乱码。"
    )
    error.data = {
        "cdp": True,
        "page_url": "https://www.1688.com/",
        "source": "1688_search_page",
        "search_keyword_state": {
            "input_values": ["é›¨è¡£"],
            "mojibake_values": ["é›¨è¡£"],
        },
    }
    state = collect_error_state(error)
    _assert(state["code"] == "search_keyword_encoding_error", "搜索词乱码应返回结构化错误码")
    _assert(state["retryable"] is False, "搜索词乱码不应继续自动重试")
    _assert(state["action"] == "manual_open_page", "搜索词乱码应要求人工打开页面确认")
    _assert("避免按错误关键词导出数据" in state["suggestion"], "搜索词乱码应明确停止导出错误数据")


def test_rpa_collect_uses_human_search_not_keyword_url():
    script = Path(__file__).with_name("rpa_collect.mjs").read_text(encoding="utf-8")
    _assert("submitHumanSearch" in script, "真实采集应通过页面搜索框提交关键词")
    _assert("https://www.1688.com/" in script, "关键词模式应先打开 1688 首页")
    _assert("hasCategoryNavigation" in script, "存在类目时应优先执行1688左侧类目导航")
    _assert("} else if (hasCategoryNavigation)" in script, "类目导航应排在关键词搜索框提交之前")
    _assert(script.index("} else if (hasCategoryNavigation)") < script.index("} else if (query)"), "类目路径不能先进入搜索框搜索")
    _assert("offer_search.htm?keywords=${encodeURIComponent(query)}" not in script, "关键词模式不应再拼中文 URL 参数")
    _assert("search_keyword_encoding_error" in script, "真实采集应检测搜索词乱码并阻断")
    _assert("search_results_not_loaded" in script, "真实采集应阻断搜索页无商品卡片的空结果")
    _assert("category_navigation_not_loaded" in script, "真实采集应区分类目导航未进入商品列表的失败")
    _assert("offerId=" in script, "真实采集应兼容新版 1688 搜索页 offerId 链接")
    _assert("offerIds=" in script, "真实采集应兼容找相似等 offerIds 链接")
    _assert("detail.m.1688.com/page/index.html" in script, "真实采集应兼容移动端详情链接")
    _assert("search-offer-wrapper" in script, "真实采集应识别新版搜索结果卡片")
    _assert("collectProductsAcrossPages" in script, "真实采集数量不足时应进入翻页采集流程")
    _assert("TAG_COLLECT_RPA_MAX_PAGES" in script, "翻页采集应支持最大页数限制")
    _assert("applyCategoryFilters" in script, "真实采集应支持按1688左侧类目点击")
    _assert("category_path" in script, "类目筛选不应退化为关键词搜索")
    _assert("categoryNeedleSets" in script, "类目点击应支持把本地类目名映射到1688页面可见入口")
    _assert("replace(/[\\uE000-\\uF8FF]/g" in script, "类目点击应忽略1688首页图标字体，避免组合一级类目误判不存在")
    _assert("replace(/[\\s、，,\\/／|｜·•\\-]+/g" in script, "类目点击应忽略顿号/斜杠等分隔符，匹配1688组合一级类目")
    _assert("index < needleSets.length - 1" in script, "三级类目应先 hover 一级/二级，不能点击二级后跳成关键词结果")
    _assert("followCategoryClick" in script, "类目点击后应跟随新页或URL变化")
    _assert("collectCategoryDiagnostics" in script, "类目点击失败应返回页面可见类目诊断")
    _assert("filter_results: categoryResults" in script, "类目点击后无商品卡片时也应返回类目执行记录")
    _assert("deferred_navigation" not in script, "多级类目不能用父级类目 href 兜底跳到关键词结果页")
    _assert("navigated_parent_href" not in script, "多级类目缺二级时不应跳父级关键词结果页")
    _assert('"男士内裤":' not in script, "RPA 不应为当前1688类目不存在的男士内裤保留模糊别名")
    _assert("raw.split(\"、\")" not in script, "组合一级类目不能按顿号拆开，否则会误点单独父类")
    _assert("fallback_keyword" not in Path(__file__).with_name("service.py").read_text(encoding="utf-8"), "类目失败不应降级为关键词搜索")
    _assert("manual_page_ambiguous" in script, "人工当前页模式存在多个1688页签时应阻断，不能静默读错页")
    _assert("noDefaults: true" in script, "CDP 连接应保留浏览器默认下载/媒体设置，避免真实 Chrome 初始化失败")
    _assert("browser.close().catch" in script, "人工当前页模式读取后应断开 Playwright CDP 连接")


def test_web_defaults_to_automatic_batch_collect():
    html = web.HTML_PAGE
    _assert('value="rpa" checked' in html, "Web 主入口应默认自动批量采集")
    _assert('value="url_direct" />' not in html, "URL 直连不应作为普通测试入口展示")
    _assert('value="api" />' not in html, "AK/API 不应作为普通测试入口展示")
    _assert('id="startChromeBtn"' in html, "页面应提供启动采集 Chrome 按钮")
    _assert('id="refreshChromeBtn"' in html, "页面应提供刷新采集 Chrome 状态按钮")
    _assert('id="readCurrentPageBtn"' in html, "页面应保留当前页兜底读取按钮")
    _assert('/api/chrome/start' in html, "前端应调用 /api/chrome/start")
    _assert('/api/chrome/status' in html, "前端应调用 /api/chrome/status")
    _assert('if ($("collectSource")) $("collectSource").value = "rpa";' in html, "初始化/重置后应回到自动批量采集")
    _assert("请先运行 scripts/capabilities/tag_collect/start_chrome_debug.sh" not in html, "Web 文案不应再要求用户手动运行终端脚本")


def test_chrome_status_sets_cdp_env_and_reports_pages():
    original_url = os.environ.pop("TAG_COLLECT_CDP_URL", None)
    try:
      status = web._chrome_runtime_status()
      _assert(os.environ.get("TAG_COLLECT_CDP_URL") == web.DEFAULT_CDP_URL, "状态检查应为 RPA 子进程设置默认 CDP URL")
      _assert("pages" in status and isinstance(status["pages"], list), "Chrome 状态应返回页签列表字段")
      _assert("candidate_count" in status, "Chrome 状态应返回可读取 1688 页签数量")
      _assert("current_page" in status, "Chrome 状态应返回唯一当前页信息")
    finally:
      if original_url is None:
          os.environ.pop("TAG_COLLECT_CDP_URL", None)
      else:
          os.environ["TAG_COLLECT_CDP_URL"] = original_url


def test_1688_homepage_is_not_collectable_current_page():
    _assert(web._is_1688_url("https://www.1688.com/"), "1688 首页应识别为 1688 页签")
    _assert(not web._is_collectable_1688_url("https://www.1688.com/"), "1688 首页不能被当作可读取商品页")
    _assert(web._is_collectable_1688_url("https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%94%B6%E7%BA%B3%E7%9B%92"), "搜索结果页应可读取")
    _assert(web._is_collectable_1688_url("https://detail.1688.com/offer/123456789.html"), "商品详情页应可读取")


def test_start_chrome_script_canary_fallback_name():
    script = Path(__file__).with_name("start_chrome_debug.sh").read_text(encoding="utf-8")
    _assert('CHROME_APP_NAME="${CHROME_APP_NAME:-}"' in script, "Chrome app 名称不应提前固定为普通 Chrome")
    _assert('CHROME_APP_NAME="${CHROME_APP_NAME:-Google Chrome Canary}"' in script, "Canary fallback 应切换 app 名称")
    _assert('CHROME_APP_NAME="${CHROME_APP_NAME:-Google Chrome}"' in script, "普通 Chrome 路径可用时应使用普通 Chrome app 名称")


def test_search_results_not_loaded_error_context():
    error = ServiceError("search_results_not_loaded: 1688 已打开搜索页，但未在页面中发现商品列表链接。")
    error.data = {
        "cdp": True,
        "page_url": "https://s.1688.com/selloffer/offer_search.htm?keywords=%D3%EA%D2%C2",
        "source": "1688_search_page",
    }
    state = collect_error_state(error)
    _assert(state["code"] == "search_results_not_loaded", "搜索页无商品卡片应返回结构化错误码")
    _assert(state["retryable"] is False, "搜索页无商品卡片不应自动重试")
    _assert(state["action"] == "manual_open_page", "搜索页无商品卡片应要求人工确认页面")
    _assert("商品列表" in state["suggestion"], "提示应要求人工确认商品列表可见")


def test_category_navigation_not_loaded_error_context():
    error = ServiceError("category_navigation_not_loaded: 1688 已执行类目导航，但未进入可解析的商品列表页。")
    error.data = {
        "cdp": True,
        "page_url": "https://www.1688.com/",
        "source": "1688_category_navigation",
        "category_path": "女装、男装、内衣>新中式>汉服套装",
    }
    state = collect_error_state(error)
    _assert(state["code"] == "category_navigation_not_loaded", "类目导航未进入列表应返回结构化错误码")
    _assert(state["retryable"] is False, "类目导航失败不应自动高频重试")
    _assert(state["action"] == "manual_open_page", "类目导航失败应要求人工确认页面")
    _assert("类目" in state["suggestion"] and "商品列表" in state["suggestion"], "提示应说明人工确认类目商品列表")


def test_invalid_category_path_blocks_before_rpa():
    try:
        parse_input(
            categories="女装、男装、内衣>内裤>男士内裤",
            sample_data=False,
            collect_source="rpa",
        )
    except ServiceError as exc:
        state = collect_error_state(exc)
        _assert(state["code"] == "category_path_invalid", "不存在于1688字典的类目应在提交前阻断")
        _assert("男士平角裤" in str(state.get("invalid_categories")), "非法类目应给出相近的真实1688类目建议")
    else:
        raise AssertionError("当前1688首页不存在的男士内裤三级不应通过类目校验")
    try:
        parse_input(
            categories="女装、男装、内衣>防晒衣>冰丝防晒衣",
            sample_data=False,
            collect_source="rpa",
        )
    except ServiceError as exc:
        state = collect_error_state(exc)
        _assert(state["code"] == "category_path_invalid", "旧运营类目路径应在提交前阻断")
        _assert("配饰、鞋、箱包>围巾/防晒>防晒衣" in str(state.get("invalid_categories")), "旧路径应建议当前1688真实防晒衣路径")
    else:
        raise AssertionError("旧运营类目防晒衣路径不应通过类目校验")


def test_category_navigation_failure_blocks_export():
    calls = []

    def fake_collect_products(query, limit, source_url="", native_filters=None, category_filters=None, manual_url_only=False, return_meta=False):
        calls.append({"query": query, "category_filters": category_filters or []})
        return {
            "products": [{
                "id": "category-fail-1",
                "title": "类目失败首轮商品不应直接采用",
                "price": "19.9",
                "image": "",
                "url": "https://detail.1688.com/offer/1234567890.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": "category_path_1",
                "label": "类目:女装、男装、内衣>新中式>汉服套装",
                "tag": "女装、男装、内衣>新中式>汉服套装",
                "status": "not_found",
                "source": "1688_category_navigation",
                "query": "",
                "page_url": "https://www.1688.com/",
                "message": "页面左侧类目中未找到：汉服套装",
                "expected_path": "女装、男装、内衣>新中式>汉服套装",
                "matched_path": "",
                "expected_depth": 2,
                "matched_depth": 0,
                "diagnostics": {"visible_category_texts": ["女装", "男装"]},
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        config = parse_input(
            categories="女装、男装、内衣>新中式>汉服套装",
            sample_data=False,
            collect_source="rpa",
            output_format="xlsx",
        )
        try:
            run_tag_collect(config)
        except ServiceError as exc:
            state = collect_error_state(exc)
            _assert(state["code"] == "category_navigation_not_loaded", "类目失败应返回类目导航阻断错误")
            _assert(state["filter_results"][0]["diagnostics"]["visible_category_texts"], "类目失败应保留页面可见类目诊断")
        else:
            raise AssertionError("类目 not_found 时不应导出商品")
        _assert(len(calls) == 1 and calls[0]["category_filters"], "类目失败不应再次用末级类目词发起关键词搜索")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_partial_category_navigation_blocks_export():
    calls = []

    def fake_collect_products(query, limit, source_url="", native_filters=None, category_filters=None, manual_url_only=False, return_meta=False):
        calls.append({"query": query, "category_filters": category_filters or []})
        return {
            "products": [{
                "id": "category-partial-1",
                "title": "类目部分命中首轮商品",
                "price": "29.9",
                "image": "",
                "url": "https://detail.1688.com/offer/1234567891.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": "category_path_1",
                "label": "类目:女装、男装、内衣>新中式>汉服套装",
                "tag": "女装、男装、内衣>新中式>汉服套装",
                "status": "partial_clicked",
                "source": "1688_category_navigation",
                "query": "",
                "page_url": "https://s.1688.com/selloffer/offer_search.htm?keywords=%E5%A5%B3%E8%A3%85",
                "message": "已点击部分类目并进入1688结果页，未找到下一层：连衣裙",
                "expected_path": "女装、男装、内衣>新中式>汉服套装",
                "matched_path": "女装",
                "expected_depth": 2,
                "matched_depth": 1,
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        config = parse_input(
            categories="女装、男装、内衣>新中式>汉服套装",
            sample_data=False,
            collect_source="rpa",
            output_format="xlsx",
        )
        try:
            run_tag_collect(config)
        except ServiceError as exc:
            state = collect_error_state(exc)
            _assert(state["code"] == "category_navigation_not_loaded", "部分类目命中应阻断导出")
            _assert("兜底读取不作为自动类目命中验收" in state["suggestion"], "提示不能引导用户改用关键词搜索")
        else:
            raise AssertionError("类目 partial_clicked 时不应导出商品")
        _assert(len(calls) == 1 and calls[0]["category_filters"], "部分类目命中不应再次用末级类目词发起关键词搜索")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_category_navigation_service_error_keeps_filter_context():
    calls = []

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
    ):
        calls.append({"query": query, "category_filters": category_filters or []})
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        error = ServiceError("category_navigation_not_loaded: 1688 类目导航未完成，未生成任何数据。")
        error.data = {
            "code": "category_navigation_not_loaded",
            "source": "1688_category_navigation",
            "cdp": True,
            "page_url": "https://www.1688.com/",
            "category_path": category_path,
            "diagnostics": {"visible_category_texts": ["童装童鞋", "母婴"]},
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "not_found",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://www.1688.com/",
                "message": "页面左侧类目中未找到：童套装",
                "diagnostics": {"visible_category_texts": ["童装童鞋", "母婴"]},
            }],
        }
        raise error

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        config = parse_input(
            categories="运动户外、玩具童装>童套>童套装",
            sample_data=False,
            collect_source="rpa",
            output_format="xlsx",
        )
        try:
            run_tag_collect(config)
        except ServiceError as exc:
            state = collect_error_state(exc)
            _assert(state["code"] == "category_navigation_not_loaded", "RPA 类目失败应透传结构化错误")
            _assert(state["filter_results"][0]["tag"] == "运动户外、玩具童装>童套>童套装", "RPA 类目失败应保留筛选执行记录")
            _assert(state["diagnostics"]["visible_category_texts"], "RPA 类目失败应保留页面诊断")
        else:
            raise AssertionError("RPA 类目失败不应降级导出")
        _assert(len(calls) == 1 and calls[0]["category_filters"], "RPA 类目失败不应再次用关键词搜索")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_multi_category_rpa_runs_are_isolated():
    calls = []

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        calls.append({
            "query": query,
            "source_url": source_url,
            "category_filters": category_filters or [],
        })
        item_id = f"cat-{len(calls)}"
        return {
            "products": [{
                "id": item_id,
                "title": f"{category_path} 测试商品",
                "price": "19.9",
                "image": "",
                "url": f"https://detail.1688.com/offer/{1000000000 + len(calls)}.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": category_filter.get("key", f"category_path_{len(calls)}"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": f"https://s.1688.com/selloffer/category-{len(calls)}.html",
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        config = parse_input(
            categories="女装、男装、内衣>新中式>汉服套装,日用餐厨、居家日用>遮阳防雨",
            sample_data=False,
            collect_source="rpa",
            output_format="xlsx",
        )
        result = run_tag_collect(config)
        data = result["data"]
        _assert(result["success"], "多个完整类目命中时应采集成功")
        _assert(len(calls) == 2, "多类目应拆成独立RPA任务，避免同页连续点击污染")
        _assert(all(len(call["category_filters"]) == 1 for call in calls), "每次RPA只应接收一个类目路径")
        _assert({call["category_filters"][0]["category_path"] for call in calls} == set(config.categories), "每个已选类目都应独立执行")
        _assert(len(data["rows"]) == 2, "多类目独立采集应保留两个候选商品")
        _assert({row["category_path"] for row in data["rows"]} == set(config.categories), "导出行应记录对应采集类目")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_sample_detail_verification():
    config = parse_input(
        categories="女装、男装、内衣,家用电器、数码电脑",
        tags="微信小店,一件代发,48小时发货",
        library_filters={"min_order_min": "1"},
        sample_data=True,
        output_format="xlsx",
    )
    result = run_tag_collect(config)
    data = result["data"]
    _assert(data["verification_queue"], "采集后应生成 P0/P1 详情核验队列")
    verify_result = verify_run_details(data["run_id"], sample_data=True)
    verify_data = verify_result["data"]
    _assert(verify_result["success"], "样例详情核验应成功")
    _assert(verify_data["verified_count"] >= 1, "样例详情核验应至少核验 1 个商品")
    _assert(verify_data["verification_records"], "应生成字段级核验记录")
    _assert(verify_data["filter_reevaluation_records"], "详情核验后应生成筛选重评估记录")
    verified_rows = [
        row for row in verify_data["rows"]
        if row.get("verification_status") == "sample_verified"
    ]
    _assert(verified_rows, "核验后应存在 sample_verified 商品")
    for row in verified_rows:
        _assert(row.get("wholesale_shipping_fee") != DETAIL_VERIFICATION_PENDING, "批发运费应由样例详情补充")
        _assert(row.get("product_refund_rate") != DETAIL_VERIFICATION_PENDING, "品退率应由样例详情补充")
        _assert(row.get("shipment_rate") != DETAIL_VERIFICATION_PENDING, "发货率应由样例详情补充")
        _assert(row.get("product_rating") != DETAIL_VERIFICATION_PENDING, "商品星级应由样例详情补充")
        _assert(row.get("review_tags") != DETAIL_VERIFICATION_PENDING, "评价标签应由样例详情补充")
    preapproved_rows = [
        row for row in verified_rows
        if row.get("wechat_shop_suggestion") == "可铺"
    ]
    _assert(preapproved_rows, "样例详情核验后应存在系统可铺商品")
    _assert(
        all(row.get("manual_review_status") == "样例预通过" for row in preapproved_rows),
        "样例关键详情字段已补齐时只能标记样例预通过，不能混同真实系统预通过",
    )
    _assert(
        all(row.get("manual_wechat_shop_suggestion") == "可铺" for row in preapproved_rows),
        "系统预通过商品应同步写入人工复核建议列，减少人工二次判断",
    )

    with zipfile.ZipFile(verify_data["output_path"]) as workbook:
        config_xml = workbook.read("xl/worksheets/sheet3.xml").decode("utf-8")
        record_xml = workbook.read("xl/worksheets/sheet5.xml").decode("utf-8")
        _assert("系统预通过数量" in config_xml, "导出标签配置应包含系统预通过数量")
        _assert("待人工复核数量" in config_xml, "导出标签配置应包含待人工复核数量")
        _assert("开发样例" in config_xml, "样例导出配置应醒目标注开发样例")
        _assert("禁止作为真实选品" in config_xml, "样例导出配置应标注禁止作为真实选品依据")
        _assert("sample_detail" in record_xml, "核验记录 sheet 应包含样例来源")
        _assert("商品星级" in record_xml, "核验记录 sheet 应包含商品星级")
        _assert("评价标签" in record_xml, "核验记录 sheet 应包含评价标签")


def test_auto_detail_verification_after_collect():
    config = parse_input(
        categories="女装、男装、内衣",
        tags="微信小店,一件代发,48小时发货",
        sample_data=True,
        output_format="xlsx",
        auto_verify_details=True,
        auto_verify_max_items=2,
    )
    result = run_tag_collect(config)
    data = result["data"]
    _assert(result["success"], "自动详情核验采集应成功")
    _assert(data["auto_verify_details"] is True, "返回结果应标记已启用自动详情核验")
    _assert(data["verified_count"] >= 1, "自动详情核验应至少补充 1 个商品")
    _assert(data["verification_records"], "自动详情核验应生成字段级记录")
    _assert(data["automation_state"]["status"] == "pending_detail", "自动核验上限未覆盖全部队列时应保持待详情核验状态")
    _assert(data["automation_state"]["verified_count"] >= 1, "自动核验状态应记录已核验数量")
    verified_rows = [
        row for row in data["top_items"]
        if row.get("verification_status") == "sample_verified"
    ]
    _assert(verified_rows, "采集结果中应直接包含已核验商品")
    _assert(
        any(row.get("product_refund_rate") != DETAIL_VERIFICATION_PENDING for row in verified_rows),
        "采集阶段应自动进入详情页补充品退率",
    )
    _assert(
        any(row.get("product_rating") != DETAIL_VERIFICATION_PENDING for row in verified_rows),
        "采集阶段应自动进入详情页补充商品星级",
    )
    _assert(
        any(row.get("review_tags") != DETAIL_VERIFICATION_PENDING for row in verified_rows),
        "采集阶段应自动进入详情页补充评价标签",
    )
    with zipfile.ZipFile(data["output_path"]) as workbook:
        result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        record_xml = workbook.read("xl/worksheets/sheet5.xml").decode("utf-8")
        _assert("sample_verified" in result_xml, "导出结果 sheet 应包含自动核验状态")
        _assert("样例预通过" in result_xml, "样例自动核验结果应标注样例预通过")
        _assert("sample_detail" in record_xml, "导出核验记录 sheet 应包含自动详情核验记录")


def test_auto_detail_verification_security_pause():
    def fake_collect_products(*args, **kwargs):
        return {
            "products": [{
                "id": "real-security-1",
                "title": "真实详情风控测试商品",
                "price": "19.9",
                "image": "",
                "url": "https://detail.1688.com/offer/real-security-1.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": "category_path_1",
                "label": "类目:日用餐厨、居家日用",
                "tag": "日用餐厨、居家日用",
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": "收纳盒",
                "page_url": "https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%94%B6%E7%BA%B3%E7%9B%92",
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    def blocked_detail(*args, **kwargs):
        raise ServiceError(
            "security_verification_required: 1688 触发了安全滑块/验证码校验，"
            "系统不会绕过或自动破解验证，也不会继续采集以免导出不可信数据。"
        )

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products,
        collect_detail_fields_from_1688_page=blocked_detail,
    )
    try:
        config = parse_input(
            categories="日用餐厨、居家日用",
            tags="微信小店,一件代发",
            keywords="收纳盒",
            sample_data=False,
            collect_source="rpa",
            output_format="xlsx",
            auto_verify_details=True,
            auto_verify_max_items=3,
        )
        result = run_tag_collect(config)
        data = result["data"]
        _assert(result["success"], "列表采集成功但详情风控暂停时接口应返回执行结果")
        _assert(data["automation_state"]["status"] == "paused", "详情风控应标记任务为 paused")
        _assert(data["automation_state"]["action"] == "manual_handoff", "详情风控应要求人工接管")
        _assert(data["verification_stopped_reason"], "详情风控应返回停止原因")
        _assert(data["verification_failed_count"] == 1, "详情风控商品应计入核验失败")
        row = data["rows"][0]
        _assert(row["verification_status"] == "failed", "详情风控商品不应被标记为已核验")
        _assert(row["product_refund_rate"] == DETAIL_VERIFICATION_PENDING, "详情风控不应写入样例品退率")
        _assert(row["shipment_rate"] == DETAIL_VERIFICATION_PENDING, "详情风控不应写入样例发货率")
        _assert("sample_verified" not in json.dumps(data, ensure_ascii=False), "真实详情风控不应出现样例核验状态")
        with zipfile.ZipFile(data["output_path"]) as workbook:
            config_xml = workbook.read("xl/worksheets/sheet3.xml").decode("utf-8")
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            _assert("详情核验暂停" in config_xml, "导出标签配置应写入暂停状态")
            _assert("人工接管验证" in config_xml, "导出标签配置应写入建议动作")
            _assert("failed" in result_xml, "导出结果 sheet 应保留核验失败状态")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_real_run_verify_ignores_sample_flag():
    def fake_collect_products(*args, **kwargs):
        return {
            "products": [{
                "id": "real-mode-1",
                "title": "真实批次模式保护商品",
                "price": "18.8",
                "image": "",
                "url": "https://detail.1688.com/offer/real-mode-1.html",
                "stats": {"rawText": "真实页面列表候选", "last30DaysSales": 80},
            }],
            "filter_results": [],
        }

    def fake_detail(*args, **kwargs):
        return {
            "min_order_range": "1件起批",
            "dropship_price": "18.8",
            "wholesale_shipping_fee": "首重6元",
            "dropship_shipping_fee": "5元起",
            "free_shipping": "部分地区包邮",
            "product_refund_rate": "1.0%",
            "product_rating": "4.9",
            "good_rate": "96.0%",
            "comment_count": "88",
            "review_tags": "发货快,质量好",
            "rights_protection": "7天包退换",
            "dropship_rights": "支持一件代发",
            "waybill_support": "微信小店",
            "collection_rate_24h": "98.0%",
            "shipment_rate": "99.0%",
            "shipment_speed": "24小时内发货",
            "supports_dropship": "是",
            "return_exchange_support": "支持退换",
            "monthly_dropship_orders": "520",
            "sku_count": "12",
            "favorite_customers": "120",
            "shop_name": "真实供应链店铺",
            "shop_url": "https://real-mode.1688.com/",
            "location": "广东 广州",
            "company_type": "生产厂家",
            "seller_member_type": "诚信通",
            "source_factory": "是",
            "stock": "现货 1000 件",
            "video_query": "待人工确认素材可用性",
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products,
        collect_detail_fields_from_1688_page=fake_detail,
    )
    try:
        config = parse_input(
            categories="",
            tags="微信小店,一件代发",
            keywords="收纳盒",
            sample_data=False,
            collect_source="rpa",
            output_format="xlsx",
            auto_verify_details=False,
        )
        result = run_tag_collect(config)
        data = result["data"]
        verify_result = verify_run_details(data["run_id"], sample_data=True, max_items=1)
        verify_data = verify_result["data"]
        _assert(verify_result["success"], "真实批次详情核验应成功")
        row = verify_data["rows"][0]
        _assert(row["data_mode"] == "真实数据", "真实批次不能被前端样例开关改成开发样例")
        _assert(row["verification_status"] in ("verified", "partial_verified"), "真实批次误传 sample_data=True 时仍应执行真实详情核验")
        _assert(row["verification_source"] == "1688_detail_page_rpa", "真实批次核验来源必须是真实详情页 RPA")
        _assert("sample_verified" not in json.dumps(verify_data, ensure_ascii=False), "真实批次不得出现样例核验状态")
        with zipfile.ZipFile(verify_data["output_path"]) as workbook:
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            record_xml = workbook.read("xl/worksheets/sheet5.xml").decode("utf-8")
            _assert("样例说明" not in workbook_xml, "真实批次导出不得包含样例说明 sheet")
            _assert("真实数据" in result_xml, "真实批次结果行必须标注真实数据")
            _assert("sample_detail" not in record_xml, "真实批次核验记录不得使用样例详情来源")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_real_page_candidates_enter_verification_queue():
    config = parse_input(
        tags="微信小店,一件代发",
        source_urls="https://detail.1688.com/offer/1234567890.html",
        sample_data=False,
        collect_source="rpa",
        output_format="xlsx",
    )
    _assert(
        config.source_urls == ["https://detail.1688.com/offer/1234567890.html"],
        "真实采集应支持直接指定 1688 页面 URL",
    )
    row = product_to_export_row(
        Product(
            id="1234567890",
            title="真实页面采集候选商品",
            price="19.9",
            image="",
            url="https://detail.1688.com/offer/1234567890.html",
            stats={"rawText": "真实页面列表文本"},
        ),
        "日用餐厨、居家日用",
        config,
    )
    _assert(row["list_source"] == "rpa", "真实页面候选商品应标记 list_source=rpa")
    queue = build_verification_queue([row])
    _assert(queue, "真实页面候选商品即使列表指标不足，也应进入详情核验队列")
    _assert("真实页面候选商品" in queue[0]["reason"], "核验队列应标明真实页面来源")


def test_url_direct_requires_url_and_parses_html():
    config = parse_input(
        categories="日用餐厨、居家日用",
        collect_source="url_direct",
        sample_data=False,
        output_format="xlsx",
    )
    try:
        run_tag_collect(config)
    except ServiceError as exc:
        state = collect_error_state(exc)
        _assert(state["code"] == "direct_url_required", "URL直连未填 URL 应返回 direct_url_required")
    else:
        raise AssertionError("URL直连未填 URL 不应执行采集")

    original_fetch = service._fetch_1688_url_direct

    def fake_fetch(source_url):
        return (
            """
            <html><body>
              <a href="https://detail.1688.com/offer/123456789012.html" title="厨房抽屉收纳盒家用分隔整理盒">商品</a>
              <span>￥12.80</span>
            </body></html>
            """,
            source_url,
        )

    service._fetch_1688_url_direct = fake_fetch
    try:
        config = parse_input(
            categories="日用餐厨、居家日用",
            tags="微信小店",
            source_urls="https://s.1688.com/selloffer/offer_search.htm?keywords=test",
            collect_source="url_direct",
            sample_data=False,
            output_format="xlsx",
        )
        result = run_tag_collect(config)
        data = result["data"]
        _assert(result["success"], "URL直连解析到商品链接时应采集成功")
        _assert(data["collect_source"] == "url_direct", "URL直连结果应保留 collect_source")
        _assert(data["row_count"] == 1, "URL直连应生成 1 条候选")
        row = data["rows"][0]
        _assert(row["item_id"] == "123456789012", "URL直连应提取商品ID")
        _assert(row["list_source"] == "url_direct", "导出行应标记 URL 直连来源")
        _assert(row["url"].startswith("https://detail.1688.com/offer/123456789012"), "导出行应保留商品链接")
        _assert(data["verification_queue"], "URL直连真实候选应进入详情核验队列")
    finally:
        service._fetch_1688_url_direct = original_fetch


def test_url_direct_security_pause_from_1688():
    config = parse_input(
        source_urls="https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%94%B6%E7%BA%B3%E7%9B%92",
        collect_source="url_direct",
        sample_data=False,
        output_format="xlsx",
    )
    try:
        result = run_tag_collect(config)
    except ServiceError as exc:
        state = collect_error_state(exc)
        _assert(state["code"] in ("security_verification_required", "login_required", "search_results_not_loaded", "direct_url_fetch_failed"), "真实1688直连失败应返回可识别错误")
    else:
        _assert(result["success"], "若当前网络未被1688拦截，URL直连应可成功")
        _assert(result["data"]["collect_source"] == "url_direct", "真实直连成功时应保留 url_direct 来源")


def test_review_filters_wait_for_detail_then_match():
    config = parse_input(
        categories="日用餐厨、居家日用",
        tags="微信小店",
        library_filters={
            "product_rating_min": "4.8",
            "good_rate_min": "95",
            "comment_count_min": "40",
            "review_tags": ["客服态度超好"],
        },
        sample_data=True,
        output_format="xlsx",
    )
    result = run_tag_collect(config)
    data = result["data"]
    _assert(result["success"], "评价口碑筛选采集应成功")
    _assert(data["rows"], "评价口碑筛选应先保留待详情核验候选")
    first_row = data["rows"][0]
    pending = [
        record for record in first_row.get("filter_match_records", [])
        if record.get("field_key") in ("product_rating", "good_rate", "comment_count", "review_tags")
    ]
    _assert(pending, "评价口碑筛选应生成详情后置记录")
    _assert(all(record.get("status") == "pending_detail" for record in pending), "详情核验前评价筛选应保持 pending_detail")
    verify_result = verify_run_details(data["run_id"], sample_data=True, max_items=1)
    verify_data = verify_result["data"]
    matched = [
        record for record in verify_data["filter_reevaluation_records"]
        if record.get("field_key") in ("product_rating", "good_rate", "comment_count", "review_tags")
    ]
    _assert(matched, "详情核验后应生成评价筛选重评估记录")
    _assert(any(record.get("status") == "matched" for record in matched), "详情核验后评价筛选应可命中")


def test_detail_filter_excluded_rows_leave_main_results():
    config = parse_input(
        categories="日用餐厨、居家日用",
        tags="微信小店",
        library_filters={"review_tags": ["不存在的评价标签"]},
        sample_data=True,
        output_format="xlsx",
    )
    result = run_tag_collect(config)
    data = result["data"]
    before_count = data["row_count"]
    _assert(before_count >= 1, "详情评价标签筛选前应保留待核验候选")
    verify_result = verify_run_details(data["run_id"], sample_data=True, max_items=1)
    verify_data = verify_result["data"]
    _assert(verify_data["filter_excluded_count"] == 1, "详情核验后未命中评价标签的商品应被筛选剔除")
    _assert(len(verify_data["rows"]) == before_count - 1, "被详情筛选剔除的商品不应继续留在主结果 rows")
    _assert(verify_data["filter_excluded_rows"], "被剔除商品应保留在 filter_excluded_rows 供审计")


def test_detail_missing_review_metrics_do_not_use_list_values():
    config = parse_input(
        categories="日用餐厨、居家日用",
        tags="微信小店",
        library_filters={"good_rate_min": "90", "comment_count_min": "30"},
        sample_data=True,
        output_format="xlsx",
    )
    result = run_tag_collect(config)
    data = result["data"]
    first_id = str(data["rows"][0]["item_id"])
    original_detail = dict(service.SAMPLE_DETAIL_VERIFICATIONS[first_id])
    patched_detail = dict(original_detail)
    patched_detail.pop("good_rate", None)
    patched_detail.pop("comment_count", None)
    service.SAMPLE_DETAIL_VERIFICATIONS[first_id] = patched_detail
    try:
        verify_result = verify_run_details(data["run_id"], sample_data=True, max_items=1)
    finally:
        service.SAMPLE_DETAIL_VERIFICATIONS[first_id] = original_detail
    records = [
        record for record in verify_result["data"]["filter_reevaluation_records"]
        if record.get("field_key") in ("good_rate", "comment_count")
    ]
    _assert(records, "缺失详情评价指标时也应生成重评估记录")
    _assert(all(record.get("status") == "pending_detail" for record in records), "详情未提取到好评率/评价数时不能用列表值通过筛选")


def test_target_publishable_count_and_candidate_budget():
    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
    ):
        return {
            "products": [
                {
                    "id": "target-bad-1",
                    "title": "低销量测试商品",
                    "price": "9.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/900000000001.html",
                    "stats": {"last30DaysSales": 5, "remarkCnt": 2, "downstreamOffer": 900},
                },
                {
                    "id": "target-good-1",
                    "title": "高潜测试商品一",
                    "price": "19.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/900000000002.html",
                    "stats": {
                        "last30DaysSales": 1600,
                        "last30DaysDropShippingSales": 800,
                        "goodRates": 0.96,
                        "repurchaseRate": 0.2,
                        "collectionRate24h": 0.98,
                        "downstreamOffer": 100,
                        "remarkCnt": 120,
                    },
                },
                {
                    "id": "target-good-2",
                    "title": "高潜测试商品二",
                    "price": "29.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/900000000003.html",
                    "stats": {
                        "last30DaysSales": 1200,
                        "last30DaysDropShippingSales": 650,
                        "goodRates": 0.94,
                        "repurchaseRate": 0.18,
                        "collectionRate24h": 0.96,
                        "downstreamOffer": 160,
                        "remarkCnt": 90,
                    },
                },
            ][:limit],
            "filter_results": [],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir():
            config = parse_input(
                tags="微信小店",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                max_items_per_query=1,
                target_publishable_count=2,
            )
            result = run_tag_collect(config)
            data = result["data"]
            _assert(result["success"], "目标可铺数量采集应成功")
            _assert(data["target_publishable_count"] == 2, "应返回目标可铺数量")
            _assert(data["candidate_scan_limit"] > config.max_items_per_query, "候选预算应为可铺目标适度放大")
            _assert(data["candidate_count"] >= 3, "应扫描超过原始每词上限的候选以满足可铺目标")
            _assert(data["publishable_count"] == 2, "目标计数应按可铺/谨慎结果计算")
            _assert(data["collection_stop_reason"] == "target_met", "达到目标后应标记 target_met")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_persistent_rejected_products_are_skipped_next_run():
    calls = []

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
    ):
        calls.append(limit)
        return {
            "products": [
                {
                    "id": "persistent-reject-1",
                    "title": "历史不合格商品",
                    "price": "19.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/910000000001.html",
                    "stats": {"last30DaysSales": 200, "remarkCnt": 20},
                },
                {
                    "id": "persistent-good-1",
                    "title": "历史过滤后保留商品",
                    "price": "19.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/910000000002.html",
                    "stats": {
                        "last30DaysSales": 1500,
                        "last30DaysDropShippingSales": 700,
                        "goodRates": 0.96,
                        "repurchaseRate": 0.18,
                        "collectionRate24h": 0.97,
                        "downstreamOffer": 100,
                        "remarkCnt": 120,
                    },
                },
            ],
            "filter_results": [],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir() as data_dir:
            config = parse_input(
                tags="微信小店",
                library_filters={"sales_units_min": "1000"},
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                target_publishable_count=1,
            )
            first = run_tag_collect(config)["data"]
            _assert(first["rejected_count"] >= 1, "列表硬筛失败商品应产生筛除记录")
            rejected_path = data_dir / "rejected_products.json"
            _assert(rejected_path.exists(), "真实筛除商品应写入本地排除池")

            second = run_tag_collect(config)["data"]
            ids = {row["item_id"] for row in second["rows"]}
            _assert("persistent-reject-1" not in ids, "历史筛除商品下次应跳过")
            _assert(second["skipped_rejected_count"] >= 1, "返回结果应记录历史筛除跳过数")
            _assert(second["skipped_rejected_records"], "应返回历史筛除跳过明细供审计")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_detail_verification_rescores_and_rejects_risky_product():
    config = parse_input(
        categories="家用电器、数码电脑",
        tags="微信小店",
        sample_data=True,
        output_format="xlsx",
        auto_verify_details=True,
        auto_verify_max_items=3,
    )
    result = run_tag_collect(config)
    data = result["data"]
    risky_rows = [
        row for row in data["rows"]
        if row.get("item_id") == "658275444569"
    ]
    _assert(risky_rows, "样例中应保留家电高风险商品用于详情重评分")
    risky = risky_rows[0]
    _assert(risky["product_refund_rate"] == "6.8%", "自动详情核验应补充高风险商品品退率")
    _assert(risky["shipment_rate"] == "93.4%", "自动详情核验应补充高风险商品发货率")
    _assert(risky["recommendation_score"] < risky["list_recommendation_score"], "详情风险应降低推荐分")
    _assert(risky["wechat_shop_suggestion"] in ("谨慎", "不建议"), "详情风险应降低微信小店建议")
    _assert(risky["manual_review_status"] != "系统预通过", "高品退率/低发货率商品不能系统预通过")
    rescore = [
        record for record in data["rescore_records"]
        if record.get("item_id") == "658275444569"
    ]
    _assert(rescore and rescore[0]["score_delta"] < 0, "详情核验后应生成负向重评分记录")


def test_auto_review_status_normalizes_export_rows():
    config = parse_input(categories="日用餐厨、居家日用", tags="微信小店", sample_data=False)
    good = product_to_export_row(
        Product(
            id="auto-review-good",
            title="厨房沥水架置物架",
            price="19.9",
            image="",
            url="https://detail.1688.com/offer/auto-review-good.html",
            stats={
                "last30DaysSales": 1800,
                "last30DaysDropShippingSales": 900,
                "downstreamOffer": 120,
                "remarkCnt": 320,
                "goodRates": 0.97,
                "repurchaseRate": 0.16,
                "collectionRate24h": 0.98,
            },
        ),
        "厨房沥水架",
        config,
    )
    _assert(good["manual_review_status"] == "待复核", "未进入详情页前可铺候选仍应等待关键字段核验")
    for key, value in {
        "wholesale_shipping_fee": "首重6元",
        "dropship_shipping_fee": "5元起",
        "free_shipping": "部分地区包邮",
        "product_refund_rate": "1.2%",
        "shipment_rate": "98.8%",
        "collection_rate_24h": "98.0%",
        "supports_dropship": "是",
        "return_exchange_support": "支持退换",
    }.items():
        good[key] = value
    service.apply_auto_review_status(good)
    _assert(good["manual_review_status"] == "待复核", "没有详情核验证据时，即使字段有值也不能系统预通过")
    good["verification_status"] = "verified"
    good["detail_verified_fields"] = sorted(service.AUTO_REVIEW_REQUIRED_DETAIL_FIELDS)
    service.rescore_row_after_detail(good)
    _assert(good["manual_review_status"] == "系统预通过", "关键字段核验后应自动预通过")

    sample_good = dict(good)
    sample_good["item_id"] = "auto-review-sample"
    sample_good["list_source"] = "sample"
    sample_good["data_mode"] = "开发样例"
    sample_good["verification_status"] = "sample_verified"
    service.apply_auto_review_status(sample_good)
    _assert(sample_good["manual_review_status"] == "样例预通过", "样例数据不能标记为真实系统预通过")
    _assert("不代表真实1688数据" in sample_good["manual_review_note"], "样例预通过必须说明不代表真实1688数据")

    unclear = dict(good)
    unclear["item_id"] = "auto-review-unclear"
    unclear["wechat_shop_suggestion"] = "可铺"
    unclear["product_refund_rate"] = "暂无"
    unclear["shipment_rate"] = "未展示"
    service.apply_auto_review_status(unclear)
    _assert(unclear["manual_review_status"] == "待复核", "品退率/发货率无法解析时不能系统预通过")

    bad = dict(good)
    bad["item_id"] = "auto-review-bad"
    bad["wechat_shop_suggestion"] = "不建议"
    service.apply_auto_review_status(bad)
    _assert(bad["manual_review_status"] == "系统不建议", "不建议商品应自动归为系统不建议")

    excluded = dict(good)
    excluded["item_id"] = "auto-review-excluded"
    excluded["filter_verification_status"] = "filtered_out"
    excluded["filter_verification_note"] = "品退率未满足<2%"
    service.apply_auto_review_status(excluded)
    _assert(excluded["manual_review_status"] == "系统剔除", "筛选不通过商品应自动剔除")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "auto_review.xlsx")
        export_xlsx([good, bad], output_path, {"filter_excluded_rows": [excluded], "run_id": "auto-review"})
        with zipfile.ZipFile(output_path) as workbook:
            config_xml = workbook.read("xl/worksheets/sheet3.xml").decode("utf-8")
            failed_xml = workbook.read("xl/worksheets/sheet4.xml").decode("utf-8")
            _assert("系统预通过数量" in config_xml, "导出配置应展示系统预通过数量")
            _assert("系统不建议数量" in config_xml, "导出配置应展示系统不建议数量")
            _assert("系统剔除数量" in config_xml, "导出配置应展示系统剔除数量")
            _assert("auto-review-excluded" in failed_xml, "筛选剔除商品应进入异常复核 sheet")
            _assert("auto-review-good" not in failed_xml, "系统预通过商品不应进入异常复核 sheet")


def test_web_token_and_sample_api():
    web.SERVER_TOKEN = "tag-collect-smoke-token"
    web.ALLOW_REAL_COLLECT = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.TagCollectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base_url}/api/options", timeout=10) as response:
            options = json.loads(response.read().decode("utf-8"))["data"]
        _assert(options["token"] == web.SERVER_TOKEN, "options 应返回本地 token")
        _assert(options["allow_real_collect"] is True, "真实采集默认应开启")
        _assert(options["limits"]["max_queries"] == 50, "服务端查询词限额应为 50")
        _assert(options["library_filter_schema"], "options 应返回店雷达选品库筛选契约")
        _assert(options["library_filter_coverage"], "options 应返回筛选覆盖状态")
        _assert(options["library_capabilities"]["implemented"], "options 应返回筛选能力清单")
        _assert(options["allow_real_collect"] is True, "本机 Web smoke 应允许真实采集模式开关")
        category_tree = options["category_tree"]
        _assert(len(category_tree) >= 10, "options 应返回1688左侧主要组合一级类目树")
        _assert("内裤" in category_tree["女装、男装、内衣"], "options 应透出组合入口下的内裤二级分组")
        _assert("男士平角裤" in category_tree["女装、男装、内衣"]["内裤"], "options 应透出女装内裤下的三级类目")
        _assert("汉服套装" in category_tree["女装、男装、内衣"]["新中式"], "options 应透出女装新中式三级类目")
        _assert(options["category_dictionary"]["status"] == "synced_from_1688_homepage_left_nav", "options 应返回类目字典可追溯状态")

        try:
            _post_json(f"{base_url}/api/collect", {"sample_data": True})
        except urllib.error.HTTPError as exc:
            _assert(exc.code == 403, "无 token POST 应返回 403")
        else:
            raise AssertionError("无 token POST 不应成功")

        status, payload = _post_json(
            f"{base_url}/api/collect",
            {
                "token": web.SERVER_TOKEN,
                "categories": ["女装、男装、内衣"],
                "tags": ["微信小店"],
                "library_filters": {
                    "search_keyword": "连衣裙",
                    "min_order_min": "1",
                    "sales_orders_min": "100",
                    "wholesale_price_max": "80",
                    "fulfillment_times": ["48小时"],
                    "seller_member_types": ["实力商家"],
                    "shop_fans_min": "100",
                },
                "sample_data": True,
                "auto_verify_details": True,
                "auto_verify_max_items": 2,
                "target_publishable_count": 2,
                "output_format": "xlsx",
            },
        )
        _assert(status == 200, "带 token 样例采集 HTTP 应成功")
        _assert(payload["success"], "带 token 样例采集业务应成功")
        _assert(payload["data"]["row_count"] >= 1, "样例采集应返回商品")
        _assert(payload["data"]["target_publishable_count"] == 2, "Web 应返回目标可铺数量")
        _assert(payload["data"]["publishable_count"] >= 1, "Web 应返回可铺/谨慎数量")
        _assert("collection_stop_reason" in payload["data"], "Web 应返回采集停止原因")
        _assert(payload["data"]["library_filters"]["search_keyword"] == "连衣裙", "Web 返回应保留店雷达筛选对象")
        _assert(_native_has(payload["data"]["filter_plan"]["native_filters"], "48小时发货"), "Web 采集应转译发货时间原生筛选")
        _assert(payload["data"]["filter_plan"]["library_reserved_fields"], "Web 采集应返回预留字段记录")
        _assert(payload["data"]["library_filter_coverage"], "Web 采集应返回筛选覆盖状态")
        run_id = payload["data"]["run_id"]
        _assert(payload["data"]["auto_verify_details"] is True, "Web 采集应接收自动详情核验开关")
        _assert(payload["data"]["automation_state"]["status"] in ("pending_detail", "sample_verified"), "Web 应返回任务状态")
        _assert(payload["data"]["verified_count"] >= 1, "Web 自动详情核验应补充商品")
        _assert(payload["data"]["verification_records"], "Web 自动详情核验应返回字段记录")
        _assert(
            any(row.get("product_refund_rate") != DETAIL_VERIFICATION_PENDING for row in payload["data"]["rows"]),
            "Web rows 应包含详情页自动补充字段",
        )
        status, default_limit_payload = _post_json(
            f"{base_url}/api/collect",
            {
                "token": web.SERVER_TOKEN,
                "categories": ["女装、男装、内衣"],
                "tags": ["微信小店"],
                "sample_data": True,
                "auto_verify_details": True,
                "target_publishable_count": 5,
                "output_format": "xlsx",
            },
        )
        _assert(status == 200 and default_limit_payload["success"], "Web 默认自动核验上限采集应成功")
        _assert(
            default_limit_payload["data"]["auto_verify_result"].get("verified_count", 0) >= 3,
            "未显式传自动核验上限时，后端不应退回只核验 3 个以内的旧默认",
        )

        status, headers, body = _get_bytes(f"{base_url}{payload['data']['download_url']}")
        _assert(status == 200, "download 应返回 200")
        _assert(run_id in headers.get("Content-Disposition", ""), "download 文件名应包含 run_id")
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
            tmp.write(body)
            tmp.flush()
            with zipfile.ZipFile(tmp.name) as workbook:
                workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                _assert("核验记录" in workbook_xml, "download xlsx 应包含核验记录 sheet")
                _assert("筛选执行记录" in workbook_xml, "download xlsx 应包含筛选执行记录 sheet")

        status, payload = _post_json(
            f"{base_url}/api/verify",
            {
                "token": web.SERVER_TOKEN,
                "run_id": run_id,
                "sample_data": True,
            },
        )
        _assert(status == 200, "Web 样例详情核验 HTTP 应成功")
        _assert(payload["success"], "Web 样例详情核验业务应成功")
        _assert(payload["data"]["verified_count"] >= 1, "Web 样例详情核验应补充商品")
        _assert(payload["data"]["verification_records"], "Web 样例详情核验应返回字段记录")
        _assert(payload["data"]["filter_reevaluation_records"], "Web 样例详情核验应返回详情筛选重评估记录")

        web.ALLOW_REAL_COLLECT = False
        status, payload = _post_json(
            f"{base_url}/api/collect",
            {
                "token": web.SERVER_TOKEN,
                "categories": ["女装、男装、内衣"],
                "tags": ["微信小店"],
                "sample_data": False,
                "collect_source": "rpa",
                "output_format": "csv",
            },
        )
        _assert(status == 200, "关闭真实采集时应返回业务失败而非 HTTP 失败")
        _assert(not payload["success"], "关闭真实采集时真实页面采集应被阻止")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_security_verification_does_not_export():
    web.SERVER_TOKEN = "tag-collect-smoke-token"
    web.ALLOW_REAL_COLLECT = True

    def blocked_collect(*args, **kwargs):
        raise ServiceError(
            "security_verification_required: 1688 触发了安全滑块/验证码校验，"
            "系统不会绕过或自动破解验证，也不会继续采集以免导出不可信数据。"
        )

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=blocked_collect
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.TagCollectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _post_json(
            f"{base_url}/api/collect",
            {
                "token": web.SERVER_TOKEN,
                "categories": ["女装、男装、内衣"],
                "tags": ["微信小店"],
                "sample_data": False,
                "collect_source": "rpa",
                "output_format": "xlsx",
            },
        )
        _assert(status == 200, "安全验证阻断应返回业务失败而非 HTTP 失败")
        _assert(not payload["success"], "安全验证阻断不应采集成功")
        _assert(payload["data"].get("run_id", "") == "", "安全验证阻断不应生成 run_id")
        _assert(payload["data"].get("row_count", 0) == 0, "安全验证阻断不应生成商品")
        _assert(payload["data"].get("rows", []) == [], "安全验证阻断不应返回 rows")
        _assert("download_url" not in payload["data"], "安全验证阻断不应返回下载链接")
        _assert(payload["data"].get("error_code") == "security_verification_required", "安全验证阻断应返回 error_code")
        _assert(payload["data"].get("action") == "manual_handoff", "安全验证阻断应要求人工接管")
        _assert(payload["data"].get("retryable") is False, "安全验证阻断不应自动重试")
        _assert("停止反复刷新" in payload["data"].get("suggestion", ""), "安全验证阻断应提示停止反复刷新")
        _assert("不会绕过" in payload["markdown"], "安全验证提示应明确不会绕过验证码")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def main():
    test_category_dictionary_expanded_tree()
    test_field_numbers()
    test_filter_plan_splits_native_and_metric_tags()
    test_library_filter_contract_and_mapping()
    test_metric_bucket_ranges()
    test_export_xlsx_and_exclude()
    test_export_normalizes_legacy_sample_rows()
    test_export_marks_unknown_source_rows_as_needing_verification()
    test_rpa_detail_extracts_structured_fields_without_bad_shop_name()
    test_security_verification_error_message()
    test_cdp_security_error_context()
    test_navigation_timeout_error_context()
    test_cdp_context_unsupported_error_context()
    test_dirty_product_title_is_cleaned()
    test_search_keyword_encoding_error_context()
    test_rpa_collect_uses_human_search_not_keyword_url()
    test_web_defaults_to_automatic_batch_collect()
    test_chrome_status_sets_cdp_env_and_reports_pages()
    test_1688_homepage_is_not_collectable_current_page()
    test_start_chrome_script_canary_fallback_name()
    test_search_results_not_loaded_error_context()
    test_category_navigation_not_loaded_error_context()
    test_invalid_category_path_blocks_before_rpa()
    test_category_navigation_failure_blocks_export()
    test_partial_category_navigation_blocks_export()
    test_category_navigation_service_error_keeps_filter_context()
    test_multi_category_rpa_runs_are_isolated()
    test_sample_detail_verification()
    test_auto_detail_verification_after_collect()
    test_auto_detail_verification_security_pause()
    test_real_run_verify_ignores_sample_flag()
    test_real_page_candidates_enter_verification_queue()
    test_url_direct_requires_url_and_parses_html()
    test_url_direct_security_pause_from_1688()
    test_review_filters_wait_for_detail_then_match()
    test_detail_filter_excluded_rows_leave_main_results()
    test_detail_missing_review_metrics_do_not_use_list_values()
    test_target_publishable_count_and_candidate_budget()
    test_persistent_rejected_products_are_skipped_next_run()
    test_detail_verification_rescores_and_rejects_risky_product()
    test_web_token_and_sample_api()
    test_web_security_verification_does_not_export()
    print("tag_collect smoke tests passed")


if __name__ == "__main__":
    main()
