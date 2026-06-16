"""
P2P Chat Message Protocol — 对标 bsv-poker 的 OnChainChat + ChatService
= 加密消息结构 + 序列化 + 链上存证回退
"""
import json
import time
import uuid
import base64
from dataclasses import dataclass, field
from typing import Optional
from cryptography.hazmat.primitives.asymmetric import ec

from crypto import (
    encrypt_for_recipient, decrypt_from_sender,
    deserialize_public_key, serialize_public_key
)

# ─── Message Types ────────────────────────────────────────────

class MsgType:
    TEXT        = "text"         # 文本消息
    IMAGE       = "image"        # 图片
    FILE        = "file"         # 文件
    SYSTEM      = "system"       # 系统消息
    READ_RECEIPT = "read_receipt"
    TYPING      = "typing"

# ─── Message ─────────────────────────────────────────────────

@dataclass
class Message:
    """
    对标 bsv-poker 的聊天消息结构
    """
    msg_id: str
    msg_type: str
    from_handle: str
    to_handle: str
    content: str                        # 明文内容
    timestamp: float
    reply_to: str = ""                  # 回复的消息 ID

    # 加密字段（传输时填充）
    ephemeral_pk: str = ""              # hex
    ciphertext: str = ""                # base64

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "type": self.msg_type,
            "from": self.from_handle,
            "to": self.to_handle,
            "content": self.content,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
            "epk": self.ephemeral_pk,
            "ct": self.ciphertext
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            msg_id=d.get("msg_id", ""),
            msg_type=d.get("type", "text"),
            from_handle=d.get("from", ""),
            to_handle=d.get("to", ""),
            content=d.get("content", ""),
            timestamp=d.get("timestamp", time.time()),
            reply_to=d.get("reply_to", ""),
            ephemeral_pk=d.get("epk", ""),
            ciphertext=d.get("ct", "")
        )

    @classmethod
    def create(cls, from_handle: str, to_handle: str,
               content: str, msg_type: str = "text",
               reply_to: str = "") -> "Message":
        return cls(
            msg_id=str(uuid.uuid4())[:12],
            msg_type=msg_type,
            from_handle=from_handle,
            to_handle=to_handle,
            content=content,
            timestamp=time.time(),
            reply_to=reply_to
        )

    # ─── Encryption ────────────────────────────────────────

    def encrypt(self, my_sk, their_pk: ec.EllipticCurvePublicKey):
        """
        对标 bsv-poker ChatService: ECDH → HKDF → AES-256-GCM
        每次加密生成新的临时密钥对
        """
        plaintext = json.dumps(self.to_dict()).encode('utf-8')
        epk_bytes, encrypted = encrypt_for_recipient(plaintext, my_sk, their_pk)
        self.ephemeral_pk = epk_bytes.hex()
        self.ciphertext = base64.b64encode(encrypted).decode('ascii')

    def decrypt(self, my_sk) -> dict:
        """
        解密消息，返回消息字典
        """
        epk_bytes = bytes.fromhex(self.ephemeral_pk)
        encrypted = base64.b64decode(self.ciphertext)
        plaintext = decrypt_from_sender(epk_bytes, encrypted, my_sk)
        return json.loads(plaintext.decode('utf-8'))

    def encrypt_for_p2p(self, my_sk, their_pk: ec.EllipticCurvePublicKey) -> dict:
        """
        为 P2P 传输生成加密负载
        返回可直接放入 DIRECT 帧的 dict
        """
        self.encrypt(my_sk, their_pk)
        return {
            "epk": self.ephemeral_pk,
            "ct": self.ciphertext,
            "to": self.to_handle,
            "msg_type": self.msg_type,
            "ts": self.timestamp
        }

    def __str__(self):
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"[{self.msg_type}] @{self.from_handle} → @{self.to_handle}: {preview}"


# ─── On-Chain Fallback Data ──────────────────────────────────

@dataclass
class OnChainEnvelope:
    """
    对标 bsv-poker 的 OnChainChat 链上消息
    当对方离线时，消息写入 BSV 链上 OP_RETURN
    """
    version: int = 1
    msg_type: str = "chat"
    recipient_pk: str = ""      # hex 压缩公钥
    ephemeral_pk: str = ""      # hex
    ciphertext: str = ""        # base64
    timestamp: int = 0
    txid: str = ""              # 存证交易 ID（回填）

    def encode(self) -> bytes:
        """编码为 OP_RETURN 数据"""
        data = json.dumps({
            "v": self.version,
            "t": self.msg_type,
            "rp": self.recipient_pk,
            "ep": self.ephemeral_pk,
            "ct": self.ciphertext,
            "ts": self.timestamp
        }, separators=(',', ':')).encode('utf-8')
        return data

    @classmethod
    def decode(cls, data: bytes) -> "OnChainEnvelope":
        d = json.loads(data.decode('utf-8'))
        return cls(
            version=d["v"],
            msg_type=d["t"],
            recipient_pk=d["rp"],
            ephemeral_pk=d["ep"],
            ciphertext=d["ct"],
            timestamp=d["ts"]
        )

    @classmethod
    def from_message(cls, msg: Message, recipient_pk_hex: str) -> "OnChainEnvelope":
        return cls(
            version=1,
            msg_type="chat",
            recipient_pk=recipient_pk_hex,
            ephemeral_pk=msg.ephemeral_pk,
            ciphertext=msg.ciphertext,
            timestamp=int(msg.timestamp)
        )


# ─── Message Store ───────────────────────────────────────────

class MessageStore:
    """本地消息存储"""
    def __init__(self):
        self.messages: list[Message] = []

    def add(self, msg: Message):
        self.messages.append(msg)

    def get_for(self, handle: str, limit: int = 50) -> list[Message]:
        """获取与某人的对话"""
        return [m for m in self.messages
                if m.from_handle == handle or m.to_handle == handle][-limit:]

    def get_by_id(self, msg_id: str) -> Optional[Message]:
        for m in self.messages:
            if m.msg_id == msg_id:
                return m
        return None

    def all(self) -> list[Message]:
        return list(self.messages)

    def save(self, path: str):
        with open(path, 'w') as f:
            json.dump([m.to_dict() for m in self.messages], f, indent=2)

    @classmethod
    def load(cls, path: str) -> "MessageStore":
        store = cls()
        try:
            with open(path) as f:
                data = json.load(f)
                store.messages = [Message.from_dict(d) for d in data]
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return store
