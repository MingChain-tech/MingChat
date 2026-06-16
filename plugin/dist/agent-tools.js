/**
 * 铭信 MingChat Agent Tools — Agent 可调用的铭信工具（供 registerP2PTools 使用）
 */
import { getBridge as getP2PBridge, isBridgeReady } from "./channel.js";

/**
 * 注册铭信 Agent 工具
 */
export function registerP2PTools(api) {
  if (!api?.registerTool) return;

  api.registerTool({
    name: "p2p_send_message",
    description: "通过铭信去中心化网络发送端到端加密消息给另一个 Agent。ECDH+AES-256-GCM 加密。",
    parameters: {
      to: { type: "string", description: "接收方 Agent 的 @handle" },
      content: { type: "string", description: "消息内容" },
    },
    handler: async (params) => {
      const bridge = getP2PBridge();
      if (!bridge || !isBridgeReady()) {
        return { error: "铭信 bridge 未就绪，请稍候。" };
      }
      try {
        const result = await bridge.send(params.to, params.content);
        return {
          ok: true,
          msg_id: result.msg_id,
          delivery: result.delivery || "p2p",
          message: `✅ 已发送给 @${params.to}（${result.delivery || "p2p"}）`,
        };
      } catch (err) {
        return { error: `发送失败: ${err.message}` };
      }
    },
  });

  api.registerTool({
    name: "p2p_broadcast",
    description: "向铭信网络中所有在线 Agent 广播消息。",
    parameters: {
      content: { type: "string", description: "广播内容" },
    },
    handler: async (params) => {
      const bridge = getP2PBridge();
      if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
      try {
        await bridge.broadcast(params.content);
        return { ok: true, message: "📢 已广播" };
      } catch (err) {
        return { error: `广播失败: ${err.message}` };
      }
    },
  });

  api.registerTool({
    name: "p2p_discover_agents",
    description: "发现铭信网络中其他在线的 Agent。",
    parameters: {},
    handler: async () => {
      const bridge = getP2PBridge();
      if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
      try {
        const { peers } = await bridge.listPeers();
        if (!peers || peers.length === 0) {
          return { agents: [], message: "当前无在线 Agent" };
        }
        return {
          agents: peers.map((p) => ({ handle: p.handle, peer_id: p.peer_id, addr: p.addr })),
          count: peers.length,
        };
      } catch (err) {
        return { error: err.message };
      }
    },
  });

  api.registerTool({
    name: "p2p_get_identity",
    description: "获取本 Agent 的铭信身份（handle + 公钥）。",
    parameters: {},
    handler: async () => {
      const bridge = getP2PBridge();
      if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
      try {
        return await bridge.getIdentity();
      } catch (err) {
        return { error: err.message };
      }
    },
  });

  api.registerTool({
    name: "p2p_spv_status",
    description: "查看铭信 SPV 轻节点的区块头同步状态。",
    parameters: {},
    handler: async () => {
      const bridge = getP2PBridge();
      if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
      try {
        return await bridge.spvStatus();
      } catch (err) {
        return { error: err.message };
      }
    },
  });

  api.registerTool({
    name: "p2p_fetch_offline",
    description: "扫描 BSV 链上铭信离线消息，使用 SPV 验证后解密。",
    parameters: {},
    handler: async () => {
      const bridge = getP2PBridge();
      if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
      try {
        const result = await bridge.fetchOffline();
        return {
          ...result,
          message: result.new_messages > 0
            ? `📩 发现 ${result.new_messages} 条链上离线消息`
            : "无新增离线消息",
        };
      } catch (err) {
        return { error: err.message };
      }
    },
  });
}

// 工具函数
export function resolveP2PToolAccount(cfg) {
  return cfg?.channels?.p2p?.handle || null;
}

export function createP2PToolClient(cfg) {
  return {
    send: async (to, content) => {
      const bridge = getP2PBridge();
      return bridge ? bridge.send(to, content) : null;
    },
  };
}
