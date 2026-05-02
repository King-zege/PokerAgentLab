import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type LegalAction = { type: string; min?: number; max?: number; amount?: number };
type GameState = {
  session_id: string;
  status: string;
  current_hand: number;
  street?: string;
  pot_bb: number;
  community_cards: string[];
  current_player_id?: string;
  hole_cards: string[];
  legal_actions: LegalAction[];
  players: { id: string; stack_bb: number; position: string; hole_cards?: string[] }[];
  hand_complete: boolean;
  can_continue: boolean;
  last_hand_result?: Record<string, unknown>;
  error?: string;
};

const API = '/api';
const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));
const statusText: Record<string, string> = {
  idle: '未开始',
  created: '已创建',
  running: '运行中',
  waiting_for_action: '等待行动',
  completed: '已完成',
  error: '错误',
};
const streetText: Record<string, string> = {
  waiting: '等待中',
  preflop: '翻前',
  flop: '翻牌',
  turn: '转牌',
  river: '河牌',
};
const actionText: Record<string, string> = {
  fold: '弃牌',
  check: '过牌',
  call: '跟注',
  bet: '下注',
  raise: '加注',
  all_in: '全下',
};
const categoryText: Record<string, string> = {
  preferences: '偏好',
  leaks: '漏洞',
  goals: '训练目标',
  knowledge_state: '知识状态',
};

