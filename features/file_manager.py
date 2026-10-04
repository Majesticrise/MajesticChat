"""
文件传输管理
- 分块发送（64KB/块，Base64 编码）
- MD5 校验
- 接收端自动保存到 received_files/
- 进度回调到 GUI
"""
import asyncio
import base64
import hashlib
import os
import time
from typing import Any, Mapping, Optional

from core.config import RECEIVED_FILES_DIR
from core.event_bus import EventBus, Events
from core.protocol import MsgType
from network.control_channel import ControlChannel

CHUNK_SIZE = 256 * 1024         # 256KB
SEND_TIMEOUT = 300             # 单文件发送超时（秒）
RECEIVE_TIMEOUT = 300          # 接收超时（秒）


class FileManager:
    def __init__(self, username: str, control: ControlChannel,
                 event_bus: EventBus, runtime):
        self.username = username
        self.control = control
        self.event_bus = event_bus
        self.runtime = runtime

        # 发送中的任务：transfer_id -> asyncio.Task
        self._send_tasks: dict[str, asyncio.Task] = {}
        # 接收上下文：key = f"{ip}:{transfer_id}"
        self._recv_ctx: dict[str, dict] = {}
        # 取消标志
        self._cancel_flags: dict[str, bool] = {}

        os.makedirs(RECEIVED_FILES_DIR, exist_ok=True)

    async def start(self):
        self.event_bus.subscribe("user_cancel_file", self._on_user_cancel_file)
        # 监听网络消息
        self.event_bus.subscribe("control_message", self._on_control_message)
        # 监听用户命令
        self.event_bus.subscribe("user_send_file", self._on_user_send_file)

    # ========== 用户发送（GUI 线程）==========
    def _on_user_send_file(self, data: Mapping[str, Any]):
        target_name = data.get("target_name", "").strip()
        file_path = data.get("file_path", "").strip()
        if not target_name or not file_path:
            self._sys_msg("用法: /send <昵称> <文件路径>")
            return
        if not os.path.exists(file_path):
            self._sys_msg(f"文件不存在: {file_path}")
            return
        if not os.path.isfile(file_path):
            self._sys_msg(f"不是文件: {file_path}")
            return
        self.runtime.submit(self._send_file(target_name, file_path))

    async def _send_file(self, target_name: str, file_path: str):
        # 查找目标 IP
        target_ip = await self._resolve_username(target_name)
        if not target_ip:
            self._sys_msg(f"用户 '{target_name}' 不在线")
            return

        # 准备传输信息
        try:
            file_size = os.path.getsize(file_path)
            filename = os.path.basename(file_path)
            chunk_size = CHUNK_SIZE
            total_chunks = (file_size + chunk_size - 1) // chunk_size
        except Exception as e:
            self._sys_msg(f"读取文件失败: {e}")
            return

        # 计算 MD5
        md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5.update(chunk)
            file_md5 = md5.hexdigest()
        except Exception as e:
            self._sys_msg(f"计算 MD5 失败: {e}")
            return

        transfer_id = f"{self.username}_{int(time.time() * 1000)}"
        self._cancel_flags[transfer_id] = False

        # 发送 file_start（不加密，走 EasyTier 加密隧道）
        start_msg = {
            "type": MsgType.FILE_START,
            "sender": self.username,
            "filename": filename,
            "total_size": file_size,
            "total_chunks": total_chunks,
            "transfer_id": transfer_id,
            "md5": file_md5,
        }
        ok = await self.control.send_to_raw(target_ip, start_msg)
        if not ok:
            self._sys_msg(f"发送失败: 无法连接 {target_name}")
            return

        size_mb = file_size / 1024 / 1024
        self._sys_msg(f"开始发送 {filename} → {target_name} ({size_mb:.2f} MB)")
        self.event_bus.publish(Events.FILE_STARTED, {
            "transfer_id": transfer_id,
            "filename": filename,
            "total": file_size,
            "direction": "send",
            "target": target_name,
        })

        # 分块发送
        sent = 0
        last_progress = -1
        start_time = time.time()
        try:
            with open(file_path, "rb") as f:
                for i in range(total_chunks):
                    # 检查取消
                    if self._cancel_flags.get(transfer_id, False):
                        await self.control.send_to(target_ip, {
                            "type": MsgType.FILE_ABORT,
                            "sender": self.username,
                            "transfer_id": transfer_id,
                        })
                        self._sys_msg(f"已取消传输 {transfer_id}")
                        return

                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    encoded = base64.b64encode(chunk).decode("ascii")
                    chunk_msg = {
                        "type": MsgType.FILE_CHUNK,
                        "sender": self.username,
                        "transfer_id": transfer_id,
                        "index": i,
                        "total": total_chunks,
                        "data": encoded,
                    }
                    ok = await self.control.send_to_raw(target_ip, chunk_msg)
                    if not ok:
                        self._sys_msg(f"传输中断: 与 {target_name} 断开")
                        return

                    sent += len(chunk)
                    progress = int((i + 1) * 100 / total_chunks)
                    if progress >= last_progress + 10:
                        last_progress = progress
                        elapsed = time.time() - start_time
                        speed = sent / elapsed / 1024 / 1024 if elapsed > 0 else 0
                        self.event_bus.publish(Events.FILE_PROGRESS, {
                            "transfer_id": transfer_id,
                            "progress": progress,
                            "sent": sent,
                            "total": file_size,
                        })
                        self._sys_msg(
                            f"📤 发送 {filename}: {progress}% "
                            f"({sent / 1024 / 1024:.1f}/{size_mb:.1f} MB, {speed:.1f} MB/s)"
                        )

                    # 每块之间让出一次控制权（避免事件循环饿死心跳/聊天消息）
                    # 若发现速度不理想，可以注释掉这行
                    await asyncio.sleep(0)

            elapsed = time.time() - start_time
            speed = file_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
            self._sys_msg(
                f"✅ 文件 '{filename}' 已发送给 {target_name} "
                f"({size_mb:.2f} MB, 耗时 {elapsed:.1f}s, 平均 {speed:.1f} MB/s)"
            )
            self.event_bus.publish(Events.FILE_FINISHED, {
                "transfer_id": transfer_id,
                "direction": "send",
                "filename": filename,
            })

        except Exception as e:
            self._sys_msg(f"发送文件失败: {e}")
        finally:
            self._cancel_flags.pop(transfer_id, None)

    async def _resolve_username(self, username: str) -> Optional[str]:
        """通过控制通道查找用户名对应的 IP"""
        async with self.control._lock:
            for ip, conn in self.control.connections.items():
                if conn.username == username:
                    return ip
        return None

    def _on_user_cancel_file(self, data: Mapping[str, Any]):
        target = data.get("target", "")
        if target == "all":
            for tid in list(self._cancel_flags.keys()):
                self._cancel_flags[tid] = True
            self._sys_msg("已取消所有传输")
        else:
            self.cancel(target)
            self._sys_msg(f"已请求取消 {target}")

    # ========== 用户取消 ==========
    def cancel(self, transfer_id: str):
        self._cancel_flags[transfer_id] = True

    # ========== 网络接收 ==========
    def _on_control_message(self, data: Mapping[str, Any]):
        ip = data.get("ip")
        msg = data.get("msg", {})
        msg_type = msg.get("type", "")

        if msg_type == MsgType.FILE_START:
            self.runtime.submit(self._handle_file_start(ip, msg))
        elif msg_type == MsgType.FILE_CHUNK:
            self.runtime.submit(self._handle_file_chunk(ip, msg))
        elif msg_type == MsgType.FILE_ABORT:
            self.runtime.submit(self._handle_file_abort(ip, msg))
        elif msg_type == MsgType.FILE_RECEIVED:
            tid = msg.get("transfer_id", "")
            print(f"[File] 对方已接收 {tid}")

    async def _handle_file_start(self, ip: str, msg: Mapping[str, Any]):
        sender = msg.get("sender", "?")
        filename = msg.get("filename", "unknown")
        total_size = int(msg.get("total_size", 0))
        total_chunks = int(msg.get("total_chunks", 0))
        transfer_id = msg.get("transfer_id", "")
        file_md5 = msg.get("md5", "")

        if not transfer_id:
            return

        # 生成保存路径（避免重名）
        base, ext = os.path.splitext(filename)
        save_path = os.path.join(RECEIVED_FILES_DIR, filename)
        counter = 1
        while os.path.exists(save_path):
            save_path = os.path.join(RECEIVED_FILES_DIR, f"{base}_{counter}{ext}")
            counter += 1

        try:
            fh = open(save_path, "wb")
        except Exception as e:
            self._sys_msg(f"无法创建文件: {e}")
            return

        key = f"{ip}:{transfer_id}"
        self._recv_ctx[key] = {
            "filename": filename,
            "total_chunks": total_chunks,
            "total_size": total_size,
            "received": 0,
            "file_handle": fh,
            "target_path": save_path,
            "sender": sender,
            "md5": file_md5,
            "last_progress": -1,
        }

        self._sys_msg(f"接收来自 {sender} 的文件: {filename} ({total_size} 字节)")
        self.event_bus.publish(Events.FILE_STARTED, {
            "transfer_id": transfer_id,
            "filename": filename,
            "total": total_size,
            "direction": "recv",
            "sender": sender,
        })

    async def _handle_file_chunk(self, ip: str, msg: Mapping[str, Any]):
        transfer_id = msg.get("transfer_id", "")
        key = f"{ip}:{transfer_id}"
        ctx = self._recv_ctx.get(key)
        if not ctx:
            return
        try:
            data_b64 = msg.get("data", "")
            chunk_data = base64.b64decode(data_b64)
            ctx["file_handle"].write(chunk_data)
            ctx["received"] += 1
            total = ctx["total_chunks"]
            received = ctx["received"]
            progress = int(received * 100 / total)
            if progress >= ctx["last_progress"] + 10:
                ctx["last_progress"] = progress
                size_mb = ctx.get("total_size", 0) / 1024 / 1024
                recv_mb = sum(1 for _ in [])  # 占位，实际用 received * chunk_size 估算
                # 用已接收块数估算
                # 注意：最后一块可能不足 chunk_size，所以这个值是估算
                self.event_bus.publish(Events.FILE_PROGRESS, {
                    "transfer_id": transfer_id,
                    "progress": progress,
                })
                self._sys_msg(f"📥 接收 {ctx['filename']}: {progress}%")

            if received >= total:
                ctx["file_handle"].close()
                # MD5 校验
                if ctx["md5"]:
                    md5 = hashlib.md5()
                    with open(ctx["target_path"], "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            md5.update(chunk)
                    if md5.hexdigest() == ctx["md5"]:
                        abs_path = os.path.abspath(ctx["target_path"])
                        self._sys_msg(f"✅ 文件已接收: {abs_path} (校验通过)")
                    else:
                        self._sys_msg(f"❌ 文件校验失败，已删除: {ctx['filename']}")
                        os.remove(ctx["target_path"])
                        self._recv_ctx.pop(key, None)
                        return
                else:
                    abs_path = os.path.abspath(ctx["target_path"])
                    self._sys_msg(f"✅ 文件已接收: {abs_path}")

                # 发送确认
                await self.control.send_to(ip, {
                    "type": MsgType.FILE_RECEIVED,
                    "transfer_id": transfer_id,
                })

                self.event_bus.publish(Events.FILE_FINISHED, {
                    "transfer_id": transfer_id,
                    "direction": "recv",
                    "filename": ctx["filename"],
                    "path": ctx["target_path"],
                })
                self._recv_ctx.pop(key, None)
        except Exception as e:
            self._sys_msg(f"接收文件块失败: {e}")
            self._cleanup_recv(key, delete_file=True)

    async def _handle_file_abort(self, ip: str, msg: Mapping[str, Any]):
        transfer_id = msg.get("transfer_id", "")
        key = f"{ip}:{transfer_id}"
        self._cleanup_recv(key, delete_file=True)
        self._sys_msg(f"对方取消了文件传输")

    def _cleanup_recv(self, key: str, delete_file: bool):
        ctx = self._recv_ctx.pop(key, None)
        if not ctx:
            return
        try:
            if ctx["file_handle"] and not ctx["file_handle"].closed:
                ctx["file_handle"].close()
            if delete_file and os.path.exists(ctx["target_path"]):
                os.remove(ctx["target_path"])
        except Exception:
            pass

    def _sys_msg(self, content: str):
        self.event_bus.publish(Events.SYSTEM_MESSAGE, {"content": content})