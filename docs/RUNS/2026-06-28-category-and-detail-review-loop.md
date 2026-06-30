# 2026-06-28 Category And Detail Review Loop

## Trigger

用户反馈：

- 本地选择类目后，1688 页面仍像是在搜索框搜索，担心不是点击 1688 左侧类目。
- 导出的 Excel 很多字段为空或待核验，希望减少人工复核，尽量自动补字段。

## Agent Roles

- 采集链路审查 agent：只读审查 UI 到 RPA 的类目数据流，确认 URL 残留和类目点击确认不足是主要风险。
- 导出与自动核验审查 agent：只读审查用户导出的 `tag_collect_20260628_154726_027.xlsx` 和快照，确认 5 条中 3 条真实详情核验为 `partial_verified`，字段级失败 58 条。
- 主控 agent：实现修复、集成验证、更新文档。

## Changes

- 自动批量 RPA 且已选类目时，前端 `sourceUrls` 使用有效值函数，后端 `parse_input()` 同步清空 URL 残留，避免旧搜索页 URL 抢走类目入口。
- RPA 类目导航增加结果页确认：记录一级/二级/三级 `hover/click`、每步 URL 前后变化、最终 `navigation_state`；新增 `not_confirmed` 阻断状态。
- 筛选执行记录前端展示点击链路，便于区分“1688 类目跳转生成 keywords”与“项目输入搜索框”。
- 自动详情核验默认上限从 3 调到 5。
- 详情页抽取从整页文本正则扩展为结构化脚本、页面文本、店铺链接 DOM 组合，修复店铺名误抓“回头率”的问题。
- 导出 `标签配置` sheet 增加自动详情核验开关、自动核验上限、字段级失败数。

## Validation

通过：

```bash
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
env PYTHONPYCACHEPREFIX=/private/tmp/pycache-1688-shopkeeper python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/rpa.py scripts/capabilities/tag_collect/smoke_test.py scripts/capabilities/tag_collect/sync_1688_categories.py
env PYTHONPYCACHEPREFIX=/private/tmp/pycache-1688-shopkeeper python3 scripts/capabilities/tag_collect/smoke_test.py
git diff --check
```

真实低频验证：

```bash
env TAG_COLLECT_CDP_URL=http://127.0.0.1:9222 TAG_COLLECT_RPA_MAX_PAGES=1 TAG_COLLECT_RPA_PACING=fast TAG_COLLECT_RPA_PAGE_TIMEOUT_MS=25000 TAG_COLLECT_RPA_TIMEOUT=90 python3 cli.py tag_collect --collect-source rpa --categories '配饰、鞋、箱包>围巾/防晒>防晒面纱面罩' --max-items-per-query 1 --output-format xlsx
```

结果：

- 批次：`20260628_161401_258`
- `queries=[""]`
- `source_urls=[]`
- 类目执行：`配饰、鞋、箱包` hover、`围巾/防晒` hover、`防晒面纱面罩` click
- 最终页：`https://s.1688.com/selloffer/offer_search.htm?...keywords=防晒面纱面罩`
- 说明：该 `keywords` 是 1688 点击类目后的结果页参数，不是项目输入搜索框。

真实详情核验：

- 同批次详情核验 1 条。
- 行状态：`partial_verified`
- 已补：店铺名、店铺链接、发货率、发货时效、是否一件代发、是否支持退换。
- 未稳定补：运费、品退率、SKU 等仍为待详情页核验。
- 字段级失败数：20。

## Remaining Risk

- 1688 某些详情字段不一定在当前账号、当前页面或当前商品上展示；系统只能自动抽取已展示且可稳定识别的字段。
- 不绕过登录、滑块、验证码或风控；遇到平台验证仍需人工处理。
- 人工复核仍保留为最终业务判断，不等于让人工逐条补所有字段。
