"""
P2P Chat Identity Layer — 对标 bsv-poker 的 Identity.cs (Type-42)
= 一个主种子 → HMAC 派生链 → 无限一次性身份密钥
= 身份密钥用作签名/DH/聊天，统一身份
"""
import os
import hashlib
import hmac
import base64
import json
from dataclasses import dataclass, field
from typing import Optional
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from crypto import (
    CURVE, generate_keypair,
    serialize_public_key, deserialize_public_key,
    serialize_private_key, deserialize_private_key,
    ecdh_shared_secret, sha256
)

# ─── Seed Generation ──────────────────────────────────────────

def generate_seed() -> bytes:
    """生成 32 字节主种子"""
    return os.urandom(32)

def seed_to_identity_key(seed: bytes) -> ec.EllipticCurvePrivateKey:
    """
    主种子 → 身份密钥（对标 bsv-poker WalletKeys）
    HMAC-SHA256(seed, "p2pchat-identity/v1") 作为私钥标量
    """
    derived = hmac.new(b"p2pchat-identity/v1", seed, hashlib.sha256).digest()
    n = int.from_bytes(derived, 'big') % CURVE.key_size
    return ec.derive_private_key(n, CURVE, default_backend())

# ─── Type-42 派生（对标 bsv-poker 的 IdentityPayment）───────

def derive_child_key(parent_sk: ec.EllipticCurvePrivateKey,
                     counterparty_pk: ec.EllipticCurvePublicKey,
                     invoice: bytes) -> ec.EllipticCurvePrivateKey:
    """
    Type-42: 从父密钥 + 对方公钥 + invoice 派生一次性子密钥
    
    shared = ECDH(parent_sk, counterparty_pk)
    k = HMAC-SHA256(shared, invoice) mod n
    child_sk = (parent_sk + k) mod n
    
    对标 bsv-poker IdentityPayment.PayToPub / SpendPriv
    """
    shared = ecdh_shared_secret(parent_sk, counterparty_pk)
    k = int.from_bytes(
        hmac.new(invoice, shared, hashlib.sha256).digest(), 'big'
    ) % ec.SECP256K1().key_size

    parent_n = parent_sk.private_numbers().private_value
    child_n = (parent_n + k) % ec.SECP256K1().key_size
    return ec.derive_private_key(child_n, CURVE, default_backend())


def derive_child_pub(parent_pk: ec.EllipticCurvePublicKey,
                     counterparty_pk: ec.EllipticCurvePublicKey,
                     invoice: bytes) -> ec.EllipticCurvePublicKey:
    """
    只从公钥侧派生子公钥（付款方无需父私钥）
    child_pub = parent_pk + k·G
    """
    # 我们需要对方的私钥来做 ECDH...但付款方没有
    # 这需要父私钥。实际使用时，如果只有公钥，需要预先商定 invoice
    # bsv-poker 的做法是：付款方用自己的私钥做 ECDH
    raise NotImplementedError("Use derive_child_key which requires the parent private key")


# ─── Identity ─────────────────────────────────────────────────

@dataclass
class Identity:
    """
    对标 bsv-poker 的 Profile / Identity
    一个身份 = 身份密钥 + handle + 联系人列表
    """
    handle: str
    identity_sk: ec.EllipticCurvePrivateKey
    seed_hash: str = ""  # 种子的 sha256（用于验证）
    contacts: dict = field(default_factory=dict)  # handle → pubkey_hex

    @property
    def identity_pk(self) -> ec.EllipticCurvePublicKey:
        return self.identity_sk.public_key()

    @property
    def pubkey_hex(self) -> str:
        return serialize_public_key(self.identity_pk).hex()

    @classmethod
    def create(cls, handle: str):
        """创建新身份"""
        seed = generate_seed()
        sk = seed_to_identity_key(seed)
        return cls(
            handle=handle,
            identity_sk=sk,
            seed_hash=sha256(seed).hex()
        )

    def sign(self, message: bytes) -> bytes:
        """用身份密钥签名（ECDSA, secp256k1）"""
        return self.identity_sk.sign(message, ec.ECDSA(hashes.SHA256()))

    def verify(self, message: bytes, signature: bytes,
               sender_pk: ec.EllipticCurvePublicKey) -> bool:
        """验证签名"""
        try:
            sender_pk.verify(signature, message, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False

    def add_contact(self, handle: str, pubkey_hex: str):
        """添加联系人"""
        self.contacts[handle] = pubkey_hex

    def get_contact_pk(self, handle: str) -> Optional[ec.EllipticCurvePublicKey]:
        """获取联系人公钥"""
        raw = self.contacts.get(handle)
        if raw:
            return deserialize_public_key(bytes.fromhex(raw))
        return None

    def to_dict(self) -> dict:
        return {
            "handle": self.handle,
            "pubkey": self.pubkey_hex,
            "seed_hash": self.seed_hash,
            "contacts": self.contacts
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def save(self, path: str):
        """保存身份到文件"""
        data = {
            **self.to_dict(),
            "secret_key": serialize_private_key(self.identity_sk).hex()
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Identity":
        """从文件加载身份"""
        with open(path) as f:
            data = json.load(f)
        sk = deserialize_private_key(bytes.fromhex(data["secret_key"]))
        ident = cls(
            handle=data["handle"],
            identity_sk=sk,
            seed_hash=data.get("seed_hash", ""),
            contacts=data.get("contacts", {})
        )
        return ident


# ─── Self-Test ────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== P2P Chat Identity Self-Test ===")

    # Create identity
    alice = Identity.create("alice")
    bob = Identity.create("bob")
    print(f"✅ Created identities: @{alice.handle} @{bob.handle}")

    # Pubkey serialization round-trip
    pk_raw = serialize_public_key(alice.identity_pk)
    assert len(pk_raw) == 33
    print(f"✅ Pubkey is 33B compressed: {pk_raw.hex()[:20]}...")

    # Sign and verify
    msg = b"Hello, this is alice!"
    sig = alice.sign(msg)
    assert alice.verify(msg, sig, alice.identity_pk)
    assert not alice.verify(msg, sig, bob.identity_pk), "Bob should not verify Alice's sig"
    print(f"✅ ECDSA sign/verify OK (sig: {len(sig)}B DER)")

    # Contact management
    alice.add_contact("bob", bob.pubkey_hex)
    bob.add_contact("alice", alice.pubkey_hex)
    assert alice.get_contact_pk("bob") is not None
    print("✅ Contact management OK")

    # Save and load
    alice.save("/tmp/test_alice.json")
    alice2 = Identity.load("/tmp/test_alice.json")
    assert alice2.handle == "alice"
    assert alice2.pubkey_hex == alice.pubkey_hex
    print("✅ Save/load identity OK")

    print("\n🎉 All identity tests passed!")
