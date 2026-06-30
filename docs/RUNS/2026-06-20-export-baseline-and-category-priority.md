# 2026-06-20 导出基线与类目优先采集记录

## 触发问题

用户反馈：

- 查询结果很多字段是 `待详情页核验`，希望确认是否能直接取到。
- 店雷达平台可以导出这些字段，当前项目导出缺少链接相关字段。
- 导出字段需要参照用户此前提供的店雷达 `.xls` 附件。
- 品类检索仍像是在搜索框里搜品类，要求按照 1688 左侧类目栏逐级点击。

## 多 Agent 评审

Workflow：

```text
2026-06-20T09-04-15Z-tag-collect-next-63963b
```

结论摘要：

- Product Agent：下一片应先处理字段可信度、导出表头和详情核验闭环，不应先做多人化或铺货。
- Architect Agent：要求将列表字段、详情字段、可信来源字段区分清楚；不得把未核验字段当作可信值。
- Verification Agent：指出文档旧口径 79/81 列与代码不一致，必须以用户 `.xls` 表头为准；链接列要校验表头和值；类目路径要真实浏览器验收。

## 附件表头核对

已用 `xlrd` 只读解析用户附件：

```text
店雷达_1688选品库-近30天_2026-06-11_154544.xls
```

第一行共 37 列：

```text
序号、商品类目、商品主图、商品标题、商品ID、商品链接(点击下方链接可跳转)、上架时间、批发价、起批范围、近30天订单数、近30天件数、近30天销售额、销售趋势(件)、复购率、代发价、权益保障、代发权益、面单支持、商品标识、资质证书、店铺名称、所在地、综合服务、公司类型、卖家会员类型、卖家服务、诚信通年限、店铺链接(点击下方链接可跳转)、主图链接(点击下方链接可跳转)、月代发订单、SKU数量、收藏客户、评论数、库存、查询视频、批发运费、代发运费
```

当前导出策略：

- `选品结果` 前 37 列严格等于店雷达附件表头。
- 项目扩展字段追加在 37 列之后。
- 当前总列数：82。
- 链接字段位置：
  - 第 6 列：商品链接
  - 第 28 列：店铺链接
  - 第 29 列：主图链接
  - 第 31 列：SKU数量

## 修复内容

- `service.py`
  - 新增 `DIANLEIDA_REFERENCE_EXPORT_LABELS` 和对应 key 列表。
  - `EXPORT_COLUMNS` 改为“店雷达 37 列基线 + 项目扩展字段”。
  - 将 `shop_url`、`sku_count` 纳入 `DETAIL_ONLY_FIELDS`。
  - 详情核验前 `SKU数量`、`店铺链接` 保持 `待详情页核验`。
  - 样例详情库补充 `sku_count`、`shop_url`，确保开发回归字段完整。

- `rpa_collect.mjs`
  - 有 `category_filters` 时优先打开 1688 首页执行类目点击。
  - 不再先把类目或类目相关查询塞进搜索框。

- `rpa_detail.mjs`
  - 详情页启发式补充 `SKU数量`。
  - 从详情页链接区域启发式提取 `店铺链接`。

- `smoke_test.py`
  - 断言导出列数为 82。
  - 断言前 37 列严格等于店雷达附件表头。
  - 断言商品链接、店铺链接、主图链接、SKU数量位置正确。
  - 断言有类目路径时 RPA 类目导航分支排在关键词搜索框之前。

- 文档
  - 更新 `docs/tag-selection-collector-progress.md` 的旧 79/81 列口径。
  - 更新 PRD 中 `3.7 SKU数量`、`3.8 收藏客户`。

## 关于“待详情页核验”

当前项目不是店雷达自有选品库，也没有店雷达的历史缓存或内部接口。店雷达能导出更多字段，通常是因为它有自己的数据源、缓存、账号能力或接口聚合。

项目当前原则：

- 搜索列表能稳定读取的字段可以直接填，如商品ID、标题、商品链接、主图链接、批发价、部分销量/订单指标。
- 运费、品退率、发货率、发货时效、一件代发、起批范围、SKU数量、店铺链接、商家可靠性等字段，必须详情页/商家页/可信来源核验后才写为可信值。
- 如果真实页面读不到，保留 `待详情页核验` 或失败原因，不用样例数据或规则猜值填充。

## 已验证

已通过：

```bash
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py scripts/capabilities/tag_collect/rpa.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
node --check scripts/capabilities/tag_collect/rpa_detail.mjs
python3 scripts/capabilities/tag_collect/smoke_test.py
git diff --check
```

服务已重启：

```text
http://localhost:8765/
```

健康检查：

```text
/api/options success=true
runtime=真实 Chrome CDP
cdp_ready=true
columns=82
first37_ok=true
商品链接位置=6
店铺链接位置=28
主图链接位置=29
```

## 剩余风险

- 真实 1688 页面仍可能触发登录、滑块、访问拒绝；系统不会绕过风控。
- `partial_clicked` 代表只命中上层类目，不能当作精确叶子类目完成；当前已升级为阻断采集和导出，不再进入主结果。
- 详情页字段提取是启发式，页面结构变化会导致字段缺失；缺失时必须人工复核。
