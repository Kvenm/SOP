# 标签选品采集工作台进度记录

## 最新状态：真实数据测试口径

记录时间：2026-07-23 CST

用户已明确要求测试时必须使用真实 1688 页面和真实数据，不再以样例数据作为默认验收链路。本项目已切换为：

- 2026-07-23 用户复测 r1 后再次重开：部署后 3 个真实批次共尝试核验 60 个商品，数字运费命中 50 个，但月代销命中 0 个；当前人工活动详情页只读 DOM 又明确存在 `.goods-operation-panel-media` 中的 `月代销100+`。根因不是 Shadow DOM，而是 RPA 每个商品新建后台标签页且未置前、未等待采购助手异步初始化。r2 将详情标签页置前，合并滚动前后证据并有限等待最多 12 秒；数字运费优先于其它位置“一件包邮”，发货模块明确包邮时写入“包邮”。新增延迟插件字段和运费/包邮冲突负例，本地与 Windows 完整 77 项 smoke 通过；Windows 已部署 `2026.07.23-shipping-monthly-metrics-r2`，真实月代销命中率待用户下一批低频复测。
- 2026-07-23 用户再次反馈运费和月代销长期不准：截图确认运费真实短标签存在 `运费￥10起/运费￥1.8起`，采购助手显示 `月代销<10/80+`，页面同时有“退货包运费、运费查询、近30天代发数量100以内”等干扰。详情抽取改为主文档、开放 Shadow DOM 和 frame 的短标签证据优先，整页文本仅兜底；严格隔离月代销、月代发订单和近30天代发数量，保留 `<10/80+/元起` 原始区间语义。起步运费只有下限时不再冒充确定落入运费筛选区间。截图负例和完整 76 项 smoke 在本地/Windows 均通过；Windows 已部署 `2026.07.23-shipping-monthly-metrics-r1`，但后续真实批次证明采购助手异步加载路径未覆盖，已由 r2 重开。
- 2026-07-17 用户明确纠正：反馈汇总中的问题全部按“仍存在、需要修改”处理，不能以“代码已有”判定完成。本轮把常用条件前置为“重点筛选”，增加二级类目行内“全选 N”、独立模板区、顶部任务进度/停止/结束原因、工作台版本标识和深度 200 预设；普通视图隐藏旧版重复运营标签。Windows 实体机已部署 `2026.07.17-feedback-r2`，远端离线 smoke 65 条通过，未运行真实 1688 采集。
- 2026-07-20 真实复测发现类目菜单无法展开且多类目不断刷新首页：上次为避免整页滚动使用了脚本合成 hover，但 1688 当前菜单依赖真实指针事件。现改用 Playwright `page.mouse` 移动/点击，同时保留“只滚菜单容器、不滚 body”；零成功且连续 2 个类目导航失败时立即停止剩余类目，避免首页刷新循环。Windows 已部署 `2026.07.20-category-navigation-r5`，本地和远端 72 条 smoke 通过，未主动运行真实 1688 采集。
- 2026-07-22 按 7.21 附件修复：XLSX 改为唯一 `选品结果` 工作表和固定 24 列，三类内部结果合并去重展示、JSON 审计不变；类目模态锁定 `.workspace` 并按列保存滚动；声音提醒默认开启且持久化，人工介入通过 Node stderr 实时转发，完成/人工提示按任务去重。本地/Windows 74 条 smoke 和本地 Playwright 浏览器验收通过；Windows 已部署 `2026.07.22-export-scroll-sound-r1`。
- 2026-07-22 真实复测再次重开类目滚动问题：远端失败快照证明一级/二级已命中，但三级“碗/保鲜盒饭盒”未渲染且失败步骤没有滚动记录。RPA 改为对候选类目菜单做有限“局部滚动-等待重绘-重新查找”，默认 18 步、硬上限 30，记录容器/`scrollTop`/页面滚动/停止原因；保留真实 `page.mouse` 交互和零结果连续 2 次熔断。本地与 Windows 均通过懒加载类目行为负例和完整 75 项 smoke；Windows 已部署 `2026.07.22-category-menu-scroll-r1`，只监听 `127.0.0.1:8765`，未主动运行真实 1688 采集。
- 2026-07-22 用户复测 `日用餐厨、居家日用>餐具>盘` 后再次重开：r1 已进入滚动扫描，但真实失败记录显示没有可滚容器；只读 DOM 进一步证明“盘”链接存在于 `cate_content--TUOLAWjz`，只是普通筛选的长等待让悬浮菜单在三级点击前变为 `display:none`。r2 改为短级联节奏、失败动作不再空等 5 秒弹窗、记录隐藏精确目标，并在同页重新悬浮父级路径后重试叶子一次；本地与 Windows 的自动收起/恢复负例和完整 75 项 smoke 均通过，Windows 已部署 `2026.07.22-category-menu-scroll-r2`，未主动运行新的真实 1688 采集。
- 2026-07-22 用户继续复测 r2 后任务在“筛选类目”阶段闪窗结束：最新真实快照显示 r2 的父级恢复确实执行，但二级“餐具”的指针坐标落在巨大 `li` 中部，未落在精确标题，三级“盘”仍为 0 尺寸；恢复只验证父级文字命中，因此产生假成功。r3 改为二级及更深层优先悬浮精确 `a/button/cTitle`，记录目标元素与尺寸，并在恢复后验证下一层是否可见，否则返回 `reopen_not_visible`。本地和 Windows 75 项 smoke、精确标题/高 `li`/假恢复负例、Python/Node 检查均通过；Windows 已部署 `2026.07.22-category-menu-scroll-r3`，只监听 `127.0.0.1:8765`，未主动运行真实 1688 采集。
- 2026-07-22 用户复测 r3 仍显示类目导航未完成：服务器快照和当前 1688 DOM 几何证明，“餐具”虽有 `90x96` 尺寸，但初始 `top=796`，最近菜单容器可视范围只有 `184-684`；目标被 `overflow:auto` 裁剪。旧代码又在 `scroll-behavior:smooth` 尚未落位时按旧坐标移动，指针轨迹斜穿其它一级类目和首页内容，菜单关闭。r4 临时关闭局部 smooth、等待两帧并确认目标进入菜单可视区，从上一层指针沿菜单内部走廊移动，最终命中文字 Range；裁剪目标不再允许直接交互。本地和 Windows 75 项 smoke、真实几何结构负例、Python/Node 检查均通过；Windows 已部署 `2026.07.22-category-menu-scroll-r4`，未主动运行真实采集。
- Web 工作台默认真实数据模式，通过 Playwright/RPA 打开 1688 搜索页采集候选商品。
- 标签已拆成四类：搜索词、1688 页面原生筛选、后置指标区间筛选、系统规则；`一件代发`、`48小时发货`、`7天包退货/七天无理由` 不再静默拼进搜索词。
- RPA 会在 1688 搜索页尝试点击/勾选原生筛选项，并把 `clicked`、`not_found`、`click_failed`、`not_applicable` 写入页面提示和 JSON 快照。
- 指标区间已细化：好评率、品退率、发货率、评论数、复购率、近30天销量、代发订单量均有区间标签；页面读不到的字段保持待核验或人工复核，不猜值。
- Web 类目区已支持一级/二级/三级路径展示，并引入可追溯类目字典文件 `scripts/capabilities/tag_collect/category_dict.json`。2026-06-28 已通过 `sync_1688_categories.py` 从 1688 首页公开 `treeData.l1s` 同步，当前为 10 个组合一级、155 个二级、1919 个三级；这不是开放平台“官方全量类目库”，但已和当前首页左侧导航对齐。
- 如遇登录、扫码或安全验证，需要用户在弹出的浏览器中处理；滑块/验证码在可视浏览器中默认等待人工最多 5 分钟，人工完成后继续同一 Node/RPA 子任务。超时或页面仍受限才返回 `security_verification_required`；系统始终不会绕过或破解验证。
- 用户 2026-07-21 模板确认 `选品结果` 主表为固定 24 列；Web、XLSX 和 CSV 均按该顺序输出。XLSX 只保留一个主表，数据模式、核验状态、失败原因、推荐/人工复核等审计信息只保留在 JSON。
- Web 和 CLI 已支持直接传入 1688 搜索页/商品详情页 URL，用于账号登录受阻时测试公开页面真实数据解析。
- 类目采集已改为严格门禁：本地选择类目后，路径必须先存在于当前 1688 首页导航字典；真实 1688 必须按类目入口逐级命中完整路径；`category_path_invalid`、`partial_clicked`、`not_found`、`click_failed`、`not_reported` 均会阻断采集和导出，不再把宽泛父类目结果当作目标类目结果。
- 2026-06-28 已复核并删除类目失败后的关键词 fallback；自动批量模式不会再把末级类目词塞进搜索框继续采集。类目失败会返回 `category_navigation_not_loaded`、筛选执行记录、命中层级和页面可见类目诊断，并保存本地失败快照。
- 2026-06-30 已强化样例隔离和真实来源门禁：样例行强制标注 `数据模式=开发样例`，样例复核状态显示 `样例预通过/样例不建议`；只有能证明来自真实页面采集或真实详情核验的行才标 `真实数据`，否则标 `来源未知/需核验`。2026-07-21 起这些审计信息只保留在 JSON。
- 2026-06-28 追加修复：自动批量采集且已选择类目时，Web 和后端都会忽略 URL 输入框残留，强制从 1688 首页类目入口执行，避免旧搜索页 URL 抢走入口。直接 URL 读取仍可通过不选类目或切换兜底/URL 模式使用。
- 2026-06-28 追加修复：RPA 类目执行记录会保留一级/二级/三级 `hover/click` 链路、每步 URL 前后变化和最终结果页确认状态；`not_confirmed` 会阻断导出，避免“看似点击但未确认进入目标类目结果页”的错数据。
- 多类目复选已拆成独立 RPA 任务执行，每个类目单独从 1688 首页/类目入口开始采集，避免多个类目在同一页面会话里连续点击导致结果串类。
- 2026-07-17 修复 1688 首页悬浮菜单竞态：一级/二级已命中、叶子链接已确认，但菜单在 Playwright 点击前收起时，允许使用该次可见菜单中捕获的真实 1688 叶子链接进入结果页；仍需通过结果页商品卡片与叶子 URL/类目证据确认，禁止自行拼接关键词兜底。
- 多类目仍按独立 RPA 任务隔离，但所有类目共享本次任务的候选扫描上限；例如 18 个类目、候选上限 120 时按总预算分配，不再让每个类目各扫 120 个候选。
- 安全验证/登录阻断在后台任务中改为 `paused/等待人工处理`，不再显示为普通“执行失败”；CDP 模式会把验证页保留并置前。滑块默认先等待人工并自动续跑同一任务，达到等待时限后才暂停；页面提示不展开带 `x5secdata` 等临时参数的超长 punish URL。
- “真实详情核验”会打开真实商品详情页，启发式提取运费、品退率、发货率等关键字段。
- `partial_verified` 表示真实详情页只提取到部分字段；缺失字段必须人工复核。
- 2026-06-28 追加修复：详情抽取从纯整页文本正则扩展为“结构化脚本 + 页面文本 + 店铺链接 DOM”组合，修复店铺名称误抓成“回头率”的问题，并增强店铺链接、发货率、发货时效、一件代发、7天无理由等字段提取。
- 自动详情核验上限已与候选上限统一为最多 200 条；工作台默认核验 200，候选扫描默认“深度 200”、目标可铺默认 60，并提供快速 50/标准 120/深度 200 预设；CLI 默认候选仍为 120。导出 `标签配置` sheet 会写入自动核验开关、核验上限、字段级失败数，避免把 `partial_verified` 误读为全部字段可信。
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
- 字段编号页继续沿用内部 1-10 分组编号；导出 `选品结果` 只显示用户确认的 28 个字段定义。
- 字段定义同源用于 Web 字段页、XLSX 和 CSV 导出列。
- 主表已包含用户确认的商品链接、月代发订单、代发运费、是否一件代发、好评率区间、48 小时揽收率、发货时效、发货率区间等字段；其它内部字段继续用于筛选、核验和 JSON 审计。

