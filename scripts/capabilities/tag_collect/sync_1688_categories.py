#!/usr/bin/env python3
"""Sync 1688 homepage left navigation categories into category_dict.json."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.request import Request, urlopen


HOME_URL = "https://www.1688.com/"
OUTPUT_PATH = Path(__file__).resolve().parent / "category_dict.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"\s+", "_", value.strip().lower())
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def _node_id(prefix: str, title: str, index: int) -> str:
    return f"{prefix}_{index}_{_slug(title, 'category')}"


def _title(node: Dict[str, Any]) -> str:
    return str(node.get("title") or node.get("name") or "").strip()


def _convert_nodes(nodes: Iterable[Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for index, node in enumerate(nodes, 1):
        title = _title(node)
        if not title:
            continue
        item: Dict[str, Any] = {
            "id": _node_id(prefix, title, index),
            "name": title,
        }
        children = [
            child for child in node.get("children", [])
            if isinstance(child, dict) and _title(child)
        ]
        if children:
            item["children"] = _convert_nodes(children, f"{prefix}_{index}")
        result.append(item)
    return result


def _extract_window_data(html: str) -> Dict[str, Any]:
    match = re.search(r"window\.\$data=(\{.*?\});\s*window\.pageData=", html, re.S)
    if not match:
        raise ValueError("未在 1688 首页 HTML 中找到 window.$data")
    return json.loads(match.group(1))


def _extract_l1s(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    page = data.get("page") if isinstance(data.get("page"), dict) else {}
    for value in page.values():
        if not isinstance(value, dict):
            continue
        tree_data = value.get("treeData")
        if isinstance(tree_data, dict) and isinstance(tree_data.get("l1s"), list):
            return [
                item for item in tree_data["l1s"]
                if isinstance(item, dict) and _title(item)
            ]
    raise ValueError("未在 1688 首页数据中找到 treeData.l1s")


def fetch_homepage() -> str:
    request = Request(HOME_URL, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urlopen(request, timeout=25) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, "replace")


def build_dictionary(html: str) -> Dict[str, Any]:
    data = _extract_window_data(html)
    tree = _convert_nodes(_extract_l1s(data), "l1")
    if not tree:
        raise ValueError("1688 首页类目为空，未写入 category_dict.json")
    return {
        "version": f"1688-home-left-nav-{datetime.now().strftime('%Y-%m-%d')}",
        "source": "https://www.1688.com/ window.$data.page.*.treeData.l1s",
        "status": "synced_from_1688_homepage_left_nav",
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "homepage_url": HOME_URL,
        "tree": tree,
    }


def main() -> int:
    html = fetch_homepage()
    dictionary = build_dictionary(html)
    OUTPUT_PATH.write_text(
        json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    l2_count = sum(len(item.get("children", [])) for item in dictionary["tree"])
    l3_count = sum(
        len(child.get("children", []))
        for item in dictionary["tree"]
        for child in item.get("children", [])
    )
    print(
        f"synced {len(dictionary['tree'])} l1 / {l2_count} l2 / {l3_count} l3 "
        f"to {OUTPUT_PATH}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
