#!/usr/bin/env python3
"""标签选品采集 Web 工作台 — 本地筛选测试页面"""

import json
import mimetypes
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from _auth import get_ak_from_env
from capabilities.tag_collect.service import (
    EXPORT_COLUMNS,
    MAX_ITEMS_PER_QUERY,
    MAX_QUERIES,
    TAG_CATEGORY_TREE,
    TAG_FILTER_GROUPS,
    get_export_path,
    get_numbered_export_columns,
    get_run_payload,
    parse_input,
    run_tag_collect,
    verify_run_details,
)


SERVER_TOKEN = ""
ALLOW_REAL_COLLECT = False


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _csv_join(values: Any) -> str:
    if isinstance(values, list):
        return ",".join(str(item).strip() for item in values if str(item).strip())
    return str(values or "")


def _parse_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    form = parse_qs(raw.decode("utf-8"))
    return {key: values[-1] if values else "" for key, values in form.items()}


class TagCollectHandler(BaseHTTPRequestHandler):
    server_version = "TagCollectWorkbench/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        self._send(status, _json_bytes(payload))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if parsed.path == "/api/options":
            self._send_json(200, {
                "success": True,
                "data": {
                    "token": SERVER_TOKEN,
                    "allow_real_collect": ALLOW_REAL_COLLECT,
                    "limits": {
                        "max_queries": MAX_QUERIES,
                        "max_items_per_query": MAX_ITEMS_PER_QUERY,
                    },
                    "category_tree": TAG_CATEGORY_TREE,
                    "filter_groups": TAG_FILTER_GROUPS,
                    "columns": [{"key": key, "label": label} for key, label in EXPORT_COLUMNS],
                    "numbered_columns": get_numbered_export_columns(),
                },
            })
            return
        if parsed.path == "/api/run":
            run_id = (parse_qs(parsed.query).get("run_id") or [""])[0]
            payload = get_run_payload(run_id)
            if not payload:
                self._send_json(404, {"success": False, "markdown": "未找到采集批次", "data": {}})
                return
            self._send_json(200, {"success": True, "markdown": "", "data": payload})
            return
        if parsed.path == "/download":
            self._download(parsed.query)
            return
        self._send_json(404, {"success": False, "markdown": "Not found", "data": {}})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/collect", "/api/verify"):
            self._send_json(404, {"success": False, "markdown": "Not found", "data": {}})
            return

        payload = _parse_body(self)
        token = str(payload.get("token") or "")
        if token != SERVER_TOKEN:
            self._send_json(403, {
                "success": False,
                "markdown": "本地工作台 token 校验失败，已拒绝执行采集任务。",
                "data": {"run_id": "", "row_count": 0, "rows": []},
            })
            return

        if parsed.path == "/api/verify":
            run_id = str(payload.get("run_id") or "")
            if not run_id:
                self._send_json(200, {
                    "success": False,
                    "markdown": "缺少采集批次 run_id，无法执行详情核验。",
                    "data": {"run_id": "", "verified_count": 0, "rows": [], "verification_queue": []},
                })
                return
            sample_verify = bool(payload.get("sample_data", True))
            if isinstance(payload.get("sample_data"), str):
                sample_verify = payload.get("sample_data", "").lower() in ("1", "true", "yes", "on")
            if not sample_verify:
                self._send_json(200, {
                    "success": False,
                    "markdown": "真实详情核验尚未接入。当前请使用样例核验；后续将通过 prod_detail 或 Playwright/RPA 接入。",
                    "data": {"run_id": run_id, "verified_count": 0, "rows": [], "verification_queue": []},
                })
                return
            result = verify_run_details(
                run_id,
                sample_data=sample_verify,
                max_items=int(payload.get("max_items") or 20),
            )
            self._send_json(200, result)
            return

        sample_data = bool(payload.get("sample_data", True))
        if isinstance(payload.get("sample_data"), str):
            sample_data = payload.get("sample_data", "").lower() in ("1", "true", "yes", "on")

        if not sample_data:
            if not ALLOW_REAL_COLLECT:
                self._send_json(200, {
                    "success": False,
                    "markdown": "当前 Web 工作台未开启真实采集。请用 `--allow-real` 启动服务，或切回样例数据模式。",
                    "data": {"run_id": "", "row_count": 0, "rows": []},
                })
                return
            ak_id, _ = get_ak_from_env()
            if not ak_id:
                self._send_json(200, {
                    "success": False,
                    "markdown": "AK 未配置，当前 Web 工作台已阻止真实采集。请先配置 AK，或切回样例数据模式。",
                    "data": {"run_id": "", "row_count": 0, "rows": []},
                })
                return

        try:
            config = parse_input(
                categories=_csv_join(payload.get("categories")),
                tags=_csv_join(payload.get("tags")),
                keywords=_csv_join(payload.get("keywords")),
                exclude_tags=_csv_join(payload.get("exclude_tags")),
                max_queries=int(payload.get("max_queries") or 20),
                max_items_per_query=int(payload.get("max_items_per_query") or 20),
                sample_data=sample_data,
                output_format=str(payload.get("output_format") or "xlsx"),
            )
            result = run_tag_collect(config)
            data = dict(result["data"])
            data["rows"] = get_run_payload(data["run_id"]).get("rows", [])
            data["download_url"] = f"/download?run_id={data['run_id']}"
            self._send_json(200, {"success": result["success"], "markdown": result["markdown"], "data": data})
        except Exception as exc:
            self._send_json(200, {
                "success": False,
                "markdown": f"采集失败：{exc}",
                "data": {"run_id": "", "row_count": 0, "rows": []},
            })

    def _download(self, query: str) -> None:
        run_id = (parse_qs(query).get("run_id") or [""])[0]
        path = get_export_path(run_id)
        if not path:
            self._send_json(404, {"success": False, "markdown": "导出文件不存在", "data": {}})
            return
        filename = os.path.basename(path)
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve_tag_collect_workbench(host: str = "127.0.0.1", port: int = 8765, allow_real: bool = False) -> None:
    global ALLOW_REAL_COLLECT, SERVER_TOKEN
    ALLOW_REAL_COLLECT = bool(allow_real and host in ("127.0.0.1", "localhost", "::1"))
    SERVER_TOKEN = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((host, port), TagCollectHandler)
    print(f"标签选品 Web 工作台已启动：http://{host}:{port}")
    if ALLOW_REAL_COLLECT:
        print("真实采集已开启；服务仅应监听本机地址，并受 AK 与限额保护。")
    else:
        print("真实采集未开启；默认仅用于样例数据筛选测试。")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n标签选品 Web 工作台已停止。")
    finally:
        server.server_close()


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>1688 标签选品工作台</title>
  <style>
    :root {
      --bg: #f5f5f7;
      --panel: rgba(255, 255, 255, .88);
      --panel-solid: #ffffff;
      --ink: #1d1d1f;
      --muted: #6e6e73;
      --muted-2: #8d8d92;
      --line: #d2d2d7;
      --line-soft: #ebebef;
      --accent: #0071e3;
      --accent-hover: #0077ed;
      --accent-soft: #e8f2ff;
      --green: #198754;
      --amber: #a05a00;
      --danger: #b42318;
      --shadow: 0 18px 50px rgba(0, 0, 0, .07);
      --small-shadow: 0 1px 2px rgba(0, 0, 0, .05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "SF Pro Text", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    header {
      -webkit-backdrop-filter: saturate(180%) blur(18px);
      backdrop-filter: saturate(180%) blur(18px);
      background: rgba(245, 245, 247, .82);
      border-bottom: 1px solid rgba(210, 210, 215, .7);
      padding: 12px 24px;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-row {
      align-items: center;
      display: flex;
      gap: 16px;
      justify-content: space-between;
      min-height: 36px;
    }
    h1 {
      font-size: 19px;
      font-weight: 720;
      line-height: 1.2;
      margin: 0;
    }
    main {
      display: grid;
      gap: 16px;
      grid-template-columns: minmax(260px, 330px) minmax(0, 1fr);
      padding: 18px;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid rgba(210, 210, 215, .78);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }
    aside {
      max-height: calc(100vh - 92px);
      overflow: auto;
      padding: 16px;
      position: sticky;
      top: 74px;
    }
    .workspace {
      display: grid;
      gap: 14px;
      min-width: 0;
    }
    .panel {
      padding: 18px;
    }
    .panel-title {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
      margin-bottom: 14px;
    }
    h2 {
      font-size: 17px;
      font-weight: 720;
      line-height: 1.3;
      margin: 0;
    }
    h3 {
      color: var(--ink);
      font-size: 13px;
      font-weight: 720;
      margin: 18px 0 10px;
    }
    .subtle-count {
      color: var(--muted);
      font-size: 12px;
      font-weight: 560;
      margin-top: 3px;
    }
    .grid {
      display: grid;
      gap: 12px;
    }
    .grid.two {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    label {
      color: var(--muted);
      display: block;
      font-size: 12px;
      font-weight: 650;
      margin-bottom: 6px;
    }
    input[type="text"], input[type="number"], select, textarea {
      background: rgba(255, 255, 255, .92);
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      font: inherit;
      min-height: 40px;
      outline: none;
      padding: 9px 12px;
      width: 100%;
    }
    textarea {
      min-height: 86px;
      resize: vertical;
    }
    input:focus, select:focus, textarea:focus {
      border-color: rgba(0, 113, 227, .74);
      box-shadow: 0 0 0 4px rgba(0, 113, 227, .12);
    }
    .button-row {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    button, .download {
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      font-weight: 650;
      gap: 6px;
      justify-content: center;
      min-height: 38px;
      padding: 8px 16px;
      text-decoration: none;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease, color .18s ease, transform .18s ease;
      white-space: nowrap;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      box-shadow: 0 8px 20px rgba(0, 113, 227, .22);
    }
    button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
    button.secondary, .download {
      background: rgba(255, 255, 255, .82);
      color: var(--ink);
    }
    button.secondary:hover, .download:hover {
      background: var(--panel-solid);
      border-color: #b9b9c0;
    }
    button:active, .download:active { transform: translateY(1px); }
    button:disabled {
      cursor: wait;
      opacity: .65;
    }
    button:focus-visible, .download:focus-visible, .chip:focus-within, .category-primary:focus-within {
      outline: 3px solid rgba(0, 113, 227, .18);
      outline-offset: 2px;
    }
    .toggle-line {
      align-items: center;
      color: var(--ink);
      display: flex;
      gap: 8px;
      min-height: 36px;
    }
    .sample-switch {
      align-items: center;
      background: rgba(255, 255, 255, .76);
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--ink);
      display: inline-flex;
      gap: 8px;
      margin: 0;
      min-height: 38px;
      padding: 7px 12px;
    }
    .sample-switch input {
      accent-color: var(--accent);
      height: 16px;
      width: 16px;
    }
    .tabs {
      align-items: center;
      background: rgba(245, 245, 247, .9);
      border-bottom: 1px solid rgba(210, 210, 215, .7);
      display: flex;
      gap: 6px;
      padding: 10px;
    }
    .tab {
      background: transparent;
      border: 0;
      border-radius: 999px;
      color: var(--muted);
      min-height: 36px;
      padding: 8px 14px;
    }
    .tab.active {
      background: var(--panel-solid);
      box-shadow: var(--small-shadow);
      color: var(--ink);
    }
    .tab-view { display: none; }
    .tab-view.active { display: block; }
    .category-group {
      background: rgba(255, 255, 255, .62);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      margin-top: 10px;
      overflow: hidden;
    }
    .category-group.is-active {
      border-color: rgba(0, 113, 227, .34);
      box-shadow: 0 0 0 4px rgba(0, 113, 227, .06);
    }
    .category-primary {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
      margin: 0;
      min-height: 58px;
      padding: 12px;
      width: 100%;
    }
    .category-primary input {
      accent-color: var(--accent);
      height: 18px;
      width: 18px;
    }
    .category-title {
      color: var(--ink);
      display: grid;
      flex: 1;
      gap: 3px;
      margin: 0;
      min-width: 0;
    }
    .category-title strong {
      font-size: 14px;
      font-weight: 760;
      line-height: 1.2;
    }
    .tier-label {
      color: var(--muted-2);
      font-size: 11px;
      font-weight: 700;
    }
    .category-count {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .category-children {
      border-top: 1px solid var(--line-soft);
      padding: 10px 12px 12px;
    }
    .category-children .tier-label {
      margin-bottom: 8px;
    }
    .tag-grid {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .chip {
      align-items: center;
      background: rgba(255, 255, 255, .76);
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--ink);
      display: flex;
      gap: 6px;
      min-height: 34px;
      margin: 0;
      padding: 7px 10px;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease;
      word-break: break-word;
    }
    .chip:hover { background: var(--panel-solid); border-color: #b9b9c0; }
    .chip input {
      accent-color: var(--accent);
      flex: 0 0 auto;
      height: 15px;
      width: 15px;
    }
    .chip:has(input:checked) {
      background: var(--accent-soft);
      border-color: rgba(0, 113, 227, .38);
      box-shadow: inset 0 0 0 1px rgba(0, 113, 227, .04);
      color: #064a8f;
      font-weight: 700;
    }
    .filter-block {
      background: rgba(255, 255, 255, .54);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      margin-top: 10px;
      padding: 12px;
    }
    .filter-block-head {
      align-items: baseline;
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
    }
    .filter-block-head strong {
      font-size: 13px;
      font-weight: 760;
    }
    .filter-block-head span {
      color: var(--muted);
      font-size: 12px;
    }
    .filter-block .tag-grid {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .summary {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-top: 14px;
    }
    .metric {
      background: var(--panel-solid);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      padding: 12px;
    }
    .metric span {
      color: var(--muted);
      display: block;
      font-size: 12px;
      margin-bottom: 4px;
    }
    .metric strong {
      font-size: 18px;
      font-weight: 760;
    }
    .notice {
      border-radius: 6px;
      display: none;
      margin-top: 12px;
      padding: 10px 12px;
    }
    .notice.show { display: block; }
    .notice.ok {
      background: #eaf7f0;
      color: var(--green);
    }
    .notice.fail {
      background: #fff0f0;
      color: var(--danger);
    }
    .result-tools {
      align-items: end;
      display: grid;
      gap: 10px;
      grid-template-columns: minmax(140px, 1.3fr) repeat(4, minmax(112px, .8fr));
      margin-bottom: 12px;
    }
    .verification-strip {
      align-items: center;
      background: rgba(255, 255, 255, .64);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(3, minmax(110px, 1fr)) auto;
      margin-bottom: 12px;
      padding: 12px;
    }
    .verification-strip span {
      color: var(--muted);
      display: block;
      font-size: 12px;
      margin-bottom: 3px;
    }
    .verification-strip strong {
      font-size: 17px;
      font-weight: 760;
    }
    .table-wrap {
      border: 1px solid var(--line);
      border-radius: 8px;
      max-height: 520px;
      overflow: auto;
      background: var(--panel-solid);
    }
    table {
      border-collapse: collapse;
      min-width: 1780px;
      table-layout: fixed;
      width: 100%;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    th {
      background: #f8f8fa;
      color: var(--muted);
      font-size: 12px;
      font-weight: 720;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    tbody tr:hover td { background: #fbfbfd; }
    th.col-seq, td.col-seq { width: 58px; }
    th.col-category_path, td.col-category_path { width: 270px; }
    th.col-title, td.col-title { width: 360px; }
    th.col-item_id, td.col-item_id { width: 140px; }
    th.col-wholesale_price, td.col-wholesale_price { width: 88px; }
    th.col-orders_30d, td.col-orders_30d,
    th.col-units_30d, td.col-units_30d,
    th.col-comment_count, td.col-comment_count { width: 116px; }
    th.col-repurchase_rate, td.col-repurchase_rate,
    th.col-recommendation_score, td.col-recommendation_score { width: 92px; }
    th.col-monthly_dropship_orders, td.col-monthly_dropship_orders,
    th.col-wholesale_shipping_fee, td.col-wholesale_shipping_fee,
    th.col-dropship_shipping_fee, td.col-dropship_shipping_fee,
    th.col-product_refund_rate, td.col-product_refund_rate,
    th.col-shipment_rate, td.col-shipment_rate,
    th.col-recommendation_level, td.col-recommendation_level,
    th.col-verification_status, td.col-verification_status { width: 120px; }
    th.col-risk_flags, td.col-risk_flags { width: 210px; }
    th.col-wechat_shop_suggestion, td.col-wechat_shop_suggestion { width: 170px; }
    td.col-category_path, td.col-title, td.col-risk_flags {
      white-space: normal;
      word-break: break-word;
    }
    .badge {
      border-radius: 999px;
      display: inline-block;
      font-size: 12px;
      font-weight: 650;
      min-width: 44px;
      padding: 3px 8px;
      text-align: center;
    }
    .badge.p0 { background: #eaf7f0; color: var(--green); }
    .badge.p1 { background: var(--accent-soft); color: #0753a2; }
    .badge.p2 { background: #fff3df; color: var(--amber); }
    .badge.no { background: #f1f2f4; color: var(--muted); }
    .field-grid {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .field-item {
      background: var(--panel-solid);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      display: grid;
      gap: 4px;
      grid-template-columns: 52px 1fr;
      min-height: 54px;
      padding: 10px;
    }
    .field-number {
      color: var(--accent);
      font-weight: 750;
    }
    .field-label {
      color: var(--ink);
      font-weight: 650;
      word-break: break-word;
    }
    .field-key {
      color: var(--muted);
      font-size: 12px;
      grid-column: 2;
    }
    .verification-records {
      border-top: 1px solid var(--line-soft);
      margin-top: 14px;
      padding-top: 14px;
    }
    .record-list {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 10px;
    }
    .record-item {
      background: var(--panel-solid);
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      padding: 10px;
    }
    .record-item strong {
      display: block;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .record-item span {
      color: var(--muted);
      display: block;
      font-size: 12px;
      line-height: 1.4;
    }
    @media (max-width: 760px) {
      main { grid-template-columns: 1fr; }
      aside {
        max-height: 560px;
        position: static;
      }
      .result-tools, .summary, .grid.two, .field-grid, .verification-strip, .record-list {
        grid-template-columns: 1fr;
      }
      .filter-block .tag-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width: 620px) {
      header { padding: 10px 12px; }
      main { padding: 12px; }
      .header-row { align-items: flex-start; flex-direction: column; }
      .tag-grid, .filter-block .tag-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <h1>1688 标签选品工作台</h1>
      <div class="button-row">
        <label class="sample-switch"><input id="sampleMode" type="checkbox" checked />样例数据</label>
        <button id="runBtn" class="primary" type="button">运行采集</button>
        <a id="downloadLink" class="download" href="#" hidden>下载表格</a>
      </div>
    </div>
  </header>

  <main>
    <aside>
      <div class="panel-title">
        <div>
          <h2>一级类目</h2>
          <div class="subtle-count">先圈定采集范围，再用二级类目细化</div>
        </div>
        <button id="clearCategories" class="secondary" type="button">清空</button>
      </div>
      <input id="categorySearch" type="text" placeholder="搜索类目" />
      <div id="categoryTree"></div>
    </aside>

    <div class="workspace">
      <section>
        <div class="tabs">
          <button class="tab active" type="button" data-tab="task">筛选任务</button>
          <button class="tab" type="button" data-tab="results">结果表格</button>
          <button class="tab" type="button" data-tab="fields">字段编号</button>
        </div>

        <div id="task" class="tab-view active panel">
          <div class="grid two">
            <div>
              <label for="keywords">搜索词</label>
              <textarea id="keywords" placeholder="可输入多个搜索词，用逗号分隔"></textarea>
            </div>
            <div>
              <label for="excludeTags">排除标签</label>
              <textarea id="excludeTags" placeholder="例如 品牌风险,高退货"></textarea>
            </div>
          </div>

          <div class="grid two" style="margin-top: 12px;">
            <div>
              <label for="outputFormat">导出格式</label>
              <select id="outputFormat">
                <option value="xlsx">XLSX</option>
                <option value="csv">CSV</option>
              </select>
            </div>
            <div class="grid two">
              <div>
                <label for="maxQueries">查询词上限</label>
                <input id="maxQueries" type="number" min="1" max="200" value="20" />
              </div>
              <div>
                <label for="maxItems">每词商品上限</label>
                <input id="maxItems" type="number" min="1" max="100" value="20" />
              </div>
            </div>
          </div>

          <h3>二级运营筛选</h3>
          <div id="filterGroups"></div>

          <div id="notice" class="notice"></div>
          <div class="summary">
            <div class="metric"><span>采集批次</span><strong id="runId">-</strong></div>
            <div class="metric"><span>商品数</span><strong id="rowCount">0</strong></div>
            <div class="metric"><span>P0/P1</span><strong id="highCount">0</strong></div>
            <div class="metric"><span>可铺/谨慎</span><strong id="suggestCount">0</strong></div>
          </div>
        </div>

        <div id="results" class="tab-view panel">
          <div class="verification-strip">
            <div><span>待核验高潜</span><strong id="queueCount">0</strong></div>
            <div><span>已核验商品</span><strong id="verifiedCount">0</strong></div>
            <div><span>核验记录</span><strong id="recordCount">0</strong></div>
            <button id="verifyBtn" class="secondary" type="button" disabled>样例核验高潜</button>
          </div>
          <div class="result-tools">
            <div>
              <label for="titleFilter">商品/类目</label>
              <input id="titleFilter" type="text" placeholder="筛选表格" />
            </div>
            <div>
              <label for="minScore">最低分</label>
              <input id="minScore" type="number" min="0" max="100" value="0" />
            </div>
            <div>
              <label for="levelFilter">推荐等级</label>
              <select id="levelFilter">
                <option value="">全部</option>
                <option value="P0">P0</option>
                <option value="P1">P1</option>
                <option value="P2">P2</option>
                <option value="不建议">不建议</option>
              </select>
            </div>
            <div>
              <label for="verificationFilter">核验状态</label>
              <select id="verificationFilter">
                <option value="">全部</option>
                <option value="unverified">unverified</option>
                <option value="sample_verified">sample_verified</option>
                <option value="verified">verified</option>
                <option value="failed">failed</option>
              </select>
            </div>
            <div>
              <label for="suggestFilter">铺货建议</label>
              <select id="suggestFilter">
                <option value="">全部</option>
                <option value="可铺">可铺</option>
                <option value="谨慎">谨慎</option>
                <option value="不建议">不建议</option>
              </select>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead id="resultHead"></thead>
              <tbody id="resultBody"></tbody>
            </table>
          </div>
          <div class="verification-records">
            <div class="panel-title">
              <div>
                <h2>字段核验记录</h2>
                <div class="subtle-count">记录 raw / normalized / source / verified_at / fail_reason</div>
              </div>
            </div>
            <div id="recordList" class="record-list"></div>
          </div>
        </div>

        <div id="fields" class="tab-view panel">
          <div id="fieldGrid" class="field-grid"></div>
        </div>
      </section>
    </div>
  </main>

  <script>
    const state = {
      rows: [],
      options: null,
      runId: "",
      verificationQueue: [],
      verificationRecords: [],
      selectedCategories: new Set(),
      selectedTags: new Set(),
      tableColumns: [
        ["seq", "序号"],
        ["category_path", "商品类目"],
        ["title", "商品标题"],
        ["item_id", "商品ID"],
        ["wholesale_price", "批发价"],
        ["orders_30d", "近30天订单数"],
        ["units_30d", "近30天件数"],
        ["repurchase_rate", "复购率"],
        ["monthly_dropship_orders", "月代发订单"],
        ["comment_count", "评论数"],
        ["wholesale_shipping_fee", "批发运费"],
        ["dropship_shipping_fee", "代发运费"],
        ["product_refund_rate", "品退率"],
        ["shipment_rate", "发货率"],
        ["verified_at", "核验时间"],
        ["recommendation_score", "推荐分"],
        ["recommendation_level", "推荐等级"],
        ["risk_flags", "风险提示"],
        ["verification_status", "核验状态"],
        ["wechat_shop_suggestion", "适合微信小店(规则预判)"]
      ]
    };

    const $ = (id) => document.getElementById(id);

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }

    function showNotice(text, ok) {
      const node = $("notice");
      node.textContent = text;
      node.className = `notice show ${ok ? "ok" : "fail"}`;
    }

    function renderCategories() {
      const wrap = $("categoryTree");
      const q = $("categorySearch").value.trim().toLowerCase();
      const tree = state.options.category_tree;
      wrap.innerHTML = Object.entries(tree).map(([parent, children]) => {
        const matchedChildren = children.filter(child => (`${parent} ${child}`).toLowerCase().includes(q));
        if (q && !parent.toLowerCase().includes(q) && matchedChildren.length === 0) return "";
        const childHtml = matchedChildren.map(child => chipHtml(`${parent}>${child}`, child, "category")).join("");
        const selectedChildren = children.filter(child => state.selectedCategories.has(`${parent}>${child}`)).length;
        const active = state.selectedCategories.has(parent) || selectedChildren > 0;
        return `
          <div class="category-group ${active ? "is-active" : ""}">
            <label class="category-primary">
              <input type="checkbox" data-kind="category" value="${esc(parent)}" ${state.selectedCategories.has(parent) ? "checked" : ""} />
              <span class="category-title">
                <span class="tier-label">一级筛选</span>
                <strong>${esc(parent)}</strong>
              </span>
              <span class="category-count">${selectedChildren ? `${selectedChildren}/` : ""}${children.length} 个二级</span>
            </label>
            <div class="category-children">
              <div class="tier-label">二级筛选</div>
              <div class="tag-grid">${childHtml}</div>
            </div>
          </div>
        `;
      }).join("");
    }

    function chipHtml(value, label, kind) {
      const selected = kind === "category" ? state.selectedCategories.has(value) : state.selectedTags.has(value);
      return `<label class="chip"><input type="checkbox" data-kind="${kind}" value="${esc(value)}" ${selected ? "checked" : ""} />${esc(label)}</label>`;
    }

    function renderFilterGroups() {
      $("filterGroups").innerHTML = Object.entries(state.options.filter_groups).map(([group, tags]) => `
        <div class="filter-block">
          <div class="filter-block-head">
            <strong>${esc(group)}</strong>
            <span>${tags.filter(tag => state.selectedTags.has(tag)).length}/${tags.length}</span>
          </div>
          <div class="tag-grid">${tags.map(tag => chipHtml(tag, tag, "tag")).join("")}</div>
        </div>
      `).join("");
    }

    function renderFields() {
      $("fieldGrid").innerHTML = state.options.numbered_columns.map(field => `
        <div class="field-item">
          <div class="field-number">${esc(field.number)}</div>
          <div class="field-label">${esc(field.label)}</div>
          <div class="field-key">${esc(field.key)} · ${esc(field.group)} · ${esc(field.source || "")} · ${esc(field.verify || "")}</div>
        </div>
      `).join("");
    }

    function renderTable() {
      $("resultHead").innerHTML = `<tr>${state.tableColumns.map(([key, label]) => `<th class="${tableCellClass(key)}">${esc(label)}</th>`).join("")}</tr>`;
      const rows = filteredRows();
      $("resultBody").innerHTML = rows.map(row => `
        <tr>
          ${state.tableColumns.map(([key]) => `<td class="${tableCellClass(key)}">${formatCell(key, row[key])}</td>`).join("")}
        </tr>
      `).join("") || `<tr><td colspan="${state.tableColumns.length}">暂无数据</td></tr>`;
    }

    function tableCellClass(key) {
      return `col-${String(key).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    }

    function formatCell(key, value) {
      if (key === "url" && value) return `<a href="${esc(value)}" target="_blank" rel="noreferrer">打开</a>`;
      if (key === "recommendation_level") {
        const cls = value === "P0" ? "p0" : value === "P1" ? "p1" : value === "P2" ? "p2" : "no";
        return `<span class="badge ${cls}">${esc(value || "-")}</span>`;
      }
      if (key === "verification_status") {
        const cls = value === "sample_verified" || value === "verified" ? "p0" : value === "failed" ? "no" : "p2";
        return `<span class="badge ${cls}">${esc(value || "-")}</span>`;
      }
      return esc(value);
    }

    function filteredRows() {
      const text = $("titleFilter").value.trim().toLowerCase();
      const minScore = Number($("minScore").value || 0);
      const level = $("levelFilter").value;
      const verification = $("verificationFilter").value;
      const suggest = $("suggestFilter").value;
      return state.rows.filter(row => {
        const haystack = `${row.title || ""} ${row.category_path || ""} ${row.risk_flags || ""}`.toLowerCase();
        return (!text || haystack.includes(text))
          && Number(row.recommendation_score || 0) >= minScore
          && (!level || row.recommendation_level === level)
          && (!verification || row.verification_status === verification)
          && (!suggest || row.wechat_shop_suggestion === suggest);
      });
    }

    function updateSummary(data) {
      state.runId = data.run_id || state.runId || "";
      state.verificationQueue = data.verification_queue || state.verificationQueue || [];
      state.verificationRecords = data.verification_records || state.verificationRecords || [];
      $("runId").textContent = data.run_id || "-";
      $("rowCount").textContent = String(state.rows.length);
      $("highCount").textContent = String(state.rows.filter(row => ["P0", "P1"].includes(row.recommendation_level)).length);
      $("suggestCount").textContent = String(state.rows.filter(row => ["可铺", "谨慎"].includes(row.wechat_shop_suggestion)).length);
      $("queueCount").textContent = String(state.verificationQueue.length);
      $("verifiedCount").textContent = String(state.rows.filter(row => ["sample_verified", "verified"].includes(row.verification_status)).length);
      $("recordCount").textContent = String(state.verificationRecords.length);
      $("verifyBtn").disabled = !state.runId || state.verificationQueue.length === 0;
      renderRecords();
    }

    function renderRecords() {
      const records = (state.verificationRecords || []).slice(-12).reverse();
      $("recordList").innerHTML = records.map(record => `
        <div class="record-item">
          <strong>${esc(record.field_label || record.field_key)} · ${esc(record.item_id)}</strong>
          <span>值：${esc(record.normalized || record.raw || "-")}</span>
          <span>来源：${esc(record.source || "-")} · ${esc(record.status || "-")}</span>
          <span>时间：${esc(record.verified_at || "-")}</span>
          ${record.fail_reason ? `<span>失败：${esc(record.fail_reason)}</span>` : ""}
        </div>
      `).join("") || `<div class="record-item"><strong>暂无核验记录</strong><span>采集后点击“样例核验高潜”生成字段级记录。</span></div>`;
    }

    async function runCollect() {
      $("runBtn").disabled = true;
      $("runBtn").textContent = "采集中";
      try {
        const payload = {
          categories: [...state.selectedCategories],
          tags: [...state.selectedTags],
          keywords: $("keywords").value,
          exclude_tags: $("excludeTags").value,
          max_queries: Number($("maxQueries").value || 20),
          max_items_per_query: Number($("maxItems").value || 20),
          output_format: $("outputFormat").value,
          sample_data: $("sampleMode").checked,
          token: state.options.token
        };
        const response = await fetch("/api/collect", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!result.success) {
          state.rows = [];
          renderTable();
          updateSummary({});
          showNotice(result.markdown || "采集失败", false);
          return;
        }
        state.rows = result.data.rows || [];
        state.runId = result.data.run_id || "";
        state.verificationQueue = result.data.verification_queue || [];
        state.verificationRecords = result.data.verification_records || [];
        renderTable();
        updateSummary(result.data);
        $("downloadLink").href = result.data.download_url;
        $("downloadLink").hidden = false;
        showNotice(`已生成 ${result.data.row_count} 条初筛商品；运费、品退率、发货率等关键字段仍需详情页核验。查询词：${(result.data.queries || []).join("，")}`, true);
        setTab("results");
      } catch (err) {
        showNotice(`采集失败：${err.message}`, false);
      } finally {
        $("runBtn").disabled = false;
        $("runBtn").textContent = "运行采集";
      }
    }

    async function runVerify() {
      if (!state.runId) {
        showNotice("请先运行采集，生成采集批次后再核验。", false);
        return;
      }
      $("verifyBtn").disabled = true;
      $("verifyBtn").textContent = "核验中";
      try {
        const response = await fetch("/api/verify", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            token: state.options.token,
            run_id: state.runId,
            sample_data: $("sampleMode").checked,
            max_items: 20
          })
        });
        const result = await response.json();
        if (!result.success) {
          showNotice(result.markdown || "核验失败", false);
          return;
        }
        state.rows = result.data.rows || [];
        state.verificationQueue = result.data.verification_queue || [];
        state.verificationRecords = result.data.verification_records || [];
        renderTable();
        updateSummary(result.data);
        $("downloadLink").href = result.data.download_url;
        $("downloadLink").hidden = false;
        showNotice(`已核验 ${result.data.verified_count} 个高潜商品，导出表已刷新。`, true);
      } catch (err) {
        showNotice(`核验失败：${err.message}`, false);
      } finally {
        $("verifyBtn").textContent = "样例核验高潜";
        $("verifyBtn").disabled = !state.runId || state.verificationQueue.length === 0;
      }
    }

    function setTab(name) {
      document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === name));
      document.querySelectorAll(".tab-view").forEach(view => view.classList.toggle("active", view.id === name));
    }

    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!target.matches("input[type='checkbox'][data-kind]")) return;
      const set = target.dataset.kind === "category" ? state.selectedCategories : state.selectedTags;
      target.checked ? set.add(target.value) : set.delete(target.value);
      target.dataset.kind === "category" ? renderCategories() : renderFilterGroups();
    });

    document.addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) setTab(tab.dataset.tab);
    });

    ["titleFilter", "minScore", "levelFilter", "verificationFilter", "suggestFilter"].forEach(id => {
      document.addEventListener("input", (event) => {
        if (event.target.id === id) renderTable();
      });
      document.addEventListener("change", (event) => {
        if (event.target.id === id) renderTable();
      });
    });

    $("categorySearch").addEventListener("input", renderCategories);
    $("clearCategories").addEventListener("click", () => {
      state.selectedCategories.clear();
      renderCategories();
    });
    $("runBtn").addEventListener("click", runCollect);
    $("verifyBtn").addEventListener("click", runVerify);

    async function init() {
      const response = await fetch("/api/options");
      const result = await response.json();
      state.options = result.data;
      $("maxQueries").max = state.options.limits.max_queries;
      $("maxItems").max = state.options.limits.max_items_per_query;
      if (!state.options.allow_real_collect) {
        $("sampleMode").checked = true;
        showNotice("当前为样例数据测试模式；真实采集需用 --allow-real 启动，并配置 AK。", true);
      }
      renderCategories();
      renderFilterGroups();
      renderFields();
      renderTable();
      updateSummary({});
    }

    init();
  </script>
</body>
</html>
"""