注意：关键字段当前仍为“待详情页核验”，没有伪装成可信数据。本轮已将详情页核验字段统一收敛到 `DETAIL_ONLY_FIELDS`，包括起批范围、代发价、批发运费、代发运费、品退率、发货率、发货时效、是否一件代发、面单支持、24 小时揽收率、月代发订单、SKU数量、店铺链接。

### 导出

已支持：

- `.xlsx`，默认导出格式。
- `.csv`，仅导出 `选品结果` 明细。

`.xlsx` 是真正的工作簿 zip 格式，但只包含一个 `选品结果` 工作表。`rows`、`category_unknown_rows`、`filter_excluded_rows` 合并去重展示；完整审计保存在同批次 JSON。

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
- 已接入真实 1688 详情页 RPA 核验；但字段仍依赖当前账号、类目和页面版本实际展示。`sample_verified` 只能证明流程通，不等于真实平台数据可信；真实字段提取不到时必须保留失败/待复核状态。
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

## 2026-07-11 商品类目证据三态修复

已完成：

- 商品类目判断从布尔值升级为 `matched / mismatched / unknown`。
- 官方数字类目 ID、商品结构化路径和详情面包屑优先于标题；标题仅作正向辅助，未命中不再直接判错。
- `unknown` 与主选品结果隔离，进入 `category_unknown_rows` 和详情核验队列，不计入可铺数量，不写历史拒绝池。
- 详情证据可把 `unknown` 提升为 `matched`，也可把标题误匹配覆盖为 `mismatched`。
- Excel 在存在待核验商品时增加 `类目待核验` sheet；Web 显示类目三态 badge 和待核验数量。
- RPA 列表/详情已提取官方数字类目 ID、商品类目路径和面包屑；本地 `l1_*` 类目节点 ID 不参与比较。
- smoke test 改为自动发现全部测试，当前 55 条全部通过；新增 ID/路径冲突保护、URL-only categoryId 隔离和 unknown 重复详情访问保护。

