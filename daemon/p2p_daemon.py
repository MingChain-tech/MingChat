#!/usr/bin/env python3.9
"""
铭信 MingChat JSON-RPC Daemon
= Agent-to-Agent 去中心化加密通讯引擎
= stdin/stdout JSON-RPC 2.0 协议（也支持 --rpc-port TCP 模式）
= 作为 OpenClaw 铭信通道后端：ECDH+AES-256-GCM 加密、SPV 验证、链上消息

用法:
  python3 p2p_daemon.py --handle @lobster [--data-dir ~/.p2pchat] [--host 127.0.0.1] [--port 0]

JSON-RPC 协议 (每行一个 JSON):
  ← {"jsonrpc":"2.0","id":1,"method":"status"}              # 请求
  → {"jsonrpc":"2.0","id":1,"result":{...}}                 # 响应
  → {"jsonrpc":"2.0","event":"message_received","data":{...}}  # 事件推送

日志输出到 stderr（不干扰 stdout 的 JSON-RPC 通信）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import signal
import logging
import threading
import argparse
from pathlib import Path
from typing import Optional

# ⚡ 关键：强制 stdout 行缓冲
# 当 stdout 是管道（非 TTY）时 Python 默认块缓冲，导致 JSON-RPC 响应被延迟
# 解决方法：用 fdopen 重开为行缓冲模式
if not sys.stdout.isatty():
    sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1, encoding='utf-8')

# ─── 日志配置（只输出到 stderr） ───────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr
)
log = logging.getLogger("p2p-daemon")

# ─── 常量 ──────────────────────────────────────────────────
DATA_DIR = os.path.expanduser("~/.p2pchat")
VERSION = "1.0.0"

# ─── JSON-RPC 核心 ─────────────────────────────────────────

class JSONRPCHandler:
    """
    JSON-RPC 2.0 协议处理器
    - 从 stdin 逐行读取请求
    - 写入响应/事件到 stdout
    - 事件可广播到 TCP 客户端（通过 broadcaster 回调）
    - 线程安全：stdout 写入加锁
    """

    def __init__(self):
        self._stdout_lock = threading.Lock()
        self._running = True
        self._broadcaster = None  # 可选: callable(line) — 广播到 TCP 客户端

    def set_broadcaster(self, broadcaster):
        """设置 TCP 事件广播器"""
        self._broadcaster = broadcaster

    def write_response(self, msg_id, result=None, error=None):
        """写入 JSON-RPC 响应"""
        resp = {"jsonrpc": "2.0", "id": msg_id}
        if error:
            resp["error"] = error
        else:
            resp["result"] = result
        self._write_line(json.dumps(resp, ensure_ascii=False, default=str))

    def write_event(self, event_type: str, data: dict = None):
        """写入 JSON-RPC 事件推送（通知）"""
        msg = {
            "jsonrpc": "2.0",
            "event": event_type,
            "data": data or {},
            "ts": int(time.time() * 1000)
        }
        line = json.dumps(msg, ensure_ascii=False, default=str)
        self._write_line(line)
        # 广播到 TCP 客户端
        if self._broadcaster:
            try:
                self._broadcaster(line)
            except Exception:
                pass

    def _write_line(self, line: str):
        """线程安全写入 stdout"""
        with self._stdout_lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                self._running = False

    def is_running(self) -> bool:
        return self._running

    def stop(self):
        self._running = False


# ─── 守护进程主逻辑 ──────────────────────────────────────

class P2PDaemon:
    """P2P Chat 守护进程 — 管理 P2P 节点 + JSON-RPC 协议"""

    def __init__(self, handle: str, data_dir: str = DATA_DIR,
                 listen_host: str = "127.0.0.1", listen_port: int = 0,
                 network: str = "main", rpc_port: int = 0):
        self.handle = handle.lstrip("@")
        self.data_dir = os.path.expanduser(data_dir)
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.network = network
        self.rpc_port = rpc_port  # 0=仅stdin/stdout, >0=TCP JSON-RPC

        self.rpc = JSONRPCHandler()
        self.chat_app = None
        self.identity = None
        self._stdin_queue = None  # 延迟到 run() 中初始化，确保绑定到正确的 event loop
        self._tcp_clients = set()  # (reader, writer) 元组集合
        self._tcp_server = None

        os.makedirs(self.data_dir, exist_ok=True)

    # ─── 生命周期 ───────────────────────────────────────

    async def start(self):
        """启动守护进程"""
        # 1. 加载或创建身份
        ident_path = Path(self.data_dir) / f"identity_{self.handle}.json"
        from identity import Identity

        if ident_path.exists():
            self.identity = Identity.load(str(ident_path))
            log.info("Loaded identity: @%s (pk: %s...)", self.identity.handle,
                     self.identity.pubkey_hex[:16])
        else:
            self.identity = Identity.create(self.handle)
            self.identity.save(str(ident_path))
            log.info("Created new identity: @%s (pk: %s...)", self.identity.handle,
                     self.identity.pubkey_hex[:16])

        # 2. 创建 P2PChat 实例
        from app import P2PChat
        self.chat_app = P2PChat(
            self.identity,
            host=self.listen_host,
            port=self.listen_port,
            data_dir=self.data_dir,
            network=self.network
        )

        # 3. 注册回调 → JSON-RPC 事件推送
        self._register_callbacks()

        # 4. 启动 P2P 节点
        await self.chat_app.start(self.listen_host, self.listen_port, sync_spv=True)

        log.info("P2P Daemon started: @%s on %s:%s",
                 self.identity.handle,
                 self.chat_app.mesh.host,
                 self.chat_app.mesh.port)

        # 推送就绪事件
        self.rpc.write_event("ready", {
            "handle": self.identity.handle,
            "pubkey": self.identity.pubkey_hex,
            "listening": f"{self.chat_app.mesh.host}:{self.chat_app.mesh.port}",
            "version": VERSION,
            "network": self.network
        })

    async def stop(self):
        """停止守护进程"""
        log.info("Shutting down P2P Daemon...")
        if self.chat_app:
            await self.chat_app.stop()
        self.rpc.stop()

    # ─── 回调注册 ───────────────────────────────────────

    def _register_callbacks(self):
        """P2PChat 事件 → JSON-RPC 事件推送"""
        chat = self.chat_app

        # 注册额外回调（P2PChat 已有自己的回调，我们的追加到回调列表）
        chat.mesh.on_hello(self._on_hello_event)
        chat.mesh.on_direct(self._on_direct_event)
        chat.mesh.on_gossip(self._on_gossip_event)
        chat.mesh.on_peer_join(self._on_peer_join_event)
        chat.mesh.on_peer_leave(self._on_peer_leave_event)

    async def _on_hello_event(self, peer, data: dict):
        self.rpc.write_event("hello", {
            "peer_id": getattr(peer, "peer_id", "")[:16],
            "handle": getattr(peer, "handle", "?"),
        })

    async def _on_direct_event(self, peer, data: dict):
        self.rpc.write_event("message_received", {
            "from": getattr(peer, "handle", data.get("from", "?")),
            "content": data.get("content", ""),
            "msg_id": data.get("msg_id", ""),
            "delivery": "p2p",
            "ts": int(time.time() * 1000)
        })

    async def _on_gossip_event(self, peer, data: dict):
        topic = data.get("topic", "")
        payload = data.get("data", {})
        if topic == "broadcast":
            self.rpc.write_event("broadcast_received", {
                "from": payload.get("from", "?"),
                "content": payload.get("content", ""),
                "ts": payload.get("ts", 0)
            })

    async def _on_peer_join_event(self, peer):
        self.rpc.write_event("peer_online", {
            "peer_id": getattr(peer, "peer_id", "")[:16],
            "handle": getattr(peer, "handle", "?"),
            "addr": getattr(peer, "addr", "?")
        })

    async def _on_peer_leave_event(self, peer_id: str):
        self.rpc.write_event("peer_offline", {
            "peer_id": peer_id[:16]
        })

    # ─── JSON-RPC 方法分发 ──────────────────────────────

    async def dispatch(self, request: dict, *, responder=None):
        """分发 JSON-RPC 请求到对应的处理方法
        
        responder: 可选的回调 (msg_id, result=None, error=None) -> None
                  为 None 时使用 self.rpc.write_response (stdin/stdout 模式)
        """
        method = request.get("method", "")
        params = request.get("params", {})
        msg_id = request.get("id")

        if not self.rpc.is_running():
            resp = self._make_error(msg_id, -32000, "Daemon is shutting down")
            if responder:
                responder(resp)
            else:
                self.rpc.write_response(msg_id, error={
                    "code": -32000, "message": "Daemon is shutting down"
                })
            return

        try:
            handler = getattr(self, f"rpc_{method}", None)
            if handler is None:
                resp = self._make_error(msg_id, -32601, f"Method not found: {method}")
                if responder:
                    responder(resp)
                else:
                    self.rpc.write_response(msg_id, error={
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    })
                return

            result = await handler(params)
            if responder:
                responder(self._make_response(msg_id, result))
            else:
                self.rpc.write_response(msg_id, result=result)

        except Exception as e:
            log.exception("Error handling method %s", method)
            resp = self._make_error(msg_id, -32000, str(e))
            if responder:
                responder(resp)
            else:
                self.rpc.write_response(msg_id, error={
                    "code": -32000,
                    "message": str(e)
                })

    @staticmethod
    def _make_response(msg_id, result):
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result},
                          ensure_ascii=False, default=str)

    @staticmethod
    def _make_error(msg_id, code, message):
        return json.dumps({"jsonrpc": "2.0", "id": msg_id,
                           "error": {"code": code, "message": message}},
                          ensure_ascii=False)

    # ─── RPC 方法实现 ──────────────────────────────────

    async def rpc_status(self, params: dict) -> dict:
        """获取节点状态"""
        if not self.chat_app:
            return {"running": False}
        s = self.chat_app.status
        return {
            "running": s["running"],
            "handle": s["handle"],
            "pubkey": self.identity.pubkey_hex,  # 完整公钥
            "listening": s["listening"],
            "peers": s["peers"],
            "messages": s["messages"],
            "contacts": s["contacts"],
            "spv": {
                "synced": s["spv"]["synced"],
                "headers": s["spv"]["headers"],
                "tip_hash": s["spv"]["tip_hash"],
                "network": s["spv"]["network"]
            }
        }

    async def rpc_send_message(self, params: dict) -> dict:
        """发送加密消息"""
        to_handle = params.get("to", "").lstrip("@")
        content = params.get("content", "")

        if not to_handle or not content:
            return {"error": "Missing 'to' or 'content' parameter"}

        if not self.chat_app:
            return {"error": "Node not running"}

        msg = await self.chat_app.send_message(to_handle, content)
        if msg:
            return {
                "msg_id": msg.msg_id,
                "delivery": "p2p",
                "to": to_handle,
                "ts": int(msg.timestamp * 1000)
            }
        else:
            return {"error": f"Failed to send to @{to_handle} (contact not found or offline)"}

    async def rpc_broadcast(self, params: dict) -> dict:
        """向所有节点广播消息"""
        content = params.get("content", "")
        if not content:
            return {"error": "Missing 'content' parameter"}

        if not self.chat_app:
            return {"error": "Node not running"}

        await self.chat_app.send_broadcast(content)
        return {"delivery": "broadcast", "ts": int(time.time() * 1000)}

    async def rpc_connect_peer(self, params: dict) -> dict:
        """连接到另一个节点"""
        host = params.get("host", "127.0.0.1")
        port = params.get("port", 9876)

        if not self.chat_app:
            return {"error": "Node not running"}

        success = await self.chat_app.connect_peer(host, port)
        return {
            "connected": success,
            "address": f"{host}:{port}"
        }

    async def rpc_add_contact(self, params: dict) -> dict:
        """添加联系人"""
        handle = params.get("handle", "").lstrip("@")
        pubkey = params.get("pubkey", "")

        if not handle or not pubkey:
            return {"error": "Missing 'handle' or 'pubkey' parameter"}

        if not self.chat_app or not self.chat_app.identity:
            return {"error": "Node not running"}

        self.chat_app.identity.add_contact(handle, pubkey)
        return {"added": f"@{handle}", "pubkey_preview": pubkey[:16] + "..."}

    async def rpc_list_contacts(self, params: dict) -> dict:
        """列出所有联系人"""
        if not self.chat_app:
            return {"contacts": []}

        contacts = [
            {"handle": h, "pubkey": pk}
            for h, pk in self.chat_app.identity.contacts.items()
        ]
        return {"contacts": contacts}

    async def rpc_list_peers(self, params: dict) -> dict:
        """列出已连接的节点"""
        if not self.chat_app:
            return {"peers": []}

        peers = []
        for pid, peer in self.chat_app.mesh._peers.items():
            peers.append({
                "peer_id": pid[:16],
                "handle": peer.handle or "?",
                "addr": getattr(peer, "addr", "?"),
                "connected_since": getattr(peer, "connected_since", 0)
            })
        return {"peers": peers}

    async def rpc_history(self, params: dict) -> dict:
        """获取消息历史"""
        handle = params.get("with", "").lstrip("@")
        limit = min(params.get("limit", 50), 200)

        if not self.chat_app:
            return {"messages": []}

        if handle:
            msgs = self.chat_app.store.get_for(handle, limit)
        else:
            msgs = self.chat_app.store.all()[-limit:]

        messages = [
            {
                "msg_id": m.msg_id,
                "type": m.msg_type,
                "from": m.from_handle,
                "to": m.to_handle,
                "content": m.content,
                "ts": int(m.timestamp * 1000),
                "reply_to": m.reply_to
            }
            for m in msgs
        ]
        return {"messages": messages}

    async def rpc_get_identity(self, params: dict) -> dict:
        """获取自己的身份信息"""
        if not self.identity:
            return {"error": "No identity loaded"}

        return {
            "handle": self.identity.handle,
            "pubkey": self.identity.pubkey_hex,
            "seed_hash": self.identity.seed_hash[:16] + "...",
            "contacts_count": len(self.identity.contacts)
        }

    async def rpc_fetch_offline(self, params: dict) -> dict:
        """手动扫描链上离线消息"""
        if not self.chat_app:
            return {"error": "Node not running"}

        try:
            loop = asyncio.get_event_loop()
            msgs = await loop.run_in_executor(
                None,
                lambda: self.chat_app.onchain.fetch_and_verify_messages(
                    self.identity.pubkey_hex
                )
            )
            # 处理解密
            new_count = 0
            for m in msgs:
                spv_ok = m.get("spv_verified", False)
                data = m.get("data", {})
                if spv_ok and data:
                    try:
                        from crypto import decrypt_from_sender
                        import base64 as b64
                        epk = bytes.fromhex(data.get("ep", ""))
                        ct = b64.b64decode(data.get("ct", ""))
                        plain = decrypt_from_sender(
                            epk, ct, self.identity.identity_sk
                        )
                        msg_dict = json.loads(plain.decode("utf-8"))
                        from message import Message
                        msg = Message.from_dict(msg_dict)
                        self.chat_app.store.add(msg)
                        new_count += 1

                        self.rpc.write_event("offline_message", {
                            "from": msg.from_handle,
                            "content": msg.content,
                            "msg_id": msg.msg_id,
                            "delivery": "onchain",
                            "txid": m.get("txid", "")[:16],
                            "spv_verified": True,
                            "ts": int(msg.timestamp * 1000)
                        })
                    except Exception:
                        pass

            return {
                "total_scanned": len(msgs),
                "new_messages": new_count
            }
        except Exception as e:
            return {"error": str(e)}

    async def rpc_spv_status(self, params: dict) -> dict:
        """获取 SPV 同步状态"""
        if not self.chat_app:
            return {"error": "Node not running"}

        s = self.chat_app.status["spv"]
        return {
            "synced": s["synced"],
            "headers": s["headers"],
            "tip_hash": s["tip_hash"],
            "network": s["network"]
        }

    async def rpc_stop(self, params: dict) -> dict:
        """优雅停止守护进程"""
        log.info("Received stop command")
        self.rpc._running = False
        return {"stopping": True}

    async def rpc_ping(self, params: dict) -> dict:
        """健康检查"""
        return {"pong": True, "ts": int(time.time() * 1000)}

    # ─── stdin 读取循环 ────────────────────────────────

    async def _read_stdin_loop(self):
        """使用独立线程读取 stdin，推送到 asyncio 队列"""
        loop = asyncio.get_event_loop()
        q = self._stdin_queue
        
        def _reader_thread():
            """独立线程：逐行读取 stdin"""
            try:
                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        request = json.loads(line)
                        # 使用线程安全方式推送到 asyncio 队列
                        loop.call_soon_threadsafe(q.put_nowait, request)
                    except json.JSONDecodeError as e:
                        log.error("Invalid JSON on stdin: %s", e)
                        # 通过队列发送错误（而不是直接用 write_event，避免线程安全问题）
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            {"method": "_invalid_json", "params": {"error": str(e)}}
                        )
            except Exception as e:
                log.exception("stdin reader thread error: %s", e)
            finally:
                log.info("stdin reader thread exiting")
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    {"method": "_shutdown"}
                )
        
        # 在默认 executor 中启动线程
        await loop.run_in_executor(None, _reader_thread)

    async def _process_queue(self):
        """处理 stdin 消息队列"""
        while self.rpc.is_running():
            try:
                # 阻塞等待队列中的下一个请求（不用 wait_for，Python 3.9 有 bug）
                request = await self._stdin_queue.get()

                if request.get("method") == "_shutdown":
                    if self.rpc_port > 0:
                        # TCP 模式下 stdin 关闭只是 stdin reader 退出
                        # daemon 继续为 TCP 客户端服务
                        log.info("stdin closed, daemon continues in TCP-only mode")
                        break
                    else:
                        log.info("Shutdown signal received from stdin")
                        await self.stop()
                        break

                if request.get("method") == "_invalid_json":
                    # 忽略无效 JSON（已在 stderr 记录）
                    continue

                await self.dispatch(request)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception("Error processing request")

    # ─── TCP JSON-RPC 服务器 ────────────────────────────

    def _broadcast_to_tcp(self, line: str):
        """将事件广播到所有 TCP 客户端"""
        dead = set()
        for reader, writer in self._tcp_clients:
            try:
                writer.write((line + "\n").encode("utf-8"))
            except Exception:
                dead.add((reader, writer))
        self._tcp_clients -= dead

    async def _handle_tcp_client(self, reader, writer):
        """处理单个 TCP JSON-RPC 客户端"""
        addr = writer.get_extra_info("peername", ("?", 0))
        log.info("TCP client connected: %s:%s", *addr)
        self._tcp_clients.add((reader, writer))

        def tcp_responder(line: str):
            """TCP 客户端响应回调 — 只发给这个连接"""
            try:
                writer.write((line + "\n").encode("utf-8"))
            except Exception:
                pass

        try:
            while self.rpc.is_running():
                line = await asyncio.wait_for(reader.readline(), timeout=60)
                if not line:  # 连接关闭
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Invalid JSON from TCP client")
                    continue
                # 处理请求（带 TCP 响应回调）
                await self.dispatch(request, responder=tcp_responder)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            log.debug("TCP client error: %s", e)
        finally:
            self._tcp_clients.discard((reader, writer))
            try:
                writer.close()
            except Exception:
                pass
            log.info("TCP client disconnected: %s:%s", *addr)

    async def _start_tcp_server(self):
        """启动 TCP JSON-RPC 服务器"""
        try:
            self._tcp_server = await asyncio.start_server(
                self._handle_tcp_client,
                host="127.0.0.1",
                port=self.rpc_port
            )
            actual_port = self._tcp_server.sockets[0].getsockname()[1]
            log.info("TCP JSON-RPC server on 127.0.0.1:%s", actual_port)
            self.rpc.write_event("tcp_ready", {
                "host": "127.0.0.1",
                "port": actual_port
            })
            return actual_port
        except Exception as e:
            log.error("Failed to start TCP server: %s", e)
            return 0

    # ─── 主运行循环 ─────────────────────────────────────

    async def run(self):
        """主运行循环"""
        try:
            # 0. 初始化队列（必须在 event loop 运行后创建，Python 3.9 兼容）
            if self._stdin_queue is None:
                self._stdin_queue = asyncio.Queue()

            # 0.1 设置 JSON-RPC 的 TCP 广播器
            self.rpc.set_broadcaster(self._broadcast_to_tcp)

            # 0.2 启动 TCP JSON-RPC 服务器（如果配置了 --rpc-port）
            tcp_task = None
            if self.rpc_port > 0:
                tcp_task = asyncio.create_task(self._start_tcp_server())

            # 1. 启动 stdin 读取器（后台）— 仅在非纯 TCP 模式下
            #    TCP 模式下 stdin 可能阻塞且不需要
            stdin_task = None
            if self.rpc_port == 0:
                stdin_task = asyncio.create_task(self._read_stdin_loop())
            queue_task = asyncio.create_task(self._process_queue())

            # 2. 启动 P2P 节点
            await self.start()

            # 3. 等待任务完成（stdin 关闭或收到 stop 命令）
            tasks = []
            if stdin_task:
                tasks.append(stdin_task)
            tasks.append(queue_task)
            if tcp_task:
                tasks.append(tcp_task)
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            log.exception("Fatal error: %s", e)
            self.rpc.write_event("fatal_error", {"message": str(e)})
        finally:
            # 关闭 TCP 服务器
            if self._tcp_server:
                self._tcp_server.close()
                await self._tcp_server.wait_closed()
            # 关闭所有 TCP 客户端
            for _, writer in list(self._tcp_clients):
                try:
                    writer.close()
                except Exception:
                    pass
            self._tcp_clients.clear()
            await self.stop()


# ─── CLI 入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="P2P Chat JSON-RPC Daemon (for OpenClaw child_process integration)"
    )
    parser.add_argument(
        "--handle", "-H", required=True,
        help="P2P identity handle (e.g. @lobster)"
    )
    parser.add_argument(
        "--data-dir", "-d", default=DATA_DIR,
        help=f"Data directory (default: {DATA_DIR})"
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Listen host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=0,
        help="Gossip P2P listen port (default: 0 = random)"
    )
    parser.add_argument(
        "--rpc-port", type=int, default=0,
        help="TCP JSON-RPC server port (default: 0 = TCP disabled, stdin/stdout only)"
    )
    parser.add_argument(
        "--network", default="main",
        choices=["main", "testnet"],
        help="BSV network (default: main)"
    )
    parser.add_argument(
        "--log-level", default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level for stderr (default: WARNING)"
    )

    args = parser.parse_args()

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # 输出初始化信息到 stderr
    log.info("P2P Chat Daemon v%s starting...", VERSION)
    log.info("  Handle: @%s", args.handle.lstrip("@"))
    log.info("  Data dir: %s", args.data_dir)
    log.info("  Listen: %s:%s", args.host, args.port or "random")
    log.info("  Network: %s", args.network)

    # 创建并运行守护进程
    daemon = P2PDaemon(
        handle=args.handle,
        data_dir=args.data_dir,
        listen_host=args.host,
        listen_port=args.port,
        network=args.network,
        rpc_port=args.rpc_port
    )

    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        log.info("Daemon exited")


if __name__ == "__main__":
    main()
