"""
加密与认证：
- 会话密钥派生（基于双方 nonce 排序后 hash）
- 控制消息加密（XOR 掩码 + HMAC-SHA256）
"""
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any, Mapping, Optional


class Crypto:
    def __init__(self, network_secret: str):
        self.network_secret = network_secret or ""

    def derive_session_key(self, nonce1: str, nonce2: str) -> bytes:
        if not self.network_secret:
            return b""
        ordered = ":".join(sorted([nonce1, nonce2]))
        return hashlib.sha256(
            f"{self.network_secret}:{ordered}".encode("utf-8")
        ).digest()

    def make_auth(self, username: str, nonce: str) -> str:
        if not self.network_secret:
            return ""
        return hmac.new(
            self.network_secret.encode("utf-8"),
            f"{username}:{nonce}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_auth(self, username: str, nonce: str, auth: str) -> bool:
        expected = self.make_auth(username, nonce)
        return hmac.compare_digest(expected, auth)

    def wrap(self, payload: Mapping[str, Any], key: bytes) -> dict:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = secrets.token_hex(8)
        masked = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        mac = hmac.new(key, masked + nonce.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "type": "secure",
            "nonce": nonce,
            "payload": base64.b64encode(masked).decode("ascii"),
            "mac": mac,
        }

    def unwrap(self, msg: Mapping[str, Any], key: bytes) -> Optional[dict]:
        payload_b64 = msg.get("payload")
        nonce = msg.get("nonce")
        mac = msg.get("mac")
        if not all(isinstance(x, str) for x in (payload_b64, nonce, mac)):
            return None
        try:
            masked = base64.b64decode(payload_b64.encode("ascii"))
        except Exception:
            return None
        expected = hmac.new(key, masked + nonce.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac):
            return None
        raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(masked))
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None