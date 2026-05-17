import type {
  HealthResponse,
  StatusResponse,
  MessagesResponse,
  SendResponse,
  DidResponse,
  ReputationScoresResponse,
  ReputationStats,
  ReputationBondsResponse,
  WebhookResponse,
  MsgFeeStats,
  GetMessagesOptions,
  SendMessageOptions,
  ErrorResponse,
} from './types';

export class MingChainClient {
  private baseUrl: string;

  constructor(baseUrl: string = 'http://121.37.44.29:8900') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private async fetch<T>(path: string, options?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    const data = await res.json();
    if (!res.ok) {
      const err = data as ErrorResponse;
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return data as T;
  }

  // ── 健康检查 ──

  async health(): Promise<HealthResponse> {
    return this.fetch<HealthResponse>('/health');
  }

  // ── 状态 ──

  async status(): Promise<StatusResponse> {
    return this.fetch<StatusResponse>('/status');
  }

  // ── 消息 ──

  async getMessages(opts: GetMessagesOptions = {}): Promise<MessagesResponse> {
    const params = new URLSearchParams();
    if (opts.limit !== undefined) params.set('limit', String(opts.limit));
    if (opts.unread) params.set('unread', 'true');
    if (opts.priority) params.set('priority', opts.priority);
    if (opts.minFee) params.set('min_fee', String(opts.minFee));
    if (opts.markRead === false) params.set('mark_read', 'false');
    const qs = params.toString();
    return this.fetch<MessagesResponse>(`/messages${qs ? '?' + qs : ''}`);
  }

  async sendMessage(
    to: string,
    content: string,
    opts: SendMessageOptions = {}
  ): Promise<SendResponse> {
    return this.fetch<SendResponse>('/send', {
      method: 'POST',
      body: JSON.stringify({
        to_address: to,
        content,
        msg_type: opts.msgType || 'TEXT',
        msg_fee: opts.msgFee ?? 0,
      }),
    });
  }

  // ── DID ──

  async resolveDid(did: string): Promise<DidResponse> {
    return this.fetch<DidResponse>(`/did/${encodeURIComponent(did)}`);
  }

  // ── 信誉 ──

  async getReputationScores(
    did: string,
    limit: number = 50
  ): Promise<ReputationScoresResponse> {
    return this.fetch<ReputationScoresResponse>(
      `/reputation/${encodeURIComponent(did)}/scores?limit=${limit}`
    );
  }

  async getReputationStats(did: string): Promise<ReputationStats> {
    return this.fetch<ReputationStats>(
      `/reputation/${encodeURIComponent(did)}/stats`
    );
  }

  async getReputationBonds(did: string): Promise<ReputationBondsResponse> {
    return this.fetch<ReputationBondsResponse>(
      `/reputation/${encodeURIComponent(did)}/bonds`
    );
  }

  // ── Webhook ──

  async getWebhook(): Promise<WebhookResponse> {
    return this.fetch<WebhookResponse>('/webhook');
  }

  async setWebhook(url: string): Promise<WebhookResponse> {
    return this.fetch<WebhookResponse>('/webhook/set', {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
  }

  async clearWebhook(): Promise<WebhookResponse> {
    return this.fetch<WebhookResponse>('/webhook/clear', {
      method: 'POST',
    });
  }

  // ── 统计 ──

  async msgFeeStats(): Promise<MsgFeeStats> {
    return this.fetch<MsgFeeStats>('/stats/msg-fee', {
      method: 'POST',
    });
  }
}

export * from './types';
