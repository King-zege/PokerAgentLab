import { useCallback, useEffect, useRef, useState } from 'react';
import {
  consolidateSessionMemory,
  continueSession,
  createSession,
  getCoachReview,
  getMemoryContext,
  getMemoryProfile,
  getSessionHistory,
  getSessionState,
  getSessionTraces,
  requestOptional,
  runSelfPlayExperiment,
  submitSessionAction,
  updateMemoryCandidate,
} from './api/client';
import type {
  CoachResponse,
  DecisionTrace,
  GameState,
  HistoryResponse,
  LegalAction,
  MemoryContextResponse,
  MemoryProfile,
  SelfPlayResponse,
} from './api/types';
import { actionText } from './constants/labels';
import { useTraceStream } from './hooks/useTraceStream';
import { dedupeTraces, traceKey } from './utils/trace';
import { ActionPanel } from './components/ActionPanel';
import { AnalysisPanel } from './components/AnalysisPanel';
import { GameTable } from './components/GameTable';
import { MemoryPanel } from './components/MemoryPanel';
import { TracePanel } from './components/TracePanel';

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

function App() {
  const [sessionId, setSessionId] = useState(`demo_${Date.now().toString().slice(-5)}`);
  const [state, setState] = useState<GameState | null>(null);
  const [traces, setTraces] = useState<DecisionTrace[]>([]);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [coach, setCoach] = useState<CoachResponse | null>(null);
  const [report, setReport] = useState<SelfPlayResponse | null>(null);
  const [memoryProfile, setMemoryProfile] = useState<MemoryProfile | null>(null);
  const [memoryContext, setMemoryContext] = useState<MemoryContextResponse | null>(null);
  const [numHands, setNumHands] = useState(3);
  const [message, setMessage] = useState('就绪');
  const [actionAmounts, setActionAmounts] = useState<Record<string, number>>({});
  const seenTraceKeysRef = useRef<Set<string>>(new Set());

  const canAct = state?.status === 'waiting_for_action' && !state.hand_complete && state.legal_actions.length > 0;

  const appendTrace = useCallback((trace: DecisionTrace) => {
    const key = traceKey(trace, sessionId);
    if (seenTraceKeysRef.current.has(key)) return;
    seenTraceKeysRef.current.add(key);
    setTraces(prev => [...prev, trace]);
  }, [sessionId]);

  const { status: traceStreamStatus, close: closeTraceStream } = useTraceStream({
    sessionId: state?.session_id,
    onTrace: appendTrace,
  });

  function applyTraceSnapshot(nextTraces: DecisionTrace[]) {
    const { deduped, seen } = dedupeTraces(nextTraces, sessionId);
    seenTraceKeysRef.current = seen;
    setTraces(deduped);
  }

  useEffect(() => {
    if (!state) return;
    const timer = window.setInterval(() => {
      void refreshAll(state.session_id, { includeTraces: traceStreamStatus !== 'connected' });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [state?.session_id, traceStreamStatus]);

  async function startSession() {
    try {
      closeTraceStream();
      seenTraceKeysRef.current = new Set();
      setTraces([]);
      setState(null);
      setHistory(null);
      setCoach(null);
      setMemoryContext(null);
      setMessage('正在创建牌局...');
      await createSession(sessionId, numHands);
      await waitForState(sessionId, next => next.status === 'waiting_for_action' || next.status === 'completed' || next.status === 'error');
      setMessage('牌局运行中');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function refreshAll(id = sessionId, options: { includeTraces?: boolean } = {}) {
    try {
      const nextState = await getSessionState(id);
      setState(nextState);
      if (options.includeTraces ?? true) {
        const traceData = await requestOptional<Awaited<ReturnType<typeof getSessionTraces>>>(`/sessions/${id}/traces`);
        if (traceData) applyTraceSnapshot(traceData.traces || []);
      }
      const profileData = await requestOptional<MemoryProfile>('/memory/profile');
      if (profileData) setMemoryProfile(profileData);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function waitForState(id: string, done: (next: GameState) => boolean, attempts = 40) {
    let latest: GameState | null = null;
    for (let i = 0; i < attempts; i += 1) {
      const nextState = await getSessionState(id);
      latest = nextState;
      setState(nextState);
      if (traceStreamStatus !== 'connected') {
        const traceData = await requestOptional<Awaited<ReturnType<typeof getSessionTraces>>>(`/sessions/${id}/traces`);
        if (traceData) applyTraceSnapshot(traceData.traces || []);
      }
      if (done(nextState)) return nextState;
      await sleep(150);
    }
    return latest;
  }

  async function submitAction(action: LegalAction) {
    try {
      const amount = actionAmount(action);
      setMessage(`正在提交：${actionLabel(action, amount)}`);
      await submitSessionAction(sessionId, action, amount);
      await waitForState(sessionId, next => next.hand_complete || next.status === 'waiting_for_action' || next.status === 'completed' || next.status === 'error');
      setMessage('行动已提交');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function continueHand(continueGame: boolean) {
    try {
      setMessage(continueGame ? '正在进入下一手...' : '正在结束牌局...');
      await continueSession(sessionId, continueGame);
      if (continueGame) {
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
      setMessage('正在加载复盘...');
      const historyData = await requestOptional<HistoryResponse>(`/sessions/${sessionId}/history`);
      setHistory(historyData);
      const coachData = await getCoachReview(sessionId);
      setCoach(coachData);
      setMessage(`复盘反馈已加载：${coachData.total_hands ?? historyData?.total_hands ?? 0} 手`);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function consolidateMemory() {
    try {
      setMessage('正在沉淀记忆...');
      const data = await consolidateSessionMemory(sessionId);
      setMemoryProfile(await getMemoryProfile());
      setMessage(`生成了 ${data.candidate_memories.length} 条候选记忆`);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function loadMemoryContext() {
    try {
      const data = await getMemoryContext(sessionId);
      setMemoryContext(data);
      setMessage('记忆上下文已加载');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function updateCandidate(memoryId: string, action: 'accept' | 'reject') {
    try {
      await updateMemoryCandidate(memoryId, action);
      setMemoryProfile(await getMemoryProfile());
      setMessage(action === 'accept' ? '记忆已确认' : '记忆已拒绝');
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function runSelfPlay(selfPlayHands: number, seed: number) {
    try {
      setMessage(`正在运行自博弈实验：${selfPlayHands} 手，seed=${seed}`);
      const data = await runSelfPlayExperiment(selfPlayHands, seed);
      setReport(data);
      setMessage(`实验 ${data.experiment_id} 已完成`);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

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
          <button onClick={() => void refreshAll()}>刷新</button>
        </div>
      </header>

      <section className="grid">
        <GameTable state={state} />
        <ActionPanel
          state={state}
          canAct={canAct}
          message={message}
          actionAmount={actionAmount}
          actionLabel={actionLabel}
          setActionAmount={setActionAmount}
          onSubmitAction={submitAction}
          onContinue={continueHand}
        />
        <TracePanel traces={traces} streamStatus={traceStreamStatus} />
        <AnalysisPanel
          history={history}
          coach={coach}
          report={report}
          onLoadAnalysis={loadAnalysis}
          onConsolidateMemory={consolidateMemory}
          onLoadMemoryContext={loadMemoryContext}
          onRunSelfPlay={runSelfPlay}
        />
        <MemoryPanel
          memoryProfile={memoryProfile}
          memoryContext={memoryContext}
          onUpdateCandidate={updateCandidate}
        />
      </section>
    </main>
  );
}

export default App;
