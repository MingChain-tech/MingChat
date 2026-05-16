"""
铭信 MCP Server v0.3
支持14个工具: send/read/status/listen/read_inbox +
task_publish/task_bid/task_deliver/task_accept/task_list +
did_register/did_resolve/did_update
"""
import sys, os, json, time, threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mingchat import MingChat, Message, MsgType
from mingchat.models import (
    TaskFields, AuditFields, AuditFlags, TaskOp, TaskStatus,
    TaskPublishPayload, TaskBidPayload, TaskDeliverPayload,
    TaskSettlePayload, TaskDisputePayload,
)
from mingchat.task import MingTask, make_publish_payload, make_bid_payload, make_deliver_payload
from mingchat.did import MingDID, make_did_document
from mingchat.protocol import hash160_to_address, address_to_hash160
from mingchat.spv import build_merkle_proof, verify_merkle_proof, extract_op_return, verify_block_hash, bits_to_target, SpvListener, woc_get, woc_get_text, woc_get_block_txids
from mingchat.spv_p2p import SpvP2PListener

PRIV_HEX = os.environ.get("MINGCHAT_PRIV_HEX") or os.environ.get("MINGCHAT_PRIVATE_KEY")
if not PRIV_HEX:
    print("⚠️  请设置 MINGCHAT_PRIV_HEX 环境变量", file=sys.stderr)

client = MingChat(private_key_wif=PRIV_HEX) if PRIV_HEX else MingChat()
task_mgr = MingTask()
did_mgr = MingDID()
spv_listener = None  # 延迟初始化

INBOX_DIR = Path.home() / ".mingchat"
INBOX_FILE = INBOX_DIR / "inbox.json"
INBOX_DIR.mkdir(parents=True, exist_ok=True)
_inbox_lock = threading.Lock()

def _append_to_inbox(entry: dict):
    with _inbox_lock:
        inbox = []
        if INBOX_FILE.exists():
            try:
                inbox = json.loads(INBOX_FILE.read_text())
            except Exception:
                inbox = []
        inbox.append(entry)
        if len(inbox) > 50:
            inbox = inbox[-50:]
        INBOX_FILE.write_text(json.dumps(inbox, ensure_ascii=False, indent=2))


# ── 工具定义 ──────────────────────────────────────────────

