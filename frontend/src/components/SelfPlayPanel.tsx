import { useMemo, useState } from 'react';
import type { SelfPlayPlayerSummary, SelfPlayResponse } from '../api/types';

type SelfPlayPanelProps = {
  report: SelfPlayResponse | null;
  onRunSelfPlay: (numHands: number, seed: number) => void;
};

const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
const bb = (value: number) => `${value.toFixed(2)} BB`;

const actionLabel: Record<string, string> = {
  fold: '弃牌',
  check: '过牌',
  call: '跟注',
  bet: '下注',
  raise: '加注',
  all_in: '全下',
  unknown: '未知',
};

export function SelfPlayPanel({ report, onRunSelfPlay }: SelfPlayPanelProps) {
  const [numHands, setNumHands] = useState(20);
  const [seed, setSeed] = useState(42);

  const rows = useMemo(() => {
    if (!report) return [];
    return Object.entries(report.summary).map(([playerId, summary]) => ({ playerId, summary }));
  }, [report]);

  const bestByBb100 = rows.reduce<{ playerId: string; summary: SelfPlayPlayerSummary } | null>((best, row) => {
    if (!best || row.summary.bb_per_100 > best.summary.bb_per_100) return row;
    return best;
  }, null);
  const totalHands = rows[0]?.summary.hands ?? report?.num_hands ?? 0;
  const avgVpip = rows.length ? rows.reduce((sum, row) => sum + row.summary.vpip, 0) / rows.length : 0;
  const totalActions = rows.reduce(
    (sum, row) => sum + Object.values(row.summary.action_distribution || {}).reduce((a, b) => a + b, 0),
    0,
  );

  return (
    <div className="selfPlayReport">
      <div className="sectionHeader">
        <h2>自博弈</h2>
        <button onClick={() => onRunSelfPlay(numHands, seed)}>运行</button>
      </div>
      <div className="selfPlayControls">
        <label>
          <span>手数</span>
          <input
            type="number"
            min={1}
            max={10000}
            value={numHands}
            onChange={e => setNumHands(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <label>
          <span>Seed</span>
          <input
            type="number"
            value={seed}
            onChange={e => setSeed(Number(e.target.value) || 0)}
          />
        </label>
      </div>

      {report ? (
        <>
          <div className="reportHero">
            <strong>Self-play 报告：{report.experiment_id}</strong>
            <p>JSON：{report.report_path}</p>
            <p>Markdown：{report.markdown_path}</p>
          </div>

          <div className="metricGrid">
            <div>
              <span>总手数</span>
              <strong>{totalHands}</strong>
            </div>
            <div>
              <span>最佳 BB/100</span>
              <strong>{bestByBb100 ? `${bestByBb100.playerId} ${bestByBb100.summary.bb_per_100}` : '-'}</strong>
            </div>
            <div>
              <span>平均 VPIP</span>
              <strong>{pct(avgVpip)}</strong>
            </div>
            <div>
              <span>动作样本</span>
              <strong>{totalActions}</strong>
            </div>
          </div>

          <div className="selfPlayTableWrap">
            <table className="selfPlayTable">
              <thead>
                <tr>
                  <th>玩家</th>
                  <th>胜局</th>
                  <th>胜率</th>
                  <th>盈亏</th>
                  <th>BB/100</th>
                  <th>VPIP</th>
                  <th>PFR</th>
                  <th>AF</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ playerId, summary }) => (
                  <tr key={playerId}>
                    <td>{playerId}</td>
                    <td>{summary.wins}</td>
                    <td>{pct(summary.win_rate)}</td>
                    <td>{bb(summary.profit_bb)}</td>
                    <td>{summary.bb_per_100.toFixed(2)}</td>
                    <td>{pct(summary.vpip)}</td>
                    <td>{pct(summary.pfr)}</td>
                    <td>{summary.aggression_factor.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="reportSection">
            <strong>动作分布</strong>
            <div className="actionDistribution">
              {rows.map(({ playerId, summary }) => {
                const actions = Object.entries(summary.action_distribution || {});
                const maxActionCount = Math.max(1, ...actions.map(([, count]) => count));
                return (
                  <div className="actionGroup" key={playerId}>
                    <span>{playerId}</span>
                    {actions.length ? actions.map(([action, count]) => (
                      <div className="actionBar" key={`${playerId}-${action}`}>
                        <small>{actionLabel[action] || action}</small>
                        <div className="barTrack">
                          <div className="barFill" style={{ width: `${Math.max(4, (count / maxActionCount) * 100)}%` }} />
                        </div>
                        <small>{count}</small>
                      </div>
                    )) : <p>暂无动作样本。</p>}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      ) : (
        <p>设置手数和 seed 后运行自博弈，这里会展示胜率、BB/100、VPIP、PFR、AF 和动作分布。</p>
      )}
    </div>
  );
}
