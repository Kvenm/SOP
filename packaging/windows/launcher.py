#!/usr/bin/env python3
"""Windows desktop launcher for the packaged SOP 1688 workbench.

The installer runs this file with the bundled ``pythonw.exe``.  It keeps the
local web service in the interactive user session so Chrome/Edge login and
security-verification windows remain visible.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import urlopen


APP_NAME = "SOP 1688 选品工作台"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
START_TIMEOUT_SECONDS = 45
START_LOCK_STALE_SECONDS = 90


def _app_root(explicit: str = "") -> Path:
    configured = explicit or os.environ.get("SOP1688_APP_DIR", "")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent


def _data_root() -> Path:
    configured = os.environ.get("SOP1688_DATA_DIR", "")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        return Path(local_app_data) / "SOP1688"
    return Path.home() / ".local" / "share" / "SOP1688"


def _workbench_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}/"


def _health_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}/api/options"


def _server_health(port: int) -> Optional[Dict[str, Any]]:
    try:
        with urlopen(_health_url(port), timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("workbench_build"):
        return None
    return data


def _runtime_python(root: Path) -> Path:
    candidate = root / "runtime" / "python" / ("python.exe" if os.name == "nt" else "python")
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def _runtime_node(root: Path) -> str:
    candidate = root / "runtime" / "node" / ("node.exe" if os.name == "nt" else "node")
    if candidate.is_file():
        return str(candidate)
    return shutil.which("node") or ""


def _runtime_env(root: Path, data_root: Path) -> Dict[str, str]:
    data_root.mkdir(parents=True, exist_ok=True)
    tag_collect_dir = data_root / "tag_collect"
    workspace_dir = data_root / "workspace"
    config_dir = data_root / "config"
    chrome_profile = data_root / "ChromeProfile"
    rpa_profile = data_root / "RPAProfile"
    for directory in (tag_collect_dir, workspace_dir, config_dir, chrome_profile, rpa_profile):
        directory.mkdir(parents=True, exist_ok=True)

    node_bin = _runtime_node(root)
    if not node_bin:
        raise RuntimeError("安装目录中未找到 Node.js 运行时，请重新安装 SOP 1688 工作台。")

    env = os.environ.copy()
    env.update(
        {
            "SOP1688_APP_DIR": str(root),
            "SOP1688_DATA_DIR": str(data_root),
            "NODE_BIN": node_bin,
            "TAG_COLLECT_DATA_DIR": str(tag_collect_dir),
            "OPENCLAW_WORKSPACE_DIR": str(workspace_dir),
            "OPENCLAW_CONFIG_DIR": str(config_dir),
            "TAG_COLLECT_CHROME_PROFILE": str(chrome_profile),
            "TAG_COLLECT_RPA_PROFILE": str(rpa_profile),
            "TAG_COLLECT_CDP_URL": "http://127.0.0.1:9222",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        }
    )
    env["PATH"] = str(Path(node_bin).parent) + os.pathsep + env.get("PATH", "")
    return env


def _server_command(root: Path, port: int) -> list[str]:
    cli_path = root / "cli.py"
    if not cli_path.is_file():
        raise RuntimeError(f"安装目录缺少 cli.py：{cli_path}")
    return [
        str(_runtime_python(root)),
        str(cli_path),
        "tag_collect",
        "--serve",
        "--host",
        DEFAULT_HOST,
        "--port",
        str(port),
    ]


def _pid_file(data_root: Path, port: int) -> Path:
    return data_root / f"workbench-{port}.pid"


def _start_lock_file(data_root: Path, port: int) -> Path:
    return data_root / f"workbench-{port}.starting"


def _log_file(data_root: Path) -> Path:
    log_dir = data_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "workbench.log"


def _write_pid(path: Path, pid: int) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(str(pid), encoding="ascii")
    os.replace(temp, path)


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def _remove_if_same_pid(path: Path, pid: int) -> None:
    if _read_pid(path) != pid:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _acquire_start_lock(path: Path) -> bool:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime > START_LOCK_STALE_SECONDS:
                path.unlink()
                return _acquire_start_lock(path)
        except OSError:
            pass
        return False
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(f"{os.getpid()}\n{int(time.time())}\n")
    return True


def _release_start_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _message(text: str, *, error: bool = False, quiet: bool = False) -> None:
    if quiet:
        return
    if os.name == "nt":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, flags)
        return
    stream = sys.stderr if error else sys.stdout
    print(text, file=stream)


def _tail_log(path: Path, limit: int = 5000) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    return raw[-limit:].decode("utf-8", errors="replace").strip()


def _open_workbench(port: int) -> None:
    webbrowser.open(_workbench_url(port), new=2)


def _wait_for_health(port: int, deadline: float, process: Optional[subprocess.Popen[Any]] = None) -> bool:
    while time.time() < deadline:
        if _server_health(port) is not None:
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.4)
    return False


def start_workbench(
    root: Path,
    data_root: Path,
    port: int,
    *,
    quiet: bool = False,
    open_browser: bool = True,
) -> int:
    if _server_health(port) is not None:
        if open_browser:
            _open_workbench(port)
        return 0

    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = _start_lock_file(data_root, port)
    if not _acquire_start_lock(lock_path):
        if _wait_for_health(port, time.time() + 20):
            if open_browser:
                _open_workbench(port)
            return 0
        _message("工作台正在启动，但等待超时。请稍后再次点击启动快捷方式。", error=True, quiet=quiet)
        return 1

    child: Optional[subprocess.Popen[Any]] = None
    pid_path = _pid_file(data_root, port)
    log_path = _log_file(data_root)
    try:
        env = _runtime_env(root, data_root)
        command = _server_command(root, port)
        creationflags = 0
        if os.name == "nt":
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with open(log_path, "ab", buffering=0) as log_handle:
            log_handle.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode("utf-8"))
            child = subprocess.Popen(
                command,
                cwd=str(root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creationflags,
            )
        _write_pid(pid_path, child.pid)
        _release_start_lock(lock_path)

        if not _wait_for_health(port, time.time() + START_TIMEOUT_SECONDS, child):
            detail = _tail_log(log_path)
            message = "工作台启动失败，请重新安装或查看日志：\n" + str(log_path)
            if detail:
                message += "\n\n最近日志：\n" + detail[-1500:]
            if child.poll() is None:
                _terminate_process_tree(child.pid, expected_executable=_runtime_python(root))
            _message(message, error=True, quiet=quiet)
            return 1

        if open_browser:
            _open_workbench(port)
        return_code = child.wait()
        return int(return_code or 0)
    except Exception as exc:
        _release_start_lock(lock_path)
        _message(f"启动工作台失败：{exc}", error=True, quiet=quiet)
        return 1
    finally:
        _release_start_lock(lock_path)
        if child is not None:
            _remove_if_same_pid(pid_path, child.pid)


def _windows_process_executable(pid: int) -> str:
    if os.name != "nt" or pid <= 0:
        return ""
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _terminate_process_tree(pid: int, *, expected_executable: Optional[Path] = None) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        if expected_executable is not None:
            running_executable = _windows_process_executable(pid)
            if not running_executable:
                return False
            if os.path.normcase(os.path.abspath(running_executable)) != os.path.normcase(os.path.abspath(expected_executable)):
                return False
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return completed.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def stop_workbench(root: Path, data_root: Path, port: int, *, quiet: bool = False) -> int:
    pid_path = _pid_file(data_root, port)
    pid = _read_pid(pid_path)
    stopped = _terminate_process_tree(pid, expected_executable=_runtime_python(root))
    try:
        pid_path.unlink()
    except OSError:
        pass
    if stopped:
        _message("SOP 1688 工作台已停止。", quiet=quiet)
        return 0
    if _server_health(port) is not None:
        _message("检测到工作台仍在运行，但没有找到对应进程记录；请重启电脑后再安装。", error=True, quiet=quiet)
        return 1
    _message("SOP 1688 工作台当前未运行。", quiet=quiet)
    return 0


def dry_run(root: Path, data_root: Path, port: int) -> int:
    env = _runtime_env(root, data_root)
    keys = (
        "NODE_BIN",
        "TAG_COLLECT_DATA_DIR",
        "OPENCLAW_WORKSPACE_DIR",
        "OPENCLAW_CONFIG_DIR",
        "TAG_COLLECT_CHROME_PROFILE",
        "TAG_COLLECT_RPA_PROFILE",
        "TAG_COLLECT_CDP_URL",
    )
    print(
        json.dumps(
            {
                "app_root": str(root),
                "data_root": str(data_root),
                "url": _workbench_url(port),
                "command": _server_command(root, port),
                "environment": {key: env[key] for key in keys},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--start", action="store_true", help="启动工作台（默认）")
    action.add_argument("--stop", action="store_true", help="停止工作台")
    action.add_argument("--open", action="store_true", help="只打开已运行的工作台")
    action.add_argument("--dry-run", action="store_true", help="输出运行配置但不启动服务")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--app-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = _app_root(args.app_dir)
    data_root = _data_root()
    if args.stop:
        return stop_workbench(root, data_root, args.port, quiet=args.quiet)
    if args.open:
        if _server_health(args.port) is None:
            _message("工作台尚未启动，请先点击“启动 SOP 1688 工作台”。", error=True, quiet=args.quiet)
            return 1
        _open_workbench(args.port)
        return 0
    if args.dry_run:
        return dry_run(root, data_root, args.port)
    return start_workbench(root, data_root, args.port, quiet=args.quiet, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
