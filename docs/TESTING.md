# Testing Matrix

本文件定义 SOP 项目不同改动对应的验证命令。

## 通用检查

```bash
git status --short --branch
git diff --check
```

## 标签选品 Python 服务

```bash
python3 -m py_compile scripts/capabilities/tag_collect/service.py scripts/capabilities/tag_collect/web.py scripts/capabilities/tag_collect/smoke_test.py
python3 scripts/capabilities/tag_collect/smoke_test.py
```

## 标签选品 Web 工作台

启动：

```bash
python3 cli.py tag_collect --serve --port 8765
```

浏览器验证：

- 打开 `http://127.0.0.1:8765/`
- 检查“1688选品库”页面能显示类目、精准搜索、高级筛选、销售/商品/卖家筛选。
- 开启“开发样例”后点击“开始查询”，确认结果表格、筛选记录、下载表格可用。
- 真实采集测试需要用户在浏览器中登录 1688，并人工处理扫码、滑块或验证码。

## RPA/真实采集

真实采集不进入自动 smoke test。测试时必须确认：

- 使用真实 1688 页面或已登录浏览器。
- 遇到登录、安全滑块、验证码时停止并提示人工处理。
- 不导出安全验证页、登录页或错误页数据。
- 运费、品退率、发货率等关键字段必须进入详情页核验。

## 无法运行测试时

最终回复必须说明：

- 哪些测试没有运行。
- 为什么不能运行。
- 残余风险。
- 下一步建议运行什么。
