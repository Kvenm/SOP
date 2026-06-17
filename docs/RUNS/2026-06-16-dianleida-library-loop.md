# Run: 店雷达 1688 选品库对齐与 Loop 接入

## 触发

用户要求前端页面和筛选条件按店雷达 `1688选品库` 全功能设计，功能接口对齐，缺少能力预留接口，并使用 loop-engineering 接入项目。

## 目标

- 接入 Loop Engineering 最小项目文档层。
- 将店雷达选品库筛选拆成可调用接口契约。
- Web 工作台改为 1688 选品库筛选布局。
- 保留真实 1688 页面采集、详情核验、导出和样例测试链路。

## 非目标

- 不绕过 1688 登录、滑块、验证码或安全风控。
- 不伪造店雷达自有数据源字段。
- 不在本轮拆分独立前端工程。

## 文件范围

- `AGENTS.md`
- `docs/LOOP.md`
- `docs/KNOWLEDGE_MAP.md`
- `docs/AGENT_BOUNDARIES.md`
- `docs/TESTING.md`
- `docs/RISKS.md`
- `docs/RUNS/2026-06-16-dianleida-library-loop.md`
- `scripts/capabilities/tag_collect/service.py`
- `scripts/capabilities/tag_collect/web.py`
- `scripts/capabilities/tag_collect/smoke_test.py`

## 验收

- 通过：`python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`python3 scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`git diff --check`
- 通过：本地浏览器打开 `http://127.0.0.1:8765/`，确认页面标题为 `1688选品库筛选工作台`，店雷达分区为 7 个，筛选控件 94 个，无重复 id。
- 通过：开发样例模式选择 `源头工厂`、`48小时`、订单数和批发价范围后点击 `开始查询`，生成 3 条结果，下载链接可见，筛选执行记录显示原生筛选。

## 风险记录

- 店雷达的增长率、采购集中率、关注商品、Temu 铺货等能力当前仅预留接口。
- 真实采集需要用户登录 1688，遇到安全校验必须人工处理。

## 2026-06-16 追加：筛选可用性闭环

### 触发

用户确认当前店雷达式筛选前端就是目标基线，要求继续按该筛选开发，并确认是否仍按 Loop 执行。

### 本轮目标

- 不再改变当前筛选页面主视觉和分区结构。
- 为每个前端筛选字段生成覆盖状态：已接入、部分接入、需详情核验、预留。
- 将覆盖状态返回给 Web 工作台和采集 payload，便于需求评审和导出追踪。
- 详情页核验后，重新评估依赖详情字段的筛选条件，生成筛选重评估记录。

### 本轮结果

- `/api/options` 新增 `library_filter_coverage`。
- Web 工作台新增“筛选覆盖状态”模块，当前统计：已接入 17、部分接入 3、需详情核验 5、预留 11。
- `verify_run_details` 在详情核验后会刷新 `filter_match_records`，并返回 `filter_reevaluation_records`。
- 导出配置 sheet 追加“筛选覆盖状态”和“详情核验后筛选重评估”记录。

### 验收

- 通过：`python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`python3 scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`git diff --check`
- 通过：浏览器刷新 `http://127.0.0.1:8765/`，覆盖状态显示 36 个筛选字段。

### 后续

- 下一轮应使用真实登录 1688 账号逐项跑筛选验收表，记录每个原生筛选是否可点击、是否 not_found、是否需要详情页核验。
- 对预留字段逐项决定：继续预留、接商家页采集、接外部数据源，或从前端隐藏。

## 2026-06-16 追加：类目三级逐级透出

### 触发

用户反馈类目区仍不清晰，要求继续参照店雷达页面，按一级、二级、三级逐级透出。

### 本轮目标

- 类目筛选从纵向嵌套卡片改为店雷达式三级分栏。
- 一级、二级、三级均保持可复选。
- 浏览当前一级/二级不清空已选类目。
- 已选类目必须显示完整路径，继续按 `一级>二级>三级` 传给采集接口。

### 本轮结果

- `scripts/capabilities/tag_collect/web.py` 新增类目级联状态：`activeCategoryParent`、`activeCategoryChild`。
- `renderCategories()` 改为三级分栏渲染：一级类目、二级类目、三级类目。
- 点击类目行只切换当前层级；勾选复选框才写入 `selectedCategories`。
- 已选条支持快速移除，摘要继续显示已选数量和完整路径。
- 搜索类目时会按一级/二级/三级匹配，并定位到命中的当前路径。

### 验收

- 通过：`python3 -m py_compile scripts/capabilities/tag_collect/web.py`
- 通过：`git diff --check`
- 通过：`python3 scripts/capabilities/tag_collect/smoke_test.py`
- 通过：浏览器刷新 `http://127.0.0.1:8765/`，类目面板显示 `一级类目`、`二级类目`、`三级类目` 三列。
- 通过：选择 `女装/女士精品>女式T恤>短袖T恤` 后，摘要显示完整路径，勾选值保持完整路径格式。
- 通过：搜索 `男装` 后，一级列只显示 `男装`，二级列显示男装下级；清空后摘要恢复 `全部类目`。

## 2026-06-17 追加：在线文档问题清单与风控策略

### 触发

用户要求查看金山文档 `1688采集修改`，先列新增问题，再解决；同时参考 GitHub 上 1688 采集项目的风控处理策略。

### 读到的问题

1. 一件代发、七天无理由等 1688 自有筛选项不能拼进搜索词，必须走页面原生筛选。
2. 部分筛选项需要删除或隐藏，避免前端展示但实际不可用。
3. 好评率等指标要细化，尤其好评率需要从商品评论/详情可信来源计算，并按 70%、80%、90% 档位规划。
4. 类目要更细，按一级、二级、三级逐级菜单展示。
5. 1688 风控出现后不能继续自动刷新或反复采集，需要暂停并交给人工处理。

### GitHub 参考结论

- 参考方向：`IjalG/1688-Smart-Scraper` 的“人工正常浏览页面，插件读取已加载商品并导出”的产品模式。
- 可采用能力：人工接管、读取已加载页面、跨页收藏候选池、字段自定义导出、减少自动页面访问次数。
- 不采用方向：验证码绕过、反爬逆向、代理池、高频自动重试、伪装指纹等会增加账号和合规风险的方案。

### 本轮已解决

- 后端新增结构化风控状态：`security_verification_required`、`manual_handoff`、`retryable=false`。
- Web 采集接口遇到风控/滑块/访问拒绝时，不生成 `run_id`、不返回下载链接、不导出空数据。
- 前端遇到风控状态时显示“暂停等待人工接管”提示，要求停止反复刷新，由人工完成登录/验证后再从 URL 或已登录浏览器会话继续。
- 复核现有标签拆分：核心标签 `一件代发`、`七天无理由/7天包退货`、`48小时发货` 等已进入 `native_filters`，不会拼回搜索词。
- 当前页面只开放 `1688选品库` 主功能；左侧其他模块入口和结果页 `关注商品`、`铺货Temu`、`铺货 dry-run` 已置灰为预留，避免误导测试。

### 待确认

- “需要删除的筛选项”尚未给出具体名单。当前先保留字段，并通过覆盖状态标记为“已接入 / 部分接入 / 详情核验 / 预留”，不擅自删除。
- 好评率真实计算仍需详情页/评论页采集能力；当前已支持 70%、80%、90% 桶和详情后重评估，但真实评论页采集还未完全落地。
