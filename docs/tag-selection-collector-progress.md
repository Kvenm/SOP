# 标签选品采集工作台进度记录

## 最新状态：真实数据测试口径

记录时间：2026-06-28 16:15 CST

用户已明确要求测试时必须使用真实 1688 页面和真实数据，不再以样例数据作为默认验收链路。本项目已切换为：

- Web 工作台默认真实数据模式，通过 Playwright/RPA 打开 1688 搜索页采集候选商品。
- 标签已拆成四类：搜索词、1688 页面原生筛选、后置指标区间筛选、系统规则；`一件代发`、`48小时发货`、`7天包退货/七天无理由` 不再静默拼进搜索词。
- RPA 会在 1688 搜索页尝试点击/勾选原生筛选项，并把 `clicked`、`not_found`、`click_failed`、`not_applicable` 写入页面提示、快照和导出表。
- 指标区间已细化：好评率、品退率、发货率、评论数、复购率、近30天销量、代发订单量均有区间标签；页面读不到的字段保持待核验或人工复核，不猜值。
- Web 类目区已支持一级/二级/三级路径展示，并引入可追溯类目字典文件 `scripts/capabilities/tag_collect/category_dict.json`。2026-06-28 已通过 `sync_1688_categories.py` 从 1688 首页公开 `treeData.l1s` 同步，当前为 10 个组合一级、155 个二级、1919 个三级；这不是开放平台“官方全量类目库”，但已和当前首页左侧导航对齐。
- 如遇登录、扫码或安全验证，需要用户在弹出的浏览器中处理；如果处理后仍失败，RPA 会明确返回 `login_required` 或 `security_verification_required`，不会绕过滑块/验证码，也不会继续采集或导出不可信数据。
- 用户提供的店雷达 `.xls` 附件原表头已核对为 37 列；当前 `选品结果` 导出顺序锁定为“店雷达 37 列基线在前 + 项目扩展字段在后”，共 84 列。新增 `数据模式` 和 `数据真实性说明`，用于区分真实采集与开发样例。smoke test 会校验列数、列名和顺序，避免后续字段漂移。
- Web 和 CLI 已支持直接传入 1688 搜索页/商品详情页 URL，用于账号登录受阻时测试公开页面真实数据解析。
- 类目采集已改为严格门禁：本地选择类目后，路径必须先存在于当前 1688 首页导航字典；真实 1688 必须按类目入口逐级命中完整路径；`category_path_invalid`、`partial_clicked`、`not_found`、`click_failed`、`not_reported` 均会阻断采集和导出，不再把宽泛父类目结果当作目标类目结果。
- 2026-06-28 已复核并删除类目失败后的关键词 fallback；自动批量模式不会再把末级类目词塞进搜索框继续采集。类目失败会返回 `category_navigation_not_loaded`、筛选执行记录、命中层级和页面可见类目诊断，并保存本地失败快照。
- 2026-06-30 已强化样例隔离和真实来源门禁：样例行强制标注 `数据模式=开发样例`，导出增加 `样例说明` sheet，样例复核状态显示 `样例预通过/样例不建议`；只有能证明来自真实页面采集或真实详情核验的行才标 `真实数据`，否则标 `来源未知/需核验`。
- 2026-06-28 追加修复：自动批量采集且已选择类目时，Web 和后端都会忽略 URL 输入框残留，强制从 1688 首页类目入口执行，避免旧搜索页 URL 抢走入口。直接 URL 读取仍可通过不选类目或切换兜底/URL 模式使用。
- 2026-06-28 追加修复：RPA 类目执行记录会保留一级/二级/三级 `hover/click` 链路、每步 URL 前后变化和最终结果页确认状态；`not_confirmed` 会阻断导出，避免“看似点击但未确认进入目标类目结果页”的错数据。
- 多类目复选已拆成独立 RPA 任务执行，每个类目单独从 1688 首页/类目入口开始采集，避免多个类目在同一页面会话里连续点击导致结果串类。
- “真实详情核验”会打开真实商品详情页，启发式提取运费、品退率、发货率等关键字段。
- `partial_verified` 表示真实详情页只提取到部分字段；缺失字段必须人工复核。
- 2026-06-28 追加修复：详情抽取从纯整页文本正则扩展为“结构化脚本 + 页面文本 + 店铺链接 DOM”组合，修复店铺名称误抓成“回头率”的问题，并增强店铺链接、发货率、发货时效、一件代发、7天无理由等字段提取。
- 自动详情核验默认上限从 3 条调整为 5 条，和 Web 默认每词商品上限一致；导出 `标签配置` sheet 现在写入自动核验开关、核验上限、字段级失败数，避免把 `partial_verified` 误读为全部字段可信。
- 2026-06-28 16:45 追加修复：新增“目标可铺数量”口径。`target_publishable_count` 表示希望拿到的可投放商品数，`max_items_per_query` 保留为候选扫描预算；Web 页面上的“目标可铺数”会传入后端。
- 目标达标数量按 `wechat_shop_suggestion == 可铺` 统计；`谨慎` 只作为可观察候选复核，不计入“满足投放数量”。返回与导出新增 `publishable_count`、`publishable_candidate_count`、`candidate_count`、`candidate_scan_limit`、`collection_stop_reason`、`shortfall_reason`。
- 同一轮采集会适度放大候选扫描预算，尽量采够目标可铺数；但遇到登录/滑块/访问拒绝/类目失败仍会停止，不做验证码绕过、不做高频自动重试。
- 新增本地历史筛除池 `{TAG_COLLECT_DATA_DIR}/rejected_products.json`。列表硬筛失败、排除标签命中、详情后筛选失败、详情核验后明确“不建议”的商品会记录原因；下次同一筛选签名下会先跳过，减少重复详情核验和重复人工判断。
- 详情核验后会重评估筛选并重算推荐分、推荐等级和微信小店建议，生成 `rescore_records`。详情字段触发的品退率/发货率等风险会降低推荐，不再沿用列表初始分。
- 历史筛除池只记录明确不合格商品；详情页风控、登录失败、字段没提取到不视为商品不合格，不会写入排除池。
- `--sample-data` 和 `sample_verified` 仅保留给开发回归测试，不能作为正式真实数据验收结果。
- 新增 RPA 文件：`scripts/capabilities/tag_collect/rpa.py`、`rpa_collect.mjs`、`rpa_detail.mjs`。
- 新增 Node 依赖描述：`package.json`，真实页面采集前需执行 `npm install` 和 `npm run install-browsers`。

