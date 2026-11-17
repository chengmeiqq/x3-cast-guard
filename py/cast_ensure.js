#!/usr/bin/env node
// 确保手机上 scrcpy 服务端已就绪，供 8140 页面投屏使用。
//
// 为什么要有它：
//   1) ws-scrcpy 只在收到 goog-device-list 的 start_server 时才拉起服务端；
//      直接连 proxy-adb 只会得到 4011 socket hang up。
//   2) 服务端不常驻（手机重启、MIUI 清 /data/local/tmp 后就没了），
//      每次进页面前都要问一次，而不是假设它一直在。
//   3) 关键坑：devicelist 里的 pid 是 ws-scrcpy 缓存的上一次结果，
//      进程早死了它照样报 pid=20644。所以不能用 pid 判活，
//      必须真的连一次 8886 收到 scrcpy_initial 才算数。
//
// 用法: node cast_ensure.js [udid]
// 输出: 一行 JSON {ok, msg}，方便 Python 侧解析。
const WS = require('/home/sunrise/projects/ws-scrcpy/node_modules/ws');

const UDID = process.argv[2] || '892e20c9';
const PORT = 8000;

let settled = false;
function done(obj) {
  if (settled) return;
  settled = true;
  process.stdout.write(JSON.stringify(obj) + '\n');
  process.exit(0);
}

// 1) 请 ws-scrcpy 拉起服务端（幂等：已在跑就什么都不做）
function kick() {
  return new Promise((resolve) => {
    const ws = new WS(`ws://127.0.0.1:${PORT}/?action=goog-device-list&type=android`);
    const t = setTimeout(() => { try { ws.close(); } catch (e) {} resolve(); }, 6000);
    ws.on('open', () => {
      ws.send(JSON.stringify({ id: 1, type: 'start_server', data: { udid: UDID } }));
    });
    ws.on('message', () => {});
    ws.on('error', () => {});
    ws.on('close', () => { clearTimeout(t); resolve(); });
    // 发出命令后留一点时间让它执行，再关
    setTimeout(() => { try { ws.close(); } catch (e) {} clearTimeout(t); resolve(); }, 3000);
  });
}

// 2) 真连一次 8886：收到 scrcpy_initial 才算服务端活着
function probe() {
  return new Promise((resolve) => {
    const ws = new WS(`ws://127.0.0.1:${PORT}/?action=proxy-adb&remote=tcp:8886&udid=${UDID}`);
    const t = setTimeout(() => { try { ws.close(); } catch (e) {} resolve(false); }, 8000);
    ws.on('message', (d) => {
      const b = Buffer.from(d);
      if (b.length > 14 && b.slice(0, 14).toString('latin1') === 'scrcpy_initial') {
        clearTimeout(t);
        // 回发 CHANGE_STREAM_PARAMETERS 后立刻断开，让服务端回到待命
        const vs = Buffer.alloc(35);
        let o = 0;
        o = vs.writeInt32BE(8000000, o);
        o = vs.writeInt32BE(30, o);
        o = vs.writeInt8(10, o);
        o = vs.writeInt16BE(720, o);
        o = vs.writeInt16BE(720, o);
        o = vs.writeInt16BE(0, o); o = vs.writeInt16BE(0, o);
        o = vs.writeInt16BE(0, o); o = vs.writeInt16BE(0, o);
        o = vs.writeInt8(0, o); o = vs.writeInt8(0, o);
        o = vs.writeInt32BE(0, o); o = vs.writeInt32BE(0, o);
        vs.writeInt32BE(0, o);
        try { ws.send(Buffer.concat([Buffer.from([101]), vs])); } catch (e) {}
        setTimeout(() => { try { ws.close(); } catch (e) {} resolve(true); }, 400);
      }
    });
    ws.on('error', () => { clearTimeout(t); resolve(false); });
    ws.on('close', () => { clearTimeout(t); resolve(false); });
  });
}

(async () => {
  const t0 = Date.now();
  if (await probe()) return done({ ok: true, msg: '服务端就绪' });

  // 没起来：催一次，等它把 jar 推上去并启动
  await kick();
  for (let i = 0; i < 6; i++) {
    await new Promise((r) => setTimeout(r, 1500));
    if (await probe()) return done({ ok: true, msg: '服务端已启动' });
  }
  done({ ok: false, msg: '服务端未就绪（' + Math.round((Date.now() - t0) / 1000) + ' 秒）' });
})();
