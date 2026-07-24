# 2026-07-22 工作台 7.21 反馈修复：单表导出、类目滚动与声音提醒

## 输入证据

- `工作台问题汇总7.21.docx`
- `表格样式（去除下方多余附表）.xlsx`
- `后台滑动，类目滑动bug.mp4`

附件确认三个问题：Excel 主表空而数据分散在附表；类目弹窗滚动带动背景工作台且选择后列表跳顶；登录/滑块/验证码和任务完成需要可关闭的声音提醒。

## 根因

1. `export_xlsx()` 只把内部可信 `rows` 写入主表，把待核验/筛除商品拆到附表；fail-closed 后 `rows` 可能为空。
2. 页面只给 `body.category-modal-open` 设置 `overflow:hidden`，真实背景滚动容器 `.workspace` 未锁定；局部列表到边界后滚动链继续传给背景。
3. `renderCategories(false)` 在点击一级/二级后整体重绘，未按列保存 `scrollTop`。
4. Node RPA 在安全页内部等待最长 5 分钟，Python 使用最终 `communicate()` 返回，工作台无法在等待开始时收到控制信号。

## 修复

- 导出字段改为 7.21 模板的 24 列，删除批发价、权益保障、代发权益、24小时揽收率。
- XLSX 只保留 `选品结果`；按 `rows -> category_unknown_rows -> filter_excluded_rows` 合并，优先商品 ID、其次 URL 去重并重排序号。
- JSON 快照继续保留全部筛选、核验、异常和真实性审计；内部可信数量与筛选逻辑仍 fail-closed。
- 类目模态同时锁定 `body/.workspace`，保存并恢复页面和工作区位置，增加 `overscroll-behavior` 与非 passive wheel 边界保护。
- 类目重绘改为列级保存策略：一级保留一级栏，二级保留一级/二级栏，三级/勾选保留三栏。
- 工作台增加声音提醒开关并写入 `localStorage`；人工介入双短音，任务完成三音阶，按任务 ID 和 `alert_key` 去重。
- Node stdout 继续只输出最终 JSON；stderr 用 `SOP_RPA_PROGRESS ` 前缀逐行输出实时 JSON。Python 用独立线程读取 stdout/stderr 并立即转发进度。
- 安全边界不变：不点击、不拖动、不破解、不刷新滑块/验证码，只提示并等待人工处理。

## 本地验证

```text
python -m py_compile: 通过
node --check rpa_collect.mjs: 通过
node --check rpa_detail.mjs: 通过
tag_collect smoke: 74 tests passed
Playwright 本机工作台验收: 通过
```

Playwright 证据：

- 模态打开后 `.workspace` 计算样式为 `overflow-y:hidden`。
- 三栏边界继续滚轮后 `window.scrollY` 和 `.workspace.scrollTop` 均不变化。
- 一级栏原位置 62、二级栏原位置 191，点击后未归零。
- 人工介入重复渲染只启动 2 个音符，完成重复渲染只启动 3 个音符，总计 5。
- 声音开关关闭/开启后刷新均保持设置。
- 700px 窄屏 `scrollWidth == clientWidth == 700`。

本地没有启动采集 Chrome，没有访问或运行真实 1688 采集。

## 部署

- Windows 主机旧运行文件已备份到 `<LocalAppData>\SOP1688\backups\export-scroll-sound-r1-20260722-104242`，共 6 个文件。
- 新运行文件已同步；远端 Python 编译、两个 Node 语法检查和 74 条 smoke 已通过，六个文件 SHA-256 与本地一致。
- 重启前发现用户任务 `FEoB2f80cNA_il2K` 正在进行 200 个商品详情核验，因此没有取消或强制重启。任务于 11:14:21 自然完成，run_id 为 `20260722_101241_418`。
- 旧批次 XLSX 和 JSON 已先备份到上述备份目录的 `run-artifacts` 子目录；随后只读取本地 JSON，用新代码刷新该批次导出，没有再次访问 1688。
- 刷新后的 XLSX 验证：`SHEETS=1`、`COLS=24`、表头严格匹配、`DATA_ROWS=200`；JSON 分类仍为 `rows=123`、`category_unknown_rows=77`、`filter_excluded_rows=0`。
- `SOP1688-Web` 已重启并处于 `Running`；只监听 `127.0.0.1:8765`，`/api/options` 返回 `2026.07.22-export-scroll-sound-r1`、`allow_real_collect=true`、`active_task=none`。
