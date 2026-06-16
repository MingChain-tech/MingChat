"""
P2P Chat Transport Layer — 对标 bsv-poker 的 BsvPoker.Net (P2P gossip mesh)
= asyncio TCP gossip 网格 + 节点发现 + 消息路由
= 无中心服务器，完全对等
"""
import asyncio
import struct
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Set
from collections import defaultdict

log = logging.getLogger("p2pchat.transport")

# ─── Wire Protocol ────────────────────────────────────────────

# 对标 bsv-poker 的消息帧格式
# 4 bytes LE length ‖ 1 byte type ‖ payload (JSON)
HEADER_FMT = "<IB"  # length(4 LE) + type(1)
HEADER_SIZE = 5
MAX_FRAME = 256 * 1024  # 256KB hard cap

class FrameType:
    HELLO      = 0x01  # 握手：身份声明
    PEERS      = 0x02  # 交换已知节点列表
    GOSSIP     = 0x03  # gossipsub 消息广播
    DIRECT     = 0x04  # 点到点加密消息
    PRESENCE   = 0x05  # 在线状态
    PING       = 0x06
    PONG       = 0x07
    BYE        = 0x08

FRAME_NAMES = {v: k for k, v in FrameType.__dict__.items() if not k.startswith('_')}

@dataclass
class Frame:
    """一个 P2P 消息帧"""
    ftype: int
    payload: bytes
    sender_id: str = ""  # 发送者 ID（本地填充）

    def encode(self) -> bytes:
        """编码为网络字节"""
        body = struct.pack("B", self.ftype) + self.payload
        header = struct.pack("<I", len(body))  # LE length, no type byte in length (同 bsv-poker)
        return header + body

    @classmethod
    def decode(cls, data: bytes) -> "Frame":
        """解码网络字节"""
        if len(data) < HEADER_SIZE:
            raise ValueError(f"Frame too short: {len(data)}B")
        length, = struct.unpack("<I", data[:4])
        ftype = data[4]
        payload = data[5:5 + length - 1]  # type byte already consumed
        return cls(ftype=ftype, payload=payload)

    def json(self) -> dict:
        return json.loads(self.payload.decode('utf-8'))

    def __repr__(self):
        name = FRAME_NAMES.get(self.ftype, f"0x{self.ftype:02X}")
        preview = self.payload[:50]
        return f"Frame({name}, {len(self.payload)}B, {preview}...)"


# ─── Peer Connection ──────────────────────────────────────────

