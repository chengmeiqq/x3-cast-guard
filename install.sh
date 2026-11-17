#!/bin/bash
# x3-cast-guard 安装脚本
#
# 用法：
#   sudo ./install.sh
#   PREFIX=/home/sunrise/projects/x3-cast-guard sudo -E ./install.sh
#
# 原则：不覆盖已有文件。目标已存在就跳过并提示。
set -u

PREFIX="${PREFIX:-/opt/x3-cast-guard}"
SRC="$(cd "$(dirname "$0")" && pwd)"
ADB=/usr/lib/android-sdk/platform-tools/adb

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$*"; }
die()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }

echo "== 1/5 检查依赖"
[ "$(id -u)" -eq 0 ] || die "需要 root：sudo ./install.sh"
command -v python3 >/dev/null || die "缺 python3"
ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"

if [ -x "$ADB" ]; then ok "adb $ADB"
elif command -v adb >/dev/null; then ADB=$(command -v adb); ok "adb $ADB"
else die "找不到 adb（期望 $ADB）"; fi

if python3 -c 'import flask' 2>/dev/null; then ok "flask $(python3 -c 'import flask;print(flask.__version__)')"
else skip "缺 flask，gateweb 会起不来：pip3 install flask"; fi

if python3 -c 'import cv2' 2>/dev/null; then ok "cv2（aiverify 视觉复核可用）"
else skip "缺 cv2，视觉复核会降级，不影响守护"; fi

echo "== 2/5 同步程序到 $PREFIX"
mkdir -p "$PREFIX/state" || die "建目录失败"
for f in "$SRC"/py/*; do
  b=$(basename "$f")
  if [ -e "$PREFIX/$b" ]; then
    if cmp -s "$f" "$PREFIX/$b"; then skip "$b 已是最新"
    else skip "$b 已存在且内容不同，保留原文件（要更新请手动 diff）"; fi
  else
    cp "$f" "$PREFIX/$b"; ok "$b"
  fi
done
chmod +x "$PREFIX"/*.sh 2>/dev/null

echo "== 3/5 安装 systemd 单元"
for u in "$SRC"/systemd/*.service; do
  b=$(basename "$u")
  if [ -e "/etc/systemd/system/$b" ]; then
    skip "$b 已存在，保留（避免覆盖你本地的改动）"
  else
    cp "$u" "/etc/systemd/system/$b"; ok "$b"
  fi
done
systemctl daemon-reload && ok "daemon-reload"

echo "== 4/5 启动"
for s in airtest-adbd airtest-keepcast airtest-lenscast airtest-gateweb; do
  if systemctl list-unit-files "$s.service" >/dev/null 2>&1 && \
     systemctl cat "$s" >/dev/null 2>&1; then
    systemctl enable --now "$s" >/dev/null 2>&1 && ok "$s 已启动"
  else
    skip "$s 未安装"
  fi
done

echo "== 5/5 健康检查"
if [ -x "$SRC/py/health_check.sh" ]; then
  "$SRC/py/health_check.sh"
else
  skip "health_check.sh 不可执行"
fi

cat <<'EOF'

注意：单元文件里的 ExecStart 指向 /home/sunrise/projects/Airtest/。
如果你装到了别的位置，需要改这两个地方：
  /etc/systemd/system/airtest-gateweb.service
  /etc/systemd/system/airtest-keepcast.service
然后 systemctl daemon-reload && systemctl restart <unit>
EOF