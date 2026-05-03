import { traceStreamText } from '../constants/labels';
import type { DecisionTrace, TraceStreamStatus } from '../api/types';
import { formatTraceAction } from '../utils/trace';

type TracePanelProps = {
  traces: DecisionTrace[];
  streamStatus: TraceStreamStatus;
};

export function TracePanel({ traces, streamStatus }: TracePanelProps) {
  const latestTrace = traces[traces.length - 1];

  return (
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
            <span>{traceStreamText[streamStatus]}</span>
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
  );
}
