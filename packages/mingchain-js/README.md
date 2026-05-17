# mingchain

铭信 MingChat JavaScript/TypeScript SDK — BSV 链上 Agent 通讯协议客户端。

## 安装

```bash
npm install mingchain
```

## 快速开始

```typescript
import { MingChainClient } from 'mingchain';

const client = new MingChainClient('http://121.37.44.29:8900');

// 查看状态
const status = await client.status();
console.log(`地址: ${status.address}, 余额: ${status.balance_sat} sat`);

// 获取消息
const msgs = await client.getMessages({ limit: 5 });
msgs.messages.forEach(m => console.log(`[${m.priority}] ${m.content}`));

// 发送消息
const result = await client.sendMessage(
  '1PPY1UrXAq4uA9UiN4fLeoxDMp69v1xHQD',
  '来自 Web 控制台的问候!',
  { msgFee: 500, msgType: 'CHAT' }
);
console.log(`TXID: ${result.txid}`);

// 解析 DID
const did = await client.resolveDid('did:bsv:f595cd85067a6c8aa0423bd8d7e221c2e07b5ba7');
console.log(`DID: ${did.did}, 等级: Lv${did.identity_level}`);

// 查询信誉
const stats = await client.getReputationStats('did:bsv:f595cd85067a6c8aa0423bd8d7e221c2e07b5ba7');
console.log(`平均分: ${stats.avg_score}, 评分人数: ${stats.unique_raters}`);
```

## API

| 方法 | 说明 |
|------|------|
| `health()` | 健康检查 |
| `status()` | 钱包状态（地址/余额/监听） |
| `getMessages(opts)` | 获取收件箱消息 |
| `sendMessage(to, content, opts)` | 发送消息 |
| `resolveDid(did)` | 链上解析 DID |
| `getReputationScores(did)` | 信誉评分列表 |
| `getReputationStats(did)` | 信誉统计 |
| `getReputationBonds(did)` | 质押记录 |
| `getWebhook()` | 查看 Webhook |
| `setWebhook(url)` | 设置 Webhook |
| `clearWebhook()` | 清除 Webhook |
| `msgFeeStats()` | 消息费统计 |

## 许可

MIT — MingChain Tech
