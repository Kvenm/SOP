# 2026-06-28 可铺目标数与历史筛除池

## 触发

用户要求确认并实现：

- 先筛选再分析。
- 采集数量按“满足微信小店投放/可铺”的数量计算。
- 没通过筛选的商品下次不要重复筛。
- 尽量减少人工复核，系统先自动分析和剔除。

## 多 Agent 评审

- Product Agent 结论：当前链路只是“采集候选后打建议”，未做到“采够可铺数量”；历史筛除只在当前批次内存在，不能跨批次跳过。
- Architect Agent 结论：需要新增 `target_publishable_count`、持久化排除池、详情后重评分；但为了风控不能无限补采，必须有候选预算和停止原因。

## 实现

- 新增 `target_publishable_count`，Web “目标可铺数”会传入后端。
- `max_items_per_query` 保留为候选扫描预算；后端按目标适度放大本轮候选扫描，最多仍受 `MAX_ITEMS_PER_QUERY=50` 限制。
- 目标达标按 `wechat_shop_suggestion == 可铺` 统计；`谨慎` 保留为候选复核，不算满足投放。
- 新增 `{TAG_COLLECT_DATA_DIR}/rejected_products.json`，记录明确不合格商品及原因；同一 `filter_signature + target_platform` 下下次采集会跳过。
- 详情核验后重评估筛选并重算推荐分、推荐等级、微信小店建议，写入 `rescore_records`。
- 导出 `标签配置` sheet 增加目标可铺数、候选扫描、历史跳过、本次筛除、停止原因、短缺原因、重评分记录。

## 边界

- 不把详情页风控、登录失败、字段未提取写入排除池。
- 不做验证码绕过、滑块破解、高频重试或无限翻页补采。
- 历史筛除池当前是单机 JSON；多人共享后续需升级 SQLite/服务端表。

## 验证

已通过：

```bash
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/cmd.py scripts/capabilities/tag_collect/smoke_test.py
python3 scripts/capabilities/tag_collect/smoke_test.py
git diff --check
```

新增 smoke 覆盖：

- 目标可铺数量按可铺结果统计，并会扩大候选预算。
- 历史筛除商品下次跳过，并返回跳过明细。
- 详情核验后品退率/发货率风险会降低推荐分并生成 `rescore_records`。

## 追加：自动复核收敛

用户反馈导出结果里仍有太多“待复核”，要求系统能核验的都自动核验、能判断的都自动判断。

实现调整：

- 新增自动复核归一化规则：
  - `系统预通过`：微信小店建议为 `可铺`，关键履约/售后详情字段已核验，且无筛选冲突或阻断风险。
  - `系统不建议`：微信小店建议为 `不建议`，不再默认交给人工二次判断。
  - `系统剔除`：详情筛选未通过的商品从主结果移入剔除/异常审计。
  - `待复核`：仅保留 `谨慎`、风控/登录/详情失败、关键字段缺失、筛选规则待确认、履约售后边界风险。
- 采集初始行、详情重评分后、导出刷新前都会调用同一套自动复核状态归一化，避免入口漏判。
- Web 自动核验默认上限从 5 调整为 20；如果用户显式设置更小的上限，则尊重用户设置。
- XLSX `标签配置` 增加系统预通过、系统不建议、系统剔除、待人工复核数量。
- XLSX 第 4 个 sheet 从 `核验失败` 改为 `异常复核`，只放异常、边界、筛选剔除和待人工复核商品；普通“待详情页核验”不再被误当作失败清单。

验证：

```bash
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/cmd.py scripts/capabilities/tag_collect/smoke_test.py cli.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
python3 scripts/capabilities/tag_collect/smoke_test.py
git diff --check
```

剩余风险：

- 真实 1688 详情页仍受登录、滑块、账号/IP 风控影响；系统不会绕过验证码或高频重试。
- “系统预通过”只代表当前可见字段和规则通过，不等于最终经营担保；人工应重点抽查异常复核和边界商品。
