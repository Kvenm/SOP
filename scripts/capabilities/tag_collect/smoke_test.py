#!/usr/bin/env python3
"""tag_collect 最小回归测试：字段、导出、Web token、真实模式开关、开发样例采集。"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import types
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from capabilities.tag_collect import rpa, service, web
from capabilities.tag_collect.service import (
    DEFAULT_ITEMS_PER_QUERY,
    DEFAULT_TARGET_PUBLISHABLE_COUNT,
    DETAIL_ONLY_FIELDS,
    DETAIL_VERIFICATION_PENDING,
    EXPORT_COLUMNS,
    MAX_ITEMS_PER_QUERY,
    MAX_TARGET_PUBLISHABLE_COUNT,
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


def _get_json(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


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


def _reset_web_task_memory():
    with web.TASK_LOCK:
        web.TASKS.clear()
        web.TASK_CANCEL_EVENTS.clear()
        web.ACTIVE_TASK_ID = ""
        web.TASK_STORE_LOADED_PATH = ""


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


def _xlsx_sheet_xml_by_name(workbook: zipfile.ZipFile, sheet_name: str) -> str:
    root = ET.fromstring(workbook.read("xl/workbook.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    sheets = root.find("m:sheets", ns)
    if sheets is None:
        return ""
    for index, sheet in enumerate(sheets.findall("m:sheet", ns), 1):
        if sheet.attrib.get("name") == sheet_name:
            return workbook.read(f"xl/worksheets/sheet{index}.xml").decode("utf-8")
    return ""


def _javascript_function_source(source: str, function_name: str) -> str:
    match = re.search(
        rf"(?P<source>(?:async\s+)?function\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{[\s\S]*?\n    \}})",
        source,
    )
    return match.group("source") if match else ""


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
    _assert(numbers[-1] == "10.9", "用户最终字段的最后一列应为采集时间")
    _assert(len(labels) == 24, "选品结果主表应严格保留7.21模板确认的24个字段")
    _assert(labels == REFERENCE_EXPORT_LABELS, "导出字段顺序应与用户2026-07-21模板一致")
    _assert(labels[6] == "月代发订单", "月代发订单应位于商品链接之后")
    _assert(labels[15] == "代发运费", "代发运费应位于库存之后")
    _assert(labels[20] == "48小时揽收率", "主表应保留48小时揽收率")
    _assert("monthly_dropship_orders" in keys, "字段应包含月代发订单")
    _assert("dropship_shipping_fee" in keys, "字段应包含代发运费")
    _assert("supports_dropship" in keys, "字段应包含是否一件代发")
    _assert("collection_rate_48h" in keys, "字段应包含48小时揽收率")
    _assert("good_rate_bucket" in keys, "字段应包含好评率区间")
    _assert("shipment_rate_bucket" in keys, "字段应包含发货率区间")
    _assert("wholesale_price" not in keys, "7.21模板已删除批发价")
    _assert("rights_protection" not in keys, "7.21模板已删除权益保障")
    _assert("dropship_rights" not in keys, "7.21模板已删除代发权益")
    _assert("collection_rate_24h" not in keys, "7.21模板已删除24小时揽收率")
    _assert("product_refund_rate" not in keys, "用户未保留品退率时不得继续出现在选品结果主表")
    _assert("recommendation_score" not in keys, "用户未保留推荐分时不得继续出现在选品结果主表")


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
        **kwargs,
    ):
        calls.append({
            "query": query,
            "source_url": source_url,
            "category_filters": category_filters or [],
            "limit": limit,
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
        "seller_member_types",
        "seller_services",
    }
    _assert("selection_mode" not in section_keys, "用户确认不需要的选品模式分组不应出现在筛选契约")
    _assert("advanced" in section_keys, "店雷达筛选契约应包含高级筛选")
    _assert("priority" in section_keys, "月代发、运费、原生履约和商家特色应集中到重点筛选区")
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
        "monthly_dropship_orders",
        "dropship_shipping_fee",
    }:
        _assert(expected_key in schema_field_keys, f"截图新增筛选字段应下发到前端 schema：{expected_key}")
    schema_field_key_list = [
        field["key"]
        for section in schema
        for field in section.get("fields", [])
    ]
    for priority_key in {
        "monthly_dropship_orders",
        "dropship_shipping_fee",
        "dropship_rights",
        "fulfillment_times",
        "seller_features",
    }:
        _assert(schema_field_key_list.count(priority_key) == 1, f"重点筛选字段只能保留一个可见入口：{priority_key}")
    seller_feature_field = next(
        field
        for section in schema
        for field in section.get("fields", [])
        if field.get("key") == "seller_features"
    )
    _assert(seller_feature_field["type"] == "radio", "商家特色应按1688截图限制为单选")
    _assert(
        seller_feature_field["options"] == ["不限", "超级工厂", "源头旗舰", "实力商家"],
        "商家特色选项应与截图一致",
    )
    surface_values = [
        tag
        for tags in service.TAG_FILTER_GROUPS.values()
        for tag in tags
    ] + [
        option
        for section in schema
        for field in section.get("fields", [])
        for option in field.get("options", [])
    ]
    for unique_label in ("一件代发", "7天无理由退货", "24H发货", "48H发货"):
        _assert(surface_values.count(unique_label) == 1, f"筛选界面中的{unique_label}只能保留一个入口")
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
        "monthly_dropship_orders_min": "10",
        "wholesale_price_max": "50",
        "dropship_shipping_fee_min": "1",
        "dropship_shipping_fee_max": "5",
        "repurchase_rate_min": "10",
        "product_marks": ["新品"],
        "certificates": ["CE"],
        "listed_time": "近30天",
        "seller_locations": ["广东"],
        "fulfillment_times": ["48H发货"],
        "waybill_support": ["抖音", "拼多多"],
        "rights_protection": ["退货包运费", "7天无理由退货", "赠运费险"],
        "platform_service_filters": ["严选", "分销严选"],
        "fulfillment_service_filters": ["官方物流", "密文面单", "晚揽必赔", "24H支揽率", "48H支揽率"],
        "dropship_rights": ["一件代发", "一件代发包邮"],
        "seller_location_regions": ["广东"],
        "seller_features": "源头旗舰",
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
    _assert(_native_has(plan["native_filters"], "48H发货"), "48H发货应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "官方物流"), "官方物流应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "密文面单"), "密文面单应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "晚揽必赔"), "晚揽必赔应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "24H支揽率"), "24H支揽率应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "48H支揽率"), "48H支揽率应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "合并供应商"), "合并供应商应转译为原生筛选")
    _assert(any(key.startswith("seller_location_") for key in native_keys), "所在地应生成下拉原生筛选计划")
    _assert(any(key.startswith("seller_feature_") for key in native_keys), "商家特色应生成下拉原生筛选计划")
    _assert(_native_has(plan["native_filters"], "源头旗舰"), "商家特色单选值应生成1688下拉筛选计划")
    _assert(any(key.startswith("business_mode_") for key in native_keys), "经营模式应生成下拉原生筛选计划")
    _assert(_native_has(plan["native_filters"], "48H发货"), "发货时间应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "支持抖音面单"), "面单支持应转译为原生筛选")
    _assert(_native_has(plan["native_filters"], "支持拼多多面单"), "面单支持应转译为原生筛选")
    _assert("orders_30d" in post_fields, "销售订单数应进入后置指标筛选")
    _assert("monthly_dropship_orders" in post_fields, "月代发订单自定义区间应进入详情后置筛选")
    _assert("wholesale_price" in post_fields, "批发价应进入后置指标筛选")
    _assert("dropship_shipping_fee" in post_fields, "代发运费自定义区间应进入详情后置筛选")
    _assert("product_rating" in post_fields, "商品星级应进入详情后置筛选")
    _assert("good_rate" in post_fields, "好评率应进入详情后置筛选")
    _assert("comment_count" in post_fields, "评价数应进入详情后置筛选")
    _assert("客服态度超好" in post_tags, "评价标签应进入详情文本匹配筛选")
    _assert("质感不错" in post_tags, "评价标签应支持多选详情文本匹配筛选")
    _assert("repurchase_rate" not in post_fields, "已移除的复购率不应进入筛选计划")
    _assert("seller_member_types" not in config.library_filters, "旧卖家会员类型入口应被服务端清洗，避免与商家特色重复")
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


def test_candidate_scan_defaults_and_caps():
    default_config = parse_input(sample_data=True)
    _assert(default_config.max_items_per_query == DEFAULT_ITEMS_PER_QUERY == 120, "默认候选扫描应提高到120条")
    _assert(default_config.target_publishable_count == DEFAULT_TARGET_PUBLISHABLE_COUNT == 60, "默认目标可铺数应与候选预算分离")

    capped_config = parse_input(
        sample_data=True,
        max_items_per_query=999,
        target_publishable_count=999,
    )
    _assert(capped_config.max_items_per_query == MAX_ITEMS_PER_QUERY == 200, "候选扫描硬上限应为200条")
    _assert(capped_config.target_publishable_count == MAX_TARGET_PUBLISHABLE_COUNT == 100, "目标可铺数硬上限应为100条")


def test_composite_navigation_group_is_not_exported_as_product_category():
    _assert(
        service._category_display_path("女装、男装、内衣>下装裤>女式牛仔裤") == "下装裤>女式牛仔裤",
        "1688组合一级入口不能冒充商品真实大类",
    )
    _assert(
        service._category_display_path("包装>纸类包装容器>纸盒") == "包装>纸类包装容器>纸盒",
        "非组合一级类目应保留完整路径",
    )


def test_export_xlsx_and_exclude():
    config = parse_input(
        categories="",
        tags="微信小店,48小时发货",
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
        _assert(len(sheets) == 1, "7.21模板要求 xlsx 只保留一个选品结果工作表")
        workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
        result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        result_labels = _xlsx_first_row_labels(workbook, 1)
        _assert(result_labels == REFERENCE_EXPORT_LABELS, "选品结果 sheet 表头应严格等于7.21模板确认的24个字段")
        _assert("选品结果" in workbook_xml, "应包含选品结果 sheet")
        _assert("字段说明" not in workbook_xml, "xlsx 不应继续生成字段说明附表")
        _assert("标签配置" not in workbook_xml, "xlsx 不应继续生成标签配置附表")
        _assert("异常复核" not in workbook_xml, "xlsx 不应继续生成异常复核附表")
        _assert("核验记录" not in workbook_xml, "xlsx 不应继续生成核验记录附表")
        _assert("筛选执行记录" not in workbook_xml, "xlsx 不应继续生成筛选执行记录附表")
        _assert("样例说明" not in workbook_xml, "xlsx 不应继续生成样例说明附表")
        for label in REFERENCE_EXPORT_LABELS:
            _assert(label in result_xml, f"选品结果表头应包含附件基线字段：{label}")
        _assert("批发价" not in result_labels, "7.21模板删除的批发价不得残留")
        _assert("权益保障" not in result_labels, "7.21模板删除的权益保障不得残留")
        _assert("代发权益" not in result_labels, "7.21模板删除的代发权益不得残留")
        _assert("24小时揽收率" not in result_labels, "7.21模板删除的24小时揽收率不得残留")
    persisted = service.get_run_payload(data["run_id"])
    _assert(persisted and persisted.get("filter_results"), "筛选执行审计应继续保存在同批次JSON快照")
    _assert(persisted.get("sample_data") is True, "样例真实性标记应继续保存在JSON快照")


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
            _assert("样例说明" not in workbook_xml, "含旧样例行的导出也只能保留唯一主表")
            _assert("开发样例" not in result_xml, "数据模式等审计字段不得挤占用户确认的24列主表")
            _assert("legacy-sample" in result_xml, "原主结果行应保留在唯一主表")
            _assert("legacy-sample-excluded" in result_xml, "旧样例筛除行也应汇总进唯一主表")
            _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "旧样例导出不得恢复附表")
            _assert(legacy_row["data_mode"] == "开发样例", "样例审计状态应保留在内存/JSON数据")
            _assert("禁止作为真实选品" in legacy_row["data_truth_note"], "样例真实性说明应保留在内存/JSON数据")


def test_export_combines_empty_primary_sources_and_deduplicates():
    unknown = {
        "item_id": "combined-unknown",
        "title": "待类目核验商品",
        "url": "https://detail.1688.com/offer/combined-unknown.html",
        "list_source": "rpa",
    }
    duplicate = dict(unknown)
    duplicate["title"] = "同一商品在筛除集合中的重复副本"
    excluded = {
        "item_id": "combined-excluded",
        "title": "筛除但仍需展示商品",
        "url": "https://detail.1688.com/offer/combined-excluded.html",
        "list_source": "rpa",
        "filter_verification_status": "filtered_out",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "combined.xlsx")
        export_xlsx(
            [],
            output_path,
            {
                "category_unknown_rows": [unknown],
                "filter_excluded_rows": [duplicate, excluded],
            },
        )
        with zipfile.ZipFile(output_path) as workbook:
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            _assert("combined-unknown" in result_xml, "rows为空时待核验商品仍应进入唯一主表")
            _assert("combined-excluded" in result_xml, "rows为空时筛除商品仍应进入唯一主表")
            _assert("同一商品在筛除集合中的重复副本" not in result_xml, "同一商品跨来源重复时只应保留第一份")
            _assert(result_xml.count("<row ") == 3, "唯一主表应包含1行表头和2行去重商品")
        _assert(unknown.get("manual_review_status"), "导出前自动复核状态仍应回写原始JSON行，不能只写临时Excel副本")


def test_rpa_progress_is_streamed_before_node_completion():
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / "progress_test.mjs"
        script_path.write_text(
            "process.stdin.resume();\n"
            "process.stdin.on('end', () => {\n"
            "  process.stderr.write('SOP_RPA_PROGRESS {\"stage\":\"manual_intervention\",\"message\":\"请人工验证\",\"alert_key\":\"smoke-alert\",\"percent\":35}\\n');\n"
            "  setTimeout(() => console.log(JSON.stringify({success:true, products:[]})), 500);\n"
            "});\n",
            encoding="utf-8",
        )
        original_script_path = rpa._script_path
        events = []
        started_at = time.monotonic()
        try:
            rpa._script_path = lambda _name: str(script_path)
            result = rpa._run_node_script(
                "progress_test.mjs",
                {},
                timeout=5,
                progress_callback=lambda event: events.append((time.monotonic(), event)),
            )
        finally:
            rpa._script_path = original_script_path
        _assert(result.get("success"), "Node测试脚本应正常返回最终JSON")
        _assert(events and events[0][1].get("stage") == "manual_intervention", "Python应识别Node实时人工介入事件")
        _assert(events[0][1].get("alert_key") == "smoke-alert", "实时事件应保留前端去重键")
        _assert(events[0][0] - started_at < 0.35, "人工介入事件必须在Node任务结束前实时回调，不能等500ms后才返回")


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
            _assert("来源未知/需核验" not in result_xml, "数据真实性审计不得挤占用户确认的24列主表")
            _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "来源未知行导出也只能保留唯一主表")
            _assert(unknown_row["data_mode"] == "来源未知/需核验", "无法证明来源的旧行应在JSON/内存审计信息中标为待核验")
            _assert("禁止直接作为真实选品" in unknown_row["data_truth_note"], "来源未知行必须保留禁止直接铺货说明")
            _assert(real_row["data_mode"] == "真实数据", "真实页面来源行应在JSON/内存审计信息中标为真实数据")


def test_rpa_detail_extracts_structured_fields_without_bad_shop_name():
    html = """
    <html>
      <body>
        <nav class="breadcrumb">
          <a href="https://www.1688.com/category/womenswear">女装、男装、内衣</a>
          <a href="https://www.1688.com/category/trousers">下装裤</a>
          <a href="https://www.1688.com/category/womens-jeans">女式牛仔裤</a>
        </nav>
        店铺 回头率 35%
        运费 首重8元续重4元
        2件起批 品退率 1.8% 发货率 98.6% 24小时揽收率 96% 48小时揽收率 99.1%
        48小时内发货 一件代发 7天无理由
        规格数量: 18 SKU 收藏客户 200+ 诚信通 5年 生产厂家 密文面单
      </body>
      <script>
        window.__INIT__ = {
          "shopName":"义乌童雨户外用品厂",
          "skuCount":"18",
          "shipmentRate":"98.6%",
          "categoryId":"1031910",
          "categoryPath":"女装、男装、内衣>下装裤>女式牛仔裤",
          "categoryBreadcrumb":[
            {"name":"女装、男装、内衣","id":"100001"},
            {"name":"下装裤","id":"100002"},
            {"name":"女式牛仔裤","id":"1031910"}
          ]
        };
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
    _assert(fields.get("collection_rate_48h") == "99.1%", "详情抽取应提取48小时揽收率")
    _assert(fields.get("sku_count") == "18", "详情抽取应提取 SKU 数量")
    _assert(fields.get("waybill_support") == "密文面单", "面单字段不应吞入 HTML/script 文本")
    _assert(fields.get("product_category_id") == "1031910", "详情抽取应优先保留结构化商品类目ID")
    _assert(
        fields.get("product_category_path") == "女装、男装、内衣>下装裤>女式牛仔裤",
        "详情抽取应输出完整商品类目路径",
    )
    _assert(
        "女式牛仔裤" in json.dumps(fields.get("product_category_breadcrumb", ""), ensure_ascii=False),
        "详情抽取应保留可审计的类目面包屑",
    )
    _assert(fields.get("category_evidence_source"), "详情类目证据应标记来源")


