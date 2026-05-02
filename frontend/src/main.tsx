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

function App() {
  const [sessionId, setSessionId] = useState(`demo_${Date.now().toString().slice(-5)}`);
  const [state, setState] = useState<GameState | null>(null);
  const [traces, setTraces] = useState<any[]>([]);
  const [history, setHistory] = useState<any | null>(null);
  const [coach, setCoach] = useState<any | null>(null);
  const [report, setReport] = useState<any | null>(null);
  const [numHands, setNumHands] = useState(3);
  const [message, setMessage] = useState('Ready');

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

  async function startSession() {
    setMessage('Starting session...');
    await request('/sessions', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, mode: 'fixed', num_hands: numHands, config_path: 'config/game_config.yaml' }),
    });
    await refreshAll(sessionId);
    setMessage('Session running');
  }

  async function refreshAll(id = sessionId) {
    try {
      const nextState = await request(`/sessions/${id}/state`);
      setState(nextState);
      const traceData = await request(`/sessions/${id}/traces`);
      setTraces(traceData.traces || []);
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function submitAction(action: LegalAction) {
    const amount = action.min ?? action.amount ?? 0;
    setMessage(`Submitting ${action.type}`);
    await request(`/sessions/${sessionId}/action`, {
      method: 'POST',
      body: JSON.stringify({ action: action.type, amount }),
    });
    await refreshAll();
  }

  async function continueHand(continue_game: boolean) {
    await request(`/sessions/${sessionId}/continue`, {
      method: 'POST',
      body: JSON.stringify({ continue_game }),
    });
    await refreshAll();
  }

  async function loadAnalysis() {
    const historyData = await request(`/sessions/${sessionId}/history`);
    setHistory(historyData);
    const coachData = await request(`/sessions/${sessionId}/coach`, { method: 'POST' });
    setCoach(coachData);
  }

  async function runSelfPlay() {
    setMessage('Running self-play experiment...');
    const data = await request('/experiments/self-play', {
      method: 'POST',
      body: JSON.stringify({ num_hands: 20, seed: 42 }),
    });
    setReport(data);
    setMessage(`Experiment ${data.experiment_id} completed`);
  }

  const latestTrace = useMemo(() => traces[traces.length - 1], [traces]);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>PokerAgentLab</h1>
          <p>Multi-agent poker training, tracing, and self-play evaluation.</p>
        </div>
        <div className="controls">
          <input value={sessionId} onChange={e => setSessionId(e.target.value)} />
          <input type="number" min={1} max={100} value={numHands} onChange={e => setNumHands(Number(e.target.value))} />
          <button onClick={startSession}>Start</button>
          <button onClick={() => refreshAll()}>Refresh</button>
        </div>
      </header>

      <section className="grid">
        <section className="panel tablePanel">
          <div className="sectionHeader">
            <h2>GamePage</h2>
            <span>{state?.status || 'idle'}</span>
          </div>
          <div className="board">
            <div className="street">{state?.street || 'waiting'}</div>
            <div className="cards">{(state?.community_cards || []).map(card => <span key={card}>{card}</span>)}</div>
            <div className="pot">Pot {state?.pot_bb?.toFixed(1) || '0.0'} BB</div>
          </div>
          <div className="seats">
            {(state?.players || []).map(player => (
              <div className="seat" key={player.id}>
                <strong>{player.id}</strong>
                <span>{player.position || 'seat'}</span>
                <span>{player.stack_bb.toFixed(1)} BB</span>
                {player.hole_cards && <small>{player.hole_cards.join(' ')}</small>}
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>ActionPanel</h2>
            <span>{state?.current_player_id || '-'}</span>
          </div>
          <div className="heroCards">{(state?.hole_cards || []).map(card => <span key={card}>{card}</span>)}</div>
          <div className="actions">
            {(state?.legal_actions || []).map(action => (
              <button disabled={!canAct} key={action.type} onClick={() => submitAction(action)}>
                {action.type}{action.min ? ` ${action.min}` : ''}
              </button>
            ))}
          </div>
          {state?.hand_complete && (
            <div className="actions">
              <button onClick={() => continueHand(true)}>Next Hand</button>
              <button onClick={() => continueHand(false)}>End Session</button>
            </div>
          )}
          <p className="message">{message}</p>
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>AgentTracePanel</h2>
            <span>{traces.length} traces</span>
          </div>
          {latestTrace ? (
            <pre>{JSON.stringify(latestTrace, null, 2)}</pre>
          ) : (
            <p>No trace yet. Submit an action or let agents act.</p>
          )}
        </section>

        <section className="panel">
          <div className="sectionHeader">
            <h2>AnalysisPage</h2>
            <button onClick={loadAnalysis}>Load</button>
          </div>
          <p>Total hands: {history?.total_hands ?? '-'}</p>
          <p>{coach?.key_findings?.[0] || 'Coach feedback will appear here.'}</p>
          <div className="sectionHeader">
            <h2>Self-Play</h2>
            <button onClick={runSelfPlay}>Run 20 Hands</button>
          </div>
          {report && <pre>{JSON.stringify(report.summary, null, 2)}</pre>}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
