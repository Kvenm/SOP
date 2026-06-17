#!/usr/bin/env python3
"""tag_collect 最小回归测试：字段、导出、Web token、真实模式开关、开发样例采集。"""

import json
import os
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


def test_field_numbers():
    fields = get_numbered_export_columns()
    numbers = [field["number"] for field in fields]
    keys = {field["key"] for field in fields}
    labels = [label for _, label in EXPORT_COLUMNS]
    _assert(numbers[0] == "1.1", "字段编号应从 1.1 开始")
    _assert(numbers[-1] == "10.11", "字段编号应到 10.11 结束")
    _assert(len(labels) == 81, "导出字段应包含附件基线和截图新增评价字段的 81 列")
    _assert(labels == REFERENCE_EXPORT_LABELS, "导出字段顺序应与用户确认的附件表头一致")
    _assert("product_refund_rate" in keys, "字段应包含品退率")
    _assert("shipment_rate" in keys, "字段应包含发货率")
    _assert("wholesale_shipping_fee" in keys, "字段应包含批发运费")
    _assert("product_rating" in keys, "字段应包含商品星级")
    _assert("review_tags" in keys, "字段应包含评价标签")
    _assert("good_rate_bucket" in keys, "字段应包含好评率区间")
    _assert("shipment_rate_bucket" in keys, "字段应包含发货率区间")


def test_filter_plan_splits_native_and_metric_tags():
    config = parse_input(
        categories="女装/女士精品>防晒衣>冰丝防晒衣",
        tags="微信小店,一件代发,48小时发货,好评率>=90%,评论数30-99,防晒",
        sample_data=True,
    )
    plan = build_filter_plan(config)
    queries = build_queries(config)
    query_text = " ".join(queries)
    post_tags = {item["tag"] for item in plan["post_filters"]}
    _assert(_native_has(plan["native_filters"], "一件代发"), "一件代发应进入1688原生筛选")
    _assert(_native_has(plan["native_filters"], "48小时发货"), "48小时发货应进入1688原生筛选")
    _assert("好评率>=90%" in post_tags, "好评率区间应进入后置指标筛选")
    _assert("评论数30-99" in post_tags, "评论数区间应进入后置指标筛选")
    _assert("防晒" in query_text, "场景词防晒应保留为搜索词")
    _assert("一件代发" not in query_text, "原生筛选不应拼进搜索词")
    _assert("48小时发货" not in query_text, "原生筛选不应拼进搜索词")


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
        "category_paths": ["女装/女士精品>连衣裙"],
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
    _assert(config.categories == ["女装/女士精品>连衣裙"], "library_filters 类目应进入 config.categories")
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
        categories="女装/女士精品,家用电器",
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
        _assert(len(sheets) == 6, "xlsx 应包含 6 个工作表")
        workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
        result_xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        field_xml = workbook.read("xl/worksheets/sheet2.xml").decode("utf-8")
        result_labels = _xlsx_first_row_labels(workbook, 1)
        _assert(result_labels == REFERENCE_EXPORT_LABELS, "选品结果 sheet 表头应严格等于附件 79 列基线")
        _assert("选品结果" in workbook_xml, "应包含选品结果 sheet")
        _assert("字段说明" in workbook_xml, "应包含字段说明 sheet")
        _assert("标签配置" in workbook_xml, "应包含标签配置 sheet")
        _assert("核验失败" in workbook_xml, "应包含核验失败 sheet")
        _assert("核验记录" in workbook_xml, "应包含核验记录 sheet")
        _assert("筛选执行记录" in workbook_xml, "应包含筛选执行记录 sheet")
        _assert("10.8" in field_xml, "字段说明应包含 10.8 核验状态")
        _assert("品退率" in field_xml, "字段说明应包含品退率")
        _assert("发货率" in field_xml, "字段说明应包含发货率")
        for label in REFERENCE_EXPORT_LABELS:
            _assert(label in result_xml, f"选品结果表头应包含附件基线字段：{label}")
        filter_xml = workbook.read("xl/worksheets/sheet6.xml").decode("utf-8")
        _assert("一件代发" in filter_xml, "筛选执行记录应包含一件代发")
        _assert("sample_skipped" in filter_xml, "样例模式应标记原生筛选未执行")


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


def test_sample_detail_verification():
    config = parse_input(
        categories="女装/女士精品,家用电器",
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

    with zipfile.ZipFile(verify_data["output_path"]) as workbook:
        record_xml = workbook.read("xl/worksheets/sheet5.xml").decode("utf-8")
        _assert("sample_detail" in record_xml, "核验记录 sheet 应包含样例来源")
        _assert("商品星级" in record_xml, "核验记录 sheet 应包含商品星级")
        _assert("评价标签" in record_xml, "核验记录 sheet 应包含评价标签")


def test_auto_detail_verification_after_collect():
    config = parse_input(
        categories="女装/女士精品",
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
            "filter_results": [],
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
            categories="家居日用品",
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


def test_real_page_candidates_enter_verification_queue():
    config = parse_input(
        categories="家居日用品",
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
        "家居日用品",
        config,
    )
    _assert(row["list_source"] == "rpa", "真实页面候选商品应标记 list_source=rpa")
    queue = build_verification_queue([row])
    _assert(queue, "真实页面候选商品即使列表指标不足，也应进入详情核验队列")
    _assert("真实页面候选商品" in queue[0]["reason"], "核验队列应标明真实页面来源")


def test_review_filters_wait_for_detail_then_match():
    config = parse_input(
        categories="居家日用品",
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
        categories="居家日用品",
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
        categories="居家日用品",
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
                "categories": ["女装/女士精品"],
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
                "output_format": "xlsx",
            },
        )
        _assert(status == 200, "带 token 样例采集 HTTP 应成功")
        _assert(payload["success"], "带 token 样例采集业务应成功")
        _assert(payload["data"]["row_count"] >= 1, "样例采集应返回商品")
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
                "categories": ["女装/女士精品"],
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
                "categories": ["女装/女士精品"],
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
    test_field_numbers()
    test_filter_plan_splits_native_and_metric_tags()
    test_library_filter_contract_and_mapping()
    test_metric_bucket_ranges()
    test_export_xlsx_and_exclude()
    test_security_verification_error_message()
    test_sample_detail_verification()
    test_auto_detail_verification_after_collect()
    test_auto_detail_verification_security_pause()
    test_real_page_candidates_enter_verification_queue()
    test_review_filters_wait_for_detail_then_match()
    test_detail_filter_excluded_rows_leave_main_results()
    test_detail_missing_review_metrics_do_not_use_list_values()
    test_web_token_and_sample_api()
    test_web_security_verification_does_not_export()
    print("tag_collect smoke tests passed")


if __name__ == "__main__":
    main()
