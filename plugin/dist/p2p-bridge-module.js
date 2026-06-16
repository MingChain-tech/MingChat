/**
 * P2P Bridge ESM Module — TCP-only version (no child_process)
 * 连接已运行的 systemd daemon，不启动子进程
 */
import { createConnection } from "net";
import { createInterface } from "readline";
import { EventEmitter } from "events";

const DEFAULT_CONFIG = {
  host: "127.0.0.1",
  rpcPort: 9877,
  reconnectDelay: 3000,
  maxReconnects: 30,
  pingInterval: 30000,
  connectTimeout: 5000,
};

export class P2PBridge extends EventEmitter {
  constructor(config = {}) {
    super();
    this.config = { ...DEFAULT_CONFIG, ...config };
    this._socket = null;
    this._running = false;
    this._ready = false;
    this._reconnectCount = 0;
    this._pending = new Map();
    this._msgId = 0;
    this._pingTimer = null;
    this._rl = null;
    this._buf = "";
    this.status = {
      handle: "?",
      listening: null,
      pubkey: null,
      peers: 0,
      spv: { synced: false, headers: 0 },
      uptime: 0,
    };
  }

  async start() {
    if (this._running) return this.status;

    this._running = true;
    return new Promise((resolve, reject) => {
      const doConnect = () => {
        if (!this._running) {
          reject(new Error("Bridge stopped"));
          return;
        }

        this._socket = createConnection(
          { host: this.config.host, port: this.config.rpcPort },
          () => {
            this._reconnectCount = 0;
            this._rl = createInterface({ input: this._socket, crlfDelay: Infinity });

            // 连接成功后立即 ping 验证连通性
            let resolved = false;
            const finish = (err, data) => {
              if (resolved) return;
              resolved = true;
              clearTimeout(timeout);
              if (err) {
                this._socket.destroy();
                reject(err);
              } else {
                this._ready = true;
                this._rl.on("line", (l) => this._handleLine(l));
                this._startPing();
                // 异步获取 daemon 状态信息
                this._call("status", {}, 5000).then(s => {
                  if (s) {
                    this.status.listening = s.listening || this.status.listening;
                    this.status.pubkey = s.pubkey || this.status.pubkey;
                    this.status.handle = s.handle || this.status.handle;
                    this.status.peers = s.peers || 0;
                    this.status.spv = s.spv || {};
                    this.emit("ready", {
                      handle: this.status.handle,
                      pubkey: this.status.pubkey,
                      listening: this.status.listening,
                    });
                  }
                }).catch(() => {});
                resolve({});
              }
            };

            const timeout = setTimeout(() => {
              finish(new Error("Ready timeout (30s)"), null);
            }, 30000);

            // 监听事件直到收到第一条消息
            const onLine = (line) => {
              try {
                const msg = JSON.parse(line);
                if (msg.event === "ready" || msg.event === "tcp_ready") {
                  finish(null, msg.data);
                } else if (msg.result || msg.error) {
                  // ping 响应视为就绪
                  finish(null, {});
                }
              } catch (_) {}
            };
            this._rl.on("line", onLine);

            // 发送 ping 探测（daemon 的 ready 事件可能在 TCP 连接建立前就已发出）
            this._socket.write(JSON.stringify({ jsonrpc: "2.0", id: 0, method: "ping", params: {} }) + "\n");
          }
        );

        this._socket.on("error", (err) => {
          this._ready = false;
          if (this._running && this._reconnectCount < this.config.maxReconnects) {
            this._reconnectCount++;
            setTimeout(doConnect, this.config.reconnectDelay);
          } else {
            this._running = false;
            this.emit("error", err);
            reject(err);
          }
        });

        this._socket.on("close", () => {
          this._ready = false;
          this._socket = null;
          this._clearPending(new Error("Connection closed"));
          if (this._running && this._reconnectCount < this.config.maxReconnects) {
            this._reconnectCount++;
            setTimeout(doConnect, this.config.reconnectDelay);
          } else if (this._running) {
            this._running = false;
            this.emit("error", new Error("Max reconnects exceeded"));
          }
        });
      };

      doConnect();
    });
  }

