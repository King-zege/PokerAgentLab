export type ActionType = 'fold' | 'check' | 'call' | 'bet' | 'raise' | 'all_in' | string;

export type LegalAction = {
  type: ActionType;
  min?: number;
  max?: number;
  amount?: number;
};

export type PlayerState = {
  id: string;
  stack_bb: number;
  position: string;
  hole_cards?: string[];
};

export type GameState = {
  session_id: string;
  status: string;
  current_hand: number;
  street?: string;
  pot_bb: number;
  community_cards: string[];
  current_player_id?: string;
  hole_cards: string[];
  legal_actions: LegalAction[];
  players: PlayerState[];
  hand_complete: boolean;
  can_continue: boolean;
  last_hand_result?: Record<string, unknown> | null;
  error?: string | null;
};

export type DecisionTrace = {
  session_id?: string;
  hand_id?: string;
  street?: string;
  player_id?: string;
  observation?: { player_id?: string };
  chosen_action?: string;
  parsed_action?: string;
  fallback_reason?: string;
  retrieved_memory_ids?: string[];
  retrieved_strategy_chunk_ids?: string[];
  timestamp?: string;
};

export type TraceListResponse = {
  session_id: string;
  total_traces: number;
  traces: DecisionTrace[];
};

export type HistoryResponse = {
  session_id: string;
  total_hands: number;
  hands: unknown[];
  player_stats: Record<string, unknown>;
};

export type CoachHandReview = {
  hand_id: string;
  critical_decisions: CriticalSpot[];
  overall_notes: string[];
  action_count?: number;
  showdown?: boolean;
  board?: string[];
  final_stacks?: Record<string, number>;
};

export type CoachSummary = {
  focus_player_id?: string;
  total_hands?: number;
  total_actions?: number;
  showdown_hands?: number;
  deviation_count?: number;
  main_pattern?: string;
  net_stack_change_bb?: number;
  sample_note?: string;
};

export type ProfileItem = {
  key: string;
  label: string;
  count: number;
  percentage: number;
};

export type LeakCandidate = {
  title: string;
  severity: string;
  evidence: string;
  recommendation: string;
};

export type CriticalSpot = {
  hand_id: string;
  street: string;
  street_label?: string;
  player_id: string;
  action_taken: string;
  issue: string;
  suggested_action?: string;
  reason?: string;
  board?: string[];
};

export type TrainingPlanItem = {
  step: number;
  title: string;
  description: string;
  success_metric: string;
};

export type NextDrill = {
  title?: string;
  hands?: number;
  focus?: string;
};

export type CoachResponse = {
  session_id: string;
  report_title: string;
  focus_player_id?: string | null;
  total_hands: number;
  summary: CoachSummary;
  action_profile: ProfileItem[];
  street_profile: ProfileItem[];
  leak_candidates: LeakCandidate[];
  critical_spots: CriticalSpot[];
  training_plan: TrainingPlanItem[];
  next_drill: NextDrill;
  key_findings: string[];
  training_goals: string[];
  hand_reviews: CoachHandReview[];
  llm_coach_summary?: string;
  personalized_feedback?: string[];
  memory_references?: Record<string, unknown>;
  llm_coach_fallback_reason?: string;
};

export type MemoryCandidate = {
  id: string;
  category: string;
  content: string;
  status: string;
  confidence?: number;
};

export type MemoryProfile = {
  total_memories: number;
  leaks?: string[];
  training_goals?: string[];
  by_status?: {
    candidate?: MemoryCandidate[];
    accepted?: MemoryCandidate[];
    rejected?: MemoryCandidate[];
    archived?: MemoryCandidate[];
  };
};

export type MemoryContextResponse = Record<string, unknown>;

export type ActionDistribution = Record<string, number>;

export type SelfPlayPlayerSummary = {
  hands: number;
  wins: number;
  win_rate: number;
  profit_bb: number;
  bb_per_100: number;
  vpip: number;
  pfr: number;
  aggression_factor: number;
  action_distribution: ActionDistribution;
};

export type SelfPlaySummary = Record<string, SelfPlayPlayerSummary>;

export type SelfPlayResponse = {
  experiment_id: string;
  num_hands: number;
  seed?: number | null;
  report_path: string;
  markdown_path: string;
  summary: SelfPlaySummary;
};

export type ConsolidateResponse = {
  session_id: string;
  candidate_memories: MemoryCandidate[];
  session_summary: Record<string, unknown>;
  training_plan: string[];
};

export type TraceStreamStatus = 'idle' | 'connected' | 'fallback';
