#!/usr/bin/env python3
"""
P2P Chat CLI — 对标 bsv-poker 的 poker.exe (Chat tab)
= 命令行界面：发送消息、查看对话、管理联系人、节点发现

用法:
  # 启动节点
  python cli.py start --handle @alice [--host 0.0.0.0] [--port 9876]

  # 连接到另一个节点
  python cli.py connect --to 127.0.0.1:9877 --handle @bob

  # 交互式聊天
  python cli.py chat --handle @alice [--connect 127.0.0.1:9877]
"""
import asyncio
import sys
import os
import argparse
import logging
import signal
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("cli")

DATA_DIR = os.path.expanduser("~/.p2pchat")


async def cmd_start(args):
    """启动 P2P 节点"""
    from identity import Identity
    from app import P2PChat

    ident_path = Path(DATA_DIR) / f"identity_{args.handle}.json"

    if ident_path.exists():
        identity = Identity.load(str(ident_path))
        print(f"✅ Loaded identity: @{identity.handle}")
    else:
        identity = Identity.create(args.handle)
        identity.save(str(ident_path))
        print(f"✅ Created new identity: @{identity.handle}")
        print(f"   Pubkey: {identity.pubkey_hex[:32]}...")
        print(f"   Saved to: {ident_path}")

    chat = P2PChat(identity,
                   host=args.host or "127.0.0.1",
                   port=args.port or 0,
                   data_dir=DATA_DIR)

    await chat.start(args.host or "127.0.0.1", args.port or 0)

    # 打印连接信息
    print()
    print("=" * 60)
    print(f"  P2P Chat Node: @{identity.handle}")
    print(f"  Listening: {chat.mesh.host}:{chat.mesh.port}")
    print(f"  Pubkey: {identity.pubkey_hex}")
    print("=" * 60)
    print()
    print("Commands:")
    print(f"  Connect another node:")
    print(f"    python3 cli.py connect --to {chat.mesh.host}:{chat.mesh.port} --handle <name>")
    print()
    print("Press Ctrl+C to stop")

    # 保持运行
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await chat.stop()


async def cmd_connect(args):
    """连接到已有节点"""
    from identity import Identity
    from app import P2PChat

    ident_path = Path(DATA_DIR) / f"identity_{args.handle}.json"

    if ident_path.exists():
        identity = Identity.load(str(ident_path))
        print(f"✅ Loaded identity: @{identity.handle}")
    else:
        identity = Identity.create(args.handle)
        identity.save(str(ident_path))
        print(f"✅ Created new identity: @{identity.handle}")

    host, port = args.to.split(":")
    port = int(port)

    chat = P2PChat(identity, data_dir=DATA_DIR)
    await chat.start()

    print(f"🔗 Connecting to {host}:{port}...")
    ok = await chat.connect_peer(host, port)
    if ok:
        print(f"✅ Connected!")
        # 等待 HELLO 交换
        await asyncio.sleep(1)
        print(f"   My pubkey: {identity.pubkey_hex}")
        print(f"   Send this to the other node to add as contact")
        print()
        print("   Share your pubkey. On the other node:")
        print(f"     /add @{identity.handle} {identity.pubkey_hex}")
    else:
        print(f"❌ Connection failed")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await chat.stop()


async def cmd_chat(args):
    """交互式聊天"""
    from identity import Identity
    from app import P2PChat

    ident_path = Path(DATA_DIR) / f"identity_{args.handle}.json"

    if ident_path.exists():
        identity = Identity.load(str(ident_path))
        print(f"✅ @{identity.handle} | {identity.pubkey_hex[:16]}...")
    else:
        identity = Identity.create(args.handle)
        identity.save(str(ident_path))
        print(f"✅ Created @{identity.handle} | {identity.pubkey_hex[:16]}...")

    chat = P2PChat(identity, data_dir=DATA_DIR)
    await chat.start()

    # 连接到 seed peer
    if args.connect:
        for addr in args.connect.split(","):
            host, port = addr.strip().split(":")
            await chat.connect_peer(host, int(port))
            await asyncio.sleep(0.5)

    print()
    print("=" * 50)
    print(f"  P2P Chat — @{identity.handle}")
    print(f"  Listening: {chat.mesh.host}:{chat.mesh.port}")
    print("=" * 50)
    print("Commands:")
    print("  @handle message   — send DM (encrypted)")
    print("  /broadcast msg    — send to all")
    print("  /add @handle pk   — add contact")
    print("  /contacts         — list contacts")
    print("  /peers            — list connected peers")
    print("  /history @handle  — show history")
    print("  /status           — show status")
    print("  /pubkey           — show my pubkey")
    print("  /help             — this help")
    print("  /quit             — exit")
    print()

    # Start async input reader
    loop = asyncio.get_event_loop()

    def handle_stdin():
        line = sys.stdin.readline()
        if line:
            asyncio.run_coroutine_threadsafe(
                process_command(chat, line.strip()), loop
            )

    loop.add_reader(sys.stdin, handle_stdin)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await chat.stop()


