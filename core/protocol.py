"""
控制通道消息协议定义。
所有消息为 dict，序列化为 JSON + '\n' 分隔。
"""


class MsgType:
    # 握手与心跳
    HANDSHAKE = "handshake"
    PING = "ping"
    PONG = "pong"

    # 文字
    CHAT = "chat"
    PRIVATE = "private"
    SYSTEM = "system"

    # 文件
    FILE_START = "file_start"
    FILE_CHUNK = "file_chunk"
    FILE_ABORT = "file_abort"
    FILE_RECEIVED = "file_received"

    # 历史同步
    SYNC_REQ = "sync_req"
    SYNC_MSG = "sync_msg"
    SYNC_END = "sync_end"

    # 游戏房间
    GAME_HOST = "game_host"
    GAME_JOIN = "game_join"
    GAME_LEAVE = "game_leave"
    GAME_STOP = "game_stop"
    GAME_LIST = "game_list"       # 新节点加入时，主机主动推送当前房间列表
    GAME_STATE = "game_state"     # 房间状态变化（有人加入/离开）

    # 语音会话控制（只走控制通道通知谁加入了语音，音频走 UDP）
    VOICE_JOIN = "voice_join"
    VOICE_LEAVE = "voice_leave"


def make_handshake(username: str, nonce: str, auth: str) -> dict:
    return {
        "type": MsgType.HANDSHAKE,
        "username": username,
        "nonce": nonce,
        "auth": auth,
    }


def make_chat(sender: str, content: str, time: float, msg_id: str) -> dict:
    return {
        "type": MsgType.CHAT,
        "sender": sender,
        "content": content,
        "time": time,
        "msg_id": msg_id,
    }


def make_private(sender: str, content: str, time: float, msg_id: str) -> dict:
    return {
        "type": MsgType.PRIVATE,
        "sender": sender,
        "content": content,
        "time": time,
        "msg_id": msg_id,
    }


def make_ping() -> dict:
    return {"type": MsgType.PING}


def make_pong() -> dict:
    return {"type": MsgType.PONG}