function App() {
  const [sessionId, setSessionId] = useState(`demo_${Date.now().toString().slice(-5)}`);
  const [state, setState] = useState<GameState | null>(null);
  const [traces, setTraces] = useState<any[]>([]);
  const [history, setHistory] = useState<any | null>(null);
  const [coach, setCoach] = useState<any | null>(null);
  const [report, setReport] = useState<any | null>(null);
  const [memoryProfile, setMemoryProfile] = useState<any | null>(null);
  const [memoryContext, setMemoryContext] = useState<any | null>(null);
  const [numHands, setNumHands] = useState(3);
  const [message, setMessage] = useState('就绪');
  const [actionAmounts, setActionAmounts] = useState<Record<string, number>>({});

  const canAct = state?.status === 'waiting_for_action' && !state.hand_complete && state.legal_actions.length > 0;

  useEffect(() => {
    if (!state) return;
    const timer = window.setInterval(() => refreshAll(sessionId), 1200);
    return () => window.clearInterval(timer);
  }, [state?.session_id]);

  async function request(path: string, options?: RequestInit) {
    const res = await fetch(`${API}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body || res.statusText);
    }
    return res.json();
  }

  async function requestOptional(path: string, options?: RequestInit) {
    try {
      return await request(path, options);
    } catch {
      return null;
    }
  }

  async function startSession() {
    try {
      setMessage('正在创建牌局...');
      await request('/sessions', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, mode: 'fixed', num_hands: numHands, config_path: 'config/game_config.yaml' }),
      });
      await waitForState(sessionId, next => next.status === 'waiting_for_action' || next.status === 'completed' || next.status === 'error');
      setMessage('牌局运行中');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function refreshAll(id = sessionId) {
    try {
      const nextState = await request(`/sessions/${id}/state`);
      setState(nextState);
      const traceData = await requestOptional(`/sessions/${id}/traces`);
      if (traceData) setTraces(traceData.traces || []);
      const profileData = await requestOptional('/memory/profile');
      if (profileData) setMemoryProfile(profileData);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function waitForState(id: string, done: (next: GameState) => boolean, attempts = 40) {
    let latest: GameState | null = null;
    for (let i = 0; i < attempts; i += 1) {
      const nextState = await request(`/sessions/${id}/state`);
      latest = nextState;
      setState(nextState);
      const traceData = await requestOptional(`/sessions/${id}/traces`);
      if (traceData) setTraces(traceData.traces || []);
      if (done(nextState)) return nextState;
      await sleep(150);
    }
    return latest;
  }

  async function submitAction(action: LegalAction) {
    try {
      const amount = actionAmount(action);
      setMessage(`正在提交：${actionLabel(action, amount)}`);
      await request(`/sessions/${sessionId}/action`, {
        method: 'POST',
        body: JSON.stringify({ action: action.type, amount }),
      });
      await waitForState(sessionId, next => next.hand_complete || next.status === 'waiting_for_action' || next.status === 'completed' || next.status === 'error');
      setMessage('行动已提交');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function continueHand(continue_game: boolean) {
    try {
      setMessage(continue_game ? '正在进入下一手...' : '正在结束牌局...');
      await request(`/sessions/${sessionId}/continue`, {
        method: 'POST',
        body: JSON.stringify({ continue_game }),
      });
      if (continue_game) {
        await waitForState(sessionId, next => !next.hand_complete && (next.status === 'waiting_for_action' || next.status === 'completed' || next.status === 'error'));
        setMessage('下一手已准备好');
      } else {
        await refreshAll();
        setMessage('牌局已结束');
      }
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function loadAnalysis() {
    try {
      const historyData = await requestOptional(`/sessions/${sessionId}/history`);
      if (historyData) setHistory(historyData);
      const coachData = await request(`/sessions/${sessionId}/coach`, { method: 'POST' });
      setCoach(coachData);
      setMessage('复盘反馈已加载');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function consolidateMemory() {
    try {
      setMessage('正在沉淀记忆...');
      const data = await request(`/sessions/${sessionId}/consolidate`, { method: 'POST' });
      setMemoryProfile(await request('/memory/profile'));
      setMessage(`生成了 ${data.candidate_memories.length} 条候选记忆`);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function loadMemoryContext() {
    try {
      const data = await request(`/sessions/${sessionId}/memory-context`);
      setMemoryContext(data);
      setMessage('记忆上下文已加载');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function updateCandidate(memoryId: string, action: 'accept' | 'reject') {
    try {
      await request(`/memory/profile/candidates/${memoryId}/${action}`, { method: 'POST' });
      setMemoryProfile(await request('/memory/profile'));
      setMessage(action === 'accept' ? '记忆已确认' : '记忆已拒绝');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function runSelfPlay() {
    try {
      setMessage('正在运行自博弈实验...');
      const data = await request('/experiments/self-play', {
        method: 'POST',
        body: JSON.stringify({ num_hands: 20, seed: 42 }),
      });
      setReport(data);
      setMessage(`实验 ${data.experiment_id} 已完成`);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  const latestTrace = useMemo(() => traces[traces.length - 1], [traces]);
  const actionAmount = (action: LegalAction) => {
    if (action.type === 'bet' || action.type === 'raise') {
      const fallback = action.min ?? action.amount ?? 0;
      const raw = actionAmounts[action.type] ?? fallback;
      const min = action.min ?? 0;
      const max = action.max ?? raw;
      return Math.max(min, Math.min(max, raw));
    }
    return action.min ?? action.amount ?? 0;
  };
  const actionLabel = (action: LegalAction, overrideAmount?: number) => {
    const amount = overrideAmount ?? action.min ?? action.amount;
    const label = actionText[action.type] || action.type.replace('_', ' ');
    if (amount !== undefined && amount !== null && amount > 0) {
      return `${label} ${amount}`;
    }
    return label;
  };
  const setActionAmount = (action: LegalAction, value: number) => {
    const min = action.min ?? 0;
    const max = action.max ?? value;
    const amount = Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : min;
    setActionAmounts(prev => ({ ...prev, [action.type]: amount }));
  };
  const formatTraceAction = (trace: any) => {
    const raw = String(trace?.chosen_action || trace?.parsed_action || '').trim();
    const parts = raw.split(/\s+/);
    const actionType = parts[0] || 'unknown';
    const amountMatch = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*BB/i);
    const amount = amountMatch ? ` ${Number(amountMatch[1]).toString()}BB` : '';
    const player = trace?.player_id || trace?.observation?.player_id || 'Unknown';
    const street = streetText[trace?.street] || trace?.street || '';
    const action = actionText[actionType] || actionType;
    const fallback = trace?.fallback_reason ? '（已兜底）' : '';
    return `🤠 ${player} ${action}${amount}${street ? ` · ${street}` : ''}${fallback}`;
  };

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>PokerAgentLab</h1>
          <p>多智能体德州扑克训练、决策追踪与自博弈评估平台。</p>
        </div>
        <div className="controls">
          <input value={sessionId} onChange={e => setSessionId(e.target.value)} />
          <input type="number" min={1} max={100} value={numHands} onChange={e => setNumHands(Number(e.target.value))} />
          <button onClick={startSession}>开始</button>
          <button onClick={() => refreshAll()}>刷新</button>
        </div>
      </header>

      <section className="grid">
        <section className="panel tablePanel">
          <div className="sectionHeader">
            <h2>牌桌</h2>
            <span>{statusText[state?.status || 'idle'] || state?.status || '未开始'}</span>
          </div>
          <div className="board">
            <div className="street">{streetText[state?.street || 'waiting'] || state?.street || '等待中'}</div>
            <div className="cards">{(state?.community_cards || []).map(card => <span key={card}>{card}</span>)}</div>
            <div className="pot">底池 {state?.pot_bb?.toFixed(1) || '0.0'} BB</div>
          </div>
          <div className="seats">
            {(state?.players || []).map(player => (
              <div className="seat" key={player.id}>
                <strong>{player.id}</strong>
                <span>{player.position || '座位'}</span>
                <span>{player.stack_bb.toFixed(1)} BB</span>
                {player.hole_cards && <small>{player.hole_cards.join(' ')}</small>}
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>行动面板</h2>
            <span>{state?.current_player_id || '-'}</span>
          </div>
          <div className="heroCards">{(state?.hole_cards || []).map(card => <span key={card}>{card}</span>)}</div>
          <div className="actions">
            {(state?.legal_actions || []).map(action => (
              <button disabled={!canAct} key={action.type} onClick={() => submitAction(action)}>
                {actionLabel(action, actionAmount(action))}
              </button>
            ))}
            {state && state.legal_actions.length === 0 && !state.hand_complete && <p>等待下一次决策...</p>}
          </div>
          {(state?.legal_actions || []).some(action => action.type === 'bet' || action.type === 'raise') && (
            <div className="amountControls">
              {(state?.legal_actions || [])
                .filter(action => action.type === 'bet' || action.type === 'raise')
                .map(action => (
                  <label key={action.type}>
                    <span>{actionText[action.type] || action.type}</span>
                    <input
                      type="number"
                      min={action.min ?? 0}
                      max={action.max ?? undefined}
                      step={0.5}
                      value={actionAmount(action)}
                      disabled={!canAct}
                      onChange={e => setActionAmount(action, Number(e.target.value))}
                    />
                    <small>{action.min ?? 0}-{action.max ?? '-' } BB</small>
                  </label>
                ))}
            </div>
          )}
          {state?.hand_complete && (
            <div className="actions">
              <button onClick={() => continueHand(true)}>下一手</button>
              <button onClick={() => continueHand(false)}>结束牌局</button>
            </div>
          )}
          <p className="message">{message}</p>
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>决策追踪</h2>
            <span>{traces.length} 条 trace</span>
          </div>
          {latestTrace ? (
            <>
              <div className="traceChips">
                <span>记忆 {latestTrace.retrieved_memory_ids?.length || 0}</span>
                <span>策略 {latestTrace.retrieved_strategy_chunk_ids?.length || 0}</span>
              </div>
              <div className="traceList">
                {traces.slice(-12).map((trace, index) => (
                  <div className="traceItem" key={`${trace.hand_id}-${index}`}>
                    {formatTraceAction(trace)}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p>暂无 trace。提交一次行动或等待 agent 行动后会显示。</p>
          )}
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>复盘分析</h2>
            <button onClick={loadAnalysis}>加载</button>
          </div>
          <p>总手数：{history?.total_hands ?? '-'}</p>
          <p>{coach?.key_findings?.[0] || '教练复盘会显示在这里。'}</p>
          <div className="actions">
            <button onClick={consolidateMemory}>沉淀记忆</button>
            <button onClick={loadMemoryContext}>记忆上下文</button>
          </div>
          <div className="sectionHeader">
            <h2>自博弈</h2>
            <button onClick={runSelfPlay}>运行 20 手</button>
          </div>
          {report && <pre>{JSON.stringify(report.summary, null, 2)}</pre>}
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>长期记忆</h2>
            <span>{memoryProfile?.total_memories ?? 0} 条记忆</span>
          </div>
          <p>已确认漏洞：{memoryProfile?.leaks?.length ?? 0}</p>
          <p>训练目标：{memoryProfile?.training_goals?.length ?? 0}</p>
          {(memoryProfile?.by_status?.candidate || []).slice(0, 4).map((memory: any) => (
            <div className="memoryItem" key={memory.id}>
              <strong>{categoryText[memory.category] || memory.category}</strong>
              <p>{memory.content}</p>
              <div className="actions">
                <button onClick={() => updateCandidate(memory.id, 'accept')}>确认</button>
                <button onClick={() => updateCandidate(memory.id, 'reject')}>拒绝</button>
              </div>
            </div>
          ))}
          {memoryContext && <pre>{JSON.stringify(memoryContext, null, 2)}</pre>}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
