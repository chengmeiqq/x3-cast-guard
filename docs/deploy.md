# 部署与运维

## 目录约定

板上实际部署在 `/home/sunrise/projects/Airtest/`，本仓库是它的可复现快照。

```
Airtest/
├── .venv/                 Python 虚拟环境（不入库）
├── airweb.py              8123 点选式 UI 自动化网页
├── gateweb.py             8140 守护总控台
├── adbd.py                adb 连接守护
├── keepcast.py            豆包投屏守护
├── lenscast_keep.py       全镜直播守护
├── gate.py / arb.py / detail.py / uitap.py   内部模块
├── smartcast.py / aiverify.py / novacast.py  投屏底层与视觉复核
├── airlib.py              airweb 的底层（依赖 airtest）
├── health_check.sh        单行健康检查
├── cluster_brief.sh       一次性输出全部现场证据（供告警诊断）
├── state/                 运行时状态（不入库）
└── logs/                  日志（不入库）
```

## 冷启动顺序

systemd 负责，人不用登进来。开机后：

1. `airtest-timesync` 校准时间（RTC 电池失效，冷启动会掉回 2021）
2. `airtest-adbd` 拉起 adb，USB 优先，断了走 WiFi
3. `airtest-keepcast` 等设备就绪，重新建立豆包投屏
4. `airtest-lenscast` 等设备就绪，开播 4K
5. `airtest-gateweb` 起 8140 总控台

`airtest-keepcast` 和 `airtest-lenscast` 都要抢前台，靠 `arb.py` 仲裁轮流来，不会互相顶掉。

## 两个健康检查脚本的分工

| 脚本 | 用途 | 耗时 | 输出 |
|---|---|---|---|
| `health_check.sh` | 定时门控，每 15 分钟跑一次 | ~9.5s | 单行 `RESULT OK/WARN/BAD/DOWN` |
| `cluster_brief.sh` | 告警触发后的一次性取证 | ~1.2s | 多段：RESULT/服务/状态/掉线历史/前台/虚拟屏 |

**为什么要两个**：`health_check.sh` 只回答「好不好」，够用来决定要不要叫醒模型。
一旦决定叫醒，模型需要的是「现场全部证据」，如果让它自己一条条 ssh 去挖，
很容易打十几次工具调用把 300 秒的 payload 预算烧光，直接判超时失败。
`cluster_brief.sh` 把该看的东西一次吐完，一条命令解决。

## 判活规则

见 `README.md` 和 `health_check.sh` 顶部注释。**改之前先读**，这三个禁令都是踩过坑的：

- 不用 `dumpsys media_projection`
- 不用 UI dump
- 不请求 `/snapshot.jpg`

后台投屏场景下这三种都会给假阴性/假阳性。

## 已知周期性故障

豆包投屏会周期性地掉，表现为 `keepcast.log` 里：

```
19:18:59 掉线（投屏没了）：owner='' pid=10492 → 重新拉起
19:19:29 已重新拉起：已确认在投屏
```

间隔约 5-6 分钟，每次约 30 秒自愈。截至 2026-11-17 累计 117 次。
`keepcast` 能自愈，但**根因未定**，怀疑是通话/投屏会话被系统周期性回收。
规律的间隔说明它不是网络抖动。

## 日志

```
logs/keepcast.log    投屏守护，含每次掉线与重拉
logs/lenscast.log    直播守护，含前台被切走与顶回
logs/service.log     start-all 时代的遗留
```

排查掉线：

```bash
grep -E '掉线|已重新拉起' logs/keepcast.log | tail -20
```

看是否规律：比较相邻两次「掉线」的时间差。

## 定时监控

Gateway 侧的 automation（不在本仓库）每 15 分钟跑一次 `health_check.sh`，
`OK` 静默、`WARN` 每小时去重、`BAD` 每次告警。告警后会跑 `cluster_brief.sh` 取证。

约束：触发脚本单次评估 30 秒 / 5 次工具调用；payload 300 秒。
`health_check.sh` 实测 9.5 秒，`cluster_brief.sh` 实测 1.2 秒，都在预算内。

## 曾经踩过的坑

- **`lenscast` 的真实入口是 `lenscast_keep.py`**，按名字猜会拷到空文件。
- **`airweb.py` 依赖 `airlib.py`**，只拷 `airweb.py` 会 ImportError。
- **`start-all.sh` / `stop-all.sh` / `cast.sh` 已失效**，引用的 usbwatch / keytrigger /
  smartcast `--serve` 都已下线，systemd 也没引用它们。留在 Airtest 里当历史，本仓库不收。
- **`health_check.sh` 跑得异常快（0.5s）不代表脚本坏了**，可能是 adb 真的快速返回了
  「确实没有虚拟屏」。要先逐条看命令有没有返回内容，再下结论。