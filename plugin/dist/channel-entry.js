/**
 * P2P Chat Channel Entry — OpenClaw 插件入口
 * 对标 feishu/extensions/feishu/channel-entry.ts
 */
import { defineBundledChannelEntry } from "openclaw/plugin-sdk/channel-entry-contract";

export default defineBundledChannelEntry({
  id: "p2p",
  name: "P2P Chat",
  description: "Decentralized encrypted P2P messaging — ECDH+AES-256-GCM, SPV on-chain verification, gossip mesh",
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
