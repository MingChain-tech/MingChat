/**
 * 铭信 MingChat Channel Plugin — 完整 OpenClaw 通道实现
 * = createChatChannelPlugin + 完整 config adapter
 * = TCP 连接 systemd daemon（无 child_process）
 */
import { createChatChannelPlugin } from "openclaw/plugin-sdk/channel-core";
import {
  createHybridChannelConfigAdapter,
  formatTrimmedAllowFromEntries,
} from "openclaw/plugin-sdk/channel-config-helpers";
import { homedir } from "node:os";
import { existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { P2PBridge } from "./p2p-bridge-module.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ─── 全局 bridge 状态 ──────────────────────────────────

let _bridge = null;
let _bridgeReady = false;

function getBridge() {
  return _bridge;
}

function isBridgeReady() {
  return _bridgeReady && _bridge !== null;
}

// ─── 配置常量 ──────────────────────────────────────────
const DEFAULT_ACCOUNT_ID = "default";
const SECTION_KEY = "p2p";

// ─── 账户解析 ──────────────────────────────────────────

function resolveP2PAccount(cfg, accountId) {
  const p2p = cfg?.channels?.p2p;
  if (!p2p) return { enabled: false, configured: false };

  return {
    enabled: p2p.enabled !== false,
    configured: Boolean(p2p.handle),
    name: `@${p2p.handle || "lobster"}`,
    appId: p2p.handle || "",
    handle: p2p.handle,
    pythonPath: p2p.pythonPath || "/usr/bin/python3.9",
    daemonScript: p2p.daemonScript || resolveDaemonScript(),
    dataDir: p2p.dataDir || `${homedir()}/.p2pchat`,
    network: p2p.network || "main",
    host: p2p.host || "127.0.0.1",
    port: p2p.port || 0,
    rpcPort: p2p.rpcPort || 9877,
    config: {
      allowFrom: p2p.allowFrom || ["*"],
      dmPolicy: p2p.dmPolicy || "open",
    },
  };
}

function listP2PAccountIds(cfg) {
  const p2p = cfg?.channels?.p2p;
  if (!p2p || !p2p.handle) return [];
  return [DEFAULT_ACCOUNT_ID];
}

function resolveDefaultP2PAccountId(cfg) {
  return DEFAULT_ACCOUNT_ID;
}

function resolveDaemonScript() {
  const candidates = [
    resolve(__dirname, "../../../p2p_daemon.py"),
    resolve(homedir(), "p2pchat/p2p_daemon.py"),
    "/root/p2pchat/p2p_daemon.py",
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

// ─── 配置适配器 ────────────────────────────────────────

const p2pConfigAdapter = createHybridChannelConfigAdapter({
  sectionKey: SECTION_KEY,
  listAccountIds: listP2PAccountIds,
  resolveAccount: resolveP2PAccount,
  defaultAccountId: resolveDefaultP2PAccountId,
  clearBaseFields: [],
  resolveAllowFrom: (account) => account.config?.allowFrom,
  formatAllowFrom: (allowFrom) => formatTrimmedAllowFromEntries(allowFrom),
});

// ─── Channel Plugin ────────────────────────────────────

export const p2pPlugin = createChatChannelPlugin({
  base: {
    id: "p2p",
    meta: {
      label: "铭信 MingChat",
      description: "Agent-to-Agent 去中心化加密通讯，BSV 链上离线消息存证",
    },

    capabilities: {
      chatTypes: ["direct"],
      polls: false,
      threads: false,
      media: false,
      tts: false,
      reactions: false,
      edit: false,
      reply: true,
    },

    agentPrompt: {
      messageToolHints: () => [
        "- 铭信 MingChat: Use `send_message` with target `p2p:@handle` to send encrypted Agent-to-Agent messages.",
        "- 铭信 MingChat: All messages are E2E encrypted with ECDH+AES-256-GCM.",
        "- 铭信 MingChat: Offline messages are stored on BSV blockchain with SPV verification.",
        "- 铭信 MingChat: Use `p2p_discover_agents` to find online peers.",
      ],
    },

    // ─── 配置 Schema ──────────────────────────────
    configSchema: {
      type: "object",
      properties: {
        enabled: { type: "boolean", default: true },
        handle: { type: "string", description: "P2P @handle" },
        pythonPath: { type: "string", default: "/usr/bin/python3.9" },
        daemonScript: { type: "string" },
        dataDir: { type: "string" },
        network: { type: "string", enum: ["main", "testnet"], default: "main" },
        host: { type: "string", default: "127.0.0.1" },
        port: { type: "integer", default: 0 },
        rpcPort: { type: "integer", default: 9877, description: "TCP JSON-RPC port for daemon connection" },
        dmPolicy: { type: "string", enum: ["open", "pairing"], default: "open" },
        allowFrom: { type: "array", items: { type: "string" } },
      },
      required: ["handle"],
      additionalProperties: false,
    },

    // ─── 配置适配器 ──────────────────────────────
    config: {
      ...p2pConfigAdapter,

      setAccountEnabled({ cfg, accountId, enabled }) {
        const next = { ...cfg };
        const nextChannels = { ...cfg.channels };
        if (accountId === DEFAULT_ACCOUNT_ID) {
          nextChannels.p2p = { ...cfg.channels?.p2p, enabled };
        }
        next.channels = nextChannels;
        return next;
      },

      deleteAccount({ cfg, accountId }) {
        if (accountId === DEFAULT_ACCOUNT_ID) {
          const next = { ...cfg };
          const nextChannels = { ...cfg.channels };
          delete nextChannels.p2p;
          if (Object.keys(nextChannels).length > 0) next.channels = nextChannels;
          else delete next.channels;
          return next;
        }
        return cfg;
      },

      isConfigured(account) {
        return account?.configured === true;
      },

      describeAccount(account) {
        return {
          id: DEFAULT_ACCOUNT_ID,
          name: account?.name || `@${account?.handle || "?"}`,
          configured: account?.configured || false,
          extra: {
            handle: account?.handle,
            network: account?.network,
          },
        };
      },
    },

    // ─── Secrets（空） ─────────────────────────────
    secrets: {
      secretTargetRegistryEntries: [],
      collectRuntimeConfigAssignments: () => ({}),
    },

    // ─── Doctor 探针 ──────────────────────────────
    doctor: {
      async probe(cfg) {
        const p2p = cfg?.channels?.p2p;
        if (!p2p?.handle) {
          return { ok: false, reason: "P2P handle not configured" };
        }
        const script = p2p.daemonScript || resolveDaemonScript();
        if (!script || !existsSync(script)) {
          return { ok: false, reason: `p2p_daemon.py not found at ${script}` };
        }
        return { ok: true };
      },
    },

    // ─── Reload 配置 ──────────────────────────────
    reload: {
      configPrefixes: ["channels.p2p"],
    },

    // ─── Actions（消息收发） ──────────────────────
    actions: {
      messageActionTargetAliases: {
        p2p: SECTION_KEY,
      },

      describeMessageTool: () => ({
        name: "send_p2p_message",
        description: "Send an encrypted message via P2P decentralized network",
        parameters: {
          target: {
            type: "string",
            description: "Recipient: p2p:@handle",
          },
          content: {
            type: "string",
            description: "Message content to send",
          },
        },
      }),

      async handleAction(ctx) {
        const { action, params } = ctx;

        if (action === "send") {
          const target = (params.target || params.to || "").replace(/^p2p:/, "").replace(/^@/, "");

          if (!isBridgeReady()) {
            return { error: "P2P bridge not ready. Try again shortly." };
          }

          try {
            const bridge = getBridge();
            const result = await bridge.send(target, params.content || params.text || "");
            return {
              ok: true,
              msg_id: result.msg_id,
              delivery: result.delivery || "p2p",
            };
          } catch (err) {
            return { error: `P2P send failed: ${err.message}` };
          }
        }

        return { error: `Unknown action: ${action}` };
      },
    },

    // ─── DM 策略 ──────────────────────────────────
    bindings: {
      resolveDmPolicy: () => "open",
    },
  },

  // ─── Security ────────────────────────────────────
  security: {
    resolveDmPolicy: () => "open",
  },

  // ─── Pairing（简化） ────────────────────────────
  pairing: {
    dmPolicy: "open",
  },

  // ─── Outbound（简化） ────────────────────────────
  outbound: {
    outboundAdapter: {
      async send(params) {
        if (!isBridgeReady()) {
          return { error: "P2P bridge not ready" };
        }
        try {
          const bridge = getBridge();
          const result = await bridge.send(
            params.target?.replace(/^p2p:/, "").replace(/^@/, "") || params.to,
            params.content || params.text || ""
          );
          return { ok: true, msg_id: result.msg_id };
        } catch (err) {
          return { error: err.message };
        }
      },
    },
  },
});

// ─── 运行时初始化（自执行） ──────────────────────────

let _connectTimer = null;

async function _autoConnect() {
  if (_bridge && _bridgeReady) return;

  const rpcPort = 9877; // systemd daemon 固定端口
  console.error(`[p2p-plugin] Auto-connecting to P2P daemon on 127.0.0.1:${rpcPort}...`);

  const bridge = new P2PBridge({
    host: "127.0.0.1",
    rpcPort: rpcPort,
  });

  bridge.on("message", (msg) => {
    console.error(`[p2p-plugin] 📩 @${msg.from}: ${msg.content?.substring(0, 80)}`);
  });

  bridge.on("ready", (data) => {
    console.error(`[p2p-plugin] ✅ Bridge ready: @${data.handle} → ${data.listening}`);
    _bridgeReady = true;
    _bridge = bridge;
    if (_connectTimer) { clearInterval(_connectTimer); _connectTimer = null; }
  });

  bridge.on("error", (err) => {
    console.error(`[p2p-plugin] ❌ ${err.message}`);
    _bridgeReady = false;
    _bridge = null;
  });

  try {
    await bridge.start();
  } catch (err) {
    console.error(`[p2p-plugin] Connection failed: ${err.message}`);
  }
}

// 立即尝试连接，然后每 30 秒重试
_autoConnect().catch(err => console.error('[p2p-plugin] Init connect error:', err.message));
_connectTimer = setInterval(() => {
  _autoConnect().catch(err => console.error('[p2p-plugin] Retry error:', err.message));
}, 30000);

export { getBridge, isBridgeReady };
