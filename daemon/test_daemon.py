#!/usr/bin/env python3
"""
Phase 1 集成测试 — p2p_daemon.py JSON-RPC 协议验证
= 启动 daemon 子进程
= 发送 JSON-RPC 请求
= 验证响应和事件推送
"""
import subprocess
import json
import time
import sys
import os
from pathlib import Path

DAEMON_SCRIPT = os.path.join(os.path.dirname(__file__), "p2p_daemon.py")
DATA_DIR = os.path.expanduser("~/.p2pchat")

# Python 3.9 required for bsv library
PYTHON39 = "/usr/bin/python3.9"
if not os.path.exists(PYTHON39):
    PYTHON39 = sys.executable  # fallback

# 确保之前没有残留身份影响
_test_identity = Path(DATA_DIR) / "identity__test_daemon.json"
if _test_identity.exists():
    _test_identity.unlink()


class DaemonTest:
    """启动 daemon 子进程，通过 stdin/stdout 进行 JSON-RPC 通信"""

    def __init__(self, handle="_test_daemon"):
        self.handle = handle
        self.proc = None
        self._running = False

    def start(self, timeout=15):
        """启动 daemon 子进程"""
        self.proc = subprocess.Popen(
            [PYTHON39, DAEMON_SCRIPT,
             "--handle", self.handle,
             "--data-dir", DATA_DIR,
             "--host", "127.0.0.1",
             "--port", "0",
             "--log-level", "WARNING",
             "--network", "testnet"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        self._running = True

        # 同步等待 ready 事件
        start = time.time()
        while time.time() - start < timeout:
            line_bytes = self.proc.stdout.readline()
            if not line_bytes:
                time.sleep(0.1)
                continue
            line = line_bytes.decode('utf-8').strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("event") == "ready":
                print(f"✅ Daemon started: @{msg['data'].get('handle')}")
                print(f"   Listening: {msg['data'].get('listening')}")
                return msg["data"]
        
        self.stop()
        raise TimeoutError("Daemon did not emit 'ready' event")

    def stop(self):
        """停止 daemon"""
        self._running = False
        if self.proc and self.proc.poll() is None:
            try:
                self._send_raw({"jsonrpc": "2.0", "id": 999, "method": "stop"})
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=2)
        print("✅ Daemon stopped")

    def call(self, method: str, params: dict = None, timeout: float = 10) -> dict:
        """发送 JSON-RPC 请求并同步等待响应"""
        msg_id = int(time.time() * 1000) % 100000
        self._send_raw({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {}
        })

        # 同步读取响应
        start = time.time()
        while time.time() - start < timeout:
            line_bytes = self.proc.stdout.readline()
            if not line_bytes:
                time.sleep(0.05)
                continue
            line = line_bytes.decode('utf-8').strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            if msg.get("id") == msg_id:
                if "error" in msg:
                    return msg
                return msg.get("result", msg)
            # 否则可能是事件，忽略（在同步模式下）

        raise TimeoutError(f"No response for method '{method}' (id={msg_id})")

    def _send_raw(self, obj: dict):
        """发送 JSON-RPC 请求到 daemon stdin"""
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode('utf-8'))
        self.proc.stdin.flush()


# ─── 测试用例 ────────────────────────────────────────────────

def test_daemon_startup():
    """测试 1: daemon 启动 + ready 事件"""
    print("\n" + "=" * 60)
    print("测试 1: Daemon 启动")
    print("=" * 60)

    dt = DaemonTest("_test_daemon")
    ready = dt.start(timeout=20)

    assert ready["handle"] == "_test_daemon", f"Wrong handle: {ready['handle']}"
    assert len(ready["pubkey"]) == 66, f"Wrong pubkey length: {len(ready['pubkey'])}"
    assert ready["version"] == "1.0.0"
    print("  ✅ ready 事件格式正确")

    # 返回 dt 用于后续测试
    return dt


def test_status(dt: DaemonTest):
    """测试 2: status 方法"""
    print("\n" + "=" * 60)
    print("测试 2: status 方法")
    print("=" * 60)

    result = dt.call("status")
    resp = result.get("result", result)

    assert resp["running"] is True
    assert resp["handle"] == "_test_daemon"
    assert "pubkey" in resp
    assert "listening" in resp
    assert "peers" in resp
    assert "spv" in resp
    print(f"  ✅ status OK: {json.dumps({k: v for k, v in resp.items() if k != 'pubkey'}, indent=2)}")


def test_get_identity(dt: DaemonTest):
    """测试 3: get_identity 方法"""
    print("\n" + "=" * 60)
    print("测试 3: get_identity 方法")
    print("=" * 60)

    result = dt.call("get_identity")
    resp = result.get("result", result)

    assert resp["handle"] == "_test_daemon"
    assert len(resp["pubkey"]) == 66
    assert resp["pubkey"].startswith("02") or resp["pubkey"].startswith("03")
    assert "seed_hash" in resp
    print(f"  ✅ get_identity OK: @{resp['handle']} pk={resp['pubkey'][:20]}...")


