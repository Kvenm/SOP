# Agent Instructions

本文件是 SOP 项目的 Agent 入口，只放硬约束和文档路由。

## 开工必读

每次开始任务前，先读取：

1. `docs/LOOP.md`
2. `docs/KNOWLEDGE_MAP.md`
3. `docs/AGENT_BOUNDARIES.md`
4. `docs/TESTING.md`
5. `docs/RISKS.md`

涉及标签选品、1688 真实采集、导出字段或详情核验时，再读：

- `docs/tag-selection-collector-prd.md`
- `docs/tag-selection-collector-progress.md`
- `references/capabilities/tag_collect.md`
- `docs/SOP-project-guide-cn.md`

## 工作规则

- 先确认目标、非目标、可改文件范围、禁止文件范围和验收命令。
- 多 Agent 工作必须先划分文件边界；Reviewer Agent 默认只读。
- 不回退、覆盖或清理用户和其他 Agent 的改动。
- 不提交密钥、AK、token、cookie、浏览器 profile、真实用户数据或真实采集导出文件。
- 不绕过 1688 登录、滑块、验证码或平台风控；遇到安全验证必须停止并提示人工处理。
- 不用样例数据冒充真实采集结果；不可采字段必须标记为待核验、预留或失败。

## 收尾要求

- 按 `docs/TESTING.md` 运行对应验证，或说明不能运行的原因。
- 重要任务更新 `docs/RUNS/` 运行记录。
- 若改变接口、风险或项目边界，同步更新对应文档。
