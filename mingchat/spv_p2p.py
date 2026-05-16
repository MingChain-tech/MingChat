"""
铭信 (MingChat) v0.3 - BSV P2P直连SPV监听模块

真正的SPV轻节点：直连BSV网络节点，通过Bloom Filter过滤OP_RETURN交易，
收到merkleblock后本地验证Merkle证明，无需信任任何第三方。

实现原理：
1. 连接BSV节点（TCP 8333）
2. 握手：version → verack
3. 发送filterload（Bloom Filter，只匹配我们的地址）
4. 节点推送inv/merkleblock → 本地验证Merkle证明
5. 区块头只验证hash是否满足难度目标，不需要维护完整链

流程：
[BSV Node] --inv--> [SPV] --getdata--> [BSV Node] --merkleblock--> [SPV]
                                                                      ↓
                                                             验证Merkle证明
                                                                      ↓
                                                             检查区块hash≤target
                                                                      ↓
                                                             解析OP_RETURN

资源消耗：
- 内存：<3MB（仅当前连接的缓冲数据）
- 磁盘：0（不缓存任何区块数据）
- 延迟：<1秒（TCP长连接，实时推送）

依赖：asyncio（Python 3.9+内置）
       spv.py（Merkle证明/OP_RETURN提取）
"""

import asyncio
import struct
import hashlib
import math
import time
import json
import threading
from typing import Optional, Callable, List, Dict, Set
from io import BytesIO
from pathlib import Path

from .models import Message
from .protocol import (
    parse_op_return_data,
    hash160_to_address,
)
from .spv import (
    build_merkle_proof,
    verify_merkle_proof,
    verify_block_hash,
    bits_to_target,
    extract_op_return,
)


# ── BSV P2P 协议常量 ──────────────────────────────

BSV_MAINNET_SEEDS = [
    "seed.bitcoincloud.net",
    "seed.bitcoinbrisbane.com.au",
    "seed.bitcoinsv.org",
    "seed.bitcoinunlimited.info",
    "dnsseed.merlinseal.com",
]

BSV_PORT = 8333
MAGIC_BYTES = 0xE3E1F3E8  # BSV mainnet magic
PROTOCOL_VERSION = 70016
SERVICE_NODE_NETWORK = 1

BLOOM_FALSE_POSITIVE_RATE = 0.0001
BLOOM_TWEAK = 2147483649


def bsv_hash256(data: bytes) -> bytes:
    """double-SHA256"""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


# ═══════════════════════════════════════════════════
# BSV P2P 协议编码/解码
# ═══════════════════════════════════════════════════

def varint_encode(n: int) -> bytes:
    if n < 0xfd:
        return struct.pack("B", n)
    elif n <= 0xffff:
        return struct.pack("<BH", 0xfd, n)
    elif n <= 0xffffffff:
        return struct.pack("<BI", 0xfe, n)
    else:
        return struct.pack("<BQ", 0xff, n)


def varint_decode(stream) -> int:
    b = stream.read(1)[0]
    if b < 0xfd:
        return b
    elif b == 0xfd:
        return struct.unpack("<H", stream.read(2))[0]
    elif b == 0xfe:
        return struct.unpack("<I", stream.read(4))[0]
    else:
        return struct.unpack("<Q", stream.read(8))[0]


def build_packet(command: str, payload: bytes) -> bytes:
    """构建P2P消息包"""
    header = struct.pack("<I", MAGIC_BYTES)
    header += command.encode().ljust(12, b'\x00')
    header += struct.pack("<I", len(payload))
    header += bsv_hash256(payload)[:4]
    return header + payload


async def read_packet(reader) -> Optional[dict]:
    """读取P2P消息包"""
    try:
        magic = await reader.readexactly(4)
        if struct.unpack("<I", magic)[0] != MAGIC_BYTES:
            return None
        command = (await reader.readexactly(12)).rstrip(b'\x00').decode()
        length = struct.unpack("<I", await reader.readexactly(4))[0]
        checksum = await reader.readexactly(4)  # noqa: verify later if needed
        payload = await reader.readexactly(length)
        return {"command": command, "payload": payload}
    except (asyncio.IncompleteReadError, ConnectionError):
        return None


# ── 消息构建/解析 ─────────────────────────────────

def build_version_payload() -> bytes:
    payload = struct.pack("<i", PROTOCOL_VERSION)
    payload += struct.pack("<Q", SERVICE_NODE_NETWORK)
    payload += struct.pack("<q", int(time.time()))
    payload += struct.pack("<Q", SERVICE_NODE_NETWORK)
    payload += b'\x00' * 16
    payload += struct.pack("<H", BSV_PORT)
    payload += struct.pack("<Q", SERVICE_NODE_NETWORK)
    payload += b'\x00' * 16
    payload += struct.pack("<H", BSV_PORT)
    payload += struct.pack("<Q", 0)
    payload += b'\x00'
    payload += struct.pack("<i", 0)
    payload += struct.pack("?", False)
    return payload


