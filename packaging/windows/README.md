# Windows 一键安装包

本目录用于生成一个可分发的 `SOP1688-Setup-x64.exe`。最终用户只需双击安装，不需要自行安装 Python、Node.js 或 npm 依赖。

## 安装包包含什么

- Python 3.12 便携运行时和 `requirements.txt` 依赖。
- Node.js 20 运行时、Playwright Node 包和当前项目代码。
- 本地工作台启动器、开始/停止快捷方式和可选登录自启动项。
- 不包含 AK、Cookie、浏览器 Profile、真实采集数据、导出表格或本机服务器信息。

浏览器内核不打进安装包。工作台优先使用已安装的 Google Chrome，没有 Chrome 时自动回退到 Windows 10/11 自带的 Microsoft Edge。这样可以保留可见的登录、扫码、滑块和验证码窗口，也避免携带过期浏览器。

## 在 Windows 构建

构建机要求：

- Windows 10/11 x64。
- 可访问 Python、Node.js 和 npm 官方下载源。
- Inno Setup 6；没有时脚本可下载固定版本的官方安装程序并按当前用户静默安装。

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build-installer.ps1 -InstallInnoSetup
```

脚本会：

1. 下载固定版本的 Python/Node 运行时到 `.tmp/windows-installer/downloads`。
2. 使用 `package-lock.json` 安装 Playwright，但不下载 Playwright Chromium。
3. 运行启动器 dry-run、Python 编译、两个 Node 语法检查和完整 tag_collect smoke。
4. 用 Inno Setup 生成单文件安装器和 SHA-256 文件。

输出：

```text
.tmp\windows-installer\dist\SOP1688-Setup-x64.exe
.tmp\windows-installer\dist\SOP1688-Setup-x64.sha256.txt
```

只生成可检查的暂存目录、不编译安装器：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build-installer.ps1 -SkipCompile
```

`-SkipSmoke` 仅用于排查构建环境，不可用于正式发布。

## 最终用户使用

1. 双击 `SOP1688-Setup-x64.exe`。
2. 安装完成后勾选“启动 SOP 1688 工作台”。
3. 浏览器会打开 `http://127.0.0.1:8765/`。
4. 点击页面中的“启动采集 Chrome”，在弹出的 Chrome/Edge 中人工登录 1688。
5. 遇到滑块或验证码时人工处理；安装包不会绕过平台验证。

桌面和开始菜单提供：

- 启动 SOP 1688 工作台
- 打开 SOP 1688 工作台
- 停止 SOP 1688 工作台
- 卸载 SOP 1688 工作台

## 本机数据与升级

程序默认安装到当前用户目录：

```text
%LOCALAPPDATA%\Programs\SOP1688
```

运行数据单独保存在：

```text
%LOCALAPPDATA%\SOP1688
```

覆盖安装新版本会先停止旧服务，然后替换程序文件；运行数据、Chrome Profile 和历史导出不会被覆盖。卸载程序也默认保留运行数据，避免误删用户结果。确认不再需要时可人工删除 `%LOCALAPPDATA%\SOP1688`。

## 发布前注意

- 当前安装器若未使用代码签名证书，Windows SmartScreen 可能显示“未知发布者”。正式发给非技术用户前应使用可信代码签名证书签名。
- 发布前必须在一台干净的 Win10/Win11 x64 电脑上完成安装、启动、Chrome/Edge 登录、样例查询、停止、覆盖升级和卸载测试。
- 真实 1688 页面仍可能因账号、地区、页面版本或平台风控要求人工登录/验证；这不是安装包能够消除的限制。
