# Changelog

## v1.0.0 (2026-06-16)

### 🚀 铭信 MingChat v1.0 — 首个正式版本

**架构重构：从 BSV 链上 SDK 到 P2P 加密通讯协议**

- 🔐 ECDH + AES-256-GCM 端到端加密
- 🪪 Type-42 自控身份密钥派生
- 🌐 P2P gossip mesh 去中心化组网
- ⛓️ SPV 轻节点（PoW + 默克尔证明验证）
- 💰 BSV 交易构建与链上离线消息存证
- 🔌 JSON-RPC 2.0 over TCP 守护进程
- 🖥️ tkinter 桌面 GUI 客户端
- 🦞 OpenClaw 通道插件（6 个 Agent Tools）
- 📦 systemd 服务部署

### 📁 目录重组

- `daemon/` — Python 铭信守护进程
- `plugin/` — OpenClaw 通道插件
- `legacy-sdk/` — 旧版 mingchat-sdk v0.3.5（归档）

---

## v0.3.5 (legacy — 已归档)

归档至 `legacy-sdk/`。旧版是基于 BSV OP_RETURN 的 Agent 链上通讯 SDK，包含 MingTask 任务协议、铭识 MingID (did:bsv)、信誉系统等。
