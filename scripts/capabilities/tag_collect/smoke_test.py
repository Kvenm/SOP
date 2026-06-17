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

from capabilities.tag_collect import web
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
    _assert(len(labels) == 79, "导出字段应保持附件基线的 79 列")
    _assert(labels == REFERENCE_EXPORT_LABELS, "导出字段顺序应与用户确认的附件表头一致")
    _assert("product_refund_rate" in keys, "字段应包含品退率")
    _assert("shipment_rate" in keys, "字段应包含发货率")
    _assert("wholesale_shipping_fee" in keys, "字段应包含批发运费")
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
    native_labels = {item["label"] for item in plan["native_filters"]}
    post_tags = {item["tag"] for item in plan["post_filters"]}
    _assert("一件代发" in native_labels, "一件代发应进入1688原生筛选")
    _assert("48小时发货" in native_labels, "48小时发货应进入1688原生筛选")
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
    _assert("selection_mode" in section_keys, "店雷达筛选契约应包含选品模式")
    _assert("advanced" in section_keys, "店雷达筛选契约应包含高级筛选")
    _assert("sales" in section_keys, "店雷达筛选契约应包含销售信息")
    _assert("product" in section_keys, "店雷达筛选契约应包含商品信息")
    _assert("seller" in section_keys, "店雷达筛选契约应包含卖家信息")
    _assert(capabilities["implemented"], "能力清单应标记已接入项目")
    schema_field_keys = {
        field["key"]
        for section in schema
        for field in section.get("fields", [])
    }
    coverage_keys = {item["field_key"] for item in coverage}
    _assert(schema_field_keys == coverage_keys, "筛选覆盖状态应覆盖 schema 中每一个字段")
    _assert(any(item["status"] == "detail_required" for item in coverage), "覆盖状态应包含详情核验字段")
    _assert(any(item["status"] == "reserved" for item in coverage), "覆盖状态应包含预留字段")

    library_filters = {
        "category_paths": ["女装/女士精品>连衣裙"],
        "search_keyword": "连衣裙",
        "match_type": "模糊匹配",
        "selection_modes": ["源头工厂", "无货源选品"],
        "downstream_platforms": ["抖店", "拼多多"],
        "cross_border_supply": True,
        "authorized_own_brand": True,
        "sales_orders_min": "100",
        "wholesale_price_max": "50",
        "repurchase_rate_min": "10",
        "fulfillment_times": ["48小时"],
        "waybill_support": ["抖音", "拼多多"],
        "rights_protection": ["7天包退货", "赠运费险"],
        "dropship_rights": ["一件代发包邮"],
        "seller_member_types": ["实力商家", "超级工厂"],
        "order_growth_7d_min": "20",
    }
    config = parse_input(sample_data=True, library_filters=library_filters)
    plan = build_filter_plan(config)
    native_labels = {item["label"] for item in plan["native_filters"]}
    post_fields = {item["field"] for item in plan["post_filters"]}
    reserved_keys = {item["field_key"] for item in plan["library_reserved_fields"]}
    _assert(config.categories == ["女装/女士精品>连衣裙"], "library_filters 类目应进入 config.categories")
    _assert(config.keywords == ["连衣裙"], "library_filters 关键词应进入 config.keywords")
    _assert("工厂" in native_labels, "源头工厂应转译为工厂原生筛选")
    _assert("超级工厂" in native_labels, "源头工厂应转译为超级工厂原生筛选")
    _assert("一件代发包邮" in native_labels, "无货源选品应转译为代发权益")
    _assert("48小时发货" in native_labels, "发货时间应转译为原生筛选")
    _assert("支持抖音面单" in native_labels, "面单支持应转译为原生筛选")
    _assert("支持拼多多面单" in native_labels, "面单支持应转译为原生筛选")
    _assert("orders_30d" in post_fields, "销售订单数应进入后置指标筛选")
    _assert("wholesale_price" in post_fields, "批发价应进入后置指标筛选")
    _assert("repurchase_rate" in post_fields, "复购率应进入后置指标筛选")
    _assert("order_growth_7d" in reserved_keys, "7日订单增长率应标记为预留字段")

    direct_plan = build_library_filter_plan(library_filters)
    _assert(direct_plan["results"], "店雷达字段映射应有结果记录")
    _assert(direct_plan["reserved_fields"], "预留字段应进入 reserved_fields")


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

    with zipfile.ZipFile(verify_data["output_path"]) as workbook:
        record_xml = workbook.read("xl/worksheets/sheet5.xml").decode("utf-8")
        _assert("sample_detail" in record_xml, "核验记录 sheet 应包含样例来源")


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
                    "selection_modes": ["源头工厂"],
                    "min_order_min": "1",
                    "sales_orders_min": "100",
                    "wholesale_price_max": "80",
                    "fulfillment_times": ["48小时"],
                    "seller_member_types": ["实力商家"],
                    "order_growth_7d_min": "20",
                },
                "sample_data": True,
                "output_format": "xlsx",
            },
        )
        _assert(status == 200, "带 token 样例采集 HTTP 应成功")
        _assert(payload["success"], "带 token 样例采集业务应成功")
        _assert(payload["data"]["row_count"] >= 1, "样例采集应返回商品")
        _assert(payload["data"]["library_filters"]["search_keyword"] == "连衣裙", "Web 返回应保留店雷达筛选对象")
        native_labels = {item["label"] for item in payload["data"]["filter_plan"]["native_filters"]}
        _assert("48小时发货" in native_labels, "Web 采集应转译发货时间原生筛选")
        _assert(payload["data"]["filter_plan"]["library_reserved_fields"], "Web 采集应返回预留字段记录")
        _assert(payload["data"]["library_filter_coverage"], "Web 采集应返回筛选覆盖状态")
        run_id = payload["data"]["run_id"]
        _assert(payload["data"]["verification_queue"], "Web 样例采集应返回详情核验队列")
        for row in payload["data"]["rows"]:
            for key in DETAIL_ONLY_FIELDS:
                _assert(
                    row.get(key) == DETAIL_VERIFICATION_PENDING,
                    f"Web rows 中 {key} 在详情页核验前应保持待核验占位",
                )
            _assert(row.get("verification_status") == "unverified", "Web rows 导出前应保持未核验状态")

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
    test_real_page_candidates_enter_verification_queue()
    test_web_token_and_sample_api()
    test_web_security_verification_does_not_export()
    print("tag_collect smoke tests passed")


if __name__ == "__main__":
    main()
