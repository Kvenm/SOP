# Run: 店雷达 1688 选品库对齐与 Loop 接入

## 触发

用户要求前端页面和筛选条件按店雷达 `1688选品库` 全功能设计，功能接口对齐，缺少能力预留接口，并使用 loop-engineering 接入项目。

## 目标

- 接入 Loop Engineering 最小项目文档层。
- 将店雷达选品库筛选拆成可调用接口契约。
- Web 工作台改为 1688 选品库筛选布局。
- 保留真实 1688 页面采集、详情核验、导出和样例测试链路。

## 非目标

- 不绕过 1688 登录、滑块、验证码或安全风控。
- 不伪造店雷达自有数据源字段。
- 不在本轮拆分独立前端工程。

## 文件范围

- `AGENTS.md`
- `docs/LOOP.md`
- `docs/KNOWLEDGE_MAP.md`
- `docs/AGENT_BOUNDARIES.md`
- `docs/TESTING.md`
- `docs/RISKS.md`
- `docs/RUNS/2026-06-16-dianleida-library-loop.md`
- `scripts/capabilities/tag_collect/service.py`
- `scripts/capabilities/tag_collect/web.py`
- `scripts/capabilities/tag_collect/smoke_test.py`

## 验收

- 通过：`python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`python3 scripts/capabilities/tag_collect/smoke_test.py`
- 通过：`git diff --check`
- 通过：本地浏览器打开 `http://127.0.0.1:8765/`，确认页面标题为 `1688选品库筛选工作台`，店雷达分区为 7 个，筛选控件 94 个，无重复 id。
- 通过：开发样例模式选择 `源头工厂`、`48小时`、订单数和批发价范围后点击 `开始查询`，生成 3 条结果，下载链接可见，筛选执行记录显示原生筛选。

## 风险记录

- 店雷达的增长率、采购集中率、关注商品、Temu 铺货等能力当前仅预留接口。
- 真实采集需要用户登录 1688，遇到安全校验必须人工处理。
