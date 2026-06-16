#!/usr/bin/env python3
"""1688 页面 RPA 采集适配层。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from _errors import ServiceError


def _script_path(name: str) -> str:
    return str(Path(__file__).resolve().parent / name)


def _node_bin() -> str:
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        raise ServiceError("未找到 Node.js，无法执行真实页面 RPA。请先安装 Node.js，并安装 playwright。")
    return node


def _run_node_script(script_name: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    script = _script_path(script_name)
    try:
        proc = subprocess.run(
            [_node_bin(), script],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(f"真实页面 RPA 超时：{exc}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or f"exit={proc.returncode}"
        raise ServiceError(f"真实页面 RPA 执行失败：{detail}")

    try:
        result = json.loads(stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ServiceError(f"真实页面 RPA 返回格式异常：{stdout or stderr}") from exc

    if not result.get("success"):
        code = str(result.get("code") or "").strip()
        message = str(result.get("message") or "真实页面 RPA 采集失败")
        raise ServiceError(f"{code}: {message}" if code else message)
    return result


def collect_products_from_1688_page(
    query: str,
    limit: int,
    source_url: str = "",
    native_filters: Optional[List[Dict[str, Any]]] = None,
    return_meta: bool = False,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """打开或连接真实 Chrome 的 1688 搜索页，从真实页面提取候选商品。"""
    result = _run_node_script(
        "rpa_collect.mjs",
        {"query": query, "limit": limit, "source_url": source_url, "native_filters": native_filters or []},
        timeout=int(os.environ.get("TAG_COLLECT_RPA_TIMEOUT", "180")),
    )
    products = result.get("products", [])
    if return_meta:
        result["products"] = products if isinstance(products, list) else []
        result["filter_results"] = result.get("filter_results", []) if isinstance(result.get("filter_results"), list) else []
        return result
    return products if isinstance(products, list) else []


def collect_detail_fields_from_1688_page(url: str, item_id: str = "") -> Dict[str, Any]:
    """打开或连接真实 Chrome 的 1688 商品详情页，启发式提取关键核验字段。"""
    result = _run_node_script(
        "rpa_detail.mjs",
        {"url": url, "item_id": item_id},
        timeout=int(os.environ.get("TAG_COLLECT_RPA_TIMEOUT", "180")),
    )
    fields = result.get("fields", {})
    return fields if isinstance(fields, dict) else {}
