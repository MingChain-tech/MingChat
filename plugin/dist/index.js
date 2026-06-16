/**
 * 铭信 MingChat Plugin — Index (OpenClaw plugin entry point)
 * = Agent-to-Agent 去中心化加密通讯通道
 * = daemon 通过 systemd 独立运行，通道启动时 TCP 连接
 */
import { defineBundledChannelEntry } from "openclaw/plugin-sdk/channel-entry-contract";

export default defineBundledChannelEntry({
  id: "p2p",
  name: "铭信 MingChat",
  description: "Agent-to-Agent 去中心化加密通讯 — ECDH+AES-256-GCM 端到端加密，SPV 链上验证，gossip mesh 组网",
  importMetaUrl: import.meta.url,
  plugin: {
    specifier: "./api.js",
    exportName: "p2pPlugin"
  },
  secrets: {
    specifier: "./secret-contract-api.js",
    exportName: "channelSecrets"
  },
  runtime: {
    specifier: "./runtime-api.js",
    exportName: "setP2PRuntime"
  }
});
