# 2026-07-11 商品类目证据三态修复

## 目标

- 解决 1688 叶子类目任务中“商品没有类目证据就被当成错类目”的假阴性。
- 保持当前本地单机工作台边界，不引入云端服务、远程队列或多人权限模型。
- 不进行真实 1688 跑批，不绕过登录、滑块、验证码或平台风控。

## 根因

- 列表采集此前主要依赖商品标题、`rawText` 和空的 `categoryListName` 做布尔判断。
- “明确属于其他类目”与“页面没有返回商品级类目证据”都被归为 `product_category_mismatch`。
- 误判商品会立即写入本地历史拒绝池，后续相同筛选签名下被永久跳过。
- 详情页此前不提取官方类目 ID、类目路径或面包屑，无法在低频详情核验时纠正列表判断。

## 已实现

### 三态类目门禁

- `matched`：官方类目 ID 一致、结构化类目路径一致，或无结构化证据时标题强正向命中。
- `mismatched`：官方类目 ID 或结构化商品类目路径明确冲突。
- `unknown`：没有足够商品级结构化证据且标题也不能正向确认。

证据优先级：一致的官方数字类目 ID、商品结构化路径/面包屑、标题强正向词。ID 与路径冲突时不强行放行或拒绝：路径一致可消解不同 ID，缺少路径或 ID/路径自相矛盾时进入 `unknown`。标题未命中不再作为负向确证。

### 数据隔离

- 主 `rows/top_items/publishable_count` 只保留 `matched`。
- `unknown` 进入 `category_unknown_rows`，仍进入详情核验队列，不写本地拒绝池。
- 只有 `mismatched` 才进入 `filter_excluded_rows`，并可写入历史拒绝池。
- Excel 主表不包含 `unknown`；有待核验商品时新增 `类目待核验` sheet。

### RPA 类目证据

- 列表类目导航从叶子链接、结果 URL 和页面结构化信息提取纯数字官方 `category_id`。
- 商品卡片仅从卡片自身 data 属性、类目链接或与 offerId 相邻的结构化数据提取：
  - `productCategoryId`
  - `productCategoryPath`
  - `productCategoryBreadcrumb`
  - `productCategorySource`
- 详情页返回：
  - `product_category_id`
  - `product_category_path`
  - `product_category_breadcrumb`
  - `category_evidence_source`
- 本地类目字典的 `l1_*` 节点 ID 不参与官方 ID 比较。

### 详情状态迁移

- `unknown -> matched`：从待核验区迁入主结果。
- `unknown -> mismatched`：迁入筛除区并记录拒绝原因。
- `matched -> mismatched`：详情结构化证据覆盖列表标题判断并移出主结果。
- `unknown -> unknown`：继续保留在待核验区。
- 详情页成功读取但仍没有类目证据时，记录一次 `inconclusive` 并停止自动重复访问；登录/安全验证失败仍保留为可人工处理后重试。

### Web 展示

- 商品类目单元格显示：`类目匹配`、`类目待核验`、`类目不匹配`。
- 运行摘要显示 `类目待核验` 数量。

## 验证

未访问真实 1688，完成本地离线验证：

```bash
git diff --check

env PYTHONPYCACHEPREFIX=/private/tmp/pycache-1688-native \
  .tmp/venv/bin/python -m py_compile \
  scripts/capabilities/tag_collect/service.py \
  scripts/capabilities/tag_collect/web.py \
  scripts/capabilities/tag_collect/rpa.py \
  scripts/capabilities/tag_collect/smoke_test.py cli.py

env PYTHONPYCACHEPREFIX=/private/tmp/pycache-1688-native \
  .tmp/venv/bin/python scripts/capabilities/tag_collect/smoke_test.py

node --check scripts/capabilities/tag_collect/rpa_collect.mjs
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
```

结果：`tag_collect smoke tests passed (55 tests)`。

## 待用户验证

- 启动本地工作台：`.tmp/venv/bin/python cli.py tag_collect --serve --port 8765`。
- 使用低频、少量候选测试真实叶子类目，重点观察列表卡片是否能提供官方类目 ID/路径。
- 若 1688 页面仍不暴露商品级类目证据，商品会进入 `类目待核验`，由详情页低频补证；不会再靠遍历试错直接永久筛除。
- 遇到登录、滑块、验证码或访问拒绝时停止并人工处理，不自动重试或绕过。