当前边界：

- 项目仍是本地单机工作台，默认绑定 `127.0.0.1:8765`。
- 本轮未运行真实 1688 采集；真实页面 DOM 证据覆盖率需要用户低频测试确认。
- 页面没有商品级证据时会进入类目待核验，不再通过批量遍历把“未知”当成“错类目”。

运行记录：`docs/RUNS/2026-07-11-category-evidence-three-state.md`。

## 2026-07-11 Web 任务类目隔离

已修复连续任务类目串入：旧页面会把第二次选择继续加入 `selectedCategories`，导致后端把“防晒面纱 + 帐篷”作为一个多类目任务执行。

- 默认改为单类目任务，新叶子类目替换旧类目。
- 显式开启“多类目任务”才允许多个类目累计。
- 服务端同步防御单类目模式下的旧值残留。
- 新任务发起前清空旧结果，并统一禁止三个运行入口并发。
- 类目切换后高级筛选值继续保留。
- 当前完整 smoke：58 条全部通过。

运行记录：`docs/RUNS/2026-07-11-web-task-category-isolation.md`。

## 2026-07-15 Windows 实体机部署

已完成 Windows 10 实体机部署，用于在交互桌面中运行 Chrome 和处理 1688 登录/安全验证：

- Web 工作台仅监听远端 `127.0.0.1:8765`，通过 SSH tunnel 访问；未开放公网或局域网端口。
- Windows “启动采集浏览器”已支持 Google Chrome / Microsoft Edge，CDP 仅监听 `127.0.0.1:9222`。
- Python 调用 Node 的 JSON 管道固定为 UTF-8；Windows smoke 临时 XLSX 改为关闭文件后再打开，避免文件锁冲突。
- 远端安装 Python 3.12.10、Node.js 20.19.4、Playwright 1.60.0、Google Chrome 150.0.7871.125。
- Web 服务通过 `SOP1688-Web` 交互式登录计划任务运行；Chrome、扫码、滑块和验证码必须在实体机物理桌面或 RDP 会话中人工处理。
- 本地和 Windows 远端完整 smoke 均为 59 条通过；两个 RPA Node 语法检查、Python 编译和 SSH tunnel HTTP 访问通过。
- 本次未启动 Chrome、未访问 1688、未执行真实采集。

