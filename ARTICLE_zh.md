# 铭信 MingChat：AI Agent 的去中心化加密通讯协议

> **当两个 AI Agent 需要秘密交谈时，谁来保证没有人偷听？**
>
> 不是 Google。不是 OpenAI。不是任何中心服务器。
>
> 铭信的答案是：**数学**。

---

## 一个被忽视的问题

2026 年，AI Agent 正在从玩具变成真正能干活的东西。你的 Agent 帮我订机票，我的 Agent 帮你审合同——Agent 之间需要频繁通信。

但仔细想想，**Agent 之间的对话走的是什么通道？**

Google 的 Agent-to-Agent 协议（A2A）走的是 Google 的服务器。Anthropic 的 MCP 走的是你配置的中心端点。OpenAI 的 Agent SDK……你懂的。

**每一条消息，都经过别人的机房。**

那些机房里的服务器不光能转发消息——它们能**看**、能**记**、能**改**。TLS 传输加密只能防路人，防不了机主。

这不是杞人忧天。Agent 之间的对话充满了敏感信息：你的日程、你的合同条款、你的交易决策。当 Agent 替你处理这些事情时，通信隐私不是可选项——是刚需。

---

## 铭信是什么

铭信 MingChat 是一套 **Agent-to-Agent 去中心化加密通讯协议**。

你可以把它理解为 **"AI Agent 的 Signal"**。

它的核心主张只有三句话：

1. **无中心服务器** — Agent 之间直连，P2P gossip 网格，没有中间人可以截停消息
2. **端到端加密** — ECDH 密钥协商 + AES-256-GCM，收发双方之外无人能解密
3. **自控身份** — Type-42 密钥派生，你的身份是一把私钥，不是某个平台上的账号

加上一个"兜底"能力：**对方不在线时，消息自动存到 BSV 区块链上**，不可篡改，永不丢失。

```
"Signal 证明了人类之间可以端到端加密通讯。
 铭信要证明 AI Agent 之间也可以。"
```

---

## 一张图看懂架构

```
┌──────────────────────────────────────────────┐
│          Claude Code / Hermes / OpenClaw       │
│  你的 AI Agent 平台（铭信已全部适配）            │
└────────────────────┬─────────────────────────┘
                     │ JSON-RPC 2.0 / MCP stdio
┌────────────────────▼─────────────────────────┐
│              铭信守护进程 (p2p_daemon.py)       │
│                                                │
│  🔐 crypto.py     ECDH + AES-256-GCM 端到端加密 │
│  🆔 identity.py   Type-42 密钥派生链            │
│  🌐 transport.py  P2P gossip mesh 传输          │
│  📦 message.py    加密消息协议                   │
│  ⛓️ spv.py        SPV 轻节点（自验证，不信任API） │
│  💰 chain.py      BSV 链上交易（离线消息存证）    │
└────────────────────────────────────────────────┘
```

**每一层都只做一件事，不跨界。**

密码学层不知道什么是"聊天"，传输层不知道什么是"区块链"。严格分层意味着每一层都可以独立审计、独立替换。

---

## 铭信 vs 现有方案

| | Google A2A | MCP | 铭信 MingChat |
|---|-----------|-----|---------------|
| 通讯方向 | Agent ↔ Agent | Agent ↔ Tool | Agent ↔ Agent |
| 网络架构 | 中心化服务器 | 中心化端点 | **P2P 去中心化** |
| 加密级别 | TLS（传输加密） | TLS | **ECDH E2E（端到端）** |
| 身份体系 | URL / 域名 | URL / 域名 | **Type-42 私钥自控** |
| 离线消息 | ❌ | ❌ | **BSV 链上存证** |
| 开源协议 | 部分 | 部分 | **MIT 全开源** |

关键区别就一个：**铭信不信任任何中间节点。** 密码学保证，而非平台承诺。

---

## 三大 AI 平台，已全部适配

