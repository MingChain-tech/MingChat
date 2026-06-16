"""
铭信 MingChat — Hermes Agent 插件
===================================

让 Hermes Agent 获得端到端加密 P2P 通信能力。

用法:
  1. 确保铭信守护进程已运行:
     python3 p2p_daemon.py --handle @agent --rpc-port 9877

  2. 将本插件安装到 ~/.hermes/plugins/mingchat/

  3. Hermes Agent 重启后自动加载，获得以下工具:
     - mingchat_send      发送加密私信
     - mingchat_broadcast 广播到所有节点
     - mingchat_status    查看节点状态
     - mingchat_contacts  查看联系人
     - mingchat_add_contact 添加联系人
     - mingchat_connect_peer 连接对等节点
     - mingchat_history   消息历史
     - mingchat_identity  查看自身身份

架构:
  Hermes Agent ←→ 本插件 ←→ TCP JSON-RPC ←→ p2p_daemon.py ←→ P2P 网络
"""

from __future__ import annotations

import json
import socket
import threading
import time
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("hermes.plugin.mingchat")

# ─── 配置 ──────────────────────────────────────────────────
DEFAULT_RPC_HOST = "127.0.0.1"
DEFAULT_RPC_PORT = 9877
RECONNECT_DELAY = 5  # 秒


# ═══════════════════════════════════════════════════════════════
# JSON-RPC TCP 客户端
# ═══════════════════════════════════════════════════════════════

class MingChatClient:
    """铭信守护进程 JSON-RPC TCP 客户端。

    线程安全：所有 RPC 调用加锁，事件回调在独立线程中执行。
    """

    def __init__(self, host: str = DEFAULT_RPC_HOST, port: int = DEFAULT_RPC_PORT):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._running = False
        self._event_handlers: Dict[str, list[Callable]] = {}

    # ─── 连接管理 ──────────────────────────────────────

    def connect(self) -> bool:
        """连接到守护进程。返回 True 表示成功。"""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            self._sock.connect((self._host, self._port))
            self._sock.settimeout(30)
            self._running = True
            logger.info("铭信插件已连接到 %s:%d", self._host, self._port)
            return True
        except Exception as e:
            logger.warning("铭信插件连接失败 %s:%d — %s", self._host, self._port, e)
            self._sock = None
            return False

    def close(self):
        """关闭连接。"""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and self._running

    # ─── RPC 调用 ──────────────────────────────────────

    def call(self, method: str, params: dict = None) -> dict:
        """同步 JSON-RPC 调用。返回 result dict 或 {"error": "..."}。"""
        with self._lock:
            if not self.is_connected:
                if not self.connect():
                    return {"error": "Not connected to MingChat daemon"}

            self._msg_id += 1
            req = {
                "jsonrpc": "2.0",
                "id": self._msg_id,
                "method": method,
                "params": params or {},
            }

            try:
                self._sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
                buf = b""
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("Connection closed by daemon")
                    buf += chunk
                    if b"\n" in buf:
                        break
                resp = json.loads(buf.decode().strip().split("\n")[0])
                if "result" in resp:
                    return resp["result"]
                elif "error" in resp:
                    return {"error": resp["error"].get("message", str(resp["error"]))}
                else:
                    return resp
            except Exception as e:
                logger.error("铭信 RPC 调用失败 [%s]: %s", method, e)
                self._running = False
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                return {"error": str(e)}

    # ─── 事件监听 ──────────────────────────────────────

    def on(self, event_type: str, handler: Callable):
        """注册事件处理器。"""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)

    def _emit(self, event_type: str, data: dict):
        """触发事件回调。"""
        handlers = self._event_handlers.get(event_type, [])
        for h in handlers:
            try:
                h(data)
            except Exception as e:
                logger.error("事件处理器异常 [%s]: %s", event_type, e)

    def listen_events(self, inject_fn: Callable):
        """阻塞式事件监听循环。在独立线程中运行。

        inject_fn(content, role) 用于将事件注入 Hermes 对话。
        """
        while self._running:
            try:
                if not self.is_connected:
                    time.sleep(RECONNECT_DELAY)
                    self.connect()
                    continue

                # 注意：这里需要独立连接来监听事件，
                # 因为 RPC 连接是锁定的。实际上 daemon 在 TCP 模式下
                # 不会主动推送事件到 TCP 客户端（当前实现中事件只走 stdout）。
                # 所以我们用轮询代替。
                time.sleep(5)
                # 检查是否有新消息
                history = self.call("history", {"limit": 5})
                if "messages" in history and history["messages"]:
                    latest = history["messages"][-1]
                    ts = latest.get("ts", 0) / 1000
                    if ts > time.time() - 30:  # 30秒内的新消息
                        self._emit("message_received", latest)

            except Exception as e:
                logger.error("事件监听异常: %s", e)
                time.sleep(RECONNECT_DELAY)


# ═══════════════════════════════════════════════════════════════
# 插件入口
# ═══════════════════════════════════════════════════════════════

_client: Optional[MingChatClient] = None
_event_thread: Optional[threading.Thread] = None


def _get_client() -> MingChatClient:
    """获取或创建全局客户端。"""
    global _client
    if _client is None:
        _client = MingChatClient()
        _client.connect()
    return _client


def _inject_message(ctx, content: str):
    """将消息注入 Hermes Agent 对话。"""
    try:
        ctx.inject_message(content, role="user")
    except Exception as e:
        logger.error("消息注入失败: %s", e)


# ─── 工具处理器 ────────────────────────────────────────

def tool_send(to: str, content: str, **kwargs) -> dict:
    """发送端到端加密私信。"""
    return _get_client().call("send_message", {"to": to, "content": content})