以下内容保留为历史记录，用于追踪此前 MVP 如何演进；遇到“默认样例”描述时，以本节最新状态为准。

记录时间：2026-06-14 09:49:15 CST
更新：已完成第二阶段第一片“P0/P1 高潜详情核验队列 + 样例详情核验 + 字段级核验记录 + Web 核验按钮 + 导出核验记录 sheet”。
暂停记录：本轮验收后已停止本地 `127.0.0.1:8765` Web 工作台进程。

## 当前阶段

已完成第一版 Web 筛选测试工作台 MVP、真实页面 RPA 采集、详情核验第一版，以及本轮“标签意图拆分 + 1688 原生筛选动作记录 + 指标区间初筛 + 三级类目入口”。当前可作为“标签复选 + 原生筛选点击记录 + 指标区间 + P0/P1 高潜核验队列 + 字段编号 + Excel 导出”的测试基线；后续重点是补完整官方 1688 类目字典和更多真实页面字段样本。

本轮按用户要求补充使用 GitHub 前端设计 skill：

- 已参考公开 Agent Skills 仓库中的 `frontend-design` 方法。
- 已继续使用 `avoid-ai-design` 做“去模板化 UI”审查。
- 设计方向定为“Apple 式安静工作台”：浅灰背景、白色半透明工作面板、系统字体、蓝色主操作、清晰一级/二级筛选层级，保持工具属性和数据密度，不做营销式 hero。

