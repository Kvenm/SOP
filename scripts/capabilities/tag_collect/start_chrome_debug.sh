#!/usr/bin/env bash
set -euo pipefail

PORT="${TAG_COLLECT_CDP_PORT:-9222}"
PROFILE="${TAG_COLLECT_CHROME_PROFILE:-$HOME/.sop-1688-chrome-profile}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

if [ ! -x "$CHROME_BIN" ]; then
  CHROME_BIN="/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
fi

if [ ! -x "$CHROME_BIN" ]; then
  echo "未找到 Google Chrome，请先安装 Chrome，或设置 CHROME_BIN。"
  exit 1
fi

mkdir -p "$PROFILE"

echo "启动 Chrome 调试窗口："
echo "  profile: $PROFILE"
echo "  cdp:     http://127.0.0.1:$PORT"
echo
echo "请在打开的 Chrome 中登录 1688，并完成扫码/安全验证。"

"$CHROME_BIN" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  "https://www.1688.com/" >/tmp/sop-1688-chrome.log 2>&1 &

echo "export TAG_COLLECT_CDP_URL=http://127.0.0.1:$PORT"
