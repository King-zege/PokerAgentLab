import type { SelfPlayResponse } from '../api/types';

type SelfPlayPanelProps = {
  report: SelfPlayResponse | null;
  onRunSelfPlay: () => void;
};

export function SelfPlayPanel({ report, onRunSelfPlay }: SelfPlayPanelProps) {
  return (
    <>
      <div className="sectionHeader">
        <h2>自博弈</h2>
        <button onClick={onRunSelfPlay}>运行 20 手</button>
      </div>
      {report && <pre>{JSON.stringify(report.summary, null, 2)}</pre>}
    </>
  );
}
