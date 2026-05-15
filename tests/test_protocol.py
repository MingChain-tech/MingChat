#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MingChat v0.3 Protocol Tests
Verify 86B header protocol encoding/decoding

Note: This file uses placeholder/example keys for testing only.
Do NOT use these keys in production!
"""

import sys
import struct
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mingchat import (
    MingChat, Message, MsgType,
    build_op_return, parse_op_return,
    address_to_hash160, hash160_to_address,
    compute_body_hash, encode_header, decode_header,
    HEADER_SIZE, PROTOCOL_MAGIC, PROTOCOL_VERSION,
    privkey_to_wif, wif_to_privkey, privkey_to_address,
    hash160, sha256, generate_privkey
)

# Example test key (TESTNET - for testing only, no funds)
# DO NOT use in production!
TEST_WIF = "L38qYrwhdLrDuKvwybSJ1QdTWWRn5VujUF6mAWWL9mdqfEmbhmw9"
TEST_ADDRESS = "1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD"


def test_protocol_magic():
    """Test protocol identifier"""
    print("=" * 60)
    print("Test 1: Protocol Identifier")
    print(f"  PROTOCOL_MAGIC: 0x{PROTOCOL_MAGIC:08X}")
    print(f"  Expected: 0x4D494E43 (MINC)")
    assert PROTOCOL_MAGIC == 0x4D494E43, f"Protocol ID error: {PROTOCOL_MAGIC}"
    print("  PASSED")


def test_header_size():
    """Test header size"""
    print("\n" + "=" * 60)
    print("Test 2: Header Size")
    print(f"  HEADER_SIZE: {HEADER_SIZE}")
    print(f"  Expected: 86")
    assert HEADER_SIZE == 86, f"Header size error: {HEADER_SIZE}"
    print("  PASSED")


def test_generate_privkey():
    """Test key generation"""
    print("\n" + "=" * 60)
    print("Test 3: Key Generation")
    
    privkey = generate_privkey()
    print(f"  Generated key length: {len(privkey)} bytes")
    assert len(privkey) == 32, "Key should be 32 bytes"
    
    wif = privkey_to_wif(privkey)
    address = privkey_to_address(privkey)
    print(f"  WIF: {wif[:10]}...")
    print(f"  Address: {address}")
    
    assert wif.startswith('L') or wif.startswith('K'), "WIF should start with L or K"
    assert address.startswith('1'), "Mainnet address should start with 1"
    
    print("  PASSED")


def test_encode_decode_header():
    """Test header encoding/decoding"""
    print("\n" + "=" * 60)
    print("Test 4: Header Encoding/Decoding")
    
    msg_type = MsgType.CHAT
    # Using well-known addresses for testing
    sender_hash160 = address_to_hash160("1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD")
    receiver_hash160 = address_to_hash160("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
    timestamp = 1700000000
    body = b"Hello, MingChat!"
    body_hash = compute_body_hash(body)
    
    print(f"  Message Type: {msg_type.to_str()} ({msg_type.value})")
    print(f"  Sender: {hash160_to_address(sender_hash160)[:16]}...")
    print(f"  Receiver: {hash160_to_address(receiver_hash160)[:16]}...")
    print(f"  Timestamp: {timestamp}")
    print(f"  Body: {body}")
    
    # Encode
    header = encode_header(msg_type, sender_hash160, receiver_hash160, timestamp, body_hash)
    
    print(f"\n  Encoded header length: {len(header)} bytes")
    print(f"  Header hex: {header.hex()}")
    
    assert len(header) == HEADER_SIZE, f"Header length error: {len(header)}"
    
    # Decode
    decoded = decode_header(header)
    
    assert decoded is not None, "Decoding failed"
    assert decoded["magic"] == PROTOCOL_MAGIC, f"Protocol ID error: {decoded['magic']}"
    assert decoded["version"] == PROTOCOL_VERSION, f"Version error: {decoded['version']}"
    assert decoded["msg_type"] == msg_type, f"Message type error: {decoded['msg_type']}"
    assert decoded["sender_hash160"] == sender_hash160, "Sender hash error"
    assert decoded["receiver_hash160"] == receiver_hash160, "Receiver hash error"
    assert decoded["timestamp"] == timestamp, f"Timestamp error: {decoded['timestamp']}"
    assert decoded["body_hash"] == body_hash, "Body hash error"
    
    print("  PASSED")


def test_message_serialize():
    """Test message serialization"""
    print("\n" + "=" * 60)
    print("Test 5: Message Serialization/Deserialization")
    
    msg = Message(
        msg_type=MsgType.RPC_REQ,
        sender_hash160=address_to_hash160("1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD"),
        receiver_hash160=address_to_hash160("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"),
        body=b'{"method": "ping", "params": {}}',
        timestamp=1700000000
    )
    
    print(f"  Message Type: {msg.msg_type.to_str()}")
    print(f"  Body: {msg.body}")
    
    # Serialize
    data = msg.serialize()
    print(f"\n  Serialized length: {len(data)} bytes")
    print(f"  First 86 bytes (header): {data[:86].hex()}")
    print(f"  Remaining (body): {data[86:].hex()}")
    
    assert len(data) == HEADER_SIZE + len(msg.body), f"Length error: {len(data)}"
    
    # Deserialize
    parsed = parse_op_return(data)
    
    assert parsed is not None, "Deserialization failed"
    assert parsed.msg_type == msg.msg_type, f"Type error: {parsed.msg_type}"
    assert parsed.sender_hash160 == msg.sender_hash160, "Sender error"
    assert parsed.receiver_hash160 == msg.receiver_hash160, "Receiver error"
    assert parsed.body == msg.body, f"Body error: {parsed.body}"
    
    print("  PASSED")


def test_build_op_return():
    """Test OP_RETURN building"""
    print("\n" + "=" * 60)
    print("Test 6: OP_RETURN Building")
    
    sender_hash160 = address_to_hash160("1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD")
    receiver_hash160 = address_to_hash160("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
    body = b"Test message for MingChat"
    
    op_return_hex = build_op_return(
        MsgType.CHAT,
        sender_hash160,
        receiver_hash160,
        body,
        timestamp=1700000000
    )
    
    print(f"  OP_RETURN hex: {op_return_hex}")
    print(f"  Length: {len(op_return_hex)} chars ({len(op_return_hex)//2} bytes)")
    
    assert len(op_return_hex) == (HEADER_SIZE + len(body)) * 2, f"Length error"
    
    # Parse
    data = bytes.fromhex(op_return_hex)
    msg = parse_op_return(data)
    
    assert msg is not None, "Parsing failed"
    assert msg.msg_type == MsgType.CHAT, "Type error"
    assert msg.get_body_text() == body.decode(), "Body error"
    
    print("  PASSED")


def test_address_hash160():
    """Test address to Hash160 conversion"""
    print("\n" + "=" * 60)
    print("Test 7: Address to Hash160 Conversion")
    
    test_addresses = [
        "1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD",
        "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
        "1CounterpartyXXXXXXXXXXXXXXXUWLpVr",  # 21-byte address
    ]
    
    for addr in test_addresses:
        h160 = address_to_hash160(addr)
        recovered = hash160_to_address(h160)
        print(f"  {addr[:20]}... -> {h160.hex()[:20]}... -> {recovered[:20]}...")
        assert recovered == addr, f"Address conversion error: {addr} != {recovered}"
    
    print("  PASSED")


def test_broadcast_type():
    """Test broadcast address"""
    print("\n" + "=" * 60)
    print("Test 8: Broadcast Message")
    
    sender_hash160 = address_to_hash160("1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD")
    broadcast_hash160 = b"\x00" * 20
    
    op_return_hex = build_op_return(
        MsgType.BROADCAST,
        sender_hash160,
        broadcast_hash160,
        b"Hello everyone!",
        timestamp=1700000000
    )
    
    data = bytes.fromhex(op_return_hex)
    msg = parse_op_return(data)
    
    assert msg is not None, "Parsing failed"
    assert msg.msg_type == MsgType.BROADCAST, "Type error"
    assert msg.receiver_hash160 == broadcast_hash160, "Receiver should be broadcast address"
    
    print(f"  Broadcast message built")
    print(f"  Receiver: {msg.receiver_hash160.hex()}")
    print("  PASSED")


def test_all_msg_types():
    """Test all message types"""
    print("\n" + "=" * 60)
    print("Test 9: All Message Types")
    
    sender_hash160 = address_to_hash160("1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD")
    receiver_hash160 = address_to_hash160("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
    body = b"Test"
    
    msg_types = [
        MsgType.CHAT, MsgType.RPC_REQ, MsgType.RPC_RESP,
        MsgType.ACK, MsgType.BROADCAST,
        MsgType.PUBLISH, MsgType.BID, MsgType.ASSIGN,
        MsgType.PROGRESS, MsgType.DELIVER, MsgType.ACCEPT,
        MsgType.REJECT, MsgType.ARBITRATE, MsgType.SETTLE,
        MsgType.CANCEL, MsgType.DID_REGISTER, MsgType.DID_UPDATE,
        MsgType.DID_REVOKE
    ]
    
    print("  Message type list:")
    for mtype in msg_types:
        op_hex = build_op_return(mtype, sender_hash160, receiver_hash160, body)
        data = bytes.fromhex(op_hex)
        parsed = parse_op_return(data)
        
        status = "PASS" if parsed and parsed.msg_type == mtype else "FAIL"
        print(f"    [{status}] {mtype.name} (0x{mtype.value:02X})")
        
        assert parsed is not None, f"Parsing failed: {mtype.name}"
        assert parsed.msg_type == mtype, f"Type mismatch: {mtype.name}"
    
    print("  PASSED")


def test_wif_operations():
    """Test WIF private key operations"""
    print("\n" + "=" * 60)
    print("Test 10: WIF Private Key Operations")
    
    # Using testnet WIF for testing
    test_wif = TEST_WIF
    
    privkey = wif_to_privkey(test_wif)
    recovered_wif = privkey_to_wif(privkey, "mainnet")
    address = privkey_to_address(privkey, "mainnet")
    
    print(f"  WIF: {test_wif}")
    print(f"  Privkey(hex): {privkey.hex()}")
    print(f"  Recovered WIF: {recovered_wif}")
    print(f"  Address: {address}")
    
    assert recovered_wif == test_wif, "WIF recovery error"
    assert address == TEST_ADDRESS, f"Address error: {address}"
    
    print("  PASSED")


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("MingChat v0.3 Protocol Tests")
    print("=" * 60)
    
    tests = [
        test_protocol_magic,
        test_header_size,
        test_generate_privkey,
        test_encode_decode_header,
        test_message_serialize,
        test_build_op_return,
        test_address_hash160,
        test_broadcast_type,
        test_all_msg_types,
        test_wif_operations,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"\n  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Tests Complete: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