本轮已按多 agent 流程执行：

- 产品 agent：完成需求验收分析，指出字段编号、详情核验、导出工作簿、EXCLUDE/AND/OR、多 agent 证据等问题。
- 架构 agent：完成 capability 架构评审，指出真实采集 gate、token/CSRF、服务端限额、CLI discover 风险等问题。
- 文档 worker：补齐 SKILL/README/PRD/reference 文档。
- 验证/风控 agent：已重新启动并完成只读验收，结论为“第一版基线基本通过，可以作为 Web 筛选测试工作台 MVP 验收”。
- 继续执行验证/风控子 agent：复核后判定无阻塞问题，但指出 P1 风险：部分详情字段可能被列表 `stats` 或标签推导提前填值，容易被误读为可信数据。本轮已修正为详情页核验前统一显示“待详情页核验”。
- 已新增可复用 workflow 草稿：`.codex-workflows/workflows/tag-collect-next.workflow.js`，用于下一阶段产品、架构、验证三角色并行评审。当前尚未启动执行，明天可先 validate/preview/run 后再继续编码。
- 2026-06-14 已运行 `.codex-workflows/workflows/tag-collect-next.workflow.js` 多 agent 评审：
  - 产品 agent：确认下一片应做 P0/P1 详情核验队列和字段级证据模型，不应先拆前后端或多人化。
  - 验证 agent：补充要求 Web rows 保持未核验占位、Web 下载端到端、未核验提示必须可见。
  - 架构 agent：原始输出可读但 JSON 校验失败；结论同样建议先在 `tag_collect.service` 补服务层契约，Web 只做薄接口。
- 2026-06-16 已继续运行 `.codex-workflows/workflows/tag-collect-next.workflow.js` 多 agent 评审：
  - 产品 agent：确认下一片必须先做“标签意图拆分 + 1688 原生筛选点击 + 指标区间可见化”。
  - 架构 agent：建议保留现有单体 Web 工作台，不急于前后端拆分；先补 `filter_plan`、RPA `filter_results`、字段证据和导出兼容。
  - 验证 agent：要求 smoke 覆盖原生筛选不进搜索词、筛选失败提示、区间字段、导出记录。
- 2026-06-16 已针对用户截图和附件表再次运行多 agent 评审：
  - 产品 agent：确认滑块/验证码失败是阻断级问题，不能继续解析、不能生成导出、不能回退样例。
  - 验证 agent：要求补安全验证失败回归测试，并锁定 `选品结果` 表头顺序。
  - 架构 agent：本次 workflow 因输出格式未满足 JSON contract 标记失败，未采纳其结果；已按产品/验证 agent 的一致结论落地。

## 已实现

### 新增能力

新增 `tag_collect` capability：

- `scripts/capabilities/tag_collect/__init__.py`
- `scripts/capabilities/tag_collect/cmd.py`
- `scripts/capabilities/tag_collect/service.py`
- `scripts/capabilities/tag_collect/web.py`

命令入口：

```bash
python3 cli.py tag_collect --sample-data --categories "女装/女士精品" --tags "微信小店" --output-format xlsx
python3 cli.py tag_collect --serve --port 8765
```

Web 工作台默认面向本机真实 1688 页面采集；开发样例需要在页面里显式开启。真实采集仅建议在本机地址使用，登录或验证由用户在浏览器中手动完成。

### Web 工作台

已实现本地 Web 页面：

