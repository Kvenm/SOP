# 2026-07-17 类目悬浮菜单与安全验证交接修复

## 目标

- 解决 `日用餐厨、居家日用 > 厨房工具 > 厨房用品配件` 在 1688 首页可见，但自动类目导航仍报失败的问题。
- 解决命中 1688 `punish` 后验证页被关闭、后台任务显示“执行失败”、页面展开超长临时 URL 的问题。
- 不绕过登录、滑块、验证码或平台风控；不使用样例数据冒充真实结果。

## 只读证据

目标 Windows 主机错误快照：

- `tag_collect_error_20260717_143455_149.json`
  - 一级 `日用餐厨、居家日用`：`matched`
  - 二级 `厨房工具`：`matched`
  - 三级 `厨房用品配件`：已找到真实链接
    `https://s.1688.com/selloffer/offer_search.htm?...keywords=厨房用品配件`
  - Playwright 在后续 `locator.hover()` 时菜单已收起，元素变为不可见，10 秒后误报 `click_failed`。
- `tag_collect_error_20260717_133636_077.json`
  - 1688 返回 `security_verification_required` / `punish`。
  - 任务选择了 18 个男鞋叶子类目；旧实现会让每个类目重复使用完整候选预算。
  - 用户提示包含完整 `x5secdata` 临时参数，后台状态为 `failed`。

## 修复

1. 类目叶子点击：
   - 仍先执行严格一级、二级 hover 和叶子精确匹配。
   - 如果叶子元素在 Playwright 操作前因菜单收起而不可见，但本轮已拿到真实 1688 结果链接，则使用该链接直达。
   - 直达后仍要求结果页 URL、商品卡片以及叶子 URL/类目证据确认；不会自行拼接关键词，也不会放宽到父类目。
   - `category_steps` 记录 `match_type`、`fallback_mode=direct_href` 和原始 locator 错误。
2. 安全验证人工交接：
   - CDP 模式下保留并置前登录/滑块/验证码页，不再关闭验证页。
   - 后台任务状态改为 `paused`，页面显示“等待人工处理”。
   - 用户完成验证并确认搜索页恢复正常后，回到工作台重新开始；不自动重试。
   - 用户提示移除完整 punish URL，结构化诊断仍在本地错误快照保留页面 URL。
3. 多类目候选预算：
   - 多类目继续独立 RPA 隔离，避免串类。
   - 所有类目共享整个任务的候选扫描上限；例如 18 类目、上限 120 时近似均分，而不是最多放大为 2160。

## 验证

通过：

```text
Python py_compile
node --check rpa_collect.mjs
node --check rpa_detail.mjs
git diff --check
tag_collect smoke tests passed (65 tests)
```

新增回归覆盖：

- 悬浮菜单收起时存在 `direct_href` 叶子链接兜底。
- CDP 登录/安全验证页使用 `releaseForManualHandoff` 保留。
- 安全验证后台任务进入 `paused` 而非 `failed`。
- 用户提示不显示 `x5secdata` 临时参数。
- 多类目共享任务总候选预算。

## 未执行与残余风险

- 本轮未运行新的真实 1688 采集，避免在账号已触发 `punish` 后继续刷新。
- 真实低频复测需要用户先在目标机采集 Chrome 中人工完成验证，并确认 1688 搜索页可以正常浏览。
- 1688 页面 DOM 仍可能变化；若真实链接跳转后没有商品卡片或类目证据，系统继续阻断，不导出不确定类目数据。

## Windows 部署

- 已部署到 `C:\Users\<WindowsUser>\apps\1688-shopkeeper`。
- 远端部署前自动备份到：
  `<LocalAppData>\SOP1688\backups\category-security-fix-20260717-145139`。
- 前两次验证分别暴露 SSH PowerShell 未继承 Node PATH、未启用 Python UTF-8；部署脚本均自动恢复旧文件并重新启动服务，未留下半部署状态。
- 补齐与正式计划任务一致的 `PATH`、`NODE_BIN`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 后部署成功。
- Windows 远端 65 条 smoke、Python 编译、两个 Node 语法检查全部通过。
- `SOP1688-Web` 状态为 `Running`，`http://127.0.0.1:8765/api/options` 返回 HTTP 200。
- 远端 `service.py`、`web.py` SHA-256 与本地一致。
- 本次部署和验证未启动真实 1688 采集。
