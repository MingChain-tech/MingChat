"""
铭信 (MingChat) v0.3 - 铭识DID协议
基于BSV的分布式身份标识

DID格式: did:bsv:{txid}
无注册局，链上即真相源
"""
import json
import time
import hashlib
from typing import Optional, List, Dict
from dataclasses import dataclass, field

from .models import (
    MsgType, TaskOp, AuditFields, AuditFlags,
    DIDDocument, Message,
)
from .bsv_tools import ecdsa_sign, ecdsa_verify, privkey_to_pubkey


class MingDID:
    """
    铭识DID管理器
    注册/解析/更新/吊销 DID 文档
    """

    def __init__(self):
        self._registry: Dict[str, dict] = {}  # did -> DID文档+元数据

    # ── DID注册 ───────────────────────────────────────

    def register(self, controller_pk: str, auth_pk: str = "",
                 name: str = "", description: str = "",
                 service_endpoint: str = "",
                 capabilities_hash: str = "",
                 controller_privkey: bytes = None) -> dict:
        """
        创建DID注册请求
        实际注册需要发送到链上，这里先构建DID文档
        """
        # 用controller_pk的hash160作为DID标识符的基础
        did_seed = hashlib.sha256(bytes.fromhex(controller_pk) if len(controller_pk) == 66
                                   else controller_pk.encode()).hexdigest()[:16]
        
        doc = DIDDocument(
            did=f"did:bsv:{did_seed}",
            controller_pk=controller_pk,
            auth_pk=auth_pk or controller_pk,
            service_endpoint=service_endpoint,
            capabilities_hash=capabilities_hash,
            profile_name=name,
            profile_description=description,
            profile_version="1.0",
        )
        
        # 如果有私钥，签名
        if controller_privkey:
            sig_payload = json.dumps({
                "did": doc.did,
                "controller_pk": controller_pk,
                "auth_pk": doc.auth_pk,
            }, sort_keys=True).encode()
            sig = ecdsa_sign(controller_privkey, hashlib.sha256(sig_payload).digest())
            # DER编码签名
            from .bsv_tools import der_encode_sig
            doc.controller_sig = der_encode_sig(sig[0], sig[1], 0x01).hex()
        
        return doc

    def resolve(self, did: str) -> Optional[dict]:
        """
        解析DID文档
        从链上或本地注册表查找
        """
        # 先查本地注册表
        if did in self._registry:
            return self._registry[did]
        
        # 如果是在链上的，需要从BSV交易解析
        # 这里由外部调用者提供解析结果
        return None

    def update(self, did: str, changes: dict,
               controller_privkey: bytes = None) -> Optional[dict]:
        """
        更新DID文档
        changes: 要修改的字段
        """
        current = self._registry.get(did)
        if not current:
            return None
        
        doc = current["doc"]
        seq = current["update_seq"] + 1
        
        # 更新字段
        for key, val in changes.items():
            if hasattr(doc, key):
                setattr(doc, key, val)
        
        doc.update_seq = seq
        
        # 重新签名
        if controller_privkey:
            sig_payload = json.dumps({
                "did": doc.did,
                "update_seq": seq,
                "changes": changes,
            }, sort_keys=True).encode()
            sig = ecdsa_sign(controller_privkey, hashlib.sha256(sig_payload).digest())
            from .bsv_tools import der_encode_sig
            doc.controller_sig = der_encode_sig(sig[0], sig[1], 0x01).hex()
        
        current["doc"] = doc
        current["update_seq"] = seq
        current["updated_at"] = int(time.time() * 1000)
        
        return current

    def revoke(self, did: str, reason: str = "deprecated",
               controller_privkey: bytes = None) -> Optional[dict]:
        """吊销DID"""
        current = self._registry.get(did)
        if not current:
            return None
        
        current["status"] = "revoked"
        current["revoke_reason"] = reason
        current["revoked_at"] = int(time.time() * 1000)
        
        return current

    # ── 链上消息解析 ───────────────────────────────────

    def handle_did_message(self, msg: Message) -> Optional[dict]:
        """处理收到的DID相关消息"""
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError):
            return {"error": "无效的JSON载荷"}
        
        if msg.msg_type == MsgType.DID_REGISTER:
            return self._handle_register(data, msg)
        elif msg.msg_type == MsgType.DID_UPDATE:
            return self._handle_update(data, msg)
        elif msg.msg_type == MsgType.DID_REVOKE:
            return self._handle_revoke(data)
        
        return None

    def _handle_register(self, data: dict, msg: Message) -> dict:
        """处理DID_REGISTER消息"""
        doc = DIDDocument(
            did=data.get("did", ""),
            controller_pk=data.get("controller_pk", ""),
            auth_pk=data.get("auth_pk", ""),
            service_endpoint=data.get("service_endpoint", ""),
            capabilities_hash=data.get("capabilities_hash", ""),
            profile_name=data.get("profile", {}).get("name", ""),
            profile_description=data.get("profile", {}).get("description", ""),
            controller_sig=data.get("controller_sig", ""),
            registration_txid=msg.txid,
        )
        
        entry = {
            "doc": doc,
            "status": "active",
            "update_seq": 1,
            "created_at": int(time.time() * 1000),
            "updated_at": int(time.time() * 1000),
        }
        self._registry[doc.did] = entry
        return entry

    def _handle_update(self, data: dict, msg: Message) -> dict:
        """处理DID_UPDATE消息"""
        did = data.get("did", "")
        current = self._registry.get(did)
        if not current:
            return {"error": f"DID {did} 未注册"}
        
        changes = data.get("changes", {})
        return self.update(did, changes)

    def _handle_revoke(self, data: dict) -> dict:
        """处理DID_REVOKE消息"""
        did = data.get("did", "")
        reason = data.get("reason", "deprecated")
        return self.revoke(did, reason)

    # ── 查询 ───────────────────────────────────────────

    def list_dids(self, status: str = "active") -> List[dict]:
        """列出所有DID"""
        return [
            {"did": k, "status": v["status"], "name": v["doc"].profile_name}
            for k, v in self._registry.items()
            if v["status"] == status
        ]

    def verify_signature(self, did: str, message: bytes,
                         signature_hex: str) -> bool:
        """验证DID所有者签名"""
        entry = self._registry.get(did)
        if not entry:
            return False
        
        doc = entry["doc"]
        pubkey_hex = doc.auth_pk or doc.controller_pk
        try:
            pubkey = bytes.fromhex(pubkey_hex)
            sig = bytes.fromhex(signature_hex)
            return ecdsa_verify(pubkey, hashlib.sha256(message).digest(), sig)
        except Exception:
            return False


# ── 快捷函数 ───────────────────────────────────────────

def make_did_document(
    controller_pk: str,
    auth_pk: str = "",
    name: str = "",
    description: str = "",
    service_endpoint: str = "",
) -> DIDDocument:
    """快速创建DID文档"""
    did_seed = hashlib.sha256(
        bytes.fromhex(controller_pk) if len(controller_pk) == 66
        else controller_pk.encode()
    ).hexdigest()[:16]
    
    return DIDDocument(
        did=f"did:bsv:{did_seed}",
        controller_pk=controller_pk,
        auth_pk=auth_pk or controller_pk,
        service_endpoint=service_endpoint,
        profile_name=name,
        profile_description=description,
    )
