"""
P2P Chat Application — 对标 bsv-poker 的 ChatService + NetGame + SPV
= 整合 crypto + identity + transport + message + onchain + SPV
= 完整的 P2P 即时通讯客户端，带 SPV 链上消息验证
"""
import asyncio
import json
import time
import logging
import sys
import os
from typing import Optional
from pathlib import Path

from crypto import (
    generate_keypair, serialize_public_key,
    deserialize_public_key
)
from identity import Identity
from message import Message, MessageStore, OnChainEnvelope
from transport import GossipMesh, Peer, FrameType
from onchain import OnChainClient, BSVConfig, VerifiedEnvelope
from spv import SPVClient

log = logging.getLogger("p2pchat")


class P2PChat:
    """
    P2P 即时通讯客户端
    
    对标 bsv-poker 的完整聊天系统:
    - BsvPoker.Crypto → 加密/解密
    - BsvPoker.Net → P2P 传输（GossipMesh）
    - BsvPoker.Core.Identity → 身份系统
    - BsvPoker.App.ChatView → 用户体验
    """

    def __init__(self, identity: Identity,
                 host: str = "127.0.0.1",
                 port: int = 0,
                 data_dir: str = "~/.p2pchat",
                 network: str = "main"):
        self.identity = identity
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)

        # 传输层
        self.mesh = GossipMesh(host=host, port=port)
        self.mesh.set_identity(identity.pubkey_hex, identity.handle)

        # 消息存储
        self.store = MessageStore()

        # ⭐ SPV 客户端 — 对标 bsv-poker 的 SpvFunding
        spv_db = os.path.join(self.data_dir, "headers.db")
        self.spv = SPVClient(network, spv_db)
        self._spv_synced = False

        # ⭐ 链上客户端 — 对标 bsv-poker 的 OnChainChat
        config = BSVConfig(network=network)
        self.onchain = OnChainClient(config, spv_client=self.spv)

        # 注册回调
        self.mesh.on_hello(self._handle_hello)
        self.mesh.on_direct(self._handle_direct)
        self.mesh.on_gossip(self._handle_gossip)
        self.mesh.on_peer_join(self._handle_peer_join)
        self.mesh.on_peer_leave(self._handle_peer_leave)
        self.mesh.on_presence(self._handle_presence)

        # 状态
        self._running = False
        self._listening = False

        # 加载历史消息
        self._load_store()

    # ─── Lifecycle ──────────────────────────────────────────

    async def start(self, listen_host: str = "127.0.0.1",
                    listen_port: int = 0,
                    sync_spv: bool = True):
        """启动 P2P 节点 + SPV 同步"""
        await self.mesh.start(listen_host, listen_port)
        self._running = True
        self._listening = True
        log.info(f"P2PChat @{self.identity.handle} started")
        log.info(f"  Listening on: {self.mesh.host}:{self.mesh.port}")
        log.info(f"  Pubkey: {self.identity.pubkey_hex[:16]}...")

        # ⭐ 启动 SPV 同步（后台）
        if sync_spv:
            asyncio.create_task(self._sync_spv())
            asyncio.create_task(self._poll_offline_messages())

    async def stop(self):
        """停止"""
        self._running = False
        self._save_store()
        await self.mesh.stop()
        log.info("P2PChat stopped")

    async def connect_peer(self, host: str, port: int) -> bool:
        """连接到另一个节点"""
        peer = await self.mesh.connect(host, port)
        return peer is not None

    # ─── Messaging ─────────────────────────────────────────

    async def send_message(self, to_handle: str, content: str,
                           msg_type: str = "text") -> Optional[Message]:
        """
        发送消息给指定用户
        
        对标 bsv-poker ChatService.SendDirect:
        1. 创建消息
        2. 用对方公钥加密（ECDH ephemeral + AES-256-GCM）
        3. 通过 P2P 发送 DIRECT 帧
        4. 如果对方不在线，回退到链上
        """
        # 1. 创建消息
        msg = Message.create(
            from_handle=self.identity.handle,
            to_handle=to_handle,
            content=content,
            msg_type=msg_type
        )

        # 2. 获取对方公钥
        their_pk = self.identity.get_contact_pk(to_handle)
        if not their_pk:
            log.error(f"Contact @{to_handle} not found. Add with: /add @{to_handle} <pubkey_hex>")
            return None

        # 3. 加密
        msg.encrypt(self.identity.identity_sk, their_pk)

        # 4. 通过 P2P 发送
        payload = msg.encrypt_for_p2p(self.identity.identity_sk, their_pk)

        # 找到对方连接的 peer
        target_peer_id = None
        for pid, peer in self.mesh._peers.items():
            if peer.handle == to_handle:
                target_peer_id = pid
                break

        if target_peer_id:
            success = await self.mesh.send_direct(target_peer_id, payload)
            if success:
                self.store.add(msg)
                log.info(f"✅ Sent to @{to_handle}: {content[:50]}")
                return msg

        # 5. 回退到 gossip（对方可能在网格中但未直接连接）
        await self.mesh.broadcast_gossip("dm", payload)
        self.store.add(msg)
        log.info(f"📡 Gossip sent to @{to_handle}: {content[:50]}")
        return msg

    async def send_broadcast(self, content: str):
        """广播消息到所有人"""
        await self.mesh.broadcast_gossip("broadcast", {
            "from": self.identity.handle,
            "content": content,
            "ts": time.time()
        })
        log.info(f"📢 Broadcast: {content[:50]}")

    # ─── Handlers ──────────────────────────────────────────

    async def _handle_hello(self, peer: Peer, data: dict):
        """处理 HELLO"""
        handle = data.get("handle", "?")
        log.info(f"👋 HELLO from @{handle} ({peer.peer_id[:12]})")

        # 自动添加联系人
        if handle and handle not in self.identity.contacts:
            self.identity.add_contact(handle, data.get("peer_id", ""))

    async def _handle_direct(self, peer: Peer, data: dict):
        """处理 DIRECT 加密消息 — 对标 bsv-poker ChatService.Receive"""
        epk_hex = data.get("epk", "")
        ct_b64 = data.get("ct", "")
        from_handle = peer.handle or "?"

        if not epk_hex or not ct_b64:
            return

        # 解密
        try:
            epk_bytes = bytes.fromhex(epk_hex)
            encrypted = __import__('base64').b64decode(ct_b64)
            from crypto import decrypt_from_sender
            plaintext = decrypt_from_sender(
                epk_bytes, encrypted, self.identity.identity_sk
            )
            msg_dict = json.loads(plaintext.decode('utf-8'))
        except Exception as e:
            log.error(f"Decrypt failed from @{from_handle}: {e}")
            return

        msg = Message.from_dict(msg_dict)
        msg.from_handle = peer.handle or msg.from_handle

        self.store.add(msg)
        self._save_store()

        # 显示
        content = msg.content
        log.info(f"\n💬 @{msg.from_handle} → @{self.identity.handle}: {content}")

        # 发送已读回执
        await self._send_read_receipt(peer, msg)

    async def _handle_gossip(self, peer: Peer, data: dict):
        """处理 GOSSIP 广播"""
        topic = data.get("topic", "")
        payload = data.get("data", {})

        if topic == "dm":
            # DM 消息 — 检查是否是给我的
            to_handle = payload.get("to", "")
            if to_handle == self.identity.handle:
                await self._handle_direct(peer, payload)
        elif topic == "broadcast":
            from_h = payload.get("from", "?")
            content = payload.get("content", "")
            log.info(f"\n📢 @{from_h}: {content}")
        elif topic == "presence":
            log.info(f"  🟢 @{payload.get('handle','?')} is {payload.get('status','online')}")

    async def _handle_peer_join(self, peer: Peer):
        log.info(f"🔗 Peer joined: @{peer.handle} ({peer.peer_id[:12]})")

    async def _handle_peer_leave(self, peer_id: str):
        log.info(f"🔌 Peer left: {peer_id[:12]}")

    async def _handle_presence(self, data: dict):
        handle = data.get("handle", "?")
        status = data.get("status", "online")
        log.info(f"  🟢 @{handle} is {status}")

    async def _send_read_receipt(self, peer: Peer, msg: Message):
        """发送已读回执"""
        pass  # TODO

    # ─── SPV & On-Chain ─────────────────────────────────────

    async def _sync_spv(self):
        """
        后台 SPV 同步 — 对标 bsv-poker 的 header sync
        在后台线程中下载并验证区块头
        """
        log.info("🔗 SPV: Starting header sync...")
        try:
            # 在线程池中运行同步（spv 使用 requests，不是 asyncio）
            loop = asyncio.get_event_loop()
            synced = await loop.run_in_executor(
                None,
                lambda: self.spv.sync_headers(batch_size=200, max_sync=2000)
            )
            self._spv_synced = True
            log.info(f"🔗 SPV: Synced {synced} headers ✓")

            # 验证已存储的
            v, f = await loop.run_in_executor(
                None, self.spv.verify_existing_headers
            )
            log.info(f"🔗 SPV: Re-verified {v} headers ({f} failed)")

        except Exception as e:
            log.warning(f"⚠️ SPV sync incomplete: {e}")

    async def _poll_offline_messages(self):
        """
        后台轮询链上离线消息 — 对标 bsv-poker 的 OnChainChat.fetch()
        每 30 秒扫描一次链上 OP_RETURN，用 SPV 验证后解密
        """
        await asyncio.sleep(5)  # 等 SPV 先同步一些头

        while self._running:
            try:
                loop = asyncio.get_event_loop()
                messages = await loop.run_in_executor(
                    None,
                    lambda: self.onchain.fetch_and_verify_messages(
                        self.identity.pubkey_hex
                    )
                )

                for m in messages:
                    txid = m.get("txid", "")[:12]
                    spv_ok = m.get("spv_verified", False)
                    data = m.get("data", {})

                    if spv_ok and data:
                        # 尝试解密
                        try:
                            epk_hex = data.get("ep", "")
                            ct_b64 = data.get("ct", "")
                            if epk_hex and ct_b64:
                                from crypto import decrypt_from_sender
                                import base64 as b64
                                epk = bytes.fromhex(epk_hex)
                                ct = b64.b64decode(ct_b64)
                                plain = decrypt_from_sender(
                                    epk, ct, self.identity.identity_sk
                                )
                                msg_dict = json.loads(plain.decode('utf-8'))
                                msg = Message.from_dict(msg_dict)
                                self.store.add(msg)
                                self._save_store()
                                log.info(
                                    f"📩 SPV-verified offline msg: "
                                    f"@{msg.from_handle}: {msg.content[:50]}"
                                )
                        except Exception as e:
                            log.debug(f"Decrypt failed for {txid}: {e}")

                    elif data and not spv_ok:
                        log.debug(f"⏳ Msg {txid}... waiting for SPV confirmation")

            except Exception as e:
                log.debug(f"Offline poll error: {e}")

            await asyncio.sleep(30)

    async def fetch_offline_now(self) -> list:
        """手动立即检查离线消息"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.onchain.fetch_and_verify_messages(
                self.identity.pubkey_hex
            )
        )

    # ─── Persistence ────────────────────────────────────────

    def _save_store(self):
        self.store.save(os.path.join(self.data_dir, "messages.json"))

    def _load_store(self):
        self.store = MessageStore.load(
            os.path.join(self.data_dir, "messages.json"))

    # ─── CLI Helpers ───────────────────────────────────────

    def get_contact_list(self) -> list:
        return [
            {"handle": h, "pk": pk[:16] + "..."}
            for h, pk in self.identity.contacts.items()
        ]

    def get_recent_messages(self, handle: str = None, limit: int = 20):
        if handle:
            return self.store.get_for(handle, limit)
        return self.store.all()[-limit:]

    @property
    def status(self) -> dict:
        tip = self.spv.store.get_tip()
        return {
            "handle": self.identity.handle,
            "pubkey": self.identity.pubkey_hex[:16] + "...",
            "listening": f"{self.mesh.host}:{self.mesh.port}",
            "peers": len(self.mesh._peers),
            "messages": len(self.store.messages),
            "contacts": len(self.identity.contacts),
            "running": self._running,
            "spv": {
                "synced": self._spv_synced,
                "headers": tip.height if tip else 0,
                "tip_hash": tip.hash_hex[:16] + "..." if tip else "none",
                "network": self.spv.network,
            }
        }
