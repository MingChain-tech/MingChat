"""
铭信 (MingChat) v0.3 - 数据模型
Message / Task / DIDDocument / AuditFields 等 dataclass
"""
from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional, List
from enum import IntEnum


# ── 消息类型 ─────────────────────────────────────────────

class MsgType(IntEnum):
    """消息类型 (v0.3扩展)"""
    TEXT = 0x01
    RPC_REQUEST = 0x02
    RPC_RESPONSE = 0x03
    NOTIFICATION = 0x04
    KEY_EXCHANGE = 0x05
    HEARTBEAT = 0x06
    HELLO = 0x07           # v0.3: 版本协商+能力声明
    TASK_PUBLISH = 0x10    # v0.3: 发布任务
    TASK_BID = 0x11        # v0.3: 竞标/接单
    TASK_DELIVER = 0x12    # v0.3: 交付结果
    TASK_SETTLE = 0x13     # v0.3: 结算确认
    TASK_DISPUTE = 0x14    # v0.3: 争议仲裁
    DID_REGISTER = 0x20    # v0.3: 铭识DID注册
    DID_UPDATE = 0x21      # v0.3: 铭识DID更新
    DID_REVOKE = 0x22      # v0.3: 铭识DID吊销
    # ── 信誉系统 (v0.3.2) ──
    REPUTATION_SCORE = 0x30  # 评分
    REPUTATION_REVIEW = 0x31 # 评语
    REPUTATION_BOND = 0x32   # 质押
    ERROR = 0xFF

    @classmethod
    def from_str(cls, name: str) -> MsgType:
        mapping = {k: v for k, v in cls.__members__.items()}
        return mapping.get(name.upper(), cls.TEXT)

    def to_str(self) -> str:
        for k, v in type(self).__members__.items():
            if v == self:
                return k
        return "UNKNOWN"


# ── 任务操作码 ──────────────────────────────────────────

class TaskOp(IntEnum):
    """任务操作码 (协议头层)"""
    NONE = 0x00           # 无任务（纯通讯）
    PUBLISH = 0x10        # 发布任务
    BID = 0x11            # 竞标/接单
    ASSIGN = 0x12         # 指派确认
    PROGRESS = 0x13       # 进度更新
    DELIVER = 0x14        # 交付结果
    ACCEPT = 0x15         # 验收通过
    REJECT = 0x16         # 验收拒绝
    ARBITRATE = 0x17      # 仲裁请求
    SETTLE = 0x18         # 结算完成
    CANCEL = 0x19         # 取消任务

    @classmethod
    def from_int(cls, val: int) -> TaskOp:
        try:
            return cls(val)
        except ValueError:
            return cls.NONE


# ── 任务状态 ─────────────────────────────────────────────

class TaskStatus(IntEnum):
    PUBLISHED = 1
    BIDDING = 2
    ASSIGNED = 3
    MATCHED = 4
    EXECUTING = 5
    DELIVERED = 6
    ACCEPTED = 7
    REJECTED = 8
    DISPUTED = 9
    RESOLVED = 10
    ESCALATED = 11
    SETTLED = 12
    CANCELLED = 13


# ── 审计字段标志位 ─────────────────────────────────────

class AuditFlags:
    ENCRYPTED = 0x01       # bit 0: ECDH+AES-256-GCM加密
    HAS_DID = 0x02         # bit 1: 含铭识DID引用
    HAS_CAPABILITIES = 0x04  # bit 2: 含能力标签
    NEEDS_APPROVAL = 0x08  # bit 3: 需要人工审批


# ── 核心数据模型 ─────────────────────────────────────────

@dataclass
class AuditFields:
    """审计字段 (32B) — v0.3新增"""
    scope_hash: bytes = b'\x00' * 16     # 16B: 对话授权哈希
    escrow_ref: bytes = b'\x00' * 8      # 8B: 托管交易引用
    flags: int = 0                        # 4B: 位标志
    reserved: bytes = b'\x00' * 4         # 4B: 保留

    def serialize(self) -> bytes:
        return self.scope_hash + self.escrow_ref + self.flags.to_bytes(4, 'big') + self.reserved

    @classmethod
    def deserialize(cls, data: bytes) -> AuditFields:
        return cls(
            scope_hash=data[0:16],
            escrow_ref=data[16:24],
            flags=int.from_bytes(data[24:28], 'big'),
            reserved=data[28:32],
        )

    @classmethod
    def empty(cls) -> AuditFields:
        return cls()

    def is_empty(self) -> bool:
        return self.flags == 0 and self.scope_hash == b'\x00' * 16 and self.escrow_ref == b'\x00' * 8


@dataclass
class TaskFields:
    """任务字段 (4B) — v0.3新增"""
    task_op: int = 0x00       # 1B: 任务操作码
    task_id_lo: bytes = b'\x00\x00\x00'  # 3B: 任务ID低3字节

    def serialize(self) -> bytes:
        return self.task_op.to_bytes(1, 'big') + self.task_id_lo

    @classmethod
    def deserialize(cls, data: bytes) -> TaskFields:
        return cls(task_op=data[0], task_id_lo=data[1:4])

    @classmethod
    def empty(cls) -> TaskFields:
        return cls()

    def is_empty(self) -> bool:
        return self.task_op == 0x00 and self.task_id_lo == b'\x00\x00\x00'