async def process_command(chat, line: str):
    """处理命令行输入"""
    from app import P2PChat

    if not line:
        return

    if line.startswith("/"):
        parts = line.split(maxsplit=2)
        cmd = parts[0][1:].lower()
        args = parts[1] if len(parts) > 1 else ""
        args2 = parts[2] if len(parts) > 2 else ""

        if cmd == "add":
            # /add @handle <pubkey_hex>
            handle = args.lstrip("@")
            pk = args2
            chat.identity.add_contact(handle, pk)
            # 保存到文件
            ident_path = Path(DATA_DIR) / f"identity_{chat.identity.handle}.json"
            chat.identity.save(str(ident_path))
            print(f"✅ Added contact @{handle}")

        elif cmd == "contacts":
            contacts = chat.get_contact_list()
            if contacts:
                print("Contacts:")
                for c in contacts:
                    print(f"  @{c['handle']} — {c['pk']}")
            else:
                print("No contacts yet")

        elif cmd == "peers":
            peers = chat.mesh.peer_list
            if peers:
                print("Connected peers:")
                for p in peers:
                    print(f"  @{p['handle']} — {p['address']} — {p['connected']}")
            else:
                print("No connected peers")

        elif cmd == "history":
            handle = args.lstrip("@") if args else None
            msgs = chat.get_recent_messages(handle)
            if msgs:
                print(f"Recent messages ({len(msgs)}):")
                for m in msgs[-20:]:
                    direction = "→" if m.from_handle == chat.identity.handle else "←"
                    t = time.strftime('%H:%M', time.localtime(m.timestamp)) if hasattr(m, 'timestamp') else "??:??"
                    print(f"  {t} {direction} {m.content[:80]}")
            else:
                print("No messages")

        elif cmd == "status":
            s = chat.status
            print(f"Handle: @{s['handle']}")
            print(f"Pubkey: {s['pubkey']}")
            print(f"Listening: {s['listening']}")
            print(f"Peers: {s['peers']}")
            print(f"Messages: {s['messages']}")
            print(f"Contacts: {s['contacts']}")
            spv = s.get("spv", {})
            print(f"SPV: {'✅ synced' if spv.get('synced') else '⏳ syncing...'}")
            print(f"  Verified headers: {spv.get('headers', 0)}")
            print(f"  Tip: {spv.get('tip_hash', 'none')}")
            print(f"  Network: {spv.get('network', '?')}")

        elif cmd == "spv":
            s = chat.status.get("spv", {})
            print(f"SPV Status:")
            print(f"  Synced: {'✅ YES' if s.get('synced') else '⏳ In progress...'}")
            print(f"  Verified Headers: {s.get('headers', 0)}")
            print(f"  Chain Tip: {s.get('tip_hash', 'none')}")
            print(f"  Network: {s.get('network', '?')}")
            print()
            print(f"  Your headers are independently verified.")
            print(f"  No trust in API required — every PoW and chain link checked.")

        elif cmd == "offline":
            print("Checking for offline messages...")
            # Trigger immediate check
            msgs = await chat.fetch_offline_now() if hasattr(chat, 'fetch_offline_now') else []
            verified = [m for m in msgs if m.get("spv_verified")]
            pending = [m for m in msgs if not m.get("spv_verified")]
            print(f"  SPV-verified: {len(verified)}")
            print(f"  Pending conf: {len(pending)}")
            for m in verified:
                data = m.get("data", {})
                print(f"  ✅ TX {m['txid'][:12]}... at height {m['height']}")

        elif cmd == "pubkey":
            print(f"Your pubkey:")
            print(f"  {chat.identity.pubkey_hex}")

        elif cmd == "broadcast":
            if args:
                await chat.send_broadcast(args + " " + args2 if args2 else args)
            else:
                print("Usage: /broadcast <message>")

        elif cmd == "help":
            print("Commands:")
            print("  @handle message   — send DM")
            print("  /add @handle pk   — add contact")
            print("  /contacts /peers  — list")
            print("  /history [@handle] — show messages")
            print("  /pubkey /status   — info")
            print("  /spv              — SPV verification status")
            print("  /offline          — check offline messages")
            print("  /quit             — exit")

        elif cmd == "quit" or cmd == "exit":
            print("Goodbye!")
            asyncio.get_event_loop().stop()

        else:
            print(f"Unknown command: /{cmd} (use /help)")

    elif line.startswith("@"):
        # @handle message
        parts = line.split(maxsplit=1)
        handle = parts[0][1:]  # remove @
        content = parts[1] if len(parts) > 1 else ""
        if content:
            await chat.send_message(handle, content)
        else:
            print("Usage: @handle <message>")


