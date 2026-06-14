# SOP 项目中文说明

## 1. 项目定位

SOP 是一个面向 1688 选品运营的本地工具项目。当前测试口径已经调整为真实数据优先：

- 默认通过 Playwright/RPA 打开真实 1688 搜索页面采集候选商品。
- 如遇 1688 登录、扫码或安全验证，需要用户在弹出的浏览器中处理。
- 高潜或真实页面候选商品可以继续进入真实商品详情页核验。
- 运费、品退率、发货率等字段只从真实详情页或明确来源补充；提取不到就标记失败或 `partial_verified`，不会用样例数据补齐。
- 导出 Excel/CSV 后，再由运营进行人工复核。

项目不是绕过 1688 风控的爬虫，也不做验证码绕过、账号密码保存或高频抓取。RPA 的用途是按运营筛选条件低频采集和核验真实页面。

## 2. 已实现功能

### 2.1 标签选品 Web 工作台

启动命令：

```bash
python3 cli.py tag_collect --serve --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

已支持：

- 一级类目复选。
- 二级类目复选。
- 运营筛选标签复选。
- 搜索词、排除标签、查询词上限、每词商品上限配置。
- 默认真实数据模式：`1688 页面 RPA`。
- 可选真实采集来源：`1688 页面 RPA` 或 `1688 AK/API`。
- 结果表格筛选：商品/类目、最低分、推荐等级、核验状态、铺货建议。
- 字段编号页，字段按 `1.1`、`1.2`、`2.1` 方式编号。
- 下载 XLSX 或 CSV。
- “真实详情核验”按钮，用于进入真实商品详情页补充关键字段。

### 2.2 真实页面采集

默认路径：

```text
Web 工作台
-> 选择类目和标签
-> 点击真实采集
-> Playwright 打开 1688 搜索页
-> 从真实页面提取候选商品
-> 去重、评分、标记风险
-> 进入详情核验队列
-> 导出表格
```

命令行也默认走真实页面 RPA：

```bash
python3 cli.py tag_collect --categories "家居日用品" --tags "微信小店,一件代发" --keywords "雨衣"
```

如果明确要走 AK/API：

```bash
python3 cli.py tag_collect --collect-source api --categories "家居日用品" --tags "微信小店"
```

开发样例模式仍保留给回归测试，但正式验收不要使用：

```bash
python3 cli.py tag_collect --sample-data --categories "女装/女士精品" --tags "微信小店"
```

### 2.3 真实详情页核验

点击“真实详情核验”后，会对核验队列中的商品打开真实 1688 商品详情页，尝试提取：

- 批发运费
- 代发运费
- 是否包邮
- 品退率
- 24 小时揽收率
- 发货率
- 发货时效
- 是否一件代发
- 起批范围
- 店铺名称
- 所在地
- 公司类型
- 卖家会员类型
- 是否源头工厂
- 库存

当前详情页解析是启发式第一版。页面结构、登录状态和字段展示位置不同，可能导致只提取到部分字段。系统会保留字段级记录，不会把缺失字段填成看似可信的值。

## 3. 导出文件

XLSX 当前包含 5 个工作表：

- `选品结果`
- `字段说明`
- `标签配置`
- `核验失败`
- `核验记录`

导出流程：

```text
标签模板配置
-> 创建采集任务
-> 标签组合生成搜索词和过滤规则
-> 打开真实 1688 页面采集候选商品
-> 列表字段初筛
-> 商品去重
-> 高潜/真实页面候选商品进入详情页核验队列
-> 真实详情页补充关键字段
-> 综合评分和风险标记
-> 导出 Excel 或 CSV
-> 人工复核
-> 可选：进入铺货 dry-run 流程
```

人工复核在导出之后进行。表格中预留：

- `人工复核状态`
- `人工复核备注`
- `微信小店铺货建议(人工复核)`

## 4. 字段真实性

当前用字段状态区分数据可信度：

- `unverified`：未进入详情页核验，只能作为初筛数据。
- `partial_verified`：真实详情页只提取到部分字段，缺失字段必须人工复核。
- `verified`：真实详情页字段提取完成。
- `failed`：详情核验失败或字段缺失。
- `sample_verified`：仅开发样例模式使用，正式验收不要作为真实结果。

关键字段在详情核验前显示为 `待详情页核验`。提取不到的字段会记录 `fail_reason`，不会用样例数据补齐。

## 5. 环境安装

### 5.1 Python 依赖

```bash
pip install -r requirements.txt
```

### 5.2 Playwright 依赖

真实页面采集需要 Node.js 和 Playwright：

```bash
npm install
npm run install-browsers
```

默认浏览器 profile：

```text
~/.sop-1688-rpa-profile
```

这会保留浏览器登录态。第一次打开 1688 时如果需要登录，请在弹出的浏览器中扫码。

如果一直触发校验，推荐改用真实 Chrome 调试窗口：

```bash
scripts/capabilities/tag_collect/start_chrome_debug.sh
export TAG_COLLECT_CDP_URL=http://127.0.0.1:9222
```

然后在打开的 Chrome 中手动登录 1688，完成扫码和安全验证。后续采集会连接这个已登录 Chrome 窗口，而不是使用 Playwright 自带浏览器。

如果账号一直登录不上或总触发安全校验，可以先用 URL 模式测试真实页面解析：

```bash
python3 cli.py tag_collect \
  --source-urls "https://detail.1688.com/offer/商品ID.html" \
  --tags "微信小店,一件代发" \
  --max-items-per-query 1