- 类目标签复选，含一级/二级/三级路径树。
- 运营筛选标签复选，并拆分为 1688 原生筛选、指标区间、平台/系统规则等意图。
- 搜索词、排除标签、查询词上限、每词商品上限。
- 开发样例需要显式开启；正式测试默认真实数据。
- 结果表格筛选：标题/类目、最低分、推荐等级、核验状态、微信小店规则预判。
- 字段编号页。
- 下载导出文件。
- 本地 token 校验，无 token 的 POST 会拒绝。
- 本机 Web 默认可进行真实采集；局域网访问适合查看页面和导出，不建议暴露真实采集能力。
- 已完成 smoke 验收：复选 `女装/女士精品`、`微信小店`、`一件代发`、`48小时发货`、`好评率>=90%` 后，`一件代发/48小时发货` 进入原生筛选计划，不再拼进查询词。
- 已新增结果页“样例核验高潜”按钮：
  - 采集后展示待核验高潜、已核验商品、核验记录数量。
  - 默认仅核验 P0/P1 且关键详情字段存在缺口的商品。
  - 样例核验不会调用真实 1688，不需要登录账号。
  - 核验后表格中的批发运费、代发运费、品退率、发货率、月代发订单等字段会从“待详情页核验”更新为样例核验值。
  - 结果页展示字段核验记录摘要，含字段名、商品 ID、值、来源、状态、核验时间。
- 已完成视觉重设：
  - 顶部操作区改为轻量 sticky toolbar，按钮改为 Apple 风格胶囊按钮。
  - 左侧类目区突出“一级筛选 / 二级筛选”，选中一级类目有明确高亮。
  - 运营标签区改为分组筛选块，并显示已选数量。
  - 结果表格改为字段级固定列宽，商品标题和类目横向正常换行，避免标题被挤成竖排。
  - 移动端左侧类目区限制高度并独立滚动，避免任务区被大量类目推到过深位置。

### 字段体系

字段编号已改为 PRD 的 1-10 分组体系：

- `1.1` 到 `10.11`
- 字段编号页保持 1-10 分组；导出 `选品结果` 表头按店雷达附件原表 37 列优先排序，后接项目扩展字段，共 82 列。
- 字段定义同源用于 Web 字段页、导出列、字段说明 sheet。
- 已包含用户重点字段：商品链接、店铺链接、主图链接、SKU数量、批发运费、代发运费、品退率、24 小时揽收率、发货率、发货时效、是否一件代发、核验状态、人工复核状态，以及好评率/品退率/发货率/评论数/复购率/近30天销量/代发订单量区间字段。

注意：关键字段当前仍为“待详情页核验”，没有伪装成可信数据。本轮已将详情页核验字段统一收敛到 `DETAIL_ONLY_FIELDS`，包括起批范围、代发价、批发运费、代发运费、品退率、发货率、发货时效、是否一件代发、面单支持、24 小时揽收率、月代发订单、SKU数量、店铺链接。

### 导出

已支持：

- `.xlsx`，默认导出格式。
- `.csv`，仅导出 `选品结果` 明细，不包含审计类工作表。

`.xlsx` 是真正的工作簿 zip 格式，真实采集默认包含 6 个工作表；开发样例或混有样例行时，会额外包含 `样例说明`：

- `选品结果`
- `字段说明`
- `标签配置`
- `异常复核`
- `核验记录`
- `筛选执行记录`
- `样例说明`（仅开发样例或混有样例行时出现）

### 风险隔离

已完成：

- 样例数据模式不依赖 `requests`，本地无依赖也能跑。
- 真实采集才懒加载现有 `search` capability。
- 服务端强制限额：`MAX_QUERIES = 50`，`MAX_ITEMS_PER_QUERY = 50`。
- Web POST 必须带服务端 token。
- Web 真实采集默认按本机安全边界运行；局域网访问不建议开放真实采集。
- `exclude_tags` 已在 MVP 中实际生效，会匹配标题、类目、风险提示、标签来源等并过滤。
- 过滤规则会写入快照和 `标签配置` sheet。
- 1688 原生筛选执行结果会写入快照、Web 筛选记录和 `筛选执行记录` sheet。