  async stop() {
    this._running = false;
    this._ready = false;
    if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null; }
    this._clearPending(new Error("Bridge stopped"));
    if (this._socket) {
      try { this._socket.destroy(); } catch (_) {}
      this._socket = null;
    }
  }

  async send(to, content) {
    if (!this._ready) throw new Error("Bridge not ready");
    return this._call("send_message", { to, content });
  }

  async broadcast(content) {
    if (!this._ready) throw new Error("Bridge not ready");
    return this._call("broadcast", { content });
  }

  async listPeers() {
    if (!this._ready) throw new Error("Bridge not ready");
    return this._call("list_peers");
  }

  async getIdentity() {
    if (!this._ready) throw new Error("Bridge not ready");
    return this._call("get_identity");
  }

  async spvStatus() {
    if (!this._ready) throw new Error("Bridge not ready");
    return this._call("spv_status");
  }

  async fetchOffline() {
    if (!this._ready) throw new Error("Bridge not ready");
    return this._call("fetch_offline");
  }

  async getStatus() {
    if (!this._ready) return { ...this.status, running: false };
    try {
      const s = await this._call("status", {}, 5000);
      this.status.peers = s.peers || 0;
      this.status.spv = s.spv || { synced: false, headers: 0 };
      this.status.listening = s.listening;
      return { ...this.status, ...s };
    } catch (_) {
      return { ...this.status, running: false };
    }
  }

  async _call(method, params = {}, timeout = 15000) {
    const msgId = ++this._msgId;
    const request = JSON.stringify({ jsonrpc: "2.0", id: msgId, method, params });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(msgId);
        reject(new Error(`RPC timeout: ${method}`));
      }, timeout);
      this._pending.set(msgId, { resolve, reject, timer });
      if (!this._socket || this._socket.destroyed) {
        clearTimeout(timer);
        this._pending.delete(msgId);
        reject(new Error("Socket not connected"));
        return;
      }
      try {
        this._socket.write(request + "\n");
      } catch (err) {
        clearTimeout(timer);
        this._pending.delete(msgId);
        reject(err);
      }
    });
  }

  _handleLine(line) {
    try {
      const msg = JSON.parse(line);
      if (msg.id !== undefined && this._pending.has(msg.id)) {
        const { resolve, reject, timer } = this._pending.get(msg.id);
        clearTimeout(timer);
        this._pending.delete(msg.id);
        if (msg.error) {
          reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        } else {
          resolve(msg.result);
        }
        return;
      }
      if (msg.event) {
        if (msg.event === "message_received") {
          this.emit("message", {
            from: msg.data.from,
            content: msg.data.content,
            msg_id: msg.data.msg_id,
            delivery: msg.data.delivery || "p2p",
            timestamp: msg.data.ts,
          });
        } else if (msg.event === "peer_online") {
          this.emit("peer_online", msg.data);
        } else if (msg.event === "peer_offline") {
          this.emit("peer_offline", msg.data);
        } else {
          this.emit(msg.event, msg.data);
        }
      }
    } catch (_) {}
  }

  _clearPending(error) {
    for (const [, { reject, timer }] of this._pending) {
      clearTimeout(timer);
      reject(error);
    }
    this._pending.clear();
  }

  _startPing() {
    if (this._pingTimer) clearInterval(this._pingTimer);
    this._pingTimer = setInterval(async () => {
      if (!this._ready) return;
      try {
        const result = await this._call("ping", {}, 5000);
        if (result?.pong) {
          try {
            const s = await this._call("status", {}, 5000);
            this.status.peers = s.peers || 0;
            this.status.spv = s.spv || {};
          } catch (_) {}
        }
      } catch (_) {}
    }, this.config.pingInterval);
  }
}
