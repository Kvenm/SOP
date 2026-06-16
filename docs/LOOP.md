# Loop Protocol

本项目按 Loop Engineering 协议执行，用于让产品、架构、实现、审查和知识更新形成可重复流程。

## 标准循环

```text
Trigger -> Context Load -> Plan -> Scoped Work -> Validate -> Review -> Integrate -> Knowledge Update -> Next Trigger
```

## Trigger

当前默认由人工命令触发。长期或高风险任务可使用 git worktree 隔离：

```bash
git worktree add ../.worktrees/SOP-<task-id> -b agent/<task-id> main
```

## Context Load

每次启动先读：

- `AGENTS.md`
- `docs/KNOWLEDGE_MAP.md`
- `docs/AGENT_BOUNDARIES.md`
- `docs/TESTING.md`
- `docs/RISKS.md`

标签选品任务还要读：

- `docs/tag-selection-collector-prd.md`
- `docs/tag-selection-collector-progress.md`
- `references/capabilities/tag_collect.md`

## Plan

改动前必须明确：

- 任务目标和非目标。
- 可修改文件范围和禁止修改范围。
- 验收命令。
- 是否需要外部网络、浏览器、真实账号登录、人工扫码或安全验证处理。

## Scoped Work

- Product Agent：需求拆解、页面能力对标、验收标准。
- Architect Agent：模块边界、接口契约、数据流、风险。
- Builder Agent：指定路径内实现。
- Reviewer Agent：只读审查 diff、接口一致性、测试和风险。

## Validate

按 `docs/TESTING.md` 运行对应验证。真实 1688 采集需要已登录浏览器和人工处理平台验证，不作为自动测试前置。

## Knowledge Update

任务结束时按实际需要更新：

- `docs/RUNS/<run-id>.md`
- `docs/tag-selection-collector-progress.md`
- `docs/RISKS.md`
- `docs/DECISIONS/`
- `docs/KNOWLEDGE_MAP.md`
