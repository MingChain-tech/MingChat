// 铭信 MingChat v0.3.5 Bridge API TypeScript 类型定义

export interface HealthResponse {
  status: string;
  uptime_sec: number;
  listening: boolean;
}

export interface StatusResponse {
  status: string;
  address: string;
  balance_sat: number;
  balance_bsv: number;
  listening: boolean;
  message_count: number;
  webhook: string | null;
  inbox_file: string;
  data_dir: string;
}

export interface Message {
  type: string;
  from: string;
  sender_did?: string;
  to: string;
  content: string;
  timestamp: number;
  time_str: string;
  txid: string;
  msg_fee: number;
  priority: 'free' | 'low' | 'medium' | 'high';
  read: boolean;
}

export interface MessagesResponse {
  status: string;
  count: number;
  total: number;
  messages: Message[];
}

export interface SendResponse {
  status: string;
  txid: string;
  from: string;
  to: string;
  content: string;
  url: string;
}

export interface DidResponse {
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

export interface ReputationScore {
  from_did: string;
  target_did: string;
  score: number;
  dims: Record<string, number>;
  sig: string;
  txid: string;
  timestamp: number;
}

export interface ReputationScoresResponse {
  did: string;
  total: number;
  scores: ReputationScore[];
}

export interface ReputationStats {
  status: string;
  score_count: number;
  unique_raters: number;
  avg_score: number;
  avg_dims: Record<string, number>;
  bond_sats: number;
  last_score_at: string | null;
}

export interface ReputationBond {
  action: string;
  amount: number;
  target_did: string;
  from_did: string;
  txid: string;
  timestamp: number;
}

export interface ReputationBondsResponse {
  did: string;
  total: number;
  bonds: ReputationBond[];
}

export interface WebhookResponse {
  status: string;
  webhook_url: string | null;
}

export interface MsgFeeStats {
  status: string;
  total_fee_received: number;
  total_messages: number;
  count_by_priority: {
    free: number;
    low: number;
    medium: number;
    high: number;
  };
}

export interface ErrorResponse {
  error: string;
}

export interface GetMessagesOptions {
  limit?: number;
  unread?: boolean;
  priority?: string;
  minFee?: number;
  markRead?: boolean;
}

export interface SendMessageOptions {
  msgType?: string;
  msgFee?: number;
}
