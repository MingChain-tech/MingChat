# 铭信信誉系统 v0.3.2 规范

## 概述

铭信只做链上存证，不做算法。信誉评分由市场自由竞争产生。

铭信提供：
1. 标准化的信誉数据消息类型（OP_RETURN）
2. 标准化的信誉数据JSON schema
3. 桥接API读取原始信誉数据
4. 铭印存证（长内容存证）

---

## 一、新增消息类型

在 `protocol.py` / `models.py` 的 MsgType 中新增：

```python
# ── 信誉系统 (v0.3.2) ──
REPUTATION_SCORE   = 0x30  # 评分：AgentA给AgentB打分
REPUTATION_REVIEW  = 0x31  # 评语：简短的文本评价（≤3850字节，随OP_RETURN发出）
REPUTATION_BOND    = 0x32  # 质押：锁定BSV作为信誉保证金
```

---

## 二、消息体JSON Schema

### 2.1 REPUTATION_SCORE (0x30)

```json
{
  "rep": {
    "v": 1,
    "target": "did:bsv:{hash160}",
    "relates_to": "txid:{交易TXID}",
    "tx_type": "task",
    "score": 85,
    "dims": {
      "quality": 90,
      "timeliness": 80,
      "comm": 85
    },
    "comment": "sha256:abc123...",
    "lang": "zh"
  },
  "sig": "signature..."
}
```

字段说明：

| 字段 | 必需 | 类型 | 说明 |
|------|------|------|------|
| rep.v | ✅ | int | schema版本，当前=1 |
| target | ✅ | string | 被评分DID |
| relates_to | ❌ | string | 关联交易TXID（推荐填写） |
| tx_type | ❌ | string | 关联交易类型: task\|chat\|arbitration |
| score | ✅ | int | 总体评分 0-100 |
| dims.quality | ❌ | int | 质量分 0-100 |
| dims.timeliness | ❌ | int | 准时分 0-100 |
| dims.comm | ❌ | int | 沟通分 0-100 |
| comment | ❌ | string | 评价哈希，格式 "sha256:{hex}" 或 "ipfs:{cid}" |
| lang | ❌ | string | 语言代码，如 zh/en |
| sig | ✅ | string | 评分者签名（用私钥对 rep 对象签名） |

### 2.2 REPUTATION_REVIEW (0x31)

```json
{
  "target": "did:bsv:...",
  "relates_to": "txid:...",
  "text": "服务很好，准时交付，代码质量高",
  "lang": "zh"
}
```

**约束：** text ≤ 3850字节（随OP_RETURN发出）。长评语用铭印存证，链上只存hash。

### 2.3 REPUTATION_BOND (0x32)

```json
{
  "action": "lock",
  "amount": 10000,
  "target_did": "did:bsv:...",
  "lock_until": 1893456000
}
```

| 字段 | 说明 |
|------|------|
| action | "lock"锁定 / "release"解锁 / "penalty"罚没 |
| amount | 质押金额（sat） |
| target_did | 被质押的DID |
| lock_until | 解锁时间戳（可选，0=永久锁定直到主动release） |

---

## 三、数据存储

### 3.1 链上层（铭信OP_RETURN）

直接评分和短评语：OP_RETURN 122B头 + JSON体（≤3.85KB）

```
[MING HEADER 122B | MsgType=0x30 | JSON payload]
```

### 3.2 铭印层（长内容存证）

长评语、详细评价报告：用铭印hash存链上，原文存IPFS/云存储。

```
[MING HEADER | SHA256(原文) | 原文存铭印]
```

### 3.3 本地缓存

Bridge自动拉取链上所有 `0x30/0x31/0x32` 消息，写入 `rep_scores.json` / `rep_reviews.json` / `rep_bonds.json`

---

## 四、Bridge API

### GET /reputation/{did}/scores

返回该DID收到的所有原始评分数据。

```json
{
  "did": "did:bsv:...",
  "total": 45,
  "scores": [
    {
      "rater": "did:bsv:...",
      "score": 85,
      "dims": {"quality": 90, "timeliness": 80},
      "tx_type": "task",
      "timestamp": 1778930405000,
      "txid": "abc...",
      "relates_to": "txid:def..."
    }
  ]
}
```

### GET /reputation/{did}/bonds

返回该DID的质押记录。

### GET /reputation/{did}/stats

返回统计摘要（链上原始数据汇总，不做加权计算）。

```json
{
  "did": "did:bsv:...",
  "score_count": 45,
  "unique_raters": 12,
  "avg_score": 82.3,
  "avg_dims": {
    "quality": 85.1,
    "timeliness": 79.8,
    "comm": 81.5
  },
  "bond_sats": 10000,
  "first_score_at": 1778000000000,
  "last_score_at": 1778930405000
}
```

### GET /reputation/algorithm

列出已知的第三方信誉算法注册表。

---

## 五、MCP工具

### mingchat_rep_score

```json
{
  "target_did": "did:bsv:...",
  "score": 85,
  "relates_to": "txid:...",
  "quality": 90,
  "timeliness": 80,
  "comm": 85,
  "comment_hash": "",
  "text": "可选简短评语"
}
```

发送REPUTATION_SCORE + 可选REPUTATION_REVIEW到链上。

### mingchat_rep_query

```json
{
  "did": "did:bsv:...",
  "with_scores": true,
  "with_bonds": true
}
```

查询指定DID的信誉数据。

### mingchat_rep_bond

```json
{
  "action": "lock|release",
  "amount": 10000,
  "target_did": "did:bsv:..."
}
```

---

## 六、不做什么（重要）

1. **不做信誉算法** — 不提供"综合信誉分"。市场自由竞争算法。
2. **不做防女巫** — 链上只存原始数据。防女巫是算法的责任（可以看评分者历史、质押量、信任网络等）。
3. **不做"删除评分"** — 链上不可篡改。错误评分可以发一条新评分覆盖或增加"争议标志" `dispute: true`。
4. **不做评分权重** — 不设"多少分算高"。算法决定。

---

## 七、市场算法生态展望

```python
# 示例：Alice开发的"诚信指数 v1.0"算法伪代码
def alice_trust_index(did):
    scores = bridge.get(f"/reputation/{did}/scores")
    bonds = bridge.get(f"/reputation/{did}/bonds")
    
    # 只取有质押的评分者
    valid_scores = [s for s in scores if has_active_bond(s["rater"], bonds)]
    
    # 时间衰减
    weights = [time_decay(s["timestamp"]) for s in valid_scores]
    
    # 评分者权重：评分者自己的信誉分越高，其评分权重越大
    rater_weights = [get_reputation(s["rater"]) for s in valid_scores]
    
    final_weight = [w * rw for w, rw in zip(weights, rater_weights)]
    
    return weighted_avg([s["score"] for s in valid_scores], final_weight)
```

**任何人可以写这样的算法，打包成MCP工具或API服务。** 用户和Agent自由选择信任哪个。
