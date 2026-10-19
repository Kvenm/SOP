#!/usr/bin/env python3
"""Offline smoke tests for the Windows package launcher."""

from __future__ import annotations

import json
import importlib.util
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_LAUNCHER_PATH = Path(__file__).with_name("launcher.py")
_LAUNCHER_SPEC = importlib.util.spec_from_file_location("sop1688_windows_launcher", _LAUNCHER_PATH)
if _LAUNCHER_SPEC is None or _LAUNCHER_SPEC.loader is None:
    raise RuntimeError(f"cannot load launcher module: {_LAUNCHER_PATH}")
launcher = importlib.util.module_from_spec(_LAUNCHER_SPEC)
_LAUNCHER_SPEC.loader.exec_module(launcher)


class _HealthHandler(BaseHTTPRequestHandler):
    payload = {"success": True, "data": {}}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve_health(payload: dict) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type("HealthHandler", (_HealthHandler,), {"payload": payload})
    server = ThreadingHTTPServer((launcher.DEFAULT_HOST, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_health_requires_sop_signature() -> None:
    server, thread = _serve_health({"success": True, "data": {"service": "other"}})
    try:
        port = int(server.server_address[1])
        _assert(launcher._server_health(port) is None, "其它本地服务不能冒充 SOP 工作台")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    server, thread = _serve_health(
        {"success": True, "data": {"workbench_build": "package-smoke", "allow_real_collect": True}}
    )
    try:
        port = int(server.server_address[1])
        health = launcher._server_health(port)
        _assert(health is not None and health.get("workbench_build") == "package-smoke", "SOP 健康检查应被识别")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_runtime_paths_and_start_lock() -> None:
    with tempfile.TemporaryDirectory(prefix="sop1688-package-") as tmp:
        root = Path(tmp) / "app"
        data_root = Path(tmp) / "data"
        node_name = "node.exe" if os.name == "nt" else "node"
        python_name = "python.exe" if os.name == "nt" else "python"
        (root / "runtime" / "node").mkdir(parents=True)
        (root / "runtime" / "python").mkdir(parents=True)
        (root / "runtime" / "node" / node_name).write_bytes(b"")
        (root / "runtime" / "python" / python_name).write_bytes(b"")
        (root / "cli.py").write_text("# package smoke\n", encoding="utf-8")

        env = launcher._runtime_env(root, data_root)
        _assert(env["NODE_BIN"].endswith(node_name), "必须使用安装包内置 Node")
        _assert(env["PATH"].split(os.pathsep)[0] == str(root / "runtime" / "node"), "私有 Node 目录必须位于 PATH 首位")
        _assert(Path(env["TAG_COLLECT_DATA_DIR"]).is_dir(), "采集数据目录必须在本机数据根目录创建")
        _assert(Path(env["TAG_COLLECT_CHROME_PROFILE"]).is_dir(), "Chrome Profile 必须与程序目录分离")
        command = launcher._server_command(root, 8876)
        _assert(command[-1] == "8876", "启动命令必须保留指定端口")
        _assert(str(root / "cli.py") in command, "启动命令必须使用安装目录中的 cli.py")

        lock = launcher._start_lock_file(data_root, 8876)
        _assert(launcher._acquire_start_lock(lock), "第一次启动应取得互斥锁")
        _assert(not launcher._acquire_start_lock(lock), "并发启动不能重复取得互斥锁")
        launcher._release_start_lock(lock)
        _assert(launcher._acquire_start_lock(lock), "释放后应能再次取得互斥锁")
        launcher._release_start_lock(lock)


def main() -> None:
    test_health_requires_sop_signature()
    test_runtime_paths_and_start_lock()
    print("windows package launcher smoke tests passed (2 tests)")


if __name__ == "__main__":
    main()
