"""
MingChat v0.3.0 — BSV区块链上的Agent间通讯协议
让AI Agent通过OP_RETURN互发消息，无需中心化服务器

域名: mingchain.tech
"""
from .client import MingChat
from .models import Message, MsgType
from .protocol import (
    parse_op_return_data,
    address_to_hash160, hash160_to_address,
    serialize_message_v0_3, deserialize_message_v0_3,
    HEADER_SIZE_V0_2, HEADER_SIZE_V0_3, PROTOCOL_MAGIC,
)
from .models import (
    TaskFields, AuditFields, AuditFlags, TaskOp, TaskStatus,
    TaskPublishPayload, TaskBidPayload, TaskDeliverPayload,
    TaskSettlePayload, TaskDisputePayload,
    DIDDocument, make_task_id,
)
from .bsv_tools import (
    privkey_to_wif, wif_to_privkey,
    privkey_to_address, pubkey_to_address,
    address_to_hash160, hash160_to_address,
    sign_message, verify_signature,
    hash160, sha256, hash256,
    build_op_return_script, build_p2pkh_script,
    generate_privkey,
)

__version__ = "0.3.0"
__author__ = "MingChain Tech"

__all__ = [
    # 主类
    "MingChat",
    # 模型
    "Message", "MsgType",
    "TaskFields", "AuditFields", "AuditFlags", "TaskOp", "TaskStatus",
    "TaskPublishPayload", "TaskBidPayload", "TaskDeliverPayload",
    "TaskSettlePayload", "TaskDisputePayload",
    "DIDDocument", "make_task_id",
    # 协议
    "parse_op_return_data",
    "serialize_message_v0_3", "deserialize_message_v0_3",
    "address_to_hash160", "hash160_to_address",
    "HEADER_SIZE_V0_2", "HEADER_SIZE_V0_3", "PROTOCOL_MAGIC",
    # 工具
    "privkey_to_wif", "wif_to_privkey",
    "privkey_to_address", "pubkey_to_address",
    "sign_message", "verify_signature",
    "hash160", "sha256", "hash256",
    "build_op_return_script", "build_p2pkh_script",
    "generate_privkey",
]
