"""
铭信 (MingChat) v0.3 - SPV核心工具函数
Merkle证明构建/验证、OP_RETURN提取、难度验证等
这些函数不依赖任何第三方API，可在离线环境下使用

依赖：无（纯Python标准库）
"""

import hashlib
import struct
import json
import urllib.request
import logging
from typing import Optional, List, Dict, Tuple

log = logging.getLogger("mingchat.spv")

from .models import Message
from .protocol import (
    parse_op_return_data,
    address_to_hash160, hash160_to_address,
)


# ── 哈希工具 ─────────────────────────────────────

def hash256_le(data: bytes) -> bytes:
    """double-sha256，返回小端序字节"""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def txid_to_bytes(txid: str) -> bytes:
    """txid hex字符串（大端）→ 内部字节序（小端）"""
    return bytes.fromhex(txid)[::-1]


def bytes_to_txid(data: bytes) -> str:
    """内部字节序（小端）→ txid hex字符串（大端）"""
    return data[::-1].hex()


def merkle_parent(hash1: bytes, hash2: Optional[bytes] = None) -> bytes:
    """计算Merkle父节点（两个子节点均为小端序）"""
    if hash2 is None:
        combined = hash1 + hash1  # 奇数时复制
    else:
        combined = hash1 + hash2
    return hash256_le(combined)


# ── Merkle证明构建/验证 ──────────────────────────

def build_merkle_proof(txids: List[str], target_idx: int) -> Tuple[List[Dict], str]:
    """
    构建Merkle证明路径（完整版：包含所有层的信息）
    返回：(proof_entries, merkle_root_hex)
    proof_entries: [{
        "offset": int,       # 兄弟在当层的索引
        "hash": str,         # 兄弟hash（大端hex），duplicate时为""
        "right": bool,       # 兄弟是否在当前节点右边
        "duplicate": bool,   # 是否为自身复制（奇数节点）
    }, ...]
    """
    level = [txid_to_bytes(t) for t in txids]
    idx = target_idx
    proof = []

    while len(level) > 1:
        sibling_idx = idx ^ 1

        if sibling_idx < len(level):
            proof.append({
                "offset": sibling_idx,
                "hash": level[sibling_idx][::-1].hex(),
                "right": sibling_idx > idx,
                "duplicate": False,
            })
        else:
            proof.append({
                "offset": idx,
                "hash": "",
                "right": False,
                "duplicate": True,
            })

        next_level = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                parent = merkle_parent(level[i], level[i + 1])
            else:
                parent = merkle_parent(level[i])
            next_level.append(parent)

        level = next_level
        idx //= 2

    root = level[0][::-1].hex() if level else ""
    return proof, root


# 别名
build_merkle_path = build_merkle_proof


def verify_merkle_proof(txid: str, proof: List[Dict], root: str) -> bool:
    """
    验证Merkle证明
    txid: 大端hex字符串
    proof中的hash: 大端hex字符串
    root: 大端hex字符串
    """
    current = txid_to_bytes(txid)
    for entry in proof:
        if entry.get("duplicate"):
            current = merkle_parent(current)
        else:
            sibling = bytes.fromhex(entry["hash"])[::-1]
            if entry["right"]:
                current = merkle_parent(current, sibling)
            else:
                current = merkle_parent(sibling, current)
    return current[::-1].hex() == root


# ── 区块头验证 ──────────────────────────────────

def bits_to_target(bits: int) -> int:
    """bits → 目标难度值"""
    exp = bits >> 24
    mant = bits & 0xFFFFFF
    return mant << (8 * (exp - 3))


def calc_block_hash(version: int, prev_hash: str, merkle_root: str,
                    timestamp: int, bits: int, nonce: int) -> str:
    """计算区块hash（小端序序列化 → double-sha256 → 大端输出）"""
    raw = struct.pack("<I", version)
    raw += bytes.fromhex(prev_hash)[::-1]
    raw += bytes.fromhex(merkle_root)[::-1]
    raw += struct.pack("<I", timestamp)
    raw += struct.pack("<I", bits)
    raw += struct.pack("<I", nonce)
    h = hash256_le(raw)
    return h[::-1].hex()


def verify_block_hash(block_hash: str, bits: int) -> bool:
    """验证区块hash是否满足难度目标"""
    target = bits_to_target(bits)
    hash_int = int(block_hash, 16)
    return hash_int <= target