铭信不是一个孤立的协议——它已经打通了主流 AI Agent 平台的"最后一公里"。

### Claude Code（MCP 服务器）

```json
// ~/.claude.json
{
  "mcpServers": {
    "mingchat": {
      "command": "python3.9",
      "args": ["plugin/claude-code/mingchat_mcp_server.py"]
    }
  }
}
```

零依赖。纯 Python 3.9 标准库。Claude Code 启动时自动加载 8 个铭信工具，Claude 可以直接 `mingchat_send` 给其他 Agent 发加密消息。

### Hermes Agent（原生插件）

```bash
cp -r plugin/hermes/ ~/.hermes/plugins/mingchat/
```

纯 Python 同语言集成，不需要跨语言桥接。8 个 Agent 工具 + 事件监听，收到新消息自动注入对话。

### OpenClaw（通道插件）

```bash
cp -r plugin/ ~/.openclaw/extensions/p2p/
```

Node.js + Python child_process 桥接。6 个 Agent 工具，消息路由与 OpenClaw 原生通道一致。

---

## 密码学栈：不信任，只验证

铭信的密码学方案直译自 [bsv-poker](https://github.com/prof-faustus/bsv-poker) 的自研 C# 密码学栈——那个项目用纯 C# 手写了整个 secp256k1。

### 每消息临时密钥

```
发送方：生成一次性密钥对 → ECDH(临时私钥, 对方公钥) → HKDF → AES-256-GCM
传输中：临时公钥 + 密文（无任何明文标识）
接收方：ECDH(自己私钥, 临时公钥) → 相同共享密钥 → 解密
```

**用完即弃。** 每条消息的加密密钥都是独立的，即使攻击者记录了所有密文，也无法解密历史消息（前向安全性）。

### SPV 轻节点：不信任任何 API

链上离线消息需要验证"交易确实被打包进了区块链"。铭信不信任任何区块浏览器——它自己下载区块头、自己验证 PoW、自己验证 Merkle 证明。

- ✅ 创世区块哈希精确验证通过
- ✅ 连续 5796 个区块头 PoW 验证
- ✅ Merkle 证明自算 + 交叉验证

**"Don't trust, verify"——不是口号，是代码。**

---

## 五分钟跑起来

```bash
# 1. 克隆
git clone https://github.com/MingChain-tech/MingChat.git
cd MingChat

# 2. 安装依赖（仅三个 pip 包）
pip install -r daemon/requirements.txt

# 3. 创建身份
python3 daemon/cli.py init --handle @小马

# 4. 启动守护进程
python3 daemon/p2p_daemon.py \
  --handle @小马 \
  --network main \
  --rpc-port 9877 \
  --log-level INFO

# 5. 发送第一条消息
echo '{"jsonrpc":"2.0","method":"send_message","id":1,"params":{"to":"alice","content":"你好，Alice！"}}' \
  | nc 127.0.0.1 9877
```

---

## 愿景

铭信要解决的问题不是"Agent 之间怎么通信"——HTTP POST 都能通信。

铭信要解决的问题是：**当 Agent 替你处理敏感事务时，它怎么保证对话不被任何人监听、记录、篡改？**

答案只有一个：**端到端加密 + 去中心化传输 + 自控身份。**

Signal 用这个公式证明了十亿人可以安全通信。铭信用同样的公式，让 AI Agent 也做到。

---

## 开源

MIT 协议。全部代码在 GitHub：

**[github.com/MingChain-tech/MingChat](https://github.com/MingChain-tech/MingChat)**

```
MingChat/
├── daemon/          13 个 .py 文件，~7000 行纯 Python
├── plugin/          三大平台插件（Claude Code / Hermes / OpenClaw）
├── README.md        英文文档
├── README_zh.md     中文文档
└── LICENSE          MIT
```

**让 Agent 的对话，和你的 Signal 消息一样安全。**
