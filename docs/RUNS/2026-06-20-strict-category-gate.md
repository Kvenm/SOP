# 2026-06-20 严格类目门禁修复记录

## 触发问题

用户确认当前仍有错误行为：

- 在本地页面点击类目后，真实 1688 仍像是在搜索框里搜索品类。
- 类目结果不符，要求每个类目标签都必须测试。
- 类目应该对应 1688 左侧/类目栏逐级点击；不能把类目文字当关键词。

## 多 Agent 结论

Workflow：

```text
2026-06-20T09-49-34Z-tag-collect-next-83d4c9
```

可采纳结论：

- Product Agent：`partial_clicked` 放行是 blocker；多类目复选不能在同一次页面会话里连续点击。
- Verification Agent：`partial_clicked` 必须阻断采集/导出；类目记录需要暴露期望路径、实际命中路径和层级。
- Architect Agent：本次输出因 JSON 校验失败未完整落库；但从可见输出看，同样指出原生筛选/类目未完整命中不能导出。

## 修复内容

- `web.py`
  - 点击三级类目时直接加入已选类目。
  - 点击没有三级的二级类目时加入已选类目；有三级的二级只负责展开。
  - 筛选执行记录展示类目期望路径、命中路径、命中层级和最终 URL。

- `rpa_collect.mjs`
  - 类目路径必须完整命中，不能因为页面已有商品卡片就提前算成功。
  - `partial_clicked` 不再被视为可继续状态。
  - 类目执行记录补充 `expected_path`、`matched_path`、`expected_depth`、`matched_depth`、`final_url`。

- `service.py`
  - `partial_clicked` 加入阻断状态。
  - 多个类目复选时拆成多个独立 RPA 任务，每次只传一个 `category_filter`。
  - 对列表页没有类目字段的商品，导出记录会填入本次采集的已选类目路径作为来源类目证据。

- `smoke_test.py`
  - 改写回归：`partial_clicked` 即使带回商品也必须抛 `category_navigation_not_loaded`，不生成导出。
  - 新增回归：多类目必须拆分为多次独立 RPA 调用。

## 验证

已通过：

```bash
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py scripts/capabilities/tag_collect/rpa.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
python3 scripts/capabilities/tag_collect/smoke_test.py
git diff --check
```

## 当前验收口径

- 只选类目时，RPA 不能输入类目词到 1688 搜索框。
- 类目路径必须完整命中；缺任一级都阻断。
- 多类目是 OR 候选池，每个类目独立采集，不在同一个 1688 页面里连续点击。
- 真实页面风控、登录、滑块仍由用户人工处理；系统不会绕过。

## 2026-06-28 复核修正

用户再次验证 `内衣>睡衣家居服` 后发现类目仍未按 1688 左侧类目命中，并要求解释采集失败含义。

本轮多 agent 复核结论：

- Product/QA Agent：服务层残留 `fallback_keyword` 会破坏“类目是采集范围门禁”的验收口径；失败提示也不能再引导用户把类目词改填到关键词。
- Architect/RPA Agent：RPA 类目失败时会直接抛 `category_navigation_not_loaded`，服务层 fallback 主路径基本不可达，但残留代码和测试仍会误导后续维护；失败快照不足，无法复盘卡在哪一级。

修复内容：

- `service.py`
  - 删除类目失败后按末级类目词重新搜索的 fallback。
  - `not_found`、`click_failed`、`not_reported`、`partial_clicked` 均直接抛 `category_navigation_not_loaded`。
  - 保留 RPA 返回的 `category_steps`、`diagnostics`、页面可见类目文本。
  - 当前页兜底读取不再把用户本地选中的类目自动补写成商品真实类目。

- `rpa_collect.mjs`
  - 类目点击改用 Playwright `locator.hover()` / `locator.click()`，减少纯 DOM 事件无法触发 1688 菜单的问题。
  - 类目失败返回当前 URL、期望路径、命中路径、命中层级、每级步骤和页面可见类目文本。
  - 类目点击成功但商品卡片未加载时，也会随 `category_navigation_not_loaded` 返回前面的类目执行记录和诊断。
  - `内衣`、`睡衣家居服` 增加页面可见同义入口候选，如 `服饰内衣`、`睡衣/家居服`。

- `web.py`
  - 采集失败也保存本地 `tag_collect_error_<timestamp>.json` 快照，便于后续排查。
  - 前端失败时保留并展示筛选执行记录，不再清空类目诊断。

- `cmd.py`
  - CLI 默认真实采集源保持 `rpa`，只传类目时默认走自动类目流程。

验证结果：

```bash
python3 -m py_compile scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/rpa.py scripts/capabilities/tag_collect/smoke_test.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
bash -n scripts/capabilities/tag_collect/start_chrome_debug.sh
git diff --check
python3 scripts/capabilities/tag_collect/smoke_test.py
```

真实低频验证：

```bash
env TAG_COLLECT_CDP_URL=http://127.0.0.1:9222 TAG_COLLECT_RPA_MAX_PAGES=1 TAG_COLLECT_RPA_PACING=fast TAG_COLLECT_RPA_TIMEOUT=90 \
  python3 cli.py tag_collect --collect-source rpa --categories "内衣>睡衣家居服" --max-items-per-query 1 --output-format xlsx
```

