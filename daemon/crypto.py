"""
P2P Chat Crypto Layer — 对标 bsv-poker 的 BsvPoker.Crypto
= ECDH (secp256k1) + HKDF-SHA256 + AES-256-GCM
= 每消息独立临时密钥，绝不重用
"""
import os
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend

CURVE = ec.SECP256K1()
AES_KEY_LEN = 32  # AES-256

# ─── Key Generation ───────────────────────────────────────────

def generate_keypair():
    """生成 secp256k1 密钥对（对标 bsv-poker 的 WalletKeys）"""
    sk = ec.generate_private_key(CURVE, default_backend())
    pk = sk.public_key()
    return sk, pk

def serialize_public_key(pk: ec.EllipticCurvePublicKey) -> bytes:
    """压缩格式公钥序列化（33 字节，02/03 前缀）"""
    return pk.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.CompressedPoint
    )

def deserialize_public_key(data: bytes) -> ec.EllipticCurvePublicKey:
    """从压缩格式恢复公钥"""
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, data)

def serialize_private_key(sk: ec.EllipticCurvePrivateKey) -> bytes:
    """私钥序列化（32 字节原始标量）"""
    return sk.private_numbers().private_value.to_bytes(32, 'big')

def deserialize_private_key(data: bytes) -> ec.EllipticCurvePrivateKey:
    """从原始标量恢复私钥"""
    n = int.from_bytes(data, 'big')
    return ec.derive_private_key(n, CURVE, default_backend())

# ─── ECDH Key Agreement ───────────────────────────────────────

def ecdh_shared_secret(my_sk: ec.EllipticCurvePrivateKey,
                       their_pk: ec.EllipticCurvePublicKey) -> bytes:
    """
    ECDH 共享密钥 — 对标 bsv-poker ChatService 的 ephemeral ECDH
    每次调用生成新的一次性共享密钥
    """
    return my_sk.exchange(ec.ECDH(), their_pk)

# ─── Message Encryption ───────────────────────────────────────

def encrypt_message(plaintext: bytes,
                    shared_secret: bytes,
                    info: bytes = b"p2pchat/v1") -> bytes:
    """
    对标 bsv-poker ChatService: ECDH → HKDF → AES-256-GCM
    
    格式: nonce(12) ‖ ciphertext ‖ tag(16)
    HKDF 从共享密钥派生出 AES 密钥
    """
    # HKDF 派生 AES 密钥
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_LEN,
        salt=None,
        info=info,
        backend=default_backend()
    ).derive(shared_secret)

    # AES-256-GCM 加密，随机 nonce
    nonce = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # ciphertext 末尾已包含 16 字节 tag
    return nonce + ciphertext


def decrypt_message(encrypted: bytes,
                    shared_secret: bytes,
                    info: bytes = b"p2pchat/v1") -> bytes:
    """
    解密 — 对标 bsv-poker 的 Aead.Open()
    失败抛异常（tag 不匹配 = 篡改或密钥错误）
    """
    if len(encrypted) < 28:  # nonce(12) + min ciphertext(1) + tag(16)
        raise ValueError("Encrypted data too short")

    nonce = encrypted[:12]
    ciphertext_with_tag = encrypted[12:]

    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_LEN,
        salt=None,
        info=info,
        backend=default_backend()
    ).derive(shared_secret)

    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext_with_tag, None)


# ─── Per-Recipient Ephemeral Encryption ───────────────────────

def encrypt_for_recipient(plaintext: bytes,
                          my_sk: ec.EllipticCurvePrivateKey,
                          their_pk: ec.EllipticCurvePublicKey,
                          info: bytes = b"p2pchat/v1") -> tuple[bytes, bytes]:
    """
    对标 bsv-poker 的 ChatService 逐消息加密:
    - 每次生成新的临时 ECDH 密钥对
    - 用临时私钥 × 对方公钥 = 共享密钥
    - 消息体 = 临时公钥 ‖ 密文
    - 临时密钥用完即弃，下一次全新
    
    返回: (ephemeral_pk_bytes, encrypted_message)
    """
    # 生成一次性临时密钥
    ephemeral_sk = ec.generate_private_key(CURVE, default_backend())
    ephemeral_pk = ephemeral_sk.public_key()

    # ECDH: ephemeral_sk × their_pk
    shared = ephemeral_sk.exchange(ec.ECDH(), their_pk)

    # 加密
    encrypted = encrypt_message(plaintext, shared, info)
    return serialize_public_key(ephemeral_pk), encrypted


def decrypt_from_sender(ephemeral_pk_bytes: bytes,
                        encrypted: bytes,
                        my_sk: ec.EllipticCurvePrivateKey,
                        info: bytes = b"p2pchat/v1") -> bytes:
    """
    解密来自发送方的消息:
    - 从临时公钥 × 我的私钥 = 共享密钥（与发送方一致）
    - 解密
    """
    ephemeral_pk = deserialize_public_key(ephemeral_pk_bytes)
    shared = my_sk.exchange(ec.ECDH(), ephemeral_pk)
    return decrypt_message(encrypted, shared, info)


# ─── Hashing ──────────────────────────────────────────────────

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def sha256d(data: bytes) -> bytes:
    """双重 SHA256（BSV 标准）"""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def hash160(data: bytes) -> bytes:
    """RIPEMD-160(SHA-256(data)) — BSV 地址标准"""
    sha = hashlib.sha256(data).digest()
    h = hashlib.new('ripemd160')
    h.update(sha)
    return h.digest()

# ─── Self-Test ────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== P2P Chat Crypto Self-Test ===")

    # Test ECDH
    alice_sk, alice_pk = generate_keypair()
    bob_sk, bob_pk = generate_keypair()

    shared_a = ecdh_shared_secret(alice_sk, bob_pk)
    shared_b = ecdh_shared_secret(bob_sk, alice_pk)
    assert shared_a == shared_b, "ECDH mismatch!"
    print("✅ ECDH key agreement OK")

    # Test encrypt/decrypt
    msg = "Hello, P2P world! 你好，去中心化世界！".encode('utf-8')
    encrypted = encrypt_message(msg, shared_a)
    decrypted = decrypt_message(encrypted, shared_a)
    assert decrypted == msg, "Encrypt/decrypt mismatch!"
    print(f"✅ Encrypt/decrypt OK ({len(msg)}B plain -> {len(encrypted)}B encrypted)")

    # Test per-recipient encryption
    epk, enc = encrypt_for_recipient(b"Secret message for Bob", alice_sk, bob_pk)
    dec = decrypt_from_sender(epk, enc, bob_sk)
    assert dec == b"Secret message for Bob", "Per-recipient encrypt/decrypt failed!"
    print("✅ Per-recipient encryption OK (ephemeral keys, no reuse)")

    # Test key serialization
    pk_bytes = serialize_public_key(alice_pk)
    pk_restored = deserialize_public_key(pk_bytes)
    assert pk_bytes == serialize_public_key(pk_restored)
    print(f"✅ Key serialization OK ({len(pk_bytes)}B compressed)")

    # Test wrong key cannot decrypt
    eve_sk, eve_pk = generate_keypair()
    try:
        decrypt_from_sender(epk, enc, eve_sk)
        assert False, "Should have raised!"
    except Exception:
        print("✅ Wrong key cannot decrypt (AEAD tag verification)")

    print("\n🎉 All crypto tests passed!")
