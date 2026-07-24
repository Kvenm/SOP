# 2026-07-22 类目“盘”仍找不到：悬浮级联超时重开修复 r2

## 用户反馈

- r1 部署后，机器人筛选 `日用餐厨、居家日用>餐具>盘` 仍提示 1688 找不到类目。
- 用户确认网站上有该入口，需要下滑找到。
- 本问题继续按老问题重开，不使用 r1 的离线通过结论替代真实页面取证。

## 真实失败证据

Windows `entity` 当前版本为 `2026.07.22-category-menu-scroll-r1`。用户复测产生：

- `tag_collect_error_20260722_154121_291.json`
- `tag_collect_error_20260722_154831_304.json`

两次记录一致：

1. 一级 `日用餐厨、居家日用` 成功命中。
2. 二级 `餐具` 成功命中。
3. 三级 `盘` 进入 r1 的滚动扫描，但第 1 次即返回 `no_remaining_category_scroll_container`。
4. `window.scrollY` 始终为 0，没有整页误滚动。

## 只读 DOM 复核

在任务结束后保留的 1688 首页通过 CDP 只读检查，不点击、不滚动页面：

- 精确文本 `盘` 链接确实存在。
- 它位于 `ul.cate_content--TUOLAWjz` 的 `餐具` 分组中，同组还有 `保鲜盒饭盒/碗/果盘果篮/碗碟盘套装` 等。
- 失败后该 `ul` 为 `display:none`、尺寸为 0，因而没有可滚动容器。
- 页面真实文档高度为 4398，但这不是需要盲目滚动整个网页的问题；目标菜单本身已经收起。

## 根因

r1 对“下方类目未渲染”增加了局部滚动重查，但层级操作仍复用普通筛选节奏：

```text
一级 hover
-> afterFilterClick 3-6.5 秒
-> beforeFilterClick 1.4-3.6 秒
-> 二级 hover
-> 再次等待
-> 三级 click
```

一级/二级/叶子是同一次悬浮会话。数秒等待使 1688 悬浮菜单在三级点击前自动变为 `display:none`。滚动扫描只看到隐藏菜单，因此误报网站没有 `盘`。

此外，旧 `followCategoryClick()` 在动作已经返回 `not_found` 时仍会空等 5 秒 popup 超时，进一步延迟恢复。

## 修复

- 类目层级间使用独立的短级联节奏，不再复用普通筛选的长等待。
- 动作未命中时立即返回，不再空等 5 秒 popup。
- 未找到类目时扫描全 DOM 的精确文本，记录 `hidden_exact_matches`、目标尺寸和菜单祖先 `display` 状态。
- 当前层失败且已有父级成功时，在同一页面快速重新悬浮父级路径，然后重试当前层一次。
- 恢复过程写入 `category_steps[].recovery`，包含父级重新命中状态和诊断。
- 保留 r1 的局部滚动重查、真实 `page.mouse.move/click`、页面滚动保护、结果页确认和失败熔断。
- 工作台版本更新为 `2026.07.22-category-menu-scroll-r2`。

## 负例回归

Chrome 行为测试新增悬浮超时场景：

1. 一级、二级先正常展开。
2. 测试菜单自动超时变为 `display:none`。
3. 第一次寻找 `盘` 必须失败，并记录隐藏精确目标。
4. 系统重新悬浮一级、二级后，必须只点击一次 `盘`。
5. 原 r1 的下方懒加载、局部滚动、页面不滚动、目标不存在有限停止负例继续保留。

## 本地验证

```text
Python py_compile：通过
两个 Node 语法检查：通过
悬浮菜单自动收起/恢复行为负例：通过
局部滚动行为负例：通过
tag_collect smoke tests passed (75 tests)
git diff --check：通过
```

## 部署状态

- 部署准备时用户另一个真实采集任务正在 Windows 上运行，本轮没有取消或中断；等待其详情核验和导出自然完成后才重启服务。
- r1 已备份到 `<LocalAppData>\SOP1688\backups\category-menu-scroll-r2-20260722-155931`。
- Windows Chrome 离线负例通过：菜单自动收起后识别隐藏 `盘`、重新展开父级并只点击一次；原局部滚动和不存在目标有限停止负例同时通过。
- 远端 Python 编译、两个 Node 语法检查和完整 75 项 smoke 通过。
- `SOP1688-Web` 已重启，版本为 `2026.07.22-category-menu-scroll-r2`，只监听 `127.0.0.1:8765`，`allow_real_collect=true`，`active_task=none`。
- 部署的 `rpa_collect.mjs`、`smoke_test.py`、`web.py` 与本地 SHA-256 一致。
- 本轮没有主动启动新的真实 1688 采集。

## 残余风险

- 本地行为契约不能代替当前 1688 页面真实低频验收；部署后仍需用户复测 `日用餐厨、居家日用>餐具>盘`。
- 如果仍失败，应优先检查 `hidden_exact_matches`、`category_steps[].recovery` 和结果页确认信息，不再只看“有无滚动容器”。