def tool_broadcast(content: str, **kwargs) -> dict:
    """向所有已连接的对等节点广播消息。"""
    return _get_client().call("broadcast", {"content": content})


def tool_status(**kwargs) -> dict:
    """获取铭信节点运行状态、SPV 同步进度、已连接节点数。"""
    return _get_client().call("status")


def tool_contacts(**kwargs) -> dict:
    """列出所有已保存的联系人（handle + 公钥）。"""
    return _get_client().call("list_contacts")


def tool_add_contact(handle: str, pubkey: str, **kwargs) -> dict:
    """添加联系人（需要对方的 handle 和 secp256k1 公钥 hex）。"""
    return _get_client().call("add_contact", {"handle": handle, "pubkey": pubkey})


def tool_connect_peer(host: str = "127.0.0.1", port: int = 9876, **kwargs) -> dict:
    """连接到另一个铭信对等节点。"""
    return _get_client().call("connect_peer", {"host": host, "port": port})


def tool_history(handle: str = "", limit: int = 50, **kwargs) -> dict:
    """获取消息历史。可指定联系人过滤。"""
    params = {"limit": min(limit, 200)}
    if handle:
        params["with"] = handle
    return _get_client().call("history", params)


def tool_identity(**kwargs) -> dict:
    """查看本地铭信身份：handle、公钥、seed 哈希。"""
    return _get_client().call("get_identity")


# ─── 注册 ─────────────────────────────────────────────

def register(ctx):
    """Hermes 插件入口 — 注册铭信工具集。"""

    toolset = "mingchat"

    # 初始化客户端
    client = _get_client()
    if client.is_connected:
        logger.info("铭信插件初始化完成 — 守护进程已连接")
    else:
        logger.warning(
            "铭信插件已加载但守护进程未连接 (tcp://%s:%d)。"
            "请先启动: python3 p2p_daemon.py --handle @agent --rpc-port %d",
            DEFAULT_RPC_HOST, DEFAULT_RPC_PORT, DEFAULT_RPC_PORT,
        )

    # ── 注册工具 ──

    ctx.register_tool(
        name="mingchat_send",
        toolset=toolset,
        schema={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "接收方的 handle（如 @alice 或 alice）",
                },
                "content": {
                    "type": "string",
                    "description": "要发送的消息内容",
                },
            },
            "required": ["to", "content"],
        },
        handler=tool_send,
        description="通过铭信发送端到端加密 P2P 私信。ECDH+AES-256-GCM 加密，对方不在线时自动回退到 BSV 链上存证。",
        emoji="✉️",
    )

    ctx.register_tool(
        name="mingchat_broadcast",
        toolset=toolset,
        schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要广播的消息内容",
                },
            },
            "required": ["content"],
        },
        handler=tool_broadcast,
        description="通过铭信 P2P gossip 网格向所有已连接节点广播消息。",
        emoji="📢",
    )

    ctx.register_tool(
        name="mingchat_status",
        toolset=toolset,
        schema={"type": "object", "properties": {}},
        handler=tool_status,
        description="查看铭信节点运行状态：在线状态、SPV 区块头同步进度、已连接对等节点数、消息统计。",
        emoji="📊",
    )

    ctx.register_tool(
        name="mingchat_contacts",
        toolset=toolset,
        schema={"type": "object", "properties": {}},
        handler=tool_contacts,
        description="列出铭信中的所有联系人及其公钥。",
        emoji="📇",
    )

    ctx.register_tool(
        name="mingchat_add_contact",
        toolset=toolset,
        schema={
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "联系人 handle（如 @alice 或 alice）",
                },
                "pubkey": {
                    "type": "string",
                    "description": "联系人的 secp256k1 公钥（hex 格式，66 字符）",
                },
            },
            "required": ["handle", "pubkey"],
        },
        handler=tool_add_contact,
        description="添加铭信联系人。需要对方的 handle 和 secp256k1 压缩公钥（66 字符 hex）。",
        emoji="➕",
    )

    ctx.register_tool(
        name="mingchat_connect_peer",
        toolset=toolset,
        schema={
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "对等节点 IP 地址（默认 127.0.0.1）",
                },
                "port": {
                    "type": "integer",
                    "description": "对等节点端口（默认 9876）",
                },
            },
            "required": [],
        },
        handler=tool_connect_peer,
        description="连接到另一个铭信对等节点，加入 P2P gossip 网格。",
        emoji="🔗",
    )

    ctx.register_tool(
        name="mingchat_history",
        toolset=toolset,
        schema={
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "可选：按联系人 handle 过滤历史消息",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回消息数上限（默认 50，最大 200）",
                },
            },
        },
        handler=tool_history,
        description="获取铭信消息历史。可按联系人过滤。",
        emoji="📜",
    )

    ctx.register_tool(
        name="mingchat_identity",
        toolset=toolset,
        schema={"type": "object", "properties": {}},
        handler=tool_identity,
        description="查看本地铭信身份信息：handle、secp256k1 公钥、seed 哈希、联系人数量。",
        emoji="🪪",
    )

    # ── 启动事件监听线程 ──
    global _event_thread
    if _event_thread is None or not _event_thread.is_alive():
        _event_thread = threading.Thread(
            target=client.listen_events,
            args=(lambda data: _inject_message(ctx, str(data)),),
            daemon=True,
            name="mingchat-events",
        )
        _event_thread.start()

    logger.info(
        "铭信插件注册完成 — 8 个工具 + 事件监听 (tcp://%s:%d)",
        DEFAULT_RPC_HOST, DEFAULT_RPC_PORT,
    )


# ─── 清理 ─────────────────────────────────────────────

def _cleanup():
    """插件卸载时清理资源。"""
    global _client, _event_thread
    if _client:
        _client.close()
        _client = None
    _event_thread = None
