#!/usr/bin/env python3
"""标签选品采集命令 — CLI 入口"""

COMMAND_NAME = "tag_collect"
COMMAND_DESC = "标签选品采集"

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..')))

import argparse
from _auth import get_ak_from_env
from _output import print_output, print_error
from capabilities.tag_collect.service import parse_input, run_tag_collect


def main():
    parser = argparse.ArgumentParser(description="1688 标签选品采集")
    parser.add_argument("--categories", default="", help="复选类目标签，逗号分隔，如 女装,家居日用品")
    parser.add_argument("--tags", default="", help="复选运营标签，逗号分隔，如 微信小店,一件代发,48小时发货")
    parser.add_argument("--keywords", default="", help="额外搜索词，逗号分隔；不传则用类目/标签生成")
    parser.add_argument("--exclude-tags", default="", help="排除标签，逗号分隔；命中标题/类目/风险提示/标签来源时过滤")
    parser.add_argument("--max-queries", type=int, default=20, help="最大查询词数量")
    parser.add_argument("--max-items-per-query", type=int, default=20, help="每个查询词最多采集商品数")
    parser.add_argument("--output-format", choices=["xlsx", "csv"], default="xlsx", help="导出格式")
    parser.add_argument("--sample-data", action="store_true", help="使用内置样例数据，不调用 1688 接口")
    parser.add_argument("--serve", action="store_true", help="启动本地 Web 筛选测试工作台")
    parser.add_argument("--host", default="127.0.0.1", help="Web 工作台监听地址")
    parser.add_argument("--port", type=int, default=8765, help="Web 工作台监听端口")
    parser.add_argument("--allow-real", action="store_true", help="允许 Web 工作台关闭样例数据后调用真实 1688 搜索")
    args = parser.parse_args()

    if args.serve:
        from capabilities.tag_collect.web import serve_tag_collect_workbench
        serve_tag_collect_workbench(args.host, args.port, allow_real=args.allow_real)
        return

    if not args.sample_data:
        ak_id, _ = get_ak_from_env()
        if not ak_id:
            print_output(
                False,
                "❌ AK 未配置，无法采集 1688 商品。\n\n可先运行: `cli.py configure YOUR_AK`，或使用 `--sample-data` 验证导出流程。",
                {"run_id": "", "row_count": 0, "output_path": ""},
            )
            return

    try:
        config = parse_input(
            categories=args.categories,
            tags=args.tags,
            keywords=args.keywords,
            exclude_tags=args.exclude_tags,
            max_queries=args.max_queries,
            max_items_per_query=args.max_items_per_query,
            sample_data=args.sample_data,
            output_format=args.output_format,
        )
        result = run_tag_collect(config)
        print_output(result["success"], result["markdown"], result["data"])
    except Exception as e:
        print_error(e, {"run_id": "", "row_count": 0, "output_path": ""})


if __name__ == "__main__":
    main()
