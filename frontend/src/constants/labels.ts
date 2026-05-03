export const statusText: Record<string, string> = {
  idle: '未开始',
  created: '已创建',
  running: '运行中',
  waiting_for_action: '等待行动',
  completed: '已完成',
  error: '错误',
};

export const streetText: Record<string, string> = {
  waiting: '等待中',
  preflop: '翻前',
  flop: '翻牌',
  turn: '转牌',
  river: '河牌',
};

export const actionText: Record<string, string> = {
  fold: '弃牌',
  check: '过牌',
  call: '跟注',
  bet: '下注',
  raise: '加注',
  all_in: '全下',
};

export const categoryText: Record<string, string> = {
  preferences: '偏好',
  leaks: '漏洞',
  goals: '训练目标',
  knowledge_state: '知识状态',
};

export const traceStreamText = {
  idle: '实时追踪连接中',
  connected: '实时追踪已连接',
  fallback: '实时追踪断开，已回退到刷新模式',
};
