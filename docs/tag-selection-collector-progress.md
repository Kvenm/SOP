# 标签选品采集工作台进度记录

记录时间：2026-06-14 09:49:15 CST
更新：已完成第二阶段第一片“P0/P1 高潜详情核验队列 + 样例详情核验 + 字段级核验记录 + Web 核验按钮 + 导出核验记录 sheet”。
暂停记录：本轮验收后已停止本地 `127.0.0.1:8765` Web 工作台进程。

## 当前阶段

已完成第一版 Web 筛选测试工作台 MVP，以及第二阶段第一片样例详情核验能力。当前可作为“标签复选 + 样例采集 + P0/P1 高潜核验队列 + 样例详情核验 + 字段编号 + Excel 导出”的测试基线；下一阶段应接入真实 `prod_detail` 或 Playwright/RPA 详情页核验。

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

真实采集 Web 模式需要显式开关：

```bash
python3 cli.py tag_collect --serve --port 8765 --allow-real
```

且仅允许本机 host 开启真实采集。

### Web 工作台

已实现本地 Web 页面：

- 类目标签复选，含一级/二级类目树。
- 运营筛选标签复选。
- 搜索词、排除标签、查询词上限、每词商品上限。
- 样例数据默认开启，不调用 1688。
- 结果表格筛选：标题/类目、最低分、推荐等级、核验状态、微信小店规则预判。
- 字段编号页。
- 下载导出文件。
- 本地 token 校验，无 token 的 POST 会拒绝。
- 真实采集默认关闭，未加 `--allow-real` 时关闭样例数据也会被阻止。
- 已完成浏览器交互验收：复选 `女装/女士精品`、`微信小店`、`一件代发`、`48小时发货` 后运行采集，生成 3 条样例结果和下载链接。
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
- 共 72 个字段定义。
- 字段定义同源用于 Web 字段页、导出列、字段说明 sheet。
- 已包含用户重点字段：批发运费、代发运费、品退率、24 小时揽收率、发货率、发货时效、是否一件代发、核验状态、人工复核状态。

注意：关键字段当前仍为“待详情页核验”，没有伪装成可信数据。本轮已将详情页核验字段统一收敛到 `DETAIL_ONLY_FIELDS`，包括起批范围、代发价、批发运费、代发运费、品退率、发货率、发货时效、是否一件代发、面单支持、24 小时揽收率、月代发订单。

### 导出

已支持：

- `.xlsx`，默认导出格式。
- `.csv`。

`.xlsx` 是真正的工作簿 zip 格式，当前包含 5 个工作表：

- `选品结果`
- `字段说明`
- `标签配置`
- `核验失败`
- `核验记录`

### 风险隔离

已完成：

- 样例数据模式不依赖 `requests`，本地无依赖也能跑。
- 真实采集才懒加载现有 `search` capability。
- 服务端强制限额：`MAX_QUERIES = 50`，`MAX_ITEMS_PER_QUERY = 50`。
- Web POST 必须带服务端 token。
- Web 真实采集必须 `--allow-real` 且本机 host。
- `exclude_tags` 已在 MVP 中实际生效，会匹配标题、类目、风险提示、标签来源等并过滤。
- 过滤规则会写入快照和 `标签配置` sheet。

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
- `.xlsx` 文件可作为 zip 打开，并包含 5 个 sheet。
- 字段说明中可找到 `10.8`、品退率、发货率。
- Web `/api/options` 返回 token、限额、字段编号。
- 无 token 调 `/api/collect` 返回 403。
- 带 token 调 `/api/collect` 成功生成样例结果。
- `/download?run_id=...` 用 GET 可下载 xlsx，响应类型为 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`。
- 已新增并通过 `scripts/capabilities/tag_collect/smoke_test.py`，覆盖字段编号、xlsx 五个 sheet、token 拒绝、样例 API 成功、真实采集未授权阻止、exclude 过滤。
- 已补充 smoke test 断言：`DETAIL_ONLY_FIELDS` 在详情页核验前必须保持“待详情页核验”，`verification_status` 保持 `unverified`。
- 已补充 smoke test 断言：
  - `DETAIL_ONLY_FIELDS` 在详情页核验前必须保持“待详情页核验”，`verification_status` 保持 `unverified`。
  - 采集后必须生成 P0/P1 详情核验队列。
  - 样例详情核验后必须生成字段级核验记录。
  - 核验后批发运费、品退率、发货率不再是占位值。
  - Web `/download?run_id=...` 端到端返回可打开的 xlsx，并包含 `核验记录` sheet。
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
