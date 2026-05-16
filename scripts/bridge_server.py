# 铭信 Bridge 守护进程
# systemd常驻 + REST API (端口8900)
# 功能: SPV监听 + 消息队列 + BSV钱包 + OpenClaw/webhook推送
#
# 启动: python3 bridge_server.py
# 环境变量: MINGCHAT_PRIV_HEX (必需)
#            BRIDGE_PORT (默认8900)
#            WEBHOOK_URL (可选，推送新消息)
#
# API:
#   GET  /health          - 健康检查
#   GET  /status          - 节点状态 (地址/余额/监听状态/消息数)
#   GET  /messages        - 读取收件箱消息
#   POST /send            - 发送消息 {to_address, content, msg_type?}
#   POST /webhook/set     - 设置webhook回调URL {url}
#   GET  /webhook         - 查看当前webhook配置

import sys, os, json, time, threading, logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional, Callable

# ── 导入铭信核心 ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mingchat import MingChat, Message, MsgType
from mingchat.protocol import hash160_to_address, address_to_hash160
from mingchat.bsv_tools import privkey_to_wif, fetch_utxos
from mingchat.spv import SpvListener
from mingchat.reputation import ReputationStore, ReputationScore, ReputationBond

# ── 配置 ──
PRIV_HEX = os.environ.get("MINGCHAT_PRIV_HEX")
PORT = int(os.environ.get("BRIDGE_PORT", "8900"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
LARK_APP_ID = os.environ.get("LARK_APP_ID", "")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET", "")
LARK_CHAT_ID = os.environ.get("LARK_CHAT_ID", "")
DATA_DIR = Path(os.environ.get("BRIDGE_DATA_DIR", str(Path.home() / ".mingchat" / "bridge")))
INBOX_FILE = DATA_DIR / "inbox.json"
REP_FILE = DATA_DIR / "reputation.json"
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "bridge.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bridge")

# ── 全局状态 ──
_inbox_lock = threading.Lock()
_webhook_url = WEBHOOK_URL or None
_listener = None
_client = None
_start_time = time.time()
_message_count = 0
_rep_store = ReputationStore()

# ── 信誉数据持久化 ──
def _load_rep_store() -> ReputationStore:
    if REP_FILE.exists():
        try:
            data = json.loads(REP_FILE.read_text())
            return ReputationStore.from_dict(data)
        except Exception:
            return ReputationStore()
    return ReputationStore()

def _save_rep_store():
    REP_FILE.write_text(json.dumps(_rep_store.to_dict(), ensure_ascii=False, indent=2))


# ── 收件箱操作 ──
def _load_inbox() -> list:
    if INBOX_FILE.exists():
        try:
            return json.loads(INBOX_FILE.read_text())
        except Exception:
            return []
    return []


def _save_inbox(inbox: list):
    INBOX_FILE.write_text(json.dumps(inbox, ensure_ascii=False, indent=2))


def _append_to_inbox(entry: dict):
    global _message_count
    with _inbox_lock:
        inbox = _load_inbox()
        inbox.append(entry)
        if len(inbox) > 200:
            inbox = inbox[-200:]
        _save_inbox(inbox)
    _message_count += 1


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


# ── 飞书批量推送 ──
_lark_token = None
_lark_token_expires = 0
_lark_batch = []
_lark_batch_lock = threading.Lock()
_lark_interval = 8  # 秒，聚合窗口

def _lark_refresh_token():
    """缓存复用tenant_access_token"""
    global _lark_token, _lark_token_expires
    now = time.time()
    if _lark_token and now < _lark_token_expires - 60:
        return _lark_token
    try:
        import urllib.request
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=json.dumps({
                "app_id": LARK_APP_ID,
                "app_secret": LARK_APP_SECRET,
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, timeout=5) as resp:
            token_data = json.loads(resp.read())
        _lark_token = token_data.get("tenant_access_token", "")
        _lark_token_expires = now + token_data.get("expire", 3600)
        return _lark_token
    except Exception as e:
        log.warning(f"飞书token刷新失败: {e}")
        return _lark_token or ""


def _lark_push_batch():
    """推送累积的一批消息（只推一次飞书API）"""
    global _lark_batch
    access_token = _lark_refresh_token()
    if not access_token:
        return

    with _lark_batch_lock:
        if not _lark_batch:
            return
        batch = _lark_batch[:]
        _lark_batch = []

    if len(batch) == 1:
        e = batch[0]
        pri = e.get("priority", "free")
        icon = _msg_priority_icon(pri)
        fee = e.get("msg_fee", 0)
        fee_str = f" ({fee} sat💰)" if fee > 0 else ""
        text = (
            f"{icon} 铭信新消息{fee_str}\n"
            f"来自: {e['from'][:12]}...\n"
            f"类型: {e['type']}\n"
            f"内容: {e['content'][:150]}\n"
            f"TXID: {e['txid'][:12]}..."
        )
    else:
        # 按优先级排序显示
        sorted_batch = sorted(batch, key=lambda x: {"high": 0, "medium": 1, "low": 2, "free": 3}.get(x.get("priority", "free"), 4))
        lines = [f"📮 铭信 {len(batch)} 条新消息"]
        for e in sorted_batch:
            pri = e.get("priority", "free")
            icon = _msg_priority_icon(pri)
            fee = e.get("msg_fee", 0)
            fee_str = f"({fee}sat)" if fee > 0 else ""
            summary = e['content'][:50].replace('\n', ' ')
            lines.append(f"· {icon} {e['from'][:10]}... {fee_str} {summary}")
        text = "\n".join(lines)

    import urllib.request
    try:
        msg_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=json.dumps({
                "receive_id": LARK_CHAT_ID,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            }).encode(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(msg_req, timeout=5) as resp:
            log.info(f"飞书推送成功 ({len(batch)}条合并)")
    except Exception as e:
        log.warning(f"飞书推送失败: {e}")
        # 推送失败时把消息放回去，避免丢消息
        with _lark_batch_lock:
            _lark_batch = batch + _lark_batch
            if len(_lark_batch) > 100:
                _lark_batch = _lark_batch[:100]


def _lark_push_worker():
    """后台线程：每隔_lark_interval秒检查并推送"""
    while True:
        time.sleep(_lark_interval)
        _lark_push_batch()


def _push_to_lark(entry: dict):
    """入队一条消息（不立即推送）"""
    if not (LARK_APP_ID and LARK_APP_SECRET and LARK_CHAT_ID):
        return
    with _lark_batch_lock:
        _lark_batch.append(entry)


def _push_to_lark_immediate(entry: dict):
    """高优先级消息直接推送（不经过队列）"""
    if not (LARK_APP_ID and LARK_APP_SECRET and LARK_CHAT_ID):
        return
    fee = entry.get("msg_fee", 0)
    text = (
        f"🔴 VIP 铭信消息 (附带 {fee} sat 💰)\n"
        f"来自: {entry['from'][:12]}...\n"
        f"类型: {entry['type']}\n"
        f"内容: {entry['content'][:150]}\n"
        f"TXID: {entry['txid'][:12]}..."
    )
    import urllib.request
    try:
        access_token = _lark_refresh_token()
        if not access_token:
            return
        msg_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=json.dumps({
                "receive_id": LARK_CHAT_ID,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            }).encode(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(msg_req, timeout=5) as resp:
            log.info(f"飞书高优推送成功 (fee={fee})")
    except Exception as e:
        log.warning(f"飞书高优推送失败: {e}")


def _msg_priority(fee: int) -> str:
    """消息费 → 优先级标签"""
    if fee <= 0:
        return "free"
    elif fee < 100:
        return "low"
    elif fee < 1000:
        return "medium"
    else:
        return "high"

def _msg_priority_icon(priority: str) -> str:
    return {"free": "🟢", "low": "🔵", "medium": "🟡", "high": "🔴"}.get(priority, "🟢")


# ── SPV监听回调 ──
def _on_new_message(msg: Message):
    sender = hash160_to_address(msg.sender_hash160)
    priority = _msg_priority(msg.msg_fee)
    entry = {
        "type": msg.msg_type.to_str(),
        "from": sender,
        "to": hash160_to_address(msg.receiver_hash160),
        "content": msg.get_payload_text(),
        "timestamp": msg.timestamp,
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(msg.timestamp / 1000
                                                  if msg.timestamp > 100000000000
                                                  else msg.timestamp)),
        "txid": msg.txid,
        "msg_fee": msg.msg_fee,
        "priority": priority,
    }
    _append_to_inbox(entry)
    log.info(f"收到消息: from={entry['from'][:16]}... type={entry['type']} fee={msg.msg_fee} sat pri={priority} txid={msg.txid[:16]}...")

    # 飞书推送（高优先级即时推，中低合并）
    if priority == "high":
        # 高优先级：跳过队列，直接推送
        _push_to_lark_immediate(entry)
    else:
        _push_to_lark(entry)

    # webhook推送
    if _webhook_url:
        try:
            import urllib.request
            req = urllib.request.Request(
                _webhook_url,
                data=json.dumps(entry).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            log.info(f"webhook推送成功: {_webhook_url[:40]}...")
        except Exception as e:
            log.warning(f"webhook推送失败: {e}")

    # ── 信誉数据自动收集 ──
    global _rep_store
    if msg.msg_type == MsgType.REPUTATION_SCORE:
        try:
            payload_data = json.loads(msg.payload)
            rep = payload_data.get("rep", payload_data)
            target = rep.get("target", "")
            if target:
                _rep_store.add_score(target, {
                    "rater": hash160_to_address(msg.sender_hash160),
                    "score": rep.get("score", 0),
                    "dims": rep.get("dims", {}),
                    "tx_type": rep.get("tx_type", ""),
                    "relates_to": rep.get("relates_to", ""),
                    "timestamp": msg.timestamp,
                    "txid": msg.txid,
                })
                _save_rep_store()
                log.info(f"信誉评分已收集: target={target[:40]}... score={rep.get('score')}")
        except Exception as e:
            log.warning(f"信誉评分解析失败: {e}")

    elif msg.msg_type == MsgType.REPUTATION_BOND:
        try:
            payload_data = json.loads(msg.payload)
            target = payload_data.get("target_did", "")
            if target:
                _rep_store.add_bond(target, {
                    "action": payload_data.get("action", "lock"),
                    "amount": payload_data.get("amount", 0),
                    "sender": hash160_to_address(msg.sender_hash160),
                    "timestamp": msg.timestamp,
                    "txid": msg.txid,
                })
                _save_rep_store()
                log.info(f"信誉质押已记录: target={target[:40]}... amount={payload_data.get('amount')}")
        except Exception as e:
            log.warning(f"信誉质押解析失败: {e}")


# ── 初始化监听器 ──
def init_listener():
    global _listener, _client
    if not PRIV_HEX:
        log.warning("未设置 MINGCHAT_PRIV_HEX，无法初始化和发送消息")
        return False

    # PRIV_HEX可能是hex格式(64位)或WIF格式
    priv_key_for_client = PRIV_HEX
    if len(PRIV_HEX) == 64:
        try:
            pk = bytes.fromhex(PRIV_HEX)
            priv_key_for_client = privkey_to_wif(pk)
        except Exception as e:
            log.error(f"私钥hex转换失败: {e}")
            return False

    _client = MingChat(private_key_wif=priv_key_for_client)
    log.info(f"钱包地址: {_client.address}")

    hash160 = address_to_hash160(_client.address)
    if isinstance(hash160, str):
        hash160 = bytes.fromhex(hash160)

    # 从inbox恢复已见过txid
    inbox = _load_inbox()
    _listener = SpvListener(target_hash160=hash160)
    for entry in inbox:
        txid = entry.get("txid", "")
        if txid:
            _listener._seen_txids.add(txid)
    log.info(f"从inbox恢复seen_txids: {len(_listener._seen_txids)}个")

    _listener.on_message(_on_new_message)
    return True


def start_listener():
    if not _listener:
        return False
    _listener.start()
    return True


def stop_listener():
    if _listener:
        _listener.stop()


# ── HTTP请求处理 ──
class BridgeHandler(BaseHTTPRequestHandler):
    def _json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._json({
                "status": "ok",
                "uptime_sec": int(time.time() - _start_time),
                "listening": _listener.is_running if _listener else False,
            })

        elif path == "/status":
            balance = 0
            address = ""
            if _client:
                try:
                    balance = _client.get_balance()
                except Exception:
                    balance = 0
                address = _client.address
            self._json({
                "status": "ok",
                "address": address or "(未初始化)",
                "balance_sat": balance,
                "balance_bsv": balance / 1e8 if balance else 0,
                "listening": _listener.is_running if _listener else False,
                "message_count": _message_count,
                "webhook": _webhook_url or None,
                "inbox_file": str(INBOX_FILE),
                "data_dir": str(DATA_DIR),
            })

        elif path == "/messages":
            limit = int(parse_qs(parsed.query).get("limit", [20])[0])
            unread = parse_qs(parsed.query).get("unread", ["false"])[0].lower() == "true"
            mark_read = parse_qs(parsed.query).get("mark_read", ["true"])[0].lower() == "true"
            priority_filter = parse_qs(parsed.query).get("priority", [""])[0]
            min_fee = int(parse_qs(parsed.query).get("min_fee", ["0"])[0])

            with _inbox_lock:
                inbox = _load_inbox()
                result = list(inbox)  # 复制
                # 筛选
                if priority_filter:
                    allowed = [p.strip() for p in priority_filter.split(",")]
                    result = [m for m in result if m.get("priority", "free") in allowed]
                if min_fee > 0:
                    result = [m for m in result if m.get("msg_fee", 0) >= min_fee]
                if unread:
                    result = [m for m in result if m.get("read") != True]
                result = result[-limit:] if limit > 0 else result
                result = result[::-1]  # 最新在前
                if mark_read:
                    for m in inbox:
                        if m.get("read") != True:
                            m["read"] = True
                    _save_inbox(inbox)

            self._json({
                "status": "ok",
                "count": len(result),
                "total": len(_load_inbox()),
                "messages": result,
            })

        elif path == "/webhook":
            self._json({
                "status": "ok",
                "webhook_url": _webhook_url or None,
            })

        elif path.startswith("/reputation/"):
            # 解析路径: /reputation/{did}/scores 或 /reputation/{did}/bonds 或 /reputation/{did}/stats
            parts = path.split("/")
            if len(parts) < 3:
                self._json({"error": "路径格式: /reputation/{did}/{endpoint}"}, 400)
                return
            did = parts[2]
            endpoint = parts[3] if len(parts) > 3 else "stats"

            # 从DID反查hash160 => 从链上收集信誉数据
            # 如果是/repuation/{did} 带limit参数，返回原始评分数据
            if endpoint == "scores":
                scores = _rep_store.get_scores(did)
                limit = int(parse_qs(parsed.query).get("limit", [50])[0])
                self._json({
                    "did": did,
                    "total": len(scores),
                    "scores": scores[-limit:][::-1],  # 最新在前
                })
            elif endpoint == "bonds":
                bonds = _rep_store.get_bonds(did)
                self._json({
                    "did": did,
                    "total": len(bonds),
                    "bonds": bonds[::-1],  # 最新在前
                })
            elif endpoint == "stats":
                # 只返回原始数据统计（不做加权计算）
                stats = _rep_store.get_stats(did)
                self._json({
                    "status": "ok",
                    **stats,
                })
            else:
                self._json({"error": f"未知端点: {endpoint}"}, 400)

        else:
            self._json({"error": f"unknown path: {path}"}, 404)

    def do_POST(self):
        global _webhook_url
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        if path == "/send":
            if not _client:
                self._json({"error": "Bridge未初始化，需要 MINGCHAT_PRIV_HEX"}, 500)
                return
            to_addr = body.get("to_address", "")
            content = body.get("content", "")
            msg_type_str = body.get("msg_type", "TEXT")
            msg_fee = int(body.get("msg_fee", "0"))
            if not to_addr or not content:
                self._json({"error": "缺少 to_address 或 content"}, 400)
                return
            try:
                msg = _client.send(to_addr, content,
                                   msg_type=MsgType.from_str(msg_type_str),
                                   msg_fee=msg_fee)
                self._json({
                    "status": "ok",
                    "txid": msg.txid,
                    "from": _client.address,
                    "to": to_addr,
                    "content": content,
                    "url": f"https://whatsonchain.com/tx/{msg.txid}",
                })
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/webhook/set":
            url = body.get("url", "")
            if url:
                _webhook_url = url
                cfg = _load_config()
                cfg["webhook_url"] = url
                _save_config(cfg)
                self._json({"status": "ok", "webhook_url": url})
            else:
                self._json({"error": "缺少 url"}, 400)

        elif path == "/webhook/clear":
            _webhook_url = None
            cfg = _load_config()
            cfg.pop("webhook_url", None)
            _save_config(cfg)
            self._json({"status": "ok", "webhook_url": None})

        elif path == "/notify-tx":
            """其他Agent通知我们有发给我们的新消息"""
            txid = body.get("txid", "")
            if not txid:
                self._json({"error": "缺少 txid"}, 400)
                return
            if not _listener:
                self._json({"error": "Bridge未就绪"}, 503)
                return
            try:
                msg = _listener.verify_tx_by_txid(txid)
                if msg:
                    self._json({"status": "ok", "txid": txid, "from": hash160_to_address(msg.sender_hash160)[:16]+"..."})
                else:
                    self._json({"status": "ignored", "reason": "txid已处理或不匹配"})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/stats/msg-fee":
            """消息费统计"""
            with _inbox_lock:
                inbox = _load_inbox()
            total_fee = sum(m.get("msg_fee", 0) for m in inbox)
            counts = {"free": 0, "low": 0, "medium": 0, "high": 0}
            for m in inbox:
                p = m.get("priority", "free")
                counts[p] = counts.get(p, 0) + 1
            self._json({
                "status": "ok",
                "total_fee_received": total_fee,
                "total_messages": len(inbox),
                "count_by_priority": counts,
            })

        else:
            self._json({"error": f"unknown path: {path}"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):
        log.info(f"HTTP {args[0]} {args[1]} {args[2]}")


def main():
    log.info("=" * 50)
    log.info("铭信 Bridge 守护进程 v0.3")
    log.info(f"数据目录: {DATA_DIR}")
    log.info(f"端口: {PORT}")

    # 加载webhook配置
    cfg = _load_config()
    global _webhook_url, _rep_store
    if cfg.get("webhook_url"):
        _webhook_url = cfg["webhook_url"]
        log.info(f"已加载webhook: {_webhook_url[:40]}...")

    # 加载信誉数据
    _rep_store = _load_rep_store()
    log.info(f"已加载信誉数据: {len(_rep_store._scores)}个DID的评分记录")

    # 启动飞书批量推送后台线程
    if LARK_APP_ID and LARK_APP_SECRET and LARK_CHAT_ID:
        t = threading.Thread(target=_lark_push_worker, daemon=True)
        t.start()
        log.info("飞书批量推送已启动 (聚合窗口{}s)".format(_lark_interval))

    # 初始化监听器
    ok = init_listener()
    if ok:
        log.info(f"钱包: {_client.address}")
        # 后台线程执行首次全量扫描（不阻塞HTTP）
        def _initial_scan():
            try:
                n = _listener.scan_once()
                log.info(f"首次扫描: 发现 {n} 条新消息")
            except Exception as e:
                log.warning(f"首次扫描异常: {e}")
        t = threading.Thread(target=_initial_scan, daemon=True)
        t.start()
        # 启动后台监听
        start_listener()
        log.info("SPV监听已启动")

    # 启动HTTP
    server = HTTPServer(("0.0.0.0", PORT), BridgeHandler)
    log.info(f"HTTP服务已启动: http://0.0.0.0:{PORT}")
    log.info(f"可用API:")
    log.info(f"  GET  /health")
    log.info(f"  GET  /status")
    log.info(f"  GET  /messages?limit=20&mark_read=true")
    log.info(f"  POST /send          {{to_address, content, msg_type?}}")
    log.info(f"  POST /webhook/set   {{url}}")
    log.info(f"  POST /webhook/clear")
    log.info("=" * 50)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("正在停止...")
        stop_listener()
        server.shutdown()
        log.info("已停止")


if __name__ == "__main__":
    main()
