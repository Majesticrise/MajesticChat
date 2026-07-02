import os
import re
import subprocess
import sys
import time
import traceback

from chat_config import BASE_DIR, CLI_EXE, CORE_EXE, CONFIG_DIR, LISTEN_HOST, RELAY_PEERS, log_error


easytier_proc = None
NETWORK_NAME = ""
NETWORK_SECRET = ""


def check_files():
    missing = []
    if not os.path.exists(CORE_EXE):
        missing.append("easytier-core.exe")
    if not os.path.exists(CLI_EXE):
        missing.append("easytier-cli.exe")
    if missing:
        log_error(f"❌ 缺少必需文件: {', '.join(missing)}")
        log_error(f"请确保这些文件与 {os.path.basename(sys.executable)} 位于同一目录")
        return False
    return True


def start_easytier(network_name: str, network_secret: str, config_dir: str):
    global easytier_proc
    if not check_files():
        sys.exit(1)
    if not network_name or not network_secret:
        log_error("❌ 网络名或密码为空，请重新启动并输入")
        sys.exit(1)
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/f", "/im", "easytier-core.exe"],
                           capture_output=True, encoding='utf-8', errors='ignore', timeout=2)
        except Exception:
            pass
    os.makedirs(config_dir, exist_ok=True)
    cmd = [
        CORE_EXE,
        "--network-name", network_name,
        "--network-secret", network_secret,
        "--config-dir", config_dir,
        "--peers", RELAY_PEERS,
        "--use-smoltcp",
        "--dhcp"
    ]
    try:
        easytier_proc = subprocess.Popen(cmd, creationflags=0)
        print("✅ EasyTier 核心服务已启动，正在组网...")
        time.sleep(4)
        if easytier_proc.poll() is not None:
            log_error(f"❌ EasyTier 进程意外退出，返回码 {easytier_proc.returncode}")
            sys.exit(1)
        print("✅ EasyTier 进程运行中")
    except Exception as e:
        log_error(f"❌ 启动 EasyTier 失败: {e}\n{traceback.format_exc()}")
        sys.exit(1)


def stop_easytier():
    global easytier_proc
    if easytier_proc:
        try:
            easytier_proc.terminate()
            easytier_proc.wait(timeout=5)
        except Exception:
            pass
        print("🛑 EasyTier 已停止")


def run_cli(*args):
    cmd = [CLI_EXE] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='ignore', timeout=10)
        if result.stderr:
            log_error(f"easytier-cli stderr: {result.stderr}")
        return result.stdout
    except Exception as e:
        log_error(f"运行 easytier-cli 时出错: {e}")
        return ""


def get_self_ip() -> str | None:
    output = run_cli("peer")
    if not output:
        return None
    match = re.search(r'(\d+\.\d+\.\d+\.\d+)\s*\(self\)', output)
    if match:
        return match.group(1)
    lines = output.splitlines()
    header_line = None
    for line in lines:
        if 'ipv4' in line and 'hostname' in line:
            header_line = line
            break
    if header_line:
        parts = header_line.split('|')
        col_idx = None
        for i, p in enumerate(parts):
            if 'ipv4' in p:
                col_idx = i
                break
        if col_idx is not None:
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


def is_valid_virtual_ip(ip: str) -> bool:
    """只保留 EasyTier 虚拟网段（10.0.0.0/8 或 100.64.0.0/10）"""
    return ip.startswith('10.') or ip.startswith('100.64.')


def get_peer_ips() -> set[str]:
    output = run_cli("peer")
    ips = set()
    for line in output.splitlines():
        if '(self)' in line:
            continue
        m = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
        if m:
            ip = m.group(1)
            if is_valid_virtual_ip(ip):
                ips.add(ip)
    return ips