@dataclass
class Message:
    """铭信消息体 (v0.3)"""
    msg_type: MsgType = MsgType.TEXT
    version: int = 0x03
    sender_hash160: bytes = b'' * 20      # 20B
    receiver_hash160: bytes = b'' * 20    # 20B
    timestamp: int = 0                    # uint64 unix毫秒
    task: TaskFields = field(default_factory=TaskFields)        # v0.3: 4B
    audit: AuditFields = field(default_factory=AuditFields)     # v0.3: 32B
    payload_hash: bytes = b'' * 32         # 32B SHA256
    payload: bytes = b''                   # 变长消息体
    txid: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = int(time.time() * 1000)

    def serialize_v0_3(self) -> bytes:
        """v0.3完整序列化: 122B头 + 消息体"""
        from .protocol import PROTOCOL_MAGIC, HEADER_SIZE_V0_3
        
        header = bytearray(HEADER_SIZE_V0_3)
        pos = 0
        
        # 协议标识 (4B)
        struct.pack_into('>I', header, pos, PROTOCOL_MAGIC); pos += 4
        # 版本 (1B)
        header[pos] = self.version; pos += 1
        # 类型 (1B)
        header[pos] = self.msg_type.value; pos += 1
        # 发送方 (20B)
        sender = self.sender_hash160.ljust(20, b'\x00')[:20]
        header[pos:pos+20] = sender; pos += 20
        # 接收方 (20B)
        receiver = self.receiver_hash160.ljust(20, b'\x00')[:20]
        header[pos:pos+20] = receiver; pos += 20
        # 时间戳 (8B)
        struct.pack_into('>Q', header, pos, self.timestamp); pos += 8
        # 任务字段 (4B) — v0.3
        task_bytes = self.task.serialize()
        header[pos:pos+4] = task_bytes; pos += 4
        # 审计字段 (32B) — v0.3
        audit_bytes = self.audit.serialize()
        header[pos:pos+32] = audit_bytes; pos += 32
        # 消息体哈希 (32B)
        payload_hash = hashlib.sha256(self.payload).digest() if self.payload else b'\x00' * 32
        header[pos:pos+32] = payload_hash; pos += 32

        return bytes(header) + self.payload

    def get_payload_text(self) -> str:
        try:
            return self.payload.decode('utf-8')
        except UnicodeDecodeError:
            return self.payload.hex()

    def to_dict(self) -> dict:
        return {
            "msg_type": self.msg_type.to_str(),
            "version": self.version,
            "sender_hash160": self.sender_hash160.hex(),
            "receiver_hash160": self.receiver_hash160.hex(),
            "timestamp": self.timestamp,
            "task_op": self.task.task_op,
            "task_id_lo": self.task.task_id_lo.hex(),
            "audit_flags": self.audit.flags,
            "scope_hash": self.audit.scope_hash.hex(),
            "escrow_ref": self.audit.escrow_ref.hex(),
            "payload_hash": self.payload_hash.hex() if self.payload_hash else "",
            "payload": self.get_payload_text(),
            "txid": self.txid,
        }


# ── MingTask 任务数据模型 ────────────────────────────────

@dataclass
class TaskPublishPayload:
    """任务发布消息体"""
    task_id: str                          # sender_hash160+task_id_lo
    task_type: str = "analysis"           # analysis|search|coding|translation|creative|custom
    title: str = ""
    description_hash: str = ""            # SHA256 of detailed description
    reward_sats: int = 0
    deadline: int = 0                     # unix timestamp
    escrow_txid: str = ""                 # 托管交易ID
    capabilities: List[str] = field(default_factory=list)
    acceptance_mode: str = "auto"         # auto|manual|hybrid
    acceptance_criteria: str = ""
    assign_mode: str = "bid"              # bid|assign|match


@dataclass
class TaskBidPayload:
    """竞标消息体"""
    task_id: str
    bid_sats: int = 0
    estimated_time: int = 0               # seconds
    capabilities_proof: List[str] = field(default_factory=list)
    did: str = ""


@dataclass
class TaskDeliverPayload:
    """交付结果消息体"""
    task_id: str
    result_hash: str = ""
    result_ref: str = ""
    proof_hash: str = ""
    summary: str = ""


@dataclass
class TaskSettlePayload:
    """结算确认消息体"""
    task_id: str
    verdict: str = "accepted"             # accepted|rejected|partial
    settlement_txid: str = ""
    amount_sats: int = 0
    arbiter: Optional[str] = None


@dataclass
class TaskDisputePayload:
    """争议仲裁消息体"""
    task_id: str
    dispute_type: str = "quality"         # quality|timeout|scope_violation|fraud
    evidence_hashes: List[str] = field(default_factory=list)
    claim: str = ""
    proposed_resolution: str = "refund"   # refund|partial_pay|rework|escalate


# ── 铭识DID 数据模型 ─────────────────────────────────────

@dataclass
class DIDDocument:
    """铭识DID文档"""
    did: str                              # did:bsv:{hash160(controller_pk)[:40]}
    controller_pk: str = ""               # 控制者压缩公钥 hex
    auth_pk: str = ""                     # 操作公钥 hex
    service_endpoint: str = ""
    capabilities_hash: str = ""
    profile_name: str = ""
    profile_description: str = ""
    profile_version: str = ""
    controller_sig: str = ""              # 控制者签名
    # ── 身份等级 (v0.3.2) ──
    identity_level: int = 0               # 0=匿名 1=邮箱 2=企业 3=个人KYC 4=政府
    kyc_hash: str = ""                    # sha256(KYC机构签名+实名信息)，可选
    kyc_provider: str = ""                # KYC机构DID或URL
    license_ref: str = ""                 # 牌照/许可证引用
    # 链上元数据
    registration_txid: str = ""
    update_seq: int = 1
    status: str = "active"                # active|updated|revoked


# ── 工具函数 ─────────────────────────────────────────────

def make_task_id(sender_hash160: bytes, task_id_lo: bytes) -> str:
    """构造完整任务ID"""
    return sender_hash160.hex() + task_id_lo.hex()
