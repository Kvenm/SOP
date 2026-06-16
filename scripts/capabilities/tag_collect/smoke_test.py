#!/usr/bin/env python3
"""tag_collect 最小回归测试：字段、导出、Web token、真实模式开关、开发样例采集。"""

import json
import os
import sys
import threading
import tempfile
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from capabilities.tag_collect import web
from capabilities.tag_collect.service import (
    DETAIL_ONLY_FIELDS,
    DETAIL_VERIFICATION_PENDING,
    Product,
    build_filter_plan,
    build_queries,
    build_verification_queue,
    get_numbered_export_columns,
    metric_bucket,
    product_to_export_row,
    parse_input,
    run_tag_collect,
    verify_run_details,
)


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


def test_field_numbers():
    fields = get_numbered_export_columns()
    numbers = [field["number"] for field in fields]
    keys = {field["key"] for field in fields}
    _assert(numbers[0] == "1.1", "字段编号应从 1.1 开始")
    _assert(numbers[-1] == "10.11", "字段编号应到 10.11 结束")
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
        field_xml = workbook.read("xl/worksheets/sheet2.xml").decode("utf-8")
        _assert("选品结果" in workbook_xml, "应包含选品结果 sheet")
        _assert("字段说明" in workbook_xml, "应包含字段说明 sheet")
        _assert("标签配置" in workbook_xml, "应包含标签配置 sheet")
        _assert("核验失败" in workbook_xml, "应包含核验失败 sheet")
        _assert("核验记录" in workbook_xml, "应包含核验记录 sheet")
        _assert("筛选执行记录" in workbook_xml, "应包含筛选执行记录 sheet")
        _assert("10.8" in field_xml, "字段说明应包含 10.8 核验状态")
        _assert("品退率" in field_xml, "字段说明应包含品退率")
        _assert("发货率" in field_xml, "字段说明应包含发货率")
        filter_xml = workbook.read("xl/worksheets/sheet6.xml").decode("utf-8")
        _assert("一件代发" in filter_xml, "筛选执行记录应包含一件代发")
        _assert("sample_skipped" in filter_xml, "样例模式应标记原生筛选未执行")


def test_sample_detail_verification():
    config = parse_input(
        categories="女装/女士精品,家用电器",
        tags="微信小店,一件代发,48小时发货",
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
                "sample_data": True,
                "output_format": "xlsx",
            },
        )
        _assert(status == 200, "带 token 样例采集 HTTP 应成功")
        _assert(payload["success"], "带 token 样例采集业务应成功")
        _assert(payload["data"]["row_count"] >= 1, "样例采集应返回商品")
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


def main():
    test_field_numbers()
    test_filter_plan_splits_native_and_metric_tags()
    test_metric_bucket_ranges()
    test_export_xlsx_and_exclude()
    test_sample_detail_verification()
    test_real_page_candidates_enter_verification_queue()
    test_web_token_and_sample_api()
    print("tag_collect smoke tests passed")


if __name__ == "__main__":
    main()