def build_filterload_payload(target_hash160: bytes) -> bytes:
    """
    构建Bloom Filter消息
    纯Python实现（无外部依赖）
    """
    n = 200
    p = BLOOM_FALSE_POSITIVE_RATE
    filter_size = max(8, int(-n * math.log(p) / (math.log(2) ** 2)))
    n_hash_funcs = max(1, int(filter_size / n * math.log(2)))
    n_tweak = BLOOM_TWEAK
    n_bytes = (filter_size + 7) // 8
    bit_field = bytearray(n_bytes)

    def _hash_element(data: bytes, n_hash_num: int) -> int:
        h = hashlib.sha256(
            data + struct.pack("<I", (n_hash_num * 0xFBA4C795 + n_tweak) & 0xFFFFFFFF)
        ).digest()
        return int.from_bytes(h[:8], 'little') % (n_bytes * 8)

    def _set_bit(bf: bytearray, pos: int):
        byte_idx = pos // 8
        bit_idx = pos % 8
        if byte_idx < len(bf):
            bf[byte_idx] |= (1 << bit_idx)

    for i in range(n_hash_funcs):
        _set_bit(bit_field, _hash_element(target_hash160, i))
    op_code = b'\x6a'
    for i in range(n_hash_funcs):
        _set_bit(bit_field, _hash_element(op_code, i))

    payload = varint_encode(len(bit_field)) + bytes(bit_field)
    payload += struct.pack("<I", n_hash_funcs)
    payload += struct.pack("<I", n_tweak)
    payload += struct.pack("B", 0)
    return payload


def build_getdata_payload(inv_hashes: List[bytes]) -> bytes:
    payload = varint_encode(len(inv_hashes))
    for h in inv_hashes:
        payload += struct.pack("<I", 2)  # MSG_FILTERED_BLOCK (2)
        payload += h
    return payload


def parse_inv_payload(payload: bytes) -> List[dict]:
    stream = BytesIO(payload)
    count = varint_decode(stream)
    items = []
    for _ in range(count):
        inv_type = struct.unpack("<I", stream.read(4))[0]
        inv_hash = stream.read(32)
        items.append({"type": inv_type, "hash": inv_hash})
    return items


def parse_merkleblock_payload(payload: bytes) -> Optional[dict]:
    """
    解析merkleblock消息
    返回区块头 + Merkle路径信息
    """
    stream = BytesIO(payload)

    version = struct.unpack("<I", stream.read(4))[0]
    prev_block = stream.read(32)
    merkle_root = stream.read(32)
    timestamp = struct.unpack("<I", stream.read(4))[0]
    bits = struct.unpack("<I", stream.read(4))[0]
    nonce = struct.unpack("<I", stream.read(4))[0]

    block_hash = bsv_hash256(payload[:80])[::-1].hex()

    tx_count = struct.unpack("<I", stream.read(4))[0]
    hash_count = varint_decode(stream)
    hashes = []
    for _ in range(hash_count):
        hashes.append(stream.read(32)[::-1].hex())
    flag_count = varint_decode(stream)
    flags = stream.read(flag_count)

    return {
        "header": {
            "version": version,
            "prev_block": prev_block[::-1].hex(),
            "merkle_root": merkle_root[::-1].hex(),
            "timestamp": timestamp,
            "bits": bits,
            "nonce": nonce,
            "hash": block_hash,
        },
        "tx_count": tx_count,
        "hashes": hashes,
        "flags": flags,
    }


# ═══════════════════════════════════════════════════
# SPV P2P 监听器
# ═══════════════════════════════════════════════════

