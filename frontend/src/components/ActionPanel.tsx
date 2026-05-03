import { actionText } from '../constants/labels';
import type { GameState, LegalAction } from '../api/types';

type ActionPanelProps = {
  state: GameState | null;
  canAct: boolean;
  message: string;
  actionAmount: (action: LegalAction) => number;
  actionLabel: (action: LegalAction, overrideAmount?: number) => string;
  setActionAmount: (action: LegalAction, value: number) => void;
  onSubmitAction: (action: LegalAction) => void;
  onContinue: (continueGame: boolean) => void;
};

export function ActionPanel({
  state,
  canAct,
  message,
  actionAmount,
  actionLabel,
  setActionAmount,
  onSubmitAction,
  onContinue,
}: ActionPanelProps) {
  const legalActions = state?.legal_actions || [];
  const amountActions = legalActions.filter(action => action.type === 'bet' || action.type === 'raise');

  return (
    <section className="panel">
      <div className="sectionHeader">
        <h2>行动面板</h2>
        <span>{state?.current_player_id || '-'}</span>
      </div>
      <div className="heroCards">{(state?.hole_cards || []).map(card => <span key={card}>{card}</span>)}</div>
      <div className="actions">
        {legalActions.map(action => (
          <button disabled={!canAct} key={action.type} onClick={() => onSubmitAction(action)}>
            {actionLabel(action, actionAmount(action))}
          </button>
        ))}
        {state && legalActions.length === 0 && !state.hand_complete && <p>等待下一次决策...</p>}
      </div>
      {amountActions.length > 0 && (
        <div className="amountControls">
          {amountActions.map(action => (
            <label key={action.type}>
              <span>{actionText[action.type] || action.type}</span>
              <input
                type="number"
                min={action.min ?? 0}
                max={action.max ?? undefined}
                step={0.5}
                value={actionAmount(action)}
                disabled={!canAct}
                onChange={e => setActionAmount(action, Number(e.target.value))}
              />
              <small>{action.min ?? 0}-{action.max ?? '-'} BB</small>
            </label>
          ))}
        </div>
      )}
      {state?.hand_complete && (
        <div className="actions">
          <button onClick={() => onContinue(true)}>下一手</button>
          <button onClick={() => onContinue(false)}>结束牌局</button>
        </div>
      )}
      <p className="message">{message}</p>
    </section>
  );
}
