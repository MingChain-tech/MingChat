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


def _pubkey_to_did_id(pubkey_hex: str) -> str:
    """压缩公钥hex → DID标识符 (hash160(公钥)的前20字节hex = 40字符)
    一把私钥只有一个压缩公钥 → 一个hash160 → 唯一DID
    """
    from .bsv_tools import hash160 as bsv_hash160
    pubkey_bytes = bytes.fromhex(pubkey_hex) if len(pubkey_hex) == 66 else pubkey_hex.encode()
    h160 = bsv_hash160(pubkey_bytes)
    return h160.hex()[:40]


class MingDID:
    """
    铭识DID管理器
    注册/解析/更新/吊销 DID 文档

    关键约束: did:bsv:{hash160(controller_pk)[:40]}
    密码学保证了同一把私钥(一个压缩公钥)只能生成唯一DID。
    """

    def __init__(self):
        self._registry: Dict[str, dict] = {}  # did -> DID文档+元数据

    # ── DID注册 ───────────────────────────────────────

    def register(self, controller_pk: str, auth_pk: str = "",
                 name: str = "", description: str = "",
                 service_endpoint: str = "",
                 capabilities_hash: str = "",
                 controller_privkey: bytes = None,
                 identity_level: int = 0,
                 kyc_hash: str = "",
                 kyc_provider: str = "",
                 license_ref: str = "") -> dict:
        """
        创建DID注册请求
        实际注册需要发送到链上，这里先构建DID文档

        ⚠ 方案A: 本地防重复 — 同一controller_pk不允许重复注册
        """
        did_id = _pubkey_to_did_id(controller_pk)
        did_str = f"did:bsv:{did_id}"

        # 方案A: 本地注册表检查 — 同一controller_pk已注册则拒绝
        for existing_did, entry in self._registry.items():
            if entry.get("doc", {}).controller_pk == controller_pk:
                if entry.get("status") != "revoked":
                    raise ValueError(
                        f"controller_pk 已注册为 {existing_did}，"
                        f"同一私钥只能注册一个DID。如要重新注册，请先吊销旧DID。"
                    )
        
        doc = DIDDocument(
            did=did_str,
            controller_pk=controller_pk,
            auth_pk=auth_pk or controller_pk,
            service_endpoint=service_endpoint,
            capabilities_hash=capabilities_hash,
            profile_name=name,
            profile_description=description,
            profile_version="1.0",
            identity_level=identity_level,
            kyc_hash=kyc_hash,
            kyc_provider=kyc_provider,
            license_ref=license_ref,
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
        
        # 写入本地注册表（供方案A防重复检查）
        entry = {
            "doc": doc,
            "status": "active",
            "update_seq": 1,
            "created_at": int(time.time() * 1000),
            "updated_at": int(time.time() * 1000),
        }
        self._registry[did_str] = entry
        
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
            # 身份等级字段
            identity_level=data.get("identity_level", 0),
            kyc_hash=data.get("kyc_hash", ""),
            kyc_provider=data.get("kyc_provider", ""),
            license_ref=data.get("license_ref", ""),
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
    identity_level: int = 0,
    kyc_hash: str = "",
    kyc_provider: str = "",
    license_ref: str = "",
) -> DIDDocument:
    """快速创建DID文档

    使用 hash160(压缩公钥) 作为DID标识符，保证一私钥一DID。
    """
    did_id = _pubkey_to_did_id(controller_pk)
    
    return DIDDocument(
        did=f"did:bsv:{did_id}",
        controller_pk=controller_pk,
        auth_pk=auth_pk or controller_pk,
        service_endpoint=service_endpoint,
        profile_name=name,
        profile_description=description,
        identity_level=identity_level,
        kyc_hash=kyc_hash,
        kyc_provider=kyc_provider,
        license_ref=license_ref,
    )