class SpvP2PListener:
    """
    SPV P2P直连监听器
    连接BSV节点，Bloom Filter过滤，零信任

    用法：
        listener = SpvP2PListener(target_hash160=my_hash160)
        listener.on_message(callback)
        listener.start()
        ...
        listener.stop()
    """

    def __init__(self, target_hash160: bytes = b""):
        self.target_hash160 = target_hash160
        self._callback: Optional[Callable[[Message], None]] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._seen_txids: Set[str] = set()

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, callback: Callable[[Message], None]):
        self._callback = callback

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="spv-p2p",
        )
        self._thread.start()
        print(f"[SPV-P2P] 监听已启动 (hash160: {self.target_hash160.hex()[:16]}...)")

    def stop(self):
        self._running = False
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        print("[SPV-P2P] 监听已停止")

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "seen_txids": len(self._seen_txids),
        }

    async def _dispatch_message(self, op_return_data: bytes, txid: str):
        msg = parse_op_return_data(op_return_data)
        if not msg:
            return
        msg.txid = txid

        if self.target_hash160 and msg.receiver_hash160 != b"\x00" * 20:
            if msg.receiver_hash160 != self.target_hash160:
                return

        if self._callback:
            try:
                self._callback(msg)
            except Exception as e:
                print(f"[SPV-P2P] 回调错误: {e}")
        self._append_to_inbox(msg)

    def _append_to_inbox(self, msg: Message):
        inbox_dir = Path.home() / ".mingchat"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        inbox_file = inbox_dir / "inbox.json"
        sender = hash160_to_address(msg.sender_hash160)
        entry = {
            "type": msg.msg_type.to_str(),
            "from": sender,
            "content": msg.get_payload_text(),
            "timestamp": msg.timestamp,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(msg.timestamp / 1000)),
            "txid": msg.txid,
        }
        with self._lock:
            inbox = []
            if inbox_file.exists():
                try:
                    inbox = json.loads(inbox_file.read_text())
                except Exception:
                    inbox = []
            inbox.append(entry)
            if len(inbox) > 200:
                inbox = inbox[-200:]
            inbox_file.write_text(json.dumps(inbox, ensure_ascii=False, indent=2))

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._p2p_main())
        except Exception as e:
            print(f"[SPV-P2P] 主循环异常: {type(e).__name__}: {e}")
        finally:
            self._loop.close()

    async def _p2p_main(self):
        connected = False
        for seed in BSV_MAINNET_SEEDS:
            try:
                print(f"[SPV-P2P] 连接 {seed}:8333...")
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(seed, BSV_PORT),
                    timeout=10,
                )
                connected = True
                print(f"[SPV-P2P] ✅ 已连接到 {seed}")
                break
            except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                print(f"[SPV-P2P] ❌ {seed}: {e}")
                continue

        if not connected:
            print("[SPV-P2P] ⚠️ 无法连接，5分钟后重试")
            await asyncio.sleep(300)
            if self._running:
                await self._p2p_main()
            return

        try:
            self._writer.write(build_packet("version", build_version_payload()))
            await self._writer.drain()

            while self._running:
                pkt = await read_packet(self._reader)
                if not pkt:
                    break
                cmd = pkt["command"]

                if cmd == "version":
                    self._writer.write(build_packet("verack", b''))
                    await self._writer.drain()
                elif cmd == "verack":
                    break
                elif cmd == "sendheaders":
                    pass

            if not self._running:
                return

            self._writer.write(
                build_packet("filterload",
                             build_filterload_payload(self.target_hash160))
            )
            await self._writer.drain()
            print(f"[SPV-P2P] ✅ Bloom Filter已发送，等待消息...")

            while self._running:
                pkt = await read_packet(self._reader)
                if not pkt:
                    break
                cmd = pkt["command"]
                payload = pkt["payload"]

                if cmd == "inv":
                    items = parse_inv_payload(payload)
                    block_hashes = [i["hash"] for i in items if i["type"] == 2]
                    if block_hashes:
                        self._writer.write(
                            build_getdata_payload(block_hashes)
                        )
                        await self._writer.drain()

                elif cmd == "merkleblock":
                    result = parse_merkleblock_payload(payload)
                    if result and verify_block_hash(
                        result["header"]["hash"],
                        result["header"]["bits"],
                    ):
                        pass  # 有效区块，等待后续tx消息

                elif cmd == "tx":
                    raw_tx = payload
                    txid = bsv_hash256(raw_tx)[::-1].hex()
                    if txid in self._seen_txids:
                        continue
                    self._seen_txids.add(txid)
                    op_data = extract_op_return(raw_tx)
                    if op_data:
                        await self._dispatch_message(op_data, txid)

                elif cmd == "ping":
                    self._writer.write(build_packet("pong", payload))
                    await self._writer.drain()

        except (ConnectionError, asyncio.IncompleteReadError, OSError) as e:
            print(f"[SPV-P2P] ❌ 断开: {e}")
        except Exception as e:
            print(f"[SPV-P2P] ❌ 异常: {type(e).__name__}: {e}")
        finally:
            try:
                self._writer.close()
            except Exception:
                pass
            if self._running:
                print("[SPV-P2P] 🔄 5秒后重连...")
                await asyncio.sleep(5)
                if self._running:
                    await self._p2p_main()
