# 跨能力数据流契约

能力之间通过以下标识符传递数据。新增能力如有跨能力依赖，**必须在此文档注册**。

## 核心数据标识符

| 标识符 | 产出方 | 消费方 | 格式 | 说明 |
|--------|--------|--------|------|------|
| `data_id` | search | publish | `YYYYMMDD_HHMMSS_mmm` | 搜索结果快照 ID（含毫秒） |
| `product_id` | search 的 `data.products[].id` | publish | 纯数字字符串 | 1688 商品 ID |
| `shop_code` | shops 的 `data.shops[].code` | publish | 纯数字字符串 | 下游店铺代码 |

## 数据存储

| 数据类型 | 存储路径 | 格式 |
|---------|---------|------|
| 搜索快照 | `{SEARCH_DATA_DIR}/1688_{data_id}.json` | `{"query", "channel", "timestamp", "data_id", "products": {id: {...}}}` |
| 铺货排查快照 | `{PUBLISH_DATA_DIR}/1688_{time}.json` | **仅正式铺货**（未带 `--dry-run`）写入；字段含 time、api_request、api_response、meta、cli_output；CLI 的 data.time 与文件名一致 |
| 标签选品任务快照 | `{TAG_COLLECT_DATA_DIR}/tag_collect_{run_id}.json` | 采集配置、筛选执行记录、结果 rows、详情核验记录、自动化状态、导出路径 |
| 标签选品历史筛除池 | `{TAG_COLLECT_DATA_DIR}/rejected_products.json` | `{"version", "updated_at", "items": {"item_id": [{"reason_code", "reason_text", "target_platform", "filter_signature", "source_run_id", "created_at"}]}}`；用于同一筛选签名下跳过历史不合格商品 |

新能力如需持久化数据，必须在此注册路径和格式。

## 能力依赖图

```
configure ← (所有能力都依赖 AK)
search → publish       (data_id / product_id)
shops  → publish       (shop_code)
```

新增能力的前置依赖必须在此声明。