TOOL_DEFS = [  # 20个工具
    # 通用
    {
        "name": "mingchat_send",
        "description": "发送铭信链上消息到BSV地址",
        "inputSchema": {
            "type": "object",
            "required": ["to_address", "content"],
            "properties": {
                "to_address": {"type": "string", "description": "接收方BSV地址"},
                "content": {"type": "string", "description": "消息内容"},
                "msg_type": {"type": "string", "description": "消息类型: TEXT/RPC_REQUEST/NOTIFICATION", "default": "TEXT"},
            },
        },
    },
    {
        "name": "mingchat_read",
        "description": "读取BSV地址上的铭信消息",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "要查询的BSV地址（默认自己）"},
                "limit": {"type": "integer", "description": "最大返回条数", "default": 5},
            },
        },
    },
    {
        "name": "mingchat_status",
        "description": "查询铭信节点状态（地址、余额、UTXO数）",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mingchat_listen",
        "description": "启动铭信链上消息监听（后台轮询，新消息存本地inbox）",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mingchat_read_inbox",
        "description": "读取监听到的新消息（自上次读取后的增量）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mark_read": {"type": "boolean", "description": "读取后标记为已读", "default": True},
            },
        },
    },
    # 任务协议
    {
        "name": "mingchat_task_publish",
        "description": "v0.3 发布任务到BSV链上",
        "inputSchema": {
            "type": "object",
            "required": ["to_address", "title", "reward_sats"],
            "properties": {
                "to_address": {"type": "string", "description": "接收方/任务市场地址"},
                "title": {"type": "string", "description": "任务标题"},
                "reward_sats": {"type": "integer", "description": "报酬(satoshis)"},
                "task_type": {"type": "string", "description": "任务类型: analysis|search|coding|translation|creative|custom", "default": "analysis"},
                "deadline": {"type": "integer", "description": "截止时间戳(可选)"},
                "capabilities": {"type": "string", "description": "所需能力标签(逗号分隔)", "default": ""},
                "assign_mode": {"type": "string", "description": "分配方式: bid|assign|match", "default": "bid"},
            },
        },
    },
    {
        "name": "mingchat_task_bid",
        "description": "v0.3 竞标/接单",
        "inputSchema": {
            "type": "object",
            "required": ["to_address", "task_id", "bid_sats"],
            "properties": {
                "to_address": {"type": "string", "description": "任务发布者地址"},
                "task_id": {"type": "string", "description": "任务ID"},
                "bid_sats": {"type": "integer", "description": "报价(satoshis)"},
                "estimated_time": {"type": "integer", "description": "预估时间(秒)", "default": 3600},
            },
        },
    },
    {
        "name": "mingchat_task_deliver",
        "description": "v0.3 交付任务结果",
        "inputSchema": {
            "type": "object",
            "required": ["to_address", "task_id", "result_hash", "summary"],
            "properties": {
                "to_address": {"type": "string", "description": "任务发布者地址"},
                "task_id": {"type": "string", "description": "任务ID"},
                "result_hash": {"type": "string", "description": "交付物SHA256哈希"},
                "summary": {"type": "string", "description": "交付摘要"},
            },
        },
    },
    {
        "name": "mingchat_task_accept",
        "description": "v0.3 验收/结算任务",
        "inputSchema": {
            "type": "object",
            "required": ["to_address", "task_id", "verdict"],
            "properties": {
                "to_address": {"type": "string", "description": "接单Agent地址"},
                "task_id": {"type": "string", "description": "任务ID"},
                "verdict": {"type": "string", "description": "accepted|rejected|partial", "default": "accepted"},
                "amount_sats": {"type": "integer", "description": "实际结算金额", "default": 0},
            },
        },
    },
    {
        "name": "mingchat_task_list",
        "description": "v0.3 查询本地任务列表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "筛选状态: PUBLISHED|BIDDING|EXECUTING|DELIVERED|SETTLED|CANCELLED", "default": ""},
                "task_type": {"type": "string", "description": "筛选任务类型", "default": ""},
            },
        },
    },
    # DID
    {
        "name": "mingchat_did_register",
        "description": "v0.3.5 注册铭识DID（含身份等级）",
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Agent名称"},
                "description": {"type": "string", "description": "Agent描述", "default": ""},
                "service_endpoint": {"type": "string", "description": "通讯端点URL", "default": ""},
                "identity_level": {"type": "integer", "description": "身份等级: 0=匿名 1=邮箱 2=企业 3=个人KYC 4=政府", "default": 0},
                "kyc_hash": {"type": "string", "description": "sha256(KYC机构签名+实名信息)", "default": ""},
                "kyc_provider": {"type": "string", "description": "KYC机构DID或URL", "default": ""},
                "license_ref": {"type": "string", "description": "牌照/许可证引用", "default": ""},
            },
        },
    },
    {
        "name": "mingchat_did_resolve",
        "description": "v0.3.5 解析铭识DID（本地+链上自动解析）",
        "inputSchema": {
            "type": "object",
            "required": ["did"],
            "properties": {
                "did": {"type": "string", "description": "DID标识符 (did:bsv:...)"},
            },
        },
    },
    {
        "name": "mingchat_did_update",
        "description": "v0.3 更新铭识DID",
        "inputSchema": {
            "type": "object",
            "required": ["did"],
            "properties": {
                "did": {"type": "string", "description": "DID标识符"},
                "name": {"type": "string", "description": "新名称", "default": ""},
                "description": {"type": "string", "description": "新描述", "default": ""},
                "service_endpoint": {"type": "string", "description": "新端点", "default": ""},
            },
        },
    },
    {
        "name": "mingchat_did_list",
        "description": "v0.3 列出本地已注册DID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "筛选状态: active|revoked", "default": "active"},
            },
        },
    },
    # ── 信誉系统 (v0.3.2) ──
    {
        "name": "mingchat_rep_score",
        "description": "v0.3.2 发送信誉评分到链上",
        "inputSchema": {
            "type": "object",
            "required": ["target_did", "score"],
            "properties": {
                "target_did": {"type": "string", "description": "被评分DID (did:bsv:...)"},
                "score": {"type": "integer", "description": "总体评分 0-100"},
                "relates_to": {"type": "string", "description": "关联交易TXID", "default": ""},
                "tx_type": {"type": "string", "description": "交易类型: task|chat|arbitration", "default": ""},
                "quality": {"type": "integer", "description": "质量分 0-100", "default": 0},
                "timeliness": {"type": "integer", "description": "准时分 0-100", "default": 0},
                "comm": {"type": "integer", "description": "沟通分 0-100", "default": 0},
                "text": {"type": "string", "description": "简短评语（OP_RETURN内）", "default": ""},
            },
        },
    },
    {
        "name": "mingchat_rep_query",
        "description": "v0.3.2 查询DID的信誉数据",
        "inputSchema": {
            "type": "object",
            "required": ["did"],
            "properties": {
                "did": {"type": "string", "description": "DID标识符"},
            },
        },
    },
    {
        "name": "mingchat_rep_bond",
        "description": "v0.3.2 信誉质押操作",
        "inputSchema": {
            "type": "object",
            "required": ["action", "amount", "target_did"],
            "properties": {
                "action": {"type": "string", "description": "lock|release"},
                "amount": {"type": "integer", "description": "质押金额（sat）"},
                "target_did": {"type": "string", "description": "被质押DID"},
            },
        },
    },
    # SPV验证
    {
        "name": "mingchat_spv_verify",
        "description": "v0.3 SPV验证：验证交易是否在链上并属于特定区块",
        "inputSchema": {
            "type": "object",
            "required": ["txid"],
            "properties": {
                "txid": {"type": "string", "description": "交易ID"},
            },
        },
    },
    {
        "name": "mingchat_spv_scan",
        "description": "v0.3 SPV扫描：执行一次区块扫描（Merkle验证），不需要第三方信任",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mingchat_spv_status",
        "description": "v0.3 SPV监听器状态",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle_tool(name: str, args: dict) -> dict:
    global spv_listener
    text = lambda s: {"content": [{"type": "text", "text": s}]}
    
    if name == "mingchat_send":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        msg = client.send(
            receiver_address=args["to_address"],
            body=args["content"],
            msg_type=MsgType.from_str(args.get("msg_type", "TEXT")),
        )
        return text(json.dumps({
            "status": "ok", "txid": msg.txid, "from": client.address,
            "to": args["to_address"], "content": args["content"],
            "url": f"https://whatsonchain.com/tx/{msg.txid}",
        }, ensure_ascii=False))
    
    elif name == "mingchat_read":
        msgs = client.get_messages(address=args.get("address", ""), limit=args.get("limit", 5))
        return text(json.dumps({
            "status": "ok", "address": args.get("address", client.address or ""),
            "count": len(msgs),
            "messages": [{
                "type": m.msg_type.to_str(),
                "from": hash160_to_address(m.sender_hash160),
                "to": hash160_to_address(m.receiver_hash160),
                "content": m.get_payload_text(),
                "timestamp": m.timestamp,
                "txid": m.txid,
            } for m in msgs],
        }, ensure_ascii=False))
    
    elif name == "mingchat_status":
        balance = client.get_balance() if PRIV_HEX else 0
        return text(json.dumps({
            "status": "ok",
            "address": client.address or "(无私钥)",
            "balance_sat": balance,
            "balance_bsv": balance / 1e8,
            "listening": client._listening,
        }, ensure_ascii=False))
    
    elif name == "mingchat_listen":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        if not client._listening:
            def on_new_msg(msg):
                sender = hash160_to_address(msg.sender_hash160)
                _append_to_inbox({
                    "type": msg.msg_type.to_str(), "from": sender,
                    "content": msg.get_payload_text(),
                    "timestamp": msg.timestamp,
                    "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msg.timestamp)),
                    "txid": msg.txid,
                })
            client.on_message(on_new_msg)
            client.listen()
        return text(json.dumps({
            "status": "ok", "listening": True,
            "address": client.address, "inbox_file": str(INBOX_FILE),
        }, ensure_ascii=False))
    
    elif name == "mingchat_read_inbox":
        mark_read = args.get("mark_read", True)
        with _inbox_lock:
            if INBOX_FILE.exists():
                try:
                    messages = json.loads(INBOX_FILE.read_text())
                except Exception:
                    messages = []
            else:
                messages = []
            if mark_read:
                INBOX_FILE.write_text("[]")
        return text(json.dumps({"status": "ok", "count": len(messages), "messages": messages}, ensure_ascii=False))
    
    # ── 任务工具 ──
    
    elif name == "mingchat_task_publish":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        caps = [c.strip() for c in args.get("capabilities", "").split(",") if c.strip()]
        payload = make_publish_payload(
            task_type=args.get("task_type", "analysis"),
            title=args["title"],
            reward_sats=args["reward_sats"],
            deadline=args.get("deadline", 0),
            capabilities=caps,
            assign_mode=args.get("assign_mode", "bid"),
        )
        msg = task_mgr.build_publish_message(client, payload, args["to_address"])
        return text(json.dumps({
            "status": "ok", "txid": msg.txid,
            "title": args["title"], "reward": args["reward_sats"],
        }, ensure_ascii=False))
    
    elif name == "mingchat_task_bid":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        payload = make_bid_payload(
            task_id=args["task_id"],
            bid_sats=args["bid_sats"],
            estimated_time=args.get("estimated_time", 3600),
        )
        msg = task_mgr.build_bid_message(client, payload, args["to_address"])
        return text(json.dumps({"status": "ok", "txid": msg.txid, "task_id": args["task_id"]}))
    
    elif name == "mingchat_task_deliver":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        payload = make_deliver_payload(
            task_id=args["task_id"],
            result_hash=args["result_hash"],
            summary=args["summary"],
        )
        msg = task_mgr.build_deliver_message(client, payload, args["to_address"])
        return text(json.dumps({"status": "ok", "txid": msg.txid, "task_id": args["task_id"]}))
    
    elif name == "mingchat_task_accept":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        payload = TaskSettlePayload(
            task_id=args["task_id"],
            verdict=args.get("verdict", "accepted"),
            amount_sats=args.get("amount_sats", 0),
        )
        msg = task_mgr.build_settle_message(client, payload, args["to_address"])
        return text(json.dumps({"status": "ok", "txid": msg.txid, "task_id": args["task_id"], "verdict": payload.verdict}))
    
    elif name == "mingchat_task_list":
        status_str = args.get("status", "")
        task_type = args.get("task_type", "")
        status_enum = None
        if status_str:
            try:
                status_enum = TaskStatus[status_str.upper()]
            except KeyError:
                pass
        tasks = task_mgr.list_tasks(status=status_enum, task_type=task_type or None)
        return text(json.dumps({
            "status": "ok", "count": len(tasks),
            "tasks": [{"task_id": t["task_id"], "status": TaskStatus(t["status"]).name,
                       "title": t["publish"].get("title", ""),
                       "type": t["publish"].get("task_type", "")} for t in tasks],
        }, ensure_ascii=False))
    
    # ── DID工具 ──
    
    elif name == "mingchat_did_register":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        from .bsv_tools import privkey_to_pubkey, wif_to_privkey
        pk = privkey_to_pubkey(wif_to_privkey(PRIV_HEX))
        doc = make_did_document(
            controller_pk=pk.hex(),
            name=args["name"],
            description=args.get("description", ""),
            service_endpoint=args.get("service_endpoint", ""),
            identity_level=args.get("identity_level", 0),
            kyc_hash=args.get("kyc_hash", ""),
            kyc_provider=args.get("kyc_provider", ""),
            license_ref=args.get("license_ref", ""),
        )
        return text(json.dumps({
            "status": "ok",
            "did": doc.did,
            "name": doc.profile_name,
            "identity_level": doc.identity_level,
            "kyc_provider": doc.kyc_provider or None,
        }, ensure_ascii=False))
    
    elif name == "mingchat_did_resolve":
        result = did_mgr.resolve(args["did"])
        if not result:
            return text(json.dumps({"status": "not_found", "did": args["did"]}))
        doc = result["doc"]
        return text(json.dumps({
            "status": result["status"],
            "did": doc.did,
            "name": doc.profile_name,
            "description": doc.profile_description,
            "service_endpoint": doc.service_endpoint,
            "controller_pk": doc.controller_pk[:16] + "..." if doc.controller_pk else "",
            "identity_level": doc.identity_level,
            "kyc_provider": doc.kyc_provider or None,
            "registration_txid": doc.registration_txid or None,
            "updated_at": result.get("updated_at", 0),
        }, ensure_ascii=False))
    
    elif name == "mingchat_did_update":
        changes = {}
        if args.get("name"):
            changes["profile_name"] = args["name"]
        if args.get("description"):
            changes["profile_description"] = args["description"]
        if args.get("service_endpoint"):
            changes["service_endpoint"] = args["service_endpoint"]
        result = did_mgr.update(args["did"], changes)
        if not result:
            return text(json.dumps({"status": "error", "error": f"DID {args['did']} 未找到"}))
        return text(json.dumps({"status": "ok", "did": args["did"], "changes": list(changes.keys())}))
    
    elif name == "mingchat_did_list":
        dids = did_mgr.list_dids(status=args.get("status", "active"))
        return text(json.dumps({"status": "ok", "count": len(dids), "dids": dids}, ensure_ascii=False))

    # ── SPV工具 ──

    elif name == "mingchat_spv_verify":
        if not spv_listener:
            # 延迟初始化
            from mingchat.bsv_tools import wif_to_privkey, privkey_to_address
            from mingchat.protocol import address_to_hash160
            if PRIV_HEX:
                privkey = wif_to_privkey(PRIV_HEX)
                addr = privkey_to_address(privkey)
                h160 = address_to_hash160(addr)
                spv_listener = SpvListener(target_hash160=h160)
        if spv_listener:
            result = spv_listener.verify_message(args["txid"])
        else:
            # 无私钥时只做基础验证
            txid = args["txid"]
            try:
                tx_info = woc_get(f"/tx/{txid}")
                block_hash = tx_info.get("blockhash", "")
                if not block_hash:
                    result = {"verified": False, "error": "交易尚未上链"}
                else:
                    txids = woc_get_block_txids(block_hash)
                    if not txids or txid not in txids:
                        result = {"verified": False, "error": "交易不在区块中"}
                    else:
                        idx = txids.index(txid)
                        block = woc_get(f"/block/hash/{block_hash}")
                        merkle_root = block.get("merkleroot", "")
                        proof, computed_root = build_merkle_proof(txids, idx)
                        verified = verify_merkle_proof(txid, proof, computed_root)
                        result = {
                            "verified": verified,
                            "txid": txid,
                            "block_hash": block_hash,
                            "block_height": block.get("height", 0),
                            "merkle_root": merkle_root,
                            "proof_entries": len(proof),
                        }
            except Exception as e:
                result = {"verified": False, "error": str(e)}
        return text(json.dumps(result, ensure_ascii=False))

    elif name == "mingchat_spv_scan":
        if not spv_listener:
            if not PRIV_HEX:
                return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
            from mingchat.bsv_tools import wif_to_privkey, privkey_to_address
            from mingchat.protocol import address_to_hash160
            privkey = wif_to_privkey(PRIV_HEX)
            addr = privkey_to_address(privkey)
            h160 = address_to_hash160(addr)
            spv_listener = SpvListener(target_hash160=h160)
        if not spv_listener.is_running:
            spv_listener.start()
        return text(json.dumps({
            "status": "ok",
            "running": spv_listener.is_running,
            "stats": spv_listener.get_stats(),
        }, ensure_ascii=False))

    elif name == "mingchat_spv_status":
        if not spv_listener:
            return text(json.dumps({"status": "ok", "spv_running": False}))
        return text(json.dumps({
            "status": "ok",
            "spv_running": spv_listener.is_running,
            "stats": spv_listener.get_stats(),
        }, ensure_ascii=False))

    # ── 信誉系统 (v0.3.2) ──
    elif name == "mingchat_rep_score":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        if not _client:
            from mingchat import MingChat
            private_key = PRIV_HEX
            if len(private_key) == 64:
                from mingchat.bsv_tools import privkey_to_wif
                private_key = privkey_to_wif(bytes.fromhex(private_key))
            _client = MingChat(private_key_wif=private_key)
        
        # 构建评分消息体
        dims = {}
        if args.get("quality"): dims["quality"] = args["quality"]
        if args.get("timeliness"): dims["timeliness"] = args["timeliness"]
        if args.get("comm"): dims["comm"] = args["comm"]
        
        payload = {
            "rep": {
                "v": 1,
                "target": args["target_did"],
                "relates_to": args.get("relates_to", ""),
                "tx_type": args.get("tx_type", ""),
                "score": args["score"],
                "dims": dims,
                "lang": "zh",
            }
        }
        
        # 签名
        from mingchat.bsv_tools import wif_to_privkey as _wif2pk
        privkey_bytes = _wif2pk(PRIV_HEX if len(PRIV_HEX) != 64 else private_key)
        from mingchat.reputation import sign_reputation as _sign_rep
        sig = _sign_rep(payload["rep"], privkey_bytes)
        payload["sig"] = sig
        
        body = json.dumps(payload, ensure_ascii=False)
        
        # 发送REPUTATION_SCORE消息
        msg = _client.send(_client.address, body, MsgType.REPUTATION_SCORE)
        
        result = {
            "status": "ok",
            "txid": msg.txid,
            "target": args["target_did"],
            "score": args["score"],
            "url": f"https://whatsonchain.com/tx/{msg.txid}",
        }
        
        # 如果同时有text评语，再发一条REPUTATION_REVIEW
        if args.get("text"):
            review_payload = {
                "target": args["target_did"],
                "relates_to": f"txid:{msg.txid}",
                "text": args["text"][:3850],
                "lang": "zh",
            }
            _client.send(_client.address, json.dumps(review_payload, ensure_ascii=False),
                         MsgType.REPUTATION_REVIEW)
            result["review_txid"] = msg.txid
        
        return text(json.dumps(result, ensure_ascii=False))

    elif name == "mingchat_rep_query":
        did = args["did"]
        # 解析DID中的hash160
        if not did.startswith("did:bsv:"):
            return text(json.dumps({"status": "error", "error": f"无效DID格式: {did}"}))
        
        # 从Bridge查询
        try:
            import urllib.request
            bridge_host = os.environ.get("BRIDGE_HOST", "http://127.0.0.1:8900")
            url = f"{bridge_host}/reputation/{did}/stats"
            with urllib.request.urlopen(url, timeout=10) as resp:
                stats = json.loads(resp.read())
            
            # 同时拉scores
            url2 = f"{bridge_host}/reputation/{did}/scores?limit=20"
            with urllib.request.urlopen(url2, timeout=10) as resp2:
                scores_data = json.loads(resp2.read())
            
            return text(json.dumps({
                "status": "ok",
                "did": did,
                "stats": stats,
                "recent_scores": scores_data.get("scores", [])[:10],
            }, ensure_ascii=False))
        except Exception as e:
            return text(json.dumps({"status": "error", "error": f"查询失败: {e}"}))

    elif name == "mingchat_rep_bond":
        if not PRIV_HEX:
            return {"isError": True, "content": [{"type": "text", "text": "需要设置 MINGCHAT_PRIV_HEX"}]}
        if not _client:
            from mingchat import MingChat
            private_key = PRIV_HEX
            if len(private_key) == 64:
                from mingchat.bsv_tools import privkey_to_wif
                private_key = privkey_to_wif(bytes.fromhex(private_key))
            _client = MingChat(private_key_wif=private_key)
        
        bond_payload = {
            "action": args["action"],
            "amount": args["amount"],
            "target_did": args["target_did"],
        }
        msg = _client.send(_client.address, json.dumps(bond_payload, ensure_ascii=False),
                           MsgType.REPUTATION_BOND)
        
        return text(json.dumps({
            "status": "ok",
            "txid": msg.txid,
            "action": args["action"],
            "amount": args["amount"],
            "target_did": args["target_did"],
        }, ensure_ascii=False))

    return {"isError": True, "content": [{"type": "text", "text": f"未知工具: {name}"}]}


# ── MCP Stdio 协议 ─────────────────────────────────────

def read_request():
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line.strip())

def write_response(resp: dict):
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()

def main():
    req = read_request()
    if req and req.get("method") == "initialize":
        write_response({
            "jsonrpc": "2.0", "id": req["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mingchat-mcp", "version": "0.3.5"},
            },
        })
        req = read_request()
    
    while True:
        req = read_request()
        if not req:
            break
        method = req.get("method")
        rid = req.get("id")
        
        if method == "tools/list":
            write_response({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOL_DEFS}})
        elif method == "tools/call":
            name = req.get("params", {}).get("name", "")
            args = req.get("params", {}).get("arguments", {})
            try:
                result = handle_tool(name, args)
                write_response({"jsonrpc": "2.0", "id": rid, "result": result})
            except Exception as e:
                write_response({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}})
        elif method == "notifications/initialized":
            pass
        else:
            write_response({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"未知方法: {method}"}})

if __name__ == "__main__":
    main()
