# Knowledge Map

这是 SOP 项目的 Agent 文档路由表。不要在这里写长历史，只指向可信文件。

## 项目基础

- 项目说明：`README.md`
- 中文运行与注意事项：`docs/SOP-project-guide-cn.md`
- Agent/Skill 编排入口：`SKILL.md`
- Loop 协议：`docs/LOOP.md`
- Agent 边界：`docs/AGENT_BOUNDARIES.md`
- 测试矩阵：`docs/TESTING.md`
- 风险和禁区：`docs/RISKS.md`
- 运行记录：`docs/RUNS/`
- 架构决策：`docs/DECISIONS/`

## 标签选品采集

- 产品需求：`docs/tag-selection-collector-prd.md`
- 当前进度：`docs/tag-selection-collector-progress.md`
- 能力说明：`references/capabilities/tag_collect.md`
- 服务实现：`scripts/capabilities/tag_collect/service.py`
- Web 工作台：`scripts/capabilities/tag_collect/web.py`
- RPA 列表采集：`scripts/capabilities/tag_collect/rpa_collect.mjs`
- RPA 详情核验：`scripts/capabilities/tag_collect/rpa_detail.mjs`
- 本地类目字典：`scripts/capabilities/tag_collect/category_dict.json`
- 回归测试：`scripts/capabilities/tag_collect/smoke_test.py`

## 其他能力

- 商品搜索：`references/capabilities/search.md`
- 商品详情：`references/capabilities/prod_detail.md`
- 店铺查询：`references/capabilities/shops.md`
- 铺货：`references/capabilities/publish.md`
- 趋势和商机：`references/capabilities/trend.md`、`references/capabilities/opportunities.md`

## 过期信息处理

如果文档和代码冲突：

1. 先以代码、测试和最近 `docs/RUNS/` 记录核对。
2. 最终回复说明冲突。
3. 更新对应文档或把问题写入 `docs/RISKS.md`。
