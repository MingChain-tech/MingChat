import { useState, useEffect, useCallback } from 'react';
import type { StatusData, Message, MessagesData, MsgFeeStatsData } from './api';
import * as api from './api';

function Header({ status, stats }: { status: StatusData | null; stats: MsgFeeStatsData | null }) {
  return (
    <header className="header">
      <div className="header-left">
        <span className="header-logo">🏛️</span>
        <span className="header-title">铭信控制台</span>
        <span className="header-subtitle">Agent 监督与授权中心</span>
      </div>
      <div className="header-right">
        <div className="header-stat">
          <span className="header-stat-label">钱包地址</span>
          <span className="header-stat-value">
            {status ? `${status.address.slice(0, 12)}...` : '加载中...'}
          </span>
        </div>
        <div className="header-stat">
          <span className="header-stat-label">余额</span>
          <span className={`header-stat-value ${status && status.balance_sat > 0 ? 'green' : ''}`}>
            {status ? `${status.balance_sat.toLocaleString()} sat` : '...'}
          </span>
        </div>
        <div className="header-stat">
          <span className="header-stat-label">消息费收入</span>
          <span className="header-stat-value green">
            {stats ? `${stats.total_fee_received.toLocaleString()} sat` : '...'}
          </span>
        </div>
        <div className="header-stat">
          <span className="header-stat-label">SPV</span>
          <span className={`header-stat-value ${status?.listening ? 'green' : ''}`}>
            {status ? (status.listening ? '✅ 运行中' : '⏸️ 已暂停') : '...'}
          </span>
        </div>
      </div>
    </header>
  );
}

// ── 消息流面板 ──

