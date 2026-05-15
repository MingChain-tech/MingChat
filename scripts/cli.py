#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
铭信 (MingChat) CLI - 命令行工具

用法:
  mingchat send --to <address> --body <text> [--type CHAT]
  mingchat read [--address <address>] [--limit 20]
  mingchat status
  mingchat listen
"""

import argparse
import json
import sys
import os
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from mingchat import MingChat, MsgType, hash160_to_address


def load_key_from_file():
    """从配置文件加载私钥"""
    key_paths = [
        os.path.expanduser("~/.hermes/workspace/mingchat-key.md"),
        os.path.expanduser("~/.mingchat/key"),
        "/root/.hermes/workspace/mingchat-key.md",
    ]
    
    for path in key_paths:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read()
                # 支持WIF格式或十六进制格式
                content = content.strip()
                if content.startswith('L') or content.startswith('K') or content.startswith('5'):
                    return content  # WIF格式
                elif len(content) == 64:
                    return content  # 十六进制
                else:
                    # 尝试解析为JSON
                    try:
                        data = json.loads(content)
                        return data.get('wif') or data.get('private_key') or data.get('key')
                    except:
                        pass
    return None


def cmd_send(args):
    """发送消息"""
    wif = args.key or load_key_from_file()
    if not wif:
        print("错误: 需要私钥。使用 --key 参数或配置 ~/.hermes/workspace/mingchat-key.md")
        sys.exit(1)
    
    client = MingChat(wif)
    
    msg_type = MsgType.from_str(args.type) if args.type else MsgType.CHAT
    
    try:
        msg = client.send(args.to, args.body, msg_type)
        print(json.dumps({
            "success": True,
            "txid": msg.txid,
            "type": msg.msg_type.to_str(),
            "receiver": args.to,
            "timestamp": msg.timestamp
        }, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_read(args):
    """读取消息"""
    wif = args.key or load_key_from_file()
    limit = args.limit or 20
    
    if args.address:
        # 查看指定地址的消息
        client = MingChat(wif) if wif else MingChat()
        msgs = client.get_messages(args.address, limit)
    else:
        # 查看自己的收件箱
        if not wif:
            print("错误: 需要私钥查看收件箱。使用 --key 参数或配置私钥文件")
            sys.exit(1)
        client = MingChat(wif)
        msgs = client.get_inbox(limit)
    
    if not msgs:
        print("没有消息")
        return
    
    for msg in msgs:
        sender = hash160_to_address(msg.sender_hash160, client.network)
        ts = msg.timestamp
        try:
            from datetime import datetime
            ts = datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except:
            ts = str(msg.timestamp)
        
        print(f"\n{'─'*60}")
        print(f"类型: {msg.msg_type.to_str()}")
        print(f"来自: {sender}")
        print(f"时间: {ts}")
        print(f"TXID: {msg.txid}")
        print(f"内容: {msg.get_body_text()[:300]}")
    
    print(f"\n共 {len(msgs)} 条消息")


def cmd_status(args):
    """显示钱包状态"""
    wif = args.key or load_key_from_file()
    
    if not wif:
        print("错误: 需要私钥。使用 --key 参数或配置私钥文件")
        sys.exit(1)
    
    client = MingChat(wif)
    status = client.status()
    
    print(json.dumps({
        "address": status["address"],
        "balance_sats": status["balance_sats"],
        "balance_bsv": f'{status["balance_bsv"]:.8f}',
        "network": status["network"],
        "listening": status["listening"]
    }, indent=2, ensure_ascii=False))


def cmd_listen(args):
    """实时监听消息"""
    wif = args.key or load_key_from_file()
    
    if not wif:
        print("错误: 需要私钥。使用 --key 参数或配置私钥文件")
        sys.exit(1)
    
    client = MingChat(wif)
    
    print(f"开始监听消息...")
    print(f"地址: {client.address}")
    print(f"按 Ctrl+C 停止")
    
    def on_message(msg):
        print(f"\n收到消息: {msg.msg_type.to_str()}")
    
    client.listen(on_message)
    
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止监听")
        client.stop()


def main():
    parser = argparse.ArgumentParser(
        description="铭信 (MingChat) CLI - BSV区块链通讯工具",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("--key", help="WIF私钥（可选，默认从配置文件加载）")
    
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # send 命令
    send_parser = subparsers.add_parser("send", help="发送消息")
    send_parser.add_argument("--to", required=True, help="接收方地址")
    send_parser.add_argument("--body", required=True, help="消息内容")
    send_parser.add_argument("--type", default="CHAT", help="消息类型 (CHAT/RPC_REQ/BROADCAST等)")
    
    # read 命令
    read_parser = subparsers.add_parser("read", help="读取消息")
    read_parser.add_argument("--address", help="查看指定地址的消息（不指定则查看收件箱）")
    read_parser.add_argument("--limit", type=int, default=20, help="消息数量限制")
    
    # status 命令
    status_parser = subparsers.add_parser("status", help="显示钱包状态")
    
    # listen 命令
    listen_parser = subparsers.add_parser("listen", help="实时监听消息")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "send":
        cmd_send(args)
    elif args.command == "read":
        cmd_read(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "listen":
        cmd_listen(args)


if __name__ == "__main__":
    main()
