# x3-cast-guard

在 RDK X3（Ubuntu / aarch64）上，用一台 Android 手机做「豆包投屏 + 全镜 4K 直播」的**无人值守守护集群**。

一个手机、一条 USB、一块板子，跑四个常驻守护。挂了自己爬起来，掉了自己重连，断了自动重开投屏。带一个 8140 网页总控台和一键健康检查。

```
                    ┌─────────────────────────────────────┐
   USB / WiFi ──────┤ 小米 12S Ultra                      │
                    │  豆包 com.larus.nova（投屏源）        │
                    │  全镜 com.opencode.multilensipcam     │
                    └───────────────┬─────────────────────┘
                                    │ adb (892e20c9)
                    ┌───────────────┴─────────────────────┐
                    │ 板子 192.168.1.168  ubuntu-x3        │
                    │                                     │
                    │  airtest-adbd      连接守护           │
                    │  airtest-keepcast  投屏守护           │
                    │  airtest-lenscast  直播守护           │
                    │  airtest-gateweb   总控台 :8140       │
                    │  ws-scrcpy         投屏看板 :8000     │
                    └─────────────────────────────────────┘
```

## 它解决什么问题

手机长时间跑直播 + 投屏，会遇到的真实故障：

- **投屏悄悄断掉**。豆包的 `ScreenCaptureService` 还在，但虚拟屏已经 `state OFF`。只看进程会漏判。
- **手机重启后 adb 掉线**，连不上，守护全部空转。
- **投屏守护和直播守护抢前台**，互相把对方顶掉，来回闪。
- **板子 RTC 电池失效**，冷启动时间掉回 2021 年，日志和证书全乱。

这四个都在这套代码里有对应处理，不是靠"应该没事"。

## 组成

| 单元 | 程序 | 职责 |
|---|---|---|
| `airtest-adbd` | `py/adbd.py` | USB 优先、断了自动切 WiFi，保持 `892e20c9` 在线 |
| `airtest-keepcast` | `py/keepcast.py` | 判断投屏是否真的活着，断了自动重新拉起 |
| `airtest-lenscast` | `py/lenscast_keep.py` | 插着 USB 就开播：4K / 质量 100 / 对焦 1.53 / 30fps |
| `airtest-gateweb` | `py/gateweb.py` | 网页总控台 `:8140`，开关上三个，看状态，重启手机 |
| `ws-scrcpy` | 外部仓库 | 投屏看板 `:8000`，`/cast` 跳转入口 |
| `airtest-timesync` | `py/timesync.sh` | 开机校时，兜住 RTC 失效 |
| `airtest-airweb` | `py/airweb.py` | 网页点选式 UI 自动化 `:8123`，框选找图、点坐标、存步骤重放 |

## 判活规则（重要，别改）

这是踩过坑换来的，改之前先读 `py/health_check.sh` 顶部注释。

**豆包投屏 = 两个条件同时成立**

1. `dumpsys activity services com.larus.nova` 里有 `ScreenCaptureService`
2. `dumpsys display` 里有 `uniqueId="virtual:` **且** `state ON` **且** `owner com.larus.nova`

**禁止**用 `dumpsys media_projection`、**禁止** UI dump、**禁止**请求 `/snapshot.jpg` 判活。

理由：后台投屏场景下这三种都会给出假阴性或假阳性。曾经因为只看进程，投屏其实已经断了还在报正常。

## 两个检查脚本，分工不同

| 脚本 | 用途 | 实测耗时 | 输出 |
|---|---|---|---|
| `health_check.sh` | 定时门控：好不好 | ~9.5s | 单行 `RESULT OK/WARN/BAD/DOWN` |
| `cluster_brief.sh` | 告警后取证：哪儿坏了 | ~1.2s | 多段现场证据 |

分开的原因：`health_check.sh` 只回答「好不好」，够用来决定要不要叫醒模型。
一旦叫醒，模型要的是「全部现场证据」。让它自己一条条 ssh 去挖，很容易打十几次
工具调用把 300 秒的 payload 预算烧光，直接判超时。`cluster_brief.sh` 一次吐完。

### `health_check.sh`

输出单行，供定时任务解析：

```bash
$ ./py/health_check.sh
RESULT OK 全部正常|cast=1 nova=10488 lens=10489 fg=com.opencode.multilensipcam/.MainActivity level=100 temp=300|services: airtest-adbd:active airtest-keepcast:active airtest-lenscast:active airtest-gateweb:active
```

状态三档：