```

Web 工作台中也可以把浏览器里能打开的 1688 搜索页或商品详情页粘贴到“1688 页面 URL”。URL 模式仍然读取真实页面，不会回退到样例数据；如果公开页面没有展示运费、品退率、发货率等字段，这些字段会继续标记为待核验或失败。

可选环境变量：

```bash
export TAG_COLLECT_RPA_PROFILE="$HOME/.sop-1688-rpa-profile"
export TAG_COLLECT_CDP_URL="http://127.0.0.1:9222"
export TAG_COLLECT_RPA_LOGIN_WAIT_MS=90000
export TAG_COLLECT_RPA_TIMEOUT=180
```

## 6. 在其他设备测试

同一台电脑测试：

```bash
python3 cli.py tag_collect --serve --port 8765
```

同一局域网其他设备访问：

```bash
python3 cli.py tag_collect --serve --host 0.0.0.0 --port 8765
```

然后访问：

```text
http://<运行电脑的局域网 IP>:8765
```

注意：

- 真实采集默认只允许本机地址开启；局域网访问适合看页面和导出，不建议暴露真实采集能力。
- 不要把服务暴露到公网。
- 不要共享 AK、浏览器 profile 或本地登录态。

## 7. 风控和封号风险

真实页面采集会访问 1688 页面，存在平台风控风险。建议：

- 只采集必要类目和关键词，不高频刷页。
- 优先小批量测试，例如每词 5-20 个商品。
- 只对候选商品做详情核验，不全量打开详情页。
- 遇到验证码、安全验证、账号异常提示时立即暂停。
- 不绕过验证码，不模拟恶意高频访问。
- 使用真实账号扫码登录，不保存账号密码。

## 8. 当前边界

已接入真实页面 RPA 第一版，但仍有边界：

- 1688 页面结构变化会影响提取准确性。
- 详情字段是启发式解析，需继续针对真实页面结构优化。
- 在线文档同步尚未接入，目前是 XLSX/CSV 导出。
- 官方完整 1688 类目树尚未自动同步。
- 多人账号、权限、任务队列、审计日志尚未产品化。
- 自动铺货只建议先做 dry-run，不建议直接批量写入。

## 9. 多 agent 流程

项目保留多 agent workflow：

```text
.codex-workflows/workflows/tag-collect-next.workflow.js
```

角色包括：

- 产品 agent：确认需求、验收标准和不该提前做的功能。
- 架构 agent：评审数据结构、导出、RPA 和详情核验架构。
- 验证/风控 agent：补测试用例、风险点和用户可见验收点。

运行记录目录 `.codex-workflows/runs/` 不提交到 GitHub，只保留 workflow 模板。

## 10. 发布前检查

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/pycache-1688-shopkeeper \
  python3 -m py_compile scripts/capabilities/tag_collect/service.py \
  scripts/capabilities/tag_collect/web.py \
  scripts/capabilities/tag_collect/cmd.py \
  scripts/capabilities/tag_collect/rpa.py \
  cli.py

python3 scripts/capabilities/tag_collect/smoke_test.py
```

不要提交：

- AK 或任何真实密钥。
- `.env`、`.env.local`、`.env.*`。
- `.local-data/`。
- `1688-skill-data/`。
- `.codex-workflows/runs/`。
- `output/` 截图和本地验收产物。
- XLSX、CSV、日志、采集快照 JSON。
