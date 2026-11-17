#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守护总控台：一个网页开关所有自动守护。

为什么需要它（用户原话）：
  "我调试的时候他老是自动弹出来" —— 四个守护各自跑，调试时被抢屏幕、
  被顶前台、被重建投屏，只能 systemctl stop 一个个人肉关，关完还要记得开回来，
  很容易漏掉一个，第二天发现自动投屏整晚没工作。

设计取舍：
  1. 开关 = 暂停标记，不是停服务。
     服务进程始终活着，只是每轮开头看一眼 gate。好处：按一下 1 秒内生效，
     不需要等 systemd RestartSec；恢复也是立刻，不用等服务重启。
  2. 状态是"读文件 + systemctl is-active"，不缓存。
     你在网页上看到的就是当下真实状态，刷新一下一定准。
  3. 只做开关和状态，不做参数设置。
     参数已经在 LensCast 自己的 41737 页面里了，重复造一份只会两边不同步。

端口 8140，避免和 airweb(8123) 打架。
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time

from flask import Flask, jsonify, redirect, request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gate  # noqa: E402
import arb  # 前台仲裁状态
import detail  # 细节点采集（豆包/直播两条链路）

app = Flask(__name__)

# 守护 -> 人话名字 + 它到底会"弹"什么
# mode: "auto"   = 常开（不受网页控制，只显示状态）
#       "manual" = 页面开关同时决定「本次运行」和「下次开机是否自启」
DAEMONS = [
    ("adbd", "adb 调试连接",
     "永远开着，不受页面开关控制。"
     "保证电脑能和手机通讯：USB 线掉了自动改用 WiFi 连，"
     "adb 服务挂了自动重拉。下面两个守护都靠它才能操作手机。",
     "auto"),
    ("keepcast", "豆包 · 投屏",
     "主动巡逻，每几秒检查一次豆包投屏还在不在（虚拟屏 + 豆包进程两项都查）。"
     "掉了就自己重新点一遍投屏把它拉回来。因为豆包投屏有 5 分钟硬上限、"
     "还会被 MIUI 清理杀掉，而且断了界面上没有任何提示，所以需要它盯着。",
     "manual"),
    ("lenscast", "全镜直播 · 推流",
     "保证全镜直播一直正常。把直播界面顶到前台、点亮屏幕防止息屏、"
     "持续校正全镜直播参数（4K、对焦 1.53、质量 100、30fps）。"
     "全镜直播被切后台或被弹窗挡住时自动恢复。",
     "manual"),
]
MANUAL = {d[0] for d in DAEMONS if d[3] == "manual"}

PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>守护总控台</title>
<style>
  :root { --bg:#0f1115; --card:#181b22; --line:#262b36; --txt:#e6e9ef;
          --dim:#8b93a5; --ok:#2ecc71; --warn:#f5a623; --bad:#e74c3c; }
  * { box-sizing:border-box; }
  body { margin:0; padding:20px 16px 40px; background:var(--bg); color:var(--txt);
         font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .wrap { max-width:720px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; font-weight:600; }
  .sub { color:var(--dim); font-size:13px; margin-bottom:20px; }
  .master { background:var(--card); border:1px solid var(--line); border-radius:12px;
            padding:18px; margin-bottom:18px; display:flex; align-items:center;
            justify-content:space-between; gap:16px; flex-wrap:wrap; }
  .master .label { font-size:17px; font-weight:600; }
  .master .hint { color:var(--dim); font-size:13px; margin-top:2px; }
  .list { display:flex; flex-direction:column; gap:10px; }
  .row { background:var(--card); border:1px solid var(--line); border-radius:10px;
         padding:14px 16px; display:flex; align-items:center; gap:14px; }
  .row.off { opacity:.55; }
  .row .meta { flex:1; min-width:0; }
  .row .name { font-weight:600; }
  .row .desc { color:var(--dim); font-size:12.5px; line-height:1.65;
               margin-top:5px; }
  .dot { width:8px; height:8px; border-radius:50%; flex:none; }
  .dot.on { background:var(--ok); box-shadow:0 0 8px var(--ok); }
  .dot.off { background:var(--bad); }
  .dot.na { background:var(--dim); }
  .sw { position:relative; width:52px; height:30px; flex:none; }
  .sw input { opacity:0; width:0; height:0; }
  .sw span { position:absolute; inset:0; background:#3a4050; border-radius:999px;
             cursor:pointer; transition:.18s; }
  .sw span:before { content:""; position:absolute; width:24px; height:24px; left:3px;
                    top:3px; background:#fff; border-radius:50%; transition:.18s; }
  .sw input:checked + span { background:var(--ok); }
  .sw input:checked + span:before { transform:translateX(22px); }
  .sw input:disabled + span { opacity:.4; cursor:not-allowed; }
  .svc { font-size:11.5px; color:var(--dim); font-family:ui-monospace,monospace; }
  .bar { margin-top:22px; display:flex; gap:8px; flex-wrap:wrap; }
  button { background:#252a35; color:var(--txt); border:1px solid var(--line);
           border-radius:8px; padding:9px 14px; font-size:13.5px; cursor:pointer; }
  button:hover { background:#2f3644; }
  #note { color:var(--warn); font-size:13px; margin-top:14px; min-height:20px; }
  .foot { color:var(--dim); font-size:12px; margin-top:26px; line-height:1.9; }
  .foot code { background:#181b22; padding:1px 5px; border-radius:4px; }
.rbtn{font:inherit;font-size:13px;padding:7px 14px;border-radius:8px;cursor:pointer;border:1px solid var(--line);background:#1b1f27;color:#e8ecf1}
.rbtn:hover{background:#242a34}
.rbtn:disabled{opacity:.5;cursor:not-allowed}
</style></head>
<body><div class="wrap">
<h1>小米手机 12S Ultra</h1>
<div class="sub">小米手机 12S Ultra · 点总开关 = 两个一起开或一起关；下面的小开关可以单独控制某一个。不管怎么选，下次开机都按最后的选择来。</div>

<div style="margin:14px 0 20px">
  <a id="castLink" class="rbtn" href="/cast" target="_blank" rel="noopener"
     style="text-decoration:none;display:inline-block">投屏 · 新窗口打开</a>
</div>

<div class="master">
  <div>
    <div class="label" id="mLabel">全部守护</div>
    <div class="hint" id="mHint">载入中…</div>
  </div>
  <div style="display:flex;align-items:center;gap:10px;flex:none">
    <button id="rebootBtn" class="rbtn">重启手机</button>
    <label class="sw"><input type="checkbox" id="mSw"><span></span></label>
  </div>
</div>

<div class="list" id="list"></div>


<div id="pw" class="pw" style="display:none"></div>

<div class="chain">
  <div class="ch">
    <div class="chh"><span class="cht">豆包 · 投屏链路</span><span class="chs" id="dbSum"></span></div>
    <div class="grid" id="dbGrid"></div>
  </div>
  <div class="ch">
    <div class="chh"><span class="cht">全镜直播 · 推流链路</span><span class="chs" id="lcSum"></span></div>
    <div class="grid" id="lcGrid"></div>
  </div>
</div>

<div class="bar">
  <button onclick="act('pause')">全部停止</button>
  <button onclick="act('resume')">全部启动</button>
  <button onclick="load()">刷新</button>
</div>
<div id="note"></div>
<div id="arb" class="arb"></div>

<style>
.arb{font-size:12px;color:#7a8595;margin:10px 0 4px;line-height:1.6}
.pw{background:#3a2418;border:1px solid #6b4520;color:#ffb26b;
    border-radius:10px;padding:10px 13px;font-size:13px;margin:16px 0 0;
    line-height:1.55}
.chain{margin:22px 0 6px}
.ch{background:var(--card);border:1px solid var(--line);border-radius:12px;
    padding:14px 16px;margin-bottom:12px}
.chh{display:flex;justify-content:space-between;align-items:baseline;
     margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.cht{font-size:14px;font-weight:600;letter-spacing:.3px}
.chs{font-size:12px;color:var(--dim)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 14px}
@media(max-width:520px){.grid{grid-template-columns:1fr}}
.item{display:flex;align-items:center;gap:7px;font-size:13px;
      padding:3px 0;min-width:0}
.item .bd{width:7px;height:7px;border-radius:50%;flex:0 0 auto}
.bd.on{background:var(--ok);box-shadow:0 0 6px rgba(46,204,113,.6)}
.bd.off{background:var(--bad);box-shadow:0 0 6px rgba(231,76,60,.5)}
.bd.na{background:#4a5263}
.item .k{color:var(--dim);flex:0 0 auto}
.item .v{color:var(--txt);overflow:hidden;text-overflow:ellipsis;
         white-space:nowrap;font-variant-numeric:tabular-nums}
.item .v.bad{color:var(--bad)}
.item .v.good{color:var(--ok)}
</style>
<div class="foot">
  暂停是「就地让路」：服务进程不退出，只是每轮跳过动作，按一下 1 秒内生效。<br>
  恢复后不需要重启服务。参数调节在 <code>手机IP:41737</code>。
</div>
</div>
<script>
const DAEMONS = __DAEMONS__;
let busy = false;

function el(tag, cls, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt !== undefined) e.textContent = txt;
  return e;
}

function render(s) {
  // 必须先算 manualOn，再用它（之前写反了会 TDZ 报错，整页 JS 直接挂掉）
  // 总开关是「全开才算开」：只要有一个没跑，就显示为关。
  const ctrl = DAEMONS.filter(x => x[3] === 'manual');
  const onCount = ctrl.filter(x => (s.daemons[x[0]] || {}).running).length;
  const manualOn = ctrl.length > 0 && onCount === ctrl.length;
  document.getElementById('mSw').checked = manualOn;
  document.getElementById('mLabel').textContent =
    manualOn ? '全部守护：已开启' :
    (onCount > 0 ? '部分开启（' + onCount + '/' + ctrl.length + '）' : '全部守护：已关闭');
  document.getElementById('mHint').textContent = manualOn
    ? '三个都在跑 · 下次开机自动开启'
    : (onCount > 0
        ? (ctrl.length - onCount) + ' 个没在跑 · 点一下全部开启'
        : '三个都停了 · 下次开机也不会自启');
  document.getElementById('mSw').disabled = busy;

  // 前台仲裁：两个守护轮流用前台，这里显示谁刚动过手。
  const arbBox = document.getElementById('arb');
  if (arbBox && s.arb) {
    const g = s.arb.gap_seconds;
    const parts = [];
    for (const [k, v] of Object.entries(s.arb.daemons || {})) {
      if (v.since === null || v.since === undefined) {
        parts.push(k + '：还没动过');
      } else {
        parts.push(k + '：' + v.since.toFixed(0) + ' 秒前' + (v.what ? '（' + v.what + '）' : ''));
      }
    }
    arbBox.textContent = '前台间隔 ' + g + ' 秒 · ' + parts.join(' · ');
  }

  const list = document.getElementById('list');
  list.innerHTML = '';
  for (const [id, name, desc, mode] of DAEMONS) {
    const d = s.daemons[id] || {};
    const running = !!d.running;
    const row = el('div', 'row' + (running ? '' : ' off'));
    const dot = el('div', 'dot ' + (running ? 'on' : 'off'));
    const meta = el('div', 'meta');
    meta.appendChild(el('div', 'name', name));
    meta.appendChild(el('div', 'desc', desc));
    let tag;
    if (mode === 'auto') {
      tag = 'service: ' + (d.service || '?') + ' · 常开';
    } else {
      const bootOn = (d.boot === 'enabled');
      tag = 'service: ' + (d.service || '?') +
            ' · 开机' + (bootOn ? '会自启' : '不自启') + ' · 开关即开机策略';
    }
    meta.appendChild(el('div', 'svc', tag));
    row.appendChild(dot); row.appendChild(meta);
    if (mode === 'auto') {
      const badge = el('span', 'lock', running ? '常开' : '异常');
      badge.style.cssText = 'font-size:12px;color:var(--dim);flex:none;padding:4px 8px;' +
        'border:1px solid var(--line);border-radius:6px';
      row.appendChild(badge);
    } else {
      const sw = el('label', 'sw');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = running;
      cb.disabled = busy;
      cb.onchange = () => toggle(id, cb.checked);
      sw.appendChild(cb);
      sw.appendChild(el('span'));
      row.appendChild(sw);
    }
    list.appendChild(row);
  }

  // 投屏不再内嵌：链接固定指向 /cast，由后端确认服务端就绪后再跳转。
  // 这里只把设备号挂到标题上，方便一眼看出是哪台机器。
  if (s.cast_udid) {
    const a = document.getElementById('castLink');
    if (a) a.title = '设备 ' + s.cast_udid;
  }

  renderChain('db', s.doubao || [], 'dbGrid', 'dbSum');
  renderChain('lc', s.lens || [], 'lcGrid', 'lcSum');

  // 手机卡住/探测熔断时，红点的一堆"没在跑"其实都是同一个原因，
  // 不说明的话会误以为 App 崩了。
  const warn = document.getElementById('pw');
  if (warn) {
    if (s.probe_down) {
      warn.textContent = '⚠ ' + (s.probe_note || 'adb 探测超时，手机可能卡住了；点位不代表真实状态');
      warn.style.display = 'block';
    } else {
      warn.style.display = 'none';
    }
  }
}

// 一条链路的所有细节点。灰点=信息项，红/绿=能判定成败的。
function renderChain(prefix, rows, gridId, sumId) {
  const g = document.getElementById(gridId);
  if (!g) return;
  g.innerHTML = '';
  let bad = 0, tot = 0;
  for (const r of rows) {
    const it = el('div', 'item');
    const d = el('div', 'bd ' + (r.ok === null ? 'na' : (r.ok ? 'on' : 'off')));
    it.appendChild(d);
    it.appendChild(el('span', 'k', r.label));
    const v = el('span', 'v' + (r.ok === null ? '' : (r.ok ? ' good' : ' bad')), r.value);
    v.title = r.label + '：' + r.value;
    it.appendChild(v);
    g.appendChild(it);
    if (r.ok !== null) { tot++; if (!r.ok) bad++; }
  }
  const sum = document.getElementById(sumId);
  if (sum) {
    sum.textContent = bad === 0 ? (tot + ' 项全部正常') : (bad + '/' + tot + ' 项异常');
    sum.style.color = bad === 0 ? 'var(--ok)' : 'var(--bad)';
  }
}

async function load() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();
    render(s);
  } catch (e) {
    document.getElementById('note').textContent = '读取失败：' + e;
  }
}

async function act(what, who) {
  if (busy) return;
  busy = true;
  document.getElementById('note').textContent = '处理中…';
  try {
    const r = await fetch('/api/' + what, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(who ? {daemon: who} : {})
    });
    const j = await r.json();
    document.getElementById('note').textContent = j.msg || '';
  } catch (e) {
    document.getElementById('note').textContent = '操作失败：' + e;
  }
  busy = false;
  await load();
}

function toggle(id, on) { act(on ? 'resume' : 'pause', id); }

document.getElementById('mSw').onchange = (e) =>
  act(e.target.checked ? 'resume' : 'pause');

// ---- 手机重启 ----
document.getElementById("rebootBtn").onclick = async () => {
  if (!confirm("确定重启手机？投屏和直播会中断，约 40 秒后恢复。")) return;
  const b = document.getElementById("rebootBtn");
  b.disabled = true; b.textContent = "重启中…";
  try {
    const r = await fetch("/api/phone/reboot", {method: "POST"});
    const j = await r.json();
    document.getElementById('note').textContent = j.msg || '';
  } catch (e) {
    document.getElementById('note').textContent = '重启请求失败：' + e.message;
  }
  setTimeout(() => { b.disabled = false; b.textContent = "重启手机"; }, 8000);
};

load();
setInterval(load, 4000);
</script>
</body></html>
"""


def svc_unit(name):
    return "airtest-" + name


def svc_start(name):
    """真启动一个守护服务。"""
    subprocess.run(["systemctl", "start", svc_unit(name)],
                   capture_output=True, text=True, timeout=30)


def svc_stop(name):
    """真停掉一个守护服务。"""
    subprocess.run(["systemctl", "stop", svc_unit(name)],
                   capture_output=True, text=True, timeout=30)


def svc_enabled(name):
    """开机自启状态：enabled / disabled。"""
    try:
        a = subprocess.run(["systemctl", "is-enabled", svc_unit(name)],
                           capture_output=True, text=True, timeout=5)
        return (a.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def ensure_adbd():
    """adbd 必须常开：开机自启 + 正在运行。gateweb 起来时补一次。"""
    for act in ("enable", "start"):
        try:
            subprocess.run(["systemctl", act, svc_unit("adbd")],
                           capture_output=True, text=True, timeout=20)
        except Exception:
            pass


def force_all_on():
    """adbd 常开：无论何时都不允许全局总闸被关。"""
    d = gate._read()
    if not d.get("all", True):
        d["all"] = True
        gate._write(d)


def svc_state(name):
    """读取 systemd 状态；不是 systemd 起的就报 -。"""
    try:
        a = subprocess.run(["systemctl", "is-active", "airtest-" + name],
                           capture_output=True, text=True, timeout=5)
        return (a.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def serial_for_detail():
    """细节点要一个设备序列号。拿不到就返回空，detail 会自己降级。"""
    try:
        import smartcast
        return smartcast.pick_serial()
    except Exception:
        return ""


def build_status():
    s = gate.status()
    for ident, _, _, mode in DAEMONS:
        st = svc_state(ident)
        s["daemons"][ident]["service"] = st
        s["daemons"][ident]["mode"] = mode
        s["daemons"][ident]["boot"] = svc_enabled(ident)
        s["daemons"][ident]["running"] = st == "active"
    s["at_now"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 投屏页地址：用请求的 Host 拼，换 IP 访问时也跟着换，
    # 不会出现「在 192.168.1.168 打开却嵌了 localhost」的错。
    try:
        _host = request.host.split(":")[0]
    except Exception:
        _host = "127.0.0.1"
    s["cast_base"] = "http://%s:8000/" % _host
    s["cast_url"] = s["cast_base"]
    # 直接出画面的深链。board 上的 open-web.sh 已经证明这套参数可用：
    #   #!action=stream&udid=..&player=mse&ws=<urlencode(proxy-adb url)>
    # ws 里的 host 用请求 Host（不能用 127.0.0.1，否则浏览器连不上）。
    try:
        _udid = serial_for_detail()
        if _udid:
            _inner = ("ws://%s:8000/?action=proxy-adb&remote=tcp:8886&udid=%s"
                      % (_host, _udid))
            from urllib.parse import quote
            s["cast_url"] = ("%s#!action=stream&udid=%s&player=mse&ws=%s"
                             % (s["cast_base"], _udid, quote(_inner, safe="")))
            s["cast_udid"] = _udid
    except Exception as e:
        s["cast_error"] = str(e)[:120]
    # 前台仲裁：谁刚动过前台、隔了多久。两个守护就是靠这个错开的，
    # 放在页面上才看得出"它们到底在轮什么"。
    try:
        s["arb"] = arb.status()
    except Exception as e:
        s["arb"] = {"error": str(e)[:80]}

    # 细节点：豆包投屏链路 + 直播推流链路。
    # 采集会跑 adb，失败绝不能把整个 /api/status 拖挂，否则页面全白。
    try:
        det = detail.collect(serial_for_detail())
        s["doubao"] = det.get("doubao", [])
        s["lens"] = det.get("lens", [])
        s["probe_at"] = det.get("at", "")
        s["probe_age"] = det.get("age", 0)
        s["probe_down"] = bool(det.get("probe_down"))
        s["probe_note"] = det.get("probe_note", "")
    except Exception as e:
        s["doubao"] = []
        s["lens"] = []
        s["probe_error"] = str(e)[:120]
    return s


@app.route("/")
def index():
    items = json.dumps([[a, b, c, d] for a, b, c, d in DAEMONS], ensure_ascii=False)
    resp = app.make_response(PAGE.replace("__DAEMONS__", items))
    # 不让浏览器缓存旧页面：改完页面刷新就能看到新版
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/status")
def api_status():
    return jsonify(build_status())


@app.route("/api/pause", methods=["POST"])
def api_pause():
    who = (request.get_json(silent=True) or {}).get("daemon")
    targets = ([who] if who else list(MANUAL))
    done = []
    for t in targets:
        if t in MANUAL:
            svc_stop(t)
            done.append(t)
    if who and who not in MANUAL:
        return jsonify({"ok": False, "msg": "%s 是常开守护，不能暂停" % who})
    # 关键：停服务但**不留 gate 暂停标记**。
    # 服务是 enabled 的，下次开机会照常起来；若留着暂停标记，
    # 起来后 gate.is_allowed() 为假，进程活着却一条命令都不发。
    for t in done:
        gate.resume(t, note="")
        # 关闭 = 下次开机也不起。把开关直接写进开机策略，
        # 就不会出现「这次关了、重启又自己回来」的假开关。
        subprocess.run(["systemctl", "disable", svc_unit(t)],
                       capture_output=True, text=True, timeout=15)
    force_all_on()
    label = "全部" if not who else who
    return jsonify({"ok": True, "msg": "%s 已关闭（%s），下次开机不会自启" % (label, ", ".join(done) or "-")})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    who = (request.get_json(silent=True) or {}).get("daemon")
    # 单项开启：允许；"全部启动"：只拉起开机自启的那批，
    targets = ([who] if who else sorted(MANUAL))
    done = []
    for t in targets:
        if t in MANUAL:
            svc_start(t)
            done.append(t)
    if who and who not in MANUAL:
        return jsonify({"ok": False, "msg": "%s 是常开守护，不需要启动" % who})
    for t in done:
        gate.resume(t, note="")
        # 开启 = 下次开机也起。
        subprocess.run(["systemctl", "enable", svc_unit(t)],
                       capture_output=True, text=True, timeout=15)
    force_all_on()
    label = "全部" if not who else who
    return jsonify({"ok": True, "msg": "%s 已开启（%s），下次开机会自启" % (label, ", ".join(done) or "-")})


@app.route("/api/phone/reboot", methods=["POST"])
def api_phone_reboot():
    """重启手机。

    用 adb reboot 而不是 su -c reboot：前者由 adbd 发起，手机重启后
    adbd 会随系统起来，不需要 root 权限，也不会因为 su 会话中断而失败。
    """
    serial = serial_for_detail()
    if not serial:
        return jsonify({"ok": False, "msg": "没有可用的手机设备"})
    try:
        p = subprocess.run(["/usr/lib/android-sdk/platform-tools/adb",
                            "-s", serial, "reboot"],
                           capture_output=True, text=True, timeout=25)
        if p.returncode != 0:
            return jsonify({"ok": False,
                            "msg": "adb reboot 失败：%s"
                                   % ((p.stderr or p.stdout or "").strip()[:120])})
        return jsonify({"ok": True, "msg": "重启指令已发出，约 40 秒后恢复"})
    except Exception as e:
        return jsonify({"ok": False, "msg": "重启出错：%s" % str(e)[:120]})


def cast_deep_link(host):
    """拼出带深链的投屏页地址。ws 参数必须整体 urlencode，否则 & 会截断。"""
    try:
        from urllib.parse import quote
    except ImportError:
        from urllib import quote  # py2 兜底
    base = "http://%s:8000/" % host
    serial = serial_for_detail()
    if not serial:
        return base
    inner = "ws://%s:8000/?action=proxy-adb&remote=tcp:8886&udid=%s" % (host, serial)
    return ("%s#!action=stream&udid=%s&player=mse&ws=%s"
            % (base, serial, quote(inner, safe="")))


def ensure_cast_server(serial):
    """让 ws-scrcpy 把手机上的 scrcpy 服务端拉起来。

    坑：devicelist 里的 pid 是 ws-scrcpy 缓存的上一次结果，进程死了也照报，
    所以判活必须靠真的握一次手（见 cast_ensure.js）。这里只负责调用，
    失败也不挡跳转 —— 大不了用户在 ws-scrcpy 页面上手动点一次「启动服务端」。
    """
    if not serial:
        return False
    try:
        p = subprocess.run(["node", os.path.join(HERE, "cast_ensure.js"), serial],
                           capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip().splitlines()
        if out:
            info = json.loads(out[-1])
            return bool(info.get("ok"))
    except Exception:
        pass
    return False


@app.route("/cast")
def cast_open():
    """一个链接直达投屏：先确保服务端在跑，再跳过去。

    为什么不放在浏览器里做：先 await fetch 再 window.open 会被弹窗拦截，
    用户会以为按钮坏了。跳转由后端发起就没有这个问题。
    """
    try:
        host = request.host.split(":")[0]
    except Exception:
        host = "127.0.0.1"
    ensure_cast_server(serial_for_detail())
    return redirect(cast_deep_link(host))


@app.route("/health")
def health():
    s = build_status()
    return jsonify({"ok": True, "allowed": s["all"]})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8140
    # adbd 常开：每次控制台起来先把它拉回 enabled + active。
    # 放在这里而不是只靠 systemd，是因为有人手滑 disable 过之后
    # 单靠 enable 的 symlink 不会自己回来。
    try:
        ensure_adbd()
    except Exception:
        pass
    print("守护总控台：http://0.0.0.0:%d/" % port, flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
