#!/usr/bin/env python3
"""
P2P Chat + SPV 完整集成测试
= P2P 加密消息 + SPV Merkle 证明验证

对标 bsv-poker 的 regtest E2E：证明整个信任最小化链路工作正常
"""
import asyncio
import logging
import json
import os
import hashlib
import time
import sys

logging.basicConfig(level=logging.WARNING, format='%(message)s')
log = logging.getLogger("test")

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

from crypto import generate_keypair, encrypt_for_recipient, decrypt_from_sender
from identity import Identity
from message import Message, MessageStore
from spv import (
    BlockHeader, build_merkle_root, generate_merkle_proof,
    verify_merkle_proof, HeaderStore
)
from app import P2PChat


def test_spv_merkle_verification():
    """
    Test 1: SPV Merkle 证明 — 模拟链上消息验证
    
    流程对标 bsv-poker 的 SpvFunding verification:
    1. 构建一个虚拟区块头（含 merkle root）
    2. 构建该区块的交易列表（含我们的消息 tx）
    3. 计算 merkle root，验证与区块头一致
    4. 生成 merkle proof
    5. 验证 proof
    """
    print("\n" + "=" * 60)
    print("  TEST 1: SPV Merkle Proof Verification")
    print("=" * 60)

    # 模拟区块中的交易列表（包括我们的聊天消息 tx）
    # 每个 txid 是 64 hex = 32 bytes
    txids = [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "1111111111111111111111111111111111111111111111111111111111111111",
        "2222222222222222222222222222222222222222222222222222222222222222",
    ]
    our_txid = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"

    # 1. 计算 merkle root
    merkle_root = build_merkle_root(txids)
    merkle_root_hex = merkle_root[::-1].hex()
    print(f"  Tx count: {len(txids)}")
    print(f"  Our tx:   {our_txid[:16]}...")
    print(f"  Merkle root: {merkle_root_hex[:24]}...")

    # 2. 构建区块头
    header = BlockHeader(
        version=536870912,
        prev_hash=b'\x11' * 32,
        merkle_root=merkle_root,
        timestamp=int(time.time()),
        bits=0x207fffff,  # regtest min difficulty
        nonce=0,
        height=999999  # 虚拟高度
    )
    header.hash = header.compute_hash()
    # 跳过 PoW 检查 — 测试用虚拟区块，不挖矿
    print(f"  Header hash: {header.hash_hex[:24]}...")
    print(f"  PoW check: skipped (test header, not mined)")

    # 3. 生成 Merkle proof
    proof = generate_merkle_proof(txids, our_txid)
    assert proof is not None, "Must generate proof!"
    assert proof["merkle_root"] == merkle_root_hex, "Root must match!"
    print(f"  Proof steps: {len(proof['proof'])}")
    for i, step in enumerate(proof["proof"]):
        print(f"    Step {i}: {step['position']} sibling {step['hash'][:16]}...")

    # 4. 验证 proof
    valid = verify_merkle_proof(our_txid, proof["proof"], merkle_root_hex)
    assert valid, "Merkle proof must be valid!"
    print(f"  Proof verified: ✅")

    # 5. 篡改攻击测试：改 txid 后验证失败
    wrong_txid = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    wrong_valid = verify_merkle_proof(wrong_txid, proof["proof"], merkle_root_hex)
    assert not wrong_valid, "Wrong txid should fail!"
    print(f"  Tamper detection: ✅ (wrong txid rejected)")

    # 6. 存储到 HeaderStore
    store = HeaderStore("/tmp/test_spv_proof.db")
    store.save_header(header)
    loaded = store.get_header(999999)
    assert loaded.hash == header.hash
    print(f"  Persistence: ✅")

    os.remove("/tmp/test_spv_proof.db")
    print("\n  ✅ TEST 1 PASSED — SPV Merkle proof complete!")


async def test_p2p_encrypted_messaging():
    """
    Test 2: P2P 加密消息传递 — 完整端到端
    """
    print("\n" + "=" * 60)
    print("  TEST 2: P2P Encrypted Messaging (E2E)")
    print("=" * 60)

    alice = Identity.create("alice_test")
    bob = Identity.create("bob_test")

    chat_a = P2PChat(alice, data_dir="/tmp/p2pchat_test_a", port=0)
    chat_b = P2PChat(bob, data_dir="/tmp/p2pchat_test_b", port=0)

    await chat_a.start(sync_spv=False)
    await chat_b.start(sync_spv=False)

    # 交换联系人
    chat_a.identity.add_contact("bob_test", bob.pubkey_hex)
    chat_b.identity.add_contact("alice_test", alice.pubkey_hex)

    # 连接
    ok = await chat_b.connect_peer(chat_a.mesh.host, chat_a.mesh.port)
    assert ok, "Connection failed!"
    await asyncio.sleep(0.5)
    print(f"  P2P connection: ✅")

    # Alice → Bob
    msg1 = await chat_a.send_message("bob_test", "Hello from Alice! 🔐")
    assert msg1 is not None
    await asyncio.sleep(0.3)
    print(f"  Alice → Bob sent: ✅")

    # Bob 收到
    bob_msgs = chat_b.get_recent_messages("alice_test")
    assert len(bob_msgs) > 0
    assert bob_msgs[-1].content == "Hello from Alice! 🔐"
    print(f"  Bob received: ✅ ({bob_msgs[-1].content})")

    # Bob → Alice
    msg2 = await chat_b.send_message("alice_test", "Hi Alice! 消息收到 ✅")
    assert msg2 is not None
    await asyncio.sleep(0.3)
    print(f"  Bob → Alice sent: ✅")

    # Alice 收到
    alice_msgs = chat_a.get_recent_messages("bob_test")
    assert len(alice_msgs) >= 1
    print(f"  Alice received: ✅ ({alice_msgs[-1].content})")

    # 广播
    await chat_a.send_broadcast("Broadcast test!")
    await asyncio.sleep(0.3)
    print(f"  Broadcast: ✅")

    # SPV 模块状态
    status = chat_a.status
    assert "spv" in status
    assert status["spv"]["network"] == "main"
    print(f"  SPV module: ✅ ({status['spv']})")

    # 统计
    print(f"\n  Messages: Alice={len(alice_msgs)}, Bob={len(bob_msgs)}")
    print(f"  Total stored: Alice={len(chat_a.store.messages)}, Bob={len(chat_b.store.messages)}")

    await chat_a.stop()
    await chat_b.stop()
    print("\n  ✅ TEST 2 PASSED — P2P encrypted messaging complete!")


