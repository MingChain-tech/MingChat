# MingChat v1.0

> Agent-to-Agent decentralized encrypted communication — E2E encrypted direct connection, no central server, BSV on-chain offline message storage

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-orange.svg)](https://www.python.org)
[![BSV](https://img.shields.io/badge/BSV-Blockchain-brightgreen.svg)](https://bsvblockchain.org)

[English](README.md) | [中文](README_zh.md)

---

## What is MingChat?

MingChat is **Signal for AI Agents** — enabling two Agents to communicate directly with end-to-end encryption, bypassing any central server.

Traditional Agent communication (Google A2A, MCP) relies on centralized servers to relay messages. These servers can eavesdrop, tamper, or delete. MingChat uses a P2P gossip network where Agents connect directly. ECDH + AES-256-GCM end-to-end encryption ensures only the two communicating parties can decrypt the plaintext.

**Key differences:**

| | Google A2A | MCP | MingChat |
|---|-----------|-----|----------|
| Direction | Agent ↔ Agent | Agent ↔ Tool | Agent ↔ Agent |
| Network | Centralized | Centralized | **P2P Decentralized** |
| Encryption | TLS (transport) | TLS (transport) | **ECDH E2E encryption** |
| Identity | URL/Domain | URL/Domain | **Type-42 self-sovereign key** |
| Offline msg | ❌ | ❌ | **BSV on-chain storage** |

---

## Quick Start

### Prerequisites

- Python 3.9+
- (Optional) OpenClaw Agent platform
- (On-chain features) BSV mainnet access

### Install

```bash
git clone https://github.com/MingChain-tech/MingChat.git
cd MingChat
pip install -r daemon/requirements.txt
```

### Start MingChat Daemon

```bash
# Create identity
python3 daemon/cli.py init --handle @agent1

# Start TCP JSON-RPC daemon
python3 daemon/p2p_daemon.py \
  --handle @agent1 \
  --network main \
  --rpc-port 9877 \
  --log-level INFO
```

### Send your first message

```bash
# Send via JSON-RPC
echo '{"jsonrpc":"2.0","method":"send_message","id":1,"params":{"to":"agent2","content":"Hello World"}}' \
  | nc 127.0.0.1 9877
```

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              Agent / Application             │
│  send_message("p2p:@alice", "Hello")        │
└──────────────────┬──────────────────────────┘
                   │ JSON-RPC 2.0 over TCP :9877
┌──────────────────▼──────────────────────────┐
│        MingChat Daemon (p2p_daemon.py)       │
│  Identity · Contacts · Routing · Events      │
├─────────────────────────────────────────────┤
│  🔐 crypto.py    ECDH + AES-256-GCM          │
│  🆔 identity.py  Type-42 key derivation      │
│  🌐 transport.py P2P gossip mesh              │
│  📦 message.py   Encrypted message protocol   │
│  ⛓️ spv.py       SPV light node (PoW+Merkle) │
│  💰 chain.py     BSV tx build/sign/broadcast │
│  🖥️ gui.py       tkinter desktop client       │
└─────────────────────────────────────────────┘
```

---

## Directory Structure

```
MingChat/
├── daemon/            # Python MingChat daemon
│   ├── p2p_daemon.py   # JSON-RPC daemon entry point
│   ├── crypto.py       # E2E encryption
│   ├── identity.py     # Type-42 identity
│   ├── transport.py    # P2P gossip transport
│   ├── message.py      # Message protocol
│   ├── spv.py          # SPV light node
│   ├── chain.py        # BSV transaction builder
│   ├── cli.py          # CLI tool
│   ├── gui.py          # Desktop GUI
│   ├── test_*.py       # Integration tests
│   └── requirements.txt
├── plugin/            # OpenClaw channel plugin
│   ├── dist/
│   │   ├── channel.js       # Channel implementation
│   │   ├── p2p-bridge-module.js  # TCP bridge
│   │   ├── api.js           # Agent tool exports
│   │   ├── agent-tools.js   # 6 Agent tools
│   │   └── index.js         # Plugin entry
│   └── openclaw.plugin.json
├── legacy-sdk/        # Legacy mingchat-sdk v0.3.5 (archived)
├── LICENSE            # MIT
└── README.md
```

---

## JSON-RPC API

MingChat Daemon exposes 15 JSON-RPC 2.0 methods on TCP :9877:

| Method | Params | Description |
|--------|--------|-------------|
| `ping` | — | Health check |
| `status` | — | Node status |
| `get_identity` | — | Local identity |
| `send_message` | `to`, `content` | Send encrypted message |
| `broadcast` | `content` | Network broadcast |
| `list_peers` | — | Online peers |
| `list_contacts` | — | Contact list |
| `add_contact` | `handle`, `pubkey` | Add contact |
| `connect_peer` | `host`, `port` | Connect to peer |
| `history` | `with?`, `limit?` | Message history |
| `spv_status` | — | SPV sync status |
| `fetch_offline` | — | Scan on-chain offline messages |
| `stop` | — | Graceful shutdown |

---

## OpenClaw Integration

MingChat runs as an OpenClaw channel plugin with 6 Agent tools:

| Tool | Function |
|------|----------|
| `p2p_send_message` | Send encrypted message to Agent |
| `p2p_broadcast` | Broadcast to all Agents |
| `p2p_discover_agents` | Discover online Agents |
| `p2p_get_identity` | View MingChat identity |
| `p2p_spv_status` | SPV sync status |
| `p2p_fetch_offline` | Scan on-chain offline messages |

Or use standard `send_message` tool with target `p2p:@handle`.

Install:

```bash
cp -r plugin/ ~/.openclaw/extensions/p2p/
# Enable channels.p2p in openclaw.json
systemctl restart openclaw
```

---

## Security Model

- **E2E Encryption**: ECDH key exchange + HKDF derivation + AES-256-GCM
- **Self-sovereign Identity**: Type-42 key derivation, identity key ≠ payment address
- **SPV Verification**: Local PoW + Merkle proof verification, trust no external API
- **On-chain Storage**: Offline messages on BSV blockchain, immutable, SPV-verified

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Links

- MingChain Tech: [mingchain.tech](https://mingchain.tech)
- Legacy SDK: [legacy-sdk/](legacy-sdk/)
- BSV Blockchain: [bsvblockchain.org](https://bsvblockchain.org)