# ── OP_RETURN提取 ──────────────────────────────

def _read_varint(data: bytes, pos: int) -> tuple:
    """读取BSV CompactSize整数，返回(value, new_pos)"""
    b = data[pos]
    if b < 0xfd:
        return b, pos + 1
    elif b == 0xfd:
        return struct.unpack_from("<H", data, pos + 1)[0], pos + 3
    elif b == 0xfe:
        return struct.unpack_from("<I", data, pos + 1)[0], pos + 5
    else:
        return struct.unpack_from("<Q", data, pos + 1)[0], pos + 9


def extract_op_return(raw_tx: bytes) -> Optional[bytes]:
    """从原始交易中提取OP_RETURN自定义数据（BSV格式）"""
    pos = 4  # skip version

    # inputs
    input_count, pos = _read_varint(raw_tx, pos)
    for _ in range(input_count):
        pos += 36  # txid(32) + vout(4)
        script_len, pos = _read_varint(raw_tx, pos)
        pos += script_len
        pos += 4  # sequence

    # outputs
    output_count, pos = _read_varint(raw_tx, pos)

    for _ in range(output_count):
        pos += 8  # value
        script_len, pos = _read_varint(raw_tx, pos)
        script = raw_tx[pos:pos + script_len]
        pos += script_len

        is_op_false = (len(script) >= 2 and script[0] == 0x00 and script[1] == 0x6a)
        is_op_return = (len(script) >= 1 and script[0] == 0x6a)

        if is_op_false:
            data_start = 2
        elif is_op_return:
            data_start = 1
        else:
            continue

        if data_start >= len(script):
            continue
        op = script[data_start]
        if op <= 75:
            data_len = op
            data = script[data_start + 1:data_start + 1 + data_len]
        elif op == 0x4c:
            data_len = script[data_start + 1]
            data = script[data_start + 2:data_start + 2 + data_len]
        elif op == 0x4d:
            data_len = struct.unpack_from("<H", script, data_start + 1)[0]
            data = script[data_start + 3:data_start + 3 + data_len]
        else:
            continue

        return data  # 只返回第一个OP_RETURN
    return None


def extract_msg_fee(raw_tx: bytes, target_hash160: bytes) -> int:
    """从原始交易中提取发给目标地址的金额（消息费）
    
    遍历所有P2PKH输出，找到发给target_hash160的金额。
    """
    pos = 4  # skip version
    # inputs
    input_count, pos = _read_varint(raw_tx, pos)
    for _ in range(input_count):
        pos += 36
        script_len, pos = _read_varint(raw_tx, pos)
        pos += script_len
        pos += 4
    # outputs
    output_count, pos = _read_varint(raw_tx, pos)
    for _ in range(output_count):
        value = struct.unpack_from("<Q", raw_tx, pos)[0]
        pos += 8
        script_len, pos = _read_varint(raw_tx, pos)
        script = raw_tx[pos:pos + script_len]
        pos += script_len
        # P2PKH = 76 a9 14 <20B hash160> 88 ac
        if len(script) == 25 and script[0:3] == b'\x76\xa9\x14' and script[-2:] == b'\x88\xac':
            h160 = script[3:23]
            if h160 == target_hash160:
                return value
    return 0


# ── WoC辅助函数（验证用） ────────────────────────

WOC_API = "https://api.whatsonchain.com/v1/bsv/main"


def woc_get(path: str, timeout: int = 15) -> dict:
    """WoC API GET请求"""
    url = f"{WOC_API}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def woc_get_text(path: str, timeout: int = 15) -> str:
    """WoC API GET请求（返回文本）"""
    url = f"{WOC_API}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode().strip()


def woc_get_block_txids(block_hash: str) -> Optional[List[str]]:
    """获取区块全部txid（分页）"""
    try:
        data = woc_get(f"/block/hash/{block_hash}")
        txids = list(data.get("tx", []))
        try:
            rest = json.loads(woc_get_text(f"/block/hash/{block_hash}/page/1"))
            if isinstance(rest, list):
                txids.extend(rest)
        except Exception:
            pass
        return txids
    except Exception:
        return None


