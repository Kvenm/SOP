# 2026-07-22 类目仍暂停：裁剪区与平滑滚动修复 r4

## 用户反馈

- r3 部署后继续复测，工作台仍提示“1688 类目导航未完成，未生成任何数据”。
- 截图当前批次选择了 `日用餐厨、居家日用>餐具` 下的多个叶子类目。
- 本问题再次按老问题重开，不以 r3 离线通过作为完成结论。

## 最新真实任务

```text
task_id: TlFfkFj66YfNHfkE
status: paused
created_at: 2026-07-22 17:12:35
snapshot: <LocalAppData>\SOP1688\tag_collect\tag_collect_error_20260722_171310_111.json
```

批次选择 8 个餐具叶子类目。首个 `盘` 导航失败，第二个 `碗` 同类失败后触发连续 2 次系统性熔断，剩余 6 个类目被跳过，避免反复刷新首页。

## r3 为什么仍失败

r3 快照已经正确记录：

```text
一级目标：li，190x31，pointer=(294,422)
二级目标：a.cTitle，90x96，rect top=796, bottom=892
二级旧指针：pointer=(454,844)
叶子“盘”：0x0
recovery.status=reopen_not_visible
```

只读连接当前 Windows 采集 Chrome，检查真实 1688 DOM：

```text
类目菜单：top=184, bottom=684, clientHeight=500
类目内容：scrollHeight=842
餐具标题：top=796, bottom=892
盘链接：top=796, bottom=828
```

所以 `餐具/盘` 虽然存在宽高，但位于 `overflow:auto` 菜单的裁剪可视区之外。旧 `visible()` 只检查元素自身宽高和 `display`，把被裁剪的节点误判为可交互。

此外，菜单使用平滑滚动：写入 `scrollTop` 后同步读取仍为 0，约 150ms 后才实际到 291。r3 没等待滚动落位便按旧坐标移动鼠标。真实轨迹从一级 `(294,422)` 斜向 `(418,832)`，依次穿过其它一级类目、首页内容和商品卡片，级联菜单随即关闭。

## 修复

- 滚动最近类目容器时临时强制 `scroll-behavior:auto!important`。
- 等待两个 `requestAnimationFrame` 后再读取目标、容器和最终 `scrollTop`。
- 诊断记录请求/实际滚动位置、菜单矩形和 `target_within_container`。
- 目标仍在菜单裁剪区外时返回 `category_target_clipped_by_menu_container`，禁止按旧坐标移动。
- 保存上一层真实鼠标坐标；二级及叶子类目先水平进入菜单，再沿菜单内部移动到目标高度，最后进入目标。
- 高标题节点使用文字 `Range` 作为最终鼠标落点，不使用整个 90x96 元素中心。
- 诊断新增 `pointer_route=category_container_corridor`、`pointer_waypoints` 和 `pointer_target_strategy=direct_text_range`。
- 工作台版本更新为 `2026.07.22-category-menu-scroll-r4`。

## 负例回归

Chrome 行为测试模拟真实结构：

1. 菜单高度 180，目标初始位于裁剪区下方。
2. 菜单启用 `scroll-behavior:smooth`。
3. 二级标题 `a` 高 96，文字 Range 仅约 20 高。
4. 鼠标斜穿菜单外部会触发 `mouseleave` 并关闭菜单。
5. 系统必须先即时局部滚动，让目标进入容器，再沿容器走廊命中文字。
6. 菜单超时重开、下一层仍隐藏的 `reopen_not_visible`、不存在目标有限停止和页面不滚动负例继续通过。

## 验证与部署

本地：

```text
Python py_compile：通过
两个 Node 语法检查：通过
裁剪区/smooth/容器走廊/文字Range负例：通过
tag_collect smoke tests passed (75 tests)
git diff --check：通过
```

Windows `entity`：

```text
Python py_compile：通过
两个 Node 语法检查：通过
完整 75 项 smoke：通过
workbench_build: 2026.07.22-category-menu-scroll-r4
active_task: none
listener: 127.0.0.1:8765
```

部署备份：

```text
<LocalAppData>\SOP1688\backups\category-menu-scroll-r4-20260722-172843
```

本地与远端 `rpa_collect.mjs`、`smoke_test.py`、`web.py` SHA-256 一致。本轮只进行了菜单几何和指针路径诊断，没有点击叶子链接、没有进入商品结果页，也没有主动运行真实 1688 采集。

## 残余风险

- 离线回归与只读/菜单悬浮诊断不能替代用户完整批次复测。
- 下一次复测优先观察首个 `盘` 是否进入结果页；若仍失败，检查 `category_scroll.target_within_container`、`pointer_route`、`pointer_waypoints` 和 `pointer_target_strategy`。
