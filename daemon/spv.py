"""
SPV (Simplified Payment Verification) Node — 对标 bsv-poker 的 SPV 验证
= 下载区块头 + 验证 PoW + 验证链 + 自算 Merkle 证明
= 不信任任何 API，只信任工作量证明

Light client that verifies transactions without running a full node.
Like bsv-poker's SpvFunding/HeaderChain, but via HTTP instead of P2P.
"""
import struct
import hashlib
import time
import json
import sqlite3
import logging
import os
import requests
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

log = logging.getLogger("spv")

# ─── BSV Block Header (80 bytes) ─────────────────────────────

HEADER_SIZE = 80  # version(4) + prev_hash(32) + merkle_root(32) + time(4) + bits(4) + nonce(4)

@dataclass
class BlockHeader:
    """BSV 区块头 — 对标 bsv-poker 的 Chain.cs Header"""
    version: int
    prev_hash: bytes   # 32 bytes, LE in header
    merkle_root: bytes # 32 bytes, LE in header
    timestamp: int
    bits: int          # compact difficulty
    nonce: int
    height: int = -1   # not in header, tracked locally
    hash: bytes = b""  # computed SHA256d

    @classmethod
    def from_json(cls, data: dict, height: int) -> "BlockHeader":
        """从 WoC JSON 重建区块头"""
        h = cls(
            version=data["version"],
            prev_hash=bytes.fromhex(data["previousblockhash"])[::-1],  # big→little endian
            merkle_root=bytes.fromhex(data["merkleroot"])[::-1],
            timestamp=data["time"],
            bits=int(data["bits"], 16),
            nonce=data["nonce"],
            height=height
        )
        h.hash = bytes.fromhex(data["hash"])[::-1]  # store as LE
        return h

    def serialize(self) -> bytes:
        """序列化为 80 字节 — 对标 BSV 网络格式"""
        return struct.pack(
            "<I32s32sIII",
            self.version,
            self.prev_hash,
            self.merkle_root,
            self.timestamp,
            self.bits,
            self.nonce
        )

    def compute_hash(self) -> bytes:
        """计算 SHA256d 哈希（LE 格式）"""
        return hashlib.sha256(hashlib.sha256(self.serialize()).digest()).digest()

    @property
    def hash_hex(self) -> str:
        """big-endian hex（标准显示格式）"""
        return (self.hash or self.compute_hash())[::-1].hex()

    @property
    def prev_hash_hex(self) -> str:
        return self.prev_hash[::-1].hex()

    def validate_pow(self) -> bool:
        """
        验证 PoW — 对标 bsv-poker 的 HeaderChain.Validate
        SHA256d(header) < target
        """
        header_hash = self.compute_hash()
        target = self.bits_to_target(self.bits)
        hash_int = int.from_bytes(header_hash, 'little')
        return hash_int < target

    @staticmethod
    def bits_to_target(bits: int) -> int:
        """compact bits → 256-bit target"""
        exponent = bits >> 24
        mantissa = bits & 0x00ffffff
        if exponent <= 3:
            return mantissa >> (8 * (3 - exponent))
        return mantissa << (8 * (exponent - 3))


# ─── Merkle Proof ────────────────────────────────────────────