- `OK` — 全绿，不打扰任何人
- `WARN` — 能跑但不理想（电量低、温度高、前台不在直播）
- `BAD` — 投屏断了 / 进程没了 / 服务挂了
- `DOWN` — 手机根本不在线

单次实测约 9.5 秒。

### `cluster_brief.sh`

一次性吐出全部现场证据，只读，不动任何状态：

```bash
$ ssh root@192.168.1.168 "bash cluster_brief.sh"
### RESULT
RESULT OK 全部正常|cast=1 nova=27484 lens=10489 fg=... level=100 temp=300|services: ...
### SERVICES
airtest-adbd=active airtest-keepcast=active ...
### KEEPCAST_STATE
{"ok": true, ...}
### 最近掉线事件
19:30:07 掉线（投屏没了）：owner='' pid=27481 → 重新拉起
19:30:42 已重新拉起：已确认在投屏
### 掉线历史统计 / ### 前台与投屏 / ### END
```

实测 1.23 秒。

## 安装

```bash
# 板子上
sudo ./install.sh
```

`install.sh` 会做这几件事，且**不覆盖已有文件**：

1. 检查 `adb`、`python3`、`flask` 是否就位
2. 把 `py/*.py` 同步到 `/opt/x3-cast-guard/`（或自定义 `PREFIX`）
3. 装 `systemd/*.service` 到 `/etc/systemd/system/`
4. `systemctl daemon-reload && enable --now`
5. 跑一次 `health_check.sh` 打印结果

改路径：`PREFIX=/home/sunrise/projects/x3-cast-guard sudo -E ./install.sh`

Python 依赖见 `requirements.txt`（Flask / opencv / numpy / airtest）。`adb` 不走 pip，用系统包。

> 单元文件里的 `ExecStart` 写的是 `/home/sunrise/projects/Airtest/`，因为本仓库是从那里抽出来的。装到别处需要同步改单元文件，`install.sh` 会提示。

## 总控台 `:8140`

| 路由 | 说明 |
|---|---|
| `GET /` | 状态页，实时刷新 |
| `GET /api/status` | JSON 状态 |
| `GET /cast` | 确保 scrcpy 服务端在跑，然后 302 到 ws-scrcpy 深链 |
| `POST /api/pause` | 暂停某个守护，body `{"daemon":"adbd\|keepcast\|lenscast"}` |
| `POST /api/resume` | 恢复 |
| `POST /api/phone/reboot` | 重启手机 |

`/cast` 是后端跳转，不是页面内嵌 iframe —— 内嵌方案在 `127.0.0.1` 和 `192.168.1.168` 两种 Host 下会被同源策略和深链路径搞坏。

## 投屏看板判活

scrcpy 是否活着，**只认真的握手收到 `scrcpy_initial`（231 字节）**。

不能看 jar 存不存在，也不能看 devicelist 里的 pid —— 只连 `proxy-adb` 不会拉起服务端，必须先发 `start_server`。这条也踩过，写在这里免得再走一遍。

## 配置

守护的开关状态在 `state/allow.json`，运行时状态在 `state/*.json`。`state/` 不入库。

豆包投屏的视觉复核会尝试读 `~/.openclaw/openclaw.json` 拿模型 key。板上通常没有这个文件，拿不到 key 时会**自动降级**为「按系统状态判定」，功能不受影响，只是日志里会留一行说明。这是设计行为，不是故障。

## 直播参数

固定，不要随手改：

```
camera=0  resolution=3840x2160  quality=100  zoom=1.53  fps=30
```

不分屏。豆包在后台保屏，直播永远在前台。

## 已知周期性故障

豆包投屏会周期性掉线，`logs/keepcast.log` 里长这样：

```
19:18:59 掉线（投屏没了）：owner='' pid=10492 → 重新拉起
19:19:29 已重新拉起：已确认在投屏
```

间隔约 5-6 分钟，每次约 30 秒自愈。截至 2026-11-17 累计 117 次。

`keepcast` 每次都自愈了，但**根因未定**。间隔这么规整，不像网络抖动，
更像通话或投屏会话被系统周期性回收。想让监控不用每次都报警，得先把这个治了。

## 不在本仓库里的东西

`start-all.sh`、`stop-all.sh`、`cast.sh` 留在 Airtest 仓库当历史，本仓库不收：
它们引用的 `usbwatch`、`keytrigger`、`smartcast --serve` 都已下线，systemd 也不再引用，
`cast.sh` 打的还是 8130 那个已经没有的接口。照着它们开机只会一无所获。

开机现在完全由 `systemd/*.service` 负责，见 `docs/deploy.md`。

## 许可

MIT