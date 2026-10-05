# MajesticLink

> 去中心化 Mesh 通讯与联机平台 · 为小型私有协作域而生

MajesticLink 是一个基于 **EasyTier Overlay Network** 的无中心化通讯套件，面向 5~10 人的小型私有团体，提供低延迟语音、加密即时通讯、点对点文件分发与虚拟局域网游戏联机能力。无需任何中心服务器，数据主权完全归用户所有。

![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![EasyTier](https://img.shields.io/badge/EasyTier-Apache--2.0-orange.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

---

## ✨ 核心特性

### 🌐 去中心化组网
- 基于 EasyTier 构建**零配置虚拟局域网**，自动 NAT 穿透
- **P2P 直连优先**，失败时智能回退至中继，无需公网 IP
- 虚拟网段自动分配（`10.x.x.x` / `100.64.x.x`）
- 定期刷新对等节点列表，新节点自动发现并连接

### 💬 加密即时通讯
- 握手阶段完成 **HMAC-SHA256 会话密钥派生**
- 消息走加密隧道，防止中间人窃听
- 群聊广播 / 私聊定向
- SQLite 本地持久化，支持新节点**自动历史同步**
- 消息去重 + 断点续传

### 🎙️ 低延迟语音
- **Opus 编码**（16kHz / 24kbps）+ UDP 传输
- **动态抖动缓冲**（40~100ms 自适应）
- **VAD 自由发言** / **PTT 按键说话** 双模式
- **全局热键**（默认 `Ctrl+Shift+V`），游戏内一键说话
- 多人混音，5 人以内零延迟叠加

### 📦 点对点文件传输
- 分块传输（256KB/块）+ MD5 完整性校验
- **实时进度与速度显示**
- 支持取消、断线自动清理
- 走 EasyTier 加密隧道，无需额外加密层

### 🎮 LAN-over-P2P 游戏联机
- 广播虚拟 IP 与端口，**Minecraft / 泰拉瑞亚 / 星露谷**等局域网游戏即插即用
- 游戏流量直达虚拟网卡，**零额外延迟**
- GUI 一键创建 / 加入房间，双击复制地址
- 房间列表全节点自动同步

### 📊 网络可视化
- **实时 RTT 延迟显示**（每个节点独立统计）
- 心跳保活 + 超时自动重连
- 连接状态、在线用户列表实时刷新

---

## 🚀 快速开始

### 前置条件

- **Python 3.11+**
- 从 [EasyTier Releases](https://github.com/zhanghanyun/easytier/releases) 下载对应平台的：
  - `easytier-core.exe`
  - `easytier-cli.exe`
  - `wintun.dll` / `Packet.dll` / `WinDivert64.sys`（Windows）
- 将所有 EasyTier 文件放到程序同目录

### 从源码运行

```bash
# 安装依赖
pip install sounddevice numpy pyogg keyboard

# 启动
python main.py
```

首次启动会提示输入：
- **网络名称**（所有成员必须一致）
- **网络密码**（用于身份认证与密钥派生）
- **昵称**

### 打包为独立 EXE

```bash
pyinstaller -D -n MajesticLink ^
  --uac-admin ^
  --windowed ^
  --collect-all=pyogg ^
  --collect-all=sounddevice ^
  --collect-all=keyboard ^
  --add-data "easytier-core.exe;." ^
  --add-data "easytier-cli.exe;." ^
  --add-data "Packet.dll;." ^
  --add-data "WinDivert64.sys;." ^
  --add-data "wintun.dll;." ^
  --add-binary "C:\Python311\Lib\site-packages\pyogg\libs\win_amd64\*.dll;pyogg\libs\win_amd64" ^
  main.py
```

打包后，`dist\MajesticLink\` 文件夹即为完整可分发版本，**必须以管理员身份运行**。

---

## 🎯 使用指南

### GUI 界面

```
┌────────────────────────────────────────────────┐
│ 在线用户            │        聊天记录            │
│ · Alice (10.x.x.1) │ [12:30] Alice: 走开黑       │
│ · Bob   (10.x.x.2) │ [12:31] Bob: 等我！         │
│   延迟: 23 ms       │                            │
├────────────────────┴────────────────────────────┤
│ 游戏房间                                        │
│ · minecraft  Alice  10.x.x.1:25565              │
├─────────────────────────────────────────────────┤
│ [VAD/PTT] [热键: Ctrl+Shift+V] [音量] [加入语音]│
├─────────────────────────────────────────────────┤
│ [输入框]                    [📎 发送文件] [发送]│
└─────────────────────────────────────────────────┘
```

### 常用命令

| 命令 | 说明 |
|------|------|
| `/list` / `/who` | 查看当前在线用户 |
| `/msg <昵称> <消息>` | 私聊指定用户 |
| `/send <昵称> <路径>` | 发送文件（也可点击 📎 按钮） |
| `/cancel <ID>` / `/cancel all` | 取消文件传输 |
| `/host <游戏> [端口]` | 创建游戏房间（也可点击"创建房间"） |
| `/join <游戏>` | 加入房间（也可点击"加入房间"） |
| `/rooms` | 列出所有游戏房间 |
| `/leave <游戏>` | 退出房间 |
| `/stopgame <游戏>` | 结束自己的房间 |
| `/history` | 查看最近 20 条聊天记录 |
| `/help` | 显示帮助 |

### 游戏联机流程

1. **主机**：在 GUI 点击"创建房间"，填写游戏名和端口（Minecraft 默认 25565，泰拉瑞亚默认 7777）
2. **其他玩家**：房间自动出现在列表中，点击"加入房间"，复制地址
3. **启动游戏**：在游戏客户端输入 `<虚拟IP>:<端口>`，如 `10.126.126.1:25565`
4. **开玩**：游戏流量走 EasyTier 虚拟网卡，延迟等同局域网

### 语音通话流程

1. 双方都点击"加入语音"
2. **VAD 模式**：直接说话即可，静音自动停止发送
3. **PTT 模式**：按住 `Ctrl+Shift+V` 说话，松开停止
4. 点击热键标签可修改按键（推荐 `caps lock` 或 `ctrl+shift+v`）

---

## ⚙️ 技术架构

```
┌─────────────────────────────────────────┐
│           GUI 层 (Tkinter)              │
│   用户列表 / 聊天 / 游戏房间 / 语音栏    │
├─────────────────────────────────────────┤
│           业务逻辑层                     │
│  ChatManager │ VoiceManager │ GameManager│
│              │ FileManager   │           │
├─────────────────────────────────────────┤
│           网络通信层                     │
│  ControlChannel (TCP) │ VoiceChannel(UDP)│
├─────────────────────────────────────────┤
│           基础设施层                     │
│  Crypto │ Discovery │ EasyTierManager   │
├─────────────────────────────────────────┤
│           EasyTier Overlay Network      │
│      P2P Mesh · NAT 穿透 · 智能中继     │
└─────────────────────────────────────────┘
```

### 关键设计

| 机制 | 实现 |
|------|------|
| **握手认证** | Nonce 交换 + HMAC-SHA256 双向验证 |
| **会话密钥** | 双方 Nonce 排序后与网络密钥派生 |
| **心跳保活** | 30s Ping / Pong，90s 超时断开 |
| **延迟统计** | Ping/Pong RTT 实时计算 |
| **文件传输** | 256KB 分块 + MD5 校验 + 加密隧道 |
| **语音链路** | Opus + 抖动缓冲 + 动态混音 |
| **节点发现** | 15s 轮询 + 3 次容错离线判定 |

---

## 📝 使用提示

- **管理员权限**：EasyTier 需要创建虚拟网卡，**必须以管理员身份运行**
- **防火墙**：首次运行请允许 `MajesticLink.exe` 通过防火墙
  - TCP 8888（控制通道）
  - UDP 8889（语音通道）
- **语音回声**：建议**戴耳机**。外放会导致对方听到自己说话的回声
- **音频设备**：确保麦克风/扬声器驱动正常，Windows 隐私设置中允许应用访问麦克风
- **带宽**：P2P 直连速度受限于双方**宽带上行**，通常 1-3 MB/s；若走中继会更慢

---

## 🛠️ 常见问题

**Q：看不到对方？**
A：检查网络名和密码是否**完全一致**（大小写、空格）。

**Q：消息发不出去？**
A：检查防火墙是否放行 TCP 8888；或用 `easytier-cli.exe peer` 确认双方虚拟 IP 是否可见。

**Q：语音单向？**
A：检查 UDP 8889 是否放行；建议双方都使用耳机。

**Q：文件速度只有 1-2 MB/s？**
A：这是家庭宽带上行的物理限制（100M 宽带上行通常 10-20 Mbps），非程序问题。

**Q：程序退出后 EasyTier 残留？**
A：正常情况下会自动清理；如异常残留，手动执行 `taskkill /f /im easytier-core.exe`。

---

## 🔐 安全说明

- **网络层**：EasyTier 默认启用 WireGuard / AES-128-GCM 加密
- **应用层**：控制通道使用 HMAC-SHA256 + XOR 掩码加密
- **文件传输**：走 EasyTier 加密隧道，跳过冗余的应用层加密以提升速度
- **语音通道**：UDP 明文（局域网内），如需更高安全性可启用 TLS
- **身份验证**：基于共享网络密钥的 HMAC 挑战-响应机制

---

## 📄 许可证

### 依赖协议
- **EasyTier** — Apache License 2.0
- **PyOgg / Opus** — BSD / MIT
- **sounddevice / PortAudio** — MIT
- **keyboard** — MIT

### 本项目协议
本项目源代码与文档采用 **MIT License**，详见 [LICENSE](LICENSE) 文件。

---

## 🙏 致谢

- [EasyTier](https://github.com/zhanghanyun/easytier) — 提供强大的 P2P 组网能力
- [PyOgg](https://github.com/TeamPyOgg/PyOgg) — Opus 编解码支持
- [sounddevice](https://github.com/spatialaudio/python-sounddevice) — 跨平台音频 I/O
- 所有参与测试与反馈的朋友们

---

<div align="center">

**MajesticLink** — 你的网络，你的规则。

[报告问题](https://github.com/yourusername/MajesticLink/issues) · [功能建议](https://github.com/yourusername/MajesticLink/issues) · [贡献代码](https://github.com/yourusername/MajesticLink/pulls)

</div>