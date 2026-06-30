#!/usr/bin/env python3
"""标签选品采集 Web 工作台 — 本地筛选测试页面"""

import json
import mimetypes
import os
import secrets
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.error import URLError
from urllib.request import urlopen
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
    save_error_payload,
    verify_run_details,
)


SERVER_TOKEN = ""
ALLOW_REAL_COLLECT = True
DEFAULT_CDP_PORT = int(os.environ.get("TAG_COLLECT_CDP_PORT") or 9222)
DEFAULT_CDP_URL = f"http://127.0.0.1:{DEFAULT_CDP_PORT}"


def _tag_collect_dir() -> Path:
    return Path(__file__).resolve().parent


def _chrome_runtime_status() -> Dict[str, Any]:
    cdp_url = os.environ.get("TAG_COLLECT_CDP_URL", "").strip() or DEFAULT_CDP_URL
    os.environ.setdefault("TAG_COLLECT_CDP_URL", cdp_url)
    status = {
        "mode": "chrome_cdp",
        "use_cdp": True,
        "cdp_url": cdp_url,
        "cdp_connected": False,
        "cdp_ready": False,
        "cdp_page_count": 0,
        "page_count": 0,
        "candidate_count": 0,
        "pages": [],
        "current_page": {},
        "label": "采集浏览器未连接",
        "message": "点击“启动采集浏览器”，然后在弹出的 Chrome 中手动打开/筛选 1688 页面。",
    }
    parsed = urlparse(cdp_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9222
    try:
        with socket.create_connection((host, port), timeout=0.4):
            status["cdp_connected"] = True
        with urlopen(f"http://{host}:{port}/json/version", timeout=0.8) as response:
            version_payload = json.loads(response.read().decode("utf-8") or "{}")
        with urlopen(f"http://{host}:{port}/json/list", timeout=0.8) as response:
            pages_payload = json.loads(response.read().decode("utf-8") or "[]")
        pages = pages_payload if isinstance(pages_payload, list) else []
        page_items = []
        for item in pages:
            if not isinstance(item, dict):
                continue
            page_url = str(item.get("url") or "")
            page_title = str(item.get("title") or "")
            is_1688 = _is_1688_url(page_url)
            is_blocked = _is_blocked_1688_url(page_url) if is_1688 else False
            is_collectable = _is_collectable_1688_url(page_url)
            page_items.append({
                "id": str(item.get("id") or ""),
                "title": page_title,
                "url": page_url,
                "type": str(item.get("type") or ""),
                "is_1688": is_1688,
                "is_blocked": is_blocked,
                "is_collectable": is_collectable,
            })
        candidate_pages = [item for item in page_items if item.get("is_collectable")]
        browser_name = str(version_payload.get("Browser") or "")
        status["cdp_page_count"] = len(pages)
        status["page_count"] = len(pages)
        status["candidate_count"] = len(candidate_pages)
        status["pages"] = page_items[:20]
        status["current_page"] = candidate_pages[0] if len(candidate_pages) == 1 else {}
        status["cdp_ready"] = bool(version_payload.get("webSocketDebuggerUrl"))
        status["label"] = "真实 Chrome CDP"
        if status["cdp_ready"]:
            if len(candidate_pages) == 1:
                page = candidate_pages[0]
                title = page.get("title") or "当前 1688 页面"
                status["message"] = f"已连接采集 Chrome，检测到 1 个可读取的 1688 页签：{title}。"
            elif len(candidate_pages) > 1:
                status["message"] = f"已连接采集 Chrome，但检测到 {len(candidate_pages)} 个可读取的 1688 页签。请只保留要采集的那个页签，或把该页 URL 粘贴到可选 URL 框用于匹配。"
            elif pages:
                status["message"] = "已连接采集 Chrome。自动批量采集可直接按本页类目/筛选执行；只有使用“当前页兜底读取”时，才需要先在 Chrome 中打开商品列表/详情页。"
            else:
                status["message"] = f"采集 Chrome 调试端口已打开（{browser_name or 'Chrome'}）；请先在弹出的 Chrome 中登录 1688，然后回到本页开始自动采集。"
        else:
            status["message"] = "9222 端口已打开，但不是完整的 Chrome CDP 调试端点；请重启项目专用 Chrome。"
    except OSError:
        status["message"] = "采集浏览器未启动；请点击“启动采集浏览器”。"
    except Exception as exc:
        status["message"] = f"已配置真实 Chrome CDP，但调试端点健康检查失败：{exc}。请重启项目专用 Chrome。"
    return status


def _start_chrome_debug_browser() -> Dict[str, Any]:
    script = _tag_collect_dir() / "start_chrome_debug.sh"
    if not script.exists():
        raise RuntimeError(f"未找到采集浏览器启动脚本：{script}")
    env = os.environ.copy()
    env.setdefault("TAG_COLLECT_CDP_PORT", str(DEFAULT_CDP_PORT))
    os.environ["TAG_COLLECT_CDP_URL"] = DEFAULT_CDP_URL
    subprocess.Popen(
        [str(script)],
        cwd=str(_tag_collect_dir()),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    status = _chrome_runtime_status()
    deadline = time.time() + 8
    while time.time() < deadline and not status.get("cdp_ready"):
        time.sleep(0.5)
        status = _chrome_runtime_status()
    if not status.get("cdp_ready"):
        raise RuntimeError(status.get("message") or "采集浏览器已尝试启动，但 CDP 调试端点未就绪。")
    return status


def _is_1688_url(page_url: str) -> bool:
    parsed = urlparse(page_url)
    host = parsed.hostname or ""
    return host == "1688.com" or host.endswith(".1688.com")


def _is_blocked_1688_url(page_url: str) -> bool:
    lowered = page_url.lower()
    return any(term in lowered for term in ("login", "punish", "captcha", "verify", "sec"))


def _is_collectable_1688_url(page_url: str) -> bool:
    if not _is_1688_url(page_url) or _is_blocked_1688_url(page_url):
        return False
    parsed = urlparse(page_url)
    host = parsed.hostname or ""
    path = parsed.path or ""
    query = parsed.query or ""
    if "detail.1688.com" in host or "detail.m.1688.com" in host:
        return True
    if "s.1688.com" in host and ("selloffer" in path or "offer_search" in path):
        return True
    if "offer" in path and ("offerId" in query or "keywords" in query):
        return True
    if any(key in query for key in ("keywords=", "keyword=", "offerId=", "offerIds=", "categoryId=", "catId=")):
        return True
    return False


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
                    "runtime": _chrome_runtime_status(),
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
        if parsed.path == "/api/chrome/status":
            self._send_json(200, {
                "success": True,
                "markdown": "",
                "data": {"runtime": _chrome_runtime_status()},
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
        if parsed.path not in ("/api/collect", "/api/verify", "/api/chrome/start"):
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

        if parsed.path == "/api/chrome/start":
            try:
                runtime = _start_chrome_debug_browser()
                self._send_json(200, {
                    "success": True,
                    "markdown": runtime.get("message", ""),
                    "data": {"runtime": runtime},
                })
            except Exception as exc:
                self._send_json(200, {
                    "success": False,
                    "markdown": f"启动采集浏览器失败：{exc}",
                    "data": {"runtime": _chrome_runtime_status()},
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
        auto_verify_details = bool(payload.get("auto_verify_details", False))
        if isinstance(payload.get("auto_verify_details"), str):
            auto_verify_details = payload.get("auto_verify_details", "").lower() in ("1", "true", "yes", "on")
        if collect_source in ("url_direct", "direct_url", "http_url") and not sample_data:
            auto_verify_details = False

        if not sample_data:
            if not ALLOW_REAL_COLLECT:
                self._send_json(200, {
                    "success": False,
                    "markdown": "当前 Web 工作台未开启真实采集。正式测试请在运行服务的本机打开 127.0.0.1 后再执行真实采集。",
                    "data": {"run_id": "", "row_count": 0, "rows": []},
                })
                return
            ak_id, _ = get_ak_from_env()
            if collect_source == "api" and not ak_id:
                self._send_json(200, {
                    "success": False,
                    "markdown": "AK 未配置，当前 Web 工作台已阻止 API 真实采集。请先配置 AK，或改用人工页面读取。",
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
                target_publishable_count=int(payload.get("target_publishable_count") or payload.get("max_items_per_query") or 20),
                sample_data=sample_data,
                output_format=str(payload.get("output_format") or "xlsx"),
                collect_source=collect_source,
                library_filters=payload.get("library_filters") or {},
                auto_verify_details=auto_verify_details,
                auto_verify_max_items=int(payload.get("auto_verify_max_items") or 0),
            )
            result = run_tag_collect(config)
            data = dict(result["data"])
            data["rows"] = get_run_payload(data["run_id"]).get("rows", [])
            data["download_url"] = f"/download?run_id={data['run_id']}"
            self._send_json(200, {"success": result["success"], "markdown": result["markdown"], "data": data})
        except Exception as exc:
            error_state = collect_error_state(exc)
            detail = getattr(exc, "data", {}) or {}
            filter_results = error_state.get("filter_results") or detail.get("filter_results") or []
            if not isinstance(filter_results, list):
                filter_results = []
            error_snapshot_path = save_error_payload({
                "request": {
                    "categories": payload.get("categories") or [],
                    "tags": payload.get("tags") or [],
                    "keywords": payload.get("keywords") or "",
                    "source_urls": payload.get("source_urls") or "",
                    "collect_source": collect_source,
                    "sample_data": sample_data,
                    "max_items_per_query": payload.get("max_items_per_query"),
                    "library_filters": payload.get("library_filters") or {},
                },
                "error_state": error_state,
                "error_data": detail,
            })
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
                    "runtime": error_state.get("runtime", _chrome_runtime_status()),
                    "filter_results": filter_results,
                    "diagnostics": error_state.get("diagnostics") or detail.get("diagnostics") or {},
                    "category_path": error_state.get("category_path") or detail.get("category_path") or "",
                    "error_snapshot_path": error_snapshot_path,
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
        runtime = _chrome_runtime_status()
        print(f"真实页面采集已开启；当前浏览器模式：{runtime['label']}。{runtime['message']}")
    else:
        print("真实采集未开启；当前环境只允许查看页面，不执行真实采集。")
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
    .collect-mode-panel {
      background: #f8fbff;
      border: 1px solid #cfe3ff;
      border-radius: 4px;
      display: grid;
      gap: 10px;
      margin: 0 0 12px;
      padding: 12px;
    }
    .collect-mode-head {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
    }
    .collect-mode-head strong {
      color: #111827;
      font-size: 14px;
      font-weight: 780;
    }
    .collect-mode-head span {
      color: var(--muted);
      font-size: 12px;
      text-align: right;
    }
    .collect-mode-options {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .collect-mode-card {
      background: #ffffff;
      border: 1px solid #dbe3ec;
      border-radius: 4px;
      cursor: pointer;
      display: grid;
      gap: 5px;
      margin: 0;
      min-height: 86px;
      padding: 10px 12px;
      position: relative;
      transition: border-color .16s ease, box-shadow .16s ease, background .16s ease;
    }
    .collect-mode-card input {
      position: absolute;
      opacity: 0;
    }
    .collect-mode-card strong {
      color: #111827;
      font-size: 13px;
      font-weight: 780;
      line-height: 1.25;
    }
    .collect-mode-card span {
      color: #667085;
      font-size: 12px;
      font-weight: 500;
      line-height: 1.35;
    }
    .collect-mode-card em {
      color: #98a2b3;
      font-size: 11px;
      font-style: normal;
      line-height: 1.3;
    }
    .collect-mode-card:has(input:checked) {
      background: #eef6ff;
      border-color: #1677ff;
      box-shadow: inset 3px 0 0 #1677ff, 0 2px 6px rgba(22, 119, 255, .12);
    }
    .collect-mode-card.is-risk:has(input:checked) {
      background: #fff7e6;
      border-color: #faad14;
      box-shadow: inset 3px 0 0 #faad14, 0 2px 6px rgba(250, 173, 20, .12);
    }
    .collect-mode-status {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .collect-mode-status .status-pill {
      background: #ffffff;
    }
    .browser-assist-box {
      background: #ffffff;
      border: 1px solid #b7d7ff;
      border-radius: 4px;
      display: grid;
      gap: 10px;
      padding: 11px;
    }
    .browser-assist-title {
      align-items: center;
      display: flex;
      gap: 8px;
      justify-content: space-between;
    }
    .browser-assist-title strong {
      color: #0958d9;
      font-size: 13px;
      font-weight: 780;
    }
    .browser-assist-actions {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .browser-assist-steps {
      color: #344054;
      display: grid;
      gap: 5px;
      font-size: 12px;
      line-height: 1.45;
    }
    .browser-page-list {
      display: grid;
      gap: 6px;
    }
    .browser-page-item {
      background: #f8fafc;
      border: 1px solid #e4e9f2;
      border-radius: 4px;
      color: #475467;
      display: grid;
      gap: 3px;
      padding: 8px;
    }
    .browser-page-item strong {
      color: #111827;
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .browser-page-item span {
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .collect-mode-hint {
      color: #344054;
      font-size: 12px;
      line-height: 1.45;
    }
    .collect-url-box {
      background: #ffffff;
      border: 1px solid #b7d7ff;
      border-radius: 4px;
      display: grid;
      gap: 6px;
      padding: 10px;
    }
    .collect-url-box label {
      align-items: center;
      color: #0958d9;
      display: flex;
      font-size: 13px;
      font-weight: 780;
      justify-content: space-between;
      margin: 0;
    }
    .collect-url-box label span {
      color: #667085;
      font-size: 12px;
      font-weight: 560;
    }
    .collect-url-steps {
      background: #f0f7ff;
      border: 1px solid #d6e8ff;
      border-radius: 4px;
      color: #344054;
      font-size: 12px;
      font-weight: 620;
      line-height: 1.5;
      padding: 8px 10px;
    }
    .collect-url-box textarea {
      border-color: #91caff;
      min-height: 72px;
    }
    .collect-url-box textarea:focus {
      border-color: #1677ff;
      box-shadow: 0 0 0 2px rgba(22, 119, 255, .14);
    }
    .collect-url-box.is-attention {
      animation: urlAttention 1.4s ease 1;
      border-color: #ff9f1a;
      box-shadow: 0 0 0 3px rgba(255, 159, 26, .12);
    }
    @keyframes urlAttention {
      0%, 100% { transform: translateY(0); }
      35% { transform: translateY(-2px); }
      70% { transform: translateY(1px); }
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
      grid-template-columns: repeat(8, minmax(110px, 1fr));
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
      grid-template-columns: repeat(4, minmax(110px, 1fr)) auto;
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
    .automation-message {
      background: #f8fafc;
      border: 1px solid var(--line-soft);
      border-radius: 4px;
      color: var(--muted);
      display: none;
      font-size: 13px;
      line-height: 1.5;
      margin: -4px 0 12px;
      padding: 9px 10px;
    }
    .automation-message.show { display: block; }
    .automation-message.paused {
      background: #fff7e6;
      border-color: #ffd591;
      color: #ad6800;
    }
    .automation-message.partial {
      background: #fff2e8;
      border-color: #ffd6ba;
      color: #b45309;
    }
    .automation-message.failed {
      background: #fff0f0;
      border-color: #ffc9c9;
      color: var(--danger);
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
      min-width: 2060px;
      table-layout: fixed;
      width: 100%;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
      max-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
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
    th.col-_select, td.col-_select { width: 46px; }
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
    th.col-verified_at, td.col-verified_at { width: 150px; }
    th.col-risk_flags, td.col-risk_flags { width: 210px; }
    th.col-wechat_shop_suggestion, td.col-wechat_shop_suggestion { width: 170px; }
    td.col-category_path, td.col-title, td.col-risk_flags,
    td.col-wholesale_shipping_fee, td.col-dropship_shipping_fee,
    td.col-product_refund_rate, td.col-shipment_rate {
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    td.col-wholesale_shipping_fee,
    td.col-dropship_shipping_fee,
    td.col-product_refund_rate,
    td.col-shipment_rate {
      line-height: 1.45;
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
    /* Workbench acceptance overrides: clearer flow, denser filters, safer defaults. */
    body {
      padding-bottom: 82px;
    }
    .task-flow-card {
      background: #ffffff;
      border: 1px solid #dfe6ef;
      border-radius: 6px;
      box-shadow: 0 10px 28px rgba(15, 23, 42, .06);
      margin: 0 0 12px;
      padding: 12px 14px;
      position: sticky;
      top: 58px;
      z-index: 35;
    }
    .task-steps {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      margin-bottom: 10px;
    }
    .task-step {
      align-items: center;
      background: #f7f9fc;
      border: 1px solid #e5ebf3;
      border-radius: 4px;
      color: #606266;
      display: flex;
      font-size: 12px;
      font-weight: 700;
      justify-content: center;
      min-height: 30px;
      padding: 0 8px;
      text-align: center;
      white-space: nowrap;
    }
    .task-step.is-active {
      background: #ecfdf3;
      border-color: #95d9b0;
      color: #087b35;
    }
    .selected-summary {
      background: #f8fafc;
      border: 1px solid #e7edf5;
      border-radius: 4px;
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      padding: 10px;
    }
    .selected-summary-item {
      border-right: 1px solid #e2e8f0;
      min-width: 0;
      padding-right: 10px;
    }
    .selected-summary-item:last-child {
      border-right: 0;
    }
    .selected-summary-item span {
      color: #7a8491;
      display: block;
      font-size: 12px;
      line-height: 1.2;
    }
    .selected-summary-item strong {
      color: #1f2937;
      display: block;
      font-size: 16px;
      line-height: 1.25;
      margin-top: 2px;
    }
    .selected-summary-item em {
      color: #606266;
      display: block;
      font-size: 12px;
      font-style: normal;
      margin-top: 3px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .filter-anchor-bar {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: space-between;
      margin-top: 10px;
    }
    .filter-anchor-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .filter-anchor-actions button {
      background: #ffffff;
      border: 1px solid #dcdfe6;
      border-radius: 4px;
      color: #3f4854;
      font-size: 12px;
      min-height: 28px;
      padding: 0 10px;
    }
    .filter-anchor-actions button:hover {
      border-color: #409eff;
      color: #1677d2;
    }
    .review-mode-toggle {
      align-items: center;
      background: #ffffff;
      border: 1px solid #e4e7ed;
      border-radius: 4px;
      color: #303133;
      display: inline-flex;
      font-size: 12px;
      gap: 7px;
      margin: 0;
      min-height: 28px;
      padding: 0 10px;
      white-space: nowrap;
    }
    .review-mode-toggle {
      display: none;
    }
    body.review-mode .review-mode-toggle {
      display: inline-flex;
    }
    .review-mode-toggle input {
      accent-color: #409eff;
      margin: 0;
    }
    .review-mode-toggle span {
      color: #909399;
    }
    .detail-warning {
      background: #fff7ed;
      border: 1px solid #fed7aa;
      border-radius: 4px;
      color: #9a3412;
      font-size: 13px;
      line-height: 1.5;
      margin-top: 10px;
      padding: 8px 10px;
    }
    .detail-warning[hidden] {
      display: none;
    }
    #task {
      padding-top: 14px;
    }
    .filter-panel {
      border-radius: 6px;
    }
    .filter-row,
    .library-section {
      grid-template-columns: 132px minmax(0, 1fr);
      padding: 14px 0;
    }
    .row-label,
    .library-section-head {
      border-left: 3px solid transparent;
      color: #1f2937;
      font-size: 14px;
      min-height: 34px;
      padding-left: 10px;
      padding-top: 7px;
    }
    .filter-row:hover .row-label,
    .library-section:hover .library-section-head {
      border-left-color: #00c84b;
    }
    .section-control,
    .row-control {
      padding-right: 12px;
    }
    .library-filter-card {
      background: #fbfcfe;
      border: 1px solid #e7edf5;
      border-radius: 4px;
      min-height: 36px;
      padding: 8px 10px;
    }
    .filter-block.library-filter-card {
      align-items: flex-start;
      gap: 8px 12px;
    }
    .range-field.library-filter-card {
      align-items: flex-start;
      flex-direction: column;
      gap: 6px;
    }
    .range-field.library-filter-card label {
      align-items: center;
      color: #303133;
      display: flex;
      font-weight: 700;
      gap: 6px;
    }
    .boolean-field.library-filter-card,
    .text-field.library-filter-card,
    .select-field.library-filter-card {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .boolean-field .chip {
      padding-right: 0;
    }
    .filter-block-head strong {
      align-items: center;
      color: #303133;
      display: flex;
      font-weight: 700;
      gap: 6px;
    }
    .filter-block-head .mapping-label {
      background: #edf2f7;
      border-radius: 999px;
      color: #64748b;
      display: none;
      font-size: 11px;
      margin-left: 4px;
      padding: 2px 7px;
    }
    body.review-mode .filter-block-head .mapping-label {
      display: inline-flex;
    }
    .field-hint {
      color: #7a8491;
      display: none;
      flex: 1 0 100%;
      font-size: 12px;
      line-height: 1.45;
    }
    body.review-mode .field-hint {
      display: block;
    }
    .is-detail-filter {
      background: #fffaf0;
      border-color: #f6c56f;
    }
    .is-partial-filter {
      background: #f7fbff;
      border-color: #b7dcff;
    }
    .is-category-mismatch {
      background: #f8fafc !important;
      border-color: #e5e7eb !important;
      opacity: .56;
    }
    .category-scope-label {
      color: #64748b;
      font-size: 11px;
      margin-left: 6px;
      white-space: nowrap;
    }
    .is-reserved-filter {
      display: none !important;
    }
    body.review-mode .is-reserved-filter {
      display: flex !important;
      opacity: .72;
    }
    body.review-mode .field-status,
    .field-status.detail_required,
    .field-status.partial_supported {
      align-items: center;
      border-radius: 999px;
      display: inline-flex;
      font-size: 11px;
      font-weight: 700;
      line-height: 1;
      padding: 4px 7px;
    }
    body.review-mode .field-status.supported {
      display: inline-flex;
    }
    body.review-mode .field-status.reserved {
      display: inline-flex;
    }
    .developer-only {
      display: none !important;
    }
    body.review-mode .developer-only {
      display: block !important;
    }
    body.review-mode .developer-only.coverage-strip {
      display: grid !important;
    }
    body.review-mode .developer-only.record-list {
      display: grid !important;
    }
    .chip {
      border: 1px solid transparent;
      border-radius: 4px;
      min-height: 30px;
      padding: 4px 9px;
    }
    .chip:hover {
      background: #f4f8ff;
      border-color: #b9d8ff;
    }
    .chip:has(input:checked) {
      background: #ecf5ff;
      border-color: #409eff;
      color: #1677d2;
      font-weight: 700;
    }
    .action-strip {
      background: #fff;
      position: sticky;
      bottom: 72px;
      z-index: 20;
    }
    .sticky-action-bar {
      align-items: center;
      background: rgba(255, 255, 255, .97);
      border: 1px solid #d8e2ef;
      border-radius: 6px;
      bottom: 12px;
      box-shadow: 0 12px 34px rgba(15, 23, 42, .16);
      display: grid;
      gap: 12px;
      grid-template-columns: minmax(180px, 1fr) auto auto auto;
      left: 205px;
      padding: 10px 12px;
      position: fixed;
      right: 18px;
      z-index: 80;
    }
    .sticky-action-meta {
      min-width: 0;
    }
    .sticky-action-meta strong {
      color: #1f2937;
      display: block;
      font-size: 14px;
      line-height: 1.3;
    }
    .sticky-action-meta span {
      color: #7a8491;
      display: block;
      font-size: 12px;
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .sticky-action-bar .download[hidden] {
      display: none;
    }
    .empty-result-card {
      background: #f8fafc;
      border: 1px dashed #cbd5e1;
      border-radius: 6px;
      color: #64748b;
      font-size: 13px;
      line-height: 1.6;
      margin: 12px 0;
      padding: 14px;
    }
    .empty-result-card[hidden] {
      display: none;
    }
    .reserved-inline {
      opacity: .62;
    }
    .reserved-inline label::after {
      color: #909399;
      content: " · 仅记录";
      font-size: 12px;
      font-weight: 400;
    }
    @media (max-width: 900px) {
      .task-flow-card {
        position: static;
      }
      .task-steps,
      .selected-summary {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .selected-summary-item:nth-child(2) {
        border-right: 0;
      }
      .sticky-action-bar {
        left: 12px;
        right: 12px;
      }
    }
    @media (max-width: 700px) {
      body {
        padding-bottom: 128px;
      }
      header.dl-topbar {
        height: auto;
        min-height: 46px;
      }
      main.dl-shell {
        display: grid;
        grid-template-columns: 1fr;
      }
      .dl-shell .side-nav {
        display: flex;
        gap: 4px;
        height: auto;
        max-height: none;
        overflow-x: auto;
        padding: 8px;
        position: static;
        top: auto;
        width: auto;
      }
      .side-nav .nav-group-title {
        display: none;
      }
      .side-nav .nav-item {
        border-radius: 4px;
        flex: 0 0 auto;
        min-height: 30px;
        padding: 6px 10px;
        white-space: nowrap;
      }
      .side-nav .nav-item.active::before {
        display: none;
      }
      .workspace {
        padding: 10px 8px 22px;
      }
      #task,
      #results {
        padding-left: 10px;
        padding-right: 10px;
      }
      .task-steps,
      .selected-summary {
        grid-template-columns: 1fr;
      }
      .selected-summary-item {
        border-bottom: 1px solid #e2e8f0;
        border-right: 0;
        padding-bottom: 8px;
      }
      .selected-summary-item:last-child {
        border-bottom: 0;
        padding-bottom: 0;
      }
      .filter-anchor-bar {
        align-items: stretch;
        flex-direction: column;
      }
      .filter-row,
      .library-section {
        gap: 6px;
        grid-template-columns: 1fr;
      }
      .row-label,
      .library-section-head {
        border-left-color: #00c84b;
        padding-left: 8px;
        white-space: normal;
      }
      .section-control,
      .row-control {
        padding-left: 8px;
        padding-right: 8px;
      }
      .category-tools,
      .category-cascade-grid {
        grid-template-columns: 1fr;
      }
      .category-cascade-grid {
        min-height: 0;
      }
      .category-column-list {
        max-height: 220px;
      }
      .search-grid > div,
      .run-grid > div,
      .url-grid > div,
      .search-grid > div:first-child,
      .search-grid > div:nth-child(3) {
        flex: 1 1 100%;
      }
      .sticky-action-bar {
        bottom: 8px;
        grid-template-columns: 1fr 1fr;
      }
      .sticky-action-meta {
        grid-column: 1 / -1;
      }
      .sticky-action-bar button,
      .sticky-action-bar .download {
        justify-content: center;
        width: 100%;
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
          <button class="tab developer-only" type="button" data-tab="fields">字段编号</button>
        </div>

        <div id="task" class="tab-view active panel">
          <div class="task-flow-card" id="taskFlowCard">
            <div class="task-steps" aria-label="采集任务流程">
              <div class="task-step is-active">1 类目</div>
              <div class="task-step">2 搜索 / URL</div>
              <div class="task-step">3 筛选</div>
              <div class="task-step">4 查询</div>
              <div class="task-step">5 导出复核</div>
            </div>
            <div class="selected-summary" id="selectedSummary">
              <div class="selected-summary-item">
                <span>类目范围</span>
                <strong id="selectedCategoryCount">全部</strong>
                <em id="selectedCategoryText">全部类目</em>
              </div>
              <div class="selected-summary-item">
                <span>已选筛选</span>
                <strong id="selectedFilterCount">0 项</strong>
                <em id="selectedFilterText">未选择高级筛选</em>
              </div>
              <div class="selected-summary-item">
                <span>搜索入口</span>
                <strong id="selectedSearchMode">关键词</strong>
                <em id="selectedKeywordText">未填写关键词或 URL</em>
              </div>
              <div class="selected-summary-item">
                <span>结果与导出</span>
                <strong id="selectedResultText">0 条</strong>
                <em id="selectedExportText">运行后生成导出文件</em>
              </div>
            </div>
            <div class="filter-anchor-bar">
              <div class="filter-anchor-actions">
                <button type="button" data-scroll-target="categoryFilterSection">类目</button>
                <button type="button" data-scroll-target="searchFilterSection">搜索</button>
                <button type="button" data-scroll-target="advancedFilterSection">高级筛选</button>
                <button type="button" data-scroll-target="runFilterSection">查询设置</button>
                <button type="button" data-scroll-target="resultSection">结果导出</button>
              </div>
              <label class="review-mode-toggle">
                <input id="reviewModeToggle" type="checkbox" />
                调试视图
                <span>显示字段映射</span>
              </label>
            </div>
            <div id="detailWarning" class="detail-warning" hidden></div>
          </div>
          <div class="filter-panel">
          <div class="library-toolbar">
            <div>
              <h2>筛选条件</h2>
              <div class="subtle-count">类目范围、精准搜索、高级筛选、销售、商品、卖家信息</div>
            </div>
            <div class="toolbar-meta">
              <span id="capabilityBadge" class="status-pill">能力加载中</span>
            </div>
          </div>

          <div class="collect-mode-panel" id="collectModePanel">
            <div class="collect-mode-head">
              <strong>采集方式</strong>
              <span>主流程：系统按你选择的类目和筛选条件批量执行；人工只负责登录和必要验证</span>
            </div>
            <div class="collect-mode-options" role="radiogroup" aria-label="采集方式">
              <label class="collect-mode-card">
                <input type="radio" name="collectModeChoice" value="rpa" checked />
                <strong>自动批量采集（推荐）</strong>
                <span>你在本页选类目、筛选和数量；系统复用采集 Chrome 登录态自动打开 1688、点击类目和筛选、翻页采集。</span>
                <em>主流程；遇到滑块会暂停给你人工处理</em>
              </label>
              <label class="collect-mode-card">
                <input type="radio" name="collectModeChoice" value="manual_url" />
                <strong>当前页兜底读取</strong>
                <span>风控或类目点击失败时使用：你在采集 Chrome 里手动筛好页面，系统只读取当前商品列表/详情页。</span>
                <em>备用；不适合批量主流程</em>
              </label>
            </div>
            <select id="collectSource" hidden aria-hidden="true">
              <option value="rpa" selected>自动批量采集（推荐）</option>
              <option value="manual_url">当前页兜底读取</option>
              <option value="url_direct">URL 直连读取（备用）</option>
              <option value="api">1688 AK/API</option>
            </select>
            <div id="browserAssistBox" class="browser-assist-box">
              <div class="browser-assist-title">
                <strong id="browserAssistTitle">自动批量采集流程</strong>
                <span id="browserPageCount" class="status-pill">等待检测</span>
              </div>
              <div class="browser-assist-actions">
                <button id="startChromeBtn" class="primary" type="button">启动采集 Chrome</button>
                <button id="refreshChromeBtn" class="secondary" type="button">刷新状态</button>
                <button id="readCurrentPageBtn" class="secondary" type="button">读取当前页兜底</button>
              </div>
              <div class="browser-assist-steps">
                <span id="browserStep1">1. 先点击“启动采集 Chrome”，在弹出的 Chrome 里登录 1688 并完成必要验证。</span>
                <span id="browserStep2">2. 回到本页选择类目、关键词、筛选条件和采集数量。</span>
                <span id="browserStep3">3. 点击“开始自动采集”，系统会自动进入 1688 执行类目/筛选/翻页，采集完成后导出真实数据。</span>
              </div>
              <div id="browserPageList" class="browser-page-list"></div>
            </div>
            <div id="collectUrlBox" class="collect-url-box">
              <label for="sourceUrls">
                可选：指定要读取的 1688 页签 URL
                <span>多个 1688 页签时用于匹配；URL 直连模式下必填</span>
              </label>
              <div class="collect-url-steps">
                仅兜底读取模式需要：如果采集 Chrome 里打开了多个 1688 页签，可以粘贴目标页 URL 防止读错。
              </div>
              <textarea id="sourceUrls" placeholder="例如：https://s.1688.com/selloffer/offer_search.htm?keywords=收纳盒 或 https://detail.1688.com/offer/xxxx.html"></textarea>
            </div>
            <div class="collect-mode-status">
              <span id="collectModeBadge" class="status-pill">自动批量采集</span>
              <span id="runtimeBadge" class="status-pill">真实 Chrome 未连接</span>
              <span id="realModeHint" class="collect-mode-hint">系统会自动按本页类目和筛选条件执行；如 1688 弹滑块/验证码，会暂停并交给你人工处理。</span>
            </div>
          </div>

          <div id="categoryFilterSection" class="filter-row compact">
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

          <div id="searchFilterSection" class="library-section precise-section">
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
                <input id="sampleMode" type="checkbox" hidden aria-hidden="true" />
              </div>
              <div class="library-grid url-grid">
                <div>
                  <label for="excludeTags">排除标签</label>
                  <textarea id="excludeTags" placeholder="屏蔽多个商品关键词，顿号隔开"></textarea>
                </div>
              </div>
            </div>
          </div>

          <div id="advancedFilterSection">
            <div id="libraryFilters"></div>
          </div>

          <details class="legacy-filter-box">
            <summary>兼容旧版运营标签</summary>
            <div id="filterGroups"></div>
          </details>

          <div id="runFilterSection" class="library-section run-section">
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
                <label for="statPeriod">统计周期</label>
                <select id="statPeriod" class="reserved-inline" data-library-select="stat_period" data-default-value="近30天" data-reserved-filter="true">
                  <option value="近30天">近30天</option>
                  <option value="近7天">近7天</option>
                  <option value="近90天">近90天</option>
                </select>
              </div>
              <div>
                <label for="sortBy">排序</label>
                <select id="sortBy" class="reserved-inline" data-library-select="sort_by" data-default-value="推荐分" data-reserved-filter="true">
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
                <label for="maxItems">目标可铺数</label>
                <input id="maxItems" type="number" min="1" max="100" value="5" />
              </div>
              <div>
                <label for="autoVerifyDetails">详情补字段</label>
                <label class="sample-switch">
                  <input id="autoVerifyDetails" type="checkbox" checked />
                  自动核验
                </label>
              </div>
              <div>
                <label for="autoVerifyMax">自动核验上限</label>
                <input id="autoVerifyMax" type="number" min="1" max="20" value="20" />
              </div>
            </div>
          </div>

          <div class="action-strip">
            <button id="runBtn" class="primary" type="button">开始自动采集</button>
            <button id="resetBtn" class="secondary" type="button">重置筛选</button>
            <button id="saveFilterBtn" class="secondary ghost" type="button" disabled title="筛选模板接口预留">保存筛选</button>
          </div>

          <div id="notice" class="notice"></div>
          <div class="sticky-action-bar" aria-label="筛选任务操作">
            <div class="sticky-action-meta">
              <strong id="stickyActionTitle">准备筛选 1688 商品</strong>
              <span id="stickyActionText">选择类目、关键词或筛选项后开始自动采集；导出后再人工复核。</span>
            </div>
            <button id="stickyRunBtn" class="primary" type="button">开始自动采集</button>
            <button id="stickyResetBtn" class="secondary" type="button">重置</button>
            <a id="stickyDownloadLink" class="download" href="#" hidden>导出</a>
          </div>
          <div class="verification-records developer-only">
            <div class="panel-title">
              <div>
                <h2>调试记录</h2>
                <div class="subtle-count">仅用于排查字段映射、原生筛选点击和后筛规则</div>
              </div>
            </div>
            <div id="coverageSummary" class="coverage-strip developer-only"></div>
            <div id="coverageList" class="coverage-list developer-only"></div>
            <div id="filterRecordList" class="record-list developer-only"></div>
          </div>
          <div class="summary">
            <div class="metric"><span>采集批次</span><strong id="runId">-</strong></div>
            <div class="metric"><span>结果商品</span><strong id="rowCount">0</strong></div>
            <div class="metric"><span>P0/P1</span><strong id="highCount">0</strong></div>
            <div class="metric"><span>达标可铺</span><strong id="suggestCount">0</strong></div>
          </div>
          </div>
        </div>

        <div id="results" class="tab-view panel">
          <span id="resultSection" aria-hidden="true"></span>
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
            <div><span>任务状态</span><strong id="automationStatus">-</strong></div>
            <div><span>待核验高潜</span><strong id="queueCount">0</strong></div>
            <div><span>已核验商品</span><strong id="verifiedCount">0</strong></div>
            <div><span>核验记录</span><strong id="recordCount">0</strong></div>
            <button id="verifyBtn" class="secondary" type="button" disabled>真实详情核验</button>
          </div>
          <div id="automationMessage" class="automation-message"></div>
          <div id="resultEmptyHint" class="empty-result-card">还没有查询结果。先完成类目、搜索词或筛选条件，再点击“开始自动采集”；生成结果后可导出 Excel/CSV，导出后再人工复核。</div>
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
                <option value="unverified">待核验</option>
                <option value="sample_verified">调试已核验</option>
                <option value="partial_verified">部分核验</option>
                <option value="verified">已核验</option>
                <option value="failed">核验失败</option>
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
          <div class="verification-records developer-only">
            <div class="panel-title">
              <div>
                <h2>字段核验记录</h2>
                <div class="subtle-count">记录 raw / normalized / source / verified_at / fail_reason</div>
              </div>
            </div>
            <div id="recordList" class="record-list"></div>
          </div>
        </div>

        <div id="fields" class="tab-view panel developer-only">
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
      automationState: {},
      runtime: {},
      filterPlan: {},
      filterResults: [],
      filterWarnings: [],
      reviewMode: false,
      debugSampleMode: false,
      debugViewMode: false,
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

    function readDebugParams() {
      const params = new URLSearchParams(window.location.search);
      const flags = {
        sample: params.get("codex_sample") === "1" || params.get("sample") === "1",
        view: params.get("codex_debug") === "1" || params.get("debug") === "1"
      };
      if ((flags.sample || flags.view) && window.history.replaceState) {
        window.history.replaceState({}, document.title, `${window.location.pathname}${window.location.hash || ""}`);
      }
      return flags;
    }

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

    function runtimeLabel(runtime = state.runtime) {
      if (!runtime) return "未知浏览器模式";
      if (runtime.use_cdp) return runtime.cdp_connected ? "真实 Chrome 已连接" : "真实 Chrome 未连接";
      return "项目内置浏览器";
    }

    function collectSourceLabel(value) {
      if (value === "url_direct") return "URL 直连读取";
      if (value === "manual_url") return "当前页兜底读取";
      if (value === "rpa") return "自动批量采集";
      if (value === "api") return "1688 AK/API";
      return "未知采集方式";
    }

    function readable1688Pages(runtime = state.runtime) {
      return (runtime?.pages || []).filter(page => page.is_collectable);
    }

    function renderChromeRuntime(runtime = state.runtime) {
      const sourceMode = $("collectSource")?.value || "rpa";
      const pages = readable1688Pages(runtime);
      const all1688Pages = (runtime?.pages || []).filter(page => page.is_1688);
      if ($("browserPageCount")) {
        if (!runtime?.cdp_connected) {
          $("browserPageCount").textContent = "Chrome 未连接";
        } else if (sourceMode === "rpa") {
          $("browserPageCount").textContent = "Chrome 已连接";
        } else if (!pages.length) {
          $("browserPageCount").textContent = all1688Pages.length ? "未进入商品页" : "未发现 1688 页";
        } else {
          $("browserPageCount").textContent = `${pages.length} 个可读取页签`;
        }
      }
      if ($("browserPageList")) {
        if (!runtime?.cdp_connected) {
          $("browserPageList").innerHTML = `<div class="browser-page-item"><strong>还没有连接采集 Chrome</strong><span>点击“启动采集 Chrome”，在弹出的 Chrome 中打开并筛好 1688 页面。</span></div>`;
        } else if (sourceMode === "rpa") {
          $("browserPageList").innerHTML = `<div class="browser-page-item"><strong>采集 Chrome 已连接</strong><span>自动批量模式会从本页选择的类目和筛选条件开始执行，不要求你先手动打开商品列表页。</span></div>`;
        } else if (!pages.length) {
          const openedText = all1688Pages.length ? `已检测到 ${all1688Pages.length} 个 1688 页签，但还不是商品列表/详情页。` : "未检测到 1688 页签。";
          $("browserPageList").innerHTML = `<div class="browser-page-item"><strong>未检测到可读取页面</strong><span>${openedText}请在采集 Chrome 中手动搜索、点击左侧类目或打开商品详情页，确认页面上有商品后再读取。</span></div>`;
        } else {
          $("browserPageList").innerHTML = pages.slice(0, 5).map(page => `
            <div class="browser-page-item">
              <strong>${esc(page.title || "1688 页面")}</strong>
              <span>${esc(page.url || "-")}</span>
            </div>
          `).join("") + (pages.length > 5 ? `<div class="browser-page-item"><strong>还有 ${pages.length - 5} 个页签未展示</strong><span>建议只保留本次要采集的商品列表/详情页签。</span></div>` : "");
        }
      }
    }

    function updateRunButtonLabels() {
      const sourceMode = $("collectSource")?.value || "rpa";
      const label = sourceMode === "manual_url" ? "读取当前页" : "开始自动采集";
      if ($("runBtn") && !$("runBtn").disabled) $("runBtn").textContent = label;
      if ($("stickyRunBtn")) $("stickyRunBtn").textContent = label;
      if ($("readCurrentPageBtn")) $("readCurrentPageBtn").hidden = sourceMode !== "manual_url";
    }

    function collectModeNoticeText(sourceMode) {
      if (!state.options || !state.options.allow_real_collect) {
        return {
          text: "当前环境未开启真实采集：不会打开真实 1688 页面。正式测试请在运行服务的本机打开 127.0.0.1。",
          ok: false,
          mode: "paused"
        };
      }
      if (state.debugSampleMode) {
        return {
          text: "调试样例入口已开启：本轮不会采集真实 1688 数据。人工验收请直接打开普通地址，不带调试参数。",
          ok: false,
          mode: "paused"
        };
      }
      if (sourceMode === "url_direct") {
        return {
          text: "当前采集方式：URL 直连读取。该方式不复用登录态，容易被 1688 风控拦截；只建议用于公开页面诊断。",
          ok: true,
          mode: "ok"
        };
      }
      if (sourceMode === "manual_url") {
        return {
          text: "当前采集方式：当前页兜底读取。只在自动批量被风控或类目点击失败后使用；请在采集 Chrome 中手动筛好商品列表页，再读取当前页。",
          ok: true,
          mode: "ok"
        };
      }
      if (sourceMode === "rpa") {
        return {
          text: "当前采集方式：自动批量采集。系统会复用采集 Chrome 登录态，按本页类目和筛选条件自动执行；遇到滑块/验证码会暂停，交给你人工处理后继续。",
          ok: true,
          mode: "ok"
        };
      }
      return {
        text: "当前采集方式：1688 AK/API。该模式需要先配置 AK，未配置时会阻止真实采集。",
        ok: true,
        mode: "ok"
      };
    }

    function updateCollectModeUI(showToast = false) {
      const select = $("collectSource");
      if (!select) return;
      const sourceMode = select.value || "rpa";
      renderChromeRuntime();
      document.querySelectorAll("input[name='collectModeChoice']").forEach(input => {
        input.checked = input.value === sourceMode;
      });
      if ($("collectModeBadge")) $("collectModeBadge").textContent = collectSourceLabel(sourceMode);
      if ($("runtimeBadge")) {
        if (sourceMode === "url_direct") {
          $("runtimeBadge").textContent = "无需 Chrome";
        } else if (sourceMode === "manual_url") {
          $("runtimeBadge").textContent = state.runtime && state.runtime.cdp_connected ? "真实 Chrome 已连接" : "真实 Chrome 未连接";
        } else if (sourceMode === "rpa") {
          $("runtimeBadge").textContent = runtimeLabel();
        } else {
          $("runtimeBadge").textContent = "等待 AK 配置";
        }
      }
      if ($("browserAssistBox")) $("browserAssistBox").hidden = !["manual_url", "rpa"].includes(sourceMode);
      if ($("browserAssistTitle")) $("browserAssistTitle").textContent = sourceMode === "manual_url" ? "当前页兜底读取流程" : "自动批量采集流程";
      if ($("browserStep1")) $("browserStep1").textContent = sourceMode === "manual_url"
        ? "1. 点击“启动采集 Chrome”，在弹出的 Chrome 里登录 1688 并完成必要验证。"
        : "1. 点击“启动采集 Chrome”，在弹出的 Chrome 里登录 1688 并完成必要验证。";
      if ($("browserStep2")) $("browserStep2").textContent = sourceMode === "manual_url"
        ? "2. 在采集 Chrome 里手动搜索、点击左侧类目、设置筛选条件，确认页面已经出现商品列表。"
        : "2. 回到本页选择类目、关键词、筛选条件和采集数量。";
      if ($("browserStep3")) $("browserStep3").textContent = sourceMode === "manual_url"
        ? "3. 回到本页点击“读取当前页”，系统只读取你已经打开的真实页面。"
        : "3. 点击“开始自动采集”，系统会自动进入 1688 执行类目/筛选/翻页，采集完成后导出真实数据。";
      if ($("collectUrlBox")) {
        const needsUrl = sourceMode === "url_direct";
        const needsMatch = sourceMode === "manual_url" && readable1688Pages().length > 1;
        $("collectUrlBox").hidden = !(needsUrl || needsMatch);
      }
      const notice = collectModeNoticeText(sourceMode);
      if ($("realModeHint")) $("realModeHint").textContent = notice.text;
      if ($("autoVerifyDetails")) {
        const directMode = sourceMode === "url_direct";
        $("autoVerifyDetails").disabled = directMode;
        if (directMode) $("autoVerifyDetails").checked = false;
      }
      updateRunButtonLabels();
      if (showToast) showNotice(notice.text, notice.ok, notice.mode);
      updateSelectedSummary();
    }

    async function refreshChromeStatus(showToast = false) {
      const response = await fetch("/api/chrome/status");
      const result = await response.json();
      if (result.success && result.data && result.data.runtime) {
        state.runtime = result.data.runtime;
        renderChromeRuntime();
        updateCollectModeUI(false);
        if (showToast) {
          const sourceMode = $("collectSource")?.value || "rpa";
          const ok = Boolean(state.runtime.cdp_ready);
          const message = sourceMode === "rpa" && ok
            ? "采集 Chrome 已连接；自动批量采集会按本页类目和筛选条件开始执行，不需要你先手动打开商品列表页。"
            : (state.runtime.message || "采集 Chrome 状态已刷新。");
          showNotice(message, ok, ok ? "ok" : "paused");
        }
      }
      return state.runtime;
    }

    async function startCollectChrome() {
      const btn = $("startChromeBtn");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "启动中";
      }
      try {
        const response = await fetch("/api/chrome/start", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({token: state.options.token})
        });
        const result = await response.json();
        if (result.data && result.data.runtime) state.runtime = result.data.runtime;
        renderChromeRuntime();
        updateCollectModeUI(false);
        const sourceMode = $("collectSource")?.value || "rpa";
        const ok = result.success && Boolean(state.runtime.cdp_ready);
        const message = sourceMode === "rpa" && ok
          ? "采集 Chrome 已连接；现在可以选择类目和筛选条件后点击“开始自动采集”。"
          : (result.markdown || state.runtime.message || "采集 Chrome 已启动。");
        showNotice(message, ok, ok ? "ok" : "paused");
        return result.success;
      } catch (err) {
        showNotice(`启动采集 Chrome 失败：${err.message}`, false, "fail");
        return false;
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "启动采集 Chrome";
        }
      }
    }

    async function ensureChromeRuntimeReady() {
      await refreshChromeStatus(false).catch(() => state.runtime);
      if (!(state.runtime?.use_cdp && state.runtime?.cdp_ready)) {
        const started = await startCollectChrome();
        if (!started) return false;
      }
      return true;
    }

    async function ensureManualChromeReady() {
      const chromeReady = await ensureChromeRuntimeReady();
      if (!chromeReady) return false;
      const pages = readable1688Pages();
      if (!pages.length) {
        showNotice("采集 Chrome 已启动，但还没有检测到可读取的商品列表/详情页。请在弹出的 Chrome 中手动登录 1688、搜索或点击左侧类目，确认页面上有商品后，再点击“读取当前页”。", false, "paused");
        return false;
      }
      if (pages.length > 1 && !$("sourceUrls").value.trim()) {
        if ($("collectUrlBox")) $("collectUrlBox").hidden = false;
        showNotice("检测到多个可读取的 1688 页签。为避免读错页面，请只保留本次要采集的页签，或把目标页 URL 粘贴到“可选 URL”框后再读取。", false, "paused");
        return false;
      }
      return true;
    }

    function focusSourceUrlBox() {
      const box = $("collectUrlBox");
      const input = $("sourceUrls");
      if (!box || !input) return;
      box.classList.remove("is-attention");
      void box.offsetWidth;
      box.classList.add("is-attention");
      box.scrollIntoView({behavior: "smooth", block: "center"});
      window.setTimeout(() => input.focus(), 280);
    }

    function runtimeHint(runtime = state.runtime) {
      if (!runtime) return "";
      return runtime.message || "";
    }

    function collectFailureNotice(result) {
      const data = result.data || {};
      const runtime = data.runtime || state.runtime || {};
      const base = result.markdown || "采集失败";
      const suggestion = data.suggestion || "";
      if (data.error_code === "security_verification_required") {
        const pageUrl = runtime.page_url ? ` 当前拦截页：${runtime.page_url}` : "";
        return `${base} ${suggestion}${pageUrl}`;
      }
      if (data.error_code === "login_required") {
        return `${base} ${suggestion || "请先在真实 Chrome 中完成 1688 登录，再回到本页查询。"}`;
      }
      if (runtime.use_cdp && runtime.cdp_connected === false) {
        return `${base} 当前采集 Chrome 未连接。请点击“启动采集 Chrome”，在弹出的 Chrome 中登录 1688 后再开始自动采集。`;
      }
      return `${base}${suggestion ? ` ${suggestion}` : ""}`;
    }

    function updateCategorySummary() {
      const selected = [...state.selectedCategories];
      const summary = $("categorySummary");
      if (!summary) return;
      summary.textContent = selected.length ? `已选 ${selected.length} 个：${selected.slice(0, 4).join("、")}${selected.length > 4 ? "..." : ""}` : "全部类目";
      updateSelectedSummary();
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
      updateSelectedSummary();
    }

    function statusLabel(status) {
      return {
        supported: "可筛选",
        partial_supported: "需复核",
        detail_required: "需详情核验",
        reserved: "预留"
      }[status] || status || "预留";
    }

    function verificationStatusLabel(value) {
      return {
        unverified: "待核验",
        sample_verified: "调试已核验",
        partial_verified: "部分核验",
        verified: "已核验",
        failed: "核验失败"
      }[value] || value || "-";
    }

    function statusBadge(status) {
      return `<span class="field-status ${esc(status || "reserved")}">${esc(statusLabel(status))}</span>`;
    }

    function fieldStatusHint(field) {
      const mapping = field.mapping ? `映射：${field.mapping}` : "";
      const scope = field.applicable_roots?.length ? `适用类目：${field.applicable_roots.slice(0, 6).join("、")}${field.applicable_roots.length > 6 ? "等" : ""}` : "";
      const statusHint = {
        supported: "可参与当前真实采集计划。",
        partial_supported: "可尝试执行，但命中情况以页面返回和详情核验为准。",
        detail_required: "列表页无法完全确认，需进入商品详情页核验后再判断。",
        reserved: "接口或数据源预留，默认不参与本次真实过滤。"
      }[field.status] || "需评审确认字段状态。";
      return [statusHint, scope, mapping].filter(Boolean).join(" ");
    }

    function fieldClass(field) {
      const classes = ["library-filter-card"];
      if (field.status === "reserved") classes.push("is-reserved-filter");
      if (field.status === "detail_required") classes.push("is-detail-filter");
      if (field.status === "partial_supported") classes.push("is-partial-filter");
      if (!fieldAppliesToSelectedCategories(field)) classes.push("is-category-mismatch");
      return classes.join(" ");
    }

    function selectedCategoryRoots() {
      return [...state.selectedCategories]
        .map(value => String(value || "").split(">")[0])
        .filter(Boolean);
    }

    function fieldAppliesToSelectedCategories(field) {
      const roots = field.applicable_roots || [];
      if (!roots.length) return true;
      const selectedRoots = selectedCategoryRoots();
      if (!selectedRoots.length) return true;
      return selectedRoots.some(root => roots.includes(root));
    }

    function categoryScopeBadge(field) {
      const roots = field.applicable_roots || [];
      if (!roots.length) return "";
      return fieldAppliesToSelectedCategories(field)
        ? `<span class="category-scope-label">适用于当前类目</span>`
        : `<span class="category-scope-label">当前类目未确认</span>`;
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
      const fields = section.fields || [];
      const rangeFields = fields.filter(field => field.type === "range");
      const otherFields = fields.filter(field => field.type !== "range");
      const rangeHtml = rangeFields.length ? `
        <div class="range-grid">
          ${rangeFields.map(field => `
            <div class="range-field ${fieldClass(field)}">
              <label>${esc(field.label)} ${statusBadge(field.status)} ${categoryScopeBadge(field)}</label>
              <div class="range-pair">
                <input type="number" data-library-range="${esc(field.key)}" data-bound="min" placeholder="最小" />
                <span class="range-sep">至</span>
                <input type="number" data-library-range="${esc(field.key)}" data-bound="max" placeholder="最大" />
              </div>
              <div class="field-hint">${esc(fieldStatusHint(field))}</div>
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
        return `<div class="filter-block ${fieldClass(field)}" style="margin-top:0;">
          <div class="filter-block-head"><strong>${esc(field.label)} ${statusBadge(field.status)} ${categoryScopeBadge(field)}</strong><span class="mapping-label">${esc(field.mapping || "")}</span></div>
          <div class="tag-grid">${(field.options || []).map(option => `
            <label class="chip"><input type="checkbox" data-library-list="${esc(field.key)}" value="${esc(option)}" />${esc(option)}</label>
          `).join("")}</div>
          <div class="field-hint">${esc(fieldStatusHint(field))}</div>
        </div>`;
      }
      if (field.type === "radio") {
        return `<div class="filter-block ${fieldClass(field)}" style="margin-top:0;">
          <div class="filter-block-head"><strong>${esc(field.label)} ${statusBadge(field.status)} ${categoryScopeBadge(field)}</strong><span class="mapping-label">${esc(field.mapping || "")}</span></div>
          <div class="tag-grid">${(field.options || []).map((option, index) => `
            <label class="chip"><input type="radio" name="library-${esc(field.key)}" data-library-radio="${esc(field.key)}" value="${esc(option)}" ${index === 0 ? "checked" : ""} />${esc(option)}</label>
          `).join("")}</div>
          <div class="field-hint">${esc(fieldStatusHint(field))}</div>
        </div>`;
      }
      if (field.type === "boolean") {
        return `<div class="boolean-field ${fieldClass(field)}">
          <label class="chip"><input type="checkbox" data-library-bool="${esc(field.key)}" />${esc(field.label)} ${statusBadge(field.status)} ${categoryScopeBadge(field)}</label>
          <div class="field-hint">${esc(fieldStatusHint(field))}</div>
        </div>`;
      }
      if (field.type === "select") {
        return `<div class="select-field ${fieldClass(field)}">
          <label>${esc(field.label)} ${statusBadge(field.status)} ${categoryScopeBadge(field)}</label>
          <select data-library-select="${esc(field.key)}">
            <option value="">不限</option>
            ${(field.options || []).map(option => `<option value="${esc(option)}">${esc(option)}</option>`).join("")}
          </select>
          <div class="field-hint">${esc(fieldStatusHint(field))}</div>
        </div>`;
      }
      return `<div class="text-field ${fieldClass(field)}">
        <label>${esc(field.label)} ${statusBadge(field.status)} ${categoryScopeBadge(field)}</label>
        <input type="text" data-library-text="${esc(field.key)}" />
        <div class="field-hint">${esc(fieldStatusHint(field))}</div>
      </div>`;
    }

    function effectiveSourceUrls() {
      const sourceMode = $("collectSource")?.value || "rpa";
      const raw = $("sourceUrls")?.value.trim() || "";
      if (sourceMode === "rpa" && state.selectedCategories.size > 0) return "";
      return raw;
    }

    function collectLibraryFilters() {
      const filters = {};
      const categoryPaths = [...state.selectedCategories];
      const keyword = $("keywords").value.trim();
      const matchType = $("matchType").value;
      const historyKeyword = $("historyKeyword").value.trim();
      const templateName = $("templateName").value.trim();
      const sourceUrls = effectiveSourceUrls();
      if (categoryPaths.length) filters.category_paths = categoryPaths;
      if (keyword) filters.search_keyword = keyword;
      if (matchType && matchType !== "模糊匹配") filters.match_type = matchType;
      if (historyKeyword) filters.history_keyword = historyKeyword;
      if (templateName) filters.template_name = templateName;
      if (sourceUrls) filters.source_urls = sourceUrls;
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
        if (input.checked && input.value && input.value !== "不限") filters[input.dataset.libraryRadio] = input.value;
      });
      document.querySelectorAll("[data-library-select]").forEach(input => {
        const value = input.value;
        if (!value || value === "不限") return;
        if (input.dataset.reservedFilter === "true" && value === input.dataset.defaultValue) return;
        filters[input.dataset.librarySelect] = value;
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

    function libraryFieldIndex() {
      const index = {};
      (state.options?.library_filter_schema || []).forEach(section => {
        (section.fields || []).forEach(field => {
          index[field.key] = field;
        });
      });
      return index;
    }

    function activeLibraryLabels(filters) {
      const fieldIndex = libraryFieldIndex();
      const ignore = new Set(["category_paths", "search_keyword", "source_urls", "history_keyword", "template_name", "match_type"]);
      const seen = new Set();
      const labels = [];
      Object.entries(filters || {}).forEach(([key, value]) => {
        if (ignore.has(key)) return;
        const baseKey = key.replace(/_(min|max)$/, "");
        if (seen.has(baseKey)) return;
        const isRange = key.endsWith("_min") || key.endsWith("_max");
        const field = fieldIndex[baseKey] || fieldIndex[key];
        if (!field) return;
        if (Array.isArray(value) && !value.length) return;
        if (value === false || value === "" || value == null) return;
        seen.add(baseKey);
        labels.push(field.label || baseKey);
        if (isRange) seen.add(baseKey);
      });
      state.selectedTags.forEach(tag => labels.push(tag));
      return labels;
    }

    function selectedDetailLabels(filters) {
      const fieldIndex = libraryFieldIndex();
      const labels = [];
      Object.entries(filters || {}).forEach(([key, value]) => {
        const baseKey = key.replace(/_(min|max)$/, "");
        const field = fieldIndex[baseKey] || fieldIndex[key];
        if (!field || field.status !== "detail_required") return;
        if (value === false || value === "" || value == null) return;
        if (Array.isArray(value) && !value.length) return;
        if (!labels.includes(field.label)) labels.push(field.label);
      });
      return labels;
    }

    function updateSelectedSummary() {
      if (!$("selectedCategoryCount")) return;
      const filters = collectLibraryFilters();
      const categoryPaths = filters.category_paths || [];
      const activeLabels = activeLibraryLabels(filters);
      const keyword = $("keywords")?.value.trim() || "";
      const sourceUrls = effectiveSourceUrls();
      const historyKeyword = $("historyKeyword")?.value.trim() || "";
      const searchParts = [keyword, historyKeyword].filter(Boolean);
      $("selectedCategoryCount").textContent = categoryPaths.length ? `${categoryPaths.length} 个` : "全部";
      $("selectedCategoryText").textContent = categoryPaths.length ? categoryPaths.slice(0, 3).join("、") : "全部类目";
      $("selectedFilterCount").textContent = `${activeLabels.length} 项`;
      $("selectedFilterText").textContent = activeLabels.length ? activeLabels.slice(0, 5).join("、") : "未选择高级筛选";
      const sourceMode = $("collectSource")?.value || "rpa";
      if (sourceMode === "manual_url") {
        $("selectedSearchMode").textContent = "当前页";
        const currentPage = state.runtime?.current_page || {};
        $("selectedKeywordText").textContent = currentPage.url || sourceUrls || "读取采集 Chrome 当前 1688 页";
      } else if (sourceMode === "rpa") {
        $("selectedSearchMode").textContent = "自动批量";
        $("selectedKeywordText").textContent = searchParts.join("、") || (categoryPaths.length ? "按已选类目自动采集" : "按筛选条件自动采集");
      } else {
        $("selectedSearchMode").textContent = sourceUrls ? "URL" : "关键词";
        $("selectedKeywordText").textContent = sourceUrls || searchParts.join("、") || "未填写关键词或 URL";
      }
      $("selectedResultText").textContent = `${state.rows.length} 条`;
      $("selectedExportText").textContent = state.runId ? `批次 ${state.runId}` : "运行后生成导出文件";
      if ($("stickyActionTitle")) {
        $("stickyActionTitle").textContent = state.runId ? `已生成 ${state.rows.length} 条结果` : "准备筛选 1688 商品";
        const searchText = sourceMode === "manual_url"
          ? "当前页兜底读取"
          : (sourceMode === "rpa" ? "自动批量采集" : (sourceUrls ? "URL 采集" : (keyword ? `关键词 ${keyword}` : "待填写搜索入口")));
        $("stickyActionText").textContent = `${categoryPaths.length ? `类目 ${categoryPaths.length} 个` : "全部类目"} · ${activeLabels.length} 个筛选 · ${searchText}`;
      }
      const detailLabels = selectedDetailLabels(filters);
      const warning = $("detailWarning");
      if (warning) {
        const autoVerifyOff = $("autoVerifyDetails") && !$("autoVerifyDetails").checked;
        warning.hidden = !(detailLabels.length && autoVerifyOff);
        warning.textContent = detailLabels.length && autoVerifyOff
          ? `已选择 ${detailLabels.join("、")}，这些字段需要进入商品详情页核验；当前关闭自动核验时，导出结果只会标记为待核验。`
          : "";
      }
      const emptyHint = $("resultEmptyHint");
      if (emptyHint) emptyHint.hidden = state.rows.length > 0;
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
        return `<span class="badge ${cls}">${esc(verificationStatusLabel(value))}</span>`;
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
      state.automationState = {};
      state.filterPlan = {};
      state.filterResults = [];
      state.filterWarnings = [];
      state.selectedRows.clear();
      $("downloadLink").hidden = true;
      $("downloadLink").removeAttribute("href");
      if ($("stickyDownloadLink")) {
        $("stickyDownloadLink").hidden = true;
        $("stickyDownloadLink").removeAttribute("href");
      }
      renderTable();
      updateSummary({run_id: "", verification_queue: [], verification_records: []});
    }

    function automationNoticeMode(automationState) {
      const status = automationState.status || "";
      if (status === "paused") return "paused";
      if (status === "failed") return "fail";
      if (status === "partial" || status === "pending_detail") return "paused";
      return "ok";
    }

    function applyAutomationState(automationState) {
      state.automationState = automationState || {};
      const node = $("automationMessage");
      $("automationStatus").textContent = state.automationState.status_label || "-";
      const message = state.automationState.message || "";
      if (!message) {
        node.textContent = "";
        node.className = "automation-message";
        return;
      }
      node.textContent = message;
      node.className = `automation-message show ${state.automationState.status || ""}`;
    }

    function updateSummary(data) {
      state.runId = data.run_id || state.runId || "";
      state.verificationQueue = data.verification_queue || state.verificationQueue || [];
      state.verificationRecords = data.verification_records || state.verificationRecords || [];
      state.filterReevaluationRecords = data.filter_reevaluation_records || state.filterReevaluationRecords || [];
      applyAutomationState(data.automation_state || state.automationState || {});
      state.filterPlan = data.filter_plan || state.filterPlan || {};
      state.filterResults = data.filter_results || state.filterResults || [];
      state.filterWarnings = data.filter_warnings || state.filterWarnings || [];
      $("runId").textContent = data.run_id || "-";
      $("rowCount").textContent = String(state.rows.length);
      $("highCount").textContent = String(state.rows.filter(row => ["P0", "P1"].includes(row.recommendation_level)).length);
      $("suggestCount").textContent = String(data.publishable_count ?? state.rows.filter(row => row.wechat_shop_suggestion === "可铺").length);
      $("queueCount").textContent = String(state.verificationQueue.length);
      $("verifiedCount").textContent = String(state.rows.filter(row => ["sample_verified", "verified", "partial_verified"].includes(row.verification_status)).length);
      $("recordCount").textContent = String(state.verificationRecords.length);
      $("verifyBtn").disabled = !state.runId || state.verificationQueue.length === 0;
      updateSelectedCount();
      updateSelectedSummary();
      renderRecords();
      renderFilterRecords();
    }

    function renderFilterRecords() {
      const categoryDiagnosticText = (record) => {
        const diagnostics = record.diagnostics || {};
        const visibleTexts = Array.isArray(diagnostics.visible_category_texts) ? diagnostics.visible_category_texts.slice(0, 8).join("、") : "";
        const missingStep = Array.isArray(record.category_steps)
          ? record.category_steps.find(step => ["not_found", "click_failed", "not_confirmed"].includes(step.status))
          : null;
        const missing = missingStep && (missingStep.expected_text || (missingStep.needles || [])[0]);
        const navigationState = record.navigation_state || {};
        const steps = Array.isArray(record.category_steps)
          ? record.category_steps.map(step => `${step.depth}.${step.expected_text || "-"}:${step.mode || "-"}=${step.status || "-"}`).join(" / ")
          : "";
        const pieces = [];
        if (missing) pieces.push(`未命中：${missing}`);
        if (steps) pieces.push(`点击链路：${steps}`);
        if (record.status === "not_confirmed") pieces.push("未确认进入目标类目结果页");
        if (navigationState.leaf_in_url || navigationState.leaf_in_page) pieces.push("叶子类目已在结果页出现");
        if (visibleTexts) pieces.push(`页面可见类目：${visibleTexts}`);
        return pieces.join(" · ");
      };
      const planned = [
        ...(state.filterPlan.category_filters || []).map(item => ({...item, plan_type: "1688类目点击"})),
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
            line1: record.source === "1688_category_navigation"
              ? `期望：${record.expected_path || record.tag || "-"} · 命中：${record.matched_path || record.matched_text || "-"}`
              : `来源：${record.source || "-"} · ${record.query || "-"}`,
            line2: record.source === "1688_category_navigation"
              ? `层级：${record.matched_depth || 0}/${record.expected_depth || "-"} · ${record.final_url || record.page_url || "-"}${categoryDiagnosticText(record) ? ` · ${categoryDiagnosticText(record)}` : ""}`
              : (record.message || record.matched_text || "-")
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
      $("runBtn").textContent = "读取中";
      try {
        const sourceMode = $("collectSource").value;
        if (!state.debugSampleMode && sourceMode === "url_direct" && !$("sourceUrls").value.trim()) {
          focusSourceUrlBox();
          showNotice("还没有粘贴 1688 链接：请先去 1688 手动搜索/筛选商品，复制浏览器地址栏链接，再粘贴到下方蓝色 URL 输入框。", false, "paused");
          return;
        }
        if (!state.debugSampleMode && sourceMode === "manual_url") {
          const ready = await ensureManualChromeReady();
          if (!ready) return;
        }
        if (!state.debugSampleMode && sourceMode === "rpa") {
          const ready = await ensureChromeRuntimeReady();
          if (!ready) return;
        }
        const sourceUrlsForPayload = effectiveSourceUrls();
        const payload = {
          categories: [...state.selectedCategories],
          tags: [...state.selectedTags],
          keywords: $("keywords").value,
          source_urls: sourceUrlsForPayload,
          exclude_tags: $("excludeTags").value,
          library_filters: collectLibraryFilters(),
          max_queries: Number($("maxQueries").value || 20),
          max_items_per_query: Number($("maxItems").value || 5),
          target_publishable_count: Number($("maxItems").value || 5),
          auto_verify_details: $("autoVerifyDetails").checked,
          auto_verify_max_items: Number($("autoVerifyMax").value || $("maxItems").value || 20),
          output_format: $("outputFormat").value,
          collect_source: sourceMode,
          sample_data: state.debugSampleMode,
          token: state.options.token
        };
        const response = await fetch("/api/collect", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (result.data && result.data.runtime) {
          state.runtime = result.data.runtime;
          renderChromeRuntime();
          updateCollectModeUI(false);
        }
        if (!result.success) {
          clearRunState();
          state.filterResults = (result.data && result.data.filter_results) || [];
          state.filterWarnings = state.filterResults;
          renderFilterRecords();
          const errorCode = result.data && result.data.error_code;
          showNotice(
            collectFailureNotice(result),
            false,
            ["security_verification_required", "login_required", "search_keyword_encoding_error", "search_box_not_found", "search_results_not_loaded", "category_navigation_not_loaded", "navigation_timeout", "direct_url_required", "direct_url_fetch_failed"].includes(errorCode) ? "paused" : "fail"
          );
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
        $("stickyDownloadLink").href = result.data.download_url;
        $("stickyDownloadLink").hidden = false;
        const sourceLabel = collectSourceLabel($("collectSource").value);
        const modeText = state.debugSampleMode ? "调试样例" : `真实数据/${sourceLabel}`;
        const queryLabel = sourceMode === "manual_url" ? "当前页" : (sourceUrlsForPayload ? "采集页面" : "查询词");
        const warningText = (state.filterWarnings || []).map(item => `${item.label || item.tag || item.filter_key}:${item.status}`).join("；");
        const nativeText = ((state.filterPlan.native_filters || []).map(item => item.label || item.tag).filter(Boolean)).join("，");
        const targetText = result.data.target_publishable_count
          ? `可铺目标 ${result.data.publishable_count || 0}/${result.data.target_publishable_count}，可铺/谨慎候选 ${result.data.publishable_candidate_count || 0}，候选扫描 ${result.data.candidate_count || 0}/${result.data.candidate_scan_limit || "-"}，历史跳过 ${result.data.skipped_rejected_count || 0}。`
          : "";
        const shortfallText = result.data.shortfall_reason ? `未达目标：${result.data.shortfall_reason}。` : "";
        const autoVerifyText = result.data.auto_verify_details
          ? `已自动详情核验 ${result.data.verified_count || 0} 个，剩余待核验 ${state.verificationQueue.length} 个。`
          : "运费、品退率、发货率等关键字段仍需详情页核验。";
        const automation = result.data.automation_state || {};
        const stateText = automation.message ? `状态：${automation.message}` : autoVerifyText;
        const ok = (state.filterWarnings || []).length === 0 && !["paused", "failed", "partial"].includes(automation.status || "");
        showNotice(`已生成 ${result.data.row_count} 条筛选结果（${modeText}）；${targetText}${shortfallText}${queryLabel}：${(result.data.queries || []).join("，")}。原生筛选：${nativeText || "无"}。${warningText ? `筛选提示：${warningText}。` : ""}${autoVerifyText}${stateText ? ` ${stateText}` : ""}`, ok, automationNoticeMode(automation));
      } catch (err) {
        showNotice(`采集失败：${err.message}`, false);
      } finally {
        $("runBtn").disabled = false;
        updateRunButtonLabels();
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
            sample_data: state.debugSampleMode,
            max_items: 5
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
        $("stickyDownloadLink").href = result.data.download_url;
        $("stickyDownloadLink").hidden = false;
        const automation = result.data.automation_state || {};
        const stopped = result.data.verification_stopped_reason || automation.stopped_reason || "";
        if (stopped || automation.status === "paused") {
          showNotice(`详情核验已暂停：${stopped || automation.message}。导出表已刷新为暂停/失败状态，请人工接管后再继续。`, false, "paused");
        } else {
          showNotice(`已核验 ${result.data.verified_count} 个高潜商品，导出表已刷新；partial_verified 表示真实页面只提取到部分字段。`, !["failed", "partial"].includes(automation.status || ""), automationNoticeMode(automation));
        }
      } catch (err) {
        showNotice(`核验失败：${err.message}`, false);
      } finally {
        $("verifyBtn").textContent = state.debugSampleMode ? "调试核验高潜" : "真实详情核验";
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
      if (target.dataset.kind === "category") renderLibraryFilters();
      updateSelectedSummary();
    });

    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!target.matches(".row-select")) return;
      target.checked ? state.selectedRows.add(target.dataset.rowKey) : state.selectedRows.delete(target.dataset.rowKey);
      updateSelectedCount();
      updateSelectedSummary();
    });

    document.addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) setTab(tab.dataset.tab);
    });

    document.addEventListener("click", (event) => {
      const action = event.target.closest("[data-scroll-target]");
      if (!action) return;
      const target = $(action.dataset.scrollTarget);
      if (!target) return;
      target.scrollIntoView({behavior: "smooth", block: "start"});
    });

    document.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-category-remove]");
      if (remove) {
        state.selectedCategories.delete(remove.dataset.categoryRemove);
        renderCategories();
        renderLibraryFilters();
        updateSelectedSummary();
        return;
      }
      const nav = event.target.closest("[data-category-nav]");
      if (!nav) return;
      const parts = (nav.dataset.categoryPath || "").split(">");
      const path = nav.dataset.categoryPath || "";
      if (nav.dataset.categoryNav === "parent" && parts[0]) {
        state.activeCategoryParent = parts[0];
        state.activeCategoryChild = "";
      }
      if (nav.dataset.categoryNav === "child" && parts[0]) {
        state.activeCategoryParent = parts[0];
        state.activeCategoryChild = parts[1] || "";
        const hasGrandchildren = categoryEntries(parts[0]).some(([child, grandchildren]) => (
          child === parts[1] && Array.isArray(grandchildren) && grandchildren.length
        ));
        if (!hasGrandchildren && path) state.selectedCategories.add(path);
      }
      if (nav.dataset.categoryNav === "grand" && parts[0]) {
        state.activeCategoryParent = parts[0];
        state.activeCategoryChild = parts[1] || "";
        if (path) state.selectedCategories.add(path);
      }
      renderCategories();
      renderLibraryFilters();
      updateSelectedSummary();
    });

    document.addEventListener("input", (event) => {
      const target = event.target;
      if (!target.matches("#keywords, #sourceUrls, #historyKeyword, #templateName, [data-library-range], [data-library-text]")) return;
      updateSelectedSummary();
    });

    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!target.matches("#matchType, #autoVerifyDetails, #statPeriod, #sortBy, [data-library-list], [data-library-bool], [data-library-radio], [data-library-select]")) return;
      updateSelectedSummary();
    });

    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!target.matches("input[name='collectModeChoice']")) return;
      $("collectSource").value = target.value;
      updateCollectModeUI(true);
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
      if ($("autoVerifyDetails")) $("autoVerifyDetails").checked = true;
      if ($("autoVerifyMax")) $("autoVerifyMax").value = "20";
      if ($("matchType")) $("matchType").value = "模糊匹配";
      if ($("collectSource")) $("collectSource").value = "rpa";
      if ($("statPeriod")) $("statPeriod").value = "近30天";
      if ($("sortBy")) $("sortBy").value = "推荐分";
      document.querySelectorAll("[data-library-list], [data-library-bool]").forEach(input => input.checked = false);
      document.querySelectorAll("[data-library-range], [data-library-text]").forEach(input => input.value = "");
      document.querySelectorAll("[data-library-radio]").forEach(input => {
        input.checked = input.value === "不限";
      });
      document.querySelectorAll("[data-library-select]").forEach(input => {
        input.value = input.dataset.defaultValue || "";
      });
      renderCategories();
      renderFilterGroups();
      renderTable();
      updateCollectModeUI();
      updateSelectedSummary();
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
    $("stickyRunBtn").addEventListener("click", runCollect);
    $("readCurrentPageBtn").addEventListener("click", runCollect);
    $("startChromeBtn").addEventListener("click", startCollectChrome);
    $("refreshChromeBtn").addEventListener("click", () => refreshChromeStatus(true));
    $("stickyResetBtn").addEventListener("click", () => $("resetBtn").click());
    $("verifyBtn").addEventListener("click", runVerify);
    $("reviewModeToggle").addEventListener("change", (event) => {
      state.reviewMode = event.target.checked;
      document.body.classList.toggle("review-mode", state.reviewMode);
      showNotice(state.reviewMode ? "调试视图已开启：会显示字段映射、预留字段和执行记录。" : "调试视图已关闭：仅显示业务筛选和结果。", true);
    });

    async function init() {
      const debugFlags = readDebugParams();
      const response = await fetch("/api/options");
      const result = await response.json();
      state.options = result.data;
      state.runtime = state.options.runtime || {};
      const caps = state.options.library_capabilities || {};
      $("capabilityBadge").textContent = `已接入 ${((caps.implemented || []).length)} 项 / 预留 ${((caps.reserved || []).length)} 项`;
      $("maxQueries").max = state.options.limits.max_queries;
      $("maxItems").max = state.options.limits.max_items_per_query;
      state.debugSampleMode = debugFlags.sample;
      state.debugViewMode = debugFlags.view || debugFlags.sample;
      state.reviewMode = state.debugViewMode;
      document.body.classList.toggle("review-mode", state.reviewMode);
      $("reviewModeToggle").checked = state.reviewMode;
      $("sampleMode").checked = state.debugSampleMode;
      $("verifyBtn").textContent = state.debugSampleMode ? "调试核验高潜" : "真实详情核验";
      if ($("collectSource")) $("collectSource").value = "rpa";
      renderCategories();
      renderLibraryFilters();
      renderFilterGroups();
      renderFilterCoverage();
      renderFields();
      renderTable();
      updateSummary({});
      renderChromeRuntime();
      updateCollectModeUI(true);
      updateSelectedSummary();
    }

    init();
  </script>
</body>
</html>
"""