## 已验证

已通过：

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/pycache-1688-shopkeeper \
  python3 -m py_compile scripts/capabilities/tag_collect/service.py \
  scripts/capabilities/tag_collect/web.py \
  scripts/capabilities/tag_collect/cmd.py cli.py
```

已通过 CLI 样例采集：

```bash
python3 cli.py tag_collect --sample-data --categories "女装/女士精品" --tags "微信小店" --output-format xlsx
```

已验证：

- `tag_collect` 能被 CLI 发现。
- 样例采集成功生成 `.xlsx` 和 JSON 快照。
- `exclude-tags "红海"` 会过滤样例中的红海商品。
- `.xlsx` 文件可作为 zip 打开，并包含 6 个 sheet。
- 字段说明中可找到 `10.8`、品退率、发货率。
- Web `/api/options` 返回 token、限额、字段编号。
- 无 token 调 `/api/collect` 返回 403。
- 带 token 调 `/api/collect` 成功生成样例结果。
- `/download?run_id=...` 用 GET 可下载 xlsx，响应类型为 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`。
- 已新增并通过 `scripts/capabilities/tag_collect/smoke_test.py`，覆盖字段编号、xlsx sheet、标签意图拆分、指标区间、token 拒绝、样例 API 成功、真实采集未授权阻止、exclude 过滤。
- 已补充 smoke test 断言：`DETAIL_ONLY_FIELDS` 在详情页核验前必须保持“待详情页核验”，`verification_status` 保持 `unverified`。
- 已补充 smoke test 断言：
  - `DETAIL_ONLY_FIELDS` 在详情页核验前必须保持“待详情页核验”，`verification_status` 保持 `unverified`。
  - 采集后必须生成 P0/P1 详情核验队列。
  - 样例详情核验后必须生成字段级核验记录。
  - 核验后批发运费、品退率、发货率不再是占位值。
  - Web `/download?run_id=...` 端到端返回可打开的 xlsx，并包含 `核验记录` sheet。
  - `一件代发/48小时发货` 不进入查询词，而进入 `native_filters`。
  - `好评率>=90%/评论数30-99` 等进入 `post_filters`。
  - 样例导出包含 `筛选执行记录` sheet，原生筛选状态为 `sample_skipped`。
  - 样例导出包含 `样例说明` sheet，结果行必须包含 `开发样例` 和禁止作为真实选品依据的说明。
- 已完成浏览器交互检查，本地截图在 `output/playwright/`，不随 Git 提交：
  - 首页/类目树截图：`output/playwright/tag_collect_initial.png`
  - 结果页截图：`output/playwright/tag_collect_results.png`
  - 字段编号页截图：`output/playwright/tag_collect_fields.png`
- 已完成新版视觉截图，本地截图在 `output/playwright/`，不随 Git 提交：
  - 筛选任务页：`output/playwright/tag_collect_redesign_task.png`
  - 结果表格页：`output/playwright/tag_collect_redesign_results.png`
  - 字段编号页：`output/playwright/tag_collect_redesign_fields.png`
  - 移动端页：`output/playwright/tag_collect_redesign_mobile.png`
- 已完成新版桌面验收：
  - 字段编号项：72 个。
  - 结果表格：3 条样例结果。
  - 表格 CSS：`table-layout: fixed`，`min-width: 1780px`。
  - 商品标题列：360px，中文标题正常横向换行。
  - 控件文本溢出：0。
- 已完成新版移动端验收：
  - 390px 宽度下无控件文本溢出。
  - 左侧类目区高度限制为 560px。
  - 工作区入口从约 18617px 前移到约 686px。
