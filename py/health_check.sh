#!/bin/bash
# 8140 守护集群健康检查（供 automation 定时调用）
#
# 判活规则（严格遵守，别改）：
#   豆包投屏 = ScreenCaptureService 存在 AND dumpsys display 里有
#              uniqueId="virtual:" + state ON + owner com.larus.nova
#   禁止用 dumpsys media_projection、禁止 UI dump、禁止请求 /snapshot.jpg
#   理由：这三个都会在后台投屏场景下给出假阴性/假阳性，之前踩过。
set -u
ADB=/usr/lib/android-sdk/platform-tools/adb
S="-s 892e20c9"
PROBLEMS=""
WARN=""

OUT=""

# ---------- 1. systemd 守护 ----------
SVC_STATE=""
for s in airtest-adbd airtest-keepcast airtest-lenscast airtest-gateweb; do
  st=$(systemctl is-active "$s" 2>/dev/null)
  if [ "$st" != "active" ]; then
    PROBLEMS="$PROBLEMS $s=$st;"
  fi
  SVC_STATE="$SVC_STATE $s:$st"
done

# ---------- 2. 手机在线 ----------
if ! $ADB devices 2>/dev/null | tr -d '\r' | grep -q "^892e20c9[[:space:]]*device"; then
  echo "RESULT DOWN 手机不在线（adb devices 无 892e20c9）|services:$SVC_STATE"
  exit 0
fi

# ---------- 3. 豆包投屏（双条件） ----------
CAP_SVC=$($ADB $S shell "dumpsys activity services com.larus.nova" 2>/dev/null | tr -d '\r' | grep -c "ScreenCaptureService")
VDISP=$($ADB $S shell "dumpsys display" 2>/dev/null | tr -d '\r' | grep 'uniqueId="virtual:' | grep -c 'state ON')
OWNER=$($ADB $S shell "dumpsys display" 2>/dev/null | tr -d '\r' | grep 'uniqueId="virtual:' | grep -c 'owner com.larus.nova')
CAST_OK=0
if [ "$CAP_SVC" -gt 0 ] && [ "$VDISP" -gt 0 ] && [ "$OWNER" -gt 0 ]; then
  CAST_OK=1
else
  PROBLEMS="$PROBLEMS 豆包投屏断(svc=$CAP_SVC,vdisp=$VDISP,owner=$OWNER);"
fi

# ---------- 4. 豆包进程 ----------
NOVA_PID=$($ADB $S shell "pidof com.larus.nova" 2>/dev/null | tr -d '\r' | awk '{print $1}')
if [ -z "$NOVA_PID" ]; then
  PROBLEMS="$PROBLEMS 豆包进程没了;"
  CAST_OK=0
fi

# ---------- 5. 全镜直播 ----------
LENS_PID=$($ADB $S shell "pidof com.opencode.multilensipcam" 2>/dev/null | tr -d '\r' | awk '{print $1}')
LENS_NOTE=$(cat /home/sunrise/projects/Airtest/state/lenscast.json 2>/dev/null | tr -d '\n' | sed -n 's/.*"note": "\([^"]*\)".*/\1/p')
if [ -z "$LENS_PID" ]; then
  PROBLEMS="$PROBLEMS 全镜进程没了;"
fi
case "$LENS_NOTE" in
  直播中*) : ;;
  *) WARN="$WARN 直播状态:$LENS_NOTE;" ;;
esac

# ---------- 6. 前台归属（直播应在前台） ----------
FG=$($ADB $S shell "dumpsys activity activities" 2>/dev/null | tr -d '\r' | grep -m1 'topResumedActivity' | grep -oE 'com\.[a-z0-9.]+/[A-Za-z0-9._]+' | head -1)
case "$FG" in
  com.opencode.multilensipcam/*) : ;;
  *) WARN="$WARN 前台不在直播($FG);" ;;
esac

# ---------- 7. 温度/电量 ----------
BATT=$($ADB $S shell "dumpsys battery" 2>/dev/null | tr -d '\r')
LEVEL=$(echo "$BATT" | sed -n 's/.*level: \([0-9]*\).*/\1/p')
TEMP=$(echo "$BATT" | sed -n 's/.*temperature: \([0-9]*\).*/\1/p')
if [ -n "$LEVEL" ] && [ "$LEVEL" -lt 20 ]; then
  WARN="$WARN 电量低($LEVEL%);"
fi
if [ -n "$TEMP" ] && [ "$TEMP" -gt 430 ]; then
  WARN="$WARN 温度高($(awk "BEGIN{printf \"%.1f\", $TEMP/10}")C);"
fi

# ---------- 汇总 ----------
if [ -n "$PROBLEMS" ]; then
  echo "RESULT BAD$PROBLEMS|cast=$CAST_OK nova=$NOVA_PID lens=$LENS_PID fg=$FG level=$LEVEL temp=$TEMP|services:$SVC_STATE|warn:$WARN"
elif [ -n "$WARN" ]; then
  echo "RESULT WARN$WARN|cast=$CAST_OK nova=$NOVA_PID lens=$LENS_PID fg=$FG level=$LEVEL temp=$TEMP|services:$SVC_STATE"
else
  echo "RESULT OK 全部正常|cast=$CAST_OK nova=$NOVA_PID lens=$LENS_PID fg=$FG level=$LEVEL temp=$TEMP|services:$SVC_STATE"
fi