运行记录：`docs/RUNS/2026-07-15-windows-physical-host-deployment.md`。

## 2026-07-16 类目精度与多页候选优化

- 1688 组合一级入口改为严格精确匹配，不再允许模糊命中包含整组文本的宽泛祖先容器。
- 结果页确认增加点击类目 ID、结果页类目 ID、标题/URL/面包屑证据，降低点错大类后仍误判成功的概率。
- 组合一级入口不再冒充商品真实大类；有商品级路径时使用真实路径，无证据时仅展示二级/叶子路径，完整导航路径继续保留在审计字段。
- 候选硬上限从 50 提高到 200；2026-07-17 根据用户“可以慢一点、希望更多商品”的反馈，Web 默认改为深度 200、目标可铺 60，并增加快速 50/标准 120/深度 200 预设；CLI 默认仍为 120。
- RPA 分页从固定默认 3 页改为按候选数量动态估算，最多 12 页；列表任务默认超时提高到 600 秒。
- 下一页优先点击明确页码，并限定在分页容器内；翻页后比较商品指纹，未变化则停止，避免重复读同一页。
- 返回和导出增加分页访问页数、分页候选数、逐页元数据和停止原因。
- 完整 smoke：61 条通过；本轮未运行真实 1688 采集。

运行记录：`docs/RUNS/2026-07-16-category-precision-and-pagination.md`。