- 浏览器 console 检查无 error/warning。
- 已完成第二阶段页面级验收：
  - 启动 `python3 cli.py tag_collect --serve --port 8765`。
  - 选择 `微信小店`、`一件代发`、`48小时发货` 后运行采集。
  - 采集结果：3 条商品，3 个待核验高潜，关键字段仍为“待详情页核验”。
  - 点击“样例核验高潜”后：3 个商品变为 `sample_verified`，核验队列清空，生成 69 条字段级核验记录。
  - 首行样例核验结果：批发运费 `首重8元，续重4元`，品退率 `1.8%`，发货率 `98.6%`。
  - 导出文件确认含 5 个 sheet，且包含 `核验记录` sheet。

## 当前未完成

下一阶段优先做：

1. 真实详情页核验接入：
   - 调用 `prod_detail` 读取真实详情快照。
   - 或通过 Playwright/RPA 打开 1688 详情页，让用户扫码登录后低频核验。
   - 为真实详情数据建立字段白名单映射，避免未知结构误写入核心字段。
   - 将真实核验状态从当前 `sample_verified` 扩展到 `verified` / `failed` / `partial_verified`。
2. 评估是否要把 `--serve` 输出改成标准 JSON 或保持本地开发入口文本输出。
3. 如果要多人使用，需要把本地 token/单机数据目录升级为用户、任务、权限、队列和审计模型。

补充决策：

- 未完成部分不要求立刻前后端分离；当前优先级是补齐详情核验队列、字段可信度和导出状态。
- RPA/Playwright 应作为详情页核验工具，而不是主采集引擎；候选商品采集优先走 AK/API 或现有 `search` capability，高潜商品再低频进入 RPA 核验。
- 样例模式可测试的“核验队列 + 样例核验结果 + 字段来源/状态”已完成；下一步再接真实 Playwright 登录和 1688 详情页解析。

## 当前风险

- 当前 `.xlsx` 生成器是标准库实现，功能够用但样式较基础。
- 真实采集依赖现有 `search` capability 和环境中的 `requests`，当前本机裸 Python 未安装 `requests`，但样例模式不受影响。
- AND/OR 当前是 MVP 规则说明和查询词生成，不是完整规则引擎；EXCLUDE 已实际生效。
- 当前仅实现样例详情核验；真实 1688 详情页核验还未接入。`sample_verified` 只能证明流程通，不等于真实平台数据可信。
- Web token 通过 `/api/options` 返回，适合本地工作台，不适合直接作为多人服务认证方案。
- Playwright CLI wrapper 受当前网络限制无法从 npm 拉取 `@playwright/cli`，本轮改用 Codex 内置 Browser 完成页面验收。

## 当前改动文件

- `.gitignore`
- `README.md`
- `SKILL.md`
- `scripts/_const.py`
- `docs/tag-selection-collector-prd.md`
- `docs/tag-selection-collector-progress.md`
- `references/capabilities/tag_collect.md`
- `scripts/capabilities/tag_collect/__init__.py`
- `scripts/capabilities/tag_collect/cmd.py`
- `scripts/capabilities/tag_collect/service.py`
- `scripts/capabilities/tag_collect/web.py`
- `scripts/capabilities/tag_collect/smoke_test.py`
- `.codex-workflows/workflows/tag-collect-next.workflow.js`
- `output/playwright/tag_collect_initial.png`
- `output/playwright/tag_collect_results.png`
- `output/playwright/tag_collect_fields.png`
- `output/playwright/tag_collect_redesign_task.png`
- `output/playwright/tag_collect_redesign_results.png`
- `output/playwright/tag_collect_redesign_fields.png`
- `output/playwright/tag_collect_redesign_mobile.png`

## 后续恢复建议

从这里开始：

```bash
cd <SOP 项目目录>
```

启动工作台：

```bash
python3 cli.py tag_collect --serve --port 8765
```

然后打开：

```text
http://127.0.0.1:8765
```

页面级验收已完成。后续建议进入真实详情核验第二片：先用 `prod_detail` 的真实详情快照做字段白名单映射；如果字段不足，再接 Playwright/RPA 登录详情页核验。
