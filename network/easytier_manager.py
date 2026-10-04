"""
EasyTier 组网管理器
职责：启动/停止 EasyTier，获取本机虚拟 IP，获取对等节点 IP 列表。
"""
import os
import re
import subprocess
import sys
import time

from core.config import BASE_DIR, CLI_EXE, CORE_EXE, CONFIG_DIR, RELAY_PEERS


class EasyTierManager:
    def __init__(self):
        self.proc = None

    def check_files(self) -> bool:
        missing = []
        if not os.path.exists(CORE_EXE):
            missing.append("easytier-core.exe")
        if not os.path.exists(CLI_EXE):
            missing.append("easytier-cli.exe")
        if missing:
            print(f"[EasyTier] 缺少必需文件: {', '.join(missing)}")
            print(f"[EasyTier] 请将 easytier-core.exe 和 easytier-cli.exe 放到: {BASE_DIR}")
            return False
        return True

    def start(self, network_name: str, network_secret: str) -> bool:
        if not self.check_files():
            return False
        if self.proc and self.proc.poll() is None:
            print("[EasyTier] 已在运行")
            return True

        # 清理可能残留的 EasyTier 进程
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/f", "/im", "easytier-core.exe"],
                    capture_output=True, encoding='utf-8',
                    errors='ignore', timeout=2
                )
            except Exception:
                pass

        os.makedirs(CONFIG_DIR, exist_ok=True)

        cmd = [
            CORE_EXE,
            "--network-name", network_name,
            "--network-secret", network_secret,
            "--config-dir", CONFIG_DIR,
            "--peers", RELAY_PEERS,
            "--use-smoltcp",     # 用户态网络栈，无需 TUN 驱动
            "--dhcp",
        ]

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            print("[EasyTier] 核心服务已启动，等待组网...")
            time.sleep(4)

            if self.proc.poll() is not None:
                print(f"[EasyTier] 进程意外退出，返回码 {self.proc.returncode}")
                self.proc = None
                return False

            print("[EasyTier] 运行中")
            return True
        except Exception as e:
            print(f"[EasyTier] 启动失败: {e}")
            self.proc = None
            return False

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
            print("[EasyTier] 已停止")

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ========== CLI 交互 ==========
    def _run_cli(self, *args) -> str:
        try:
            result = subprocess.run(
                [CLI_EXE] + list(args),
                capture_output=True, text=True,
                encoding='utf-8', errors='ignore', timeout=10
            )
            return result.stdout
        except Exception as e:
            print(f"[EasyTier] CLI 调用失败: {e}")
            return ""

    def get_self_ip(self) -> str | None:
        """解析 easytier-cli peer 输出，返回本机虚拟 IP"""
        output = self._run_cli("peer")
        if not output:
            return None

        # 旧格式：(self) 标记
        match = re.search(r'(\d+\.\d+\.\d+\.\d+)\s*\(self\)', output)
        if match:
            return match.group(1)

        # 新表格格式
        lines = output.splitlines()
        header_line = None
        for line in lines:
            if 'ipv4' in line and 'hostname' in line:
                header_line = line
                break
        if not header_line:
            return None

        parts = header_line.split('|')
        col_idx = None
        for i, p in enumerate(parts):
            if 'ipv4' in p:
                col_idx = i
                break
        if col_idx is None:
            return None

        for line in lines:
            if 'Local' in line or 'LAPTOP' in line:
                cols = line.split('|')
                if len(cols) > col_idx:
                    val = cols[col_idx].strip()
                    if val and val != '-':
                        if '/' in val:
                            val = val.split('/')[0]
                        return val
        return None

    def get_peer_ips(self) -> set[str]:
        """获取所有对等节点虚拟 IP（过滤 10.x 和 100.64.x 网段）"""
        output = self._run_cli("peer")
        ips = set()
        for line in output.splitlines():
            if '(self)' in line:
                continue
            m = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
            if m:
                ip = m.group(1)
                if self._is_valid_virtual_ip(ip):
                    ips.add(ip)
        return ips

    @staticmethod
    def _is_valid_virtual_ip(ip: str) -> bool:
        return ip.startswith('10.') or ip.startswith('100.64.')