def test_rpa_detail_dropship_fields_require_explicit_evidence():
    html = """
    <html><body>
      代发价：￥12.80 月代发订单：326 月代销：1.2万 代发运费：8元
      页面仅展示价格和订单统计。
    </body></html>
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
    fields = json.loads((proc.stdout or "").splitlines()[-1]).get("fields", {})
    _assert(fields.get("dropship_price") == "12.80", "详情抽取应回填代发价")
    _assert(fields.get("monthly_dropship_orders") == "326", "详情抽取应回填月代发订单")
    _assert(fields.get("units_30d") == "1.2万", "月代销应映射到近30天件数")
    _assert(fields.get("dropship_shipping_fee") == "8元", "详情抽取应识别普通数值代发运费")
    _assert(not fields.get("supports_dropship"), "只有代发价/月代发订单文字时不能误判为支持一件代发")

    explicit_html = """
    <html><body>支持一件代发 代发价 9.90元 代发运费 3元 48小时内发货</body></html>
    """
    proc = subprocess.run(
        ["node", str(Path(__file__).resolve().parent / "rpa_detail.mjs")],
        input=json.dumps({"test_html": explicit_html}, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    fields = json.loads((proc.stdout or "").splitlines()[-1]).get("fields", {})
    _assert(fields.get("supports_dropship") == "是", "明确显示支持一件代发时才可回填为是")
    _assert("明确" in fields.get("dropship_rights", ""), "一件代发权益应说明证据是页面明确显示")


def test_rpa_detail_shipping_and_monthly_distribution_reopened_screenshot_cases():
    script = str(Path(__file__).resolve().parent / "rpa_detail.mjs")
    bucket_payload = {
        "test_html": """
        <html><body>
          月成交 &lt;10 月代销 &lt;10 年成交件数 100+ 年成交笔数 100+
          退货包运费 运费查询 运费￥10起
        </body></html>
        """,
        "test_dom_evidence": {
            "shipping": [{"text": "运费￥10起", "source": "document", "selector_hint": "span.freight"}],
            "monthly_sales": [{
                "text": "月成交 <10 月代销 <10 年成交件数 100+ 年成交笔数 100+",
                "source": "open_shadow_dom",
                "selector_hint": "div.assistant-stats",
            }],
        },
    }
    proc = subprocess.run(
        ["node", script],
        input=json.dumps(bucket_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(proc.returncode == 0, f"截图1详情抽取应执行成功：{proc.stderr or proc.stdout}")
    payload = json.loads((proc.stdout or "").splitlines()[-1])
    fields = payload.get("fields", {})
    evidence = payload.get("field_evidence", {})
    _assert(fields.get("units_30d") == "<10", "截图1的月代销<10应映射到近30天件数并保留小于号")
    _assert(not fields.get("monthly_dropship_orders"), "月代销不能伪装成月代发订单")
    _assert(fields.get("dropship_shipping_fee") == "10元起", "截图1的运费￥10起应标准化为10元起")
    _assert(evidence.get("units_30d", {}).get("kind") == "short_dom_label", "月代销应优先保存短DOM标签证据")
    _assert("运费￥10起" in evidence.get("dropship_shipping_fee", {}).get("text", ""), "运费记录应保留命中的短标签")

    magnifier_payload = {
        "test_html": """
        <html><body>
          月成交 2600+ 月代销 80+ 近30天代发数量 100以内 近7天代发数量 100以内
          退货包运费 运费查询 运费￥1.8起
        </body></html>
        """,
        "test_dom_evidence": {
            "shipping": [{"text": "运费￥1.8起", "source": "document", "selector_hint": "div.delivery-fee"}],
            "monthly_sales": [{"text": "月成交 2600+ 月代销 80+", "source": "open_shadow_dom"}],
        },
    }
    proc = subprocess.run(
        ["node", script],
        input=json.dumps(magnifier_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(proc.returncode == 0, f"截图2详情抽取应执行成功：{proc.stderr or proc.stdout}")
    payload = json.loads((proc.stdout or "").splitlines()[-1])
    fields = payload.get("fields", {})
    _assert(fields.get("units_30d") == "80+", "截图2应取月代销80+，不能被近30天代发数量100以内覆盖")
    _assert(fields.get("units_30d") != "100以内", "近30天代发数量是另一指标，不能覆盖月代销")
    _assert(fields.get("dropship_shipping_fee") == "1.8元起", "截图2的运费￥1.8起应标准化为1.8元起")

    interference_payload = {
        "test_html": "<html><body>退货包运费 运费查询 赠运费险 券后￥1.8起</body></html>",
    }
    proc = subprocess.run(
        ["node", script],
        input=json.dumps(interference_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    fields = json.loads((proc.stdout or "").splitlines()[-1]).get("fields", {})
    _assert(not fields.get("dropship_shipping_fee"), "只有退货包运费/运费查询/运费险时不得伪造运费金额")

    original_run_node_script = rpa._run_node_script
    rpa._run_node_script = lambda *args, **kwargs: {
        "success": True,
        "fields": {"dropship_shipping_fee": "1.8元起"},
        "field_evidence": {
            "dropship_shipping_fee": {
                "kind": "short_dom_label",
                "text": "运费￥1.8起",
                "source": "document",
                "selector_hint": "div.delivery-fee",
            },
        },
    }
    try:
        wrapped_fields = rpa.collect_detail_fields_from_1688_page(
            "https://detail.1688.com/offer/shipping-evidence-smoke.html",
            "shipping-evidence-smoke",
        )
    finally:
        rpa._run_node_script = original_run_node_script
    _assert(
        wrapped_fields.get("_field_evidence", {}).get("dropship_shipping_fee", {}).get("text") == "运费￥1.8起",
        "Python RPA适配层不得丢弃Node返回的字段级证据",
    )

    records = service._build_verification_evidence(
        "shipping-evidence-smoke",
        wrapped_fields,
        source="1688_detail_page_rpa",
        source_url="https://detail.1688.com/offer/shipping-evidence-smoke.html",
        mode="real",
        status=service.VERIFICATION_STATUS_PARTIAL,
    )
    shipping_record = next(record for record in records if record.get("field_key") == "dropship_shipping_fee")
    _assert(shipping_record.get("evidence_kind") == "short_dom_label", "服务层核验记录应保留运费证据类型")
    _assert(shipping_record.get("evidence_text") == "运费￥1.8起", "服务层核验记录应保留短证据文本")
    _assert(
        service._match_metric_rule("1.8元起", {"op": "range", "min": 1, "max": 5}) is None,
        "起步运费只有下限，不能冒充确定落在1-5元区间内",
    )
    _assert(service._match_metric_rule("<10", {"op": "<", "value": 10}) is True, "<10应保留区间语义")
    _assert(
        service._match_metric_rule("100以内", {"op": ">=", "value": 100}) is None,
        "100以内不能冒充确定达到100",
    )


def test_rpa_detail_waits_for_assistant_metrics_and_keeps_shipping_evidence_consistent():
    script = str(Path(__file__).resolve().parent / "rpa_detail.mjs")
    delayed_payload = {
        "test_html": "<html><body>分销代发 1件包邮 ￥148.4 退货包运费 运费查询</body></html>",
        "test_dom_evidence": {
            "shipping": [{"text": "运费￥10起", "source": "document", "selector_hint": "span.service-item"}],
        },
        "test_dom_evidence_sequence": [
            {},
            {"monthly_sales": [{"text": "月成交 200+ 月代销 100+", "source": "document", "selector_hint": "div.goods-operation-panel-media"}]},
        ],
    }
    proc = subprocess.run(
        ["node", script],
        input=json.dumps(delayed_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(proc.returncode == 0, f"延迟采购助手证据抽取应执行成功：{proc.stderr or proc.stdout}")
    payload = json.loads((proc.stdout or "").splitlines()[-1])
    fields = payload.get("fields", {})
    evidence = payload.get("field_evidence", {})
    _assert(fields.get("units_30d") == "100+", "后台详情页应等待采购助手异步出现月代销，而不是提前写入空值")
    _assert(fields.get("dropship_shipping_fee") == "10元起", "数字运费证据必须优先于页面其它位置的一件包邮文案")
    _assert(fields.get("free_shipping") == "否", "同页已有10元起运费时，分销区的一件包邮不能把当前运费口径改成包邮")
    _assert(
        evidence.get("units_30d", {}).get("selector_hint") == "div.goods-operation-panel-media",
        "月代销应保留采购助手面板的DOM证据位置",
    )

    shipping_module_payload = {
        "test_html": "<html><body>商品详情</body></html>",
        "test_dom_evidence": {
            "shipping": [{"text": "广东广州 送至上海 包邮", "source": "document", "selector_hint": "div#shippingServices"}],
            "monthly_sales": [{"text": "月代销 <10", "source": "document"}],
        },
    }
    proc = subprocess.run(
        ["node", script],
        input=json.dumps(shipping_module_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    fields = json.loads((proc.stdout or "").splitlines()[-1]).get("fields", {})
    _assert(fields.get("dropship_shipping_fee") == "包邮", "发货模块明确显示包邮时，运费不能继续留成待核验")
    _assert(fields.get("free_shipping") == "是", "发货模块明确包邮时是否包邮应保持一致")


def test_detail_filters_quarantine_unverified_or_out_of_range_rows():
    config = parse_input(
        categories="日用餐厨、居家日用>厨房工具>冰格",
        tags="一件代发",
        sample_data=True,
        library_filters={"dropship_shipping_fee_min": "1", "dropship_shipping_fee_max": "5"},
    )
    plan = build_filter_plan(config)
    _assert(
        any(rule.get("field") == "supports_dropship" and rule.get("op") == "equals" for rule in plan["post_filters"]),
        "选择一件代发后必须增加详情页明确证据的二次硬筛",
    )
    row = product_to_export_row(service.SAMPLE_PRODUCTS[0], "冰格", config)
    _, records = service._evaluate_post_filters(row, plan["post_filters"])
    row["filter_match_records"] = records
    payload = {
        "sample_data": True,
        "rows": [row],
        "filter_excluded_rows": [],
        "filter_signature": "smoke",
        "target_platform": "",
    }
    service._apply_detail_filter_exclusions(payload)
    _assert(not payload["rows"], "详情筛选字段没有核实的商品不能继续留在主结果")
    _assert(payload["filter_excluded_rows"][0].get("exclusion_type") == "detail_filter_unverified", "未核实商品应进入异常复核而不是假装命中")

    verified = product_to_export_row(service.SAMPLE_PRODUCTS[0], "冰格", config)
    verified["dropship_shipping_fee"] = "8元"
    verified["supports_dropship"] = "是"
    verified["detail_verified_fields"] = ["dropship_shipping_fee", "supports_dropship"]
    records = service.reevaluate_row_filters_after_verification(verified, plan)
    _assert(any(record.get("field_key") == "dropship_shipping_fee" and record.get("status") == "filtered_out" for record in records), "8元运费不能通过1-5元筛选")


def test_auto_verify_limit_and_category_export_filename_cover_full_batch():
    config = parse_input(sample_data=True, auto_verify_details=True, auto_verify_max_items=200, max_items_per_query=200)
    _assert(config.auto_verify_max_items == 200, "自动详情核验不应继续被旧的20条硬上限截断")
    rows = [{"item_id": str(index)} for index in range(200)]
    _assert(service._auto_verify_limit(config, rows) == 200, "自动详情核验应允许覆盖本轮全部候选")

    single = service._safe_export_filename(
        ["日用餐厨、居家日用>厨房工具>冰格"],
        "20260720_120000_000",
        "xlsx",
    )
    _assert("厨房工具﹥冰格" in single and "20260720_120000_000" in single, "单叶子导出文件名应使用完整类目并保留批次")
    _assert(not re.search(r'[<>:"/\\|?*]', single), "导出文件名必须兼容 Windows 非法字符限制")

    grandchildren = service.TAG_CATEGORY_TREE["日用餐厨、居家日用"]["厨房工具"]
    whole_secondary = [f"日用餐厨、居家日用>厨房工具>{leaf}" for leaf in grandchildren]
    grouped = service._safe_export_filename(whole_secondary, "20260720_120000_001", "xlsx")
    _assert("日用餐厨、居家日用﹥厨房工具_" in grouped, "全选整个二级类目时文件名应收敛为二级类目名")


def test_rpa_detail_does_not_treat_url_category_id_as_product_evidence():
    html = """
    <html>
      <head>
        <link rel="canonical" href="https://detail.1688.com/offer/1234567890.html">
      </head>
      <body>
        <a href="https://s.1688.com/selloffer/offer_search.htm?categoryId=777001">
          查看同类商品
        </a>
        普通商品详情，页面没有商品结构化类目、类目路径或类目面包屑。
      </body>
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
    _assert(
        not fields.get("product_category_id"),
        "只有搜索/导航URL上的categoryId时，不能误当作商品自身类目ID",
    )
    _assert(
        not fields.get("product_category_path") and not fields.get("product_category_breadcrumb"),
        "没有商品结构化类目或面包屑时，不应伪造商品类目路径",
    )


