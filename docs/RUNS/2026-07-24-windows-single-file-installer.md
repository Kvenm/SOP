# 2026-07-24 Windows 单文件安装包

## 目标

- 让其它 Win10/Win11 x64 电脑通过一个 `SOP1688-Setup-x64.exe` 安装标签选品工作台。
- 最终用户不需要自行安装 Python、Node.js、npm 或 Playwright。
- 保持 Chrome/Edge 可见窗口，登录、扫码、滑块和验证码继续由用户人工处理。

## 非目标

- 不把 AK、Cookie、浏览器 Profile、真实采集结果、导出表格或服务器信息打进安装包。
- 不绕过 1688 登录、安全验证或平台风控。
- 不在本轮执行真实 1688 采集。
- 不把 Chrome/Chromium 内核固化进安装包；优先使用系统 Chrome，缺失时回退 Edge。

## 实现

- `packaging/windows/launcher.py`
  - 使用安装包内置 Python 启动 `127.0.0.1:8765` 工作台。
  - 将内置 Node 通过 `NODE_BIN` 和 `PATH` 传给 RPA 子进程。
  - 强制 Python/Node 管道使用 UTF-8。
  - 程序和运行数据分离，数据写入 `%LOCALAPPDATA%\SOP1688`。
  - 支持启动、重复打开、停止、备用端口和无浏览器验收模式。
  - PID 停止前核对真实可执行文件路径，避免陈旧 PID 误杀其它进程。
- `packaging/windows/build-installer.ps1`
  - 下载固定版本的 Python 3.12、Node.js 20 和官方 Inno Setup。
  - 按 `package-lock.json` 安装 Playwright，明确禁止下载 Playwright 浏览器内核。
  - 复制白名单项目运行文件，不复制用户数据和凭证目录。
  - 在便携运行时中执行启动器 smoke、Python 编译、Node 语法和完整 tag_collect smoke。
- `packaging/windows/SOP1688.iss`
  - 生成当前用户级单文件安装器，不要求管理员权限。
  - 创建启动、打开、停止和卸载快捷方式，可选登录自动启动。
  - 覆盖安装前停止旧版；卸载默认保留用户数据。

## 构建过程中沉淀的问题

1. Windows PowerShell 5.1 会把无 BOM 的 UTF-8 `.ps1` 当本地编码读取；构建脚本因此保持纯 ASCII 文本，避免中文提示破坏解析。
2. 便携 Python 的 `._pth` 隔离模式不会自动把测试脚本目录放入 `sys.path`；启动器测试改为按绝对文件路径加载模块。
3. 部分旧 smoke 夹具直接调用命令名 `node`；安装包同时设置 `NODE_BIN` 并把私有 Node 目录放到 `PATH` 首位。
4. Windows 默认 GBK 会误解码 Node 的 UTF-8 中文 JSON；启动器和构建验证都设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`。
5. 目标机 `winget` 源损坏；构建脚本改为下载固定版本、有效签名的官方 Inno Setup 安装程序，不再依赖 winget 软件源。
6. 官方 Inno Setup 未携带简体中文语言文件；安装向导先使用官方默认语言，应用名称、快捷方式和工作台仍为中文，避免引入来源不明的翻译包。

## 验证结果

本地：

```text
launcher Python compile: passed
windows package launcher smoke tests passed (2 tests)
git diff --check: passed
```

Windows x64 构建机，安装包内置运行时：

```text
Python 3.12 runtime: passed
private Node.js runtime: passed
rpa_collect.mjs node --check: passed
rpa_detail.mjs node --check: passed
tag_collect smoke tests passed (77 tests)
Inno Setup 6.7.3 compile: passed
```

安装器回归使用隔离目录、隔离数据目录和备用端口，没有访问真实 1688：

```text
silent install: exit 0
installed /api/options: healthy
workbench_build: 2026.07.23-shipping-monthly-metrics-r2
launcher stop: passed
silent uninstall: exit 0
installed program files removed: yes
```

产物：

```text
filename: SOP1688-Setup-x64.exe
size: 32619030 bytes
sha256: 177568c59396b13b54d18ed8054ffe0a700236f4a902a71df4e02d9e20be064f
```

## 残余风险

- 当前安装器没有代码签名，Windows SmartScreen 可能提示“未知发布者”；正式外部分发前应签名。
- 本轮在现有 Win10 x64 实体机做了隔离安装回归，但尚未在另一台完全干净的 Win11 电脑上验证 Chrome/Edge 可见窗口和人工登录。
- 安装向导按钮暂为官方默认英文；应用名、快捷方式和 Web 工作台为中文。
- 当前只生成 x64 安装包，不支持 32 位 Windows 或 ARM Windows。
