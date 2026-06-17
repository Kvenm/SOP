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

- 店雷达的关注商品、Temu 铺货等能力当前仅预留接口；用户确认不需要的增长率、采购集中率等筛选入口已移除。
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

## 2026-06-17 追加：采集阶段自动详情补字段

### 触发

用户反馈部分字段不在 1688 列表/主页，当前采集逻辑没有自动进入商品详情页，导致运费、品退率、发货率等字段仍停留在待核验。

### 本轮结果

- Web 工作台新增“详情补字段/自动核验”开关，默认开启。
- 新增“自动核验上限”，默认 3 个商品，避免一次性打开大量详情页。
- `run_tag_collect` 在列表初筛后可自动调用 `verify_run_details`，刷新 rows、核验记录、筛选重评估记录和导出文件。
- 真实详情页核验遇到登录/安全滑块/访问被拒绝时，会停止后续详情页访问，避免继续触发风控。
- 仍保留手动“真实详情核验”按钮，用于导出后继续补充剩余高潜商品。

### 验收

- `test_auto_detail_verification_after_collect` 覆盖采集阶段自动补充详情字段。
- Web smoke test 覆盖 `auto_verify_details` 参数、自动核验记录和导出刷新。

## 2026-06-17 追加：多 Agent 评审与详情核验暂停态

### 触发

用户指出“必须评审，估计是没走网关”，要求按多 agent/Loop 机制先评审再继续开发。

### 评审执行

- Codex Workflows 模板 `.codex-workflows/workflows/tag-collect-next.workflow.js` 已通过 validate/preview。
- 之前 workflow run `2026-06-17T03-50-46Z-tag-collect-next-36f313` 的 3 个代理实际启动并回落到 `exec` adapter，请求地址为 `https://aidock.ows.us/responses`，失败原因为 AIDock 网关 502，不是项目 `SKILL.md` 缺失，也不是 workflow 文件格式错误。
- 本轮改用独立多 agent 执行只读评审：
  - Product Agent：结论为“部分满足”，下一片应做真实小批量闭环，防止 `partial_verified/failed` 被误读为可铺。
  - Architect Agent：确认本地 `tag_collect` 服务自身不会返回 502；若页面外层出现 502，应查反代/端口/网关。建议先补状态和字段可信度，不急于前后端大拆。
  - Verification Agent：最高风险是自动详情核验真实失败时仍可能表现为整体成功，需要 Web 和导出明确“列表成功但详情核验暂停”。

### 本轮已解决

- 后端新增统一 `automation_state`：
  - `pending_detail`：列表初筛完成，但仍有高潜商品待详情核验。
  - `paused`：列表采集完成，但详情页遇到登录/滑块/访问拒绝，已停止后续核验。
  - `partial` / `failed` / `verified` / `sample_verified`：用于区分核验完整性和样例模式。
- `verify_run_details`、`run_tag_collect` 和导出 `标签配置` sheet 均写入任务状态、当前阶段、建议动作、暂停原因、核验失败数和待核验数。
- Web 结果页新增“任务状态”和状态说明；详情核验暂停时显示黄色暂停提示，不再以普通成功文案展示。
- 非本机/远程模式下，页面明确提示“远程只读/样例模式”，真实采集仍限制在运行服务的本机。

### 验收

- 通过：`python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py scripts/capabilities/tag_collect/cmd.py`
- 通过：`node --check scripts/capabilities/tag_collect/rpa_collect.mjs`
- 通过：`node --check scripts/capabilities/tag_collect/rpa_detail.mjs`
- 通过：`git diff --check`
- 通过：`python3 scripts/capabilities/tag_collect/smoke_test.py`
- 通过：浏览器刷新 `http://127.0.0.1:8765/`，确认默认真实模式、自动详情核验开启、自动核验上限为 3，并显示“任务状态”区域。

### 未完成

- 真实账号端到端验收仍需用户在真实浏览器中完成登录/验证后小批量执行。
- 真实详情页字段证据还可继续加强：DOM 区域、原始片段、置信度、截图路径等尚未写入字段级记录。

## 2026-06-17 追加：截图框选筛选项接入

### 触发

用户补充两张 1688 页面截图，要求加入框出的筛选：

- 商品详情评价块：商品星级、好评率、评价数、评价标签。
- 搜索页筛选块：价格、起订量、店铺商品数、所在地、商家特色、经营模式、合并供应商，以及严选、分销严选、一件代发、退货包运费、7天无理由退货、24H/48H 发货、官方物流、密文面单、晚揽必赔、24H/48H 支揽率等。

### 多 Agent 结果

- Product/Architect Agent 只读复核：确认勾选类筛选应进入 `native_filters`，详情评价字段必须进入详情页核验，店铺商品数等不稳定字段应标记预留或部分支持。
- Builder Agent：落地后端筛选 schema、筛选计划、RPA 原生筛选动作和详情页评价字段解析。
- Reviewer Agent：只读审查 diff 和测试结果。

### 本轮已解决

- `LIBRARY_FILTER_SCHEMA` 新增平台服务、履约服务、所在地、商家特色、经营模式、合并供应商和评价口碑筛选。
- 新增/扩展 `NATIVE_FILTER_SPECS`：严选、分销严选、退货包运费、7天无理由退货、24H/48H 发货、官方物流、密文面单、晚揽必赔、24H/48H 支揽率、合并供应商等。
- 原生筛选去重改为按 key 合并别名，避免 7天包退货/7天无理由退货这类同义筛选互相覆盖。
- RPA 搜索页筛选支持 `dropdown_option`，用于所在地、商家特色、经营模式这类“先展开再选择”的筛选。
- 详情页 RPA 补充解析 `product_rating`、`good_rate`、`comment_count`、`review_tags`。
- 导出字段新增 `5.10 商品星级`、`5.11 评价标签`，核验记录也会写入这些字段。
- 评价筛选遵循“列表先保留，详情核验后重评估”，不会在未核验前误删候选商品。

### 验收

- 通过：`python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`python3 scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`git diff --check`

### 残余风险

- 店铺商品数当前保留为预留字段，未作为真实过滤条件执行。
- 所在地、商家特色、经营模式依赖 1688 当前页面 DOM 和下拉展示，RPA 会限制在筛选控件附近点击并记录 clicked/not_found/click_failed，不能把 `not_found` 当作筛选成功。
- 商品星级、评价标签等真实提取依赖详情页文本是否可见；遇到登录、滑块、访问拒绝仍会暂停并交给人工处理。滚动后二次出现的风控页也会再次识别。

### Reviewer 修复

- 详情核验后若商品不满足星级/评价标签等后置筛选，会从主 `rows` 移入 `filter_excluded_rows`，导出“选品结果”不再保留该商品。
- `good_rate/comment_count` 只有在详情核验证据中成功提取后，才可用于详情后置筛选；列表 stats 不再冒充详情核验结果。
- 搜索页下拉筛选点击限制在筛选控件附近，降低误点商品卡片文本的风险。
- 详情页滚动/懒加载后重新读取页面文本时，会再次判断登录、滑块、验证码和访问拒绝。
