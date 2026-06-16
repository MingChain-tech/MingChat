/**
 * 铭信 MingChat Plugin API — 导出 channel 插件 + Agent 工具处理函数
 */
export { p2pPlugin } from "./channel.js";
export { p2pSetupWizard } from "./setup-wizard.js";

// Agent Tools — 作为顶层函数导出，OpenClaw 按 contracts.tools 名称自动发现
import { getBridge, isBridgeReady } from "./channel.js";

export async function p2p_send_message(params) {
  const bridge = getBridge();
  if (!bridge || !isBridgeReady()) {
    return { error: "铭信 bridge 未就绪，请稍候重试。" };
  }
  try {
    const result = await bridge.send(
      (params.to || "").replace(/^@/, ""),
      params.content || ""
    );
    return {
      ok: true,
      msg_id: result.msg_id,
      delivery: result.delivery || "p2p",
      message: `✅ 已通过 ${result.delivery || "p2p"} 发送给 @${params.to}`,
    };
  } catch (err) {
    return { error: `发送失败: ${err.message}` };
  }
}

export async function p2p_broadcast(params) {
  const bridge = getBridge();
  if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
  try {
    await bridge.broadcast(params.content);
    return { ok: true, message: "📢 已广播" };
  } catch (err) {
    return { error: `广播失败: ${err.message}` };
  }
}

export async function p2p_discover_agents(_params) {
  const bridge = getBridge();
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
}

export async function p2p_get_identity(_params) {
  const bridge = getBridge();
  if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
  try {
    return await bridge.getIdentity();
  } catch (err) {
    return { error: err.message };
  }
}

export async function p2p_spv_status(_params) {
  const bridge = getBridge();
  if (!bridge || !isBridgeReady()) return { error: "铭信 bridge 未就绪" };
  try {
    return await bridge.spvStatus();
  } catch (err) {
    return { error: err.message };
  }
}

export async function p2p_fetch_offline(_params) {
  const bridge = getBridge();
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
}

// 保留旧的 agent-tools.js 函数供编程调用
export {
  registerP2PTools,
  resolveP2PToolAccount,
  createP2PToolClient,
} from "./agent-tools.js";
