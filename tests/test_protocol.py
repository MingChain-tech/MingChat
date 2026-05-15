"""
铭信 v0.3 协议层测试
覆盖: v0.3序列化/反序列化、v0.2向后兼容、任务字段、审计字段
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
import hashlib
import time

from mingchat import Message, MsgType
from mingchat.models import TaskFields, AuditFields, AuditFlags, TaskOp, TaskStatus
from mingchat.protocol import (
    serialize_message_v0_3, deserialize_message_v0_3,
    parse_op_return_data,
    PROTOCOL_MAGIC, HEADER_SIZE_V0_2, HEADER_SIZE_V0_3,
)


class TestProtocolV03(unittest.TestCase):
    """v0.3 协议编解码测试"""

    def setUp(self):
        self.sender = bytes.fromhex("f595cd85067a6c8aa0423bd8d7e221c2e07b5ba7")
        self.receiver = bytes.fromhex("a495cd85067a6c8aa0423bd8d7e221c2e07b5ba8")
        self.payload = b"Hello MingChat v0.3!"
        self.timestamp = int(time.time() * 1000)

    def test_serialize_basic_text(self):
        """基本TEXT消息序列化"""
        msg = Message(
            msg_type=MsgType.TEXT,
            sender_hash160=self.sender,
            receiver_hash160=self.receiver,
            timestamp=self.timestamp,
            payload=self.payload,
        )
        data = serialize_message_v0_3(msg)
        
        # 总长度
        self.assertEqual(len(data), HEADER_SIZE_V0_3 + len(self.payload))
        
        # 协议标识
        magic = int.from_bytes(data[0:4], 'big')
        self.assertEqual(magic, PROTOCOL_MAGIC)
        self.assertEqual(data[0:4], b'MCH\x00')
        
        # 版本和类型
        self.assertEqual(data[4], 0x03)
        self.assertEqual(data[5], MsgType.TEXT.value)
        
        # 发送方/接收方
        self.assertEqual(data[6:26], self.sender)
        self.assertEqual(data[26:46], self.receiver)
        
        # 任务字段（默认空）
        self.assertEqual(data[54:58], b'\x00\x00\x00\x00')
        
        # 审计字段（默认空）
        self.assertEqual(data[58:62], b'\x00\x00\x00\x00')
        self.assertEqual(data[62:90], b'\x00' * 28)
        
        # 消息体哈希
        expected_hash = hashlib.sha256(self.payload).digest()
        self.assertEqual(data[90:122], expected_hash)
        
        # 消息体
        self.assertEqual(data[HEADER_SIZE_V0_3:], self.payload)

    def test_deserialize_basic_text(self):
        """基本TEXT消息反序列化"""
        msg = Message(
            msg_type=MsgType.TEXT,
            sender_hash160=self.sender,
            receiver_hash160=self.receiver,
            timestamp=self.timestamp,
            payload=self.payload,
        )
        data = serialize_message_v0_3(msg)
        msg2 = deserialize_message_v0_3(data)
        
        self.assertIsNotNone(msg2)
        self.assertEqual(msg2.msg_type, MsgType.TEXT)
        self.assertEqual(msg2.sender_hash160, self.sender)
        self.assertEqual(msg2.receiver_hash160, self.receiver)
        self.assertEqual(msg2.timestamp, self.timestamp)
        self.assertEqual(msg2.payload, self.payload)
        self.assertEqual(msg2.get_payload_text(), "Hello MingChat v0.3!")

    def test_serialize_with_task_fields(self):
        """带任务字段的消息序列化"""
        msg = Message(
            msg_type=MsgType.TASK_PUBLISH,
            sender_hash160=self.sender,
            receiver_hash160=self.receiver,
            timestamp=self.timestamp,
            payload=b'{"task_type":"analysis"}',
            task=TaskFields(task_op=TaskOp.PUBLISH, task_id_lo=b'\x01\x02\x03'),
        )
        data = serialize_message_v0_3(msg)
        
        # 任务字段
        self.assertEqual(data[54], TaskOp.PUBLISH.value)  # op
        self.assertEqual(data[55:58], b'\x01\x02\x03')     # id_lo
        
        # 反序列化验证
        msg2 = deserialize_message_v0_3(data)
        self.assertEqual(msg2.task.task_op, TaskOp.PUBLISH)
        self.assertEqual(msg2.task.task_id_lo, b'\x01\x02\x03')
        self.assertEqual(msg2.msg_type, MsgType.TASK_PUBLISH)

    def test_serialize_with_audit_flags(self):
        """带审计标志位的消息"""
        msg = Message(
            msg_type=MsgType.TEXT,
            sender_hash160=self.sender,
            receiver_hash160=self.receiver,
            timestamp=self.timestamp,
            payload=b"secret msg",
            audit=AuditFields(
                scope_hash=hashlib.sha256(b"scope").digest()[:16],
                escrow_ref=b'\x00' * 8,
                flags=AuditFlags.ENCRYPTED | AuditFlags.HAS_DID,
            ),
        )
        data = serialize_message_v0_3(msg)
        
        # 审计字段
        self.assertEqual(data[58:74], hashlib.sha256(b"scope").digest()[:16])
        self.assertEqual(data[82:86], b'\x00\x00\x00\x03')  # flags=1|2=3, 在82-85
        
        # 反序列化验证
        msg2 = deserialize_message_v0_3(data)
        self.assertEqual(msg2.audit.flags, AuditFlags.ENCRYPTED | AuditFlags.HAS_DID)
        self.assertEqual(msg2.audit.scope_hash[:4].hex(), hashlib.sha256(b"scope").digest()[:4].hex())

    def test_parse_op_return(self):
        """parse_op_return_data 快捷函数"""
        msg = Message(payload=b"test", sender_hash160=self.sender, receiver_hash160=self.receiver)
        data = serialize_message_v0_3(msg)
        msg2 = parse_op_return_data(data)
        self.assertIsNotNone(msg2)
        self.assertEqual(msg2.payload, b"test")

    def test_roundtrip_all_msg_types(self):
        """所有消息类型序列化/反序列化闭环"""
        types = [
            MsgType.TEXT, MsgType.RPC_REQUEST, MsgType.RPC_RESPONSE,
            MsgType.NOTIFICATION, MsgType.HEARTBEAT, MsgType.ERROR,
            MsgType.HELLO, MsgType.TASK_PUBLISH, MsgType.TASK_BID,
            MsgType.TASK_DELIVER, MsgType.TASK_SETTLE, MsgType.TASK_DISPUTE,
            MsgType.DID_REGISTER, MsgType.DID_UPDATE, MsgType.DID_REVOKE,
        ]
        for mt in types:
            msg = Message(msg_type=mt, payload=b"x", sender_hash160=self.sender, receiver_hash160=self.receiver)
            data = serialize_message_v0_3(msg)
            msg2 = parse_op_return_data(data)
            self.assertEqual(msg2.msg_type, mt, f"Failed for {mt.name}")

    def test_payload_utf8(self):
        """中文UTF-8消息体"""
        msg = Message(
            payload="铭信Phase1 首条链上消息! 🎉".encode("utf-8"),
            sender_hash160=self.sender,
            receiver_hash160=self.receiver,
        )
        data = serialize_message_v0_3(msg)
        msg2 = deserialize_message_v0_3(data)
        self.assertEqual(msg2.get_payload_text(), "铭信Phase1 首条链上消息! 🎉")


class TestCompatV02(unittest.TestCase):
    """v0.2 向前兼容测试 — v0.3解析器读v0.2消息"""

    V2_SENDER = bytes.fromhex("f595cd85067a6c8aa0423bd8d7e221c2e07b5ba7")
    V2_RECEIVER = bytes.fromhex("a495cd85067a6c8aa0423bd8d7e221c2e07b5ba8")

    def _build_v2_message(self, body: bytes, msg_type: int = 0x01) -> bytes:
        """构造一个v0.2风格的86B头消息"""
        header = bytearray(HEADER_SIZE_V0_2)
        header[0:4] = PROTOCOL_MAGIC.to_bytes(4, 'big')
        header[4] = 0x02  # v0.2
        header[5] = msg_type
        header[6:26] = self.V2_SENDER
        header[26:46] = self.V2_RECEIVER
        ts = int(time.time() * 1000)
        header[46:54] = ts.to_bytes(8, 'big')
        body_hash = hashlib.sha256(body).digest()
        header[54:86] = body_hash
        return bytes(header) + body

    def test_parse_v2_text(self):
        """v0.3解析器可读v0.2 TEXT消息"""
        data = self._build_v2_message(b"Hello from v0.2!")
        msg = parse_op_return_data(data)
        self.assertIsNotNone(msg, "v0.3解析器应能解析v0.2消息")
        self.assertEqual(msg.msg_type, MsgType.TEXT)
        self.assertEqual(msg.get_payload_text(), "Hello from v0.2!")
        self.assertEqual(msg.version, 0x02)
        # v0.2消息的任务/审计字段应为空
        self.assertTrue(msg.task.is_empty(), "v0.2消息的任务字段应为空")
        self.assertTrue(msg.audit.is_empty(), "v0.2消息的审计字段应为空")

    def test_parse_v2_rpc(self):
        """v0.3解析器可读v0.2 RPC消息"""
        data = self._build_v2_message(b'{"method":"ping"}', msg_type=0x02)
        msg = parse_op_return_data(data)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.msg_type, MsgType.RPC_REQUEST)

    def test_v3_writer_emits_v3(self):
        """v0.3写入器写入version=0x03"""
        msg = Message(payload=b"hi", sender_hash160=self.V2_SENDER, receiver_hash160=self.V2_RECEIVER)
        data = serialize_message_v0_3(msg)
        self.assertEqual(data[4], 0x03)
        self.assertEqual(len(data), HEADER_SIZE_V0_3 + 2)

    def test_v2_skip_unknown_version(self):
        """模拟v0.2解析器遇到v0.3消息——version≠0x02，旧版解析器不会解析"""
        # 用v0.3格式序列化
        msg = Message(payload=b"new", sender_hash160=self.V2_SENDER, receiver_hash160=self.V2_RECEIVER)
        data = serialize_message_v0_3(msg)
        self.assertEqual(data[4], 0x03)
        # v0.3解析器当然能解析
        parsed = parse_op_return_data(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.version, 0x03)
        self.assertEqual(parsed.get_payload_text(), "new")

    def test_v2_truncated_header(self):
        """v0.2截断头应被拒绝"""
        data = self._build_v2_message(b"x")
        truncated = data[:50]  # 不足86B
        msg = parse_op_return_data(truncated)
        self.assertIsNone(msg)


class TestEdgeCases(unittest.TestCase):
    """边界情况测试"""

    def test_empty_payload(self):
        """空消息体"""
        msg = Message(payload=b"", sender_hash160=b'\x11' * 20, receiver_hash160=b'\x22' * 20)
        data = serialize_message_v0_3(msg)
        self.assertEqual(len(data), HEADER_SIZE_V0_3)
        msg2 = deserialize_message_v0_3(data)
        self.assertEqual(msg2.payload, b"")

    def test_max_size_payload(self):
        """接近最大尺寸的消息体"""
        payload = b"x" * 3800  # 约3.7KB
        msg = Message(payload=payload, sender_hash160=b'\x11' * 20, receiver_hash160=b'\x22' * 20)
        data = serialize_message_v0_3(msg)
        self.assertEqual(len(data), HEADER_SIZE_V0_3 + 3800)

    def test_invalid_magic(self):
        """错误的协议标识"""
        msg = Message(payload=b"x", sender_hash160=b'\x11' * 20, receiver_hash160=b'\x22' * 20)
        data = serialize_message_v0_3(msg)
        data = bytearray(data)
        data[0:4] = b'AAAA'
        msg2 = parse_op_return_data(bytes(data))
        self.assertIsNone(msg2)

    def test_to_dict(self):
        """to_dict输出格式"""
        msg = Message(
            msg_type=MsgType.NOTIFICATION,
            payload=b"alert",
            sender_hash160=self._h160(1),
            receiver_hash160=self._h160(2),
            task=TaskFields(task_op=TaskOp.PUBLISH, task_id_lo=b'\xaa\xbb\xcc'),
            audit=AuditFields(flags=AuditFlags.NEEDS_APPROVAL),
        )
        d = msg.to_dict()
        self.assertEqual(d["msg_type"], "NOTIFICATION")
        self.assertEqual(d["task_op"], TaskOp.PUBLISH.value)
        self.assertEqual(d["audit_flags"], AuditFlags.NEEDS_APPROVAL)
        self.assertEqual(d["payload"], "alert")

    def _h160(self, val: int) -> bytes:
        return bytes([val] * 20)


if __name__ == "__main__":
    unittest.main()
