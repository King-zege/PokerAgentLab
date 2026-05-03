import type {
  CoachResponse,
  ConsolidateResponse,
  GameState,
  HistoryResponse,
  LegalAction,
  MemoryContextResponse,
  MemoryProfile,
  SelfPlayResponse,
  TraceListResponse,
} from './types';

const API = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || res.statusText);
  }
  return res.json() as Promise<T>;
}

export async function requestOptional<T>(path: string, options?: RequestInit): Promise<T | null> {
  try {
    return await request<T>(path, options);
  } catch {
    return null;
  }
}

export function createSession(sessionId: string, numHands: number) {
  return request('/sessions', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      mode: 'fixed',
      num_hands: numHands,
      config_path: 'config/game_config.yaml',
    }),
  });
}

export function getSessionState(sessionId: string) {
  return request<GameState>(`/sessions/${sessionId}/state`);
}

export function submitSessionAction(sessionId: string, action: LegalAction, amount: number) {
  return request(`/sessions/${sessionId}/action`, {
    method: 'POST',
    body: JSON.stringify({ action: action.type, amount }),
  });
}

export function continueSession(sessionId: string, continueGame: boolean) {
  return request(`/sessions/${sessionId}/continue`, {
    method: 'POST',
    body: JSON.stringify({ continue_game: continueGame }),
  });
}

export function getSessionTraces(sessionId: string) {
  return request<TraceListResponse>(`/sessions/${sessionId}/traces`);
}

export function getSessionHistory(sessionId: string) {
  return request<HistoryResponse>(`/sessions/${sessionId}/history`);
}

export function getCoachReview(sessionId: string) {
  return request<CoachResponse>(`/sessions/${sessionId}/coach`, { method: 'POST' });
}

export function consolidateSessionMemory(sessionId: string) {
  return request<ConsolidateResponse>(`/sessions/${sessionId}/consolidate`, { method: 'POST' });
}

export function getMemoryContext(sessionId: string) {
  return request<MemoryContextResponse>(`/sessions/${sessionId}/memory-context`);
}

export function getMemoryProfile() {
  return request<MemoryProfile>('/memory/profile');
}

export function updateMemoryCandidate(memoryId: string, action: 'accept' | 'reject') {
  return request(`/memory/profile/candidates/${memoryId}/${action}`, { method: 'POST' });
}

export function runSelfPlayExperiment() {
  return request<SelfPlayResponse>('/experiments/self-play', {
    method: 'POST',
    body: JSON.stringify({ num_hands: 20, seed: 42 }),
  });
}

export const traceStreamUrl = (sessionId: string) =>
  `${API}/sessions/${encodeURIComponent(sessionId)}/trace-stream`;
