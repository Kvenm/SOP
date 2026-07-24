# 2026-07-22 1688 类目菜单下方入口无法查找：重开修复与部署

## 用户反馈与状态

- 用户反馈：筛选开始后，在 1688 工作台选择类目时无法自动下滑寻找下方类目入口；之前正常，现在失效。
- 截图显示：`1688 类目导航未完成，未生成任何数据`，当前类目为 `日用餐厨、居家日用>餐具>碗`。
- 该问题与 2026-07-20 的“整页滚动/菜单不展开”有关，但本轮按老问题重开，重新读取当前 Windows 失败快照，不沿用旧“已解决”结论。

## 目标与边界

- 目标：目标类目位于菜单当前渲染区下方时，自动滚动真实类目容器并重新查找，保持真实鼠标 hover/click。
- 失败边界：滚动到底仍找不到时有限停止并返回诊断，不刷新页面、不降级关键词、不导出错类目。
- 禁止：不绕过登录、滑块、验证码或平台风控；不主动运行新的真实 1688 采集；不提交、不推送。

## 远端现场证据

从 Windows `entity` 的安全任务摘要和错误快照只读取必要诊断：

- `tag_collect_error_20260722_140805_341.json`
- `tag_collect_error_20260722_140625_022.json`
- `tag_collect_error_20260722_134740_554.json`
- `tag_collect_error_20260722_134613_859.json`

四次失败一致：

1. 一级 `日用餐厨、居家日用`：`matched`，真实鼠标 hover，页面 `scrollY=0`。
2. 二级 `餐具`：`matched`，命中局部容器 `cate_content--TUOLAWjz`，容器 `scrollTop=0`。
3. 三级 `碗` 或 `保鲜盒饭盒`：`not_found`，没有 `category_scroll` 记录。
4. 可见文本仍停留在首页上方组合类目，证明失败路径没有继续滚动并重新读取下方菜单内容。

部署前还发现用户已有真实任务在运行，最近进度为详情核验 `6/20`。本轮没有取消、重启或覆盖该任务；等待其自然结束并确认活动任务为 0 后才部署。

## 根因

`clickCategoryPart()` 的旧流程是：

```text
在当前已渲染 DOM 中找到目标
-> 找到后才滚动最近可滚容器
-> 真实鼠标移动/点击
```

这能处理“节点已存在但被容器裁剪”的情况，不能处理 1688 当前的懒加载/虚拟菜单：下方类目只有滚动后才渲染。节点尚不存在时函数直接返回 `not_found`，因此永远不会触发局部滚动。旧回归只验证了“不使用 scrollIntoView”和“使用真实鼠标”，没有覆盖“目标初始不在 DOM、滚动后才出现”的负例。

## 修复

- 未找到目标时，从当前可见类目节点及其祖先中选择可滚动菜单容器，排除 `body/html`。
- 按类目特征、真实类目链接数量、与上一层命中节点的包含/位置关系给容器评分，优先滚动相关菜单。
- 每轮执行：局部容器分段滚动 -> 等待重绘 -> 重新查询节点；不复用滚动前的空结果或过期 locator。
- 默认最多 18 步，可用 `TAG_COLLECT_RPA_CATEGORY_SCROLL_STEPS` 调整，硬上限 30。
- 记录 `category_scroll_scan`：尝试次数、最大次数、容器、`scrollTop` 前后值、页面 `window.scrollY`、停止原因。
- 页面如发生意外滚动会立即恢复；真实 hover/click 继续使用 Playwright `page.mouse.move/click`。
- 到底无剩余容器返回 `no_remaining_category_scroll_container`；达到上限返回 `category_scroll_max_steps_reached`。
- 工作台版本更新为 `2026.07.22-category-menu-scroll-r1`。

## 负例回归

新增真实 Chrome 本地页行为测试：

1. 菜单初始不渲染“碗”，滚动到阈值后才懒加载；RPA 必须重新定位并用真实鼠标点击一次。
2. 整个过程中菜单 `scrollTop` 必须增加，`window.scrollY` 必须保持不变。
3. 目标不存在时必须滚到局部容器底部并在有限次数内停止。
4. macOS 和 Windows Chrome 均验证：3 次局部滚动找到“碗”；不存在目标时 11 次后在 `scrollTop=920/920` 停止。

## 验证

本地：

```text
Python py_compile：通过
node --check rpa_collect.mjs：通过
node --check rpa_detail.mjs：通过
category_scroll Chrome 行为负例：通过
tag_collect smoke tests passed (75 tests)
git diff --check：通过
```

Windows `entity`：

```text
Python py_compile：通过
两个 Node 语法检查：通过
category_scroll Chrome 行为负例：通过
tag_collect smoke tests passed (75 tests)
工作台版本：2026.07.22-category-menu-scroll-r1
监听：127.0.0.1:8765
allow_real_collect：true
active_task：none
```

部署文件 `rpa_collect.mjs`、`smoke_test.py`、`web.py` 的 Windows SHA-256 与本地一致。

## 部署与回滚记录

- 备份：`<LocalAppData>\SOP1688\backups\category-menu-scroll-r1-20260722-151600`，共 3 个文件。
- 第一次部署验证发现 SSH PowerShell 的 `PATH` 不包含独立 Node 目录，既有 smoke 测试无法找到 `node`；自动恢复备份并启动旧版。
- 第二次验证暴露 Windows Chrome 的自测懒加载回调比 macOS 慢；测试页补充“scroll 事件 + 定时渲染检查”双保险，自动恢复备份。
- 新行为先在 Windows 项目 `.tmp` 中单独验证通过，再进行最终覆盖；最终完整远端验证通过并重启服务。
- 未启动新的真实 1688 采集，未修改 Cookie、浏览器 profile、真实导出或用户任务数据。

## 残余风险与用户验收

- 离线和本地 Chrome 行为契约已通过；真实 1688 当前账号/页面结构仍需要用户低频验收，不能用模拟页面代替第三方真实环境结论。
- 建议先选择单个 `日用餐厨、居家日用>餐具>碗` 小任务，观察类目菜单是否自动向下滚动并进入结果页。
- 如果仍失败，查看筛选执行记录中的 `category_scroll_scan.records`、停止原因和滚动后的可见类目文本；不要连续高频重试或刷新首页。
