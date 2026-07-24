#!/usr/bin/env python3
"""1688 页面 RPA 采集适配层。"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from _errors import ServiceError


RPA_PROGRESS_PREFIX = "SOP_RPA_PROGRESS "
RpaProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _script_path(name: str) -> str:
    return str(Path(__file__).resolve().parent / name)


def _node_bin() -> str:
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        raise ServiceError("未找到 Node.js，无法执行真实页面 RPA。请先安装 Node.js，并安装 playwright。")
    return node


def _run_node_script(
    script_name: str,
    payload: Dict[str, Any],
    timeout: int = 180,
    *,
    cancel_event: Any = None,
    progress_callback: RpaProgressCallback = None,
) -> Dict[str, Any]:
    script = _script_path(script_name)
    serialized = json.dumps(payload, ensure_ascii=False)
    proc = subprocess.Popen(
        [_node_bin(), script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    started_at = time.monotonic()
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    progress_events: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def read_stream(stream: Any, output: List[str], *, parse_progress: bool = False) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                if parse_progress and line.startswith(RPA_PROGRESS_PREFIX):
                    try:
                        event = json.loads(line[len(RPA_PROGRESS_PREFIX):].strip())
                    except json.JSONDecodeError:
                        output.append(line)
                        continue
                    if isinstance(event, dict):
                        progress_events.put(event)
                        continue
                output.append(line)
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=read_stream,
        args=(proc.stdout, stdout_lines),
        name=f"tag-collect-{script_name}-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=read_stream,
        args=(proc.stderr, stderr_lines),
        kwargs={"parse_progress": True},
        name=f"tag-collect-{script_name}-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    def deliver_progress_events() -> None:
        while True:
            try:
                event = progress_events.get_nowait()
            except queue.Empty:
                return
            if not progress_callback:
                continue
            normalized = dict(event)
            normalized.setdefault("updated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
            try:
                progress_callback(normalized)
            except Exception:
                # 进度提示不能影响真实采集主流程。
                continue

    try:
        if proc.stdin is not None:
            proc.stdin.write(serialized)
            proc.stdin.close()
        while proc.poll() is None:
            deliver_progress_events()
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                error = ServiceError("task_cancelled: 用户已停止采集任务。")
                error.data = {"code": "task_cancelled", "retryable": True}
                raise error
            if time.monotonic() - started_at >= timeout:
                proc.kill()
                proc.wait()
                raise ServiceError(f"真实页面 RPA 超时：超过 {timeout} 秒")
            time.sleep(0.05)
        deliver_progress_events()
    except Exception:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    finally:
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        deliver_progress_events()

    stdout = "".join(stdout_lines).strip()
    stderr = "".join(stderr_lines).strip()
    if proc.returncode != 0:
        detail = stderr or stdout or f"exit={proc.returncode}"
        if "Browser.setDownloadBehavior" in detail or "Browser context management is not supported" in detail:
            error = ServiceError(
                "cdp_context_unsupported: 真实 Chrome CDP 端口能连接，但当前调试端点不支持 Playwright 初始化上下文。"
                "请关闭该调试 Chrome 后重新运行 scripts/capabilities/tag_collect/start_chrome_debug.sh；"
                "如果仍失败，先确认 9222 没有被其它浏览器/工具占用。"
            )
            error.data = {
                "code": "cdp_context_unsupported",
                "source": "chrome_cdp",
                "cdp": bool(os.environ.get("TAG_COLLECT_CDP_URL")),
                "cdp_url": os.environ.get("TAG_COLLECT_CDP_URL", ""),
                "low_level_error": detail,
            }
            raise error
        raise ServiceError(f"真实页面 RPA 执行失败：{detail}")

    try:
        result = json.loads(stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ServiceError(f"真实页面 RPA 返回格式异常：{stdout or stderr}") from exc

    if not result.get("success"):
        code = str(result.get("code") or "").strip()
        message = str(result.get("message") or "真实页面 RPA 采集失败")
        error = ServiceError(f"{code}: {message}" if code else message)
        error.data = result
        raise error
    return result


def collect_products_from_1688_page(
    query: str,
    limit: int,
    source_url: str = "",
    native_filters: Optional[List[Dict[str, Any]]] = None,
    category_filters: Optional[List[Dict[str, Any]]] = None,
    manual_url_only: bool = False,
    return_meta: bool = False,
    cancel_event: Any = None,
    progress_callback: RpaProgressCallback = None,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """打开或连接真实 Chrome 的 1688 搜索页，从真实页面提取候选商品。"""
    result = _run_node_script(
        "rpa_collect.mjs",
        {
            "query": query,
            "limit": limit,
            "source_url": source_url,
            "native_filters": native_filters or [],
            "category_filters": category_filters or [],
            "manual_url_only": manual_url_only,
        },
        timeout=int(os.environ.get("TAG_COLLECT_RPA_LIST_TIMEOUT") or os.environ.get("TAG_COLLECT_RPA_TIMEOUT", "600")),
        cancel_event=cancel_event,
        progress_callback=progress_callback,
    )
    products = result.get("products", [])
    if return_meta:
        result["products"] = products if isinstance(products, list) else []
        result["filter_results"] = result.get("filter_results", []) if isinstance(result.get("filter_results"), list) else []
        return result
    return products if isinstance(products, list) else []


def collect_detail_fields_from_1688_page(
    url: str,
    item_id: str = "",
    *,
    cancel_event: Any = None,
    progress_callback: RpaProgressCallback = None,
) -> Dict[str, Any]:
    """打开或连接真实 Chrome 的 1688 商品详情页，启发式提取关键核验字段。"""
    result = _run_node_script(
        "rpa_detail.mjs",
        {"url": url, "item_id": item_id},
        timeout=int(os.environ.get("TAG_COLLECT_RPA_DETAIL_TIMEOUT") or os.environ.get("TAG_COLLECT_RPA_TIMEOUT", "420")),
        cancel_event=cancel_event,
        progress_callback=progress_callback,
    )
    fields = result.get("fields", {})
    if not isinstance(fields, dict):
        return {}
    field_evidence = result.get("field_evidence")
    if isinstance(field_evidence, dict):
        fields = dict(fields)
        fields["_field_evidence"] = field_evidence
    return fields