def test_security_verification_error_message():
    message = friendly_collect_error(
        Exception(
            "security_verification_required: 已等待你手动处理 1688 安全验证，"
            "但当前页面仍停留在滑块/验证码校验。系统不会绕过或自动破解验证。"
            "当前页面：https://s.1688.com/selloffer/offer_search.htm/punish?x5secdata=temporary-token"
        )
    )
    _assert("安全验证" in message or "滑块" in message, "滑块验证应给出明确人工处理提示")
    state = collect_error_state(ServiceError(message))
    _assert(state["code"] == "security_verification_required", "滑块验证应返回结构化风控状态")
    _assert(state["action"] == "manual_handoff", "滑块验证应进入人工接管")
    _assert(state["retryable"] is False, "滑块验证不应提示自动重试")
    _assert("不会绕过" in message or "不会继续采集" in message, "提示应明确不会绕过验证码")
    _assert("x5secdata" not in message, "用户提示不应展示风控页超长临时参数")


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


def test_wechat_hanfu_adult_title_is_not_publishable():
    config = parse_input(
        categories="女装、男装、内衣>新中式>汉服套装",
        tags="微信小店",
        sample_data=False,
        collect_source="rpa",
    )
    row = product_to_export_row(
        Product(
            id="hanfu-risk-1",
            title="新款古装大码汉服套装睡衣女性感古代肚兜制服雪纺纱睡裙古风纯欲",
            price="9.3",
            image="",
            url="https://detail.1688.com/offer/hanfu-risk-1.html",
            stats={
                "last30DaysSales": 2000,
                "last30DaysDropShippingSales": 1000,
                "goodRates": 0.98,
                "collectionRate24h": 0.96,
            },
        ),
        "",
        config,
    )
    _assert("汉服套装结果疑似偏成人/睡衣场景" in row["risk_flags"], "汉服成人/睡衣倾向标题应打风险")
    _assert(row["recommendation_score"] <= 35, "标题风险应压低推荐分")
    _assert(row["wechat_shop_suggestion"] == "不建议", "成人/睡衣倾向商品不能作为微信小店可铺候选")


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
    _assert("Math.min(200" in script, "RPA 单次候选硬上限应提高到200条")
    _assert("estimatedResultPages" in script and "Math.min(12" in script, "分页页数应按候选目标动态估算且最多12页")
    _assert("resultPageFingerprint" in script, "翻页后应比较商品指纹，避免重复读取同一页")
    _assert("page_not_changed" in script, "翻页点击后页面未变化时应停止并记录原因")
    _assert("requested_limit_reached" in script and "max_pages_reached" in script, "分页应记录明确停止原因")
    _assert("paginationRoots" in script, "分页控件查找应限制在分页区域，避免误点页面其它箭头")
    _assert("const target = numberNode || nextNode" in script, "翻页应优先点击明确页码，再回退下一页按钮")
    _assert("applyCategoryFilters" in script, "真实采集应支持按1688左侧类目点击")
    _assert("category_path" in script, "类目筛选不应退化为关键词搜索")
    _assert("categoryNeedleSets" in script, "类目点击应支持把本地类目名映射到1688页面可见入口")
    _assert("replace(/[\\uE000-\\uF8FF]/g" in script, "类目点击应忽略1688首页图标字体，避免组合一级类目误判不存在")
    _assert("replace(/[\\s、，,\\/／|｜·•\\-]+/g" in script, "类目点击应忽略顿号/斜杠等分隔符，匹配1688组合一级类目")
    _assert("index < needleSets.length - 1" in script, "三级类目应先 hover 一级/二级，不能点击二级后跳成关键词结果")
    _assert("followCategoryClick" in script, "类目点击后应跟随新页或URL变化")
    _assert("collectCategoryDiagnostics" in script, "类目点击失败应返回页面可见类目诊断")
    _assert("clickCategoryLeafLink" in script and 'mode: "direct_link"' in script, "1688 首页隐藏叶子类目链接应有兜底跳转")
    _assert('fallback_mode: "direct_href"' in script, "悬浮菜单收起导致叶子元素不可见时，应使用已确认的1688叶子链接直达结果页")
    _assert("releaseForManualHandoff" in script, "CDP 命中登录/安全验证时应保留真实 Chrome 页签供人工处理")
    _assert("waitForManualSecurityResolution" in script, "滑块出现后应等待人工处理并尝试继续同一任务")
    _assert("TAG_COLLECT_RPA_MANUAL_VERIFY_WAIT_MS" in script, "人工滑块等待时限应可配置")
    _assert("人工处理已完成" in script and "继续同一采集任务" in script, "滑块恢复后状态栏应明确同任务续跑")
    _assert("sop-1688-workbench-overlay" in script, "正在操作的1688页面应注入独立悬浮工作状态栏")
    _assert("scrollWithinNearestContainer" in script, "类目菜单应只滚动最近的可滚动容器")
    _assert("TAG_COLLECT_RPA_CATEGORY_SCROLL_STEPS" in script, "类目懒加载扫描应有可配置且有限的局部滚动次数")
    _assert("category_scroll_scan" in script, "类目滚动扫描应返回次数、停止原因和容器证据")
    _assert("target_found_after_category_scroll" in script, "局部滚动后必须重新查找目标，不能沿用滚动前的空结果")
    _assert("no_remaining_category_scroll_container" in script, "滚动到底仍找不到类目时应有限停止并返回明确原因")
    _assert("reopenCategoryParents" in script, "悬浮菜单超时收起后应快速重新悬浮父级路径再点叶子类目")
    _assert("preciseHoverTarget" in script, "二级类目悬浮必须优先落在精确标题节点，不能移动到巨大li容器中心")
    _assert("pointer_target_element" in script, "类目诊断应记录真实悬浮目标元素及尺寸")
    _assert("pointer_text_hit_box" in script and 'pointer_target_strategy: usableTextHitBox ? "direct_text_range"' in script, "高标题节点应使用文字Range而非元素中心作为真实鼠标落点")
    _assert('pointerRoute = "category_container_corridor"' in script, "一级到下方类目必须沿菜单容器走廊移动，不能斜穿其它一级类目和首页内容")
    _assert('container.style.setProperty("scroll-behavior", "auto", "important")' in script, "类目容器滚动必须临时关闭smooth并等待落位")
    _assert("reopen_not_visible" in script, "父级表面命中但下一层仍无尺寸时不得误报恢复成功")
    _assert("hidden_exact_matches" in script, "类目未找到时应区分隐藏目标和真正不存在")
    _assert("if (!actionResult?.ok)" in script, "类目动作未命中时不得额外空等5秒弹窗超时")
    _assert("page_scroll_restored" in script, "类目交互如意外滚动网页应恢复原页面位置并记录证据")
    _assert("page.mouse.move" in script, "类目悬停必须使用真实浏览器指针移动，不能只派发脚本合成事件")
    _assert("page.mouse.click" in script, "类目点击必须使用真实浏览器指针，避免框架忽略脚本合成点击")
    _assert('pointer_interaction: "playwright_mouse"' in script, "类目诊断应记录真实指针交互方式")
    _assert("dispatchEvent(new PointerEvent" not in script, "类目菜单不能再依赖非可信的脚本合成PointerEvent")
    _assert('best.clickable.scrollIntoView({ block: "center", inline: "center" })' not in script, "类目目标不能再用scrollIntoView带动整个网页滚动")
    _assert("filter_results: categoryResults" in script, "类目点击后无商品卡片时也应返回类目执行记录")
    _assert("deferred_navigation" not in script, "多级类目不能用父级类目 href 兜底跳到关键词结果页")
    _assert("navigated_parent_href" not in script, "多级类目缺二级时不应跳父级关键词结果页")
    _assert('"男士内裤":' not in script, "RPA 不应为当前1688类目不存在的男士内裤保留模糊别名")
    _assert("raw.split(\"、\")" not in script, "组合一级类目不能按顿号拆开，否则会误点单独父类")
    _assert("strictMatch" in script and "directTextOf" in script, "组合一级类目应使用直接文本精确匹配，不能命中宽泛祖先容器")
    _assert("categoryIdConfirmed" in script, "类目结果确认应优先核对点击类目ID与结果页类目ID")
    _assert("fallback_keyword" not in Path(__file__).with_name("service.py").read_text(encoding="utf-8"), "类目失败不应降级为关键词搜索")
    _assert("manual_page_ambiguous" in script, "人工当前页模式存在多个1688页签时应阻断，不能静默读错页")
    _assert("noDefaults: true" in script, "CDP 连接应保留浏览器默认下载/媒体设置，避免真实 Chrome 初始化失败")
    _assert("browser.close().catch" in script, "人工当前页模式读取后应断开 Playwright CDP 连接")


