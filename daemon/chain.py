#!/usr/bin/env python3.9
"""
P2P Chat — BSV Chain Module (Python 3.9 + bsv library)
= 对标 bsv-poker 的 Chain.cs
= UTXO 管理 + OP_RETURN 存证 + 交易构建 + 签名 + 广播

使用 python3.9 因为 bsv 库只支持 3.9
由主应用通过 subprocess 调用
"""
import sys
import json
import logging
import requests
from typing import Optional

from bsv import (
    PrivateKey, P2PKH, OpReturn,
    Transaction, TransactionInput, TransactionOutput,
    WhatsOnChainBroadcaster, Network
)

log = logging.getLogger("chain")

WOC_MAIN = "https://api.whatsonchain.com/v1/bsv/main"
WOC_TEST = "https://api.whatsonchain.com/v1/bsv/test"


class ChainClient:
    """BSV 链上操作客户端"""

    def __init__(self, private_key_wif: str, network: str = "main"):
        # PrivateKey constructor accepts WIF string directly
        self.sk = PrivateKey(private_key_wif)
        self.address = self.sk.address()
        self.network = Network.MAINNET if network == "main" else Network.TESTNET
        self.api = WOC_MAIN if network == "main" else WOC_TEST
        self.broadcaster = WhatsOnChainBroadcaster(network=self.network)
        self._session = requests.Session()

    # ─── UTXO (via WoC API) ────────────────────────────────

    def get_utxos(self) -> list:
        """通过 WoC API 获取 UTXO 列表"""
        try:
            resp = self._session.get(
                f"{self.api}/address/{self.address}/unspent", timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"get_utxos failed: {e}")
            return []

    def get_balance(self) -> dict:
        """查询余额"""
        utxos = self.get_utxos()
        total = sum(u.get("value", 0) for u in utxos)
        return {
            "address": self.address,
            "balance": total,
            "utxo_count": len(utxos),
            "utxos": [
                {"txid": u["tx_hash"], "vout": u["tx_pos"], "satoshis": u["value"]}
                for u in utxos[:10]
            ]
        }

    # ─── Fetch Source Transaction ──────────────────────────

    def _fetch_tx(self, txid: str) -> Optional[Transaction]:
        """从 WoC 获取原始交易并解析"""
        try:
            resp = self._session.get(
                f"{self.api}/tx/{txid}/hex", timeout=10
            )
            resp.raise_for_status()
            raw_hex = resp.text.strip()
            return Transaction.from_hex(raw_hex)
        except Exception as e:
            log.error(f"fetch_tx {txid[:12]}... failed: {e}")
            return None

    # ─── OP_RETURN Store ───────────────────────────────────

    def store_op_return(self, data: bytes, fee_sats: int = 1) -> dict:
        """
        对标 bsv-poker OnChainChat.send()
        将数据存入 BSV 链上 OP_RETURN
        """
        try:
            utxos_woc = self.get_utxos()
            if not utxos_woc:
                return {"error": "No UTXOs. Fund this address first."}

            # 选择 UTXO
            u = utxos_woc[0]
            utxo_txid = u["tx_hash"]
            utxo_vout = u["tx_pos"]
            utxo_sats = u["value"]

            # 获取源交易（用于获得 satoshis 和 locking_script）
            source_tx = self._fetch_tx(utxo_txid)
            if not source_tx:
                return {"error": f"Cannot fetch source tx {utxo_txid[:12]}..."}

            # 构建交易
            tx = Transaction()

            tx.add_input(TransactionInput(
                source_transaction=source_tx,
                source_output_index=utxo_vout,
                unlocking_script_template=P2PKH().unlock(self.sk)
            ))

            # OP_RETURN 输出
            op_return = OpReturn()
            tx.add_output(TransactionOutput(
                satoshis=0,
                locking_script=op_return.lock([data])
            ))

            # 找零
            change = utxo_sats - fee_sats
            if change > 0:
                tx.add_output(TransactionOutput(
                    satoshis=change,
                    locking_script=P2PKH().lock(self.address)
                ))

            tx.sign()
            
            # broadcast 是 async，需要用 asyncio 包装
            import asyncio
            result = asyncio.run(self.broadcaster.broadcast(tx))

            if result and hasattr(result, 'txid'):
                return {"txid": result.txid, "fee": fee_sats, "data_size": len(data)}
            return {"error": "Broadcast returned no txid"}

        except Exception as e:
            return {"error": str(e)}

    # ─── Announce Node ─────────────────────────────────────

    def announce_node(self, peer_id: str, handle: str,
                      host: str, port: int) -> dict:
        data = json.dumps({
            "v": 1, "t": "p2pchat-node",
            "pid": peer_id, "h": handle,
            "host": host, "port": port
        }, separators=(',', ':')).encode('utf-8')
        return self.store_op_return(data)

    # ─── Sign Message ──────────────────────────────────────

    def sign_message(self, message: str) -> dict:
        sig = self.sk.sign_text(message)
        return {
            "address": self.address,
            "public_key": self.sk.public_key().hex(),
            "signature": sig,
            "message": message
        }


# ─── CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    python3.9 chain.py <command> <wif> [args...] [network]
    
    Commands:
      balance  — 查询余额
      store    — 写入 OP_RETURN (参数: data_hex)
      announce — 公告节点 (参数: peer_id handle host port)
      sign     — 签名消息 (参数: message)
    """
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: chain.py <cmd> <wif> [args]"}))
        sys.exit(1)

    cmd = sys.argv[1]
    wif = sys.argv[2]
    # 最后一个参数如果是 main/test 则作为 network
    network = "main"
    args = sys.argv[3:]
    if args and args[-1] in ("main", "test"):
        network = args[-1]
        args = args[:-1]

    client = ChainClient(wif, network)

    if cmd == "balance":
        result = client.get_balance()
    elif cmd == "store":
        if not args:
            result = {"error": "Missing data_hex"}
        else:
            data = bytes.fromhex(args[0])
            result = client.store_op_return(data)
    elif cmd == "announce":
        if len(args) < 4:
            result = {"error": "Need: peer_id handle host port"}
        else:
            result = client.announce_node(args[0], args[1], args[2], int(args[3]))
    elif cmd == "sign":
        msg = args[0] if args else "p2pchat identity"
        result = client.sign_message(msg)
    else:
        result = {"error": f"Unknown command: {cmd}"}

    print(json.dumps(result, indent=2))