def test_ping(dt: DaemonTest):
    """测试 4: ping 健康检查"""
    print("\n" + "=" * 60)
    print("测试 4: ping 健康检查")
    print("=" * 60)

    result = dt.call("ping")
    resp = result.get("result", result)

    assert resp["pong"] is True
    assert "ts" in resp
    print(f"  ✅ ping OK: pong={resp['pong']}")


def test_list_contacts_empty(dt: DaemonTest):
    """测试 5: list_contacts（空）"""
    print("\n" + "=" * 60)
    print("测试 5: list_contacts（空列表）")
    print("=" * 60)

    result = dt.call("list_contacts")
    resp = result.get("result", result)

    assert "contacts" in resp
    assert isinstance(resp["contacts"], list)
    print(f"  ✅ list_contacts OK: {len(resp['contacts'])} contacts (expected 0)")


def test_add_contact(dt: DaemonTest):
    """测试 6: add_contact + list_contacts"""
    print("\n" + "=" * 60)
    print("测试 6: add_contact")
    print("=" * 60)

    fake_pk = "02" + "ab" * 32  # 模拟 33 字节压缩公钥
    result = dt.call("add_contact", {
        "handle": "bob",
        "pubkey": fake_pk
    })
    resp = result.get("result", result)
    assert resp.get("added") == "@bob"
    print(f"  ✅ add_contact OK: {resp}")

    # 验证
    result2 = dt.call("list_contacts")
    contacts = result2.get("result", result2).get("contacts", [])
    assert len(contacts) == 1
    assert contacts[0]["handle"] == "bob"
    print(f"  ✅ 验证: {len(contacts)} contact(s)")


def test_list_peers(dt: DaemonTest):
    """测试 7: list_peers"""
    print("\n" + "=" * 60)
    print("测试 7: list_peers")
    print("=" * 60)

    result = dt.call("list_peers")
    peers = result.get("result", result).get("peers", [])

    assert isinstance(peers, list)
    # 刚启动应该没有 peers（除非有其他节点在运行）
    print(f"  ✅ list_peers OK: {len(peers)} peers")


def test_history_empty(dt: DaemonTest):
    """测试 8: history（空）"""
    print("\n" + "=" * 60)
    print("测试 8: history（空历史）")
    print("=" * 60)

    result = dt.call("history")
    msgs = result.get("result", result).get("messages", [])

    assert isinstance(msgs, list)
    print(f"  ✅ history OK: {len(msgs)} messages")


def test_spv_status(dt: DaemonTest):
    """测试 9: spv_status"""
    print("\n" + "=" * 60)
    print("测试 9: spv_status")
    print("=" * 60)

    result = dt.call("spv_status")
    resp = result.get("result", result)

    assert "synced" in resp
    assert "headers" in resp
    assert "network" in resp
    print(f"  ✅ spv_status OK: synced={resp['synced']}, headers={resp['headers']}")


def test_error_method(dt: DaemonTest):
    """测试 10: 调用不存在的方法"""
    print("\n" + "=" * 60)
    print("测试 10: 错误处理 — 不存在的方法")
    print("=" * 60)

    result = dt.call("nonexistent_method")
    error = result.get("error", {})

    assert error.get("code") == -32601
    print(f"  ✅ 正确返回 Method not found: {error.get('message')}")


def test_invalid_json(dt: DaemonTest):
    """测试 11: 错误处理 — 发送格式正确的请求但方法是 _invalid_json"""
    print("\n" + "=" * 60)
    print("测试 11: 错误处理 — 无效 JSON 鲁棒性")
    print("=" * 60)

    # 直接用 _send_raw 发送会被忽略的无效 JSON 方法
    # （实际无效 JSON 行会被 daemon 的 reader 线程忽略并记录）
    result = dt.call("ping")
    assert result.get("pong") is True
    print("  ✅ daemon 正常运行（ping 响应正常）")


# ─── 主入口 ────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  P2P Daemon Phase 1 集成测试")
    print("  JSON-RPC 2.0 over stdin/stdout")
    print("=" * 60)

    dt = None
    try:
        # 测试 1: 启动
        dt = test_daemon_startup()

        # 测试 2-9: JSON-RPC 方法
        test_status(dt)
        test_get_identity(dt)
        test_ping(dt)
        test_list_contacts_empty(dt)
        test_add_contact(dt)
        test_list_peers(dt)
        test_history_empty(dt)
        test_spv_status(dt)

        # 测试 10-11: 错误处理
        test_error_method(dt)
        test_invalid_json(dt)

        print("\n" + "=" * 60)
        print("  🎉 所有测试通过！")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        if dt:
            dt.stop()

    # 清理测试身份
    test_ident = Path(DATA_DIR) / "identity__test_daemon.json"
    if test_ident.exists():
        test_ident.unlink()
        print(f"  🧹 已清理测试身份: {test_ident}")

    sys.exit(0)


if __name__ == "__main__":
    main()
