# -*- coding: utf-8 -*-
"""
铭信 (MingChat) v0.3.2 - 信誉系统
只做链上存证，不做算法。信誉评分由市场自由竞争。

消息类型：
  0x30 REPUTATION_SCORE  — 评分消息
  0x31 REPUTATION_REVIEW — 评语消息
  0x32 REPUTATION_BOND   — 质押消息
"""

import json
import time
import hashlib
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict

from .models import MsgType
from .bsv_tools import ecdsa_sign, ecdsa_verify, sha256


# ── 数据模型 ──────────────────────────────────────────────

@dataclass
class ReputationScore:
    """评分消息体 (REPUTATION_SCORE, 0x30)"""
    v: int = 1                              # schema版本
    target: str = ""                        # 被评分DID: did:bsv:{hash160}
    relates_to: str = ""                    # 关联交易TXID (可选)
    tx_type: str = ""                       # 交易类型: task|chat|arbitration|recommend
    score: int = 0                          # 总体评分 0-100
    dims: Dict[str, int] = field(default_factory=dict)  # 维度评分: {"quality": 90, "timeliness": 80, "comm": 85}
    comment: str = ""                       # 评语哈希: "sha256:{hex}" 或 "ipfs:{cid}"
    lang: str = "zh"                        # 语言代码

    def validate(self) -> Optional[str]:
        """验证数据有效性，返回错误信息或None"""
        if not self.target or not self.target.startswith("did:bsv:"):
            return "target 必需且格式为 did:bsv:{hash160}"
        if not isinstance(self.score, int) or self.score < 0 or self.score > 100:
            return "score 必需为 0-100 的整数"
        for key, val in self.dims.items():
            if not isinstance(val, int) or val < 0 or val > 100:
                return f"dims.{key} 必需为 0-100 的整数"
        if self.comment and not self.comment.startswith(("sha256:", "ipfs:")):
            return "comment 格式应为 sha256:{hex} 或 ipfs:{cid}"
        return None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v or v == 0}

    @classmethod
    def from_dict(cls, data: dict) -> "ReputationScore":
        return cls(
            v=data.get("v", 1),
            target=data.get("target", ""),
            relates_to=data.get("relates_to", ""),
            tx_type=data.get("tx_type", ""),
            score=data.get("score", 0),
            dims=data.get("dims", {}),
            comment=data.get("comment", ""),
            lang=data.get("lang", "zh"),
        )


@dataclass
class ReputationReview:
    """评语消息体 (REPUTATION_REVIEW, 0x31)"""
    target: str = ""
    relates_to: str = ""
    text: str = ""
    lang: str = "zh"

    def validate(self) -> Optional[str]:
        if not self.target or not self.target.startswith("did:bsv:"):
            return "target 必需且格式为 did:bsv:{hash160}"
        if len(self.text.encode('utf-8')) > 3850:
            return f"评语文超过长: {len(self.text.encode('utf-8'))} > 3850 字节"
        return None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

    @classmethod
    def from_dict(cls, data: dict) -> "ReputationReview":
        return cls(
            target=data.get("target", ""),
            relates_to=data.get("relates_to", ""),
            text=data.get("text", ""),
            lang=data.get("lang", "zh"),
        )


@dataclass
class ReputationBond:
    """质押消息体 (REPUTATION_BOND, 0x32)"""
    action: str = "lock"                    # lock|release|penalty
    amount: int = 0                         # 质押金额（sat）
    target_did: str = ""                    # 被质押的DID
    lock_until: int = 0                     # 解锁时间戳（0=永久锁定）

    def validate(self) -> Optional[str]:
        if self.action not in ("lock", "release", "penalty"):
            return "action 必须为 lock|release|penalty"
        if not self.target_did or not self.target_did.startswith("did:bsv:"):
            return "target_did 必需且格式为 did:bsv:{hash160}"
        if self.amount < 0:
            return "amount 不能为负数"
        return None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

    @classmethod
    def from_dict(cls, data: dict) -> "ReputationBond":
        return cls(
            action=data.get("action", "lock"),
            amount=data.get("amount", 0),
            target_did=data.get("target_did", ""),
            lock_until=data.get("lock_until", 0),
        )


# ── 签名验证 ──────────────────────────────────────────────

