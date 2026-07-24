# 2026-07-22 类目闪窗结束：二级精确悬浮修复 r3

## 用户反馈

- r2 部署后，任务右下角显示“筛选类目”。
- 1688 类目区域闪一下，随后又弹出一个新窗口闪一下，任务直接结束，不再采集商品。
- 本问题继续按老问题重开，不沿用 r2 离线通过结论。

## 真实环境证据

Windows 最新失败任务：

```text
task_id: x0hckiUbn8mplvMH
status: paused
created_at: 2026-07-22 16:32:16
error snapshot: <LocalAppData>\SOP1688\tag_collect\tag_collect_error_20260722_163253_543.json
```

失败链路：

1. 一级 `日用餐厨、居家日用` 成功。
2. 二级 `餐具` 日志显示成功，真实指针坐标约为 `x=866, y=844`。
3. 三级 `盘` 为隐藏精确节点，尺寸 `0x0`，返回 `not_found`。
4. r2 父级恢复实际执行且记录 `recovery.ok=true`，但恢复后 `盘` 仍为 0 尺寸。
5. 页面仍为 `https://www.1688.com/`，没有进入商品结果页，`navigation_state={}`。

用户看到的第二个窗口不是已经进入商品结果页，而是多类目独立子任务重新打开首页；相同前置导航失败后触发熔断并关闭，所以表现为“闪一下结束”。

## 根因

`clickCategoryPart()` 的旧悬浮目标无条件向上取最近的：

```text
li / category / cate / menu / industry / cBox
```

1688 当前二级标题 `餐具` 是高分组 `li` 顶部的 `a.cTitle`。代码虽然用标题文字完成了匹配，却把真实鼠标移动到整个 `li` 的中心。中心可能是空白或其它叶子区域，导致级联菜单没有保持展开。

r2 的恢复流程只验证一级、二级各自能否再次匹配文字，没有验证恢复后三级目标是否获得非零尺寸，因此把“父级文字命中、子级仍隐藏”误记为恢复成功。

## 修复

- 二级及更深层悬浮优先使用精确 `a`、`button`、`role=button` 或 `cTitle` 标题节点；一级仍允许使用导航容器。
- 类目诊断新增真实指针目标元素：标签、class、文本、坐标和宽高。
- 父级恢复完成后立即检查下一层精确目标是否具有非零尺寸。
- 父级文字均命中但下一层仍隐藏时返回 `reopen_not_visible`，不再记录 `recovery.ok=true`。
- 工作台版本更新为 `2026.07.22-category-menu-scroll-r3`。

## 负例回归

Chrome 行为测试使用一个高度 210px 的二级 `li`，标题 `a.cTitle` 只有 90x34：

1. 鼠标必须落在精确标题 `a`，不能落到高 `li` 中心。
2. 菜单自动收起后，重新悬浮父级必须让 `盘` 恢复非零尺寸，并只点击一次。
3. 禁止子菜单再次展开时，即使一级和二级都能命中文字，也必须返回 `reopen_not_visible`。
4. 原有下方类目局部滚动、目标不存在有限停止、页面不滚动负例继续通过。

## 验证

本地：

```text
Python py_compile：通过
两个 Node 语法检查：通过
精确标题/高 li/假恢复行为负例：通过
tag_collect smoke tests passed (75 tests)
git diff --check：通过
```

Windows `entity`：

```text
Python py_compile：通过
两个 Node 语法检查：通过
精确标题/高 li/假恢复行为负例：通过
tag_collect smoke tests passed (75 tests)
workbench_build: 2026.07.22-category-menu-scroll-r3
active_task: none
listener: 127.0.0.1:8765
```

首次远端发布验证因 SSH 会话的 Node 目录未加入 `PATH` 而触发保护回滚；服务自动恢复为 r2。补齐远端验证环境后重新发布成功，未影响真实采集任务。

部署前备份：

```text
<LocalAppData>\SOP1688\backups\category-menu-scroll-r3-20260722-165608
```

本地与远端 `rpa_collect.mjs`、`smoke_test.py`、`web.py` 的 SHA-256 一致。本轮没有主动运行真实 1688 采集。

## 残余风险

- 离线 DOM 行为回归不能替代当前账号和当前 1688 页面版本的完整真实验收。
- 下一步由用户低频复测 `日用餐厨、居家日用>餐具>盘`；若仍失败，优先检查 `pointer_target_element`、`recovery.status`、`next_target` 和最终结果页确认，不再只看文字是否 matched。
