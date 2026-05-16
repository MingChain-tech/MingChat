"""
铭信 (MingChat) v0.3 - OP_RETURN 协议层

协议标识: 0x4D434800 ("MCH\0")
v0.2: 86B固定头 (兼容)
v0.3: 122B固定头 (+4B任务字段 +32B审计字段)

版本协商:
  - v0.3解析器读v0.2消息: 任务字段=0x00000000, 审计字段=全零
  - v0.2解析器读v0.3消息: version≠0x02, 应跳过(安全忽略)
"""
import struct
import hashlib
import time
from typing import Optional, Dict, Any, Union
from enum import IntEnum

from .models import MsgType, TaskOp, TaskFields, AuditFields, AuditFlags
from .models import Message as ModelMessage

# ── 协议常量 ──────────────────────────────────────────────

PROTOCOL_MAGIC = 0x4D434800  # "MCH\0"
HEADER_SIZE_V0_2 = 86        # 兼容 v0.2
HEADER_SIZE_V0_3 = 122       # v0.3: 86 + 4(任务) + 32(审计)
MAX_PAYLOAD_SIZE = 3850      # 约3.85KB (4KB - 122B - 交易开销)


# ── v0.3 协议编码/解码 ──────────────────────────────────

def serialize_message_v0_3(msg: ModelMessage) -> bytes:
    """v0.3序列化: 122B头 + 消息体"""
    header = bytearray(HEADER_SIZE_V0_3)
    
    struct.pack_into(">I", header, 0, PROTOCOL_MAGIC)        # 0-3: 协议标识
    header[4] = 0x03                                          # 4: 版本
    header[5] = msg.msg_type.value                            # 5: 类型
    header[6:26] = msg.sender_hash160.ljust(20, b'\x00')[:20]  # 6-25: 发送方
    header[26:46] = msg.receiver_hash160.ljust(20, b'\x00')[:20]  # 26-45: 接收方
    struct.pack_into(">Q", header, 46, msg.timestamp)          # 46-53: 时间戳
    
    # v0.3 新增字段
    task_bytes = msg.task.serialize()                          # 54-57: 任务字段
    header[54:58] = task_bytes
    audit_bytes = msg.audit.serialize()                        # 58-89: 审计字段
    header[58:90] = audit_bytes
    
    # 消息体哈希
    payload_hash = hashlib.sha256(msg.payload).digest() if msg.payload else b'\x00' * 32
    header[90:122] = payload_hash                              # 90-121: 哈希
    
    return bytes(header) + msg.payload


def deserialize_message_v0_3(data: bytes) -> Optional[ModelMessage]:
    """v0.3反序列化: 122B头 + 消息体"""
    if len(data) < HEADER_SIZE_V0_3:
        return None
    
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic != PROTOCOL_MAGIC:
        return None
    
    version = data[4]
    # v0.3解析器也接受v0.2消息（兼容模式）
    if version == 0x02:
        return _deserialize_v0_2_compat(data)
    if version != 0x03:
        return None  # 未知版本
    
    try:
        msg_type = MsgType(data[5])
    except ValueError:
        msg_type = MsgType.TEXT
    
    msg = ModelMessage(
        msg_type=msg_type,
        version=version,
        sender_hash160=data[6:26],
        receiver_hash160=data[26:46],
        timestamp=struct.unpack_from(">Q", data, 46)[0],
        task=TaskFields.deserialize(data[54:58]),
        audit=AuditFields.deserialize(data[58:90]),
        payload_hash=data[90:122],
        payload=data[HEADER_SIZE_V0_3:] if len(data) > HEADER_SIZE_V0_3 else b'',
    )
    return msg


def _deserialize_v0_2_compat(data: bytes) -> Optional[ModelMessage]:
    """将v0.2消息解析为v0.3 Message对象（任务/审计字段补零）"""
    version = data[4]
    try:
        msg_type = MsgType(data[5])
    except ValueError:
        msg_type = MsgType.TEXT

    return ModelMessage(
        msg_type=msg_type,
        version=version,
        sender_hash160=data[6:26],
        receiver_hash160=data[26:46],
        timestamp=struct.unpack_from(">Q", data, 46)[0],
        task=TaskFields.empty(),         # v0.2无任务字段
        audit=AuditFields.empty(),       # v0.2无审计字段
        payload_hash=data[54:86],
        payload=data[HEADER_SIZE_V0_2:] if len(data) > HEADER_SIZE_V0_2 else b'',
    )


def _deserialize_v0_1_compat(data: bytes) -> Optional[ModelMessage]:
    """将v0.1（Phase1）消息解析为v0.3 Message对象
    v0.1: 86B头 (magic 4B + ver 1B + type 1B + sender 20B + receiver 20B + ts 8B + msg_id 32B) + payload
    注意：v0.1 没有task/audit字段，payload_hash字段实际是msg_id
    """
    from .models import MsgType, TaskFields, AuditFields
    try:
        msg_type = MsgType(data[5])
    except ValueError:
        msg_type = MsgType.TEXT

    return ModelMessage(
        msg_type=msg_type,
        version=0x01,
        sender_hash160=data[6:26],
        receiver_hash160=data[26:46],
        timestamp=struct.unpack_from(">Q", data, 46)[0],
        task=TaskFields.empty(),
        audit=AuditFields.empty(),
        payload_hash=data[54:86],     # v0.1 msg_id被当作payload_hash存着
        payload=data[HEADER_SIZE_V0_2:] if len(data) > HEADER_SIZE_V0_2 else b'',
    )


# ── 快捷函数 ──────────────────────────────────────────────

def build_op_return_data(msg: ModelMessage) -> bytes:
    """构建OP_RETURN输出数据（自动选择v0.2/v0.3格式）"""
    if msg.version == 0x03:
        return serialize_message_v0_3(msg)
    # fallback: v0.2格式（旧版兼容）
    return serialize_message_v0_3(msg)


def parse_op_return_data(data: bytes) -> Optional[ModelMessage]:
    """解析OP_RETURN数据（自动识别v0.1/v0.2/v0.3）"""
    if len(data) < HEADER_SIZE_V0_2:
        return None
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic != PROTOCOL_MAGIC:
        return None
    version = data[4]
    if version == 0x03:
        if len(data) < HEADER_SIZE_V0_3:
            return None
        return deserialize_message_v0_3(data)
    elif version == 0x02:
        return _deserialize_v0_2_compat(data)
    elif version == 0x01:
        return _deserialize_v0_1_compat(data)
    return None


# ── 地址工具（兼容旧导入）───────────────────────────

def address_to_hash160(address: str) -> bytes:
    """BSV地址 → Hash160 (20字节)"""
    from .bsv_tools import b58decode
    try:
        decoded = b58decode(address)
        if len(decoded) >= 21:
            return decoded[1:21]
        return b'\x00' * 20
    except Exception:
        return b'\x00' * 20


def hash160_to_address(h160: bytes, network: str = "mainnet") -> str:
    """Hash160 → BSV地址"""
    from .bsv_tools import b58encode_check
    version = b'\x00' if network == "mainnet" else b'\x6f'
    return b58encode_check(version + h160)
