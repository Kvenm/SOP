#!/usr/bin/env bash
set -euo pipefail

PORT="${TAG_COLLECT_CDP_PORT:-9222}"
PROFILE="${TAG_COLLECT_CHROME_PROFILE:-$HOME/.sop-1688-chrome-profile}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
CHROME_APP_NAME="${CHROME_APP_NAME:-}"

if [ ! -x "$CHROME_BIN" ]; then
  CHROME_BIN="/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
  CHROME_APP_NAME="${CHROME_APP_NAME:-Google Chrome Canary}"
else
  CHROME_APP_NAME="${CHROME_APP_NAME:-Google Chrome}"
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

if command -v open >/dev/null 2>&1; then
  open -na "$CHROME_APP_NAME" --args \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    "https://www.1688.com/" >/tmp/sop-1688-chrome.log 2>&1
else
  "$CHROME_BIN" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    "https://www.1688.com/" >/tmp/sop-1688-chrome.log 2>&1 &
fi

echo "export TAG_COLLECT_CDP_URL=http://127.0.0.1:$PORT"
