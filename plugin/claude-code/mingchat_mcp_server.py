#!/usr/bin/env python3.9
"""
铭信 MingChat — Claude Code MCP Server
======================================

让 Claude Code 获得端到端加密 P2P 通信能力。

协议: MCP (Model Context Protocol) over stdio
桥接: Claude Code ←→ MCP Server ←→ TCP JSON-RPC ←→ MingChat Daemon

用法:
  1. 确保铭信守护进程已运行:
     python3 p2p_daemon.py --handle @agent --rpc-port 9877

  2. 在 Claude Code 配置文件 (~/.claude.json 或项目 .mcp.json) 中添加:
     {
       "mcpServers": {
         "mingchat": {
           "command": "python3.9",
           "args": ["plugin/claude-code/mingchat_mcp_server.py"],
           "env": {"MINGCHAT_RPC_HOST": "127.0.0.1", "MINGCHAT_RPC_PORT": "9877"}
         }
       }
     }

  3. Claude Code 重启后获得 8 个 mingchat_* 工具

零依赖: 仅使用 Python 3.9 标准库（socket + json + sys）
"""

from __future__ import annotations

import json
import os
import socket
import sys
import logging
import threading
from typing import Any, Callable, Dict, Optional

# ─── 配置（环境变量覆盖）─────────────────────────────────
RPC_HOST = os.environ.get("MINGCHAT_RPC_HOST", "127.0.0.1")
RPC_PORT = int(os.environ.get("MINGCHAT_RPC_PORT", "9877"))

# ─── 日志到 stderr（不干扰 stdout 的 MCP 通信）───────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [mingchat-mcp] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mingchat-mcp")

# ─── 协议常量 ────────────────────────────────────────────
MCP_VERSION = "2024-11-05"
SERVER_NAME = "mingchat-mcp"
SERVER_VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════════
# JSON-RPC TCP 客户端（连接铭信守护进程）
# ═══════════════════════════════════════════════════════════════