## 2026-07-16 工作台截图反馈优化

- `选品结果` 主表锁定为用户直接确认的 28 列，CSV/XLSX/Web 表头一致；异常复核表额外保留异常状态、说明、数据模式和核验状态。
- 类目三栏重绘会恢复滚动位置；二级类目支持全选/取消其下三级叶子，提交数据不包含非叶子父路径。
- 新增月代发订单、代发运费自定义区间；商家特色按截图改为单选“超级工厂/源头旗舰/实力商家”。
- 一件代发、7天无理由退货、24H/48H 发货等重复筛选入口已收拢，旧卖家会员类型/卖家服务 payload 会被清洗。
- 新增筛选模板保存/加载/删除，存储在本地数据目录。
- 新增后台任务启动、状态和取消接口；页面显示阶段、百分比、页数/候选数，并可真正停止 Python/Node RPA；刷新页面会恢复同一服务进程内的活动任务。
- 详情字段增加 48 小时揽收率解析、样例值和核验记录。
- 结果表改为 4000px 容器内横向滚动，类目和标题列保持可读，不再把中文压成竖排。
- 本地 Python 编译、两个 Node 语法检查、`git diff --check`、64 条完整 smoke 和 Browser 桌面/移动交互验收通过；未运行真实 1688 采集。
- 已部署到 Windows 主机的 `C:\Users\<WindowsUser>\apps\1688-shopkeeper`；远端 Python 3.12 编译、Node 语法和 64 条 smoke 通过，`SOP1688-Web` 已重启且只监听 `127.0.0.1:8765`。

运行记录：`docs/RUNS/2026-07-16-workbench-feedback-optimization.md`。

## 2026-07-20 Web 任务历史与重启中断恢复

- 最近 20 条任务的安全摘要原子写入 `{TAG_COLLECT_DATA_DIR}/web_tasks.json`；不保存配置对象、商品 `rows`、Cookie、Token、浏览器 profile 或安全验证临时链接。
- 服务重新启动时，既有 `completed/paused/failed/cancelled/interrupted` 终态继续保留；原 `queued/running/cancelling` 明确转为 `interrupted`，写入结束时间并提示“Web 服务重启，原任务已中断，不会自动继续”。
- `/api/options` 新增 `recent_tasks`；页面新增“最近任务”，展示类目/采集方式、页数、候选数、结果数、停止原因和终态。
- 有 `run_id` 的历史任务可从本地批次重新加载结果或下载导出文件；不会重新访问 1688。
- 工作台版本更新为 `2026.07.20-task-history-r3`。
- 本地 Python 编译、两个 Node 语法检查、`git diff --check`、66 条完整 smoke 通过；Browser 实测开发样例任务完成、刷新后仍保留、历史结果重新载入 3 条、窄屏无横向溢出、重启中断黄色提示和停止原因均正常，控制台无 error/warn。
- 已部署到 Windows 主机；备份目录为 `<LocalAppData>\SOP1688\backups\task-history-r3-20260720-100545`，远端 66 条 smoke、Python 编译、两个 Node 语法检查通过，`SOP1688-Web` 已重启并返回新版本号。
- 本轮没有启动采集 Chrome，没有运行真实 1688 列表采集或详情核验，没有提交或推送。

运行记录：`docs/RUNS/2026-07-17-feedback-completion-audit.md`。

## 2026-07-20 工作台问题单红色老问题复审与修复

