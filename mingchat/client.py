# -*- coding: utf-8 -*-
"""
铭信 (MingChat) v0.3 - 客户端
发送和接收BSV链上消息
"""

import json
import time
import threading
import struct
import urllib.request
from typing import Optional, Callable, List, Dict, Union

from .models import MsgType
from .models import Message
from .protocol import (
    parse_op_return_data,
    address_to_hash160, hash160_to_address,
    HEADER_SIZE_V0_3, PROTOCOL_MAGIC,
)
from .bsv_tools import (
    privkey_to_wif, wif_to_privkey, privkey_to_pubkey, privkey_to_address,
    pubkey_to_address, address_to_hash160 as addr_to_h160, hash160_to_address,
    build_p2pkh_script, build_op_return_script, broadcast_tx, fetch_utxos,
    serialize_varint, sha256, hash256
)


class MingChat:
    """铭信客户端 - 发送和接收BSV链上消息"""

    BASE_URL = "https://api.whatsonchain.com/v1/bsv/main"
    MINING_FEE = 100  # satoshis

    def __init__(self, private_key_wif: str = None, network: str = "mainnet"):
        self.network = network
        self._privkey_bytes = None
        self._address = None
        
        if private_key_wif:
            self._privkey_bytes = wif_to_privkey(private_key_wif)
            self._address = privkey_to_address(self._privkey_bytes, network)
        
        self._callback = None
        self._listening = False
        self._seen_txids = set()

    @property
    def address(self) -> str:
        return self._address or ""

    @property
    def hash160(self) -> bytes:
        return address_to_hash160(self.address) if self.address else b"\x00" * 20

    @property
    def wif(self) -> str:
        if self._privkey_bytes:
            return privkey_to_wif(self._privkey_bytes, self.network)
        return ""

    # ── 发送消息 ───────────────────────────────────────────

    def send(self, receiver_address: str, body: Union[str, bytes], 
             msg_type: Union[MsgType, int] = MsgType.TEXT) -> Message:
        """发送链上消息，返回Message对象（含txid）"""
        if not self._privkey_bytes:
            raise RuntimeError("需要私钥才能发送消息")
        
        if isinstance(body, str):
            body_bytes = body.encode('utf-8')
        else:
            body_bytes = body
        
        receiver_hash160 = address_to_hash160(receiver_address)
        timestamp = int(time.time() * 1000)
        
        msg = Message(
            msg_type=msg_type if isinstance(msg_type, MsgType) else MsgType(msg_type),
            sender_hash160=self.hash160,
            receiver_hash160=receiver_hash160,
            timestamp=timestamp,
            payload=body_bytes,
        )
        
        # 构建OP_RETURN交易并广播
        txid = self._broadcast_op_return(msg)
        msg.txid = txid
        
        return msg

    def rpc_call(self, receiver_address: str, method: str, params: Dict = None) -> Dict:
        """发送RPC请求"""
        payload = json.dumps({
            "method": method,
            "params": params or {},
            "jsonrpc": "2.0",
            "id": int(time.time())
        }).encode('utf-8')
        
        msg = self.send(receiver_address, payload, MsgType.RPC_REQUEST)
        return {
            "method": method,
            "params": params or {},
            "txid": msg.txid,
            "timestamp": msg.timestamp
        }

    def reply(self, original_msg: Message, body: Union[str, bytes]) -> Message:
        """回复消息"""
        sender_addr = hash160_to_address(original_msg.sender_hash160, self.network)
        return self.send(sender_addr, body, MsgType.RPC_RESPONSE)

    # ── 监听 ───────────────────────────────────────────────

    def listen(self, callback: Callable[[Message], None] = None):
        """开始监听消息（后台轮询）"""
        if callback:
            self._callback = callback
        
        if self._listening:
            return
        
        self._listening = True
        self._seen_txids = set()
        
        # 记录最新txid
        try:
            history = self._fetch_history(self.address)
            if history:
                self._last_known_txid = history[0].get("tx_hash", "")
        except Exception:
            self._last_known_txid = ""
        
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    def stop(self):
        """停止监听"""
        self._listening = False

    def on_message(self, func: Callable):
        """装饰器方式注册回调"""
        self._callback = func
        return func

    # ── 获取消息 ───────────────────────────────────────────

    def get_messages(self, address: str = None, limit: int = 20) -> List[Message]:
        """获取链上铭信消息"""
        target = address or self.address
        if not target:
            return []
        
        msgs = []
        try:
            history = self._fetch_history(target, limit)
            for tx in history:
                txid = tx.get("tx_hash", "")
                if not txid or txid in self._seen_txids:
                    continue
                
                data = self._fetch_op_return(txid)
                if data:
                    msg = parse_op_return_data(data)
                    if msg:
                        msg.txid = txid
                        msgs.append(msg)
        except Exception as e:
            print(f"获取消息失败: {e}")
        
        return msgs

    def get_inbox(self, limit: int = 20) -> List[Message]:
        """获取收件箱（只返回发给自己的消息）"""
        all_msgs = self.get_messages(limit=limit)
        return [m for m in all_msgs if m.receiver_hash160 == self.hash160]

    # ── 内部方法 ───────────────────────────────────────────

    def _poll_loop(self):
        """轮询循环"""
        while self._listening:
            try:
                history = self._fetch_history(self.address)
                if not history:
                    time.sleep(10)
                    continue

                for tx in history:
                    txid = tx.get("tx_hash", "")
                    if not txid:
                        continue
                    
                    if txid == self._last_known_txid:
                        break
                    
                    if txid in self._seen_txids:
                        continue
                    
                    self._seen_txids.add(txid)
                    
                    data = self._fetch_op_return(txid)
                    if data:
                        msg = parse_op_return_data(data)
                        if msg and (msg.receiver_hash160 == self.hash160 or 
                                   msg.receiver_hash160 == b"\x00" * 20):  # 广播也接收
                            msg.txid = txid
                            self._dispatch(msg)

                self._last_known_txid = history[0].get("tx_hash", "")
            except Exception as e:
                print(f"轮询错误: {e}")
            
            time.sleep(10)

    def _dispatch(self, msg: Message):
        """分发消息到回调"""
        sender_addr = hash160_to_address(msg.sender_hash160, self.network)
        msg_type_str = msg.msg_type.to_str()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msg.timestamp))
        preview = msg.get_payload_text()[:200]

        summary = (
            f"\n{'='*60}\n"
            f"[MingChat] 新消息\n"
            f"  类型: {msg_type_str}\n"
            f"  来自: {sender_addr[:12]}...{sender_addr[-6:]}\n"
            f"  时间: {ts}\n"
            f"  内容: {preview}\n"
            f"  TXID: {msg.txid[:16]}...\n"
            f"{'='*60}\n"
        )
        print(summary, flush=True)

        if self._callback:
            try:
                self._callback(msg)
            except Exception as e:
                print(f"回调错误: {e}")

    def _broadcast_op_return(self, msg: Message) -> str:
        """构建并广播OP_RETURN交易 (使用bsv-sdk)"""
        from bsv.transaction import Transaction, TransactionInput, TransactionOutput
        from bsv.script.type import OpReturn, P2PKH
        from bsv.constants import SIGHASH
        from bsv.keys import PrivateKey

        # 获取UTXO
        utxos = fetch_utxos(self.address)
        if not utxos:
            raise RuntimeError(f"地址 {self.address} 没有可用UTXO")

        utxo = utxos[0]

        # 序列化OP_RETURN数据 (v0.3)
        from .protocol import serialize_message_v0_3
        op_data = serialize_message_v0_3(msg)

        # 获取源交易
        source_hex = self._fetch_raw(f"/tx/{utxo['txid']}/hex")
        source_tx = Transaction.from_hex(source_hex)

        # bsv-sdk私钥
        privkey = PrivateKey.from_hex(self._privkey_bytes.hex())

        # 构建交易
        fee = self.MINING_FEE
        change = utxo["satoshis"] - fee

        tx = Transaction()
        tx.add_input(TransactionInput(
            source_transaction=source_tx,
            source_output_index=utxo["vout"],
        ))

        # OP_RETURN输出
        op_return = OpReturn()
        tx.add_output(TransactionOutput(
            satoshis=0,
            locking_script=op_return.lock(pushdatas=[op_data]),
        ))

        # 找零
        if change > 546:
            p2pkh = P2PKH()
            tx.add_output(TransactionOutput(
                satoshis=change,
                locking_script=p2pkh.lock(self.address),
            ))

        # 签名
        tx.inputs[0].sighash = SIGHASH.FORKID | SIGHASH.ALL
        unlock = p2pkh.unlock(privkey)
        tx.inputs[0].unlocking_script = unlock.sign(tx, 0)

        # 广播
        txid = broadcast_tx(tx.hex())
        return txid

    def _build_tx(self, utxo: Dict, op_data: bytes) -> Dict:
        """构建交易"""
        inputs = [{
            "txid": utxo["txid"],
            "vout": utxo["vout"],
            "script_sig": b"",
            "sequence": 0xffffffff
        }]
        
        outputs = [{
            "value": 0,
            "script_pubkey": build_op_return_script(op_data)
        }]
        
        # 找零
        change = utxo["satoshis"] - self.MINING_FEE
        if change > 546:  # 保留最小输出
            outputs.append({
                "value": change,
                "script_pubkey": build_p2pkh_script(self.hash160)
            })
        
        return {
            "version": 1,
            "inputs": inputs,
            "outputs": outputs,
            "locktime": 0
        }

    def _sign_tx(self, tx: Dict, utxo: Dict) -> str:
        """签名交易"""
        from .bsv_tools import ecdsa_sign, der_encode_sig
        
        # 获取源交易
        source_tx_hex = self._fetch_raw(f"/tx/{utxo['txid']}/hex")
        source_tx_raw = bytes.fromhex(source_tx_hex)
        
        # 构建签名交易
        raw = struct.pack('<I', tx["version"])  # version
        
        # inputs
        raw += serialize_varint(len(tx["inputs"]))
        for inp in tx["inputs"]:
            raw += bytes.fromhex(inp["txid"])[::-1]  # txid反转
            raw += struct.pack('<I', inp["vout"])
            raw += serialize_varint(len(inp["script_sig"])) + inp["script_sig"]
            raw += struct.pack('<I', inp["sequence"])
        
        # outputs
        raw += serialize_varint(len(tx["outputs"]))
        for out in tx["outputs"]:
            raw += struct.pack('<q', out["value"])  # 有符号
            raw += struct.pack('<H', len(out["script_pubkey"]))
            raw += out["script_pubkey"]
        
        raw += struct.pack('<I', tx["locktime"])
        
        # Sighash
        sighash_type = 0x41  # SIGHASH_ALL | SIGHASH_FORKID
        raw += struct.pack('<I', sighash_type)
        
        # 计算签名哈希
        sig_hash = hash256(raw)
        
        # 签名
        r, s = ecdsa_sign(self._privkey_bytes, sig_hash)
        der_sig = der_encode_sig(r, s, sighash_type)
        
        # 获取公钥
        pubkey = privkey_to_pubkey(self._privkey_bytes)
        
        # P2PKH解锁脚本
        unlock_script = bytes([len(der_sig)]) + der_sig + bytes([len(pubkey)]) + pubkey
        
        # 构建最终交易
        final_raw = struct.pack('<I', tx["version"])
        final_raw += serialize_varint(len(tx["inputs"]))
        
        # 第一个输入使用签名
        final_raw += bytes.fromhex(tx["inputs"][0]["txid"])[::-1]
        final_raw += struct.pack('<I', tx["inputs"][0]["vout"])
        final_raw += serialize_varint(len(unlock_script)) + unlock_script
        final_raw += struct.pack('<I', tx["inputs"][0]["sequence"])
        
        # 其他输入
        for inp in tx["inputs"][1:]:
            final_raw += bytes.fromhex(inp["txid"])[::-1]
            final_raw += struct.pack('<I', inp["vout"])
            final_raw += b'\x00'
            final_raw += struct.pack('<I', inp["sequence"])
        
        # outputs
        final_raw += serialize_varint(len(tx["outputs"]))
        for out in tx["outputs"]:
            final_raw += struct.pack('<q', out["value"])
            final_raw += struct.pack('<H', len(out["script_pubkey"]))
            final_raw += out["script_pubkey"]
        
        final_raw += struct.pack('<I', tx["locktime"])
        
        return final_raw.hex()

    def _fetch_history(self, address: str, limit: int = 50) -> List[Dict]:
        """获取地址历史交易"""
        try:
            url = f"{self.BASE_URL}/address/{address}/history?limit={limit}"
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception:
            return []

    def _fetch_op_return(self, txid: str) -> Optional[bytes]:
        """从交易中提取OP_RETURN数据"""
        try:
            tx_hex = self._fetch_raw(f"/tx/{txid}/hex")
            return self._extract_op_return(bytes.fromhex(tx_hex))
        except Exception:
            return None

    def _extract_op_return(self, raw: bytes) -> Optional[bytes]:
        """从原始交易提取OP_RETURN数据"""
        pos = 4  # skip version
        
        # inputs
        input_count = raw[pos]
        pos += 1
        for _ in range(input_count):
            pos += 36  # txid(32) + vout(4)
            script_len = raw[pos]
            pos += 1 + script_len + 4  # script_len + script + sequence
        
        # outputs
        output_count = raw[pos]
        pos += 1
        
        for _ in range(output_count):
            pos += 8  # value
            script_len = struct.unpack_from('<H', raw, pos)[0]
            pos += 2
            script = raw[pos:pos + script_len]
            pos += script_len
            
            # 检查OP_RETURN
            if len(script) >= 2 and script[0] == 0x00 and script[1] == 0x6a:
                # OP_FALSE OP_RETURN
                data_start = 2
                if script[data_start] <= 75:
                    data_len = script[data_start]
                    return script[data_start+1:data_start+1+data_len]
                elif script[data_start] == 0x4c:
                    data_len = script[data_start+1]
                    return script[data_start+2:data_start+2+data_len]
                elif script[data_start] == 0x4d:
                    data_len = struct.unpack_from('<H', script, data_start+1)[0]
                    return script[data_start+3:data_start+3+data_len]
            
            # 也检查只有OP_RETURN的情况
            if len(script) >= 2 and script[0] == 0x6a:
                data_start = 1
                if script[data_start] <= 75:
                    return script[data_start+1:data_start+1+script[data_start]]
        
        return None

    def _fetch_raw(self, path: str) -> str:
        """获取原始数据"""
        url = f"{self.BASE_URL}{path}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.read().decode().strip()

    # ── 工具方法 ───────────────────────────────────────────

    def get_balance(self) -> int:
        """获取地址余额（satoshis）"""
        try:
            utxos = fetch_utxos(self.address)
            return sum(u["satoshis"] for u in utxos)
        except Exception:
            return 0

    def status(self) -> Dict:
        """获取钱包状态"""
        balance = self.get_balance()
        return {
            "address": self.address,
            "balance_sats": balance,
            "balance_bsv": balance / 1e8,
            "network": self.network,
            "listening": self._listening
        }
