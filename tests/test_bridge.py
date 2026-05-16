"""
铭信 Bridge 单元测试
测试HTTP API的各端点（启动测试server + 发HTTP请求）
"""
import sys, os, json, time, threading, socket
from pathlib import Path
from http.server import HTTPServer
from urllib.request import urlopen, Request, HTTPError

TEST_DIR = Path("/tmp/mingchat_bridge_test")
TEST_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["BRIDGE_DATA_DIR"] = str(TEST_DIR)


def _find_free_port():
    """找个空闲端口"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _TestServer:
    def __init__(self, handler_class, port=None):
        self.port = port or _find_free_port()
        self.handler_class = handler_class
        self._server = None
        self._thread = None

    def __enter__(self):
        self._server = HTTPServer(("127.0.0.1", self.port), self.handler_class)
        self._thread = threading.Thread(target=lambda: self._server.handle_request(), daemon=True)
        self._thread.start()
        time.sleep(0.05)
        return self

    def __exit__(self, *args):
        if self._server:
            self._server.server_close()
        time.sleep(0.1)

    def get(self, path: str, timeout: int = 3) -> dict:
        resp = urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout)
        return json.loads(resp.read())

    def post(self, path: str, body: dict, timeout: int = 3) -> dict:
        data = json.dumps(body).encode()
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=timeout)
            return json.loads(resp.read())
        except HTTPError as e:
            return json.loads(e.read())

    def request(self, path: str, timeout: int = 3):
        """返回原始响应对象（用于检测404等）"""
        try:
            return urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout)
        except HTTPError as e:
            return e


def test_health():
    from scripts.bridge_server import BridgeHandler
    with _TestServer(BridgeHandler) as srv:
        data = srv.get("/health")
    assert data["status"] == "ok"
    print(f"✅ /health: {data['status']}, uptime={data.get('uptime_sec')}s")


def test_status():
    from scripts.bridge_server import BridgeHandler
    with _TestServer(BridgeHandler) as srv:
        data = srv.get("/status")
    assert data["status"] == "ok"
    assert "address" in data
    assert "balance_sat" in data
    print(f"✅ /status: address={data['address']}, balance={data['balance_sat']}sat")


def test_send_no_privkey():
    """没有私钥时 /send 应返回500"""
    from scripts.bridge_server import BridgeHandler
    with _TestServer(BridgeHandler) as srv:
        data = srv.post("/send", {"to_address": "1PPY1...", "content": "test"})
    assert "error" in data or "未初始化" in str(data)
    print(f"✅ /send 无私钥: 正确拒绝")


def test_webhook_set_and_clear():
    from scripts.bridge_server import BridgeHandler
    with _TestServer(BridgeHandler) as srv:
        # 设置
        data = srv.post("/webhook/set", {"url": "http://example.com/hook"})
        assert data["status"] == "ok"
        assert data["webhook_url"] == "http://example.com/hook"
        print(f"✅ /webhook/set: {data['webhook_url']}")

        # 读回
        data = srv.get("/webhook")
        assert data["webhook_url"] == "http://example.com/hook"
        print(f"✅ /webhook 读取: {data['webhook_url']}")

        # 清除
        data = srv.post("/webhook/clear", {})
        assert data["webhook_url"] is None
        print(f"✅ /webhook/clear: webhook已清除")


def test_unknown_path_returns_404():
    from scripts.bridge_server import BridgeHandler
    with _TestServer(BridgeHandler) as srv:
        resp = srv.request("/unknown")
    assert resp.code == 404, f"应返回404, 实际: {resp.code}"
    print(f"✅ /unknown → 404")


def test_messages_empty():
    from scripts.bridge_server import BridgeHandler
    with _TestServer(BridgeHandler) as srv:
        data = srv.get("/messages?limit=10")
    assert data["status"] == "ok"
    assert data["count"] == 0
    print(f"✅ /messages (空): count={data['count']}")


def cleanup():
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


if __name__ == "__main__":
    print("=" * 50)
    print("铭信 Bridge 单元测试")
    print("=" * 50)

    tests = [
        test_health,
        test_status,
        test_send_no_privkey,
        test_webhook_set_and_clear,
        test_messages_empty,
        test_unknown_path_returns_404,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 50)

    cleanup()
