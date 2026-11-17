#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novacast —— 从板子上手动控制手机端的 nova.sh。

背景：
  手机侧 nova.sh 是「豆包投屏 + 直播」的实际执行者，住在 Termux 里
  （/data/data/com.termux/files/home/nova.sh，1120 行）。
  它原来自带开机自启（Magisk service.d 每 20s 重拉），用户明确要求
  改成手动，所以启动器已删除，只剩这个脚本本体可以按需调用。

为什么由板子来调，而不是手机自己跑：
  用户不想开 Termux 敲命令，希望在 8140 网页上点。板子有 adb 通路，
  用 su -c 就能执行，不需要在手机上再装任何东西。

三种模式（来自 nova.sh 自身的 case 分支，不是我自己造的）：
  once  -> mode_both   投屏 + 直播跑一次，跑完就退出。
                       豆包 5 分钟硬上限到点还是会断，需要再点一次。
  watch -> mode_lens   前台常驻，每轮巡检；投屏掉了会**主动重开**。
                       这是「自动维护」，点之前要知道它会自己拉起豆包。
  stop  -> 写 pause 标记 + kill -9，并清掉 lock。

安全约束：
  - 绝不调用 install（那会重新写 service.d 开机启动，破坏用户的「手动」要求）
  - stop 后必须确认 pause 标记存在，否则 watch 被重拉时又会自动开
"""
from __future__ import print_function

import os
import re
import subprocess
import time

ADB = "/usr/lib/android-sdk/platform-tools/adb"
HOME = "/data/data/com.termux/files/home"
SELF = HOME + "/nova.sh"

# 手机上跑命令的统一包装。su -c 后面的整串必须被单引号包住，
# 否则 && 会被手机外壳先解释掉，跑到 su 外面的用户态去。
def _phone(sh_cmd, timeout=120):
    script = "su -c '%s'" % sh_cmd
    try:
        p = subprocess.run([ADB, "-s", _serial(), "shell", script],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout after %ss" % timeout
    except Exception as e:
        return 1, str(e)[:200]


def _serial():
    """取当前设备序列号。smartcast 已有成熟的选机逻辑，复用它。"""
    try:
        import smartcast
        s = smartcast.pick_serial()
        if s:
            return s
    except Exception:
        pass
    # 兜底：直接问 adb，只接受唯一在线设备
    try:
        p = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
        devs = [l.split()[0] for l in (p.stdout or "").splitlines()[1:]
                if l.strip().endswith("device")]
        if len(devs) == 1:
            return devs[0]
    except Exception:
        pass
    return ""


def _run(action, timeout=240):
    """执行 nova.sh 的某个动作。"""
    if not os.path.exists(ADB):
        return False, "板上找不到 adb：%s" % ADB
    if not _serial():
        return False, "没有可用的手机设备（adb devices 为空或有多台）"
    cmd = "cd %s && ./nova.sh %s" % (HOME, action)
    rc, out = _phone(cmd, timeout=timeout)
    return rc == 0, out.strip()


# ---------------------------------------------------------------- 动作

def once():
    """投屏 + 直播，跑一次就退出。"""
    return _run("once")


def watch():
    """常驻维护。后台起，不阻塞 HTTP 请求。"""
    if _serial() is None:
        return False, "没有可用设备"
    # pause 标记会直接让 watch 立刻 exit 0，所以先清掉
    _phone("rm -f %s/.nova/pause" % HOME, timeout=15)
    # nohup + & 让它在设备上脱离 adb 会话，否则 adb 一断就跟着死
    cmd = ("cd %s && nohup ./nova.sh watch >>%s/.nova/nova.log 2>&1 &"
           % (HOME, HOME))
    rc, out = _phone(cmd, timeout=25)
    time.sleep(2)
    st = status()
    if st.get("running"):
        return True, "常驻守护已启动"
    return False, out.strip()[:300] or "启动后没检测到守护进程"


def stop():
    """停止。写 pause 标记 + 杀进程 + 清锁。"""
    ok, out = _run("stop", timeout=60)
    # stop 自己会写 pause；再补一次，防止它半路失败
    _phone("mkdir -p %s/.nova && echo 1 > %s/.nova/pause" % (HOME, HOME), timeout=15)
    time.sleep(1)
    st = status()
    if not st.get("running"):
        return True, out.strip()[:300] or "已停止"
    return False, out.strip()[:300] or "仍有守护进程在跑"


# ---------------------------------------------------------------- 状态

def status():
    """读 nova.sh status 的输出，转成结构化数据。

    真跑一次 status 而不是自己拼 dumpsys：脚本内部判断更准
    （它知道 frame_alive / sharing 的具体口径），两边口径不一致
    会让页面和实际打架。
    """
    out = {"ok": False, "raw": "", "cast": None, "lens": None,
           "guard": "", "front": "", "screen": "", "running": False}
    if not _serial():
        out["raw"] = "没有可用的手机设备"
        return out

    rc, txt = _phone("cd %s && ./nova.sh status 2>&1" % HOME, timeout=60)
    out["raw"] = txt.strip()
    out["ok"] = rc == 0

    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("投屏:"):
            out["cast"] = "开" in line
        elif line.startswith("直播:"):
            out["lens"] = "streaming=True" in line
            # 顺手把分辨率和参数抽出来给页面看
            m = re.search(r"(\d+x\d+)\s+q(\d+)\s+zoom([\d.]+)", line)
            if m:
                out["lens_detail"] = "%s q%s zoom%s" % (m.group(1), m.group(2), m.group(3))
        elif line.startswith("守护:"):
            out["guard"] = line.split(":", 1)[1].strip()
            out["running"] = "没跑" not in out["guard"]
        elif line.startswith("前台:"):
            out["front"] = line.split(":", 1)[1].strip()
        elif line.startswith("屏幕:"):
            out["screen"] = line.split(":", 1)[1].strip()
    return out


if __name__ == "__main__":
    import json
    import sys
    a = sys.argv[1] if len(sys.argv) > 1 else "status"
    if a == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    elif a == "once":
        print(once())
    elif a == "watch":
        print(watch())
    elif a == "stop":
        print(stop())
    else:
        print("用法: novacast.py [status|once|watch|stop]")
