# 2026-06-20 类目导航采集修复记录

## 触发问题

用户在本地工作台选择 1688 品类后执行采集，真实页面没有进入搜索/结果页。用户明确要求：

- 类目必须通过 1688 左侧/类目入口点击。
- 不能把类目文本塞进搜索框当关键词。
- 每次改完必须测试。
- 使用多 agent 角色机制复核。

## 多 Agent 评审

Workflow：

```text
2026-06-20T08-33-22Z-tag-collect-next-e780f7
```

只读角色：

- Product Agent：确认类目是采集范围门禁，不是普通提示；类目失败不应继续导出未约束结果。
- Architect Agent：确认 Web/service 传参链路基本贯通，问题集中在 RPA 点击、跟随结果页和失败状态。
- Verification Agent：先前要求 `partial_clicked` 进入警告；本次用户进一步明确“每个类目标签都需要测试、类目不完整不能继续”，因此最终收紧为 `partial_clicked` 也必须阻断采集和导出。

## 修复内容

- `rpa_collect.mjs`
  - 支持 `category_filters` 作为无关键词真实采集入口。
  - 类目点击不再一次性在页面脚本里连点，而是逐级 hover/click，并跟随新 tab 或 URL 变化。
  - `女装/女士精品` 这类本地类目会拆成页面可见候选，如 `女装`。
  - 点击后必须确认结果页商品卡片；失败返回 `category_navigation_not_loaded`。
  - 只命中上层类目时返回 `partial_clicked`，并由上层阻断采集/导出，不伪装成完整命中。

- `service.py`
  - 类目不再生成搜索词；只选类目时 query 为 `""`，由 RPA 执行类目入口点击。
  - `not_found` / `click_failed` / `not_reported` 类目结果会阻断导出。
  - `partial_clicked` 纳入阻断状态，返回 `category_navigation_not_loaded`，不生成导出。
  - 多个类目复选时，每个类目拆成独立 RPA 任务，避免同一页面状态污染下一类目。

- `web.py`
  - `category_navigation_not_loaded` 作为暂停类错误展示，不走普通失败。

- `smoke_test.py`
  - 增加类目导航失败错误态测试。
  - 增加“类目失败即使 RPA 返回商品也不能导出”的服务层回归。
  - 增加 `partial_clicked` 必须阻断导出的回归。
  - 增加多类目必须拆分为独立 RPA 调用的回归。

## 已验证

已通过：

```bash
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
python3 -m py_compile scripts/capabilities/tag_collect/rpa.py scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py
python3 scripts/capabilities/tag_collect/smoke_test.py
git diff --check
```

真实 Chrome CDP 最小链路已验证：

```text
输入类目：女装/女士精品>连衣裙
搜索词：空
结果：进入 https://s.1688.com/selloffer/offer_search.htm?...keywords=女装
商品：采到 1 条真实商品
筛选状态：partial_clicked
提示：未找到下一层：连衣裙
```

旧记录中的这个结果现在会被阻断，不会生成导出。原因是 1688 页面没有精确命中 `连衣裙` 入口时，宽泛 `女装` 结果不能作为目标叶子类目结果。

Web 工作台已重启：

```text
http://localhost:8765/
```

页面健康检查：

- 标题：`1688选品库筛选工作台`
- 主按钮：`开始查询`
- 控制台：无 error/warn

## 剩余风险

- 当前本地类目字典仍是 partial seed，不是完整官方 1688 类目树。
- 1688 对不同账号、IP、浏览器状态展示的类目入口可能不同；找不到叶子类目时会标 `partial_clicked` 并阻断，不会伪造成功。
- 真实采集仍受 1688 登录、滑块、访问拒绝影响；系统不绕过风控。