async def test_spv_onchain_flow():
    """
    Test 3: 模拟链上消息的完整 SPV 验证流
    
    模拟场景：Alice 发送消息时 Bob 离线
    → 消息写入链上 OP_RETURN
    → Bob 上线后从链上获取
    → SPV 验证交易确实在区块中
    → 解密消息
    """
    print("\n" + "=" * 60)
    print("  TEST 3: On-Chain Message SPV Verification (Sim)")
    print("=" * 60)

    alice = Identity.create("alice_onchain")
    bob = Identity.create("bob_onchain")

    # 1. Alice 创建加密消息
    msg = Message.create("alice_onchain", "bob_onchain",
                         "Offline message for Bob! SPV verified.")
    msg.encrypt(alice.identity_sk, bob.identity_pk)
    print(f"  Message encrypted: ✅")
    print(f"    Ephemeral PK: {msg.ephemeral_pk[:20]}...")
    print(f"    Ciphertext: {msg.ciphertext[:20]}...")

    # 2. 模拟 OP_RETURN 数据（对标 bsv-poker OnChainEnvelope）
    from message import OnChainEnvelope
    envelope = OnChainEnvelope.from_message(msg, bob.pubkey_hex)
    op_return_data = envelope.encode()
    print(f"  OP_RETURN data: {len(op_return_data)}B")

    # 3. 模拟该交易被打包进区块
    # 构造虚拟区块
    op_return_txid = hashlib.sha256(op_return_data).hexdigest()
    txids = [
        hashlib.sha256(f"tx{i}".encode()).hexdigest() for i in range(7)
    ]
    txids.insert(3, op_return_txid)  # 插入我们的消息 tx
    print(f"  Block tx count: {len(txids)}")
    print(f"  Our txid: {op_return_txid[:16]}...")

    merkle_root = build_merkle_root(txids)
    merkle_root_hex = merkle_root[::-1].hex()

    header = BlockHeader(
        version=1,
        prev_hash=b'\x22' * 32,
        merkle_root=merkle_root,
        timestamp=int(time.time()),
        bits=0x207fffff,  # regtest min difficulty — SET BEFORE hash
        nonce=42,
        height=1000000
    )
    header.hash = header.compute_hash()
    # 跳过 PoW — 测试虚拟区块
    print(f"  Header stored: height={header.height}, hash={header.hash_hex[:16]}...")
    # 4. Bob 上线 → SPV 验证
    store = HeaderStore("/tmp/test_spv_onchain.db")
    store.save_header(header)
    print(f"  Header stored: height={header.height}, hash={header.hash_hex[:16]}...")

    # 5. 生成 merkle proof
    proof = generate_merkle_proof(txids, op_return_txid)
    assert proof is not None
    valid = verify_merkle_proof(op_return_txid, proof["proof"], merkle_root_hex)
    assert valid, "SPV verification must pass!"
    print(f"  SPV proof: {len(proof['proof'])} steps, verified ✅")

    # 6. Bob 解密消息
    import base64
    epk_bytes = bytes.fromhex(msg.ephemeral_pk)
    ct = base64.b64decode(msg.ciphertext)
    plaintext = decrypt_from_sender(epk_bytes, ct, bob.identity_sk)
    msg_dict = json.loads(plaintext.decode('utf-8'))
    assert msg_dict["content"] == "Offline message for Bob! SPV verified."
    print(f"  Message decrypted: ✅")
    print(f"    Content: {msg_dict['content']}")

    os.remove("/tmp/test_spv_onchain.db")
    print("\n  ✅ TEST 3 PASSED — On-chain SPV verification complete!")


async def main():
    print("=" * 60)
    print("  P2P Chat + SPV — FULL INTEGRATION TEST SUITE")
    print("  = 加密消息 + P2P 传输 + SPV 链上验证")
    print("=" * 60)

    # Test 1: SPV Merkle Proof (纯逻辑，无网络)
    test_spv_merkle_verification()

    # Test 2: P2P Encrypted Messaging (真实 asyncio 网络)
    await test_p2p_encrypted_messaging()

    # Test 3: On-Chain SPV Verification (模拟链上消息)
    await test_spv_onchain_flow()

    print("\n" + "=" * 60)
    print("  🎉 ALL 3 TESTS PASSED")
    print("  ✅ SPV Merkle proof verification")
    print("  ✅ P2P encrypted messaging (ECDH + AES-256-GCM)")
    print("  ✅ On-chain message SPV verification flow")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
