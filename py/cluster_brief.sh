#!/bin/bash
# 8140 守护集群：一次 ssh 吐出全部现场证据
# 目的：让定时告警的自动诊断只需 1 次工具调用就能拿到全部必要信息，
#       避免逐个 ssh 挖线索把 300s 的 payload 预算烧光。
# 只读，不修改任何状态。目标耗时 < 20s。
set -u
ADB=/usr/lib/android-sdk/platform-tools/adb
S="-s 892e20c9"
P=/home/sunrise/projects/Airtest

echo "### RESULT"
"$P/health_check.sh" 2>&1 | tail -1

echo
echo "### SERVICES"
for s in airtest-adbd airtest-keepcast airtest-lenscast airtest-gateweb; do
  printf '%s=%s ' "$s" "$(systemctl is-active "$s" 2>/dev/null)"
done
echo

echo
echo "### KEEPCAST_STATE"
cat "$P/state/keepcast.json" 2>/dev/null | tr -d '\n'
echo

echo
echo "### LENS_STATE"
cat "$P/state/lenscast.json" 2>/dev/null | tr -d '\n'
echo

echo
echo "### 最近掉线事件（最多 8 条）"
grep -E '掉线|已重新拉起|失败' "$P/logs/keepcast.log" 2>/dev/null | tail -8

echo
echo "### keepcast 日志尾部（10 行）"
tail -10 "$P/logs/keepcast.log" 2>/dev/null

echo
echo "### lenscast 日志尾部（6 行）"
tail -6 "$P/logs/lenscast.log" 2>/dev/null

echo
echo "### 掉线历史统计"
DROPS=$(grep -cE '掉线' "$P/logs/keepcast.log" 2>/dev/null || echo 0)
echo "keepcast.log 累计掉线次数: $DROPS"
if [ "$DROPS" -gt 0 ]; then
  echo "最近 24h 内掉线时间点:"
  grep -E '掉线' "$P/logs/keepcast.log" 2>/dev/null | tail -12 | sed 's/ .*掉线.*/ <- 掉线/'
fi

echo
echo "### 前台与投屏（一次 adb）"
FG=$($ADB $S shell "dumpsys activity activities" 2>/dev/null | tr -d '\r' | grep -m1 'topResumedActivity')
echo "top: $FG"
VD=$($ADB $S shell "dumpsys display" 2>/dev/null | tr -d '\r' | grep 'uniqueId="virtual:' | head -2)
echo "virtual: ${VD:-（无）}"

echo
echo "### END"
