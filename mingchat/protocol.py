# -*- coding: utf-8 -*-
"""
MingChat v0.3 - OP_RETURN 86B Protocol
Protocol ID: 0x4D494E43 = "MINC"
Fixed header: 86 bytes
"""

import struct
import hashlib
import time
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass, field
from enum import IntEnum

PROTOCOL_MAGIC = 0x4D494E43
PROTOCOL_VERSION = 0x03
HEADER_SIZE = 86


class MsgType(IntEnum):
    CHAT = 0x01
    RPC_REQ = 0x02
    RPC_RESP = 0x03
    ACK = 0x04
    BROADCAST = 0x05
    PUBLISH = 0x10
    BID = 0x11
    ASSIGN = 0x12
    PROGRESS = 0x13
    DELIVER = 0x14
    ACCEPT = 0x15
    REJECT = 0x16
    ARBITRATE = 0x17
    SETTLE = 0x18
    CANCEL = 0x19
    DID_REGISTER = 0x20
    DID_UPDATE = 0x21
    DID_REVOKE = 0x22

    @classmethod
    def from_str(cls, name: str) -> "MsgType":
        mapping = {
            "CHAT": cls.CHAT, "RPC_REQ": cls.RPC_REQ, "RPC_RESP": cls.RPC_RESP,
            "ACK": cls.ACK, "BROADCAST": cls.BROADCAST, "PUBLISH": cls.PUBLISH,
            "BID": cls.BID, "ASSIGN": cls.ASSIGN, "PROGRESS": cls.PROGRESS,
            "DELIVER": cls.DELIVER, "ACCEPT": cls.ACCEPT, "REJECT": cls.REJECT,
            "ARBITRATE": cls.ARBITRATE, "SETTLE": cls.SETTLE, "CANCEL": cls.CANCEL,
            "DID_REGISTER": cls.DID_REGISTER, "DID_UPDATE": cls.DID_UPDATE, "DID_REVOKE": cls.DID_REVOKE,
        }
        return mapping.get(name.upper(), cls.CHAT)

    def to_str(self) -> str:
        mapping = {
            self.CHAT: "CHAT", self.RPC_REQ: "RPC_REQ", self.RPC_RESP: "RPC_RESP",
            self.ACK: "ACK", self.BROADCAST: "BROADCAST", self.PUBLISH: "PUBLISH",
            self.BID: "BID", self.ASSIGN: "ASSIGN", self.PROGRESS: "PROGRESS",
            self.DELIVER: "DELIVER", self.ACCEPT: "ACCEPT", self.REJECT: "REJECT",
            self.ARBITRATE: "ARBITRATE", self.SETTLE: "SETTLE", self.CANCEL: "CANCEL",
            self.DID_REGISTER: "DID_REGISTER", self.DID_UPDATE: "DID_UPDATE", self.DID_REVOKE: "DID_REVOKE",
        }
        return mapping.get(self, "UNKNOWN")