class MingChatClient:
    """铭信守护进程 JSON-RPC TCP 客户端。线程安全。"""

    def __init__(self, host: str = RPC_HOST, port: int = RPC_PORT):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._running = False

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            self._sock.connect((self._host, self._port))
            self._sock.settimeout(30)
            self._running = True
            log.info("已连接到铭信守护进程 %s:%d", self._host, self._port)
            return True
        except Exception as e:
            log.warning("连接铭信守护进程失败: %s", e)
            self._sock = None
            return False

    def close(self):
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

    def call(self, method: str, params: dict = None) -> dict:
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
                self._sock.sendall(
                    (json.dumps(req, ensure_ascii=False) + "\n").encode()
                )
                buf = b""
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("Daemon closed connection")
                    buf += chunk
                    if b"\n" in buf:
                        break
                resp = json.loads(buf.decode().strip().split("\n")[0])
                if "result" in resp:
                    return resp["result"]
                elif "error" in resp:
                    return {"error": resp["error"].get("message", str(resp["error"]))}
                return resp
            except Exception as e:
                log.error("RPC 调用失败 [%s]: %s", method, e)
                self._running = False
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# MCP 工具定义
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "mingchat_send",
        "description": "通过铭信发送端到端加密 P2P 私信。ECDH+AES-256-GCM 加密，对方不在线时回退到 BSV 链上存证。",
        "inputSchema": {
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
    },
    {
        "name": "mingchat_broadcast",
        "description": "通过铭信 P2P gossip 网格向所有已连接节点广播消息。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要广播的消息内容",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "mingchat_status",
        "description": "查看铭信节点运行状态：在线状态、SPV 区块头同步进度、已连接对等节点数、消息统计。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mingchat_contacts",
        "description": "列出铭信中的所有联系人及其公钥。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mingchat_add_contact",
        "description": "添加铭信联系人。需要对方的 handle 和 secp256k1 压缩公钥（66 字符 hex）。",
        "inputSchema": {
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
    },
    {
        "name": "mingchat_connect_peer",
        "description": "连接到另一个铭信对等节点，加入 P2P gossip 网格。",
        "inputSchema": {
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
        },
    },
    {
        "name": "mingchat_history",
        "description": "获取铭信消息历史。可按联系人过滤。",
        "inputSchema": {
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
    },
    {
        "name": "mingchat_identity",
        "description": "查看本地铭信身份信息：handle、secp256k1 公钥、seed 哈希、联系人数量。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# 工具名 → daemon RPC 方法 + 参数映射
METHOD_MAP = {
    "mingchat_send": ("send_message", lambda a: {"to": a["to"], "content": a["content"]}),
    "mingchat_broadcast": ("broadcast", lambda a: {"content": a["content"]}),
    "mingchat_status": ("status", lambda a: {}),
    "mingchat_contacts": ("list_contacts", lambda a: {}),
    "mingchat_add_contact": ("add_contact", lambda a: {"handle": a["handle"], "pubkey": a["pubkey"]}),
    "mingchat_connect_peer": ("connect_peer", lambda a: {"host": a.get("host", "127.0.0.1"), "port": a.get("port", 9876)}),
    "mingchat_history": ("history", lambda a: {"with": a.get("handle", ""), "limit": min(a.get("limit", 50), 200)}),
    "mingchat_identity": ("get_identity", lambda a: {}),
}


# ═══════════════════════════════════════════════════════════════
# MCP stdio JSON-RPC 服务器
# ═══════════════════════════════════════════════════════════════

class MingChatMCPServer:
    """MCP 协议服务器 — stdin/stdout JSON-RPC 2.0。

    在 Claude Code 作为子进程启动，通过 stdio 通信。
    """

    def __init__(self):
        self._client = MingChatClient()
        self._initialized = False
        self._stdout_lock = threading.Lock()

    def _write(self, data: dict):
        """写入 JSON-RPC 响应到 stdout（线程安全）。"""
        line = json.dumps(data, ensure_ascii=False, default=str)
        with self._stdout_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def _handle_initialize(self, msg_id, params: dict) -> dict:
        """MCP initialize 握手。"""
        self._initialized = True
        # 尝试连接守护进程
        if not self._client.is_connected:
            self._client.connect()
        return {
            "protocolVersion": MCP_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        }

    def _handle_tools_list(self, msg_id, params: dict) -> dict:
        """列出所有可用工具。"""
        return {"tools": TOOLS}

    def _handle_tools_call(self, msg_id, params: dict) -> dict:
        """调用工具并返回结果。"""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in METHOD_MAP:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

        daemon_method, arg_mapper = METHOD_MAP[tool_name]
        daemon_params = arg_mapper(arguments)
        result = self._client.call(daemon_method, daemon_params)

        # 格式化返回结果
        if "error" in result:
            return {
                "content": [{"type": "text", "text": f"Error: {result['error']}"}],
                "isError": True,
            }

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }
            ],
        }

    def _handle_ping(self, msg_id, params: dict) -> dict:
        return {}

    def run(self):
        """主循环 — 从 stdin 逐行读取 MCP 请求，写入响应到 stdout。"""

        # 确保 stdout 行缓冲
        if not sys.stdout.isatty():
            sys.stdout = os.fdopen(
                sys.stdout.fileno(), "w", buffering=1, encoding="utf-8"
            )

        log.info("铭信 MCP 服务器启动 — 等待 Claude Code 连接...")

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                log.error("无效 JSON: %s", e)
                continue

            msg_id = request.get("id")
            method = request.get("method", "")

            # 分发
            try:
                if method == "initialize":
                    result = self._handle_initialize(msg_id, request.get("params", {}))
                    self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

                elif method == "tools/list":
                    result = self._handle_tools_list(msg_id, request.get("params", {}))
                    self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

                elif method == "tools/call":
                    result = self._handle_tools_call(msg_id, request.get("params", {}))
                    self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

                elif method == "ping":
                    result = self._handle_ping(msg_id, request.get("params", {}))
                    self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

                elif method == "notifications/initialized":
                    # 客户端确认初始化完成 — 无需响应
                    log.info("Claude Code 初始化完成")

                else:
                    self._write({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    })

            except Exception as e:
                log.exception("处理请求失败 [%s]", method)
                self._write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32000, "message": str(e)},
                })

        log.info("铭信 MCP 服务器退出")
        self._client.close()


# ─── 入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    server = MingChatMCPServer()
    server.run()