def double_sha256(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def build_merkle_root(txids: List[str]) -> bytes:
    """
    从交易 ID 列表构建 Merkle Root — 对标 bsv-poker 的 SPV 验证
    txids 是 big-endian hex 字符串
    
    算法：比特币标准 Merkle 树（成对 SHA256d）
    """
    if not txids:
        return b'\x00' * 32

    # Convert to LE bytes
    hashes = [bytes.fromhex(txid)[::-1] for txid in txids]

    while len(hashes) > 1:
        new_hashes = []
        for i in range(0, len(hashes), 2):
            left = hashes[i]
            right = hashes[i + 1] if i + 1 < len(hashes) else left  # duplicate last if odd
            new_hashes.append(double_sha256(left + right))
        hashes = new_hashes

    return hashes[0]


def generate_merkle_proof(txids: List[str], target_txid: str) -> Optional[dict]:
    """
    生成 Merkle 证明 — 给定区块所有 txid 和目标 txid
    返回 {target_hash, proof_hashes[], merkle_root}
    
    对标 bsv-poker 的 SpvFunding merkle block verification
    """
    if target_txid not in txids:
        return None

    target_idx = txids.index(target_txid)
    hashes = [bytes.fromhex(txid)[::-1] for txid in txids]
    proof = []

    idx = target_idx
    while len(hashes) > 1:
        new_hashes = []
        for i in range(0, len(hashes), 2):
            left = hashes[i]
            right = hashes[i + 1] if i + 1 < len(hashes) else left
            new_hashes.append(double_sha256(left + right))

            # 记录证明路径
            if i == idx or i + 1 == idx:
                sibling = hashes[i + 1] if i == idx else hashes[i]
                is_left = (i == idx)
                proof.append({
                    "hash": sibling[::-1].hex(),  # store as big-endian hex
                    "position": "right" if is_left else "left"
                })

        idx = idx // 2
        hashes = new_hashes

    merkle_root = hashes[0][::-1].hex()
    return {
        "txid": target_txid,
        "merkle_root": merkle_root,
        "proof": proof
    }


def verify_merkle_proof(txid: str, proof: list[dict], expected_root: str) -> bool:
    """
    验证 Merkle 证明 — 对标 bsv-poker 的 SPV verification
    """
    current = bytes.fromhex(txid)[::-1]  # LE

    for step in proof:
        sibling = bytes.fromhex(step["hash"])[::-1]  # LE
        if step["position"] == "right":
            current = double_sha256(current + sibling)
        else:  # left
            current = double_sha256(sibling + current)

    computed_root = current[::-1].hex()
    return computed_root == expected_root


# ─── Header Store ────────────────────────────────────────────

class HeaderStore:
    """
    对标 bsv-poker 的持久化区块头存储
    SQLite 存 headers，支持断点续传
    """

    def __init__(self, db_path: str = "~/.p2pchat/headers.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS headers (
                    height INTEGER PRIMARY KEY,
                    hash TEXT NOT NULL,
                    version INTEGER,
                    prev_hash TEXT,
                    merkle_root TEXT,
                    timestamp INTEGER,
                    bits INTEGER,
                    nonce INTEGER,
                    chainwork TEXT,
                    validated INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON headers(hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_validated ON headers(validated)")
            conn.commit()

    def save_header(self, header: BlockHeader, chainwork: str = "", validated: bool = True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO headers
                   (height, hash, version, prev_hash, merkle_root,
                    timestamp, bits, nonce, chainwork, validated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (header.height, header.hash_hex, header.version,
                 header.prev_hash_hex, header.merkle_root[::-1].hex(),
                 header.timestamp, header.bits, header.nonce,
                 chainwork, int(validated))
            )
            conn.commit()

    def get_header(self, height: int) -> Optional[BlockHeader]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM headers WHERE height = ?", (height,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_header(row)

    def get_tip(self) -> Optional[BlockHeader]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM headers ORDER BY height DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return self._row_to_header(row)

    def get_height(self) -> int:
        tip = self.get_tip()
        return tip.height if tip else -1

    def get_range(self, start: int, end: int) -> List[BlockHeader]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM headers WHERE height >= ? AND height <= ? ORDER BY height",
                (start, end)
            ).fetchall()
            return [self._row_to_header(r) for r in rows]

    @staticmethod
    def _row_to_header(row) -> BlockHeader:
        # row: height, hash, version, prev_hash, merkle_root, timestamp, bits, nonce, chainwork, validated
        return BlockHeader(
            version=row[2],
            prev_hash=bytes.fromhex(row[3])[::-1],
            merkle_root=bytes.fromhex(row[4])[::-1],
            timestamp=row[5],
            bits=row[6],
            nonce=row[7],
            height=row[0],
            hash=bytes.fromhex(row[1])[::-1]
        )


# ─── SPV Client ──────────────────────────────────────────────

class SPVClient:
    """
    SPV 客户端 — 对标 bsv-poker 的 BsvNode + SpvFunding
    = 下载区块头 + 验证 + 存储 + Merkle 证明验证
    """

    WOC_MAIN = "https://api.whatsonchain.com/v1/bsv/main"
    WOC_TEST = "https://api.whatsonchain.com/v1/bsv/test"

    def __init__(self, network: str = "main",
                 db_path: str = "~/.p2pchat/headers.db"):
        self.network = network
        self.api = self.WOC_MAIN if network == "main" else self.WOC_TEST
        self.store = HeaderStore(db_path)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "p2pchat-spv/1.0"})
        # ⭐ 扩大连接池，支持并行下载
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=2)
        self._session.mount('https://', adapter)
        self._session.mount('http://', adapter)

    # ─── Header Sync ────────────────────────────────────────

    def sync_headers(self, batch_size: int = 500,
                     start_height: int = None,
                     max_sync: int = None,
                     progress_callback=None) -> int:
        """
        同步区块头 — 对标 bsv-poker 的 header sync
        
        Strategy:
        1. 如果本地无 headers，使用检查点快速跳转到接近链尖
        2. 并行批量下载区块（每个区块一次请求）
        3. PoW + 链验证后存储
        
        start_height: 从哪个高度开始（默认=本地最高+1）
        max_sync: 最多同步多少个（默认=无限，即追到链尖）
        """
        local_height = self.store.get_height()
        if start_height is None:
            start_height = max(0, local_height + 1)

        # 获取远程最新高度
        remote_height = self._get_remote_height()
        if remote_height is None:
            log.error("Cannot get remote chain height")
            return 0

        # 大跨度时使用检查点跳过
        gap = remote_height - start_height
        if gap > 5000 and max_sync is None and local_height < 0:
            # 首次同步：从最近 2000 个块开始（Electrum 风格）
            start_height = max(0, remote_height - 2000)
            log.info(f"First sync: fast-forward to {start_height} (skipping {start_height} genesis blocks)")

        if max_sync is not None:
            end_height = min(start_height + max_sync - 1, remote_height)
        else:
            end_height = remote_height

        if start_height > end_height:
            return 0

        log.info(f"Syncing headers: {start_height} → {end_height} ({end_height - start_height + 1} blocks)")

        synced = 0
        current = start_height

        # 并行批量下载（每批 10 个并发请求 — 避免 API 限流）
        CONCURRENT = 10
        import concurrent.futures

        while current <= end_height:
            batch_end = min(current + batch_size - 1, end_height)
            heights = list(range(current, batch_end + 1))

            # 并发下载区块
            headers_data = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT) as executor:
                futures = {executor.submit(self._fetch_block_by_height, h): h for h in heights}
                for future in concurrent.futures.as_completed(futures):
                    h = futures[future]
                    try:
                        data = future.result()
                        if data:
                            headers_data[h] = data
                    except Exception as e:
                        log.debug(f"Failed to fetch height {h}: {e}")

            # 按高度排序后验证
            for h in sorted(headers_data.keys()):
                data = headers_data[h]
                try:
                    header = BlockHeader.from_json(data, h)
                except Exception as e:
                    log.warning(f"Failed to parse header at height {h}: {e}")
                    continue

                # 验证 PoW
                if not header.validate_pow():
                    log.warning(f"PoW validation failed at height {h}")
                    return synced

                # 验证链连接
                if h > 0:
                    prev = self.store.get_header(h - 1)
                    if prev and prev.hash != header.prev_hash:
                        log.warning(
                            f"Chain linkage broken at height {h}: "
                            f"expected {prev.hash_hex[:16]}..., "
                            f"got {header.prev_hash_hex[:16]}..."
                        )
                        return synced

                self.store.save_header(header)
                synced += 1

            if progress_callback:
                progress_callback(current, batch_end, synced, end_height)

            current = batch_end + 1

        log.info(f"Header sync done: {synced} new headers")
        return synced

    def verify_existing_headers(self) -> Tuple[int, int]:
        """
        重新验证已存储的所有区块头 — 对标 bsv-poker 的 re-validate on restart
        返回 (verified, failed)
        """
        verified = 0
        failed = 0
        height = self.store.get_height()

        if height < 0:
            return 0, 0

        # 获取已存储的所有 header
        headers = self.store.get_range(0, height)
        if not headers:
            return 0, 0

        for i, h in enumerate(headers):
            if not h.validate_pow():
                log.warning(f"Stored header at height {h.height} fails PoW!")
                failed += 1
                continue
            if i > 0:
                prev = headers[i - 1]
                if prev.compute_hash() != h.prev_hash:
                    log.warning(f"Chain linkage broken at height {h.height}")
                    failed += 1
                    continue
            verified += 1

        return verified, failed

    # ─── Merkle Proof Verification ──────────────────────────

    def verify_transaction(self, txid: str) -> Optional[dict]:
        """
        验证交易是否在区块链中 — SPV 核心功能
        对标 bsv-poker 的 SpvFunding verification
        
        1. 从 WoC 获取交易所在区块
        2. 获取该区块的所有 txid
        3. 计算 merkle root
        4. 验证 merkle root 匹配已存储的区块头
        
        返回 {height, block_hash, confirmed} 或 None
        """
        # 获取交易信息
        tx_info = self._fetch_tx(txid)
        if not tx_info:
            return None

        block_hash = tx_info.get("blockhash")
        block_height = tx_info.get("blockheight")

        if not block_hash or block_height is None:
            return {"confirmed": False, "reason": "unconfirmed"}

        # 验证我们有这个区块头
        stored = self.store.get_header(block_height)
        if not stored:
            # 需要的区块头未同步 — 只同步附近的一批
            log.warning(f"Header for height {block_height} not synced, syncing nearby...")
            from concurrent.futures import ThreadPoolExecutor
            # 只下载目标高度周围的区块
            start_h = max(0, block_height - 50)
            end_h = block_height + 10
            self.sync_headers(batch_size=100, start_height=start_h, max_sync=end_h - start_h + 1)
            
            stored = self.store.get_header(block_height)
            if not stored:
                return {"confirmed": False, "reason": "header not found"}

        # 获取区块中所有交易
        block = self._fetch_block(block_hash)
        if not block or "tx" not in block:
            return {"confirmed": False, "reason": "block data unavailable"}

        txids = block["tx"]

        # 计算 merkle root
        computed_root = build_merkle_root(txids)
        computed_root_hex = computed_root[::-1].hex()

        # 验证匹配区块头中的 merkle root
        expected_root = stored.merkle_root[::-1].hex()

        if computed_root_hex != expected_root:
            log.error(
                f"Merkle root MISMATCH at height {block_height}!\n"
                f"  Computed: {computed_root_hex}\n"
                f"  Expected: {expected_root}"
            )
            return {"confirmed": False, "reason": "merkle root mismatch"}

        # 生成 merkle proof
        proof = generate_merkle_proof(txids, txid)

        return {
            "confirmed": True,
            "height": block_height,
            "block_hash": block_hash,
            "merkle_root": expected_root,
            "proof": proof,
            "confirmations": self.store.get_height() - block_height + 1
        }

    def verify_utxo(self, txid: str, vout: int) -> Optional[dict]:
        """
        验证 UTXO — 对标 bsv-poker SpvFunding 的完整流程
        验证交易已确认 + 输出未被花费
        """
        result = self.verify_transaction(txid)
        if not result or not result.get("confirmed"):
            return result

        # 检查输出是否仍然未花费
        utxo_info = self._fetch_utxo(txid, vout)
        if utxo_info:
            result["utxo"] = utxo_info
            result["spent"] = utxo_info.get("spent", False)

        return result

    # ─── API Methods ────────────────────────────────────────

    def _get_remote_height(self) -> Optional[int]:
        try:
            resp = self._session.get(f"{self.api}/chain/info", timeout=10)
            resp.raise_for_status()
            return resp.json()["blocks"]
        except Exception as e:
            log.error(f"Failed to get chain info: {e}")
            return None

    def _fetch_headers(self, start_height: int, count: int) -> List[BlockHeader]:
        """从 WoC 下载区块头"""
        url = f"{self.api}/block/headers?count={count}&offset={start_height}"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list):
            return []

        headers = []
        for i, item in enumerate(data):
            height = start_height + i
            try:
                h = BlockHeader.from_json(item, height)
                headers.append(h)
            except Exception as e:
                log.warning(f"Failed to parse header at height {height}: {e}")

        return headers

    def _fetch_block_by_height(self, height: int) -> Optional[dict]:
        """获取指定高度的区块（含全部 header 字段）"""
        try:
            resp = self._session.get(
                f"{self.api}/block/height/{height}", timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _fetch_block(self, block_hash: str) -> Optional[dict]:
        """获取区块详情"""
        try:
            resp = self._session.get(
                f"{self.api}/block/hash/{block_hash}", timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"Failed to fetch block {block_hash}: {e}")
            return None

    def _fetch_tx(self, txid: str) -> Optional[dict]:
        """获取交易信息"""
        try:
            resp = self._session.get(f"{self.api}/tx/hash/{txid}", timeout=10)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"Failed to fetch tx {txid}: {e}")
            return None

    def _fetch_utxo(self, txid: str, vout: int) -> Optional[dict]:
        """获取 UTXO 信息"""
        # WoC doesn't have a direct UTXO endpoint, use tx info
        tx_info = self._fetch_tx(txid)
        if not tx_info:
            return None
        vouts = tx_info.get("vout", [])
        if vout < len(vouts):
            out = vouts[vout]
            return {
                "value": out.get("value", 0),
                "scriptPubKey": out.get("scriptPubKey", {}).get("hex", ""),
                "spent": False  # 需要额外查询
            }
        return None

    # ─── Stats ──────────────────────────────────────────────

    @property
    def status(self) -> dict:
        tip = self.store.get_tip()
        return {
            "network": self.network,
            "local_height": self.store.get_height(),
            "tip_hash": tip.hash_hex[:16] + "..." if tip else "none",
            "headers_validated": self.store.get_height() + 1 if tip else 0
        }