def sign_reputation(payload: dict, privkey_bytes: bytes) -> str:
    """对信誉消息体签名
    
    签名对象: sha256(json.dumps(payload, sort_keys=True))
    返回 hex 格式的 DER 签名
    """
    msg_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    msg_hash = sha256(msg_bytes)
    r, s = ecdsa_sign(privkey_bytes, msg_hash)
    
    # 构建标准ECDSA DER签名（不含sighash，ecdsa_verify能解析的格式）
    def _enc(n: int) -> bytes:
        b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
        if b[0] & 0x80:
            b = b'\x00' + b  # 高位为1时加0x00前导
        return b
    r_b = _enc(r)
    s_b = _enc(s)
    # 0x30 [total_len] 0x02 [r_len] [r] 0x02 [s_len] [s]
    inner = bytes([0x02, len(r_b)]) + r_b + bytes([0x02, len(s_b)]) + s_b
    der = bytes([0x30, len(inner)]) + inner
    return der.hex()


def verify_reputation(payload: dict, signature_hex: str, pubkey_hex: str) -> bool:
    """验证信誉消息签名"""
    msg_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    msg_hash = sha256(msg_bytes)
    sig_bytes = bytes.fromhex(signature_hex)
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    return ecdsa_verify(pubkey_bytes, sig_bytes, msg_hash)


# ── 本地信誉缓存 ──────────────────────────────────────────

class ReputationStore:
    """本地信誉数据缓存
    
    自动从Bridge同步链上原始数据，不做计算。
    """

    def __init__(self):
        self._scores: Dict[str, List[dict]] = {}   # did -> [{score_entry}]
        self._reviews: Dict[str, List[dict]] = {}   # did -> [{review_entry}]
        self._bonds: Dict[str, List[dict]] = {}     # did -> [{bond_entry}]
        self._updated_at: int = 0

    def add_score(self, target_did: str, entry: dict):
        if target_did not in self._scores:
            self._scores[target_did] = []
        self._scores[target_did].append(entry)
        self._updated_at = int(time.time() * 1000)

    def add_review(self, target_did: str, entry: dict):
        if target_did not in self._reviews:
            self._reviews[target_did] = []
        self._reviews[target_did].append(entry)
        self._updated_at = int(time.time() * 1000)

    def add_bond(self, target_did: str, entry: dict):
        if target_did not in self._bonds:
            self._bonds[target_did] = []
        self._bonds[target_did].append(entry)
        self._updated_at = int(time.time() * 1000)

    def get_scores(self, did: str) -> List[dict]:
        return self._scores.get(did, [])

    def get_reviews(self, did: str) -> List[dict]:
        return self._reviews.get(did, [])

    def get_bonds(self, did: str) -> List[dict]:
        return self._bonds.get(did, [])

    def get_stats(self, did: str) -> dict:
        """返回链上原始数据统计摘要（不做加权计算）"""
        scores = self._scores.get(did, [])
        if not scores:
            return {
                "did": did,
                "score_count": 0,
                "unique_raters": 0,
                "avg_score": 0,
                "avg_dims": {},
            }
        
        unique_raters = len(set(s.get("rater", "") for s in scores))
        avg_score = sum(s["score"] for s in scores if "score" in s) / len(scores)
        
        # 各维度平均值
        dims_sum = {}
        dims_count = {}
        for s in scores:
            for key, val in s.get("dims", {}).items():
                dims_sum[key] = dims_sum.get(key, 0) + val
                dims_count[key] = dims_count.get(key, 0) + 1
        avg_dims = {k: round(v / dims_count[k], 1) for k, v in dims_sum.items()}
        
        # 质押
        bonds = self._bonds.get(did, [])
        active_bond = sum(
            b["amount"] for b in bonds
            if b.get("action") == "lock"
        )
        
        return {
            "did": did,
            "score_count": len(scores),
            "unique_raters": unique_raters,
            "avg_score": round(avg_score, 1),
            "avg_dims": avg_dims,
            "bond_sats": active_bond,
            "last_score_at": max(s.get("timestamp", 0) for s in scores),
        }

    def to_dict(self) -> dict:
        return {
            "scores": self._scores,
            "reviews": self._reviews,
            "bonds": self._bonds,
            "updated_at": self._updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReputationStore":
        store = cls()
        store._scores = data.get("scores", {})
        store._reviews = data.get("reviews", {})
        store._bonds = data.get("bonds", {})
        store._updated_at = data.get("updated_at", 0)
        return store