@dataclass
class Message:
    msg_type: MsgType = MsgType.CHAT
    version: int = PROTOCOL_VERSION
    sender_hash160: bytes = field(default_factory=lambda: b"\x00" * 20)
    receiver_hash160: bytes = field(default_factory=lambda: b"\x00" * 20)
    timestamp: int = 0
    body_hash: bytes = field(default_factory=lambda: b"\x00" * 32)
    body: bytes = b""
    txid: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = int(time.time())
        if not self.body_hash:
            self.body_hash = compute_body_hash(self.body)

    def serialize_header(self) -> bytes:
        header = bytearray(HEADER_SIZE)
        struct.pack_into(">I", header, 0, PROTOCOL_MAGIC)
        header[4] = self.version
        header[5] = self.msg_type.value
        header[6:26] = self.sender_hash160[:20]
        header[26:46] = self.receiver_hash160[:20]
        struct.pack_into(">Q", header, 46, self.timestamp)
        if not self.body_hash:
            self.body_hash = compute_body_hash(self.body)
        header[54:86] = self.body_hash[:32]
        return bytes(header)

    def serialize(self) -> bytes:
        header = self.serialize_header()
        if self.body:
            body_bytes = self.body if isinstance(self.body, bytes) else self.body.encode('utf-8')
        else:
            body_bytes = b""
        return header + body_bytes

    def to_op_return_hex(self) -> str:
        return self.serialize().hex()

    @classmethod
    def deserialize(cls, data: bytes) -> Optional["Message"]:
        if len(data) < HEADER_SIZE:
            return None
        magic = struct.unpack_from(">I", data, 0)[0]
        if magic != PROTOCOL_MAGIC:
            return None
        version = data[4]
        msg_type_val = data[5]
        sender_h160 = data[6:26]
        receiver_h160 = data[26:46]
        timestamp = struct.unpack_from(">Q", data, 46)[0]
        body_hash = data[54:86]
        body = data[HEADER_SIZE:] if len(data) > HEADER_SIZE else b""
        try:
            msg_type = MsgType(msg_type_val)
        except ValueError:
            msg_type = MsgType.CHAT
        return cls(
            msg_type=msg_type, version=version, sender_hash160=sender_h160,
            receiver_hash160=receiver_h160, timestamp=timestamp,
            body_hash=body_hash, body=body,
        )

    def get_body_text(self) -> str:
        try:
            return self.body.decode("utf-8") if self.body else ""
        except UnicodeDecodeError:
            return self.body.hex()

    def to_dict(self) -> Dict:
        return {
            "msg_type": self.msg_type.to_str(), "msg_type_val": self.msg_type.value,
            "version": self.version,
            "sender_hash160": self.sender_hash160.hex(),
            "receiver_hash160": self.receiver_hash160.hex(),
            "timestamp": self.timestamp,
            "timestamp_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "body_hash": self.body_hash.hex() if self.body_hash else "",
            "body": self.get_body_text(),
            "body_hex": self.body.hex() if self.body else "",
            "txid": self.txid,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Message":
        msg_type = MsgType.from_str(d.get("msg_type", "CHAT"))
        sender = d.get("sender_hash160", "")
        if isinstance(sender, str):
            sender = bytes.fromhex(sender) if sender else b"\x00" * 20
        receiver = d.get("receiver_hash160", "")
        if isinstance(receiver, str):
            receiver = bytes.fromhex(receiver) if receiver else b"\x00" * 20
        body = d.get("body", d.get("content", ""))
        if isinstance(body, str):
            body = body.encode("utf-8")
        return cls(
            msg_type=msg_type, sender_hash160=sender, receiver_hash160=receiver,
            timestamp=d.get("timestamp", int(time.time())), body=body,
            txid=d.get("txid", ""),
        )


def compute_body_hash(body_bytes: bytes) -> bytes:
    if isinstance(body_bytes, str):
        body_bytes = body_bytes.encode('utf-8')
    return hashlib.sha256(body_bytes).digest()


def encode_header(msg_type: Union[MsgType, int], sender_hash160: bytes,
                   receiver_hash160: bytes, timestamp: int, body_hash: bytes) -> bytes:
    header = bytearray(HEADER_SIZE)
    struct.pack_into(">I", header, 0, PROTOCOL_MAGIC)
    header[4] = PROTOCOL_VERSION
    header[5] = int(msg_type) if isinstance(msg_type, MsgType) else msg_type
    header[6:26] = sender_hash160[:20]
    header[26:46] = receiver_hash160[:20]
    struct.pack_into(">Q", header, 46, timestamp)
    header[54:86] = body_hash[:32]
    return bytes(header)


def decode_header(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) < HEADER_SIZE:
        return None
    magic = struct.unpack_from(">I", raw, 0)[0]
    if magic != PROTOCOL_MAGIC:
        return None
    return {
        "magic": magic, "version": raw[4], "msg_type": MsgType(raw[5]),
        "sender_hash160": raw[6:26], "receiver_hash160": raw[26:46],
        "timestamp": struct.unpack_from(">Q", raw, 46)[0], "body_hash": raw[54:86],
    }


def build_op_return(msg_type: Union[MsgType, int], sender_hash160: bytes,
                    receiver_hash160: bytes, body_bytes: bytes,
                    timestamp: Optional[int] = None) -> str:
    if timestamp is None:
        timestamp = int(time.time())
    body_hash = compute_body_hash(body_bytes)
    header = encode_header(msg_type, sender_hash160, receiver_hash160, timestamp, body_hash)
    if isinstance(body_bytes, str):
        body_bytes = body_bytes.encode('utf-8')
    return (header + body_bytes).hex()


def parse_op_return(data: Union[bytes, str]) -> Optional[Message]:
    if isinstance(data, str):
        data = bytes.fromhex(data)
    return Message.deserialize(data)


def address_to_hash160(address: str) -> bytes:
    from .bsv_tools import b58decode
    try:
        decoded = b58decode(address)
        if len(decoded) >= 21:
            return decoded[1:21]
        return b"\x00" * 20
    except Exception:
        return b"\x00" * 20


def hash160_to_address(h160: bytes, network: str = "mainnet") -> str:
    from .bsv_tools import b58encode_check
    version = b'\x00' if network == "mainnet" else b'\x6f'
    return b58encode_check(version + h160)
