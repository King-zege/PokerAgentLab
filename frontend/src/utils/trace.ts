import { actionText, streetText } from '../constants/labels';
import type { DecisionTrace } from '../api/types';

export function traceKey(trace: DecisionTrace, fallbackSessionId = '') {
  return [
    trace.session_id || fallbackSessionId,
    trace.hand_id || '',
    trace.player_id || '',
    trace.timestamp || '',
    trace.chosen_action || trace.parsed_action || '',
  ].join('|');
}

export function dedupeTraces(traces: DecisionTrace[], fallbackSessionId = '') {
  const seen = new Set<string>();
  const deduped: DecisionTrace[] = [];
  for (const trace of traces) {
    const key = traceKey(trace, fallbackSessionId);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(trace);
  }
  return { deduped, seen };
}

export function formatTraceAction(trace: DecisionTrace) {
  const raw = String(trace.chosen_action || trace.parsed_action || '').trim();
  const parts = raw.split(/\s+/);
  const actionType = parts[0] || 'unknown';
  const amountMatch = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*BB/i);
  const amount = amountMatch ? ` ${Number(amountMatch[1]).toString()}BB` : '';
  const player = trace.player_id || trace.observation?.player_id || 'Unknown';
  const street = streetText[trace.street || ''] || trace.street || '';
  const action = actionText[actionType] || actionType;
  const fallback = trace.fallback_reason ? '（已兜底）' : '';
  return `🤠 ${player} ${action}${amount}${street ? ` · ${street}` : ''}${fallback}`;
}