@dataclass
class Peer:
    """一个已连接的节点"""
    host: str
    port: int
    peer_id: str = ""      # 节点 pubkey hex
    handle: str = ""       # @handle
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None
    connected_at: float = 0.0
    last_seen: float = 0.0
    outbound: bool = True

    @property
    def address(self) -> tuple:
        return (self.host, self.port)

    @property
    def is_alive(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()


# ─── Gossip Mesh ──────────────────────────────────────────────

class GossipMesh:
    """
    对标 bsv-poker 的 P2PNode — TCP gossip 网格
    
    功能:
    - flood-with-dedup 消息广播
    - 去中心化节点列表维护
    - 连接上限 + 速率限制
    - loopback 默认，显式公开
    """

    def __init__(self,
                 host: str = "127.0.0.1",
                 port: int = 0,  # 0 = random
                 max_peers: int = 50,
                 rate_limit: float = 100.0  # msgs/sec per peer
                 ):
        self.host = host
        self.port = port
        self.max_peers = max_peers
        self.rate_limit = rate_limit

        self._server: Optional[asyncio.AbstractServer] = None
        self._peers: dict[str, Peer] = {}  # peer_id → Peer
        self._seen: Set[bytes] = set()     # 消息去重（frame hash）
        self._seen_hashes: list = []       # 滑动窗口去重
        self._seen_max = 10000

        # 速率限制
        self._rate_buckets: dict[str, list] = defaultdict(list)

        # 回调
        self._on_hello: list = []
        self._on_direct: list = []
        self._on_gossip: list = []
        self._on_peer_join: list = []
        self._on_peer_leave: list = []
        self._on_presence: list = []

        self._running = False
        self._my_id = ""
        self._my_handle = ""

    def set_identity(self, peer_id: str, handle: str):
        self._my_id = peer_id
        self._my_handle = handle

    # ─── Callbacks ─────────────────────────────────────────

    def on_hello(self, cb: Callable[[Peer, dict], Awaitable[None]]):
        """收到 HELLO 帧"""
        self._on_hello.append(cb)

    def on_direct(self, cb: Callable[[Peer, dict], Awaitable[None]]):
        """收到 DIRECT 加密消息"""
        self._on_direct.append(cb)

    def on_gossip(self, cb: Callable[[Peer, dict], Awaitable[None]]):
        """收到 GOSSIP 广播"""
        self._on_gossip.append(cb)

    def on_peer_join(self, cb: Callable[[Peer], Awaitable[None]]):
        self._on_peer_join.append(cb)

    def on_peer_leave(self, cb: Callable[[str], Awaitable[None]]):  # peer_id
        self._on_peer_leave.append(cb)

    def on_presence(self, cb: Callable[[dict], Awaitable[None]]):
        self._on_presence.append(cb)

    # ─── Server ────────────────────────────────────────────

    async def start(self, listen_host: str = None, listen_port: int = None):
        """启动监听"""
        host = listen_host or self.host
        port = listen_port if listen_port is not None else self.port
        self._server = await asyncio.start_server(
            self._handle_incoming, host, port
        )
        addr = self._server.sockets[0].getsockname()
        self.host = addr[0]
        self.port = addr[1]
        self._running = True
        log.info(f"GossipMesh listening on {self.host}:{self.port}")

    async def stop(self):
        """停止"""
        self._running = False
        for peer in list(self._peers.values()):
            await self._disconnect_peer(peer, "shutdown")
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ─── Connect ───────────────────────────────────────────

    async def connect(self, host: str, port: int) -> Optional[Peer]:
        """主动连接节点"""
        if len(self._peers) >= self.max_peers:
            log.warning(f"Max peers ({self.max_peers}) reached")
            return None

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10.0
            )
        except Exception as e:
            log.warning(f"Connect to {host}:{port} failed: {e}")
            return None

        peer = Peer(host=host, port=port,
                    reader=reader, writer=writer,
                    connected_at=time.time(), last_seen=time.time())

        # 发送 HELLO
        await self._send_hello(peer)

        # 启动读取
        asyncio.create_task(self._read_loop(peer))

        # 暂时用地址作为 ID，等 HELLO 回复后再更新
        temp_id = f"{host}:{port}"
        self._peers[temp_id] = peer
        return peer

    # ─── Send ──────────────────────────────────────────────

    async def send_direct(self, peer_id: str, message: dict):
        """发送加密 DIRECT 消息到指定节点"""
        peer = self._peers.get(peer_id)
        if not peer or not peer.is_alive:
            log.warning(f"Peer {peer_id[:12]} not connected")
            return False
        frame = Frame(ftype=FrameType.DIRECT,
                       payload=json.dumps(message).encode('utf-8'))
        await self._send_frame(peer, frame)
        return True

    async def broadcast_gossip(self, topic: str, data: dict):
        """Gossip 广播消息到所有节点"""
        payload = json.dumps({"topic": topic, "data": data}).encode('utf-8')
        frame = Frame(ftype=FrameType.GOSSIP, payload=payload)
        await self._broadcast(frame)

    async def broadcast_presence(self, status: str = "online"):
        """广播在线状态"""
        payload = json.dumps({
            "peer_id": self._my_id,
            "handle": self._my_handle,
            "status": status,
            "timestamp": time.time()
        }).encode('utf-8')
        frame = Frame(ftype=FrameType.PRESENCE, payload=payload)
        await self._broadcast(frame)

    # ─── Internal ──────────────────────────────────────────

    async def _handle_incoming(self, reader, writer):
        """处理入站连接"""
        addr = writer.get_extra_info('peername')
        log.info(f"Incoming connection from {addr}")

        if len(self._peers) >= self.max_peers:
            log.warning(f"Max peers, rejecting {addr}")
            writer.close()
            return

        peer = Peer(host=addr[0], port=addr[1],
                    reader=reader, writer=writer,
                    connected_at=time.time(), last_seen=time.time(),
                    outbound=False)
        temp_id = f"{addr[0]}:{addr[1]}"
        self._peers[temp_id] = peer

        # 发送我们的 HELLO
        await self._send_hello(peer)

        # 读取循环
        await self._read_loop(peer)

    async def _read_loop(self, peer: Peer):
        """读取节点消息的循环"""
        try:
            while self._running and not peer.writer.is_closing():
                # 读长度
                len_data = await asyncio.wait_for(
                    peer.reader.readexactly(4), timeout=120.0
                )
                length, = struct.unpack("<I", len_data)

                if length > MAX_FRAME:
                    log.warning(f"Oversize frame ({length}B) from {peer.peer_id[:12]}")
                    break

                # 读 type + payload
                body = await asyncio.wait_for(
                    peer.reader.readexactly(length), timeout=30.0
                )
                ftype = body[0]
                payload = body[1:]

                frame = Frame(ftype=ftype, payload=payload, sender_id=peer.peer_id)
                await self._dispatch(peer, frame)

        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError) as e:
            log.debug(f"Peer {peer.peer_id[:12]} disconnected: {e}")
        except Exception as e:
            log.error(f"Read error from {peer.peer_id[:12]}: {e}")
        finally:
            await self._disconnect_peer(peer, "read_error")

    async def _dispatch(self, peer: Peer, frame: Frame):
        """分发消息到处理器"""
        now = time.time()
        peer.last_seen = now

        # 速率限制
        if not self._check_rate(peer.peer_id, now):
            return

        if frame.ftype == FrameType.HELLO:
            try:
                data = json.loads(frame.payload.decode('utf-8'))
            except json.JSONDecodeError:
                return
            # 更新节点信息
            new_id = data.get("peer_id", "")
            if new_id and new_id != peer.peer_id:
                # 重新注册
                old_id = peer.peer_id
                peer.peer_id = new_id
                peer.handle = data.get("handle", "")
                if old_id in self._peers:
                    del self._peers[old_id]
                self._peers[new_id] = peer

                log.info(f"Peer {new_id[:12]} (@{peer.handle}) identified")
                # 回复 HELLO
                await self._send_hello(peer)
                # 通知
                for cb in self._on_peer_join:
                    await cb(peer)
            for cb in self._on_hello:
                await cb(peer, data)

        elif frame.ftype == FrameType.GOSSIP:
            # Gossip 去重
            h = hash(frame.payload)
            if h in self._seen:
                return
            self._seen.add(h)
            if len(self._seen) > self._seen_max:
                # 简单清理
                self._seen = set(list(self._seen)[-self._seen_max // 2:])

            # 转发给其他节点
            await self._forward(peer, frame)

            # 处理
            try:
                data = json.loads(frame.payload.decode('utf-8'))
            except json.JSONDecodeError:
                return
            for cb in self._on_gossip:
                await cb(peer, data)

        elif frame.ftype == FrameType.DIRECT:
            try:
                data = json.loads(frame.payload.decode('utf-8'))
            except json.JSONDecodeError:
                return
            for cb in self._on_direct:
                await cb(peer, data)

        elif frame.ftype == FrameType.PRESENCE:
            try:
                data = json.loads(frame.payload.decode('utf-8'))
            except json.JSONDecodeError:
                return
            for cb in self._on_presence:
                await cb(data)

        elif frame.ftype == FrameType.PING:
            # Reply PONG
            pong = Frame(ftype=FrameType.PONG,
                         payload=json.dumps({"echo": frame.payload.decode()}).encode())
            await self._send_frame(peer, pong)

        elif frame.ftype == FrameType.PEERS:
            try:
                data = json.loads(frame.payload.decode('utf-8'))
            except json.JSONDecodeError:
                return
            # 连接新节点
            for p in data.get("peers", []):
                if len(self._peers) >= self.max_peers:
                    break
                addr = p.get("host"), p.get("port")
                # 不连接已知节点
                if any(ep.host == addr[0] and ep.port == addr[1]
                       for ep in self._peers.values()):
                    continue
                asyncio.create_task(self.connect(addr[0], addr[1]))

    async def _send_frame(self, peer: Peer, frame: Frame):
        """发送单个帧"""
        if not peer.is_alive:
            return False
        try:
            data = frame.encode()
            peer.writer.write(data)
            await peer.writer.drain()
            return True
        except Exception as e:
            log.error(f"Send to {peer.peer_id[:12]} failed: {e}")
            return False

    async def _send_hello(self, peer: Peer):
        """发送 HELLO 握手"""
        hello = json.dumps({
            "peer_id": self._my_id,
            "handle": self._my_handle,
            "host": self.host,
            "port": self.port,
            "version": 1,
            "timestamp": time.time()
        }).encode('utf-8')
        frame = Frame(ftype=FrameType.HELLO, payload=hello)
        await self._send_frame(peer, frame)

    async def _broadcast(self, frame: Frame):
        """向所有节点广播（排除发送者）"""
        for peer in list(self._peers.values()):
            if peer.peer_id != frame.sender_id:
                await self._send_frame(peer, frame)

    async def _forward(self, from_peer: Peer, frame: Frame):
        """转发 gossip 消息给其他节点"""
        frame.sender_id = from_peer.peer_id
        await self._broadcast(frame)

    async def _disconnect_peer(self, peer: Peer, reason: str):
        """断开节点"""
        pid = peer.peer_id
        log.info(f"Disconnecting {pid[:12]}: {reason}")
        try:
            # Send BYE
            bye = Frame(ftype=FrameType.BYE,
                       payload=json.dumps({"reason": reason}).encode())
            await self._send_frame(peer, bye)
        except Exception:
            pass

        try:
            peer.writer.close()
        except Exception:
            pass

        if pid in self._peers:
            del self._peers[pid]

        for cb in self._on_peer_leave:
            await cb(pid)

    def _check_rate(self, peer_id: str, now: float) -> bool:
        """速率限制检查"""
        bucket = self._rate_buckets[peer_id]
        # 清除旧记录
        bucket[:] = [t for t in bucket if now - t < 1.0]
        if len(bucket) >= self.rate_limit:
            return False
        bucket.append(now)
        return True

    @property
    def peer_list(self) -> list:
        """列出所有已连接节点"""
        return [
            {
                "peer_id": p.peer_id[:12],
                "handle": p.handle,
                "address": f"{p.host}:{p.port}",
                "connected": f"{time.time() - p.connected_at:.0f}s ago"
            }
            for p in self._peers.values()
        ]
