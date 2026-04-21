export const GAP_STATUS_COLOR: Record<string, string> = {
  '达标': 'green',
  '接近达标': 'blue',
  '明显Gap': 'red',
};

export const SEVERITY_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
};

export const SEVERITY_LABEL: Record<string, string> = {
  high: '高优先级',
  medium: '中优先级',
  low: '低优先级',
};

export const PHASE_COLORS = ['#1677ff', '#722ed1', '#52c41a'];
export const PHASE_BG = [
  'bg-blue-50 dark:bg-blue-900/20',
  'bg-purple-50 dark:bg-purple-900/20',
  'bg-green-50 dark:bg-green-900/20',
];
export const PHASE_BORDER = [
  'border-blue-200 dark:border-blue-800',
  'border-purple-200 dark:border-purple-800',
  'border-green-200 dark:border-green-800',
];
export const PHASE_TEXT = ['text-blue-700', 'text-purple-700', 'text-green-700'];

export const VERDICT_COLOR: Record<string, string> = {
  '高度匹配': '#52c41a',
  '中高匹配': '#1677ff',
  '潜力匹配': '#fa8c16',
  '不建议': '#ff4d4f',
};

export const IMPACT_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'default',
};
