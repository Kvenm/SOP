# Agent Boundaries

本文件定义 SOP 项目中多 Agent 的角色、权限和文件边界。

## 角色

| Role | 默认权限 | 职责 | 是否可改文件 |
|---|---|---|---|
| 主控 Agent | 全局读，有限写 | 任务拆解、集成、验证、知识库更新 | 可以 |
| Product Agent | 只读 | 需求、页面能力对标、验收标准 | 默认不改 |
| Architect Agent | 只读 | 模块边界、接口契约、风控和数据可信链路 | 默认不改 |
| Builder Agent | 指定路径写 | 实现功能或修复问题 | 可以 |
| Reviewer Agent | 只读 | 审查 diff、测试、接口和风险 | 不改 |

## 文件边界

### 标签选品后端

允许：

```text
scripts/capabilities/tag_collect/service.py
scripts/capabilities/tag_collect/rpa.py
scripts/capabilities/tag_collect/rpa_collect.mjs
scripts/capabilities/tag_collect/rpa_detail.mjs
scripts/capabilities/tag_collect/smoke_test.py
references/capabilities/tag_collect.md
docs/tag-selection-collector-*.md
```

### 标签选品前端

允许：

```text
scripts/capabilities/tag_collect/web.py
docs/tag-selection-collector-*.md
```

当前 Web 页面是嵌入 `web.py` 的本地单页工作台；如拆成前后端分离，需要先由 Architect Agent 输出接口契约和迁移计划。

### 项目知识库

允许：

```text
AGENTS.md
docs/LOOP.md
docs/KNOWLEDGE_MAP.md
docs/AGENT_BOUNDARIES.md
docs/TESTING.md
docs/RISKS.md
docs/RUNS/**
docs/DECISIONS/**
```

## 禁止事项

- 不提交 `.local-data/`、导出的真实表格、采集快照、cookie、AK、浏览器 profile。
- 不绕过 1688 登录、滑块、验证码或安全风控。
- 不用样例数据替代真实采集结果。
- 不让两个 Builder 同时修改同一文件。
- 不把在线文档或私密资料原文写入仓库，除非用户明确要求且确认可公开。
