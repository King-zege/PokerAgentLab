import { categoryText } from '../constants/labels';
import type { MemoryContextResponse, MemoryProfile } from '../api/types';

type MemoryPanelProps = {
  memoryProfile: MemoryProfile | null;
  memoryContext: MemoryContextResponse | null;
  onUpdateCandidate: (memoryId: string, action: 'accept' | 'reject') => void;
};

export function MemoryPanel({ memoryProfile, memoryContext, onUpdateCandidate }: MemoryPanelProps) {
  const candidates = memoryProfile?.by_status?.candidate || [];

  return (
    <section className="panel">
      <div className="sectionHeader">
        <h2>长期记忆</h2>
        <span>{memoryProfile?.total_memories ?? 0} 条记忆</span>
      </div>
      <p>已确认漏洞：{memoryProfile?.leaks?.length ?? 0}</p>
      <p>训练目标：{memoryProfile?.training_goals?.length ?? 0}</p>
      {candidates.slice(0, 4).map(memory => (
        <div className="memoryItem" key={memory.id}>
          <strong>{categoryText[memory.category] || memory.category}</strong>
          <p>{memory.content}</p>
          <div className="actions">
            <button onClick={() => onUpdateCandidate(memory.id, 'accept')}>确认</button>
            <button onClick={() => onUpdateCandidate(memory.id, 'reject')}>拒绝</button>
          </div>
        </div>
      ))}
      {memoryContext && <pre>{JSON.stringify(memoryContext, null, 2)}</pre>}
    </section>
  );
}