结果：返回 `category_navigation_not_loaded`，`run_id` 为空、`row_count=0`，没有导出错误类目数据。说明当前 1688 页面仍未成功命中该类目入口，但错误行为已经从“可能降级关键词/导出不准数据”收紧为“阻断并返回诊断”。

## 2026-06-28 类目扩展与别名复核

用户要求继续补全类目，并修复 `内衣>男士内裤` 这类路径可能没有按 1688 左侧类目逐级命中的问题。

修复内容：

- `category_dict.json`
  - 从少量种子扩展为 24 个新增一级类目、134 个二级类目、417 个三级类目的本地扩展种子。
  - 服务启动后与 legacy seed 合并，`TAG_CATEGORY_TREE` 当前透出 50 个一级类目。
  - 字典状态标记为 `expanded_seed_needs_official_sync`，明确不是 1688 官方实时全量字典。

- `rpa_collect.mjs`
  - 拆分 `男士内裤` 与 `男士平角裤`、`男士三角裤` 的同义词。
  - `男士内裤` 只作为二级类目匹配，不再把平角裤/三角裤当作二级兜底，避免选二级时误点到三级。

- `smoke_test.py`
  - 新增类目字典覆盖度回归，要求一级类目不低于 45 个。
  - 新增 `/api/options` 类目树透出回归，覆盖 `内衣>男士内裤>男士平角裤`、`女装/女士精品>防晒衣>冰丝防晒衣`。
  - 新增 RPA 脚本断言，防止 `男士内裤` 二级别名再次混入三级类目。

验证结果：

```bash
python3 -m json.tool scripts/capabilities/tag_collect/category_dict.json >/tmp/category_dict.validated.json
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/rpa.py scripts/capabilities/tag_collect/smoke_test.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
git diff --check
```

本轮未跑会访问真实 1688 的完整 smoke，避免继续触发平台风控；已运行本地关键回归子集，覆盖类目树、API 透出、类目不进搜索框、类目失败阻断导出、多类目独立 RPA 任务。

## 2026-06-28 改回 1688 左侧类目源

用户进一步确认：不要继续用店雷达式运营类目做采集入口。店雷达可以这样设计，是因为它背后有一套类目映射和筛选映射；当前项目没有这套映射时，使用 `女装/女士精品`、`男士内裤` 这种运营类目名会导致 RPA 在 1688 上命中宽泛父类，如 `内衣`，从而导出错误结果。

本轮修复：

- `category_dict.json`
  - 已改为从 1688 首页公开 `window.$data.page.*.treeData.l1s` 同步。
  - 当前字典版本为 `1688-home-left-nav-2026-06-28`，状态为 `synced_from_1688_homepage_left_nav`。
  - 当前透出 10 个组合一级、155 个二级、1919 个三级：`女装、男装、内衣`、`配饰、鞋、箱包`、`运动户外、玩具童装`、`办公文化、宠物园艺`、`美妆个护、收纳清洁`、`食品酒水、餐饮生鲜`、`日用餐厨、居家日用`、`家用电器、数码电脑`、`家装灯饰、家纺家饰`、`汽车用品、工业用品`。
  - 这不是开放平台官方全量类目接口，但已经与当前 1688 首页左侧导航数据源一致，优先保证本地可选类目与真实页面可点击入口一致。

- `service.py`
  - 有有效 `category_dict.json` 时，不再合并 legacy 类目。
  - 防止 `女装/女士精品`、`鞋靴`、`箱包皮具` 这类旧运营类目混入页面。
  - 类目路径提交前必须存在于当前字典；不存在则返回 `category_path_invalid`，不会改成关键词搜索或放大到父类继续采集。
  - 无效路径建议按末级语义排序：例如 `女装、男装、内衣>内裤>男士内裤` 当前不存在时，会优先建议 `男士平角裤`、`男士阿罗裤`、`男士大码内裤`、`男士三角裤` 等真实存在的 1688 三级类目。
  - 给关键筛选字段增加 `applicable_roots`，让前端展示当前筛选与所选一级类目的适用关系。

- `rpa_collect.mjs`
  - 移除 `女装 + 二级类目` 的旧兜底候选。
  - 移除 `家居日用品` 旧别名，避免新旧类目混用。

- `web.py`
  - 筛选字段根据已选类目的一级入口显示“适用于当前类目/当前类目未确认”。
  - 该提示只作为 UI 验收辅助，不替代真实 1688 页面点击结果；最终仍以 `filter_results` 的 `clicked/not_found/click_failed` 为准。

当前策略：

- 类目路径必须按 1688 一二三级真实文本点击。
- 如果页面没有对应层级，任务阻断并返回诊断，不改用搜索框，不导出宽泛父类数据。
- 店雷达类目只作为产品设计参考，不再作为 RPA 采集入口字典。

验证结果：

```bash
python3 scripts/capabilities/tag_collect/smoke_test.py
python3 -m json.tool scripts/capabilities/tag_collect/category_dict.json >/tmp/category_dict.validated.json
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/rpa.py scripts/capabilities/tag_collect/smoke_test.py scripts/capabilities/tag_collect/sync_1688_categories.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
git diff --check
```

本地 API 复核：

- `/api/options` 返回类目字典 `1688-home-left-nav-2026-06-28`。
- 一级类目数量为 10，旧运营类目 `女装/女士精品` 不再透出。
- `女装、男装、内衣>内裤>男士平角裤` 已存在。
- `女装、男装、内衣>内裤>男士内裤` 不存在，会在提交前阻断并给真实相近类目建议。
