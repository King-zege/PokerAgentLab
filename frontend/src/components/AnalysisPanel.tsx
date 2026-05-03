import type {
  CoachResponse,
  HistoryResponse,
  ProfileItem,
  SelfPlayResponse,
} from '../api/types';
import { SelfPlayPanel } from './SelfPlayPanel';

type AnalysisPanelProps = {
  history: HistoryResponse | null;
  coach: CoachResponse | null;
  report: SelfPlayResponse | null;
  onLoadAnalysis: () => void;
  onConsolidateMemory: () => void;
  onLoadMemoryContext: () => void;
  onRunSelfPlay: () => void;
};

function ProfileBars({ items }: { items: ProfileItem[] }) {
  if (!items.length) {
    return <p>暂无足够样本。</p>;
  }
  return (
    <div className="profileBars">
      {items.map(item => (
        <div className="profileBar" key={item.key}>
          <div>
            <strong>{item.label}</strong>
            <span>{item.count} 次 · {item.percentage}%</span>
          </div>
          <div className="barTrack">
            <div className="barFill" style={{ width: `${Math.min(100, item.percentage)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function AnalysisPanel({
  history,
  coach,
  report,
  onLoadAnalysis,
  onConsolidateMemory,
  onLoadMemoryContext,
  onRunSelfPlay,
}: AnalysisPanelProps) {
  const totalHands = history?.total_hands ?? coach?.total_hands ?? '-';
  const summary = coach?.summary;

  return (
    <section className="panel">
      <div className="sectionHeader">
        <h2>训练报告</h2>
        <button onClick={onLoadAnalysis}>加载</button>
      </div>
      <p>总手数：{totalHands}</p>
      {coach ? (
        <div className="reviewBlock trainingReport">
          <div className="reportHero">
            <strong>{coach.report_title || 'Poker Agent Lab 训练报告'}</strong>
            <p>{summary?.sample_note || '训练报告已生成。'}</p>
            <div className="reportStats">
              <span>关注玩家：{coach.focus_player_id || summary?.focus_player_id || '全部玩家'}</span>
              <span>决策数：{summary?.total_actions ?? 0}</span>
              <span>摊牌：{summary?.showdown_hands ?? 0}</span>
              <span>偏离点：{summary?.deviation_count ?? 0}</span>
              <span>主模式：{summary?.main_pattern || '暂无'}</span>
              <span>净变化：{summary?.net_stack_change_bb ?? 0}BB</span>
            </div>
          </div>

          <div className="reportSection">
            <strong>关键发现</strong>
            {(coach.key_findings || []).map((finding, index) => (
              <p key={`finding-${index}`}>{finding}</p>
            ))}
          </div>

          <div className="reportGrid">
            <div className="reportSection">
              <strong>动作画像</strong>
              <ProfileBars items={coach.action_profile || []} />
            </div>
            <div className="reportSection">
              <strong>街道画像</strong>
              <ProfileBars items={coach.street_profile || []} />
            </div>
          </div>

          <div className="reportSection">
            <strong>漏洞候选</strong>
            {(coach.leak_candidates || []).length ? (
              coach.leak_candidates.map((leak, index) => (
                <div className="reviewItem" key={`leak-${index}`}>
                  <span>{leak.title} · {leak.severity}</span>
                  <p>{leak.evidence}</p>
                  <p>{leak.recommendation}</p>
                </div>
              ))
            ) : (
              <p>暂未发现稳定漏洞候选，建议继续扩大样本。</p>
            )}
          </div>

          <div className="reportSection">
            <strong>关键决策点</strong>
            {(coach.critical_spots || []).length ? (
              coach.critical_spots.map((spot, index) => (
                <div className="reviewItem" key={`${spot.hand_id}-${index}`}>
                  <span>{spot.hand_id} · {spot.street_label || spot.street} · {spot.player_id}</span>
                  <p>实际动作：{spot.action_taken}</p>
                  <p>问题：{spot.issue}</p>
                  {spot.suggested_action && <p>建议动作：{spot.suggested_action}</p>}
                  {spot.reason && <p>原因：{spot.reason}</p>}
                </div>
              ))
            ) : (
              <p>没有检测到明显风格偏离。样本不足时仍建议继续采样。</p>
            )}
          </div>

          <div className="reportSection">
            <strong>训练计划</strong>
            {(coach.training_plan || []).map(item => (
              <div className="reviewItem" key={`plan-${item.step}`}>
                <span>第 {item.step} 步 · {item.title}</span>
                <p>{item.description}</p>
                <p>验收标准：{item.success_metric}</p>
              </div>
            ))}
          </div>

          <div className="reportSection">
            <strong>下一阶段训练任务</strong>
            <p>{coach.next_drill?.title || '继续完成更多手牌样本。'}</p>
            <p>建议手数：{coach.next_drill?.hands ?? 10}</p>
            <p>训练重点：{coach.next_drill?.focus || '观察动作分布和关键决策解释。'}</p>
          </div>
        </div>
      ) : (
        <p>完成至少一手牌后点击“加载”，这里会生成结构化训练报告。</p>
      )}
      <div className="actions">
        <button onClick={onConsolidateMemory}>沉淀记忆</button>
        <button onClick={onLoadMemoryContext}>记忆上下文</button>
      </div>
      <SelfPlayPanel report={report} onRunSelfPlay={onRunSelfPlay} />
    </section>
  );
}
