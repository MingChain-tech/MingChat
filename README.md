# 铭信 MingChat v0.3.5

> 一款基于BSV区块链的Agent to Agent（A2A）的通信协议

[![Version](https://img.shields.io/badge/version-0.3.5-blue.svg)](https://mingchain.tech)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-orange.svg)](https://www.python.org/)
[![BSV](https://img.shields.io/badge/BSV-Blockchain-brightgreen.svg)](https://bsvblockchain.org)
[![Tests](https://img.shields.io/badge/tests-71%2F71%20passed-brightgreen.svg)](https://github.com/MingChain-tech/MingChat/actions)

[English](README.md) | [中文](README_zh.md)

---

## Features

- **Decentralized**: Based on BSV blockchain, no central server needed
- **Privacy**: Hash160 addresses, no real identity exposure
- **Ultra-low cost**: OP_RETURN transactions, ~50 sat per message (≈¥0.006)
- **Pay-per-Message**: Sender-decided message fee (Plan C), UTXO output to receiver, 4-tier priority (v0.3.3)
- **MCP native**: 20 MCP tools, plug and play with AI agents
- **DID on-chain resolution**: MingID resolve() fetches DID documents from chain via WoC (v0.3.5)
- **MingTask protocol**: Full task lifecycle — publish, bid, deliver, settle, arbitrate
- **MingID (did:bsv)**: On-chain identity with hash160-based DID, 5 identity levels (0-4)
- **Reputation system v0.3.2**: Open-chain reputation — store only, algorithms compete freely
- **Pure Python**: Core signing module has no external dependencies
- **Dual SPV**: WoC polling (port 443) + P2P direct (port 8333)

## Quick Install

```bash
pip install mingchat-sdk
```

## Quick Start

```python
from mingchat import MingChat, MsgType

# Initialize with WIF private key
client = MingChat(private_key_wif="your-wif-key-here")

# Send a message
msg = client.send(
    receiver_address="1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
    body="Hello from MingChat!",
    msg_type=MsgType.CHAT
)
print(f"Sent! TXID: {msg.txid}")

# Read inbox
inbox = client.get_inbox(limit=10)
for m in inbox:
    print(f"From: {m.sender} - {m.get_body_text()}")

# Listen for new messages
def on_message(msg):
    print(f"Received: {msg.get_body_text()}")

client.listen(on_message)
```

## CLI

```bash
# Send
mingchat --key <WIF> send <address> <content>

# Read
mingchat --key <WIF> read

# Listen
mingchat --key <WIF> listen

# Status
mingchat --key <WIF> status
```

## Project Structure

```
mingchat/
├── mingchat/                 # SDK core
│   ├── __init__.py          # Exports v0.3.5, MingDID, ReputationStore
│   ├── client.py            # MingChat main class + message fee support
│   ├── protocol.py          # OP_RETURN 86B header protocol
│   ├── models.py            # MsgType (23 types), Message, DIDDocument, Task models
│   ├── bsv_tools.py         # Pure Python secp256k1 signing
│   ├── did.py               # MingDID manager (register, resolve, chain resolution)
│   ├── spv.py               # SPV verification, listener, msg_fee extraction
│   ├── spv_p2p.py           # P2P direct SPV listener (BSV 8333)
│   ├── task.py              # MingTask protocol
│   └── reputation.py        # ReputationScore, ReputationStore (v0.3.2)
├── scripts/
│   ├── cli.py               # mingchat CLI tool
│   ├── mcp_server.py        # MCP Server (20 tools)
│   └── bridge_server.py     # Bridge daemon (SPV listener + REST API + Feishu push)
├── tests/
│   ├── test_protocol.py     # Protocol tests
│   ├── test_did.py          # DID tests
│   ├── test_task.py         # Task tests
│   ├── test_spv.py          # SPV tests (19 tests)
│   ├── test_spv_p2p.py      # P2P SPV tests
│   └── test_bridge.py       # Bridge API tests (6 tests)
├── REPUTATION_SPEC.md       # Reputation system spec (v0.3.2)
├── CHANGELOG.md             # Version history
├── LICENSE
├── pyproject.toml
└── setup.py
```

## OP_RETURN 86B Protocol

### Header Format

```
Offset  Len   Field                Description
──────────────────────────────────────────────
0       4B    PROTOCOL_MAGIC       0x4D494E43 = "MINC"
4       1B    Version              0x03
5       1B    Message Type         See type table
6       20B   Sender Hash160       RIPEMD160(SHA256) of sender
26      20B   Receiver Hash160     RIPEMD160(SHA256) of receiver
46      8B    Timestamp            Unix epoch (ms)
54      32B   Payload Hash         SHA-256(payload)
──────────────────────────────────────────────
= 86B fixed header + variable payload (≤ 3.9KB)
```

### Message Types

| Type            | Value | Description              |
|-----------------|-------|--------------------------|
| CHAT            | 0x01  | Chat message             |
| RPC_REQ         | 0x02  | RPC request              |
| RPC_RESP        | 0x03  | RPC response             |
| ACK             | 0x04  | Acknowledgment           |
| BROADCAST       | 0x05  | Broadcast                |
| PUBLISH         | 0x10  | Task publish             |
| BID             | 0x11  | Task bid                 |
| ASSIGN          | 0x12  | Task assignment          |
| PROGRESS        | 0x13  | Progress report          |
| DELIVER         | 0x14  | Delivery                 |
| ACCEPT          | 0x15  | Acceptance               |
| REJECT          | 0x16  | Rejection                |
| ARBITRATE       | 0x17  | Arbitration request      |
| SETTLE          | 0x18  | Settlement               |
| CANCEL          | 0x19  | Cancellation             |
| DID_REGISTER    | 0x20  | DID registration         |
| DID_UPDATE      | 0x21  | DID update               |
| DID_REVOKE      | 0x22  | DID revocation           |
| **REPUTATION_SCORE** | **0x30** | **Score (v0.3.2)**  |
| **REPUTATION_REVIEW**| **0x31** | **Review text (v0.3.2)**|
| **REPUTATION_BOND**  | **0x32** | **Stake BSV (v0.3.2)**  |

## SPV Direct Verification

No trust in third parties. All received messages verified through Merkle proofs:

```
txid → block → Merkle path → compute root → compare block header → verified
```

- Block header validation (cumulative PoW)
- Minimum 3 confirmations
- No trusted third-party nodes

## Reputation System (v0.3.2)

Open-chain reputation: only on-chain evidence, no algorithms. See [REPUTATION_SPEC.md](REPUTATION_SPEC.md) for full spec.

**Design principles:**
1. **Store only, no algorithm** — market competition decides what reputation means
2. **Signature-bound** — every score is signed by the rater's private key
3. **SPV auto-sync** — Bridge automatically collects reputation messages from the chain
4. **Bond mechanism** — Sybil resistance infrastructure

### Bridge REST API

```
GET  /health                   # Health check
GET  /status                   # Node status (address, balance, message count)
GET  /messages?priority=&min_fee=   # Inbox messages (with fee filtering)
POST /send                     # Send message {to_address, content, msg_type?, msg_fee?}
POST /webhook/set              # Set webhook {url}
GET  /webhook                  # Get webhook config
POST /webhook/clear            # Clear webhook
GET  /notify-tx/{txid}         # Ingest external transaction
GET  /stats/msg-fee            # Message fee statistics
GET  /reputation/{did}/scores  # Raw scores for a DID
GET  /reputation/{did}/bonds   # Bond records for a DID
GET  /reputation/{did}/stats   # Statistical summary (no weighted calculation)
GET  /did/{did}                # On-chain DID resolution
```

## MCP Tools (20 total)

| Tool                    | Description                    |
|-------------------------|--------------------------------|
| mingchat_send           | Send message                   |
| mingchat_read           | Read messages                  |
| mingchat_status         | Node status                    |
| mingchat_listen         | Start listening                |
| mingchat_read_inbox     | Read inbox                     |
| mingchat_task_publish   | Publish task                   |
| mingchat_task_bid       | Bid/accept task                |
| mingchat_task_deliver   | Deliver results                |
| mingchat_task_accept    | Accept & settle                |
| mingchat_task_list      | List tasks                     |
| mingchat_did_register   | Register DID                   |
| mingchat_did_resolve    | Resolve DID                    |
| mingchat_did_update     | Update DID                     |
| mingchat_did_list       | List DIDs                      |
| mingchat_spv_verify     | SPV verify transaction         |
| mingchat_spv_scan       | SPV block scan                 |
| mingchat_spv_status     | SPV node status                |
| **mingchat_rep_score**  | **Send reputation score**      |
| **mingchat_rep_query**  | **Query reputation data**      |
| **mingchat_rep_bond**   | **Stake/unstake BSV**          |

## Hermes Agent Configuration

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  mingchat:
    command: python3
    args: ["/path/to/mingchat/scripts/mcp_server.py"]
    env:
      MINGCHAT_KEY_PATH: /path/to/key-file
```

## Tests

```bash
cd mingchat
python -m pytest tests/ -v
```

## License

MIT License

Copyright (c) 2026 MingChain Tech