async def cmd_e2e_test(args):
    """
    端到端测试 — 对标 bsv-poker 的 RedTest
    在同一进程中模拟两个用户聊天
    """
    import time as _time
    from identity import Identity
    from app import P2PChat

    print("=" * 60)
    print("  P2P Chat — End-to-End Test")
    print("  Simulating @alice ↔ @bob conversation")
    print("=" * 60)

    # Alice
    alice_ident = Identity.create("alice")
    alice = P2PChat(alice_ident, port=0, data_dir="/tmp/p2pchat_test_alice")
    await alice.start()

    # Bob
    bob_ident = Identity.create("bob")
    bob = P2PChat(bob_ident, port=0, data_dir="/tmp/p2pchat_test_bob")
    await bob.start()

    # 交换联系人
    alice.identity.add_contact("bob", bob.identity.pubkey_hex)
    bob.identity.add_contact("alice", alice.identity.pubkey_hex)

    # Bob 连接 Alice
    ok = await bob.connect_peer(alice.mesh.host, alice.mesh.port)
    assert ok, "Connection failed!"
    print("✅ Bob connected to Alice")

    # 等待 HELLO 交换
    await asyncio.sleep(0.5)

    # Alice 发送消息给 Bob
    msg = await alice.send_message("bob", "Hello Bob! This is an encrypted message.")
    assert msg is not None, "Send failed!"
    print(f"✅ Alice sent: {msg.content[:50]}")

    # 等待消息传递
    await asyncio.sleep(0.5)

    # 检查 Bob 是否收到
    bob_msgs = bob.get_recent_messages("alice")
    assert len(bob_msgs) > 0, "Bob didn't receive message!"
    received = bob_msgs[-1]
    print(f"✅ Bob received: {received.content[:50]}")

    # Bob 回复
    msg2 = await bob.send_message("alice", "Hi Alice! Message received. 消息收到！")
    assert msg2 is not None, "Reply failed!"
    print(f"✅ Bob replied: {msg2.content[:50]}")

    await asyncio.sleep(0.3)

    # Alice 收到回复
    alice_msgs = alice.get_recent_messages("bob")
    assert len(alice_msgs) >= 2, "Alice didn't receive reply!"
    print(f"✅ Alice got reply: {alice_msgs[-1].content[:50]}")

    # 广播测试
    await alice.send_broadcast("Hello everyone!")
    await asyncio.sleep(0.3)

    # 统计
    print(f"\n📊 Stats:")
    print(f"   Alice messages: {len(alice.store.messages)}")
    print(f"   Bob messages: {len(bob.store.messages)}")
    print(f"   Alice peers: {len(alice.mesh._peers)}")
    print(f"   Bob peers: {len(bob.mesh._peers)}")

    # 清理
    await alice.stop()
    await bob.stop()

    print("\n🎉 ALL E2E TESTS PASSED!")


def main():
    parser = argparse.ArgumentParser(description="P2P Chat — Decentralized Instant Messaging")
    sub = parser.add_subparsers(dest="command")

    # start
    p_start = sub.add_parser("start", help="Start a P2P node")
    p_start.add_argument("--handle", required=True, help="@handle")
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=0)

    # connect
    p_conn = sub.add_parser("connect", help="Connect to a peer")
    p_conn.add_argument("--to", required=True, help="host:port")
    p_conn.add_argument("--handle", required=True)

    # chat
    p_chat = sub.add_parser("chat", help="Interactive chat")
    p_chat.add_argument("--handle", required=True)
    p_chat.add_argument("--connect", help="seed peer host:port (comma-separated)")
    p_chat.add_argument("--host", default="127.0.0.1")
    p_chat.add_argument("--port", type=int, default=0)

    # e2e test
    p_test = sub.add_parser("test", help="Run end-to-end test")

    args = parser.parse_args()

    if args.command == "start":
        asyncio.run(cmd_start(args))
    elif args.command == "connect":
        asyncio.run(cmd_connect(args))
    elif args.command == "chat":
        asyncio.run(cmd_chat(args))
    elif args.command == "test":
        asyncio.run(cmd_e2e_test(args))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
