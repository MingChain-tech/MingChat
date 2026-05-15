#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
铭信 (MingChat) MCP Server - 增强版
提供 send/read/status/listen/inbox 五个工具

用于AI Agent通过MCP协议调用铭信功能
域名: mingchain.tech
"""

import os
import sys
import json
import threading
import time
from pathlib import Path
from typing import Optional, List, Callable

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from mingchat import MingChat, MsgType, Message, hash160_to_address

# MCP Server实现
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    from mcp.server.stdio import stdio_server
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


# 工具定义 - 增强版
TOOLS = [
    {
        "name": "mingchat_send",
        "description": "发送铭信消息到BSV区块链",
        "inputSchema": {
            "type": "object",
            "properties": {
                "receiver_address": {
                    "type": "string",
                    "description": "接收方BSV地址"
                },
                "body": {
                    "type": "string", 
                    "description": "消息内容"
                },
                "msg_type": {
                    "type": "string",
                    "enum": ["CHAT", "RPC_REQ", "BROADCAST", "PUBLISH", "BID", "ASSIGN", "DID_REGISTER"],
                    "description": "消息类型，默认CHAT",
                    "default": "CHAT"
                }
            },
            "required": ["receiver_address", "body"]
        }
    },
    {
        "name": "mingchat_read",
        "description": "读取铭信消息（指定地址或收件箱）",
        "inputSchema": {
            "type": "object", 
            "properties": {
                "address": {
                    "type": "string",
                    "description": "地址（不填则查看收件箱）"
                },
                "limit": {
                    "type": "number",
                    "description": "消息数量限制",
                    "default": 20
                }
            }
        }
    },
    {
        "name": "mingchat_status",
        "description": "获取铭信钱包状态",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "mingchat_listen",
        "description": "开始实时监听链上消息（后台轮询）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "auto_start": {
                    "type": "boolean",
                    "description": "是否自动开始监听",
                    "default": True
                }
            }
        }
    },
    {
        "name": "mingchat_inbox",
        "description": "获取收件箱（只返回发给自己的消息）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "number",
                    "description": "消息数量限制",
                    "default": 20
                }
            }
        }
    },
    {
        "name": "mingchat_rpc",
        "description": "发送RPC请求并等待响应",
        "inputSchema": {
            "type": "object",
            "properties": {
                "receiver_address": {
                    "type": "string",
                    "description": "接收方地址"
                },
                "method": {
                    "type": "string", 
                    "description": "RPC方法名"
                },
                "params": {
                    "type": "object",
                    "description": "RPC参数"
                }
            },
            "required": ["receiver_address", "method"]
        }
    }
]


class MingChatMCP:
    """MingChat MCP 核心类"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self):
        self._client: Optional[MingChat] = None
        self._listening = False
        self._listener_thread: Optional[threading.Thread] = None
        self._message_buffer: List[dict] = []
        self._buffer_lock = threading.Lock()
    
    def load_key(self) -> Optional[str]:
        """加载私钥"""
        key_path = os.environ.get("MINGCHAT_KEY_PATH", "/root/.hermes/workspace/mingchat-key.md")
        
        if os.path.exists(key_path):
            with open(key_path, 'r') as f:
                content = f.read().strip()
                if content.startswith('L') or content.startswith('K') or content.startswith('5'):
                    return content
                try:
                    data = json.loads(content)
                    return data.get('wif') or data.get('private_key')
                except:
                    return content
        return None
    
    def get_client(self) -> MingChat:
        """获取或创建客户端"""
        if self._client is None:
            wif = self.load_key()
            if not wif:
                raise RuntimeError("需要配置私钥")
            self._client = MingChat(wif)
        return self._client
    
    def send(self, receiver_address: str, body: str, msg_type: str = "CHAT") -> dict:
        """发送消息"""
        client = self.get_client()
        mtype = MsgType.from_str(msg_type)
        
        msg = client.send(receiver_address, body, mtype)
        return {
            "success": True,
            "txid": msg.txid,
            "type": msg.msg_type.to_str(),
            "timestamp": msg.timestamp
        }
    
    def read(self, address: str = None, limit: int = 20) -> dict:
        """读取消息"""
        client = self.get_client()
        
        if address:
            msgs = client.get_messages(address, limit)
        else:
            msgs = client.get_inbox(limit)
        
        return {
            "messages": [self._msg_to_dict(m, client.network) for m in msgs],
            "count": len(msgs)
        }
    
    def status(self) -> dict:
        """获取状态"""
        client = self.get_client()
        return client.status()
    
    def inbox(self, limit: int = 20) -> dict:
        """获取收件箱"""
        client = self.get_client()
        msgs = client.get_inbox(limit)
        return {
            "messages": [self._msg_to_dict(m, client.network) for m in msgs],
            "count": len(msgs)
        }
    
    def listen(self, auto_start: bool = True) -> dict:
        """开始监听"""
        if self._listening:
            return {"status": "already_listening", "message": "已经在监听中"}
        
        client = self.get_client()
        self._listening = True
        
        def on_message(msg: Message):
            with self._buffer_lock:
                self._message_buffer.append(self._msg_to_dict(msg, client.network))
                # 只保留最近100条
                if len(self._message_buffer) > 100:
                    self._message_buffer = self._message_buffer[-100:]
        
        client.listen(on_message)
        
        return {"status": "listening", "message": "开始监听消息"}
    
    def get_new_messages(self) -> dict:
        """获取新消息（从缓冲区）"""
        with self._buffer_lock:
            msgs = self._message_buffer.copy()
            self._message_buffer.clear()
        
        return {
            "messages": msgs,
            "count": len(msgs)
        }
    
    def rpc(self, receiver_address: str, method: str, params: dict = None) -> dict:
        """发送RPC请求"""
        client = self.get_client()
        result = client.rpc_call(receiver_address, method, params or {})
        return {
            "success": True,
            "rpc_id": result.get("rpc_id"),
            "txid": result.get("txid"),
            "method": method
        }
    
    def _msg_to_dict(self, msg: Message, network: str) -> dict:
        """消息转字典"""
        return {
            "type": msg.msg_type.to_str(),
            "sender": hash160_to_address(msg.sender_hash160, network),
            "body": msg.get_body_text(),
            "timestamp": msg.timestamp,
            "txid": msg.txid
        }


