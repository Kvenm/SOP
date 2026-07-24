# 2026-07-15 Windows 实体机部署

## 目标

- 将当前 `1688-shopkeeper` 工作树部署到用户提供的 Windows 10 实体机。
- 让 Chrome/Edge、扫码登录、滑块和验证码能够在 Windows 交互桌面中人工处理。
- 工作台保持本机监听，通过 SSH tunnel 访问，不暴露 Web 或 CDP 端口。

## 非目标

- 不执行真实 1688 采集。
- 不自动登录、不绕过滑块/验证码或平台风控。
- 不迁移 AK、cookie、浏览器 profile、真实采集结果或导出文件。
- 不提交或推送 Git 改动。

## 本地实现

- `web.py` 增加 Windows 浏览器启动分支：优先 `CHROME_BIN`、Google Chrome，回退 Microsoft Edge；CDP 地址固定为 `127.0.0.1`。
- `rpa.py` 的 Python -> Node 文本管道显式使用 UTF-8，避免 Windows GBK 破坏中文 JSON。
- `smoke_test.py` 修复 Windows 对已打开 `NamedTemporaryFile` 的文件锁冲突，并增加 Windows Chrome/Edge 启动参数回归。

## 远端部署

```text
OS: Microsoft Windows 10 专业版，Build 19045
Deploy dir: C:\Users\<WindowsUser>\apps\1688-shopkeeper
State dir: <LocalAppData>\SOP1688
Python: 3.12.10
Node.js: v20.19.4
npm: 10.8.2
Playwright: 1.60.0
Google Chrome: 150.0.7871.125
Web task: SOP1688-Web，Interactive logon
Web listen: 127.0.0.1:8765
Chrome CDP: 127.0.0.1:9222，验收时未启动
```

访问方式：

```bash
ssh -N -L 8765:127.0.0.1:8765 entity
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

## 验证结果

本地通过：

```text
git diff --check
Python py_compile
node --check rpa_collect.mjs
node --check rpa_detail.mjs
tag_collect smoke tests passed (59 tests)
```

Windows 远端通过：

```text
pip check
npm ls playwright --depth=0
Python py_compile
两个 RPA Node 语法检查
tag_collect CLI help
tag_collect smoke tests passed (59 tests)
SOP1688-Web task state: Running
GET /api/options through SSH tunnel: HTTP 200
allow_real_collect: true
listener: 127.0.0.1:8765 only
```

关键运行文件 `web.py`、`rpa.py`、`smoke_test.py`、`service.py` 的远端 SHA-256 与当前本地工作树一致。

## 安全边界与残余风险

- Chrome/验证码窗口依赖登录用户的交互桌面；RDP 断开可以，注销会停止交互任务。
- `9222` 等同于完整浏览器控制权限，禁止开放到网络。
- 服务绑定 `0.0.0.0` 会触发远程只读/样例门禁，部署固定使用 `127.0.0.1`。
- 本次只验证离线能力和服务可达性；真实 1688 页面、登录态、DOM 字段覆盖率仍需用户后续低频人工验收。