- 不再沿用上次“已解决”结论：红色反馈全部重新按截图、代码路径和可执行回归测试审计。
- 主表继续固定用户确认的 28 列；详情新增代发价、月代发订单、月代销/近30天件数、普通数值运费提取，自动详情核验上限从 20 放开到 200。
- “一件代发”只接受明确的一件代发证据；裸“代发价/月代发订单”不能再判定为支持一件代发。选择一件代发后会增加详情二次硬筛。
- 运费、一件代发等详情筛选未核实或未命中的商品移出 `选品结果`，进入 `异常复核`，不再用 `pending_detail` 假装通过。
- 类目菜单不再对目标元素使用 `scrollIntoView`；改为只滚动最近可滚动容器，并记录/恢复页面滚动位置。
- 类目选择改为模态弹窗，包含“全部类目 / 收藏类目”、三栏类目、已选区、整二级全选；收藏存储在当前浏览器本地。
- 1688 列表页和详情页会注入独立 Shadow DOM 悬浮状态栏；页面跳转后按阶段重新注入，显示类目/页数/候选数/详情核验/等待人工状态。
- 滑块/验证码默认等待人工最多 5 分钟，人工完成后继续同一任务；不点击、不拖动、不破解验证。等待时限可用 `TAG_COLLECT_RPA_MANUAL_VERIFY_WAIT_MS` 调整。
- 多类目独立子任务中单个类目失败时，保留前面成功商品并生成部分完成导出；失败类目、错误码、诊断和建议写入结果 JSON 与 `标签配置`。
- 导出文件名使用类目名：单叶子用完整路径；全选同一二级全部叶子时用二级路径；其它多类目用首类目加“等N类目”。Windows 非法字符会替换为全角/安全字符，并保留 run_id 防覆盖。
- 工作台版本更新为 `2026.07.20-feedback-fixes-r4`。
- 本地 Python 编译、两个 Node 语法检查、70 条完整 smoke 通过；Browser 桌面与窄屏验收通过，类目收藏刷新后仍存在，整二级“厨房工具”20 个叶子全选正常，窄屏无横向溢出，控制台无 error/warn。
- 已部署到 Windows 主机；备份目录 `<LocalAppData>\SOP1688\backups\feedback-fixes-r4-20260720-160059`，远端 Python 编译、两个 Node 语法和 70 条 smoke 通过。`SOP1688-Web` 已重启，返回版本 `2026.07.20-feedback-fixes-r4`，仅监听 `127.0.0.1:8765`；Chrome 150 已存在。
- 本轮未运行真实 1688 采集，未提交、未推送。

运行记录：`docs/RUNS/2026-07-20-workbench-feedback-red-bug-fixes.md`。

## 2026-07-22 7.21 工作台反馈：单表导出、类目滚动与声音提醒

- 按用户 XLSX 模板把主表从旧 28 列更新为 24 列，删除批发价、权益保障、代发权益、24小时揽收率；Web、CSV、XLSX 使用同一字段顺序。
- XLSX 只生成一个 `选品结果` 工作表；`rows`、`category_unknown_rows`、`filter_excluded_rows` 合并去重并重排序号。内部 fail-closed 和 JSON 审计结构不变，自动化判断仍以 JSON 分类为准。
- 类目弹窗打开时同时锁定 `body` 和 `.workspace`，增加 overscroll/wheel 边界保护；点击一级、二级、三级时按列保存滚动位置，修复背景跟随滑动和二级栏跳顶。
- 增加默认开启且可持久化的声音提醒：登录/滑块/验证码播放双短音，任务完成播放完成音，同一任务同一提醒只播放一次。
- Node RPA 新增 `SOP_RPA_PROGRESS` stderr 实时进度协议；Python 并发读取 stdout/stderr，在最终任务结束前即可把人工介入事件转发到 Web。仍不点击、不拖动、不破解验证。
- 本地 Python 编译、两个 Node 语法检查、74 条完整 smoke、`git diff --check` 和 Playwright 浏览器验收通过。浏览器验证了背景滚动不变、一级/二级栏位置保持、声音开关刷新持久化、人工双滴/完成三音各只播放一次、700px 窄屏无横向溢出。
- 本轮本地验证只使用开发样例和模拟进度，没有启动采集 Chrome，没有访问或采集真实 1688。

运行记录：`docs/RUNS/2026-07-22-workbench-feedback-export-scroll-sound.md`。