# 全局实例
mcp = MingChatMCP()


def handle_tool_call(name: str, params: dict) -> dict:
    """处理工具调用"""
    try:
        if name == "mingchat_send":
            return mcp.send(
                params.get("receiver_address"),
                params.get("body"),
                params.get("msg_type", "CHAT")
            )
        elif name == "mingchat_read":
            return mcp.read(
                params.get("address"),
                params.get("limit", 20)
            )
        elif name == "mingchat_status":
            return mcp.status()
        elif name == "mingchat_listen":
            return mcp.listen(params.get("auto_start", True))
        elif name == "mingchat_inbox":
            return mcp.inbox(params.get("limit", 20))
        elif name == "mingchat_rpc":
            return mcp.rpc(
                params.get("receiver_address"),
                params.get("method"),
                params.get("params")
            )
        else:
            return {"error": f"未知工具: {name}"}
    except Exception as e:
        return {"error": str(e)}


def main():
    """主函数"""
    if not HAS_MCP:
        print("MCP SDK未安装，使用简单stdio模式", file=sys.stderr)
        run_simple_mode()
        return
    
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    from mcp.server.stdio import stdio_server
    
    server = Server("mingchat-mcp-enhanced")
    
    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"]
            )
            for t in TOOLS
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = handle_tool_call(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    
    import asyncio
    asyncio.run(stdio_server.run(server))


def run_simple_mode():
    """简单stdio交互模式"""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            request = json.loads(line.strip())
            method = request.get("method", "")
            params = request.get("params", {})
            id_val = request.get("id")
            
            if method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": id_val,
                    "result": {"tools": TOOLS}
                }
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments", {})
                
                result = handle_tool_call(name, args)
                
                response = {
                    "jsonrpc": "2.0",
                    "id": id_val,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
                }
            else:
                response = {"jsonrpc": "2.0", "id": id_val, "error": {"code": -32601, "message": "Method not found"}}
            
            print(json.dumps(response), flush=True)
        except Exception as e:
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
