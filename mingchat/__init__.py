# -*- coding: utf-8 -*-
"""
铭信 (MingChat) v0.3 — BSV区块链上的Agent间通讯协议
让AI Agent通过OP_RETURN互发消息，无需中心化服务器

域名: mingchain.tech
"""

__version__ = "0.3.0"
__author__ = "MingChain Tech"

from .client import MingChat
from .protocol import (
    Message, MsgType, 
    build_op_return, parse_op_return,
    address_to_hash160, hash160_to_address,
    compute_body_hash, encode_header, decode_header,
    HEADER_SIZE, PROTOCOL_MAGIC, PROTOCOL_VERSION
)
from .bsv_tools import (
    privkey_to_wif, wif_to_privkey, 
    privkey_to_address, pubkey_to_address,
    address_to_hash160, hash160_to_address,
    sign_message, verify_signature,
    hash160, sha256, hash256,
    build_op_return_script, build_p2pkh_script
)

__all__ = [
    # 主类
    "MingChat",
    # 协议
    "Message", "MsgType",
    "build_op_return", "parse_op_return",
    "address_to_hash160", "hash160_to_address",
    "compute_body_hash", "encode_header", "decode_header",
    "HEADER_SIZE", "PROTOCOL_MAGIC", "PROTOCOL_VERSION",
    # 工具
    "privkey_to_wif", "wif_to_privkey",
    "privkey_to_address", "pubkey_to_address",
    "sign_message", "verify_signature",
    "hash160", "sha256", "hash256",
    "build_op_return_script", "build_p2pkh_script",
]
