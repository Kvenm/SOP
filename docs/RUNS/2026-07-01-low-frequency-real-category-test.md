# 2026-07-01 低频真实类目测试记录

## 执行范围

- 测试计划：`docs/1688-low-frequency-real-test-plan.md`
- 测试目标：字段 `1`、`1.1`、`1.2`、`1.3`、`1.4`
- 真实环境：真实 Chrome CDP，`http://127.0.0.1:9222`
- 采集频率：每次只测 1 个类目、1 条商品，不翻页，不做压测

## 本地回归

```bash
python3 scripts/capabilities/tag_collect/smoke_test.py
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py cli.py
node --check scripts/capabilities/tag_collect/rpa_collect.mjs
```

结果：通过。

## 真实低频测试

### 配饰、鞋、箱包 > 围巾/防晒 > 防晒衣

```bash
env TAG_COLLECT_CDP_URL=http://127.0.0.1:9222 TAG_COLLECT_RPA_MAX_PAGES=1 TAG_COLLECT_RPA_PACING=slow TAG_COLLECT_RPA_PAGE_TIMEOUT_MS=30000 TAG_COLLECT_RPA_TIMEOUT=120 python3 cli.py tag_collect --collect-source rpa --categories '配饰、鞋、箱包>围巾/防晒>防晒衣' --max-queries 1 --max-items-per-query 1 --target-publishable-count 1 --output-format xlsx
```

结果：失败，`1688 类目导航未完成`，未生成 run_id/Excel。

### 女装、男装、内衣 > 内裤 > 男士平角裤

```bash
env TAG_COLLECT_CDP_URL=http://127.0.0.1:9222 TAG_COLLECT_RPA_MAX_PAGES=1 TAG_COLLECT_RPA_PACING=slow TAG_COLLECT_RPA_PAGE_TIMEOUT_MS=30000 TAG_COLLECT_RPA_TIMEOUT=120 python3 cli.py tag_collect --collect-source rpa --categories '女装、男装、内衣>内裤>男士平角裤' --max-queries 1 --max-items-per-query 1 --target-publishable-count 1 --output-format xlsx
```

结果：失败，`1688 类目导航未完成`，未生成 run_id/Excel。

## 诊断结论

- 真实 Chrome/CDP 可连接，1688 首页可打开，没有在本轮触发访问拒绝。
- 当前失败点在 RPA 类目导航，不在导出字段。
- 首页可见“男士平角裤”等真实类目链接，但当前 RPA 点击链路没有完成进入商品结果页。
- 本轮未生成数据，符合真实性原则：没有把失败结果导出成真实商品数据。

## 下一步

- 修复 `scripts/capabilities/tag_collect/rpa_collect.mjs` 中首页类目浮层的二级/三级节点定位与点击确认。
- 修复后只复测字段 `1`、`1.1`、`1.2`、`1.3`、`1.4`，继续保持低频。