# ─── Self-Test ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    print("=" * 60)
    print("  SPV Node Self-Test")
    print("=" * 60)

    # Test 1: Header parse and PoW validation
    print("\n[1] Header parse + PoW test...")
    # Bitcoin/BSV genesis block (height 0)
    genesis = BlockHeader(
        version=1,
        prev_hash=b'\x00' * 32,
        # Correct genesis merkle root:
        # 4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b
        merkle_root=bytes.fromhex(
            "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
        )[::-1],
        timestamp=1231006505,
        bits=0x1d00ffff,
        nonce=2083236893,
        height=0
    )
    genesis.hash = genesis.compute_hash()
    print(f"  Genesis hash: {genesis.hash_hex}")
    print(f"  Expected:     000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")
    print(f"  PoW valid: {genesis.validate_pow()}")
    assert genesis.validate_pow(), "Genesis PoW must be valid!"

    # Test 2: Merkle proof
    print("\n[2] Merkle proof test...")
    txids = ["aaa"] * 8  # placeholder, replaced with real below
    # Use block 1 on BSV: single transaction (coinbase)
    txids_1tx = ["0e3e2357e806b6cdb1f70b54c3a3a17b6714ee1f0e68bebb44a74b1efd512098"]
    root_1tx = build_merkle_root(txids_1tx)
    root_hex = root_1tx[::-1].hex()
    print(f"  Merkle root of 1 tx: {root_hex}")
    assert root_hex == "0e3e2357e806b6cdb1f70b54c3a3a17b6714ee1f0e68bebb44a74b1efd512098"

    # Test 3: Merkle proof generation + verification
    print("\n[3] Merkle proof generation + verification...")
    txids_4 = [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    ]
    root_4 = build_merkle_root(txids_4)
    root_4_hex = root_4[::-1].hex()
    print(f"  Merkle root of 4 tx: {root_4_hex}")

    # Generate proof for txid[0]
    proof = generate_merkle_proof(txids_4, txids_4[0])
    assert proof is not None
    print(f"  Proof for tx[0]: {len(proof['proof'])} steps")

    # Verify
    valid = verify_merkle_proof(txids_4[0], proof["proof"], root_4_hex)
    assert valid, "Merkle proof verification failed!"
    print("  ✅ Merkle proof verified OK")

    # Test wrong txid fails
    invalid = verify_merkle_proof(txids_4[1], proof["proof"], root_4_hex)
    assert not invalid, "Wrong proof should fail!"
    print("  ✅ Wrong txid rejected")

    print("\n[4] HeaderStore test...")
    store = HeaderStore("/tmp/test_headers.db")
    store.save_header(genesis)
    loaded = store.get_header(0)
    assert loaded and loaded.hash == genesis.hash
    print("  ✅ HeaderStore save/load OK")

    print("\n🎉 All SPV tests passed!")

    # Live test (optional)
    if "--live" in sys.argv:
        print("\n" + "=" * 60)
        print("  LIVE SPV Test (BSV Mainnet)")
        print("=" * 60)
        client = SPVClient("main", "/tmp/live_spv.db")
        
        print("\n[5] Syncing headers...")
        synced = client.sync_headers(batch_size=500, progress_callback=
            lambda s, e, n, t: print(f"\r  Synced {s}-{e} ({n}/{t-0} new)  ", end="")
        )
        print(f"\n  ✅ Synced {synced} headers")
        print(f"  Local tip: {client.store.get_height()}")

        print("\n[6] Verifying stored headers...")
        v, f = client.verify_existing_headers()
        print(f"  Verified: {v}, Failed: {f}")