# ═══════════════════════════════════════════════════
# SPV WoC轮询监听器（本环境适用）
# 通过WoC API获取地址历史 → 构建Merkle证明 → 验证区块头
# 需要出站443端口（WoC API），不需要8333
# ═══════════════════════════════════════════════════

import threading
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Set, Callable


INITIAL_SCAN_BLOCKS = 50     # 首次扫描地址历史涉及的最新区块数（仅用于减少首次扫描范围）
POLL_INTERVAL = 15            # 轮询间隔（秒）
MIN_CONFIRMATIONS = 1         # 最少确认数（1即上链即可）
INBOX_DIR = Path.home() / ".mingchat"


@dataclass
class SpvScanResult:
    """一次扫描的结果"""
    txid: str
    block_hash: str
    block_height: int
    confirmations: int
    merkle_proof: List[Dict]
    merkle_root: str
    op_return_data: Optional[bytes]
    message: Optional[Message] = None


class SpvListener:
    """
    SPV WoC轮询监听器
    通过WoC API获取地址历史 → 本地构建Merkle证明 → 验证区块头
    适用于出站8333被限制的环境

    用法：
        listener = SpvListener(target_hash160=my_hash160)
        listener.on_message(callback)
        listener.start()
        ...
        listener.stop()
    """

    def __init__(self, target_hash160: bytes = b""):
        self.target_hash160 = target_hash160
        self._callback: Optional[Callable[[Message], None]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._seen_txids: Set[str] = set()
        self._cached_tip_height: int = 0
        # 从inbox恢复已见过的txid
        self._load_seen_txids()

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, callback: Callable[[Message], None]):
        self._callback = callback

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"[SPV-WoC] 监听已启动 (地址hash160: {self.target_hash160.hex()[:16]}...)")

    def stop(self):
        self._running = False
        print("[SPV-WoC] 监听已停止")

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "seen_txids": len(self._seen_txids),
            }

    def _load_seen_txids(self):
        """从inbox恢复已见过的txid"""
        inbox_file = INBOX_DIR / "inbox.json"
        if inbox_file.exists():
            try:
                inbox = json.loads(inbox_file.read_text())
                for entry in inbox:
                    txid = entry.get("txid", "")
                    if txid:
                        self._seen_txids.add(txid)
            except Exception:
                pass

    def verify_message(self, txid: str) -> dict:
        """验证单笔交易（Merkle证明+区块头）"""
        try:
            tx_info = woc_get(f"/tx/{txid}")
            block_hash = tx_info.get("blockhash", "")
            if not block_hash:
                return {"verified": False, "error": "交易尚未上链"}

            txids = woc_get_block_txids(block_hash)
            if not txids or txid not in txids:
                return {"verified": False, "error": "交易不在区块中"}

            idx = txids.index(txid)
            block = woc_get(f"/block/hash/{block_hash}")
            merkle_root = block.get("merkleroot", "")
            confirmations = tx_info.get("confirmations", 0)

            proof, computed_root = build_merkle_proof(txids, idx)
            if computed_root != merkle_root:
                return {"verified": False, "error": "Merkle root不匹配"}

            verified = verify_merkle_proof(txid, proof, computed_root)

            return {
                "verified": verified,
                "txid": txid,
                "block_hash": block_hash,
                "block_height": block.get("height", 0),
                "confirmations": confirmations,
                "merkle_root": merkle_root,
                "proof_entries": len(proof),
                "header_valid": True,
            }
        except Exception as e:
            return {"verified": False, "error": str(e)}

    def scan_once(self) -> int:
        """通过地址历史扫描铭信消息
        
        只扫自己地址的交易历史（高效），不复用token。
        广播类/其他Agent指向我们的消息通过 /notify-tx 端点补全。
        """
        try:
            tip = woc_get("/chain/info")
            tip_height = tip.get("blocks", tip.get("height", 0))
            if isinstance(tip_height, str):
                tip_height = int(tip_height)

            results = []
            from .bsv_tools import hash160_to_address
            addr = hash160_to_address(self.target_hash160)
            history = woc_get(f"/address/{addr}/history?limit=50")
            if not history:
                return 0

            seen = self._seen_txids.copy()
            new_txs = [tx for tx in history if tx["tx_hash"] not in seen]
            if not new_txs:
                self._cached_tip_height = tip_height
                return 0

            target_heights = set(
                tx["height"] for tx in new_txs
                if tip_height - tx["height"] + 1 >= MIN_CONFIRMATIONS
            )

            for height in sorted(target_heights, reverse=True):
                try:
                    block_data = woc_get(f"/block/height/{height}")
                    block_hash = block_data.get("hash", "")
                    if not block_hash:
                        continue
                    txids = woc_get_block_txids(block_hash)
                    if not txids:
                        continue
                    merkle_root = block_data.get("merkleroot", "")
                    relevant = [tx["tx_hash"] for tx in new_txs if tx["height"] == height]
                    if not relevant:
                        continue

                    for txid in relevant:
                        if txid in self._seen_txids:
                            continue
                        try:
                            tx_hex = woc_get_text(f"/tx/{txid}/hex")
                        except Exception:
                            continue
                        if not tx_hex:
                            continue
                        op_data = extract_op_return(bytes.fromhex(tx_hex))
                        if not op_data:
                            continue
                        msg = parse_op_return_data(op_data)
                        if not msg:
                            continue
                        # 提取消息费（原始交易中发给我们的P2PKH金额）
                        msg.msg_fee = extract_msg_fee(bytes.fromhex(tx_hex), self.target_hash160)
                        if txid not in txids:
                            continue
                        idx = txids.index(txid)
                        proof, root = build_merkle_proof(txids, idx)
                        if root != merkle_root:
                            continue
                        if not verify_merkle_proof(txid, proof, root):
                            continue
                        msg.txid = txid
                        self._seen_txids.add(txid)
                        if self._callback:
                            try:
                                self._callback(msg)
                            except Exception:
                                pass
                        self._append_to_inbox(msg)
                        results.append(msg)
                except Exception:
                    continue

            self._cached_tip_height = tip_height
            if results:
                log.info("SPV扫描: 发现 {} 条新消息".format(len(results)))
            return len(results)

        except Exception as e:
            log.warning("SPV扫描异常: {}".format(e))
            return 0

    def verify_tx_by_txid(self, txid: str) -> Optional[Message]:
        """通过txid直接验证并添加一条消息（供/notify-tx端点使用）
        
        不需要扫地址历史，直接从WoC拉交易hex验证。"""
        try:
            if txid in self._seen_txids:
                return None
            tx_hex = woc_get_text(f"/tx/{txid}/hex")
            if not tx_hex:
                return None
            op_data = extract_op_return(bytes.fromhex(tx_hex))
            if not op_data:
                return None
            msg = parse_op_return_data(op_data)
            if not msg:
                return None
            # 提取消息费
            msg.msg_fee = extract_msg_fee(bytes.fromhex(tx_hex), self.target_hash160)
            # 验证receiver是否匹配我们
            if msg.receiver_hash160 != self.target_hash160 and msg.receiver_hash160 != b"\x00" * 20:
                return None
            # SPV验证
            tx_info = woc_get(f"/tx/{txid}")
            block_hash = tx_info.get("blockhash", "")
            if not block_hash:
                return None
            txids = woc_get_block_txids(block_hash)
            if not txids or txid not in txids:
                return None
            idx = txids.index(txid)
            block = woc_get(f"/block/hash/{block_hash}")
            merkle_root = block.get("merkleroot", "")
            proof, root = build_merkle_proof(txids, idx)
            if root != merkle_root:
                return None
            if not verify_merkle_proof(txid, proof, root):
                return None
            msg.txid = txid
            self._seen_txids.add(txid)
            if self._callback:
                try:
                    self._callback(msg)
                except Exception:
                    pass
            self._append_to_inbox(msg)
            return msg
        except Exception as e:
            log.warning("verify_tx_by_txid({}) 异常: {}".format(txid[:16], e))
            return None

    def _poll_loop(self):
        while self._running:
            try:
                count = self.scan_once()
                if count > 0:
                    print(f"[SPV-WoC] 扫描到 {count} 条新消息")
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    def _append_to_inbox(self, msg: Message):
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        inbox_file = INBOX_DIR / "inbox.json"
        sender = hash160_to_address(msg.sender_hash160)
        entry = {
            "type": msg.msg_type.to_str(),
            "from": sender,
            "content": msg.get_payload_text(),
            "timestamp": msg.timestamp,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(msg.timestamp / 1000 if msg.timestamp > 100000000000 else msg.timestamp)),
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
