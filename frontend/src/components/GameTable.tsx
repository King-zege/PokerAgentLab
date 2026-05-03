import { statusText, streetText } from '../constants/labels';
import type { GameState } from '../api/types';

type GameTableProps = {
  state: GameState | null;
};

export function GameTable({ state }: GameTableProps) {
  return (
    <section className="panel tablePanel">
      <div className="sectionHeader">
        <h2>牌桌</h2>
        <span>{statusText[state?.status || 'idle'] || state?.status || '未开始'}</span>
      </div>
      <div className="board">
        <div className="street">{streetText[state?.street || 'waiting'] || state?.street || '等待中'}</div>
        <div className="cards">{(state?.community_cards || []).map(card => <span key={card}>{card}</span>)}</div>
        <div className="pot">底池 {state?.pot_bb?.toFixed(1) || '0.0'} BB</div>
      </div>
      <div className="seats">
        {(state?.players || []).map(player => (
          <div className="seat" key={player.id}>
            <strong>{player.id}</strong>
            <span>{player.position || '座位'}</span>
            <span>{player.stack_bb.toFixed(1)} BB</span>
            {player.hole_cards && <small>{player.hole_cards.join(' ')}</small>}
          </div>
        ))}
      </div>
    </section>
  );
}
