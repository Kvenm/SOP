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
    CATEGORY_DICTIONARY,
    EXPORT_COLUMNS,
    MAX_ITEMS_PER_QUERY,
    MAX_QUERIES,
    TAG_CATEGORY_TREE,
    TAG_FILTER_GROUPS,
    METRIC_FILTER_GROUPS,
    get_export_path,
    get_library_capabilities,
    get_library_filter_coverage,
    get_library_filter_schema,
    get_numbered_export_columns,
    collect_error_state,
    get_run_payload,
    parse_input,
    run_tag_collect,
    verify_run_details,
)


SERVER_TOKEN = ""
ALLOW_REAL_COLLECT = True


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
                    "category_dictionary": {
                        "version": CATEGORY_DICTIONARY.get("version", ""),
                        "source": CATEGORY_DICTIONARY.get("source", ""),
                        "status": CATEGORY_DICTIONARY.get("status", ""),
                        "updated_at": CATEGORY_DICTIONARY.get("updated_at", ""),
                    },
                    "filter_groups": TAG_FILTER_GROUPS,
                    "metric_filter_groups": METRIC_FILTER_GROUPS,
                    "columns": [{"key": key, "label": label} for key, label in EXPORT_COLUMNS],
                    "numbered_columns": get_numbered_export_columns(),
                    "library_filter_schema": get_library_filter_schema(),
                    "library_filter_coverage": get_library_filter_coverage(),
                    "library_capabilities": get_library_capabilities(),
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
            sample_verify = bool(payload.get("sample_data", False))
            if isinstance(payload.get("sample_data"), str):
                sample_verify = payload.get("sample_data", "").lower() in ("1", "true", "yes", "on")
            result = verify_run_details(
                run_id,
                sample_data=sample_verify,
                max_items=int(payload.get("max_items") or 20),
            )
            self._send_json(200, result)
            return

        sample_data = bool(payload.get("sample_data", False))
        if isinstance(payload.get("sample_data"), str):
            sample_data = payload.get("sample_data", "").lower() in ("1", "true", "yes", "on")
        collect_source = str(payload.get("collect_source") or "rpa").lower()

        if not sample_data:
            if not ALLOW_REAL_COLLECT:
                self._send_json(200, {
                    "success": False,
                    "markdown": "当前 Web 工作台未开启真实采集。请重新用默认真实模式启动，或仅在开发时切回样例数据模式。",
                    "data": {"run_id": "", "row_count": 0, "rows": []},
                })
                return
            ak_id, _ = get_ak_from_env()
            if collect_source == "api" and not ak_id:
                self._send_json(200, {
                    "success": False,
                    "markdown": "AK 未配置，当前 Web 工作台已阻止 API 真实采集。请先配置 AK，或改用真实页面 RPA 采集。",
                    "data": {"run_id": "", "row_count": 0, "rows": []},
                })
                return

        try:
            config = parse_input(
                categories=_csv_join(payload.get("categories")),
                tags=_csv_join(payload.get("tags")),
                keywords=_csv_join(payload.get("keywords")),
                source_urls=_csv_join(payload.get("source_urls")),
                exclude_tags=_csv_join(payload.get("exclude_tags")),
                max_queries=int(payload.get("max_queries") or 20),
                max_items_per_query=int(payload.get("max_items_per_query") or 20),
                sample_data=sample_data,
                output_format=str(payload.get("output_format") or "xlsx"),
                collect_source=collect_source,
                library_filters=payload.get("library_filters") or {},
            )
            result = run_tag_collect(config)
            data = dict(result["data"])
            data["rows"] = get_run_payload(data["run_id"]).get("rows", [])
            data["download_url"] = f"/download?run_id={data['run_id']}"
            self._send_json(200, {"success": result["success"], "markdown": result["markdown"], "data": data})
        except Exception as exc:
            error_state = collect_error_state(exc)
            self._send_json(200, {
                "success": False,
                "markdown": f"采集失败：{error_state['message']}",
                "data": {
                    "run_id": "",
                    "row_count": 0,
                    "rows": [],
                    "error_code": error_state["code"],
                    "action": error_state["action"],
                    "retryable": error_state["retryable"],
                    "suggestion": error_state["suggestion"],
                },
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


def serve_tag_collect_workbench(host: str = "127.0.0.1", port: int = 8765, allow_real: bool = True) -> None:
    global ALLOW_REAL_COLLECT, SERVER_TOKEN
    ALLOW_REAL_COLLECT = bool(allow_real and host in ("127.0.0.1", "localhost", "::1"))
    SERVER_TOKEN = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((host, port), TagCollectHandler)
    print(f"标签选品 Web 工作台已启动：http://{host}:{port}")
    if ALLOW_REAL_COLLECT:
        print("真实页面采集已开启；默认通过 Playwright/RPA 打开 1688 页面采集真实数据。")
    else:
        print("真实采集未开启；仅用于开发样例模式。")
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
  <title>1688选品库筛选工作台</title>
  <style>
    :root {
      --bg: #f3f5f8;
      --panel: #ffffff;
      --panel-solid: #ffffff;
      --ink: #1f2937;
      --muted: #667085;
      --muted-2: #98a2b3;
      --line: #d9dee8;
      --line-soft: #eef1f5;
      --accent: #1677ff;
      --accent-hover: #0f67dd;
      --accent-soft: #eaf3ff;
      --accent-deep: #0958d9;
      --orange: #ff7a1a;
      --orange-soft: #fff2e8;
      --green: #16a34a;
      --amber: #b76e00;
      --danger: #c2410c;
      --shadow: 0 2px 8px rgba(16, 24, 40, .06);
      --small-shadow: 0 1px 2px rgba(16, 24, 40, .06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Inter", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px;
      letter-spacing: 0;
    }
    header {
      background: #ffffff;
      border-bottom: 1px solid var(--line-soft);
      box-shadow: 0 1px 3px rgba(16, 24, 40, .04);
      padding: 9px 16px;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-row {
      align-items: center;
      display: flex;
      gap: 16px;
      justify-content: space-between;
      min-height: 38px;
    }
    h1 {
      color: #111827;
      font-size: 20px;
      font-weight: 760;
      line-height: 1.2;
      margin: 0;
    }
    main {
      display: grid;
      gap: 12px;
      grid-template-columns: 176px minmax(270px, 310px) minmax(0, 1fr);
      padding: 12px;
    }
    section, aside, .side-nav {
      background: var(--panel);
      border: 1px solid var(--line-soft);
      border-radius: 4px;
      box-shadow: var(--shadow);
      min-width: 0;
    }
    .side-nav {
      align-self: start;
      max-height: calc(100vh - 78px);
      overflow: auto;
      padding: 8px;
      position: sticky;
      top: 66px;
    }
    .nav-brand {
      border-bottom: 1px solid var(--line-soft);
      color: #111827;
      font-size: 13px;
      font-weight: 760;
      margin: 0 4px 8px;
      padding: 8px 6px 10px;
    }
    .nav-group-title {
      color: var(--muted-2);
      font-size: 11px;
      font-weight: 760;
      margin: 12px 8px 6px;
    }
    .nav-item {
      align-items: center;
      border-radius: 4px;
      color: #344054;
      display: flex;
      font-size: 13px;
      font-weight: 650;
      min-height: 34px;
      padding: 7px 10px;
      position: relative;
      text-decoration: none;
    }
    .nav-item:hover {
      background: #f7f9fc;
      color: var(--accent-deep);
    }
    .nav-item.active {
      background: var(--accent-soft);
      color: var(--accent-deep);
    }
    .nav-item.is-disabled {
      color: #98a2b3;
      cursor: not-allowed;
      opacity: .62;
      pointer-events: none;
    }
    .nav-item.is-disabled::after {
      background: #eef1f5;
      border-radius: 999px;
      color: #667085;
      content: "预留";
      font-size: 11px;
      font-weight: 720;
      margin-left: auto;
      padding: 2px 6px;
    }
    .nav-item.active::before {
      background: var(--accent);
      border-radius: 999px;
      content: "";
      height: 18px;
      left: 0;
      position: absolute;
      width: 3px;
    }
    aside {
      max-height: calc(100vh - 92px);
      overflow: auto;
      padding: 12px;
      position: sticky;
      top: 66px;
    }
    .workspace {
      display: grid;
      gap: 12px;
      min-width: 0;
    }
    .panel {
      padding: 14px;
    }
    .panel-title {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
      margin-bottom: 14px;
    }
    h2 {
      color: #111827;
      font-size: 15px;
      font-weight: 760;
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
      font-weight: 500;
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
      font-weight: 620;
      margin-bottom: 5px;
    }
    input[type="text"], input[type="number"], select, textarea {
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 4px;
      color: var(--ink);
      font: inherit;
      min-height: 34px;
      outline: none;
      padding: 7px 10px;
      width: 100%;
    }
    textarea {
      min-height: 64px;
      resize: vertical;
    }
    input:focus, select:focus, textarea:focus {
      border-color: rgba(22, 119, 255, .82);
      box-shadow: 0 0 0 2px rgba(22, 119, 255, .12);
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
      border-radius: 4px;
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      font-size: 13px;
      font-weight: 660;
      gap: 6px;
      justify-content: center;
      min-height: 34px;
      padding: 7px 13px;
      text-decoration: none;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease, color .18s ease, transform .18s ease;
      white-space: nowrap;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      box-shadow: 0 2px 5px rgba(22, 119, 255, .2);
    }
    button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
    button.secondary, .download {
      background: #ffffff;
      color: #344054;
    }
    button.ghost {
      background: #f7f9fc;
      border-color: var(--line-soft);
      color: var(--muted);
    }
    button.ghost::after {
      background: #eef1f5;
      border-radius: 999px;
      color: #667085;
      content: "预留";
      font-size: 11px;
      font-weight: 720;
      padding: 2px 6px;
    }
    button.secondary:hover, .download:hover {
      background: var(--panel-solid);
      border-color: #b9b9c0;
    }
    button:active, .download:active { transform: translateY(1px); }
    button:disabled {
      cursor: not-allowed;
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
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 4px;
      color: var(--ink);
      display: inline-flex;
      gap: 8px;
      margin: 0;
      min-height: 32px;
      padding: 6px 10px;
    }
    .sample-switch input {
      accent-color: var(--accent);
      height: 16px;
      width: 16px;
    }
    .tabs {
      align-items: center;
      background: #ffffff;
      border-bottom: 1px solid var(--line-soft);
      display: flex;
      gap: 0;
      padding: 0 12px;
    }
    .tab {
      background: transparent;
      border: 0;
      border-bottom: 2px solid transparent;
      border-radius: 0;
      color: var(--muted);
      min-height: 42px;
      padding: 10px 16px;
    }
    .tab.active {
      background: transparent;
      border-bottom-color: var(--accent);
      box-shadow: none;
      color: var(--accent-deep);
    }
    .tab-view { display: none; }
    .tab-view.active { display: block; }
    .category-cascade {
      display: grid;
      gap: 10px;
    }
    .category-dict-card {
      background: #ffffff;
      border: 1px solid #ebeef5;
      border-radius: 4px;
      display: grid;
      gap: 6px;
      padding: 12px 16px;
    }
    .category-dict-card strong {
      color: #1f2937;
      font-size: 14px;
      font-weight: 760;
    }
    .category-dict-card span {
      color: #667085;
      font-size: 12px;
    }
    .category-cascade-grid {
      background: #ffffff;
      border: 1px solid #ebeef5;
      border-radius: 4px;
      display: grid;
      grid-template-columns: minmax(190px, .9fr) minmax(220px, 1fr) minmax(260px, 1.25fr);
      min-height: 300px;
      overflow: hidden;
    }
    .category-column {
      background: #ffffff;
      border-right: 1px solid #ebeef5;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-width: 0;
    }
    .category-column:last-child {
      border-right: 0;
    }
    .category-column-head {
      align-items: center;
      background: #f5f7fa;
      border-bottom: 1px solid #ebeef5;
      color: #303133;
      display: flex;
      justify-content: space-between;
      min-height: 38px;
      padding: 9px 12px;
    }
    .category-column-head strong {
      font-size: 13px;
      font-weight: 760;
    }
    .category-column-head span {
      color: #909399;
      font-size: 12px;
      white-space: nowrap;
    }
    .category-column-list {
      max-height: 360px;
      overflow: auto;
      padding: 6px;
    }
    .category-row {
      align-items: stretch;
      border: 1px solid transparent;
      border-radius: 3px;
      display: grid;
      gap: 4px;
      grid-template-columns: 28px minmax(0, 1fr);
      margin-bottom: 4px;
      min-height: 36px;
      transition: background .16s ease, border-color .16s ease;
    }
    .category-row:hover {
      background: #f5f7fa;
    }
    .category-row.is-current {
      background: #ecf5ff;
      border-color: #c6e2ff;
    }
    .category-row.is-selected {
      border-color: rgba(64, 158, 255, .45);
    }
    .category-check {
      align-items: center;
      display: flex;
      justify-content: center;
      margin: 0;
      padding: 0;
    }
    .category-check input {
      accent-color: #409eff;
      height: 14px;
      width: 14px;
    }
    .category-row-button {
      align-items: center;
      background: transparent;
      border: 0;
      border-radius: 0;
      box-shadow: none;
      color: #303133;
      cursor: pointer;
      display: grid;
      gap: 4px;
      grid-template-columns: minmax(0, 1fr) auto;
      min-height: 34px;
      min-width: 0;
      padding: 6px 8px 6px 0;
      text-align: left;
      width: 100%;
    }
    .category-row-button:hover,
    .category-row-button:focus-visible {
      background: transparent;
      box-shadow: none;
      color: #409eff;
      outline: none;
    }
    .category-row-title {
      font-size: 13px;
      font-weight: 650;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .category-row-meta {
      color: #909399;
      font-size: 12px;
      white-space: nowrap;
    }
    .category-row.is-current .category-row-title,
    .category-row.is-selected .category-row-title {
      color: #409eff;
    }
    .category-empty {
      align-items: center;
      color: #909399;
      display: flex;
      font-size: 13px;
      justify-content: center;
      min-height: 160px;
      padding: 18px;
      text-align: center;
    }
    .category-selected-bar {
      align-items: flex-start;
      background: #ffffff;
      border: 1px solid #ebeef5;
      border-radius: 4px;
      display: flex;
      gap: 8px;
      padding: 8px 10px;
    }
    .category-selected-title {
      color: #606266;
      flex: 0 0 auto;
      font-size: 12px;
      font-weight: 700;
      line-height: 24px;
    }
    .category-selected-list {
      display: flex;
      flex: 1;
      flex-wrap: wrap;
      gap: 6px;
      min-width: 0;
    }
    .category-selected-chip {
      align-items: center;
      background: #ecf5ff;
      border: 1px solid #c6e2ff;
      border-radius: 3px;
      color: #409eff;
      display: inline-flex;
      font-size: 12px;
      gap: 5px;
      min-height: 24px;
      padding: 3px 7px;
    }
    .category-selected-chip button {
      background: transparent;
      border: 0;
      border-radius: 0;
      color: #409eff;
      font-size: 14px;
      min-height: 18px;
      min-width: 18px;
      padding: 0;
    }
    .category-empty-selection {
      color: #909399;
      font-size: 12px;
      line-height: 24px;
    }
    .tier-label {
      color: var(--muted-2);
      font-size: 11px;
      font-weight: 700;
    }
    .tag-grid {
      display: grid;
      gap: 6px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .chip {
      align-items: center;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 4px;
      color: #344054;
      display: flex;
      gap: 6px;
      font-size: 12px;
      min-height: 30px;
      margin: 0;
      padding: 5px 8px;
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
      border-color: rgba(22, 119, 255, .5);
      box-shadow: inset 0 0 0 1px rgba(22, 119, 255, .08);
      color: var(--accent-deep);
      font-weight: 700;
    }
    .mode-chip {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 4px;
      cursor: pointer;
      display: grid;
      gap: 3px;
      min-height: 52px;
      padding: 9px 10px;
      position: relative;
    }
    .mode-chip input {
      position: absolute;
      opacity: 0;
    }
    .mode-chip strong {
      color: var(--ink);
      font-size: 13px;
      line-height: 1.2;
    }
    .mode-chip span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
    }
    .mode-chip:has(input:checked) {
      background: var(--teal-soft);
      border-color: rgba(22, 119, 255, .5);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .library-toolbar {
      align-items: center;
      background: #f8fafc;
      border-bottom: 1px solid var(--line-soft);
      display: flex;
      justify-content: space-between;
      margin: -14px -14px 0;
      padding: 12px 14px;
    }
    .toolbar-meta {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }
    .status-pill {
      background: #f1f5f9;
      border: 1px solid #dbe3ec;
      border-radius: 999px;
      color: #475467;
      display: inline-flex;
      font-size: 12px;
      font-weight: 720;
      min-height: 28px;
      padding: 5px 9px;
    }
    .library-section {
      background: #ffffff;
      border-bottom: 1px solid var(--line-soft);
      border-left: 0;
      border-radius: 0;
      border-right: 0;
      border-top: 0;
      margin-top: 0;
      padding: 13px 0;
    }
    .precise-section {
      border-top: 0;
      padding-top: 14px;
    }
    .library-section-head {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
      margin-bottom: 10px;
      min-height: 24px;
    }
    .library-section-head strong {
      align-items: center;
      color: #111827;
      display: inline-flex;
      font-size: 14px;
      font-weight: 780;
      gap: 7px;
    }
    .library-section-head strong::before {
      background: var(--accent);
      border-radius: 999px;
      content: "";
      height: 14px;
      width: 3px;
    }
    .library-section-head span {
      color: var(--muted);
      font-size: 12px;
      text-align: right;
    }
    .library-grid {
      display: grid;
      gap: 8px;
    }
    .search-grid {
      grid-template-columns: minmax(220px, 1.5fr) minmax(130px, .7fr) minmax(180px, 1fr) minmax(160px, .9fr);
    }
    .url-grid {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      margin-top: 10px;
    }
    .run-grid {
      grid-template-columns: repeat(6, minmax(110px, 1fr));
    }
    .filter-grid-wide {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .range-grid {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
    }
    .range-field {
      background: #f8fafc;
      border: 1px solid var(--line-soft);
      border-radius: 4px;
      padding: 8px;
    }
    .range-pair {
      display: grid;
      gap: 6px;
      grid-template-columns: 1fr 1fr;
    }
    .field-status {
      border-radius: 999px;
      display: inline-flex;
      font-size: 10px;
      font-weight: 760;
      margin-left: 6px;
      padding: 2px 6px;
      vertical-align: middle;
    }
    .field-status.supported { background: #eaf7f0; color: var(--green); }
    .field-status.partial_supported { background: var(--orange-soft); color: var(--orange); }
    .field-status.detail_required { background: var(--accent-soft); color: var(--accent-deep); }
    .field-status.reserved { background: #f1f2f4; color: var(--muted); }
    .legacy-filter-box {
      border: 1px dashed var(--line);
      border-radius: 4px;
      margin-top: 12px;
      padding: 10px;
    }
    .legacy-filter-box summary {
      cursor: pointer;
      font-weight: 760;
    }
    .action-row {
      border-top: 1px solid var(--line-soft);
      margin-top: 10px;
      padding-top: 10px;
    }
    .filter-block {
      background: #f8fafc;
      border: 1px solid var(--line-soft);
      border-radius: 4px;
      margin-top: 0;
      padding: 8px;
    }
    .filter-block-head {
      align-items: baseline;
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
    }
    .filter-block-head strong {
      color: #111827;
      font-size: 12px;
      font-weight: 760;
    }
    .filter-block-head span {
      color: var(--muted);
      font-size: 12px;
    }
    .filter-block .tag-grid {
      grid-template-columns: repeat(4, minmax(84px, 1fr));
    }
    .summary {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-top: 12px;
    }
    .metric {
      background: var(--panel-solid);
      border: 1px solid var(--line-soft);
      border-radius: 4px;
      padding: 10px;
    }
    .metric span {
      color: var(--muted);
      display: block;
      font-size: 12px;
      margin-bottom: 4px;
    }
    .metric strong {
      font-size: 17px;
      font-weight: 760;
    }
    .notice {
      border-radius: 4px;
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
    .notice.paused {
      background: #fff7e6;
      border: 1px solid #ffd591;
      color: #ad6800;
    }
    .result-tools {
      align-items: end;
      display: grid;
      gap: 8px;
      grid-template-columns: minmax(140px, 1.3fr) repeat(4, minmax(112px, .8fr));
      margin-bottom: 12px;
    }
    .verification-strip {
      align-items: center;
      background: #f8fafc;
      border: 1px solid var(--line-soft);
      border-radius: 4px;
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(3, minmax(110px, 1fr)) auto;
      margin-bottom: 12px;
      padding: 10px;
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
      border-radius: 4px;
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
      background: #f8fafc;
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
      border-radius: 4px;
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
      border-radius: 4px;
      padding: 9px;
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
    .coverage-strip {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin: 10px 0 12px;
    }
    .coverage-pill {
      background: #f8fafc;
      border: 1px solid #ebeef5;
      border-radius: 4px;
      display: grid;
      gap: 2px;
      min-height: 48px;
      padding: 8px 10px;
    }
    .coverage-pill strong {
      color: #303133;
      font-size: 16px;
      font-weight: 760;
    }
    .coverage-pill span {
      color: #606266;
      font-size: 12px;
    }
    .coverage-list {
      border: 1px solid #ebeef5;
      border-radius: 4px;
      max-height: 180px;
      overflow: auto;
    }
    .coverage-row {
      align-items: center;
      border-bottom: 1px solid #f0f0f0;
      display: grid;
      gap: 8px;
      grid-template-columns: 112px 130px 92px minmax(0, 1fr);
      min-height: 34px;
      padding: 6px 10px;
    }
    .coverage-row:last-child {
      border-bottom: 0;
    }
    .coverage-row span {
      color: #606266;
      font-size: 12px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .coverage-row strong {
      color: #303133;
      font-size: 12px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .coverage-status {
      border-radius: 999px;
      display: inline-flex;
      font-size: 11px;
      font-weight: 720;
      justify-content: center;
      padding: 2px 7px;
    }
    .coverage-status.supported { background: #eaf7f0; color: #16a34a; }
    .coverage-status.partial_supported { background: #fff2e8; color: #ff7a1a; }
    .coverage-status.detail_required { background: #eaf3ff; color: #0958d9; }
    .coverage-status.reserved { background: #f1f2f4; color: #606266; }
    body {
      background: #f0f2f5;
    }
    .dl-topbar {
      background: #1a1a1a;
      border-bottom: 0;
      box-shadow: none;
      color: #fff;
      height: 46px;
      padding: 0 18px;
    }
    .dl-topbar .header-row {
      height: 46px;
      min-height: 46px;
    }
    .dl-logo {
      align-items: center;
      color: #fff;
      display: flex;
      font-size: 18px;
      font-weight: 780;
      gap: 8px;
    }
    .dl-logo-mark {
      background: #00c84b;
      border-radius: 4px;
      display: inline-block;
      height: 18px;
      width: 18px;
    }
    .dl-top-links {
      align-items: center;
      color: rgba(255,255,255,.78);
      display: flex;
      font-size: 13px;
      gap: 24px;
    }
    .dl-top-links .is-current {
      color: #fff;
      font-weight: 700;
    }
    .dl-top-links .is-reserved,
    .dl-top-actions .is-reserved,
    .help-links .is-reserved {
      color: rgba(255,255,255,.34);
      cursor: not-allowed;
      display: inline-flex;
      gap: 5px;
      pointer-events: none;
    }
    .dl-top-links .is-reserved::after,
    .dl-top-actions .is-reserved::after,
    .help-links .is-reserved::after {
      background: rgba(255,255,255,.1);
      border-radius: 999px;
      color: rgba(255,255,255,.5);
      content: "预留";
      font-size: 11px;
      font-weight: 720;
      padding: 1px 5px;
    }
    .dl-top-actions {
      align-items: center;
      display: flex;
      gap: 14px;
    }
    .dl-top-actions a {
      color: rgba(255,255,255,.82);
      text-decoration: none;
    }
    .dl-top-actions span {
      text-decoration: none;
    }
    .dl-shell {
      display: grid;
      gap: 0;
      grid-template-columns: 180px minmax(0, 1fr);
      min-height: calc(100vh - 46px);
      padding: 0;
    }
    .side-nav {
      background: #1a1a1a;
      border: 0;
      border-radius: 0;
      box-shadow: none;
      color: #fff;
      height: calc(100vh - 46px);
      max-height: none;
      overflow: auto;
      padding: 10px 0 42px;
      position: sticky;
      top: 46px;
    }
    .nav-brand {
      display: none;
    }
    .nav-group-title {
      color: #909399;
      font-size: 13px;
      font-weight: 650;
      margin: 14px 0 4px;
      padding: 0 20px;
    }
    .nav-item {
      border-radius: 0;
      color: rgba(255,255,255,.86);
      font-size: 13px;
      font-weight: 500;
      min-height: 32px;
      padding: 7px 24px 7px 28px;
    }
    .nav-item:hover {
      background: #262626;
      color: #fff;
    }
    .nav-item.active {
      background: #333333;
      color: #fff;
    }
    .nav-item.is-disabled {
      color: rgba(255,255,255,.34);
      cursor: not-allowed;
      opacity: 1;
      pointer-events: none;
    }
    .nav-item.is-disabled::after {
      background: rgba(255,255,255,.1);
      color: rgba(255,255,255,.48);
    }
    .nav-item.active::before {
      background: #00c84b;
      border-radius: 0;
      height: 32px;
      left: 0;
      top: 0;
      width: 4px;
    }
    .workspace {
      background: #f0f2f5;
      display: block;
      min-width: 0;
      padding: 23px 23px 32px;
    }
    section.library-page {
      background: transparent;
      border: 0;
      border-radius: 0;
      box-shadow: none;
    }
    .page-head {
      align-items: end;
      display: flex;
      height: 48px;
      justify-content: space-between;
    }
    .page-tab-title {
      align-items: center;
      background: #fff;
      border-radius: 6px 6px 0 0;
      color: #333;
      display: inline-flex;
      font-size: 20px;
      font-weight: 760;
      height: 48px;
      padding: 0 24px;
      position: relative;
    }
    .page-tab-title::after {
      background: #00c84b;
      bottom: 0;
      content: "";
      height: 3px;
      left: 0;
      position: absolute;
      right: 0;
    }
    .help-links {
      align-items: center;
      color: #666;
      display: flex;
      font-size: 13px;
      gap: 18px;
      height: 44px;
    }
    .help-links .is-reserved {
      color: #b0b4bc;
    }
    .help-links .is-reserved::after {
      background: #eef1f5;
      color: #909399;
    }
    .tabs {
      display: none;
    }
    .panel {
      padding: 0;
    }
    #task {
      background: #fff;
      border-radius: 0 0 6px 6px;
      padding: 18px 20px 0;
    }
    .filter-panel {
      background: #fff;
    }
    .library-toolbar {
      display: none;
    }
    .filter-row,
    .library-section {
      align-items: flex-start;
      background: #fff;
      border: 0;
      border-bottom: 1px solid #f0f0f0;
      display: grid;
      grid-template-columns: 104px minmax(0, 1fr);
      margin: 0;
      padding: 11px 0;
    }
    .filter-row.compact {
      align-items: center;
      min-height: 36px;
    }
    .row-label,
    .library-section-head {
      align-items: flex-start;
      color: #333;
      display: flex;
      font-size: 14px;
      font-weight: 700;
      gap: 0;
      justify-content: flex-start;
      margin: 0;
      min-height: 32px;
      padding-top: 6px;
      white-space: nowrap;
    }
    .row-label::after,
    .library-section-head strong::after {
      content: "：";
    }
    .library-section-head strong {
      color: #333;
      display: inline;
      font-size: 14px;
      font-weight: 700;
    }
    .library-section-head strong::before,
    .library-section-head span {
      display: none;
    }
    .row-control {
      min-width: 0;
    }
    .section-control {
      min-width: 0;
    }
    .top-filter-line {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      justify-content: space-between;
    }
    .category-inline,
    .template-inline {
      align-items: center;
      display: flex;
      gap: 8px;
      min-height: 32px;
    }
    .inline-label {
      color: #333;
      font-size: 14px;
      font-weight: 700;
      white-space: nowrap;
    }
    .category-picker {
      background: #fafafa;
      border: 1px solid #ebeef5;
      border-radius: 4px;
      margin-top: 10px;
      max-height: 340px;
      overflow: auto;
      padding: 10px;
    }
    .category-tools {
      display: grid;
      gap: 8px;
      grid-template-columns: minmax(180px, 280px) auto;
      margin-bottom: 8px;
    }
    .search-grid,
    .url-grid,
    .run-grid,
    .library-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .search-grid > div,
    .run-grid > div {
      min-width: 176px;
    }
    .url-grid > div {
      min-width: 280px;
      flex: 1 1 360px;
    }
    input[type="text"],
    input[type="number"],
    select,
    textarea {
      border-color: #dcdfe6;
      border-radius: 4px;
      min-height: 32px;
      padding: 6px 10px;
    }
    textarea {
      min-height: 58px;
    }
    label {
      color: #606266;
      font-weight: 500;
      margin-bottom: 4px;
    }
    .mode-chip,
    .chip {
      background: transparent;
      border: 0;
      border-radius: 0;
      color: #333;
      display: inline-flex;
      font-size: 14px;
      min-height: 28px;
      padding: 0 6px 0 0;
    }
    .mode-chip {
      border: 1px solid #dcdfe6;
      border-radius: 4px;
      gap: 4px;
      min-height: 32px;
      min-width: 96px;
      padding: 6px 10px;
    }
    .mode-chip strong {
      font-size: 14px;
      font-weight: 500;
    }
    .mode-chip span {
      display: none;
    }
    .mode-chip:has(input:checked),
    .chip:has(input:checked) {
      background: transparent;
      border-color: #409eff;
      box-shadow: none;
      color: #409eff;
      font-weight: 500;
    }
    .chip input,
    .mode-chip input {
      accent-color: #409eff;
      height: 14px;
      width: 14px;
    }
    .filter-block {
      background: transparent;
      border: 0;
      border-radius: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      padding: 0;
    }
    .filter-block-head {
      align-items: center;
      display: inline-flex;
      margin: 0 4px 0 0;
      min-height: 30px;
    }
    .filter-block-head strong {
      color: #606266;
      font-size: 13px;
      font-weight: 500;
    }
    .filter-block-head span {
      display: none;
    }
    .filter-block .tag-grid,
    .tag-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
    }
    .range-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
    }
    .range-field {
      align-items: center;
      background: transparent;
      border: 0;
      display: flex;
      gap: 8px;
      padding: 0;
    }
    .range-field label {
      color: #606266;
      font-size: 13px;
      margin: 0;
      white-space: nowrap;
    }
    .range-pair {
      align-items: center;
      display: flex;
      gap: 5px;
    }
    .range-pair::before {
      content: "";
    }
    .range-pair input {
      width: 86px;
    }
    .range-pair input + input {
      margin-left: 16px;
      position: relative;
    }
    .field-status {
      display: none;
    }
    .legacy-filter-box {
      margin: 12px 0;
    }
    .action-strip {
      align-items: center;
      border-bottom: 1px solid #f0f0f0;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
      padding: 14px 0 18px;
    }
    button.primary {
      background: #409eff;
      border-color: #409eff;
      box-shadow: none;
    }
    button.primary:hover {
      background: #66b1ff;
      border-color: #66b1ff;
    }
    button.secondary,
    .download {
      background: #fff;
      border-color: #dcdfe6;
      color: #606266;
    }
    .summary {
      margin: 14px 0 0;
    }
    #results {
      background: #fff;
      border-radius: 0 0 6px 6px;
      margin-top: 0;
      padding: 0 20px 20px;
    }
    .result-bar {
      align-items: center;
      border-bottom: 1px solid #ebeef5;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
      min-height: 54px;
      padding: 12px 0;
    }
    .result-actions {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .selected-count {
      color: #606266;
      font-size: 13px;
      font-weight: 500;
    }
    .verification-strip,
    .result-tools {
      background: transparent;
      border: 0;
      border-radius: 0;
      padding: 12px 0;
    }
    .table-wrap {
      border-color: #ebeef5;
      border-radius: 0;
      max-height: 620px;
    }
    th {
      background: #f5f7fa;
      color: #606266;
    }
    @media (max-width: 760px) {
      main { grid-template-columns: 1fr; }
      aside {
        max-height: 560px;
        position: static;
      }
      .side-nav {
        display: flex;
        gap: 6px;
        max-height: none;
        overflow-x: auto;
        padding: 8px;
        position: static;
      }
      .nav-brand, .nav-group-title {
        display: none;
      }
      .nav-item {
        border: 1px solid var(--line-soft);
        flex: 0 0 auto;
        min-height: 32px;
        white-space: nowrap;
      }
      .nav-item.active::before {
        display: none;
      }
      .result-tools, .summary, .grid.two, .field-grid, .verification-strip, .record-list,
      .search-grid, .url-grid, .run-grid, .range-grid {
        grid-template-columns: 1fr;
      }
      .category-cascade-grid {
        grid-template-columns: 1fr;
      }
      .category-column {
        border-bottom: 1px solid #ebeef5;
        border-right: 0;
      }
      .category-column:last-child {
        border-bottom: 0;
      }
      .category-column-list {
        max-height: 240px;
      }
      .filter-block .tag-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .library-toolbar, .library-section-head {
        align-items: flex-start;
        flex-direction: column;
      }
    }
    @media (max-width: 620px) {
      header { padding: 10px 12px; }
      main { padding: 12px; }
      .header-row { align-items: flex-start; flex-direction: column; }
      .tag-grid, .filter-block .tag-grid {
        grid-template-columns: 1fr;
      }
      .filter-grid-wide {
        grid-template-columns: 1fr;
      }
    }
    .dl-topbar {
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .dl-topbar .header-row {
      margin: 0 auto;
      max-width: none;
    }
    .dl-logo-mark {
      box-shadow: inset 0 -3px 0 rgba(0,0,0,.18);
    }
    .dl-shell {
      background: #f0f2f5;
    }
    .workspace {
      min-height: calc(100vh - 46px);
      overflow: auto;
    }
    .library-page #task.tab-view,
    .library-page #results.tab-view {
      display: block;
    }
    .library-page #fields.tab-view {
      display: none;
    }
    #task {
      border-bottom: 1px solid #ebeef5;
    }
    #results {
      border-top: 0;
    }
    .filter-panel {
      border: 1px solid #ebeef5;
      border-top: 0;
      border-radius: 0 0 4px 4px;
      overflow: hidden;
    }
    .filter-row,
    .library-section {
      padding-left: 0;
      padding-right: 0;
    }
    .row-label,
    .library-section-head {
      padding-left: 4px;
    }
    .row-control,
    .section-control,
    .library-section > .library-grid,
    .library-section > .range-grid {
      padding-right: 6px;
    }
    .top-filter-line {
      justify-content: flex-start;
      gap: 34px;
    }
    .template-inline input {
      width: 160px;
    }
    .category-summary {
      color: #606266;
      display: inline-flex;
      font-size: 13px;
      margin-left: 4px;
      max-width: 520px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .category-picker {
      display: none;
    }
    .category-picker.is-open {
      display: block;
    }
    .category-cascade {
      gap: 10px;
    }
    .category-dict-card {
      background: #fff;
      border-color: #ebeef5;
      border-radius: 4px;
      padding: 12px 16px;
    }
    .category-dict-card strong {
      color: #303133;
      font-size: 14px;
      font-weight: 700;
    }
    .category-dict-card span {
      color: #606266;
      font-size: 13px;
    }
    .category-cascade-grid {
      border-color: #ebeef5;
      grid-template-columns: minmax(200px, .9fr) minmax(240px, 1fr) minmax(300px, 1.25fr);
      min-height: 318px;
    }
    .category-column-head {
      background: #f5f7fa;
      min-height: 38px;
    }
    .category-column-head strong {
      font-size: 13px;
      font-weight: 700;
    }
    .category-column-list {
      max-height: 380px;
    }
    .category-row {
      grid-template-columns: 30px minmax(0, 1fr);
      margin-bottom: 5px;
      min-height: 38px;
    }
    .category-row-title {
      font-size: 14px;
      font-weight: 600;
    }
    .category-row-meta {
      font-size: 12px;
    }
    .category-row-button {
      min-height: 36px;
      padding: 6px 8px 6px 0;
    }
    .category-selected-bar {
      background: #fff;
      border-color: #ebeef5;
    }
    .library-grid.search-grid,
    .library-grid.url-grid,
    .library-grid.run-grid {
      align-items: end;
    }
    .search-grid > div,
    .run-grid > div {
      flex: 0 0 176px;
    }
    .search-grid > div:first-child {
      flex-basis: 280px;
    }
    .search-grid > div:nth-child(3) {
      flex-basis: 220px;
    }
    .mode-chip {
      background: #fff;
      color: #606266;
      justify-content: center;
      line-height: 1;
    }
    .mode-chip:has(input:checked) {
      background: #ecf5ff;
      border-color: #409eff;
      color: #409eff;
    }
    .mode-chip input {
      pointer-events: none;
    }
    .filter-block {
      align-items: center;
    }
    .filter-block-head {
      flex: 0 0 auto;
    }
    .filter-block + .filter-block {
      margin-left: 8px;
    }
    .range-pair {
      grid-template-columns: none;
    }
    .range-sep {
      color: #909399;
      font-size: 12px;
      line-height: 32px;
      padding: 0 1px;
    }
    .action-strip {
      justify-content: flex-start;
    }
    button.ghost::after {
      display: none;
    }
    .notice {
      border-radius: 4px;
      margin: 12px 0 0;
    }
    #results {
      border: 1px solid #ebeef5;
      border-top: 0;
    }
    .result-bar {
      background: #fff;
      margin: 0;
    }
    .result-actions select {
      min-width: 112px;
      width: auto;
    }
    .verification-strip {
      grid-template-columns: repeat(3, minmax(110px, 160px)) auto;
      justify-content: start;
    }
    th.col-_select,
    td.col-_select {
      text-align: center;
      width: 48px;
    }
    .row-select {
      accent-color: #409eff;
      height: 14px;
      width: 14px;
    }
    @media (max-width: 900px) {
      .page-head {
        height: auto;
      }
      .category-summary {
        max-width: 100%;
      }
    }
    header.dl-topbar {
      height: 46px;
      padding: 0 18px;
    }
    .dl-topbar .header-row {
      align-items: center;
      flex-direction: row;
      height: 46px;
      min-height: 46px;
    }
    .dl-top-links {
      display: flex;
    }
    main.dl-shell {
      display: grid;
      grid-template-columns: 180px minmax(1060px, 1fr);
      padding: 0;
    }
    .dl-shell .side-nav {
      display: block;
      height: calc(100vh - 46px);
      max-height: none;
      overflow: auto;
      padding: 10px 0 42px;
      position: sticky;
      top: 46px;
    }
    .side-nav .nav-group-title {
      display: block;
    }
    .side-nav .nav-item {
      border: 0;
      border-radius: 0;
      flex: initial;
      min-height: 32px;
      padding: 7px 24px 7px 28px;
      white-space: normal;
    }
    .side-nav .nav-item.active::before {
      display: block;
    }
    .library-section-head {
      align-items: flex-start;
      flex-direction: row;
    }
    html,
    body {
      min-width: 0;
      overflow-x: hidden;
    }
    header.dl-topbar {
      min-width: 0;
      width: 100%;
    }
    main.dl-shell {
      grid-template-columns: 180px minmax(0, 1fr);
      min-width: 0;
      width: 100%;
    }
    .dl-shell .workspace {
      overflow: auto;
    }
    .library-page {
      min-width: 0;
    }
    @media (max-width: 900px) {
      .dl-top-links {
        display: none;
      }
      .dl-top-actions {
        margin-left: auto;
      }
    }
    @media (max-width: 700px) {
      main.dl-shell {
        grid-template-columns: 150px minmax(0, 1fr);
      }
      .dl-shell .side-nav {
        width: 150px;
      }
      .side-nav .nav-group-title {
        padding: 0 16px;
      }
      .side-nav .nav-item {
        padding: 7px 16px 7px 22px;
      }
      .workspace {
        padding: 14px 10px 24px;
      }
      .page-tab-title {
        font-size: 18px;
        padding: 0 18px;
      }
      .help-links {
        display: none;
      }
      #task,
      #results {
        padding-left: 12px;
        padding-right: 12px;
      }
      .filter-row,
      .library-section {
        grid-template-columns: 92px minmax(0, 1fr);
      }
      .row-label,
      .library-section-head strong {
        font-size: 13px;
      }
      .top-filter-line {
        gap: 8px;
      }
      .category-inline,
      .template-inline,
      .search-grid,
      .url-grid,
      .run-grid,
      .library-grid,
      .range-grid,
      .filter-block .tag-grid,
      .tag-grid {
        gap: 8px;
      }
      .search-grid > div,
      .run-grid > div,
      .url-grid > div,
      .search-grid > div:first-child,
      .search-grid > div:nth-child(3) {
        flex: 1 1 170px;
        min-width: 0;
      }
      .range-field {
        flex-wrap: wrap;
      }
      .range-pair input {
        width: 72px;
      }
    }
  </style>
</head>
<body>
  <header class="dl-topbar">
    <div class="header-row">
      <div class="dl-logo">
        <span class="dl-logo-mark"></span>
        <span>店雷达</span>
      </div>
      <div class="dl-top-links">
        <span class="is-reserved" aria-disabled="true">首页</span>
        <span class="is-reserved" aria-disabled="true">图搜</span>
        <span class="is-reserved" aria-disabled="true">产品</span>
        <span class="is-current" aria-current="page">1688选品</span>
        <span class="is-reserved" aria-disabled="true">跨境选品</span>
        <span class="is-reserved" aria-disabled="true">选品监控</span>
      </div>
      <div class="dl-top-actions">
        <span class="is-reserved" aria-disabled="true">购买续费</span>
        <span class="is-reserved" aria-disabled="true">登录/注册</span>
      </div>
    </div>
  </header>

  <main class="dl-shell">
    <nav class="side-nav" aria-label="功能导航">
      <div class="nav-group-title">常用</div>
      <span class="nav-item is-disabled" aria-disabled="true">工作台</span>
      <span class="nav-item is-disabled" aria-disabled="true">铺货分销</span>
      <span class="nav-item is-disabled" aria-disabled="true">图搜</span>
      <div class="nav-group-title">1688</div>
      <a class="nav-item active" href="#">1688选品库</a>
      <span class="nav-item is-disabled" aria-disabled="true">1688AI新品</span>
      <span class="nav-item is-disabled" aria-disabled="true">1688商品榜</span>
      <span class="nav-item is-disabled" aria-disabled="true">个人选品池</span>
      <span class="nav-item is-disabled" aria-disabled="true">供应商货源库</span>
      <span class="nav-item is-disabled" aria-disabled="true">一手源头厂家</span>
      <div class="nav-group-title">我的选品</div>
      <span class="nav-item is-disabled" aria-disabled="true">监控搜索词</span>
      <span class="nav-item is-disabled" aria-disabled="true">黑名单</span>
      <div class="nav-group-title">监控</div>
      <span class="nav-item is-disabled" aria-disabled="true">我的监控</span>
      <span class="nav-item is-disabled" aria-disabled="true">店铺分析</span>
      <span class="nav-item is-disabled" aria-disabled="true">商品分析</span>
      <div class="nav-group-title">系统</div>
      <span class="nav-item is-disabled" aria-disabled="true">会员</span>
      <span class="nav-item is-disabled" aria-disabled="true">子账号</span>
      <span class="nav-item is-disabled" aria-disabled="true">反馈</span>
    </nav>

    <div class="workspace">
      <section class="library-page">
        <div class="page-head">
          <div class="page-tab-title">1688选品库</div>
          <div class="help-links">
            <span class="is-reserved" aria-disabled="true">使用帮助</span>
            <span class="is-reserved" aria-disabled="true">新手教程</span>
            <span class="is-reserved" aria-disabled="true">在线翻译</span>
          </div>
        </div>
        <div class="tabs">
          <button class="tab active" type="button" data-tab="task">筛选任务</button>
          <button class="tab" type="button" data-tab="results">结果表格</button>
          <button class="tab" type="button" data-tab="fields">字段编号</button>
        </div>

        <div id="task" class="tab-view active panel">
          <div class="filter-panel">
          <div class="library-toolbar">
            <div>
              <h2>筛选条件</h2>
              <div class="subtle-count">类目范围、精准搜索、选品模式、高级筛选、销售、商品、卖家信息</div>
            </div>
            <div class="toolbar-meta">
              <span id="capabilityBadge" class="status-pill">能力加载中</span>
            </div>
          </div>

          <div class="filter-row compact">
            <div class="row-label">类目范围</div>
            <div class="row-control">
              <div class="top-filter-line">
                <div class="category-inline">
                  <button id="toggleCategoryPanel" class="secondary" type="button">选择类目</button>
                  <button id="clearCategories" class="secondary" type="button">清空</button>
                  <span id="categorySummary" class="category-summary">全部类目</span>
                </div>
                <div class="template-inline">
                  <span class="inline-label">我的模板：</span>
                  <input id="templateName" type="text" placeholder="无数据" />
                  <button id="saveTemplateBtn" class="secondary ghost" type="button" disabled title="模板管理接口预留">管理</button>
                </div>
              </div>
              <div id="categoryPanel" class="category-picker">
                <div class="category-tools">
                  <input id="categorySearch" type="text" placeholder="搜索类目" />
                  <span class="status-pill">可复选一级 / 二级 / 三级</span>
                </div>
                <div id="categoryTree"></div>
              </div>
            </div>
          </div>

          <div class="library-section precise-section">
            <div class="library-section-head">
              <strong>精准搜索</strong>
              <span>商品关键词 / 模糊匹配 / 历史搜索 / URL</span>
            </div>
            <div class="section-control">
              <div class="library-grid search-grid">
                <div>
                  <label for="keywords">商品关键词</label>
                  <input id="keywords" type="text" placeholder="例如 连衣裙, 防晒衣, 收纳盒" />
                </div>
                <div>
                  <label for="matchType">匹配方式</label>
                  <select id="matchType">
                    <option value="模糊匹配">模糊匹配</option>
                    <option value="精准匹配">精准匹配</option>
                  </select>
                </div>
                <div>
                  <label for="historyKeyword">历史搜索</label>
                  <input id="historyKeyword" type="text" placeholder="请输入历史关键词" />
                </div>
                <div>
                  <label for="sampleMode">数据模式</label>
                  <label class="sample-switch">
                    <input id="sampleMode" type="checkbox" />
                    开发样例
                  </label>
                </div>
              </div>
              <div class="library-grid url-grid">
                <div>
                  <label for="sourceUrls">1688 页面 URL</label>
                  <textarea id="sourceUrls" placeholder="可粘贴 1688 搜索页或商品详情页链接，用逗号分隔；填写后优先按 URL 采集真实页面"></textarea>
                </div>
                <div>
                  <label for="excludeTags">排除标签</label>
                  <textarea id="excludeTags" placeholder="屏蔽多个商品关键词，顿号隔开"></textarea>
                </div>
              </div>
            </div>
          </div>

          <div id="libraryFilters"></div>

          <details class="legacy-filter-box">
            <summary>兼容旧版运营标签</summary>
            <div id="filterGroups"></div>
          </details>

          <div class="library-section run-section">
            <div class="library-section-head">
              <strong>批量与导出</strong>
              <span>当前支持导出和详情核验，关注商品/Temu 铺货为预留接口</span>
            </div>
            <div class="library-grid run-grid">
              <div>
                <label for="outputFormat">导出格式</label>
                <select id="outputFormat">
                  <option value="xlsx">XLSX</option>
                  <option value="csv">CSV</option>
                </select>
              </div>
              <div>
                <label for="collectSource">真实采集来源</label>
                <select id="collectSource">
                  <option value="rpa">1688 页面 RPA</option>
                  <option value="api">1688 AK/API</option>
                </select>
              </div>
              <div>
                <label for="statPeriod">统计周期</label>
                <select id="statPeriod" data-library-select="stat_period">
                  <option value="近30天">近30天</option>
                  <option value="近7天">近7天</option>
                  <option value="近90天">近90天</option>
                </select>
              </div>
              <div>
                <label for="sortBy">排序</label>
                <select id="sortBy" data-library-select="sort_by">
                  <option value="推荐分">推荐分</option>
                  <option value="销售订单数">销售订单数</option>
                  <option value="销售件数">销售件数</option>
                  <option value="销售额">销售额</option>
                  <option value="复购率">复购率</option>
                  <option value="批发价">批发价</option>
                </select>
              </div>
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

          <div class="action-strip">
            <button id="runBtn" class="primary" type="button">开始查询</button>
            <button id="resetBtn" class="secondary" type="button">重置筛选</button>
            <button id="saveFilterBtn" class="secondary ghost" type="button" disabled title="筛选模板接口预留">保存筛选</button>
          </div>

          <div id="notice" class="notice"></div>
          <div class="verification-records">
            <div class="panel-title">
              <div>
                <h2>筛选计划与执行记录</h2>
                <div class="subtle-count">原生筛选必须在 1688 页面点击；找不到会显示 not_found</div>
              </div>
            </div>
            <div id="coverageSummary" class="coverage-strip"></div>
            <div id="coverageList" class="coverage-list"></div>
            <div id="filterRecordList" class="record-list"></div>
          </div>
          <div class="summary">
            <div class="metric"><span>采集批次</span><strong id="runId">-</strong></div>
            <div class="metric"><span>商品数</span><strong id="rowCount">0</strong></div>
            <div class="metric"><span>P0/P1</span><strong id="highCount">0</strong></div>
            <div class="metric"><span>可铺/谨慎</span><strong id="suggestCount">0</strong></div>
          </div>
          </div>
        </div>

        <div id="results" class="tab-view panel">
          <div class="result-bar">
            <div class="result-actions">
              <label class="chip"><input id="selectAllRows" type="checkbox" />全选</label>
              <span id="selectedCount" class="selected-count">已选: 0</span>
              <button id="followBtn" class="secondary ghost" type="button" disabled>关注商品</button>
              <a id="downloadLink" class="download" href="#" hidden>导出</a>
              <button id="temuBtn" class="secondary ghost" type="button" disabled>铺货Temu</button>
              <button id="dryRunBtn" class="secondary ghost" type="button" disabled>铺货 dry-run</button>
            </div>
            <div class="result-actions">
              <span class="selected-count">统计周期</span>
              <select id="periodView">
                <option>近30天</option>
                <option>近7天</option>
                <option>近90天</option>
              </select>
            </div>
          </div>
          <div class="verification-strip">
            <div><span>待核验高潜</span><strong id="queueCount">0</strong></div>
            <div><span>已核验商品</span><strong id="verifiedCount">0</strong></div>
            <div><span>核验记录</span><strong id="recordCount">0</strong></div>
            <button id="verifyBtn" class="secondary" type="button" disabled>真实详情核验</button>
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
                <option value="partial_verified">partial_verified</option>
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
      filterReevaluationRecords: [],
      filterPlan: {},
      filterResults: [],
      filterWarnings: [],
      selectedCategories: new Set(),
      activeCategoryParent: "",
      activeCategoryChild: "",
      selectedTags: new Set(),
      selectedLibrary: {},
      selectedRows: new Set(),
      tableColumns: [
        ["_select", ""],
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

    function showNotice(text, ok, mode = "") {
      const node = $("notice");
      node.textContent = text;
      node.className = `notice show ${mode || (ok ? "ok" : "fail")}`;
    }

    function updateCategorySummary() {
      const selected = [...state.selectedCategories];
      const summary = $("categorySummary");
      if (!summary) return;
      summary.textContent = selected.length ? `已选 ${selected.length} 个：${selected.slice(0, 4).join("、")}${selected.length > 4 ? "..." : ""}` : "全部类目";
    }

    function rowKey(row, index) {
      return String(row.item_id || row.url || `${row.title || "row"}-${index}`);
    }

    function updateSelectedCount() {
      const node = $("selectedCount");
      if (node) node.textContent = `已选: ${state.selectedRows.size}`;
      const selectAll = $("selectAllRows");
      if (!selectAll) return;
      const visibleKeys = filteredRows().map((row, index) => rowKey(row, index));
      const checked = visibleKeys.length > 0 && visibleKeys.every(key => state.selectedRows.has(key));
      selectAll.checked = checked;
      selectAll.indeterminate = !checked && visibleKeys.some(key => state.selectedRows.has(key));
    }

    function categoryEntries(parent) {
      const children = (state.options.category_tree || {})[parent];
      return Array.isArray(children)
        ? children.map(child => [child, []])
        : Object.entries(children || {}).map(([child, grandchildren]) => [child, Array.isArray(grandchildren) ? grandchildren : []]);
    }

    function categorySelectedCount(parent, child = "") {
      const prefix = child ? `${parent}>${child}` : parent;
      return [...state.selectedCategories].filter(value => value === prefix || value.startsWith(`${prefix}>`)).length;
    }

    function ensureActiveCategory() {
      const tree = state.options.category_tree || {};
      const parents = Object.keys(tree);
      if (!parents.length) {
        state.activeCategoryParent = "";
        state.activeCategoryChild = "";
        return;
      }
      if (!state.activeCategoryParent || !tree[state.activeCategoryParent]) {
        const firstSelected = [...state.selectedCategories][0] || "";
        const selectedParent = firstSelected.split(">")[0];
        state.activeCategoryParent = tree[selectedParent] ? selectedParent : parents[0];
      }
      const children = categoryEntries(state.activeCategoryParent);
      const childNames = children.map(([child]) => child);
      if (!state.activeCategoryChild || !childNames.includes(state.activeCategoryChild)) {
        const firstSelectedChild = [...state.selectedCategories]
          .map(value => value.split(">"))
          .find(parts => parts[0] === state.activeCategoryParent && parts[1]);
        state.activeCategoryChild = firstSelectedChild && childNames.includes(firstSelectedChild[1])
          ? firstSelectedChild[1]
          : (childNames[0] || "");
      }
    }

    function categoryRowHtml({value, label, kind, meta = "", current = false, selected = false, checked = false, path = ""}) {
      return `
        <div class="category-row ${current ? "is-current" : ""} ${selected ? "is-selected" : ""}">
          <label class="category-check" title="复选${esc(label)}">
            <input type="checkbox" data-kind="category" value="${esc(value)}" ${checked ? "checked" : ""} />
          </label>
          <button class="category-row-button" type="button" data-category-nav="${esc(kind)}" data-category-path="${esc(path || value)}" title="${esc(path || value)}">
            <span class="category-row-title">${esc(label)}</span>
            ${meta ? `<span class="category-row-meta">${esc(meta)}</span>` : ""}
          </button>
        </div>
      `;
    }

    function renderCategories() {
      const wrap = $("categoryTree");
      const q = $("categorySearch").value.trim().toLowerCase();
      const tree = state.options.category_tree || {};
      const dict = state.options.category_dictionary || {};
      ensureActiveCategory();

      const parents = Object.keys(tree);
      const allMatches = [];
      for (const parent of parents) {
        const entries = categoryEntries(parent);
        const parentMatches = !q || parent.toLowerCase().includes(q);
        const matchedChildren = [];
        for (const [child, grandchildren] of entries) {
          const childMatches = !q || `${parent} ${child}`.toLowerCase().includes(q);
          const matchedGrandchildren = grandchildren.filter(grand => !q || `${parent} ${child} ${grand}`.toLowerCase().includes(q));
          if (parentMatches || childMatches || matchedGrandchildren.length) {
            matchedChildren.push([child, q && !parentMatches && !childMatches ? matchedGrandchildren : grandchildren]);
          }
        }
        if (parentMatches || matchedChildren.length) {
          allMatches.push([parent, matchedChildren.length ? matchedChildren : entries]);
        }
      }

      const visibleParents = q ? allMatches.map(([parent]) => parent) : parents;
      if (q && visibleParents.length && !visibleParents.includes(state.activeCategoryParent)) {
        state.activeCategoryParent = visibleParents[0];
        state.activeCategoryChild = "";
      }
      const activeEntries = q
        ? (allMatches.find(([parent]) => parent === state.activeCategoryParent)?.[1] || [])
        : categoryEntries(state.activeCategoryParent);
      if (!state.activeCategoryChild || !activeEntries.some(([child]) => child === state.activeCategoryChild)) {
        state.activeCategoryChild = activeEntries[0]?.[0] || "";
      }
      const activeGrandchildren = activeEntries.find(([child]) => child === state.activeCategoryChild)?.[1] || [];

      const parentHtml = visibleParents.map(parent => {
        const entries = categoryEntries(parent);
        const selectedCount = categorySelectedCount(parent);
        return categoryRowHtml({
          value: parent,
          label: parent,
          kind: "parent",
          path: parent,
          meta: selectedCount ? `已选 ${selectedCount}` : `${entries.length} 个二级`,
          current: parent === state.activeCategoryParent,
          selected: state.selectedCategories.has(parent) || selectedCount > 0,
          checked: state.selectedCategories.has(parent)
        });
      }).join("");

      const childHtml = activeEntries.map(([child, grandchildren]) => {
        const value = `${state.activeCategoryParent}>${child}`;
        const selectedCount = categorySelectedCount(state.activeCategoryParent, child);
        return categoryRowHtml({
          value,
          label: child,
          kind: "child",
          path: value,
          meta: selectedCount ? `已选 ${selectedCount}` : `${grandchildren.length} 个三级`,
          current: child === state.activeCategoryChild,
          selected: state.selectedCategories.has(value) || selectedCount > 0,
          checked: state.selectedCategories.has(value)
        });
      }).join("");

      const grandHtml = activeGrandchildren.map(grand => {
        const value = `${state.activeCategoryParent}>${state.activeCategoryChild}>${grand}`;
        return categoryRowHtml({
          value,
          label: grand,
          kind: "grand",
          path: value,
          meta: "完整路径",
          selected: state.selectedCategories.has(value),
          checked: state.selectedCategories.has(value)
        });
      }).join("");

      const selectedHtml = [...state.selectedCategories].map(value => `
        <span class="category-selected-chip" title="${esc(value)}">
          ${esc(value)}
          <button type="button" data-category-remove="${esc(value)}" aria-label="移除 ${esc(value)}">×</button>
        </span>
      `).join("");

      wrap.innerHTML = `
        <div class="category-cascade">
          <div class="category-dict-card">
            <strong>类目字典 ${esc(dict.version || "-")}</strong>
            <span>${esc(dict.source || "-")} · ${esc(dict.status || "-")}</span>
          </div>
          <div class="category-cascade-grid">
            <div class="category-column">
              <div class="category-column-head"><strong>一级类目</strong><span>${visibleParents.length}/${parents.length}</span></div>
              <div class="category-column-list">${parentHtml || `<div class="category-empty">没有匹配的一级类目</div>`}</div>
            </div>
            <div class="category-column">
              <div class="category-column-head"><strong>二级类目</strong><span>${esc(state.activeCategoryParent || "请选择一级")}</span></div>
              <div class="category-column-list">${childHtml || `<div class="category-empty">先选择左侧一级类目</div>`}</div>
            </div>
            <div class="category-column">
              <div class="category-column-head"><strong>三级类目</strong><span>${esc(state.activeCategoryChild || "请选择二级")}</span></div>
              <div class="category-column-list">${grandHtml || `<div class="category-empty">当前二级暂无三级类目</div>`}</div>
            </div>
          </div>
          <div class="category-selected-bar">
            <div class="category-selected-title">已选类目</div>
            <div class="category-selected-list">${selectedHtml || `<span class="category-empty-selection">未选择时默认全部类目</span>`}</div>
          </div>
        </div>
      `;
      updateCategorySummary();
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

    function statusLabel(status) {
      return {
        supported: "已接入",
        partial_supported: "部分接入",
        detail_required: "需核验",
        reserved: "预留"
      }[status] || status || "预留";
    }

    function statusBadge(status) {
      return `<span class="field-status ${esc(status || "reserved")}">${esc(statusLabel(status))}</span>`;
    }

    function renderLibraryFilters() {
      const schema = state.options.library_filter_schema || [];
      const sections = schema.filter(section => !["scope", "precise_search", "batch_export"].includes(section.key));
      $("libraryFilters").innerHTML = sections.map(section => `
        <div class="library-section" data-library-section="${esc(section.key)}">
          <div class="library-section-head">
            <strong>${esc(section.title)}</strong>
            <span>${esc(section.description || "")}</span>
          </div>
          <div class="section-control">
            ${renderLibrarySectionFields(section)}
          </div>
        </div>
      `).join("");
    }

    function renderLibrarySectionFields(section) {
      if (section.key === "selection_mode") {
        const field = (section.fields || [])[0] || {};
        const modeCopy = {
          "新品热卖": "新品、热度、订单",
          "无货源选品": "一件代发、包邮权益",
          "同期热卖": "近30天表现、趋势",
          "源头工厂": "工厂/超级工厂"
        };
        return `<div class="library-grid filter-grid-wide">${(field.options || []).map(option => `
          <label class="mode-chip">
            <input type="checkbox" data-library-list="${esc(field.key)}" value="${esc(option)}" />
            <strong>${esc(option)} ${statusBadge(field.status)}</strong>
            <span>${esc(modeCopy[option] || field.mapping || "")}</span>
          </label>
        `).join("")}</div>`;
      }
      const fields = section.fields || [];
      const rangeFields = fields.filter(field => field.type === "range");
      const otherFields = fields.filter(field => field.type !== "range");
      const rangeHtml = rangeFields.length ? `
        <div class="range-grid">
          ${rangeFields.map(field => `
            <div class="range-field">
              <label>${esc(field.label)} ${statusBadge(field.status)}</label>
              <div class="range-pair">
                <input type="number" data-library-range="${esc(field.key)}" data-bound="min" placeholder="最小" />
                <span class="range-sep">至</span>
                <input type="number" data-library-range="${esc(field.key)}" data-bound="max" placeholder="最大" />
              </div>
            </div>
          `).join("")}
        </div>
      ` : "";
      const otherHtml = otherFields.length ? `
        <div class="library-grid filter-grid-wide" style="${rangeFields.length ? "margin-top:10px;" : ""}">
          ${otherFields.map(renderLibraryField).join("")}
        </div>
      ` : "";
      return rangeHtml + otherHtml;
    }

    function renderLibraryField(field) {
      if (field.type === "multi_chip") {
        return `<div class="filter-block" style="margin-top:0;">
          <div class="filter-block-head"><strong>${esc(field.label)} ${statusBadge(field.status)}</strong><span>${esc(field.mapping || "")}</span></div>
          <div class="tag-grid">${(field.options || []).map(option => `
            <label class="chip"><input type="checkbox" data-library-list="${esc(field.key)}" value="${esc(option)}" />${esc(option)}</label>
          `).join("")}</div>
        </div>`;
      }
      if (field.type === "radio") {
        return `<div class="filter-block" style="margin-top:0;">
          <div class="filter-block-head"><strong>${esc(field.label)} ${statusBadge(field.status)}</strong><span>${esc(field.mapping || "")}</span></div>
          <div class="tag-grid">${(field.options || []).map((option, index) => `
            <label class="chip"><input type="radio" name="library-${esc(field.key)}" data-library-radio="${esc(field.key)}" value="${esc(option)}" ${index === 0 ? "checked" : ""} />${esc(option)}</label>
          `).join("")}</div>
        </div>`;
      }
      if (field.type === "boolean") {
        return `<label class="chip"><input type="checkbox" data-library-bool="${esc(field.key)}" />${esc(field.label)} ${statusBadge(field.status)}</label>`;
      }
      if (field.type === "select") {
        return `<div>
          <label>${esc(field.label)} ${statusBadge(field.status)}</label>
          <select data-library-select="${esc(field.key)}">
            <option value="">不限</option>
            ${(field.options || []).map(option => `<option value="${esc(option)}">${esc(option)}</option>`).join("")}
          </select>
        </div>`;
      }
      return `<div>
        <label>${esc(field.label)} ${statusBadge(field.status)}</label>
        <input type="text" data-library-text="${esc(field.key)}" />
      </div>`;
    }

    function collectLibraryFilters() {
      const filters = {};
      filters.category_paths = [...state.selectedCategories];
      filters.search_keyword = $("keywords").value;
      filters.match_type = $("matchType").value;
      filters.history_keyword = $("historyKeyword").value;
      filters.template_name = $("templateName").value;
      filters.source_urls = $("sourceUrls").value;
      document.querySelectorAll("[data-library-list]").forEach(input => {
        if (!input.checked) return;
        const key = input.dataset.libraryList;
        filters[key] = filters[key] || [];
        filters[key].push(input.value);
      });
      document.querySelectorAll("[data-library-bool]").forEach(input => {
        if (input.checked) filters[input.dataset.libraryBool] = true;
      });
      document.querySelectorAll("[data-library-radio]").forEach(input => {
        if (input.checked) filters[input.dataset.libraryRadio] = input.value;
      });
      document.querySelectorAll("[data-library-select]").forEach(input => {
        if (input.value) filters[input.dataset.librarySelect] = input.value;
      });
      document.querySelectorAll("[data-library-text]").forEach(input => {
        if (input.value.trim()) filters[input.dataset.libraryText] = input.value.trim();
      });
      document.querySelectorAll("[data-library-range]").forEach(input => {
        if (!input.value) return;
        const key = `${input.dataset.libraryRange}_${input.dataset.bound}`;
        filters[key] = input.value;
      });
      state.selectedLibrary = filters;
      return filters;
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

    function renderFilterCoverage() {
      const coverage = state.options.library_filter_coverage || [];
      const order = [
        ["supported", "已接入"],
        ["partial_supported", "部分接入"],
        ["detail_required", "需详情核验"],
        ["reserved", "预留"]
      ];
      const counts = coverage.reduce((acc, item) => {
        acc[item.status] = (acc[item.status] || 0) + 1;
        return acc;
      }, {});
      $("coverageSummary").innerHTML = order.map(([key, label]) => `
        <div class="coverage-pill">
          <strong>${counts[key] || 0}</strong>
          <span>${label}</span>
        </div>
      `).join("");
      $("coverageList").innerHTML = coverage.map(item => `
        <div class="coverage-row">
          <span>${esc(item.section_title)}</span>
          <strong>${esc(item.label)}</strong>
          <span class="coverage-status ${esc(item.status)}">${esc(item.status_label)}</span>
          <span title="${esc(item.message || "")}">${esc(item.mapping || "-")} · ${esc(item.message || "")}</span>
        </div>
      `).join("");
    }

    function renderTable() {
      $("resultHead").innerHTML = `<tr>${state.tableColumns.map(([key, label]) => `<th class="${tableCellClass(key)}">${esc(label)}</th>`).join("")}</tr>`;
      const rows = filteredRows();
      $("resultBody").innerHTML = rows.map((row, index) => `
        <tr>
          ${state.tableColumns.map(([key]) => `<td class="${tableCellClass(key)}">${formatCell(key, row[key], row, index)}</td>`).join("")}
        </tr>
      `).join("") || `<tr><td colspan="${state.tableColumns.length}">暂无数据</td></tr>`;
      updateSelectedCount();
    }

    function tableCellClass(key) {
      return `col-${String(key).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    }

    function formatCell(key, value, row, index) {
      if (key === "_select") {
        const keyValue = rowKey(row, index);
        return `<input class="row-select" type="checkbox" data-row-key="${esc(keyValue)}" ${state.selectedRows.has(keyValue) ? "checked" : ""} />`;
      }
      if (key === "url" && value) return `<a href="${esc(value)}" target="_blank" rel="noreferrer">打开</a>`;
      if (key === "recommendation_level") {
        const cls = value === "P0" ? "p0" : value === "P1" ? "p1" : value === "P2" ? "p2" : "no";
        return `<span class="badge ${cls}">${esc(value || "-")}</span>`;
      }
      if (key === "verification_status") {
        const cls = value === "sample_verified" || value === "verified" ? "p0" : value === "partial_verified" ? "p1" : value === "failed" ? "no" : "p2";
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

    function clearRunState() {
      state.rows = [];
      state.runId = "";
      state.verificationQueue = [];
      state.verificationRecords = [];
      state.filterReevaluationRecords = [];
      state.filterPlan = {};
      state.filterResults = [];
      state.filterWarnings = [];
      state.selectedRows.clear();
      $("downloadLink").hidden = true;
      $("downloadLink").removeAttribute("href");
      renderTable();
      updateSummary({run_id: "", verification_queue: [], verification_records: []});
    }

    function updateSummary(data) {
      state.runId = data.run_id || state.runId || "";
      state.verificationQueue = data.verification_queue || state.verificationQueue || [];
      state.verificationRecords = data.verification_records || state.verificationRecords || [];
      state.filterReevaluationRecords = data.filter_reevaluation_records || state.filterReevaluationRecords || [];
      state.filterPlan = data.filter_plan || state.filterPlan || {};
      state.filterResults = data.filter_results || state.filterResults || [];
      state.filterWarnings = data.filter_warnings || state.filterWarnings || [];
      $("runId").textContent = data.run_id || "-";
      $("rowCount").textContent = String(state.rows.length);
      $("highCount").textContent = String(state.rows.filter(row => ["P0", "P1"].includes(row.recommendation_level)).length);
      $("suggestCount").textContent = String(state.rows.filter(row => ["可铺", "谨慎"].includes(row.wechat_shop_suggestion)).length);
      $("queueCount").textContent = String(state.verificationQueue.length);
      $("verifiedCount").textContent = String(state.rows.filter(row => ["sample_verified", "verified", "partial_verified"].includes(row.verification_status)).length);
      $("recordCount").textContent = String(state.verificationRecords.length);
      $("verifyBtn").disabled = !state.runId || state.verificationQueue.length === 0;
      updateSelectedCount();
      renderRecords();
      renderFilterRecords();
    }

    function renderFilterRecords() {
      const planned = [
        ...(state.filterPlan.native_filters || []).map(item => ({...item, plan_type: "1688原生筛选"})),
        ...(state.filterPlan.post_filters || []).map(item => ({...item, plan_type: "指标区间"})),
        ...(state.filterPlan.system_rules || []).map(item => ({...item, plan_type: "系统规则"})),
        ...(state.filterPlan.library_filter_results || []).map(item => ({...item, plan_type: "店雷达字段"})),
        ...(state.filterPlan.library_reserved_fields || []).map(item => ({...item, plan_type: "预留字段"}))
      ];
      const execution = state.filterResults || [];
      const reevaluated = (state.filterReevaluationRecords || []).map(record => ({
        title: `详情重评估 · ${record.field_label || record.field_key || "-"}`,
        line1: `商品：${record.item_id || "-"} · 状态：${record.status || "-"}`,
        line2: `期望：${record.expected || "-"} · 实际：${record.raw || "-"} · ${record.message || ""}`
      }));
      const rows = execution.length
        ? execution.map(record => ({
            title: `${record.label || record.tag || record.filter_key || "-"} · ${record.status || "-"}`,
            line1: `来源：${record.source || "-"} · ${record.query || "-"}`,
            line2: record.message || record.matched_text || "-"
          }))
        : planned.map(record => ({
            title: `${record.plan_type} · ${record.label || record.tag || record.key || "-"}`,
            line1: `字段：${record.field || record.field_key || record.key || "-"} · 状态：${record.status || "planned"}`,
            line2: record.message || record.bucket || record.value || record.type || "待执行"
          }));
      $("filterRecordList").innerHTML = [...reevaluated, ...rows].slice(0, 24).map(record => `
        <div class="record-item">
          <strong>${esc(record.title)}</strong>
          <span>${esc(record.line1)}</span>
          <span>${esc(record.line2)}</span>
        </div>
      `).join("") || `<div class="record-item"><strong>暂无筛选计划</strong><span>选择标签后运行采集，会展示搜索词/原生筛选/指标区间拆分。</span></div>`;
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
      `).join("") || `<div class="record-item"><strong>暂无核验记录</strong><span>采集后点击“真实详情核验”生成字段级记录。</span></div>`;
    }

    async function runCollect() {
      $("runBtn").disabled = true;
      $("runBtn").textContent = "采集中";
      try {
        const payload = {
          categories: [...state.selectedCategories],
          tags: [...state.selectedTags],
          keywords: $("keywords").value,
          source_urls: $("sourceUrls").value,
          exclude_tags: $("excludeTags").value,
          library_filters: collectLibraryFilters(),
          max_queries: Number($("maxQueries").value || 20),
          max_items_per_query: Number($("maxItems").value || 20),
          output_format: $("outputFormat").value,
          collect_source: $("collectSource").value,
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
          clearRunState();
          if (result.data && result.data.error_code === "security_verification_required") {
            showNotice(`${result.markdown || "1688 风控已暂停采集"} ${result.data.suggestion || "请人工处理后再继续。"}`, false, "paused");
          } else {
            showNotice(result.markdown || "采集失败", false);
          }
          return;
        }
        state.selectedRows.clear();
        state.rows = result.data.rows || [];
        state.runId = result.data.run_id || "";
        state.verificationQueue = result.data.verification_queue || [];
        state.verificationRecords = result.data.verification_records || [];
        state.filterPlan = result.data.filter_plan || {};
        state.filterResults = result.data.filter_results || [];
        state.filterWarnings = result.data.filter_warnings || [];
        renderTable();
        updateSummary(result.data);
        $("downloadLink").href = result.data.download_url;
        $("downloadLink").hidden = false;
        const modeText = $("sampleMode").checked ? "开发样例" : `真实数据/${$("collectSource").value === "rpa" ? "1688页面RPA" : "AK/API"}`;
        const queryLabel = $("sourceUrls").value.trim() ? "采集页面" : "查询词";
        const warningText = (state.filterWarnings || []).map(item => `${item.label || item.tag || item.filter_key}:${item.status}`).join("；");
        const nativeText = ((state.filterPlan.native_filters || []).map(item => item.label || item.tag).filter(Boolean)).join("，");
        showNotice(`已生成 ${result.data.row_count} 条初筛商品（${modeText}）；${queryLabel}：${(result.data.queries || []).join("，")}。原生筛选：${nativeText || "无"}。${warningText ? `筛选提示：${warningText}。` : ""}运费、品退率、发货率等关键字段仍需详情页核验。`, (state.filterWarnings || []).length === 0);
      } catch (err) {
        showNotice(`采集失败：${err.message}`, false);
      } finally {
        $("runBtn").disabled = false;
        $("runBtn").textContent = "开始查询";
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
        showNotice(`已核验 ${result.data.verified_count} 个高潜商品，导出表已刷新；partial_verified 表示真实页面只提取到部分字段。`, true);
      } catch (err) {
        showNotice(`核验失败：${err.message}`, false);
      } finally {
        $("verifyBtn").textContent = $("sampleMode").checked ? "样例核验高潜" : "真实详情核验";
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
      if (target.dataset.kind === "category") {
        const parts = target.value.split(">");
        if (parts[0]) state.activeCategoryParent = parts[0];
        if (parts[1]) state.activeCategoryChild = parts[1];
      }
      target.dataset.kind === "category" ? renderCategories() : renderFilterGroups();
    });

    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!target.matches(".row-select")) return;
      target.checked ? state.selectedRows.add(target.dataset.rowKey) : state.selectedRows.delete(target.dataset.rowKey);
      updateSelectedCount();
    });

    document.addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) setTab(tab.dataset.tab);
    });

    document.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-category-remove]");
      if (remove) {
        state.selectedCategories.delete(remove.dataset.categoryRemove);
        renderCategories();
        return;
      }
      const nav = event.target.closest("[data-category-nav]");
      if (!nav) return;
      const parts = (nav.dataset.categoryPath || "").split(">");
      if (nav.dataset.categoryNav === "parent" && parts[0]) {
        state.activeCategoryParent = parts[0];
        state.activeCategoryChild = "";
      }
      if ((nav.dataset.categoryNav === "child" || nav.dataset.categoryNav === "grand") && parts[0]) {
        state.activeCategoryParent = parts[0];
        state.activeCategoryChild = parts[1] || "";
      }
      renderCategories();
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
    $("toggleCategoryPanel").addEventListener("click", () => {
      const panel = $("categoryPanel");
      panel.classList.toggle("is-open");
      $("toggleCategoryPanel").textContent = panel.classList.contains("is-open") ? "收起类目" : "选择类目";
    });
    $("clearCategories").addEventListener("click", () => {
      state.selectedCategories.clear();
      state.activeCategoryParent = "";
      state.activeCategoryChild = "";
      renderCategories();
    });
    $("resetBtn").addEventListener("click", () => {
      state.selectedCategories.clear();
      state.activeCategoryParent = "";
      state.activeCategoryChild = "";
      state.selectedTags.clear();
      state.selectedLibrary = {};
      state.selectedRows.clear();
      ["keywords", "sourceUrls", "excludeTags", "historyKeyword", "templateName", "titleFilter"].forEach(id => {
        if ($(id)) $(id).value = "";
      });
      ["minScore"].forEach(id => {
        if ($(id)) $(id).value = "0";
      });
      document.querySelectorAll("[data-library-list], [data-library-bool]").forEach(input => input.checked = false);
      document.querySelectorAll("[data-library-range], [data-library-text]").forEach(input => input.value = "");
      document.querySelectorAll("[data-library-radio]").forEach(input => {
        input.checked = input.value === "不限";
      });
      renderCategories();
      renderFilterGroups();
      renderTable();
      showNotice("筛选条件已重置。", true);
    });
    $("selectAllRows").addEventListener("change", (event) => {
      filteredRows().forEach((row, index) => {
        const key = rowKey(row, index);
        event.target.checked ? state.selectedRows.add(key) : state.selectedRows.delete(key);
      });
      renderTable();
    });
    $("periodView").addEventListener("change", (event) => {
      $("statPeriod").value = event.target.value;
      showNotice(`统计周期已切换为 ${event.target.value}。当前版本会记录到筛选参数，真实采集仍以 1688 页面可获取字段为准。`, true);
    });
    $("statPeriod").addEventListener("change", (event) => {
      $("periodView").value = event.target.value;
    });
    ["saveTemplateBtn", "saveFilterBtn"].forEach(id => {
      $(id).addEventListener("click", () => {
        showNotice("该功能接口已预留，当前版本不会伪造执行结果。", false);
      });
    });
    $("runBtn").addEventListener("click", runCollect);
    $("verifyBtn").addEventListener("click", runVerify);

    async function init() {
      const response = await fetch("/api/options");
      const result = await response.json();
      state.options = result.data;
      const caps = state.options.library_capabilities || {};
      $("capabilityBadge").textContent = `已接入 ${((caps.implemented || []).length)} 项 / 预留 ${((caps.reserved || []).length)} 项`;
      $("maxQueries").max = state.options.limits.max_queries;
      $("maxItems").max = state.options.limits.max_items_per_query;
      $("sampleMode").checked = false;
      $("sampleMode").addEventListener("change", () => {
        $("verifyBtn").textContent = $("sampleMode").checked ? "样例核验高潜" : "真实详情核验";
        showNotice(
          $("sampleMode").checked
            ? "当前切换到开发样例模式，不会采集真实 1688 数据。正式测试请关闭开发样例。"
            : "当前为真实数据模式：默认通过 1688 页面 RPA 采集；如果账号登录不上，可粘贴 1688 搜索页/商品详情页 URL 先做公开页面真实数据测试。",
          true
        );
      });
      if (!state.options.allow_real_collect) {
        $("sampleMode").checked = true;
        showNotice("当前真实采集被关闭，仅可用于开发样例模式。正式测试请用默认真实模式启动本地服务。", false);
      } else {
        showNotice("当前为真实数据模式：将打开 1688 页面采集真实数据；如账号登录不上，可粘贴浏览器里能打开的 1688 搜索页/商品详情页 URL 测试。", true);
      }
      renderCategories();
      renderLibraryFilters();
      renderFilterGroups();
      renderFilterCoverage();
      renderFields();
      renderTable();
      updateSummary({});
    }

    init();
  </script>
</body>
</html>
"""
