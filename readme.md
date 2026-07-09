# MajesticChat

基于 EasyTier 的去中心化 P2P 聊天与小游戏程序，支持群聊、私聊、文件传输、历史同步、节点发现与内置游戏。

![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![EasyTier](https://img.shields.io/badge/EasyTier-Apache--2.0-orange.svg)

---

## ✨ 主要功能

- **去中心化组网**：通过 EasyTier 自动建立 P2P 虚拟网络，无需中心服务器。
- **即时聊天**：支持群聊与私聊。
- **文件传输**：逐块发送、MD5 校验、进度显示、可取消。
- **历史同步**：新节点加入时自动同步缺失聊天记录。
- **节点发现**：定期刷新对等节点列表，自动连接新节点。
- **心跳保活**：定期 Ping/Pong 检测连接状态，超时自动断开。
- **SQLite 本地持久化**：聊天记录保存在本地数据库。
- **内置小游戏**：支持五子棋（Gomoku）和猜数字（Guess）。
- **跨平台兼容**：Windows / Linux / macOS（需搭配对应 EasyTier 可执行文件）。

---

## 📋 前置条件

- Python 3.8+
- 将 EasyTier 核心文件（`easytier-core` 和 `easytier-cli`）放到程序同目录。
- [EasyTier Releases](https://github.com/zhanghanyun/easytier/releases)：选择对应平台版本。
- 网络环境允许 UDP 打洞。

> 默认使用公共中继服务器 `38.147.105.178:11010`，如需自定义请修改 `easytier_utils.py` 中的 `relay_ip` 和 `relay_port`。

---

## 🎮 常用命令

| 命令 | 说明 |
|------|------|
| `/list` / `/who` | 查看当前在线用户 |
| `/msg <昵称> <消息>` | 私聊指定用户 |
| `/send <昵称> <文件路径>` | 发送文件给指定用户 |
| `/cancel <传输ID>` / `/cancel all` | 取消一个或全部文件传输 |
| `/host <游戏名> [端口]` | 创建外部联机房间 |
| `/join <游戏名>` | 加入外部联机房间 |
| `/rooms` | 列出外部游戏房间 |
| `/stopgame <游戏名>` | 结束自己创建的外部游戏房间 |
| `/leave <游戏名>` | 退出外部游戏房间 |
| `/start_game <gomoku\|guess> [参数]` | 创建内置小游戏 |
| `/join_game <game_id>` | 加入内置小游戏 |
| `/game <game_id> <动作>` | 执行小游戏操作（如 `move 7 7` 或 `guess 50`） |
| `/games` | 列出当前活跃的内置小游戏 |
| `/end_game <game_id>` | 结束自己创建的小游戏 |
| `/history` | 查看最近 20 条聊天记录 |
| `/help` | 显示帮助信息 |

> 建议：后续可将外部房间命令统一为 `/rooms`、`/join_room`，内置游戏命令统一为 `/games`、`/join_game`，以减少混淆。

---

## ⚙️ 运行原理

1. **启动 EasyTier**：程序启动 EasyTier 进程并加入虚拟网络，获取虚拟 IP（如 `10.x.x.x`）。
2. **节点发现**：通过 `easytier-cli peer` 获取当前网络中的对等节点。
3. **建立连接**：与对等节点建立 TCP 长连接（默认端口 `8888`），交换握手信息。
4. **同步历史**：连接建立后互相同步缺失消息记录。
5. **消息广播**：群聊消息广播给所有连接节点，私聊仅发送给目标节点。
6. **文件传输**：文件按块编码发送，接收端校验 MD5 并保存。
7. **心跳与重连**：定期发送 `ping`，若未收到 `pong` 则断开并尝试重连。
8. **小游戏同步**：由创建者作为主机验证动作并广播游戏状态。

---

## 📦 打包发布

推荐使用 PyInstaller 打包为可执行文件：

\`\`\`bash
pyinstaller -D -n MajesticChat --uac-admin --hidden-import=sqlite3 --console chat_main.py
\`\`\`

打包完成后，将 `easytier-core.exe` 和 `easytier-cli.exe` 一起放入 `dist/MajesticChat/` 目录内分发。

---

## 📝 使用提示

- 建议使用 PowerShell 或 Windows Terminal 运行，以获得更佳日志与 Emoji 显示效果。
- 文件传输无显式大小限制，但请注意接收端磁盘空间。
- 若防火墙阻止连接，请允许端口 `8888` 和 EasyTier 的网络访问。

---

## 📄 许可证

### 依赖协议
- **EasyTier** - Apache License 2.0

### 本项目协议
本项目源码与文档采用 **MIT License**，详见 `LICENSE` 文件。

---

## 🙏 致谢

- [EasyTier](https://github.com/zhanghanyun/easytier) – 提供 P2P 组网能力。
- 参与测试和贡献的朋友们。