function MsgPanel({
  messages,
  onRefresh,
  onSend,
}: {
  messages: Message[];
  onRefresh: () => void;
  onSend: (to: string, content: string, fee: number) => Promise<void>;
}) {
  const [to, setTo] = useState('');
  const [content, setContent] = useState('');
  const [fee, setFee] = useState(500);
  const [sending, setSending] = useState(false);
  const [filter, setFilter] = useState('');

  const handleSend = async () => {
    if (!to || !content) return;
    setSending(true);
    try {
      await onSend(to, content, fee);
      setContent('');
      onRefresh();
    } catch (e: any) {
      alert(`发送失败: ${e.message}`);
    } finally {
      setSending(false);
    }
  };

  const typeBadge = (type: string) => {
    if (type === 'CHAT') return <span className="badge badge-chat">聊天</span>;
    if (type.includes('DID')) return <span className="badge badge-did">DID</span>;
    if (type.includes('REPUTATION')) return <span className="badge badge-rep">信誉</span>;
    if (type.includes('TASK') || type.includes('BID') || type.includes('DELIVER'))
      return <span className="badge badge-task">任务</span>;
    return <span className="badge badge-free">{type}</span>;
  };

  const priorityBadge = (p: string) => {
    const map: Record<string, string> = {
      free: 'badge-free',
      low: 'badge-low',
      medium: 'badge-medium',
      high: 'badge-high',
    };
    return <span className={`badge ${map[p] || 'badge-free'}`}>{p}</span>;
  };

  const filteredMsgs = filter
    ? messages.filter((m) => m.priority === filter)
    : messages;

  return (
    <div className="col">
      <div className="col-header">
        <h2>📩 消息流 {messages.length > 0 && `(${messages.length})`}</h2>
        <div style={{ display: 'flex', gap: 6 }}>
          <select
            className="btn btn-sm"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}
          >
            <option value="">全部</option>
            <option value="free">免费</option>
            <option value="low">低优先</option>
            <option value="medium">中优先</option>
            <option value="high">高优先</option>
          </select>
          <button className="btn btn-sm" onClick={onRefresh}>
            🔄 刷新
          </button>
        </div>
      </div>

      {/* 发送表单 */}
      <div className="send-form">
        <div className="send-row">
          <input
            className="input"
            placeholder="收件地址 1PPY..."
            value={to}
            onChange={(e) => setTo(e.target.value)}
            style={{ fontSize: 12 }}
          />
        </div>
        <div className="send-row">
          <input
            className="input"
            placeholder="消息内容..."
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          />
          <button
            className="btn btn-primary"
            onClick={handleSend}
            disabled={sending || !to || !content}
          >
            {sending ? '⏳' : '📤'}
          </button>
        </div>
        <div className="fee-slider">
          <input
            type="range"
            min="0"
            max="5000"
            step="100"
            value={fee}
            onChange={(e) => setFee(Number(e.target.value))}
          />
          <span className="fee-value">
            {fee === 0 ? '免费' : `${fee} sat`} {priorityBadge(fee === 0 ? 'free' : fee < 100 ? 'low' : fee < 1000 ? 'medium' : 'high')}
          </span>
        </div>
      </div>

      {/* 消息列表 */}
      <div className="col-body">
        {filteredMsgs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">📭</div>
            <div>暂无消息</div>
          </div>
        ) : (
          filteredMsgs.map((m, i) => (
            <div key={m.txid || i} className={`msg-item ${m.read ? '' : 'unread'}`}>
              <div className="msg-header">
                <div className="msg-type-row">
                  {typeBadge(m.type)}
                  {priorityBadge(m.priority)}
                </div>
                <span className="msg-fee" style={{ color: m.msg_fee > 0 ? 'var(--yellow)' : 'var(--text-muted)' }}>
                  {m.msg_fee > 0 ? `${m.msg_fee} sat` : ''}
                </span>
              </div>
              <div className="msg-content">{m.content}</div>
              <div className="msg-meta">
                <span className="msg-addr">{m.from.slice(0, 14)}...</span>
                <span>{m.time_str}</span>
                <a
                  href={`https://whatsonchain.com/tx/${m.txid}`}
                  target="_blank"
                  rel="noopener"
                  className="msg-txid"
                  title={m.txid}
                >
                  {m.txid.slice(0, 16)}...
                </a>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ── 密钥权限面板 ──

function KeyPanel({ status }: { status: StatusData | null }) {
  const [didInput, setDidInput] = useState(
    'did:bsv:f595cd85067a6c8aa0423bd8d7e221c2e07b5ba7'
  );
  const [didResult, setDidResult] = useState<any>(null);
  const [didLoading, setDidLoading] = useState(false);

  const [repInput, setRepInput] = useState('');
  const [repStats, setRepStats] = useState<any>(null);
  const [repLoading, setRepLoading] = useState(false);

  const [webhookUrl, setWebhookUrl] = useState('');
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [currentWebhook, setCurrentWebhook] = useState<string | null>(null);

  useEffect(() => {
    api.getWebhook().then((r) => setCurrentWebhook(r.webhook_url)).catch(() => {});
  }, []);

  const handleDidResolve = async () => {
    if (!didInput) return;
    setDidLoading(true);
    try {
      const r = await api.resolveDid(didInput);
      setDidResult(r);
    } catch (e: any) {
      setDidResult({ error: e.message });
    }
    setDidLoading(false);
  };

  const handleRepQuery = async () => {
    if (!repInput) return;
    setRepLoading(true);
    try {
      const stats = await api.getReputationStats(repInput);
      setRepStats(stats);
    } catch (e: any) {
      setRepStats({ error: e.message });
    }
    setRepLoading(false);
  };

  const handleSetWebhook = async () => {
    if (!webhookUrl) return;
    setWebhookLoading(true);
    try {
      await api.setWebhook(webhookUrl);
      setCurrentWebhook(webhookUrl);
      setWebhookUrl('');
    } catch (e: any) {
      alert(`设置失败: ${e.message}`);
    }
    setWebhookLoading(false);
  };

  const handleClearWebhook = async () => {
    setWebhookLoading(true);
    try {
      await api.clearWebhook();
      setCurrentWebhook(null);
    } catch (e: any) {
      alert(`清除失败: ${e.message}`);
    }
    setWebhookLoading(false);
  };

  return (
    <div className="col">
      <div className="col-header">
        <h2>🔑 密钥权限</h2>
      </div>
      <div className="col-body">
        {/* DID 查询 */}
        <div className="card">
          <div className="card-title">🔍 DID 链上查询</div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <input
              className="input"
              placeholder="did:bsv:..."
              value={didInput}
              onChange={(e) => setDidInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleDidResolve()}
              style={{ fontSize: 12 }}
            />
            <button className="btn btn-sm btn-primary" onClick={handleDidResolve} disabled={didLoading}>
              {didLoading ? '⏳' : '查询'}
            </button>
          </div>
          {didResult && !didResult.error && (
            <div style={{ fontSize: 12 }}>
              <div><strong>{didResult.name || '未命名'}</strong></div>
              <div style={{ color: 'var(--text-muted)', marginTop: 2 }}>
                {didResult.service_endpoint || '无端点'} | 等级: Lv{didResult.identity_level ?? 0}
              </div>
              {didResult.registration_txid && (
                <a
                  href={`https://whatsonchain.com/tx/${didResult.registration_txid}`}
                  target="_blank"
                  rel="noopener"
                  style={{ fontSize: 11, color: 'var(--accent)' }}
                >
                  注册 TXID: {didResult.registration_txid.slice(0, 16)}...
                </a>
              )}
            </div>
          )}
          {didResult?.error && <div className="error-msg">{didResult.error}</div>}
        </div>

        {/* Webhook 管理 */}
        <div className="card">
          <div className="card-title">📡 Webhook 推送</div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            当前: {currentWebhook ? (
              <span style={{ color: 'var(--green)', fontSize: 11 }}>{currentWebhook}</span>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>未设置</span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              className="input"
              placeholder="Webhook URL..."
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              style={{ fontSize: 12, flex: 1 }}
            />
            <button className="btn btn-sm" onClick={handleSetWebhook} disabled={webhookLoading || !webhookUrl}>
              设置
            </button>
            {currentWebhook && (
              <button className="btn btn-sm btn-danger" onClick={handleClearWebhook} disabled={webhookLoading}>
                清除
              </button>
            )}
          </div>
        </div>

        {/* 信誉查询 */}
        <div className="card">
          <div className="card-title">⭐ 信誉查询</div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <input
              className="input"
              placeholder="did:bsv:..."
              value={repInput}
              onChange={(e) => setRepInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleRepQuery()}
              style={{ fontSize: 12 }}
            />
            <button className="btn btn-sm btn-primary" onClick={handleRepQuery} disabled={repLoading}>
              {repLoading ? '⏳' : '查询'}
            </button>
          </div>
          {repStats && !repStats.error && (
            <div>
              <div className="stat-grid">
                <div className="stat-item">
                  <div className="stat-item-label">平均分</div>
                  <div className={`stat-item-value ${repStats.avg_score > 0 ? 'green' : ''}`}>
                    {repStats.avg_score?.toFixed(1) || '-'}
                  </div>
                </div>
                <div className="stat-item">
                  <div className="stat-item-label">评分人数</div>
                  <div className="stat-item-value">{repStats.score_count || 0}</div>
                </div>
                <div className="stat-item">
                  <div className="stat-item-label">独特评分者</div>
                  <div className="stat-item-value">{repStats.unique_raters || 0}</div>
                </div>
                <div className="stat-item">
                  <div className="stat-item-label">质押总额</div>
                  <div className="stat-item-value green">{(repStats.bond_sats || 0).toLocaleString()} sat</div>
                </div>
              </div>
            </div>
          )}
          {repStats?.error && <div className="error-msg">{repStats.error}</div>}
        </div>

        {/* Agent 钱包信息 */}
        {status && (
          <div className="card">
            <div className="card-title">💳 Agent 钱包</div>
            <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
              <div>地址: {status.address}</div>
              <div style={{ color: 'var(--green)', marginTop: 4 }}>
                余额: {status.balance_sat.toLocaleString()} sat = {status.balance_bsv} BSV
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── 链上验证面板 ──

function ChainPanel({ status, stats }: { status: StatusData | null; stats: MsgFeeStatsData | null }) {
  if (!stats) {
    return (
      <div className="col">
        <div className="col-header"><h2>⛓️ 链上验证</h2></div>
        <div className="col-body"><div className="loading">加载中...</div></div>
      </div>
    );
  }

  const maxCount = Math.max(
    stats.count_by_priority.free,
    stats.count_by_priority.low,
    stats.count_by_priority.medium,
    stats.count_by_priority.high,
    1
  );

  return (
    <div className="col">
      <div className="col-header">
        <h2>⛓️ 链上验证</h2>
        <button className="btn btn-sm" onClick={() => window.location.reload()}>
          🔄 刷新
        </button>
      </div>
      <div className="col-body">
        {/* 钱包概览 */}
        <div className="card">
          <div className="card-title">💰 链上资产</div>
          <div className="stat-grid">
            <div className="stat-item">
              <div className="stat-item-label">BSV 余额</div>
              <div className="stat-item-value green" style={{ fontSize: 16 }}>
                {status?.balance_bsv?.toFixed(8) || '0'}
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-item-label">Sat 余额</div>
              <div className="stat-item-value green">
                {status?.balance_sat?.toLocaleString() || '0'}
              </div>
            </div>
          </div>
          {status?.address && (
            <div style={{ marginTop: 8, fontSize: 11 }}>
              <a
                href={`https://whatsonchain.com/address/${status.address}`}
                target="_blank"
                rel="noopener"
                style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}
              >
                {status.address} 🔗
              </a>
            </div>
          )}
        </div>

        {/* 消息费统计 */}
        <div className="card">
          <div className="card-title">💸 消息费统计</div>
          <div className="stat-grid">
            <div className="stat-item">
              <div className="stat-item-label">总收入</div>
              <div className="stat-item-value green">
                {stats.total_fee_received.toLocaleString()} sat
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-item-label">总消息</div>
              <div className="stat-item-value">{stats.total_messages}</div>
            </div>
          </div>

          {/* 优先级柱状图 */}
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>优先级分布</div>
            <div className="priority-bars">
              {(['free', 'low', 'medium', 'high'] as const).map((p) => (
                <div key={p} className={`priority-bar ${p}`} style={{ height: `${(stats.count_by_priority[p] / maxCount) * 40 + 4}px` }}>
                  <span className="priority-count">{stats.count_by_priority[p]}</span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)' }}>
              <span>免费</span>
              <span>低</span>
              <span>中</span>
              <span>高</span>
            </div>
          </div>
        </div>

        {/* 网络状态 */}
        <div className="card">
          <div className="card-title">🌐 网络状态</div>
          <div className="stat-grid">
            <div className="stat-item">
              <div className="stat-item-label">SPV 监听</div>
              <div className="stat-item-value" style={{ fontSize: 14 }}>
                {status?.listening ? '✅ 运行中' : '⏸️ 已暂停'}
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-item-label">消息计数</div>
              <div className="stat-item-value">{status?.message_count ?? '-'}</div>
            </div>
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
            监听周期: 15 秒 | 方案: WoC HTTPS 轮询
          </div>
        </div>

        {/* 铭信协议信息 */}
        <div className="card">
          <div className="card-title">📋 协议版本</div>
          <div style={{ fontSize: 12 }}>
            <div>铭信 MingChat v0.3.5</div>
            <div style={{ color: 'var(--text-muted)', marginTop: 4 }}>
              122B 协议头 | 21 种消息类型 | BSV OP_RETURN
            </div>
            <div style={{ color: 'var(--text-muted)', marginTop: 2 }}>
              消息费方案C | SPV 双通道 | DID 链上解析
            </div>
            <a
              href="https://github.com/MingChain-tech/MingChat"
              target="_blank"
              rel="noopener"
              style={{ fontSize: 11, color: 'var(--accent)', display: 'inline-block', marginTop: 6 }}
            >
              GitHub → MingChain-tech/MingChat MIT
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 主应用 ──

export default function App() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [stats, setStats] = useState<MsgFeeStatsData | null>(null);
  const [error, setError] = useState('');

  const refreshAll = useCallback(async () => {
    try {
      const [s, mData, feeStats] = await Promise.all([
        api.getStatus(),
        api.getMessages(50),
        api.getMsgFeeStats(),
      ]);
      setStatus(s);
      setMessages(mData.messages || []);
      setStats(feeStats);
      setError('');
    } catch (e: any) {
      setError(`连接 Bridge 失败: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    refreshAll();
    const timer = setInterval(refreshAll, 15000); // 15s 自动刷新
    return () => clearInterval(timer);
  }, [refreshAll]);

  const handleSend = async (to: string, content: string, fee: number) => {
    await api.sendMessage(to, content, fee);
  };

  return (
    <div className="console">
      <Header status={status} stats={stats} />

      {error && (
        <div style={{ padding: '8px 20px', background: 'rgba(248,81,73,0.1)', borderBottom: '1px solid var(--red)', fontSize: 12, color: 'var(--red)' }}>
          ⚠️ {error} — <button onClick={refreshAll} style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}>重试</button>
        </div>
      )}

      <div className="three-col">
        <MsgPanel messages={messages} onRefresh={refreshAll} onSend={handleSend} />
        <KeyPanel status={status} />
        <ChainPanel status={status} stats={stats} />
      </div>
    </div>
  );
}
