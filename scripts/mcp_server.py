#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
铭信 (MingChat) MCP Server - 标准版
提供 send/read/status 三个工具

用于AI Agent通过MCP协议调用铭信功能
"""

import os
import sys
import json
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from mingchat import MingChat, MsgType, hash160_to_address

# MCP Server实现
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    from mcp.server.stdio import stdio_server
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


# 工具定义
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
                    "enum": ["CHAT", "RPC_REQ", "BROADCAST", "PUBLISH", "BID", "ASSIGN"],
                    "description": "消息类型，默认CHAT",
                    "default": "CHAT"
                }
            },
            "required": ["receiver_address", "body"]
        }
    },
    {
        "name": "mingchat_read",
        "description": "读取铭信消息",
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
    }
]


def load_key():
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


def handle_send(params: dict) -> dict:
    """处理发送请求"""
    wif = load_key()
    if not wif:
        return {"error": "需要配置私钥"}
    
    client = MingChat(wif)
    
    receiver = params.get("receiver_address")
    body = params.get("body")
    msg_type = MsgType.from_str(params.get("msg_type", "CHAT"))
    
    try:
        msg = client.send(receiver, body, msg_type)
        return {
            "success": True,
            "txid": msg.txid,
            "type": msg.msg_type.to_str(),
            "timestamp": msg.timestamp
        }
    except Exception as e:
        return {"error": str(e)}


def handle_read(params: dict) -> dict:
    """处理读取请求"""
    wif = load_key()
    
    address = params.get("address")
    limit = params.get("limit", 20)
    
    if address:
        client = MingChat(wif) if wif else MingChat()
        msgs = client.get_messages(address, limit)
    else:
        if not wif:
            return {"error": "需要私钥查看收件箱"}
        client = MingChat(wif)
        msgs = client.get_inbox(limit)
    
    return {
        "messages": [
            {
                "type": m.msg_type.to_str(),
                "sender": hash160_to_address(m.sender_hash160, client.network),
                "body": m.get_body_text(),
                "timestamp": m.timestamp,
                "txid": m.txid
            }
            for m in msgs
        ],
        "count": len(msgs)
    }


def handle_status(params: dict) -> dict:
    """处理状态请求"""
    wif = load_key()
    if not wif:
        return {"error": "需要配置私钥"}
    
    client = MingChat(wif)
    return client.status()


def main():
    """主函数 - 标准MCP Server"""
    if not HAS_MCP:
        # 降级为简单HTTP服务器模式
        print("MCP SDK未安装，使用简单stdio模式", file=sys.stderr)
        run_simple_mode()
        return
    
    server = Server("mingchat-mcp")
    
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
        if name == "mingchat_send":
            result = handle_send(arguments)
        elif name == "mingchat_read":
            result = handle_read(arguments)
        elif name == "mingchat_status":
            result = handle_status(arguments)
        else:
            result = {"error": f"未知工具: {name}"}
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    
    # 运行服务器
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
            id = request.get("id")
            
            if method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {"tools": TOOLS}
                }
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments", {})
                
                if name == "mingchat_send":
                    result = handle_send(args)
                elif name == "mingchat_read":
                    result = handle_read(args)
                elif name == "mingchat_status":
                    result = handle_status(args)
                else:
                    result = {"error": f"Unknown tool: {name}"}
                
                response = {
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
                }
            else:
                response = {"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "Method not found"}}
            
            print(json.dumps(response), flush=True)
        except Exception as e:
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