def test_rpa_category_scroll_finds_lazy_target_without_scrolling_page():
    node_bin = os.environ.get("NODE_BIN") or shutil.which("node")
    _assert(node_bin, "类目局部滚动行为回归需要 Node.js")
    script_path = Path(__file__).with_name("rpa_collect.mjs")
    proc = subprocess.run(
        [node_bin, str(script_path)],
        input=json.dumps({"self_test": "category_scroll", "category_scroll_max_steps": 12}, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
        encoding="utf-8",
    )
    _assert(proc.returncode == 0, f"类目局部滚动行为测试进程失败：{proc.stderr or proc.stdout}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    _assert(lines, f"类目局部滚动行为测试没有输出：{proc.stderr}")
    payload = json.loads(lines[-1])
    _assert(payload.get("success") is True, f"类目局部滚动行为测试失败：{payload}")
    found = payload.get("found") or {}
    missing = payload.get("missing") or {}
    reopen = payload.get("reopen") or {}
    _assert(found.get("ok") is True and found.get("click_count") == 1, "目标类目懒加载后应被重新定位并用真实鼠标点击一次")
    _assert(found.get("menu_scroll_top", 0) > 0 and found.get("attempts", 0) > 0, "目标位于下方时必须实际滚动局部类目容器")
    _assert(found.get("page_scroll_before") == found.get("page_scroll_after"), "寻找下方类目时不得改变 window.scrollY")
    _assert(missing.get("ok") is False, "滚动到底仍不存在的类目不能误报成功")
    _assert(0 < missing.get("attempts", 0) <= missing.get("max_steps", 0), "不存在的类目必须在有限滚动次数内停止")
    _assert(
        missing.get("stop_reason") in {"no_remaining_category_scroll_container", "category_scroll_max_steps_reached"},
        "滚动到底仍找不到类目时应返回可诊断停止原因",
    )
    _assert(missing.get("page_scroll_before") == missing.get("page_scroll_after"), "失败扫描也不得滚动整个网页")
    _assert(reopen.get("parents_opened_initially") is True, "级联菜单负例应先成功展开一级和二级")
    l2_pointer_target = reopen.get("l2_pointer_target") or {}
    l2_text_hit_box = reopen.get("l2_text_hit_box") or {}
    _assert(l2_pointer_target.get("tag") == "a", "高li容器中的二级类目必须定位精确标题a节点")
    _assert((l2_pointer_target.get("rect") or {}).get("height", 0) >= 90, "负例必须覆盖1688真实页面中的高标题a节点")
    _assert(0 < l2_text_hit_box.get("height", 0) < (l2_pointer_target.get("rect") or {}).get("height", 0), "鼠标应落在标题文字Range而非高a节点中心")
    _assert(reopen.get("l2_menu_scroll_top", 0) > 0 and reopen.get("l2_target_within_container") is True, "被菜单裁剪的下方类目必须先完成局部滚动并进入可视区")
    _assert(reopen.get("l2_pointer_route") == "category_container_corridor", "从一级到下方二级类目应沿菜单容器内部移动")
    _assert(reopen.get("l2_pointer_target_strategy") == "direct_text_range", "二级类目真实鼠标应命中文字Range")
    _assert(reopen.get("leaf_failed_after_timeout") is True and reopen.get("hidden_match_count", 0) >= 1, "菜单超时收起时应识别到隐藏叶子而非误判网站无此类目")
    _assert(reopen.get("parents_reopened") is True and reopen.get("recovery_status") == "reopened", "菜单收起后必须重新悬浮父级路径")
    _assert(reopen.get("next_target_visible") is True, "恢复父级后必须确认下一层目标已获得非零尺寸")
    _assert(reopen.get("non_visible_recovery_ok") is False, "父级显示命中但下一层仍隐藏时不得误报恢复成功")
    _assert(reopen.get("non_visible_recovery_status") == "reopen_not_visible", "下一层仍隐藏时应返回reopen_not_visible诊断")
    _assert(reopen.get("non_visible_recovery_target_visible") is False, "reopen_not_visible负例必须保留下一层零尺寸证据")
    _assert(reopen.get("l2_hover_count", 0) >= 3, "初次展开、成功恢复和失败恢复都必须真正悬浮二级标题")
    _assert(reopen.get("leaf_clicked_after_reopen") is True and reopen.get("click_count") == 1, "重新展开后应只点击一次目标叶子类目")


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
    _assert('for="maxItems">候选扫描上限' in html, "Web 应单独展示候选扫描上限")
    _assert('id="targetPublishableCount"' in html, "Web 应把目标可铺数与候选扫描预算分开")
    _assert('value="200"' in html and 'max_items_per_query: Number($("maxItems").value || 200)' in html, "Web 深度采集默认应提高到200条")
    _assert('target_publishable_count: Number($("targetPublishableCount").value || 60)' in html, "Web 默认目标可铺数应为60条")
    _assert('id="autoVerifyMax" type="number" min="1" max="200" value="200"' in html, "详情补字段默认应允许覆盖200个候选，不能仍停在20条")
    _assert('role="dialog"' in html and 'data-category-view="favorite"' in html, "类目选择应使用弹窗并提供收藏类目视图")
    _assert("FAVORITE_CATEGORY_STORAGE_KEY" in html and "localStorage.setItem" in html, "收藏类目应在当前浏览器持久化")
    _assert('id="categoryModalSummary"' in html and 'id="confirmCategoryPanel"' in html, "类目弹窗应有已选摘要和确定操作")
    _assert('if ($("collectSource")) $("collectSource").value = "rpa";' in html, "初始化/重置后应回到自动批量采集")
    _assert("请先运行 scripts/capabilities/tag_collect/start_chrome_debug.sh" not in html, "Web 文案不应再要求用户手动运行终端脚本")


def test_windows_chrome_launcher_uses_available_browser():
    with tempfile.TemporaryDirectory() as tmp_dir:
        browser = Path(tmp_dir) / "msedge.exe"
        browser.touch()
        profile = Path(tmp_dir) / "ChromeProfile"
        old_values = {
            key: os.environ.get(key)
            for key in ("CHROME_BIN", "TAG_COLLECT_CHROME_PROFILE", "TAG_COLLECT_CDP_URL")
        }
        original_platform_system = web.platform.system
        original_popen = web.subprocess.Popen
        original_runtime_status = web._chrome_runtime_status
        calls = []

        def fake_popen(args, **kwargs):
            calls.append((args, kwargs))
            return types.SimpleNamespace(pid=1234)

        try:
            os.environ["CHROME_BIN"] = str(browser)
            os.environ["TAG_COLLECT_CHROME_PROFILE"] = str(profile)
            web.platform.system = lambda: "Windows"
            web.subprocess.Popen = fake_popen
            web._chrome_runtime_status = lambda: {"cdp_ready": True, "message": "ready"}
            runtime = web._start_chrome_debug_browser()
        finally:
            web.platform.system = original_platform_system
            web.subprocess.Popen = original_popen
            web._chrome_runtime_status = original_runtime_status
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        _assert(runtime["cdp_ready"], "Windows 浏览器启动后应返回 CDP ready 状态")
        _assert(len(calls) == 1, "Windows 浏览器启动器只应创建一个进程")
        args, kwargs = calls[0]
        _assert(args[0] == str(browser), "Windows 启动器应使用已发现的 Chrome/Edge")
        _assert("--remote-debugging-address=127.0.0.1" in args, "CDP 必须只监听本机")
        _assert(f"--remote-debugging-port={web.DEFAULT_CDP_PORT}" in args, "Windows 启动器应使用配置的 CDP 端口")
        _assert(f"--user-data-dir={profile}" in args, "Windows 启动器应使用项目专用浏览器 profile")
        _assert(kwargs.get("close_fds") is True, "Windows 浏览器进程不应继承服务端句柄")


def test_web_category_selection_does_not_leak_between_tasks():
    html = web.HTML_PAGE
    mode_input = re.search(
        r"<input\b[^>]*\bid=[\"']multiCategoryMode[\"'][^>]*>",
        html,
        flags=re.I,
    )
    _assert(mode_input, "Web应提供id=multiCategoryMode的多类目模式复选框")
    mode_tag = mode_input.group(0)
    _assert(re.search(r"\btype=[\"']checkbox[\"']", mode_tag, flags=re.I), "多类目模式应使用checkbox显式控制")
    _assert(not re.search(r"\bchecked\b", mode_tag, flags=re.I), "多类目模式默认必须未选，避免新任务累积旧类目")

    selection_source = _javascript_function_source(html, "setCategorySelection")
    _assert(selection_source, "Web应集中通过setCategorySelection(value, selected)维护类目Set")
    _assert("multiCategoryMode" in selection_source, "类目选择函数必须读取多类目模式开关")
    _assert("state.selectedCategories.clear()" in selection_source, "单类目选择新值前必须清空旧类目Set")
    _assert(
        re.search(r"state\.selectedCategories\.add\((?:value|path)\)", selection_source),
        "选中类目时应把当前值写入Set",
    )
    _assert(
        re.search(r"state\.selectedCategories\.delete\((?:value|path)\)", selection_source),
        "取消类目时应从Set移除当前值",
    )

    mode_helper_source = _javascript_function_source(html, "multiCategoryModeEnabled")
    _assert(mode_helper_source, "类目选择函数应通过统一helper读取多类目模式")

    node_script = f"""
      const controls = {{multiCategoryMode: {{checked: false}}}};
      const $ = (id) => controls[id];
      const state = {{selectedCategories: new Set(["旧任务类目"])}};
      const renderCategories = () => {{}};
      const renderLibraryFilters = () => {{}};
      const updateSelectedSummary = () => {{}};
      {mode_helper_source}
      {selection_source}
      setCategorySelection("新任务类目", true);
      if (JSON.stringify([...state.selectedCategories]) !== JSON.stringify(["新任务类目"])) {{
        throw new Error("single_category_mode_did_not_replace_old_selection");
      }}
      controls.multiCategoryMode.checked = true;
      setCategorySelection("第二个类目", true);
      if (!state.selectedCategories.has("新任务类目") || !state.selectedCategories.has("第二个类目") || state.selectedCategories.size !== 2) {{
        throw new Error("multi_category_mode_did_not_accumulate");
      }}
      setCategorySelection("新任务类目", false);
      if (state.selectedCategories.has("新任务类目") || !state.selectedCategories.has("第二个类目")) {{
        throw new Error("category_deselect_failed");
      }}
    """
    proc = subprocess.run(
        ["node", "-e", node_script],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(proc.returncode == 0, f"类目选择JS轻量行为测试失败：{proc.stderr or proc.stdout}")

    _assert(
        "setCategorySelection(target.value, target.checked)" in html,
        "类目checkbox必须统一经过setCategorySelection，不能直接累积旧Set",
    )
    _assert(
        "setCategorySelection(path, true)" in html,
        "二三级类目点击必须统一经过setCategorySelection，不能直接累积旧Set",
    )

    clear_source = _javascript_function_source(html, "clearRunState")
    _assert(clear_source, "Web应提供clearRunState清理上一任务结果")
    _assert(
        "state.selectedCategories.clear()" not in clear_source,
        "清理旧任务结果时不能同时清空本次即将提交的当前类目Set",
    )

    collect_source = _javascript_function_source(html, "runCollect")
    _assert(collect_source, "Web应提供runCollect采集入口")
    clear_index = collect_source.find("clearRunState()")
    fetch_index = collect_source.find('fetch("/api/task/start"')
    _assert(clear_index >= 0, "runCollect发起新任务前必须调用clearRunState清掉旧结果/批次")
    _assert(fetch_index >= 0 and clear_index < fetch_index, "clearRunState必须发生在新/api/task/start请求发出之前")
    direct_categories = re.search(
        r"categories\s*:\s*\[\s*\.\.\.state\.selectedCategories\s*\]",
        collect_source,
    )
    snapshotted_categories = (
        re.search(
            r"const\s+taskCategories\s*=\s*\[\s*\.\.\.state\.selectedCategories\s*\]",
            collect_source,
        )
        and re.search(r"categories\s*:\s*taskCategories\b", collect_source)
    )
    _assert(
        direct_categories or snapshotted_categories,
        "新请求payload.categories必须直接使用当前selectedCategories Set或其即时快照",
    )


def test_web_task_scope_single_category_and_legacy_multi_compatibility():
    old_category = "日用餐厨、居家日用>遮阳防雨>雨衣雨披"
    new_category = "女装、男装、内衣>下装裤>女式牛仔裤"
    single_payload = {
        "categories": [old_category, new_category],
        "library_filters": {
            "category_paths": [old_category, new_category],
            "search_keyword": "牛仔裤",
        },
        "multi_category_mode": False,
    }
    categories, library_filters, multi_mode = web._normalized_web_task_scope(single_payload)
    _assert(multi_mode is False, "显式关闭多类目模式时后端应进入单类目任务防线")
    _assert(categories == [new_category], "单类目模式categories即使混入旧+新，也只能保留最后新类目")
    _assert(
        library_filters.get("category_paths") == [new_category],
        "单类目模式library_filters.category_paths也必须同步收敛到最后新类目",
    )
    _assert(library_filters.get("search_keyword") == "牛仔裤", "收敛任务类目时不能丢失其它高级筛选值")

    legacy_payload = {
        "categories": [old_category, new_category],
        "library_filters": {
            "category_paths": [old_category, new_category],
            "search_keyword": "牛仔裤",
        },
    }
    legacy_categories, legacy_filters, legacy_multi_mode = web._normalized_web_task_scope(legacy_payload)
    _assert(legacy_multi_mode is True, "缺省multi_category_mode时应保持旧API多类目兼容")
    _assert(legacy_categories == [old_category, new_category], "旧API未传multi flag时不能擅自裁掉历史多类目请求")
    _assert(
        legacy_filters.get("category_paths") == [old_category, new_category],
        "旧API兼容模式应保留library_filters中的多类目路径",
    )


def test_web_category_switch_preserves_library_filters_and_collecting_guard():
    html = web.HTML_PAGE
    render_library_source = _javascript_function_source(html, "renderLibraryFilters")
    restore_library_source = _javascript_function_source(html, "restoreLibraryFilterValues")
    _assert(render_library_source and restore_library_source, "Web应提供高级筛选重绘和恢复函数")
    _assert(
        "restoreLibraryFilterValues()" in render_library_source,
        "renderLibraryFilters重建DOM后必须恢复state.selectedLibrary中的原筛选值",
    )
    for selector in (
        "[data-library-list]",
        "[data-library-bool]",
        "[data-library-radio]",
        "[data-library-select]",
        "[data-library-text]",
        "[data-library-range]",
    ):
        _assert(selector in restore_library_source, f"高级筛选恢复函数应覆盖{selector}")

    checkbox_anchor = 'if (target.dataset.kind === "category") {'
    checkbox_start = html.find(checkbox_anchor)
    _assert(checkbox_start >= 0, "应存在类目checkbox切换处理")
    checkbox_scope = html[checkbox_start:checkbox_start + 1200]
    _assert("collectLibraryFilters();" in checkbox_scope, "checkbox切换类目前应先快照当前高级筛选")
    _assert("renderLibraryFilters();" in checkbox_scope, "checkbox切换类目后应按新类目重绘高级筛选")
    _assert(
        checkbox_scope.find("collectLibraryFilters();") < checkbox_scope.find("renderLibraryFilters();"),
        "checkbox类目切换必须先保存再重绘，避免高级筛选丢失",
    )

    nav_anchor = 'const nav = event.target.closest("[data-category-nav]");'
    nav_start = html.find(nav_anchor)
    _assert(nav_start >= 0, "应存在二三级类目导航点击处理")
    nav_scope = html[nav_start:nav_start + 1800]
    _assert("collectLibraryFilters();" in nav_scope, "二三级类目点击前应先快照当前高级筛选")
    _assert("renderLibraryFilters();" in nav_scope, "二三级类目点击后应重绘高级筛选")
    _assert(
        nav_scope.find("collectLibraryFilters();") < nav_scope.find("renderLibraryFilters();"),
        "二三级类目切换必须先保存再重绘，避免高级筛选丢失",
    )

    collecting_source = _javascript_function_source(html, "setCollecting")
    collect_source = _javascript_function_source(html, "runCollect")
    _assert(collecting_source and collect_source, "Web应提供统一采集并发状态控制")
    for control_id in ("runBtn", "stickyRunBtn", "readCurrentPageBtn"):
        _assert(control_id in collecting_source, f"setCollecting必须统一禁用{control_id}")
        _assert(
            f'$("{control_id}").addEventListener("click", runCollect)' in html,
            f"{control_id}必须统一进入带collecting guard的runCollect",
        )
    _assert("if (state.collecting)" in collect_source, "runCollect必须阻止并发重复请求")
    _assert("setCollecting(true)" in collect_source, "请求开始前必须统一进入collecting状态")
    _assert("setCollecting(false)" in collect_source, "runCollect finally必须恢复三个采集入口")


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


def test_rpa_requires_leaf_category_before_collect():
    try:
        parse_input(
            categories="女装、男装、内衣",
            sample_data=False,
            collect_source="rpa",
        )
    except ServiceError as exc:
        state = collect_error_state(exc)
        _assert(state["code"] == "category_path_invalid", "真实自动采集不应允许只选一级类目")
        _assert("最右侧" in state["message"] or "最右侧" in state["suggestion"], "提示应要求选到叶子类目")
    else:
        raise AssertionError("真实自动采集只选一级类目应在提交前阻断")

    config = parse_input(
        categories="女装、男装、内衣",
        sample_data=True,
        collect_source="rpa",
    )
    _assert(config.categories == ["女装、男装、内衣"], "样例模式仍允许一级类目做本地演示")


def test_all_category_paths_leaf_gate():
    tree = service.TAG_CATEGORY_TREE
    total_paths = 0
    leaf_paths = 0
    blocked_non_leaf = 0
    for parent, children in tree.items():
        total_paths += 1
        try:
            parse_input(categories=parent, sample_data=False, collect_source="rpa")
        except ServiceError as exc:
            _assert(collect_error_state(exc)["code"] == "category_path_invalid", f"一级类目应被叶子门禁阻断：{parent}")
            blocked_non_leaf += 1
        else:
            raise AssertionError(f"一级类目不应直接进入真实自动采集：{parent}")

        for child, grandchildren in (children or {}).items():
            child_path = f"{parent}>{child}"
            total_paths += 1
            if grandchildren:
                try:
                    parse_input(categories=child_path, sample_data=False, collect_source="rpa")
                except ServiceError as exc:
                    _assert(collect_error_state(exc)["code"] == "category_path_invalid", f"有下级的二级类目应被阻断：{child_path}")
                    blocked_non_leaf += 1
                else:
                    raise AssertionError(f"有下级的二级类目不应直接进入真实自动采集：{child_path}")
            else:
                parsed = parse_input(categories=child_path, sample_data=False, collect_source="rpa")
                _assert(parsed.categories == [child_path], f"无下级二级类目应可作为叶子类目：{child_path}")
                leaf_paths += 1

            for grandchild in grandchildren or []:
                grand_path = f"{child_path}>{grandchild}"
                total_paths += 1
                parsed = parse_input(categories=grand_path, sample_data=False, collect_source="rpa")
                _assert(parsed.categories == [grand_path], f"三级叶子类目应可进入真实自动采集：{grand_path}")
                leaf_paths += 1

    _assert(total_paths >= 1000, "全量类目门禁测试应覆盖当前同步字典的大部分路径")
    _assert(leaf_paths > 0 and blocked_non_leaf > 0, "全量类目门禁应同时覆盖叶子和非叶子路径")


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

    def fake_collect_products(query, limit, source_url="", native_filters=None, category_filters=None, manual_url_only=False, return_meta=False, **kwargs):
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

    def fake_collect_products(query, limit, source_url="", native_filters=None, category_filters=None, manual_url_only=False, return_meta=False, **kwargs):
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
        **kwargs,
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
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        calls.append({
            "query": query,
            "source_url": source_url,
            "category_filters": category_filters or [],
            "limit": limit,
        })
        item_id = f"cat-{len(calls)}"
        return {
            "products": [{
                "id": item_id,
                "title": f"{category_path} 测试商品",
                "price": "19.9",
                "image": "",
                "url": f"https://detail.1688.com/offer/{1000000000 + len(calls)}.html",
                "stats": {"rawText": f"{category_path} 真实页面列表候选"},
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
        with _temporary_tag_data_dir():
            config = parse_input(
                categories="女装、男装、内衣>新中式>汉服套装,日用餐厨、居家日用>遮阳防雨>雨衣雨披",
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
            _assert(sum(call["limit"] for call in calls) == DEFAULT_ITEMS_PER_QUERY, "多类目应共享任务候选预算，不能每个类目重复使用完整上限")
            _assert(max(call["limit"] for call in calls) <= DEFAULT_ITEMS_PER_QUERY // 2 + 1, "两个类目应近似均分候选预算")
            _assert(len(data["rows"]) == 2, "多类目独立采集应保留两个候选商品")
            _assert({row["selected_category_path"] for row in data["rows"]} == set(config.categories), "审计字段应保留完整1688导航路径")
            _assert(
                {row["category_path"] for row in data["rows"]}
                == {service._category_display_path(category) for category in config.categories},
                "导出商品类目不应把组合一级导航入口冒充真实大类",
            )
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_multi_category_partial_failure_still_exports_successful_results():
    calls = []

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        calls.append(category_path)
        if len(calls) == 2:
            error = ServiceError("category_navigation_not_loaded: 第二个类目菜单未稳定展开")
            error.data = {
                "code": "category_navigation_not_loaded",
                "category_path": category_path,
                "filter_results": [{
                    "filter_key": category_filter.get("key", "category_path_2"),
                    "label": f"类目:{category_path}",
                    "tag": category_path,
                    "status": "not_found",
                    "source": "1688_category_navigation",
                    "message": "第二个类目菜单未稳定展开",
                }],
            }
            raise error
        return {
            "products": [{
                "id": "partial-success-1",
                "title": f"{category_path} 测试商品",
                "price": "19.9",
                "image": "",
                "url": "https://detail.1688.com/offer/1999999999.html",
                "stats": {"rawText": f"{category_path} 真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "page_url": "https://s.1688.com/selloffer/category-success.html",
                "message": "已进入商品结果页",
            }],
            "pagination": {"pages_visited": 2, "collected_count": 1, "stop_reason": "source_exhausted"},
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir():
            config = parse_input(
                categories="女装、男装、内衣>新中式>汉服套装,日用餐厨、居家日用>遮阳防雨>雨衣雨披",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
            )
            result = run_tag_collect(config)
            data = result["data"]
            _assert(result["success"], "多类目已有成功数据时，后续单类目失败仍应返回部分成功")
            _assert(data["partial_failure_count"] == 1, "部分成功结果应记录失败类目数")
            _assert(data["collection_status"] == "partial", "部分成功结果应标记为partial")
            _assert(data["row_count"] == 1, "前一个成功类目的商品必须保留")
            _assert(Path(data["output_path"]).exists(), "部分成功也必须生成可下载导出文件")
            _assert(data["partial_failures"][0]["category_path"] == config.categories[1], "失败记录应指向实际失败类目")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_repeated_category_navigation_failures_stop_remaining_home_refreshes():
    calls = []

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        calls.append(category_path)
        error = ServiceError("category_navigation_not_loaded: 首页类目菜单没有展开")
        error.data = {
            "code": "category_navigation_not_loaded",
            "category_path": category_path,
            "page_url": "https://www.1688.com/",
            "filter_results": [{
                "filter_key": category_filter.get("key", f"category_path_{len(calls)}"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "not_found",
                "source": "1688_category_navigation",
                "page_url": "https://www.1688.com/",
                "message": "首页类目菜单没有展开",
            }],
        }
        raise error

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir():
            config = parse_input(
                categories=(
                    "日用餐厨、居家日用>厨房工具>烹饪勺铲,"
                    "日用餐厨、居家日用>厨房工具>砧板、菜板,"
                    "日用餐厨、居家日用>厨房工具>多功能切菜器,"
                    "日用餐厨、居家日用>厨房工具>开瓶器、开罐器"
                ),
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
            )
            try:
                run_tag_collect(config)
            except ServiceError as exc:
                detail = getattr(exc, "data", {}) or {}
                _assert(detail.get("systemic_abort") is True, "连续类目导航失败应标记为系统性停止")
                _assert(detail.get("consecutive_failure_count") == 2, "应在连续2次相同类目导航失败后停止")
                _assert(detail.get("skipped_job_count") == 2, "应记录为避免刷新而跳过的剩余类目数")
                failures = detail.get("partial_failures") or []
                _assert(len(failures) == 2, "停止前应保留已经尝试的两个失败类目诊断")
                _assert(failures[-1].get("systemic_abort") is True, "最后一次失败应记录停止刷新原因")
            else:
                raise AssertionError("连续类目导航失败且零结果时不应返回成功")
            _assert(len(calls) == 2, "连续系统性失败后不能继续打开剩余类目并反复刷新首页")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_task_store_retries_transient_windows_replace_error():
    original_replace = web.os.replace
    calls = []

    def flaky_replace(source, target):
        calls.append((str(source), str(target)))
        if len(calls) < 3:
            raise PermissionError(5, "transient Windows file lock", str(target))
        return original_replace(source, target)

    try:
        with _temporary_tag_data_dir() as data_dir:
            _reset_web_task_memory()
            web.os.replace = flaky_replace
            with web.TASK_LOCK:
                web.TASKS["persist-retry-test"] = {
                    "task_id": "persist-retry-test",
                    "status": "completed",
                    "created_at": "2026-07-20 17:00:00",
                    "updated_at": "2026-07-20 17:00:01",
                    "finished_at": "2026-07-20 17:00:01",
                    "progress": {"stage": "completed", "message": "done"},
                    "summary": {"categories": [], "category_count": 0},
                }
                web._persist_tasks_locked()
            _assert(len(calls) == 3, "Windows短暂文件占用时任务状态应重试写入")
            _assert((data_dir / web.TASK_STORE_FILENAME).exists(), "重试成功后任务状态文件必须存在")
    finally:
        web.os.replace = original_replace
        _reset_web_task_memory()


def test_category_products_require_item_level_evidence():
    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        return {
            "products": [
                {
                    "id": "category-evidence-good",
                    "title": "厚款一次性雨衣全身雨披暴雨卡片式透明加厚男女成人长款便携雨披",
                    "price": "19.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/920000000001.html",
                    "stats": {
                        "productCategoryPath": category_path,
                        "rawText": "真实页面列表候选",
                    },
                },
                {
                    "id": "category-parent-only",
                    "title": "户外遮阳伞加厚防晒便携摆摊大伞",
                    "price": "25.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/920000000003.html",
                    "stats": {"rawText": "遮阳防雨 真实页面列表候选"},
                },
                {
                    "id": "category-evidence-bad",
                    "title": "爆款一次性雨衣雨披户外便携成人长款",
                    "price": "29.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/920000000002.html",
                    "stats": {
                        "productCategoryPath": "家用电器、数码电脑>厨房电器>电热饭盒",
                        "rawText": "厨房电器 电热饭盒 真实页面列表候选",
                    },
                },
                {
                    "id": "category-id-conflict-path-match",
                    "title": "春季高腰宽松百搭垂感长裤",
                    "price": "39.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/920000000004.html",
                    "stats": {
                        "productCategoryId": "1031911",
                        "productCategoryPath": category_path,
                        "rawText": "真实页面列表候选",
                    },
                },
                {
                    "id": "category-id-conflict-no-path",
                    "title": "厚款一次性雨衣全身雨披暴雨成人长款",
                    "price": "18.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/920000000005.html",
                    "stats": {
                        "productCategoryId": "1031912",
                        "rawText": "真实页面列表候选",
                    },
                },
            ],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://s.1688.com/selloffer/category-evidence.html",
                "category_id": "1031910",
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir() as data_dir:
            config = parse_input(
                categories="日用餐厨、居家日用>遮阳防雨>雨衣雨披",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                target_publishable_count=2,
            )
            result = run_tag_collect(config)
            data = result["data"]
            ids = {row["item_id"] for row in data["rows"]}
            unknown_ids = {row["item_id"] for row in data.get("category_unknown_rows", [])}
            _assert("category-evidence-good" in ids, "结构化商品类目路径匹配时应保留")
            _assert("category-parent-only" not in ids, "只命中上级类目词不能混入叶子类目")
            _assert("category-parent-only" in unknown_ids, "标题缺少叶子类目证据时应进入unknown，而不是误判错类目")
            _assert("category-evidence-bad" not in ids, "商品结构化类目路径冲突时不能混入目标类目")
            _assert(
                "category-id-conflict-path-match" in ids,
                "导航ID与商品ID不同但结构化路径完全一致时，应按路径matched",
            )
            id_conflict_path_row = next(
                row for row in data["rows"]
                if row.get("item_id") == "category-id-conflict-path-match"
            )
            _assert(id_conflict_path_row.get("category_match_source") == "path", "ID冲突被路径消解后应记录source=path")
            _assert(
                "category-id-conflict-no-path" in unknown_ids,
                "导航ID与商品ID不同且无结构化路径时只能进入unknown",
            )
            _assert(
                any(
                    record.get("item_id") == "category-evidence-bad"
                    and record.get("reason_code") == "product_category_mismatch"
                    for record in data["rejection_records"]
                ),
                "结构化证据确认的错类目商品应留下筛除记录",
            )
            _assert(
                not any(record.get("item_id") == "category-parent-only" for record in data["rejection_records"]),
                "unknown商品不能混同为确认错类目并进入拒绝记录",
            )
            _assert(
                not any(
                    record.get("item_id") in {
                        "category-id-conflict-path-match",
                        "category-id-conflict-no-path",
                    }
                    for record in data["rejection_records"]
                ),
                "可由路径确认或仅ID冲突的商品都不能被误写为错类目拒绝",
            )
            status_by_id = {
                record.get("item_id"): record.get("status")
                for record in data.get("category_match_records", [])
            }
            _assert(status_by_id.get("category-evidence-good") == "matched", "应记录matched类目审计结论")
            _assert(status_by_id.get("category-parent-only") == "unknown", "应记录unknown类目审计结论")
            _assert(status_by_id.get("category-evidence-bad") == "mismatched", "应记录mismatched类目审计结论")
            _assert(status_by_id.get("category-id-conflict-path-match") == "matched", "ID冲突路径一致应审计为matched")
            _assert(status_by_id.get("category-id-conflict-no-path") == "unknown", "仅ID冲突应审计为unknown")

            rejected_path = data_dir / "rejected_products.json"
            if rejected_path.exists():
                rejected_payload = json.loads(rejected_path.read_text(encoding="utf-8"))
                persisted_ids = set((rejected_payload.get("items") or {}).keys())
                _assert("category-parent-only" not in persisted_ids, "unknown商品不能写入持久拒绝池")
                _assert("category-evidence-bad" in persisted_ids, "确认错类目商品应写入持久拒绝池")
                _assert("category-id-conflict-path-match" not in persisted_ids, "路径确认matched的ID冲突商品不能持久拒绝")
                _assert("category-id-conflict-no-path" not in persisted_ids, "仅ID冲突的unknown商品不能持久拒绝")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_product_category_evaluator_three_states_and_structured_precedence():
    evaluate = getattr(service, "_evaluate_product_category", None)
    _assert(callable(evaluate), "service应提供三态商品类目evaluator")
    selected_path = "女装、男装、内衣>下装裤>女式牛仔裤"

    def decide(item_id, title, stats, selected_category_id=""):
        return evaluate(
            Product(
                id=item_id,
                title=title,
                price="29.9",
                image="",
                url=f"https://detail.1688.com/offer/{item_id}.html",
                stats=stats,
            ),
            selected_path,
            selected_category_id=selected_category_id,
        )

    id_match = decide(
        "category-id-match",
        "女士短裤夏季高腰宽松休闲裤",
        {"productCategoryId": "1031910"},
        selected_category_id="1031910",
    )
    _assert(id_match.get("status") == "matched", "商品类目ID与所选类目ID一致时应matched")
    _assert(id_match.get("source") == "id", "ID结论应标记source=id")

    same_id_path_conflict = decide(
        "category-same-id-path-conflict",
        "浅蓝色微喇牛仔裤女高腰显瘦",
        {
            "productCategoryId": "1031910",
            "productCategoryPath": "女装、男装、内衣>下装裤>女士短裤",
        },
        selected_category_id="1031910",
    )
    _assert(
        same_id_path_conflict.get("status") == "unknown",
        "数字ID相同但结构化路径明确冲突时证据自相矛盾，应unknown而非由ID强行放行",
    )
    _assert(
        same_id_path_conflict.get("source") == "path",
        "ID与路径冲突的unknown应标记path来源以暴露冲突证据",
    )

    id_conflict_path_match = decide(
        "category-id-conflict-path-match",
        "春季高腰宽松百搭垂感长裤",
        {
            "productCategoryId": "1031911",
            "productCategoryPath": selected_path,
        },
        selected_category_id="1031910",
    )
    _assert(
        id_conflict_path_match.get("status") == "matched",
        "数字ID冲突但结构化路径完全一致时应由路径确认matched",
    )
    _assert(id_conflict_path_match.get("source") == "path", "ID冲突被路径消解后应标记source=path")

    id_conflict_no_path = decide(
        "category-id-conflict-no-path",
        "浅蓝色微喇牛仔裤女高腰显瘦",
        {"productCategoryId": "1031911"},
        selected_category_id="1031910",
    )
    _assert(id_conflict_no_path.get("status") == "unknown", "数字ID冲突且无结构化路径时不能直接判mismatched")
    _assert(id_conflict_no_path.get("source") == "id", "仅ID冲突的unknown应保留source=id供审计")

    path_match = decide(
        "category-path-match",
        "春季百搭长裤",
        {"productCategoryPath": selected_path},
    )
    _assert(path_match.get("status") == "matched", "结构化商品类目路径一致时应matched")
    _assert(path_match.get("source") == "path", "路径结论应标记source=path")

    path_mismatch = decide(
        "category-path-mismatch",
        "浅蓝色微喇牛仔裤女高腰显瘦",
        {"productCategoryPath": "女装、男装、内衣>下装裤>女士短裤"},
    )
    _assert(path_mismatch.get("status") == "mismatched", "兄弟类目结构化路径应覆盖标题并判mismatched")
    _assert(path_mismatch.get("source") == "path", "结构化路径冲突不能退回标题放行")

    title_match = decide(
        "category-title-match",
        "浅蓝色微喇牛仔裤女春季设计感高腰显瘦",
        {},
    )
    _assert(title_match.get("status") == "matched", "标题词序变化“牛仔裤女”应匹配女式牛仔裤")
    _assert(title_match.get("source") == "title", "无结构化证据时标题只能作为正向匹配来源")

    no_evidence = decide(
        "category-no-evidence",
        "春季高腰宽松百搭长裤",
        {},
    )
    _assert(no_evidence.get("status") == "unknown", "标题未命中且无结构化证据时应unknown")
    _assert(no_evidence.get("source") == "none", "无证据结论应标记source=none")
    _assert(no_evidence.get("reason"), "三态结论必须保留可审计reason")
    _assert("evidence" in no_evidence, "三态结论必须保留原始证据摘要")


def test_gender_style_leaf_category_matches_title_suffix():
    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        return {
            "products": [
                {
                    "id": "jeans-good",
                    "title": "浅蓝色微喇牛仔裤女春季设计感美式高街显瘦小众高腰休闲马蹄裤子",
                    "price": "29.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/930000000001.html",
                    "stats": {"rawText": "真实页面列表候选"},
                },
                {
                    "id": "shorts-bad",
                    "title": "女士短裤夏季高腰宽松休闲裤",
                    "price": "19.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/930000000002.html",
                    "stats": {
                        "productCategoryPath": "女装、男装、内衣>下装裤>女士短裤",
                        "rawText": "真实页面列表候选",
                    },
                },
            ],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://s.1688.com/selloffer/category-jeans.html",
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir():
            config = parse_input(
                categories="女装、男装、内衣>下装裤>女式牛仔裤",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                target_publishable_count=2,
            )
            result = run_tag_collect(config)
            data = result["data"]
            ids = {row["item_id"] for row in data["rows"]}
            _assert("jeans-good" in ids, "女式牛仔裤应兼容标题里的“牛仔裤女”表达")
            jeans = next(row for row in data["rows"] if row.get("item_id") == "jeans-good")
            _assert(jeans.get("category_match_status") == "matched", "标题词序正例应保留matched结论")
            _assert(jeans.get("category_match_source") == "title", "标题词序正例应标记由title证据命中")
            _assert("shorts-bad" not in ids, "同为女装但非牛仔裤商品不能混入女式牛仔裤")
            _assert(
                any(
                    record.get("item_id") == "shorts-bad"
                    and record.get("reason_code") == "product_category_mismatch"
                    for record in data["rejection_records"]
                ),
                "结构化兄弟类目商品应留下确认错类目拒绝记录",
            )
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_category_unknown_is_not_publishable_exported_or_persisted():
    selected_path = "女装、男装、内衣>下装裤>女式牛仔裤"

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        return {
            "products": [{
                "id": "category-unknown-only",
                "title": "春季高腰宽松百搭垂感长裤",
                "price": "29.9",
                "image": "",
                "url": "https://detail.1688.com/offer/940000000001.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://s.1688.com/selloffer/category-unknown.html",
                "expected_path": selected_path,
                "matched_path": selected_path,
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products
    )
    try:
        with _temporary_tag_data_dir() as data_dir:
            config = parse_input(
                categories=selected_path,
                tags="微信小店",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                auto_verify_details=False,
                target_publishable_count=1,
            )
            result = run_tag_collect(config)
            data = result["data"]
            _assert(result["success"], "仅有unknown候选时任务仍应返回可审计结果")
            _assert(data["rows"] == [], "unknown商品不能进入主选品结果")
            _assert(data["top_items"] == [], "unknown商品不能进入top_items")
            _assert(data["row_count"] == 0, "unknown商品不能计入主结果数量")
            _assert(data["publishable_count"] == 0, "unknown商品不能计入可铺数量")
            _assert(data.get("category_unknown_count") == 1, "应单独统计类目证据unknown商品")
            unknown_rows = data.get("category_unknown_rows", [])
            _assert(
                [row.get("item_id") for row in unknown_rows] == ["category-unknown-only"],
                "unknown商品应保留在category_unknown_rows等待详情核验",
            )
            _assert(unknown_rows[0].get("category_match_status") == "unknown", "unknown行应保留三态结论")
            _assert(
                any(item.get("item_id") == "category-unknown-only" for item in data.get("verification_queue", [])),
                "unknown商品仍应进入详情核验队列以便后续补证据",
            )
            _assert(
                not any(record.get("item_id") == "category-unknown-only" for record in data["rejection_records"]),
                "unknown商品不能进入确认拒绝记录",
            )

            rejected_path = data_dir / "rejected_products.json"
            if rejected_path.exists():
                rejected_payload = json.loads(rejected_path.read_text(encoding="utf-8"))
                _assert(
                    "category-unknown-only" not in (rejected_payload.get("items") or {}),
                    "unknown商品不能写入持久拒绝池导致下次永久跳过",
                )

            with zipfile.ZipFile(data["output_path"]) as workbook:
                workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
                _assert("category-unknown-only" in result_xml, "unknown商品应按7.21要求汇总到唯一选品结果sheet")
                _assert("类目待核验" not in workbook_xml, "Excel不应再生成独立类目待核验附表")
                _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "unknown商品导出仍只能有一个工作表")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_detail_category_evidence_promotes_unknown_and_overrides_title_match():
    selected_path = "女装、男装、内衣>下装裤>女式牛仔裤"
    sibling_path = "女装、男装、内衣>下装裤>女士短裤"

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        return {
            "products": [
                {
                    "id": "detail-promote-unknown",
                    "title": "春季高腰宽松百搭垂感长裤",
                    "price": "29.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/950000000001.html",
                    "stats": {"rawText": "真实页面列表候选"},
                },
                {
                    "id": "detail-override-title",
                    "title": "浅蓝色微喇牛仔裤女春季设计感高腰显瘦",
                    "price": "29.9",
                    "image": "",
                    "url": "https://detail.1688.com/offer/950000000002.html",
                    "stats": {"rawText": "真实页面列表候选"},
                },
            ],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://s.1688.com/selloffer/category-detail-evidence.html",
                "expected_path": selected_path,
                "matched_path": selected_path,
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    def fake_detail(url, item_id="", **kwargs):
        if item_id == "detail-promote-unknown":
            return {
                "product_category_id": "1031910",
                "product_category_path": selected_path,
                "product_category_breadcrumb": [
                    "女装、男装、内衣",
                    "下装裤",
                    "女式牛仔裤",
                ],
                "category_evidence_source": "test_detail_structured_data",
            }
        return {
            "product_category_id": "1031911",
            "product_category_path": sibling_path,
            "product_category_breadcrumb": [
                "女装、男装、内衣",
                "下装裤",
                "女士短裤",
            ],
            "category_evidence_source": "test_detail_structured_data",
        }

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products,
        collect_detail_fields_from_1688_page=fake_detail,
    )
    try:
        with _temporary_tag_data_dir() as data_dir:
            config = parse_input(
                categories=selected_path,
                tags="微信小店",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                auto_verify_details=True,
                auto_verify_max_items=2,
                target_publishable_count=2,
            )
            result = run_tag_collect(config)
            data = result["data"]
            row_by_id = {row.get("item_id"): row for row in data["rows"]}
            excluded_by_id = {
                row.get("item_id"): row
                for row in data.get("filter_excluded_rows", [])
            }

            _assert("detail-promote-unknown" in row_by_id, "详情目标类目证据应把unknown候选迁入主rows")
            promoted = row_by_id["detail-promote-unknown"]
            _assert(promoted.get("category_match_status") == "matched", "详情补证据后应更新为matched")
            _assert(promoted.get("product_category_path") == selected_path, "迁入主rows时应保留详情商品类目路径")
            _assert("detail-override-title" not in row_by_id, "详情兄弟类目证据应覆盖列表标题命中并移出主rows")
            _assert("detail-override-title" in excluded_by_id, "详情确认错类目商品应迁入筛除区")
            overridden = excluded_by_id["detail-override-title"]
            _assert(overridden.get("category_match_status") == "mismatched", "详情覆盖后的商品应标记mismatched")
            _assert(overridden.get("exclusion_type") == "category_mismatch", "详情错类目筛除应保留专用exclusion_type")
            _assert(data.get("category_unknown_count") == 0, "详情处理后两个候选都不应继续停留unknown")

            status_history = {}
            for record in data.get("category_match_records", []):
                status_history.setdefault(record.get("item_id"), set()).add(record.get("status"))
            _assert(
                {"unknown", "matched"}.issubset(status_history.get("detail-promote-unknown", set())),
                "审计记录应保留unknown被详情提升为matched的状态迁移",
            )
            _assert(
                {"matched", "mismatched"}.issubset(status_history.get("detail-override-title", set())),
                "审计记录应保留标题matched被详情证据覆盖为mismatched的状态迁移",
            )
            _assert(
                any(
                    record.get("item_id") == "detail-override-title"
                    and record.get("reason_code") == "product_category_mismatch"
                    for record in data["rejection_records"]
                ),
                "详情确认错类目后应生成可持久化拒绝记录",
            )

            rejected_path = data_dir / "rejected_products.json"
            _assert(rejected_path.exists(), "详情确认错类目后应写入持久拒绝池")
            rejected_payload = json.loads(rejected_path.read_text(encoding="utf-8"))
            persisted_items = rejected_payload.get("items") or {}
            override_records = persisted_items.get("detail-override-title", [])
            promote_records = persisted_items.get("detail-promote-unknown", [])
            _assert(
                any(record.get("reason_code") == "product_category_mismatch" for record in override_records),
                "详情确认错类目商品下次应按product_category_mismatch直接跳过",
            )
            _assert(
                not any(record.get("reason_code") == "product_category_mismatch" for record in promote_records),
                "详情提升为matched的商品不能被持久标记为错类目",
            )

            with zipfile.ZipFile(data["output_path"]) as workbook:
                result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
                _assert("detail-promote-unknown" in result_xml, "详情提升后的商品应进入Excel主选品结果")
                _assert("detail-override-title" in result_xml, "详情错类目商品也应按7.21要求汇总进唯一主表供用户查看")
                _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "详情类目复核导出不得生成异常附表")
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_category_unknown_successful_detail_without_category_is_not_requeued():
    selected_path = "女装、男装、内衣>下装裤>女式牛仔裤"
    item_id = "category-detail-no-evidence"

    def fake_collect_products(
        query,
        limit,
        source_url="",
        native_filters=None,
        category_filters=None,
        manual_url_only=False,
        return_meta=False,
        **kwargs,
    ):
        category_filter = (category_filters or [{}])[0]
        category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
        return {
            "products": [{
                "id": item_id,
                "title": "春季高腰宽松百搭垂感长裤",
                "price": "29.9",
                "image": "",
                "url": "https://detail.1688.com/offer/960000000001.html",
                "stats": {"rawText": "真实页面列表候选"},
            }],
            "filter_results": [{
                "filter_key": category_filter.get("key", "category_path_1"),
                "label": f"类目:{category_path}",
                "tag": category_path,
                "status": "clicked",
                "source": "1688_category_navigation",
                "query": query,
                "page_url": "https://s.1688.com/selloffer/category-detail-no-evidence.html",
                "expected_path": selected_path,
                "matched_path": selected_path,
                "message": "已在1688页面按类目入口点击并进入商品结果页",
            }],
        }

    def fake_detail(url, current_item_id="", **kwargs):
        _assert(current_item_id == item_id, "详情核验应处理当前unknown商品")
        return {key: "1" for key in service.DETAIL_VERIFICATION_FIELDS}

    original_module = sys.modules.get("capabilities.tag_collect.rpa")
    sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
        collect_products_from_1688_page=fake_collect_products,
        collect_detail_fields_from_1688_page=fake_detail,
    )
    try:
        with _temporary_tag_data_dir():
            config = parse_input(
                categories=selected_path,
                tags="微信小店",
                sample_data=False,
                collect_source="rpa",
                output_format="xlsx",
                auto_verify_details=True,
                auto_verify_max_items=1,
                target_publishable_count=1,
            )
            result = run_tag_collect(config)
            data = result["data"]
            _assert(data.get("category_unknown_count") == 1, "详情成功但无类目证据时仍应保持unknown")
            unknown = data.get("category_unknown_rows", [])[0]
            _assert(unknown.get("category_match_status") == "unknown", "无类目详情证据不能擅自升级matched")
            _assert(
                unknown.get("verification_status") in ("verified", "partial_verified"),
                "非类目详情字段成功提取时应记录一次成功详情核验",
            )
            _assert(
                not any(item.get("item_id") == item_id for item in data.get("verification_queue", [])),
                "一次成功详情核验仍无类目证据后，不应无限重复进入verification_queue",
            )
            second = verify_run_details(data["run_id"], sample_data=False, max_items=1)
            _assert(second["success"], "无待核验项时再次请求详情核验应安全返回")
            _assert(second["data"]["verified_count"] == 0, "已成功核验但无类目证据的unknown不应被重复核验")
            _assert(
                not any(item.get("item_id") == item_id for item in second["data"].get("verification_queue", [])),
                "重复核验后队列仍不应重新加入该unknown商品",
            )
    finally:
        if original_module is None:
            sys.modules.pop("capabilities.tag_collect.rpa", None)
        else:
            sys.modules["capabilities.tag_collect.rpa"] = original_module


def test_category_unknown_security_or_login_failure_remains_retryable():
    selected_path = "女装、男装、内衣>下装裤>女式牛仔裤"

    for error_code, message in (
        (
            "security_verification_required",
            "1688触发安全滑块/验证码，需人工处理后继续",
        ),
        (
            "login_required",
            "1688详情页需要人工登录后继续",
        ),
    ):
        item_id = f"category-unknown-{error_code}"

        def fake_collect_products(
            query,
            limit,
            source_url="",
            native_filters=None,
            category_filters=None,
            manual_url_only=False,
            return_meta=False,
            **kwargs,
        ):
            category_filter = (category_filters or [{}])[0]
            category_path = str(category_filter.get("category_path") or category_filter.get("tag") or "")
            return {
                "products": [{
                    "id": item_id,
                    "title": "春季高腰宽松百搭垂感长裤",
                    "price": "29.9",
                    "image": "",
                    "url": f"https://detail.1688.com/offer/{item_id}.html",
                    "stats": {"rawText": "真实页面列表候选"},
                }],
                "filter_results": [{
                    "filter_key": category_filter.get("key", "category_path_1"),
                    "label": f"类目:{category_path}",
                    "tag": category_path,
                    "status": "clicked",
                    "source": "1688_category_navigation",
                    "query": query,
                    "page_url": "https://s.1688.com/selloffer/category-detail-retry.html",
                    "expected_path": selected_path,
                    "matched_path": selected_path,
                    "message": "已在1688页面按类目入口点击并进入商品结果页",
                }],
            }

        def blocked_detail(*args, **kwargs):
            raise ServiceError(f"{error_code}: {message}")

        original_module = sys.modules.get("capabilities.tag_collect.rpa")
        sys.modules["capabilities.tag_collect.rpa"] = types.SimpleNamespace(
            collect_products_from_1688_page=fake_collect_products,
            collect_detail_fields_from_1688_page=blocked_detail,
        )
        try:
            with _temporary_tag_data_dir():
                config = parse_input(
                    categories=selected_path,
                    tags="微信小店",
                    sample_data=False,
                    collect_source="rpa",
                    output_format="xlsx",
                    auto_verify_details=True,
                    auto_verify_max_items=1,
                    target_publishable_count=1,
                )
                result = run_tag_collect(config)
                data = result["data"]
                _assert(data.get("category_unknown_count") == 1, f"{error_code}后商品应继续保持unknown")
                unknown = data.get("category_unknown_rows", [])[0]
                _assert(unknown.get("verification_status") == "failed", f"{error_code}应记录详情核验失败")
                _assert(data["automation_state"]["status"] == "paused", f"{error_code}应暂停并等待人工接管")
                _assert(data.get("verification_stopped_reason"), f"{error_code}应保留人工处理提示")
                _assert(
                    any(item.get("item_id") == item_id for item in data.get("verification_queue", [])),
                    f"{error_code}属于可人工恢复场景，商品应保留在verification_queue供处理后重试",
                )
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
        _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "详情核验后重新导出仍只能有一个工作表")
    saved = service.get_run_payload(verify_data["run_id"])
    _assert(saved and saved.get("verification_records"), "字段级核验记录应继续保存在JSON快照")
    _assert(any(record.get("source") == "sample_detail" for record in saved["verification_records"]), "JSON核验记录应包含样例详情来源")


def test_auto_detail_verification_after_collect():
    config = parse_input(
        categories="",
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
    _assert(data["automation_state"]["status"] in ("pending_detail", "sample_verified", "partial"), "一件代发等详情硬筛未核实的候选移出主结果后，应显示待继续核验或已核验/部分完成状态")
    _assert(
        all(row.get("supports_dropship") == "是" for row in data["rows"]),
        "选择一件代发时，主结果不得保留尚未明确核实的一件代发商品",
    )
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
        _assert("sample_verified" not in result_xml, "核验状态不得挤占用户确认的24列主表")
        _assert(any(row.get("manual_review_status") == "样例预通过" for row in verified_rows), "样例自动核验结果应在JSON审计信息中标注样例预通过")
        _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "自动详情核验导出也只能有一个工作表")
    saved = service.get_run_payload(data["run_id"])
    _assert(saved and any(record.get("source") == "sample_detail" for record in saved.get("verification_records", [])), "自动详情核验审计应保留在JSON快照")


def test_auto_detail_verification_security_pause():
    def fake_collect_products(*args, **kwargs):
        return {
            "products": [{
                "id": "real-security-1",
                "title": "真实详情风控测试商品",
                "price": "19.9",
                "image": "",
                "url": "https://detail.1688.com/offer/real-security-1.html",
                "stats": {"rawText": "日用餐厨 居家日用 真实页面列表候选"},
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
            categories="",
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
        _assert(not data["rows"], "一件代发详情核验被风控阻断时，未确认商品不能继续留在主结果")
        row = data["filter_excluded_rows"][0]
        _assert(row["verification_status"] == "failed", "详情风控商品不应被标记为已核验")
        _assert(row["product_refund_rate"] == DETAIL_VERIFICATION_PENDING, "详情风控不应写入样例品退率")
        _assert(row["shipment_rate"] == DETAIL_VERIFICATION_PENDING, "详情风控不应写入样例发货率")
        _assert("sample_verified" not in json.dumps(data, ensure_ascii=False), "真实详情风控不应出现样例核验状态")
        with zipfile.ZipFile(data["output_path"]) as workbook:
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            _assert("real-security-1" in result_xml, "详情风控筛除商品应汇总到唯一主表供用户查看")
            _assert("failed" not in result_xml, "核验失败状态不得挤占用户确认的24列主表")
            _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "详情风控暂停导出不得生成异常附表")
        saved = service.get_run_payload(data["run_id"])
        _assert(saved and saved.get("automation_state", {}).get("status") == "paused", "暂停状态应保留在JSON快照")
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
            _assert("样例说明" not in workbook_xml, "真实批次导出不得包含样例说明 sheet")
            _assert("真实数据" not in result_xml, "数据模式不得挤占用户确认的24列主表")
            _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "真实批次导出也只能有一个工作表")
        saved = service.get_run_payload(verify_data["run_id"])
        _assert(saved and not any(record.get("source") == "sample_detail" for record in saved.get("verification_records", [])), "真实批次JSON核验记录不得使用样例详情来源")
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
        categories="美妆个护、收纳清洁>收纳防尘>收纳盒",
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
            categories="美妆个护、收纳清洁>收纳防尘>收纳盒",
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
    _assert(not data["rows"] and data["filter_excluded_rows"], "详情评价筛选未核实前应进入异常复核，不能留在主结果")
    first_row = data["filter_excluded_rows"][0]
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
    _assert(verify_data["rows"], "详情筛选全部命中后商品应恢复到主结果")


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
    before_count = len(data["filter_excluded_rows"])
    _assert(data["row_count"] == 0 and before_count >= 1, "详情评价标签未核实前应全部进入异常复核")
    verify_result = verify_run_details(data["run_id"], sample_data=True, max_items=1)
    verify_data = verify_result["data"]
    _assert(verify_data["filter_excluded_count"] >= 1, "详情核验后未命中或尚未核实评价标签的商品应移出主结果")
    _assert(not verify_data["rows"], "不存在的评价标签不能让任何商品进入主结果 rows")
    _assert(
        all(not any(record.get("status") in ("pending_detail", "manual_review_required") for record in row.get("filter_match_records", [])) for row in verify_data["rows"]),
        "详情硬筛执行后主结果不得继续保留待核验筛选条件",
    )
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
    _assert(data["filter_excluded_rows"], "详情评价指标未核实前应进入异常复核")
    first_id = str(data["filter_excluded_rows"][0]["item_id"])
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
        **kwargs,
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
            "page_meta": [
                {"page_index": 1, "extracted_count": 1, "accepted_count": 1},
                {"page_index": 2, "extracted_count": 1, "accepted_count": 1},
                {"page_index": 3, "extracted_count": 1, "accepted_count": 1},
            ],
            "pagination": {
                "requested_limit": limit,
                "max_pages": 6,
                "pages_visited": 3,
                "collected_count": min(limit, 3),
                "stop_reason": "requested_limit_reached",
            },
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
                max_items_per_query=3,
                target_publishable_count=2,
            )
            result = run_tag_collect(config)
            data = result["data"]
            _assert(result["success"], "目标可铺数量采集应成功")
            _assert(data["target_publishable_count"] == 2, "应返回目标可铺数量")
            _assert(data["candidate_scan_limit"] == config.max_items_per_query == 3, "候选预算应使用显式扫描上限")
            _assert(data["candidate_count"] >= 3, "应按显式候选预算扫描足够商品")
            _assert(data["publishable_count"] == 2, "目标计数应按可铺/谨慎结果计算")
            _assert(data["collection_stop_reason"] == "target_met", "达到目标后应标记 target_met")
            _assert(data["pagination_pages_visited"] == 3, "应返回真实分页访问页数")
            _assert(data["pagination_collected_count"] == 3, "应返回分页采集候选数")
            _assert(data["pagination_records"][0]["stop_reason"] == "requested_limit_reached", "应保留分页停止原因")
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
        **kwargs,
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
    config = parse_input(
        categories="美妆个护、收纳清洁>厨房整理>厨房置物架",
        tags="微信小店",
        sample_data=False,
    )
    good = product_to_export_row(
        Product(
            id="auto-review-good",
            title="厨房沥水架置物架",
            price="19.9",
            image="",
            url="https://detail.1688.com/offer/auto-review-good.html",
            stats={
                "productCategoryPath": config.categories[0],
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
        "collection_rate_48h": "99.2%",
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
    sample_good["url"] = "https://detail.1688.com/offer/auto-review-sample.html"
    sample_good["list_source"] = "sample"
    sample_good["data_mode"] = "开发样例"
    sample_good["verification_status"] = "sample_verified"
    service.apply_auto_review_status(sample_good)
    _assert(sample_good["manual_review_status"] == "样例预通过", "样例数据不能标记为真实系统预通过")
    _assert("不代表真实1688数据" in sample_good["manual_review_note"], "样例预通过必须说明不代表真实1688数据")

    unclear = dict(good)
    unclear["item_id"] = "auto-review-unclear"
    unclear["url"] = "https://detail.1688.com/offer/auto-review-unclear.html"
    unclear["wechat_shop_suggestion"] = "可铺"
    unclear["product_refund_rate"] = "暂无"
    unclear["shipment_rate"] = "未展示"
    service.apply_auto_review_status(unclear)
    _assert(unclear["manual_review_status"] == "待复核", "品退率/发货率无法解析时不能系统预通过")

    bad = dict(good)
    bad["item_id"] = "auto-review-bad"
    bad["url"] = "https://detail.1688.com/offer/auto-review-bad.html"
    bad["wechat_shop_suggestion"] = "不建议"
    service.apply_auto_review_status(bad)
    _assert(bad["manual_review_status"] == "系统不建议", "不建议商品应自动归为系统不建议")

    excluded = dict(good)
    excluded["item_id"] = "auto-review-excluded"
    excluded["url"] = "https://detail.1688.com/offer/auto-review-excluded.html"
    excluded["filter_verification_status"] = "filtered_out"
    excluded["filter_verification_note"] = "品退率未满足<2%"
    service.apply_auto_review_status(excluded)
    _assert(excluded["manual_review_status"] == "系统剔除", "筛选不通过商品应自动剔除")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "auto_review.xlsx")
        export_xlsx([good, bad], output_path, {"filter_excluded_rows": [excluded], "run_id": "auto-review"})
        with zipfile.ZipFile(output_path) as workbook:
            result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            _assert("auto-review-good" in result_xml, "系统预通过商品应进入唯一主表")
            _assert("auto-review-bad" in result_xml, "系统不建议商品应进入唯一主表")
            _assert("auto-review-excluded" in result_xml, "筛选剔除商品也应汇总进唯一主表")
            _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "人工复核状态不得再拆成附表")


def test_web_feedback_controls_and_inline_javascript_contract():
    html = web.HTML_PAGE
    script_match = re.search(r"<script>([\s\S]*?)</script>", html)
    _assert(script_match, "Web 页面应包含可执行的内嵌脚本")
    proc = subprocess.run(
        ["node", "--check"],
        input=script_match.group(1),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(proc.returncode == 0, f"Web 内嵌 JavaScript 语法检查失败：{proc.stderr or proc.stdout}")

    render_categories = _javascript_function_source(html, "renderCategories")
    _assert("previousScroll" in render_categories and "preservedColumns.has(key)" in render_categories, "类目重绘应按列恢复滚动位置")
    _assert("categoryPanel" in render_categories and "panelScrollTop" in render_categories, "勾选三级类目后应恢复类目面板滚动位置")
    _assert("pageScrollTop" in render_categories and "window.scrollTo" in render_categories, "类目重绘还应恢复页面位置，避免选择后整页上下跳动")
    _assert('body.category-modal-open .workspace' in html and "overflow: hidden" in html, "类目弹窗打开时必须锁定实际滚动容器workspace")
    _assert("overscroll-behavior: contain" in html, "类目弹窗和三栏列表应阻断滚动链传到背景")
    _assert('addEventListener("wheel"' in html and "passive: false" in html and "event.preventDefault()" in html, "类目弹窗边界滚轮必须阻止背景继续滚动")
    _assert('?["parent"]' in html.replace(" ", "") or '?["parent"]' in html, "点击一级类目时应只保留一级栏滚动位置")
    _assert('["parent", "child"]' in html, "点击二级类目时应保留一级和二级栏滚动位置")
    _assert("data-category-group='true'" in html or 'data-category-group="true"' in html, "二级类目应提供全选/取消其下三级类目的复选入口")
    _assert('data-category-group-action="${esc(value)}"' in html, "二级类目行应提供明确的全选下级按钮")
    _assert("paths.forEach(path =>" in html, "二级全选应逐个写入三级叶子路径")
    _assert("state.selectedCategories.add(path)" in html, "二级全选应加入完整三级路径")
    _assert("setCategorySelection(target.value, target.checked)" in html, "普通叶子类目仍应通过统一选择函数处理")
    _assert('fetch("/api/task/start"' in html and 'fetch("/api/task/cancel"' in html, "Web 应使用后台任务启动和停止接口")
    _assert("resumeActiveTask(state.options.active_task || {})" in html, "刷新页面后应恢复仍在运行的后台任务进度")
    _assert('fetch("/api/templates/save"' in html and 'fetch("/api/templates/delete"' in html, "Web 应提供模板保存和删除操作")
    _assert('id="stopTaskBtn"' in html and 'id="stickyStopBtn"' in html and 'id="taskProgressStopBtn"' in html, "主操作区、悬浮区和进度卡都应提供停止按钮")
    _assert('id="versionBadge"' in html and "workbench_build" in html, "页面应展示版本标识，便于确认访问的是新工作台")
    _assert('data-library-section="${esc(section.key)}"' in html and 'section.key === "priority"' not in html, "重点筛选应由统一 schema 渲染，避免另建重复字段")
    _assert("safeNoticeText" in html and "x5secdata" in html, "前端提示还应防御性隐藏安全验证临时参数")
    _assert("本任务不会停一会再自动继续" in html, "完成进度卡应明确任务已结束而不是暂歇")
    _assert('id="recentTasksCard"' in html and 'id="recentTaskList"' in html, "页面应提供最近任务列表")
    _assert("state.options.recent_tasks || []" in html and "renderRecentTasks" in html, "页面初始化时应加载持久化任务历史")
    _assert("服务重启中断" in html and "task_interrupted" in html, "页面应明确展示服务重启中断状态")
    _assert("loadRecentTaskRun" in html and "/api/run?run_id=" in html, "最近任务应支持重新加载已有批次结果")
    _assert('id="soundAlertsToggle"' in html, "工作台应提供可持久化的声音提醒开关")
    _assert("SOUND_ALERT_STORAGE_KEY" in html and "localStorage.setItem" in html, "声音提醒开关应保存到localStorage")
    _assert('progress.stage === "manual_intervention"' in html, "人工介入实时进度应触发声音判断")
    _assert('task.status === "completed"' in html and "playTaskCompletedAlert" in html, "任务完成时应触发完成提示音")

    alert_once_source = _javascript_function_source(html, "playTaskAlertOnce")
    _assert(alert_once_source, "声音提醒应通过统一去重函数播放")
    alert_proc = subprocess.run(
        ["node", "-e", f"""
          const state = {{playedAlertKeys: new Set(), soundAlertsEnabled: true}};
          let manualCount = 0;
          let completedCount = 0;
          const playManualInterventionAlert = () => manualCount += 1;
          const playTaskCompletedAlert = () => completedCount += 1;
          {alert_once_source}
          playTaskAlertOnce("manual:t1:a1", "manual");
          playTaskAlertOnce("manual:t1:a1", "manual");
          playTaskAlertOnce("completed:t1", "completed");
          playTaskAlertOnce("completed:t1", "completed");
          if (manualCount !== 1 || completedCount !== 1) throw new Error("sound_alert_not_deduplicated");
        """],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    _assert(alert_proc.returncode == 0, f"声音提醒去重轻量行为测试失败：{alert_proc.stderr or alert_proc.stdout}")
    _assert("manualSeenKey" in html and 'task.status === "paused"' in html, "实时人工提醒后进入paused终态时不得再次双滴")


def test_web_templates_and_background_sample_task_api():
    web.SERVER_TOKEN = "tag-collect-background-smoke-token"
    web.ALLOW_REAL_COLLECT = True
    _reset_web_task_memory()
    with _temporary_tag_data_dir() as data_dir:
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.TagCollectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            template_config = {
                "categories": ["女装、男装、内衣>新中式>汉服套装"],
                "tags": ["微信小店"],
                "library_filters": {
                    "monthly_dropship_orders_min": "10",
                    "dropship_shipping_fee_min": "1",
                    "dropship_shipping_fee_max": "5",
                    "seller_features": "源头旗舰",
                },
                "max_items_per_query": 12,
            }
            status, payload = _post_json(
                f"{base_url}/api/templates/save",
                {"token": web.SERVER_TOKEN, "name": "日用模板", "config": template_config},
            )
            _assert(status == 200 and payload["success"], "模板保存 API 应成功")
            _assert(payload["data"]["templates"][0]["config"]["library_filters"]["seller_features"] == "源头旗舰", "模板应完整保留商家特色单选值")
            _, options = _get_json(f"{base_url}/api/options")
            _assert(any(item.get("name") == "日用模板" for item in options["data"]["templates"]), "刷新 options 后应加载已保存模板")

            status, deleted = _post_json(
                f"{base_url}/api/templates/delete",
                {"token": web.SERVER_TOKEN, "name": "日用模板"},
            )
            _assert(status == 200 and deleted["success"] and not deleted["data"]["templates"], "模板删除 API 应移除已保存模板")

            status, started = _post_json(
                f"{base_url}/api/task/start",
                {
                    "token": web.SERVER_TOKEN,
                    "categories": ["女装、男装、内衣>新中式>汉服套装"],
                    "tags": ["微信小店"],
                    "sample_data": True,
                    "auto_verify_details": False,
                    "max_items_per_query": 5,
                    "target_publishable_count": 2,
                    "output_format": "xlsx",
                },
            )
            _assert(status == 200 and started["success"], "后台样例任务应成功启动")
            task_id = started["data"]["task_id"]
            task = started["data"]
            deadline = time.time() + 10
            while time.time() < deadline and task.get("status") not in {"completed", "failed", "cancelled"}:
                time.sleep(0.05)
                _, wrapper = _get_json(f"{base_url}/api/task/status?task_id={task_id}&token={web.SERVER_TOKEN}")
                task = wrapper["data"]
            _assert(task.get("status") == "completed", f"后台样例任务应完成，实际状态：{task.get('status')}")
            _assert(task.get("progress", {}).get("percent") == 100, "后台任务完成时进度应为100%")
            _assert("collection_stop_reason" in task.get("progress", {}), "后台任务完成进度应携带明确停止原因")
            _assert("完成" in task.get("progress", {}).get("message", ""), "完成提示应明确采集、筛选和导出已经结束")
            _assert(task.get("result", {}).get("success"), "后台任务状态应包含最终成功结果")
            _assert(task["result"]["data"].get("row_count", 0) >= 1, "后台样例任务应返回商品结果")
            task_store_path = data_dir / web.TASK_STORE_FILENAME
            _assert(task_store_path.exists(), "后台任务应写入本地状态文件")
            persisted_text = task_store_path.read_text(encoding="utf-8")
            lowered = persisted_text.lower()
            _assert('"rows"' not in lowered, "任务状态文件不应保存商品 rows")
            _assert("cookie" not in lowered and "x5secdata" not in lowered, "任务状态文件不应保存 Cookie 或风控临时参数")
            _assert(web.SERVER_TOKEN not in persisted_text, "任务状态文件不应保存 Web token")
            _, refreshed_options = _get_json(f"{base_url}/api/options")
            recent = refreshed_options["data"].get("recent_tasks") or []
            _assert(recent and recent[0].get("task_id") == task_id, "options 应返回最近任务摘要")
            _assert(recent[0].get("result_summary", {}).get("data", {}).get("row_count", 0) >= 1, "最近任务应保留安全的结果数量摘要")

            _reset_web_task_memory()
            web._ensure_tasks_loaded()
            with web.TASK_LOCK:
                recovered = dict(web.TASKS.get(task_id) or {})
            _assert(recovered.get("status") == "completed", "服务重新加载后应恢复已完成任务的终态")
            _assert(recovered.get("result") is None, "恢复历史任务时不应把商品明细重新塞回任务内存")
            _assert(recovered.get("result_summary", {}).get("data", {}).get("run_id"), "恢复终态任务时应保留 run_id 摘要")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            _reset_web_task_memory()


def test_web_task_store_marks_running_as_interrupted_and_strips_sensitive_state():
    _reset_web_task_memory()
    with _temporary_tag_data_dir() as data_dir:
        path = data_dir / web.TASK_STORE_FILENAME
        path.write_text(
            json.dumps({
                "version": 1,
                "tasks": [
                    {
                        "task_id": "running-before-restart",
                        "status": "running",
                        "created_at": "2026-07-20 09:00:00",
                        "updated_at": "2026-07-20 09:01:00",
                        "progress": {
                            "stage": "collecting",
                            "message": "正在访问 https://s.1688.com/punish?x5secdata=secret-token",
                            "percent": 35,
                            "candidate_count": 18,
                            "candidate_scan_limit": 200,
                        },
                        "summary": {
                            "category_count": 1,
                            "categories": ["女装、男装、内衣>新中式>汉服套装"],
                            "collect_source": "rpa",
                            "sample_data": False,
                        },
                        "config": {"token": "must-not-survive", "cookie": "must-not-survive"},
                        "result": {"success": False, "data": {"rows": [{"item_id": "private-row"}], "x5secdata": "must-not-survive"}},
                    },
                    {
                        "task_id": "completed-before-restart",
                        "status": "completed",
                        "created_at": "2026-07-20 08:00:00",
                        "updated_at": "2026-07-20 08:05:00",
                        "finished_at": "2026-07-20 08:05:00",
                        "progress": {"stage": "completed", "message": "任务已完成", "percent": 100},
                        "summary": {"category_count": 0, "categories": [], "collect_source": "rpa", "sample_data": True},
                        "result_summary": {"success": True, "data": {"run_id": "persisted-run", "row_count": 2, "download_url": "/download?run_id=persisted-run"}},
                    },
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        web._ensure_tasks_loaded()
        recent = web._recent_tasks()
        by_id = {item.get("task_id"): item for item in recent}
        interrupted = by_id.get("running-before-restart") or {}
        _assert(interrupted.get("status") == "interrupted", "服务启动时原 running 任务应改为 interrupted")
        _assert(interrupted.get("progress", {}).get("stage") == "interrupted", "重启中断任务应写入 interrupted 阶段")
        _assert("不会自动继续" in interrupted.get("progress", {}).get("message", ""), "重启中断提示应明确不会自动续跑")
        _assert(interrupted.get("finished_at"), "重启中断任务应补齐结束时间")
        _assert(by_id.get("completed-before-restart", {}).get("status") == "completed", "既有终态任务应原样恢复")
        _assert(web._active_task() == {}, "恢复历史任务后不应把已中断任务设为活动任务")

        rewritten = path.read_text(encoding="utf-8").lower()
        for forbidden in ('"rows"', "private-row", "must-not-survive", "cookie", "x5secdata", "secret-token"):
            _assert(forbidden not in rewritten, f"重写后的任务状态文件不应包含敏感内容：{forbidden}")
    _reset_web_task_memory()


def test_web_background_task_cancel_stops_worker():
    web.SERVER_TOKEN = "tag-collect-cancel-smoke-token"
    web.ALLOW_REAL_COLLECT = True
    original_run_tag_collect = web.run_tag_collect

    def slow_collect(config, progress_callback=None, cancel_event=None):
        if progress_callback:
            progress_callback({"stage": "collecting", "message": "慢任务测试中", "percent": 15, "candidate_count": 1})
        deadline = time.time() + 5
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                error = ServiceError("task_cancelled: 用户已停止采集任务。")
                error.data = {"code": "task_cancelled", "retryable": True}
                raise error
            time.sleep(0.02)
        raise AssertionError("取消事件未传递到后台采集任务")

    web.run_tag_collect = slow_collect
    with web.TASK_LOCK:
        web.TASKS.clear()
        web.TASK_CANCEL_EVENTS.clear()
        web.ACTIVE_TASK_ID = ""
    try:
        with _temporary_tag_data_dir():
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.TagCollectHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                _, started = _post_json(
                    f"{base_url}/api/task/start",
                    {"token": web.SERVER_TOKEN, "sample_data": True, "tags": ["微信小店"], "output_format": "xlsx"},
                )
                _assert(started["success"], "取消测试任务应成功启动")
                task_id = started["data"]["task_id"]
                deadline = time.time() + 3
                task = started["data"]
                while time.time() < deadline and task.get("status") == "queued":
                    time.sleep(0.02)
                    _, wrapper = _get_json(f"{base_url}/api/task/status?task_id={task_id}&token={web.SERVER_TOKEN}")
                    task = wrapper["data"]
                _, cancelling = _post_json(
                    f"{base_url}/api/task/cancel",
                    {"token": web.SERVER_TOKEN, "task_id": task_id},
                )
                _assert(cancelling["success"], "停止 API 应接受当前后台任务")
                deadline = time.time() + 5
                while time.time() < deadline and task.get("status") not in {"cancelled", "failed", "completed"}:
                    time.sleep(0.03)
                    _, wrapper = _get_json(f"{base_url}/api/task/status?task_id={task_id}&token={web.SERVER_TOKEN}")
                    task = wrapper["data"]
                _assert(task.get("status") == "cancelled", f"停止后后台任务应进入cancelled，实际：{task.get('status')}")
                _assert(task.get("result", {}).get("data", {}).get("error_code") == "task_cancelled", "取消结果应返回 task_cancelled")
                _assert(task.get("progress", {}).get("stage") == "cancelled", "取消完成后进度阶段应为cancelled")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
    finally:
        web.run_tag_collect = original_run_tag_collect
        with web.TASK_LOCK:
            web.TASKS.clear()
            web.TASK_CANCEL_EVENTS.clear()
        web.ACTIVE_TASK_ID = ""


def test_web_background_security_block_is_paused():
    web.SERVER_TOKEN = "tag-collect-paused-smoke-token"
    web.ALLOW_REAL_COLLECT = True
    original_run_tag_collect = web.run_tag_collect

    def blocked_collect(*args, **kwargs):
        error = ServiceError(
            "security_verification_required: 1688 触发了安全滑块/验证码校验。"
            "系统不会绕过或自动破解验证。"
            "当前页面：https://s.1688.com/selloffer/offer_search.htm/punish?x5secdata=temporary-token"
        )
        error.data = {
            "code": "security_verification_required",
            "cdp": True,
            "source": "1688_search_page",
            "page_url": "https://s.1688.com/selloffer/offer_search.htm/punish?x5secdata=temporary-token",
        }
        raise error

    web.run_tag_collect = blocked_collect
    with web.TASK_LOCK:
        web.TASKS.clear()
        web.TASK_CANCEL_EVENTS.clear()
        web.ACTIVE_TASK_ID = ""
    try:
        with _temporary_tag_data_dir():
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.TagCollectHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                _, started = _post_json(
                    f"{base_url}/api/task/start",
                    {
                        "token": web.SERVER_TOKEN,
                        "sample_data": True,
                        "tags": ["微信小店"],
                        "output_format": "xlsx",
                    },
                )
                _assert(started["success"], "风控暂停测试任务应成功启动")
                task_id = started["data"]["task_id"]
                task = started["data"]
                deadline = time.time() + 5
                while time.time() < deadline and task.get("status") not in {"paused", "failed", "completed", "cancelled"}:
                    time.sleep(0.03)
                    _, wrapper = _get_json(f"{base_url}/api/task/status?task_id={task_id}&token={web.SERVER_TOKEN}")
                    task = wrapper["data"]
                _assert(task.get("status") == "paused", f"安全验证应进入paused而非failed，实际：{task.get('status')}")
                _assert(task.get("progress", {}).get("stage") == "paused", "安全验证任务进度阶段应为paused")
                _assert("等待人工" in task.get("progress", {}).get("message", "") or "验证页已保留" in task.get("progress", {}).get("message", ""), "暂停提示应明确需要人工验证")
                result = task.get("result", {})
                _assert(result.get("data", {}).get("action") == "manual_handoff", "暂停结果应要求人工接管")
                _assert("x5secdata" not in result.get("markdown", ""), "页面提示不应展示风控页临时参数")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
    finally:
        web.run_tag_collect = original_run_tag_collect
        with web.TASK_LOCK:
            web.TASKS.clear()
            web.TASK_CANCEL_EVENTS.clear()
            web.ACTIVE_TASK_ID = ""


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
        _assert(options["workbench_build"] == web.WORKBENCH_BUILD, "options 应返回工作台版本号")
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
                "categories": ["女装、男装、内衣>新中式>汉服套装"],
                "tags": ["微信小店"],
                "library_filters": {
                    "search_keyword": "连衣裙",
                    "min_order_min": "1",
                    "sales_orders_min": "100",
                    "wholesale_price_max": "80",
                    "fulfillment_times": ["48H发货"],
                    "seller_features": "实力商家",
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
                "categories": ["女装、男装、内衣>新中式>汉服套装"],
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
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(body)
            tmp_path = tmp.name
        try:
            with zipfile.ZipFile(tmp_path) as workbook:
                workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                _assert("选品结果" in workbook_xml, "download xlsx 应包含唯一选品结果 sheet")
                _assert("核验记录" not in workbook_xml, "download xlsx 不应继续包含核验记录附表")
                _assert("筛选执行记录" not in workbook_xml, "download xlsx 不应继续包含筛选执行记录附表")
                _assert(len([name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]) == 1, "download xlsx 应严格只有一个工作表")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

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
        _assert(payload["data"]["verified_count"] >= 0, "Web 样例详情核验应返回本次实际处理数量；已自动核验完成时允许为0")
        _assert(payload["data"]["verification_records"], "Web 样例详情核验应返回字段记录")
        _assert(payload["data"]["filter_reevaluation_records"], "Web 样例详情核验应返回详情筛选重评估记录")

        web.ALLOW_REAL_COLLECT = False
        status, payload = _post_json(
            f"{base_url}/api/collect",
            {
                "token": web.SERVER_TOKEN,
                "categories": ["女装、男装、内衣>新中式>汉服套装"],
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
                "categories": ["女装、男装、内衣>新中式>汉服套装"],
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
    tests = [
        func
        for name, func in tuple(globals().items())
        if name.startswith("test_") and callable(func)
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:
            raise AssertionError(f"{test.__name__} failed: {exc}") from exc
    print(f"tag_collect smoke tests passed ({len(tests)} tests)")


if __name__ == "__main__":
    main()
