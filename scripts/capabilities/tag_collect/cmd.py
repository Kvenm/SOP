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
from capabilities.tag_collect.service import friendly_collect_error, parse_input, run_tag_collect


def main():
    parser = argparse.ArgumentParser(description="1688 标签选品采集")
    parser.add_argument("--categories", default="", help="复选类目标签，逗号分隔，如 女装,家居日用品")
    parser.add_argument("--tags", default="", help="复选运营标签，逗号分隔，如 微信小店,一件代发,48小时发货")
    parser.add_argument("--keywords", default="", help="额外搜索词，逗号分隔；不传则用类目/标签生成")
    parser.add_argument("--source-urls", default="", help="直接采集真实 1688 搜索页或商品详情页 URL，逗号分隔；用于登录受阻时测试公开页面真实数据")
    parser.add_argument("--exclude-tags", default="", help="排除标签，逗号分隔；命中标题/类目/风险提示/标签来源时过滤")
    parser.add_argument("--max-queries", type=int, default=20, help="最大查询词数量")
    parser.add_argument("--max-items-per-query", type=int, default=20, help="每个查询词最多采集商品数")
    parser.add_argument("--output-format", choices=["xlsx", "csv"], default="xlsx", help="导出格式")
    parser.add_argument("--collect-source", choices=["rpa", "api"], default="rpa", help="真实采集来源：rpa=打开真实1688页面；api=AK接口")
    parser.add_argument("--auto-verify-details", action="store_true", help="采集后自动进入详情页核验高潜商品关键字段")
    parser.add_argument("--auto-verify-max-items", type=int, default=3, help="自动详情核验商品上限，默认 3")
    parser.add_argument("--sample-data", action="store_true", help="使用内置样例数据，不调用 1688 接口")
    parser.add_argument("--serve", action="store_true", help="启动本地 Web 筛选测试工作台")
    parser.add_argument("--host", default="127.0.0.1", help="Web 工作台监听地址")
    parser.add_argument("--port", type=int, default=8765, help="Web 工作台监听端口")
    parser.add_argument("--allow-real", dest="allow_real", action="store_true", default=True, help="允许 Web 工作台调用真实 1688 页面/接口，默认开启")
    parser.add_argument("--no-real", dest="allow_real", action="store_false", help="关闭 Web 工作台真实采集，仅保留开发样例模式")
    args = parser.parse_args()

    if args.serve:
        from capabilities.tag_collect.web import serve_tag_collect_workbench
        serve_tag_collect_workbench(args.host, args.port, allow_real=args.allow_real)
        return

    if not args.sample_data and args.collect_source == "api":
        ak_id, _ = get_ak_from_env()
        if not ak_id:
            print_output(
                False,
                "❌ AK 未配置，无法通过 API 采集 1688 商品。\n\n可先运行: `cli.py configure YOUR_AK`，或使用默认 `--collect-source rpa` 打开真实页面采集。",
                {"run_id": "", "row_count": 0, "output_path": ""},
            )
            return

    try:
        config = parse_input(
            categories=args.categories,
            tags=args.tags,
            keywords=args.keywords,
            source_urls=args.source_urls,
            exclude_tags=args.exclude_tags,
            max_queries=args.max_queries,
            max_items_per_query=args.max_items_per_query,
            sample_data=args.sample_data,
            output_format=args.output_format,
            collect_source=args.collect_source,
            auto_verify_details=args.auto_verify_details,
            auto_verify_max_items=args.auto_verify_max_items,
        )
        result = run_tag_collect(config)
        print_output(result["success"], result["markdown"], result["data"])
    except Exception as e:
        print_output(False, f"❌ {friendly_collect_error(e)}", {"run_id": "", "row_count": 0, "output_path": ""})


if __name__ == "__main__":
    main()
