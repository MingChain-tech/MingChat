// Bridge API 封装 — 通过 Nginx 代理访问
// 代理路径: /api/bridge/ → http://127.0.0.1:8900/

const BASE_URL = '/api/bridge';

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as any).error || `HTTP ${res.status}`);
  return data as T;
}

// ── 类型 ──

export interface StatusData {
  status: string;
  address: string;
  balance_sat: number;
  balance_bsv: number;
  listening: boolean;
  message_count: number;
  webhook: string | null;
}

export interface Message {
  type: string;
  from: string;
  to: string;
  content: string;
  timestamp: number;
  time_str: string;
  txid: string;
  msg_fee: number;
  priority: 'free' | 'low' | 'medium' | 'high';
  read: boolean;
}

export interface MessagesData {
  status: string;
  count: number;
  total: number;
  messages: Message[];
}

export interface SendResult {
  status: string;
  txid: string;
  from: string;
  to: string;
  url: string;
}

export interface DidData {
  status: string;
  did: string;
  name: string;
  description: string;
  service_endpoint: string;
  controller_pk: string;
  identity_level: number;
  kyc_provider: string | null;
  registration_txid: string | null;
}

export interface ReputationStatsData {
  status: string;
  score_count: number;
  unique_raters: number;
  avg_score: number;
  avg_dims: Record<string, number>;
  bond_sats: number;
  last_score_at: string | null;
}

export interface ReputationScoreItem {
  from_did: string;
  target_did: string;
  score: number;
  sig: string;
  txid: string;
  timestamp: number;
}

export interface ReputationScoresData {
  did: string;
  total: number;
  scores: ReputationScoreItem[];
}

export interface MsgFeeStatsData {
  status: string;
  total_fee_received: number;
  total_messages: number;
  count_by_priority: { free: number; low: number; medium: number; high: number };
}

// ── API 函数 ──

export async function getStatus(): Promise<StatusData> {
  return fetchJson<StatusData>('/status');
}

export async function getMessages(
  limit = 20,
  priority?: string
): Promise<MessagesData> {
  const params = new URLSearchParams();
  params.set('limit', String(limit));
  if (priority) params.set('priority', priority);
  return fetchJson<MessagesData>(`/messages?${params}`);
}

export async function sendMessage(
  to: string,
  content: string,
  msgFee = 500
): Promise<SendResult> {
  return fetchJson<SendResult>('/send', {
    method: 'POST',
    body: JSON.stringify({ to_address: to, content, msg_fee: msgFee }),
  });
}

export async function resolveDid(did: string): Promise<DidData> {
  return fetchJson<DidData>(`/did/${encodeURIComponent(did)}`);
}

export async function getReputationStats(
  did: string
): Promise<ReputationStatsData> {
  return fetchJson<ReputationStatsData>(
    `/reputation/${encodeURIComponent(did)}/stats`
  );
}

export async function getReputationScores(
  did: string,
  limit = 20
): Promise<ReputationScoresData> {
  return fetchJson<ReputationScoresData>(
    `/reputation/${encodeURIComponent(did)}/scores?limit=${limit}`
  );
}

export async function getMsgFeeStats(): Promise<MsgFeeStatsData> {
  return fetchJson<MsgFeeStatsData>('/stats/msg-fee', { method: 'POST' });
}

export async function getWebhook(): Promise<{ status: string; webhook_url: string | null }> {
  return fetchJson('/webhook');
}

export async function setWebhook(url: string): Promise<{ status: string; webhook_url: string }> {
  return fetchJson('/webhook/set', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });
}

export async function clearWebhook(): Promise<{ status: string; webhook_url: null }> {
  return fetchJson('/webhook/clear', { method: 'POST' });
}
