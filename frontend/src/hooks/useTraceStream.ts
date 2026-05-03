import { useEffect, useRef, useState } from 'react';
import { traceStreamUrl } from '../api/client';
import type { DecisionTrace, TraceStreamStatus } from '../api/types';

type UseTraceStreamOptions = {
  sessionId?: string;
  onTrace: (trace: DecisionTrace) => void;
};

export function useTraceStream({ sessionId, onTrace }: UseTraceStreamOptions) {
  const [status, setStatus] = useState<TraceStreamStatus>('idle');
  const streamRef = useRef<EventSource | null>(null);
  const onTraceRef = useRef(onTrace);

  useEffect(() => {
    onTraceRef.current = onTrace;
  }, [onTrace]);

  useEffect(() => {
    if (!sessionId) return undefined;
    if (streamRef.current?.url.includes(`/sessions/${encodeURIComponent(sessionId)}/trace-stream`)) {
      return undefined;
    }

    close();
    const stream = new EventSource(traceStreamUrl(sessionId));
    streamRef.current = stream;
    setStatus('idle');

    stream.onopen = () => setStatus('connected');
    stream.addEventListener('trace', event => {
      try {
        onTraceRef.current(JSON.parse((event as MessageEvent).data) as DecisionTrace);
      } catch {
        setStatus('fallback');
      }
    });
    stream.onerror = () => setStatus('fallback');

    return () => close();
  }, [sessionId]);

  function close() {
    streamRef.current?.close();
    streamRef.current = null;
  }

  return { status, close };
}
