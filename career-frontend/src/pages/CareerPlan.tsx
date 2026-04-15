import React, { useEffect, useState } from 'react';
import { Button, Spin, Alert, Steps, Tag, Collapse, Divider } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store/appStore';
import CareerMatchSkeleton from '@/components/skeletons/CareerMatchSkeleton';
import { api } from '@/api/client';
import type {
  CareerRecommendation,
  CareerPlanBlocks,
  MatchOverviewBlock,
  JdRecommendationsBlock,
  GapAnalysisBlock,
  ActionPlanBlock,
  KeyFactor,
  JDPosition,
  GapItem,
  StrengthItem,
} from '@/types';

const { Panel } = Collapse;

// ── 工具函数 ──────────────────────────────────────────────────────────

function scrollTo(id: string) {
  document.getElementById(`plan-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

const GAP_STATUS_COLOR: Record<string, string> = {
  '达标': 'green',
  '接近达标': 'blue',
  '明显Gap': 'red',
};

const SEVERITY_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
};

const SEVERITY_LABEL: Record<string, string> = {
  high: '高优先级',
  medium: '中优先级',
  low: '低优先级',
};

const PHASE_COLORS = ['#1677ff', '#722ed1', '#52c41a'];
const PHASE_BG = ['bg-blue-50 dark:bg-blue-900/20', 'bg-purple-50 dark:bg-purple-900/20', 'bg-green-50 dark:bg-green-900/20'];
const PHASE_BORDER = ['border-blue-200 dark:border-blue-800', 'border-purple-200 dark:border-purple-800', 'border-green-200 dark:border-green-800'];
const PHASE_TEXT = ['text-blue-700', 'text-purple-700', 'text-green-700'];

const VERDICT_COLOR: Record<string, string> = {
  '高度匹配': '#52c41a',
  '中高匹配': '#1677ff',
  '潜力匹配': '#fa8c16',
  '不建议': '#ff4d4f',
};

const IMPACT_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'default',
};

const PLAN_STEPS = [
  '分析综合匹配度（Block 1）',
  '生成高匹配岗位推荐（Block 2）',
  '分析差距与优势（Block 3）',
  '生成分阶段行动计划（Block 4）',
];

// ── CareerCard ───────────────────────────────────────────────────────

interface PlannedInfo {
  onetsoc_code: string;
  title?: string;
  final_score?: number;
  verdict?: string;
}

function CareerCard({
  rec,
  planned,
  onSelect,
  onView,
  onReplan,
}: {
  rec: CareerRecommendation;
  planned?: PlannedInfo;
  onSelect: () => void;
  onView: () => void;
  onReplan: () => void;
}) {
  const score = typeof rec.match_score === 'number' ? rec.match_score : null;
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-600 shadow-sm p-5 flex flex-col hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between mb-2">
        <h3 className="font-bold text-gray-800 dark:text-gray-100 text-base">{rec.title}</h3>
        <div className="flex items-center gap-2 shrink-0">
          {planned && (
            <span className="bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 text-xs font-medium px-2 py-0.5 rounded-full">
              已规划
            </span>
          )}
          {score !== null && (
            <span className="bg-blue-600 text-white text-xs font-bold px-2 py-1 rounded-full whitespace-nowrap">
              {score}分
            </span>
          )}
        </div>
      </div>
      {rec.jd_market_signal && (
        <Tag color="cyan" className="mb-2 self-start">{rec.jd_market_signal}</Tag>
      )}

      {rec.match_reason && (
        <p className="text-gray-600 dark:text-gray-300 text-sm mb-3 leading-relaxed flex-1">{rec.match_reason}</p>
      )}

      {(rec.key_gaps || []).length > 0 && (
        <div className="mb-3">
          <p className="text-xs text-gray-400 dark:text-gray-500 mb-1">关键差距</p>
          <ul className="list-disc list-inside text-sm text-orange-600 space-y-0.5">
            {(rec.key_gaps || []).map((g, i) => <li key={i}>{g}</li>)}
          </ul>
        </div>
      )}

      {(rec.typical_jd_skills || []).length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-gray-400 dark:text-gray-500 mb-1">JD 高频技能</p>
          <div className="flex flex-wrap gap-1">
            {(rec.typical_jd_skills || []).map((s, i) => (
              <Tag key={i} className="text-xs">{s}</Tag>
            ))}
          </div>
        </div>
      )}

      <div className="mt-auto flex gap-2">
        {planned ? (
          <>
            <Button type="primary" onClick={onView} className="flex-1">
              查看规划
            </Button>
            <Button onClick={onReplan} className="flex-1">
              重新规划
            </Button>
          </>
        ) : (
          <Button type="primary" onClick={onSelect} className="w-full">
            选择这个方向
          </Button>
        )}
      </div>
    </div>
  );
}

// ================================================================== //
//  Block 1: MatchOverviewBlock  综合匹配评估
// ================================================================== //

function MatchOverviewSection({ block }: { block: MatchOverviewBlock }) {
  const verdictColor = VERDICT_COLOR[block.verdict] || '#1677ff';
  const { rule_based, llm_analysis } = block;

  return (
    <section id="plan-match_overview" className="mb-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-5">
      {/* 标题行 */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg">{block.occupation_title}</h3>
          <Tag color="blue" className="mt-0.5 text-xs">市场匹配</Tag>
        </div>
        <div className="text-center flex-shrink-0">
          <div
            className="w-20 h-20 rounded-full flex flex-col items-center justify-center border-4"
            style={{ borderColor: verdictColor }}
          >
            <span className="text-2xl font-bold text-gray-800 dark:text-gray-100">{block.final_score}</span>
            <span className="text-xs text-gray-400 dark:text-gray-500">/ 100</span>
          </div>
          <Tag color={verdictColor === '#52c41a' ? 'green' : verdictColor === '#ff4d4f' ? 'red' : verdictColor === '#fa8c16' ? 'orange' : 'blue'} className="mt-1">
            {block.verdict}
          </Tag>
        </div>
      </div>

      {/* 规则算分（JD 直接匹配无维度对比时隐藏） */}
      {rule_based.dim_comparison.length > 0 && (
        <>
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">规则算分</span>
              <span className="text-xs text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded">权重 60% · {rule_based.score}/100</span>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-600">
                  <th className="text-left py-2">维度</th>
                  <th className="text-center py-2">候选人</th>
                  <th className="text-center py-2">目标要求</th>
                  <th className="text-center py-2">Gap</th>
                  <th className="text-center py-2">状态</th>
                </tr>
              </thead>
              <tbody>
                {rule_based.dim_comparison.map((d, i) => (
                  <tr key={i} className="border-b border-gray-50 dark:border-gray-700">
                    <td className="py-2 text-gray-700 dark:text-gray-200">{d.label}</td>
                    <td className="text-center font-medium dark:text-gray-200">{d.candidate_score.toFixed(1)}</td>
                    <td className="text-center text-gray-500 dark:text-gray-400">{d.onet_required.toFixed(1)}</td>
                    <td className="text-center font-medium" style={{ color: d.gap > 0 ? '#fa8c16' : '#52c41a' }}>
                      {d.gap > 0 ? '+' : ''}{d.gap.toFixed(2)}
                    </td>
                    <td className="text-center">
                      <Tag color={GAP_STATUS_COLOR[d.status] || 'default'}>{d.status}</Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Divider className="my-4" />
        </>
      )}

      {/* LLM 综合判断 */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">AI 综合判断</span>
          <span className="text-xs text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded">
            {rule_based.dim_comparison.length > 0 ? `权重 40% · ${llm_analysis.score}/100` : `${llm_analysis.score}/100`}
          </span>
        </div>

        {llm_analysis.narrative && (
          <p className="text-gray-600 dark:text-gray-300 text-sm leading-relaxed mb-4 bg-gray-50 dark:bg-gray-700 rounded-lg px-4 py-3">
            {llm_analysis.narrative}
          </p>
        )}

        {(llm_analysis.key_factors || []).length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {(llm_analysis.key_factors || []).map((f: KeyFactor, i: number) => (
              <div key={i} className="flex items-start gap-2 bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-lg px-3 py-2">
                <Tag color={IMPACT_COLOR[f.impact] || 'default'} className="mt-0.5 shrink-0 text-xs">
                  {f.impact === 'positive' ? '✅ 正向' : f.impact === 'negative' ? '⚠️ 负向' : '─ 中性'}
                </Tag>
                <div>
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-100">{f.factor}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{f.note}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// ================================================================== //
//  Block 2: JdRecommendationsSection  高匹配岗位推荐
// ================================================================== //

function JDPositionCard({ pos, index }: { pos: JDPosition; index: number }) {
  const difficultyLabel = { easy: '容易入行', moderate: '中等难度', hard: '难度较高' };
  const difficultyColor = { easy: 'green', moderate: 'blue', hard: 'orange' };
  const diff = pos.match_analysis?.entry_difficulty || 'moderate';

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl shadow-sm overflow-hidden">
      {/* 卡片头 */}
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold text-gray-400 dark:text-gray-500">#{index + 1}</span>
            <h4 className="font-bold text-gray-800 dark:text-gray-100">{pos.title}</h4>
          </div>
          {pos.company_type && (
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{pos.company_type}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className="text-lg font-bold text-blue-600">{pos.match_score}%</span>
          <Tag color={difficultyColor[diff as keyof typeof difficultyColor] || 'blue'} className="text-xs">
            {difficultyLabel[diff as keyof typeof difficultyLabel] || diff}
          </Tag>
          {pos.salary_range && (
            <span className="text-xs text-gray-400 dark:text-gray-500">{pos.salary_range}</span>
          )}
        </div>
      </div>

      {/* 岗位解读 */}
      {pos.role_explanation && (
        <div className="px-5 py-3 bg-blue-50 dark:bg-blue-900/20 border-b border-blue-100 dark:border-blue-800">
          <p className="text-xs text-blue-600 dark:text-blue-400 font-medium mb-1">这个岗位在做什么</p>
          <p className="text-sm text-blue-800 dark:text-blue-300 leading-relaxed">{pos.role_explanation}</p>
        </div>
      )}

      {/* 职责 & 要求 */}
      {((pos.key_responsibilities || []).length > 0 || (pos.required_qualifications || []).length > 0) && (
        <div className="px-5 py-3 grid grid-cols-1 sm:grid-cols-2 gap-4 border-b border-gray-100 dark:border-gray-700">
          {(pos.key_responsibilities || []).length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">核心职责</p>
              <ul className="space-y-1">
                {(pos.key_responsibilities || []).map((r, i) => (
                  <li key={i} className="text-xs text-gray-600 dark:text-gray-300 flex items-start gap-1">
                    <span className="text-blue-400 mt-0.5">▸</span>{r}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(pos.required_qualifications || []).length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">任职要求</p>
              <ul className="space-y-1">
                {(pos.required_qualifications || []).map((q, i) => (
                  <li key={i} className="text-xs text-gray-600 dark:text-gray-300 flex items-start gap-1">
                    <span className="text-gray-400 dark:text-gray-500 mt-0.5">·</span>{q}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 对照分析：优势 & 顾虑 */}
      {pos.match_analysis && (
        <div className="px-5 py-3 grid grid-cols-1 sm:grid-cols-2 gap-4 border-b border-gray-100 dark:border-gray-700">
          <div>
            <p className="text-xs font-medium text-green-600 dark:text-green-400 mb-1.5">✅ 你的优势</p>
            <ul className="space-y-1">
              {(pos.match_analysis.strengths || []).map((s, i) => (
                <li key={i} className="text-xs text-gray-600 dark:text-gray-300">{s}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-xs font-medium text-orange-500 dark:text-orange-400 mb-1.5">⚠ 顾虑</p>
            <ul className="space-y-1">
              {(pos.match_analysis.concerns || []).map((c, i) => (
                <li key={i} className="text-xs text-gray-600 dark:text-gray-300">{c}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* 结论 + 完整 JD 展开 */}
      <div className="px-5 py-3">
        {pos.match_analysis?.verdict && (
          <p className="text-sm text-gray-600 dark:text-gray-300 italic mb-2">{pos.match_analysis.verdict}</p>
        )}
        {pos.full_jd && (
          <Collapse ghost>
            <Panel header={<span className="text-xs text-blue-500">查看完整 JD</span>} key="jd">
              <div className="prose prose-sm max-w-none text-gray-600 dark:text-gray-300 text-xs leading-relaxed whitespace-pre-wrap">
                {pos.full_jd}
              </div>
            </Panel>
          </Collapse>
        )}
      </div>
    </div>
  );
}

function JdRecommendationsSection({ block }: { block: JdRecommendationsBlock }) {
  return (
    <section id="plan-jd_recommendations" className="mb-6">
      <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-4">
        高匹配岗位推荐
        <span className="ml-2 text-sm font-normal text-gray-400 dark:text-gray-500">（{block.positions.length} 个岗位）</span>
      </h3>
      <div className="space-y-4">
        {(block.positions || []).map((pos, i) => (
          <JDPositionCard key={i} pos={pos} index={i} />
        ))}
      </div>
    </section>
  );
}

// ================================================================== //
//  Block 3: GapAnalysisSection  差距与优势分析
// ================================================================== //

function GapAnalysisSection({ block }: { block: GapAnalysisBlock }) {
  return (
    <section id="plan-gap_analysis" className="mb-6">
      <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-2">差距与优势分析</h3>
      {block.summary && (
        <p className="text-gray-500 dark:text-gray-400 text-sm mb-4 leading-relaxed">{block.summary}</p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* 左：差距 */}
        <div>
          <h4 className="text-sm font-semibold text-red-600 mb-3 flex items-center gap-1">
            ⚠️ 需要弥合的差距 <span className="text-gray-400 dark:text-gray-500 font-normal">（{(block.gaps || []).length} 项）</span>
          </h4>
          <div className="space-y-3">
            {(block.gaps || []).map((g: GapItem, i: number) => (
              <div key={i} className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl p-4 shadow-sm">
                <div className="flex items-center gap-2 mb-2">
                  <Tag color={SEVERITY_COLOR[g.severity] || 'default'} className="text-xs">
                    {SEVERITY_LABEL[g.severity] || g.severity}
                  </Tag>
                  <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{g.area}</span>
                </div>
                <div className="space-y-1.5 text-xs text-gray-600 dark:text-gray-300">
                  <div className="flex items-start gap-1.5">
                    <span className="text-gray-400 dark:text-gray-500 shrink-0 mt-0.5">岗位要求：</span>
                    <span className="leading-relaxed">{g.required}</span>
                  </div>
                  <div className="flex items-start gap-1.5">
                    <span className="text-gray-400 dark:text-gray-500 shrink-0 mt-0.5">当前状态：</span>
                    <span className="leading-relaxed">{g.current}</span>
                  </div>
                  {g.how_to_close && (
                    <div className="flex items-start gap-1.5 bg-orange-50 dark:bg-orange-900/20 rounded px-2 py-1.5 mt-2">
                      <span className="text-orange-500 dark:text-orange-400 shrink-0 mt-0.5">💡 闭合：</span>
                      <span className="text-orange-700 dark:text-orange-300 leading-relaxed">{g.how_to_close}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 右：优势 */}
        <div>
          <h4 className="text-sm font-semibold text-green-600 mb-3 flex items-center gap-1">
            ✅ 超过要求的优势 <span className="text-gray-400 dark:text-gray-500 font-normal">（{(block.strengths || []).length} 项）</span>
          </h4>
          <div className="space-y-3">
            {(block.strengths || []).map((s: StrengthItem, i: number) => (
              <div key={i} className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl p-4 shadow-sm">
                <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{s.area}</span>
                <div className="space-y-1.5 text-xs text-gray-600 dark:text-gray-300 mt-2">
                  <div className="flex items-start gap-1.5">
                    <span className="text-gray-400 dark:text-gray-500 shrink-0 mt-0.5">岗位基线：</span>
                    <span className="leading-relaxed">{s.required}</span>
                  </div>
                  <div className="flex items-start gap-1.5">
                    <span className="text-gray-400 dark:text-gray-500 shrink-0 mt-0.5">你的水平：</span>
                    <span className="leading-relaxed">{s.current}</span>
                  </div>
                  {s.leverage && (
                    <div className="flex items-start gap-1.5 bg-green-50 dark:bg-green-900/20 rounded px-2 py-1.5 mt-2">
                      <span className="text-green-600 dark:text-green-400 shrink-0 mt-0.5">🚀 放大：</span>
                      <span className="text-green-700 dark:text-green-300 leading-relaxed">{s.leverage}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

// ================================================================== //
//  Block 4: ActionPlanSection  分阶段行动计划
// ================================================================== //

function ActionPlanSection({ block }: { block: ActionPlanBlock }) {
  return (
    <section id="plan-action_plan" className="mb-6">
      <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-4">分阶段行动计划</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {block.phases.map((phase, pi) => (
          <div
            key={phase.phase_id}
            className={`rounded-xl border p-4 ${PHASE_BG[pi]} ${PHASE_BORDER[pi]}`}
            style={{ borderLeftWidth: 4, borderLeftColor: PHASE_COLORS[pi] }}
          >
            <p className={`font-bold text-sm mb-1 ${PHASE_TEXT[pi]}`}>{phase.label}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{phase.focus}</p>
            <div className="space-y-3">
              {phase.actions.map((action, ai) => (
                <div key={ai} className="bg-white dark:bg-gray-800 rounded-lg p-3 shadow-sm">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">📌 {action.item}</span>
                    {action.severity && (
                      <Tag color={SEVERITY_COLOR[action.severity] || 'default'} className="text-xs">
                        {SEVERITY_LABEL[action.severity] || action.severity}
                      </Tag>
                    )}
                  </div>
                  <p className="text-gray-600 dark:text-gray-300 text-xs mt-1"><strong>行动：</strong>{action.action}</p>
                  <p className="text-gray-600 dark:text-gray-300 text-xs mt-0.5"><strong>产出：</strong>{action.deliverable}</p>
                  <p className="text-blue-600 dark:text-blue-400 text-xs mt-0.5"><strong>资源：</strong>{action.resource}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ================================================================== //
//  主页面
// ================================================================== //

type Step = 'match_loading' | 'select' | 'plan_loading' | 'report';

const PLAN_BLOCK_ANCHORS = [
  { id: 'match_overview',    label: '综合评估' },
  { id: 'jd_recommendations', label: '岗位推荐' },
  { id: 'gap_analysis',      label: '差距分析' },
  { id: 'action_plan',       label: '行动计划' },
];

const CareerPlan: React.FC = () => {
  const navigate = useNavigate();
  const {
    assessmentId, matchData, planData, selectedCareer,
    setMatchData, setMatchLoading, setMatchError,
    matchLoading, matchError,
    setPlanData, setPlanLoading, setPlanError,
    planLoading, planError,
    setSelectedCareer, resetPlan,
  } = useAppStore();

  // ── 从 store 推导初始 step（避免导航返回时闪烁） ────────
  const [step, setStep] = useState<Step>(() => {
    if (planLoading) return 'plan_loading';
    if (matchLoading) return 'match_loading';
    if (planData && selectedCareer) return 'report';
    if (matchData) return 'select';
    return 'match_loading';
  });
  const [planStep, setPlanStep] = useState(0);
  const [plannedCodes, setPlannedCodes] = useState<Record<string, PlannedInfo>>({});

  // ── 获取已规划的职业代码 ─────────────────────────────────
  const fetchPlannedCodes = () => {
    if (!assessmentId) return;
    api.getPlannedCodes(assessmentId)
      .then((res) => setPlannedCodes(res.data.planned || {}))
      .catch(() => { /* 非关键请求，静默降级 */ });
  };

  // ── step 推导：响应 store 中 loading 状态变化 ─────────────
  useEffect(() => {
    if (!assessmentId) return;
    if (planLoading) { setStep('plan_loading'); return; }
    if (matchLoading) { setStep('match_loading'); return; }
    // loading 完成后，根据前一状态决定下一视图
    setStep(prev => {
      if (prev === 'plan_loading' && planData) return 'report';
      if (prev === 'match_loading' && matchData) return 'select';
      if (prev === 'report' && planData) return 'report';
      if (matchData) return 'select';
      return 'match_loading';
    });
  }, [assessmentId, matchData, matchLoading, planLoading, planData]);

  // ── 获取职业匹配 ─────────────────────────────────────────
  const fetchMatch = (force = false) => {
    if (!assessmentId) return;
    if (!force && (matchData || matchLoading)) return;
    setMatchLoading(true);
    setMatchError(null);
    setStep('match_loading');
    api.matchCareers(assessmentId)
      .then((res) => { setMatchData(res.data); setStep('select'); })
      .catch((err) => {
        setMatchError(err?.response?.data?.detail || err?.message || '职业匹配失败');
        setStep('select');
      })
      .finally(() => setMatchLoading(false));
  };

  // ── 初始化（不重复触发已进行中的请求） ───────────────────
  useEffect(() => {
    if (!assessmentId) return;
    fetchPlannedCodes();
    if (!matchData && !matchLoading) fetchMatch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessmentId]);

  // ── plan_loading 步骤动画 ────────────────────────────────
  useEffect(() => {
    if (step !== 'plan_loading') { setPlanStep(0); return; }
    const timer = setInterval(() => {
      setPlanStep((s) => Math.min(s + 1, PLAN_STEPS.length - 1));
    }, 35_000);
    return () => clearInterval(timer);
  }, [step]);

  // ── 查看已有规划（GET 读缓存，快速） ─────────────────────
  const handleViewPlan = (code: string) => {
    if (!assessmentId) return;
    // 如果 store 中已有该职业的规划数据，直接展示
    if (planData && selectedCareer === code) {
      setStep('report');
      return;
    }
    setSelectedCareer(code);
    setPlanLoading(true);
    setPlanError(null);
    setStep('plan_loading');
    api.getCareerPlanCached(assessmentId, code)
      .then((res) => { setPlanData(res.data); setStep('report'); })
      .catch((err) => {
        setPlanError(err?.response?.data?.detail || err?.message || '加载规划失败');
        setStep('select');
      })
      .finally(() => setPlanLoading(false));
  };

  // ── 生成/重新生成规划（POST 走 Agent，慢） ───────────────
  const handleGeneratePlan = (code: string, title?: string) => {
    if (!assessmentId) return;
    setSelectedCareer(code);
    setPlanLoading(true);
    setPlanError(null);
    setStep('plan_loading');
    // JD 直接推荐需要传 title 给后端
    const jdTitle = code.startsWith('jd-') ? title : undefined;
    api.careerPlan(assessmentId, code, jdTitle)
      .then((res) => {
        setPlanData(res.data);
        setStep('report');
        // 刷新 plannedCodes
        setPlannedCodes((prev) => ({
          ...prev,
          [code]: {
            onetsoc_code: code,
            title: res.data.blocks?.match_overview?.occupation_title,
            final_score: res.data.blocks?.match_overview?.final_score,
            verdict: res.data.blocks?.match_overview?.verdict,
          },
        }));
      })
      .catch((err) => {
        setPlanError(err?.response?.data?.detail || err?.message || '规划生成失败');
        setStep('select');
      })
      .finally(() => setPlanLoading(false));
  };

  const extractRecs = (): CareerRecommendation[] => {
    if (!matchData) return [];
    const result = matchData.result;
    if (Array.isArray(result?.recommended)) return result.recommended;
    if (Array.isArray(result)) return result as CareerRecommendation[];
    return [];
  };

  // ── 渲染 ─────────────────────────────────────────────────

  if (!assessmentId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <p className="text-gray-500 dark:text-gray-400">请先完成能力评估</p>
        <Button type="primary" onClick={() => navigate('/profile')}>去评估</Button>
      </div>
    );
  }

  if (step === 'match_loading') {
    return <CareerMatchSkeleton />;
  }

  if (step === 'select' && matchError) {
    return (
      <Alert
        type="error"
        message="职业匹配失败"
        description={matchError}
        showIcon
        action={
          <Button onClick={() => { setMatchData(null); fetchMatch(true); }}>
            重试
          </Button>
        }
      />
    );
  }

  if (step === 'select') {
    const recs = extractRecs();
    const plannedCount = Object.keys(plannedCodes).length;
    return (
      <div>
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">为您推荐的职业方向</h2>
            <p className="text-gray-500 dark:text-gray-400 mt-1">
              基于您的能力画像，以下职业方向与您最为匹配
              {plannedCount > 0 && (
                <span className="ml-2 text-green-600 text-sm">（已规划 {plannedCount} 个方向）</span>
              )}
            </p>
          </div>
          <Button
            onClick={() => fetchMatch(true)}
            loading={matchLoading}
          >
            重新匹配
          </Button>
        </div>
        {recs.length === 0 ? (
          <Alert
            type="warning"
            message="暂无推荐结果"
            description="点击右上方按钮重新匹配职业方向。"
            showIcon
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {recs.map((rec) => (
              <CareerCard
                key={rec.onetsoc_code}
                rec={rec}
                planned={plannedCodes[rec.onetsoc_code]}
                onSelect={() => handleGeneratePlan(rec.onetsoc_code, rec.title)}
                onView={() => handleViewPlan(rec.onetsoc_code)}
                onReplan={() => handleGeneratePlan(rec.onetsoc_code, rec.title)}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  if (step === 'plan_loading') {
    return (
      <div className="max-w-md mx-auto mt-12 text-center">
        <Spin size="large" />
        <p className="text-blue-700 dark:text-blue-400 font-medium text-lg mt-4 mb-6">正在生成详细职业规划（约2-4分钟）...</p>
        <Steps
          current={planStep}
          direction="vertical"
          size="small"
          items={PLAN_STEPS.map((title) => ({ title }))}
        />
      </div>
    );
  }

  if (step === 'report' && planData) {
    const { blocks } = planData as { blocks: CareerPlanBlocks };
    const occupationTitle =
      blocks.match_overview?.occupation_title || selectedCareer || '';

    return (
      <div>
        {/* 顶部导航栏 */}
        <div className="sticky top-0 z-10 bg-white dark:bg-gray-800 border-b border-gray-100 dark:border-gray-700 -mx-6 px-6 py-2 mb-6 flex items-center gap-4">
          <button
            onClick={() => setStep('select')}
            className="text-sm text-gray-400 dark:text-gray-500 hover:text-blue-600 transition-colors"
          >
            ← 返回列表
          </button>
          <span className="text-sm text-gray-500 dark:text-gray-400">|</span>
          <span className="text-sm font-medium text-gray-700 dark:text-gray-200">{occupationTitle}</span>
          {blocks.match_overview && (
            <Tag
              color={blocks.match_overview.verdict === '不建议' ? 'red' : blocks.match_overview.verdict === '潜力匹配' ? 'orange' : 'blue'}
              className="text-xs"
            >
              {blocks.match_overview.verdict} · {blocks.match_overview.final_score}分
            </Tag>
          )}
          <button
            onClick={() => {
              if (selectedCareer && assessmentId) {
                handleGeneratePlan(selectedCareer);
              }
            }}
            className="text-xs text-gray-400 dark:text-gray-500 hover:text-orange-500 border border-gray-200 dark:border-gray-600 hover:border-orange-300 px-2 py-1 rounded transition-colors whitespace-nowrap"
          >
            重新规划
          </button>
          <div className="flex gap-2 ml-auto overflow-x-auto">
            {PLAN_BLOCK_ANCHORS.map(({ id, label }) => (
              <button
                key={id}
                onClick={() => scrollTo(id)}
                className="text-xs text-gray-400 dark:text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 px-2 py-1 rounded whitespace-nowrap transition-colors"
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {planError && <Alert type="error" message={planError} showIcon className="mb-4" />}

        {blocks.match_overview && <MatchOverviewSection block={blocks.match_overview} />}
        {blocks.jd_recommendations && <JdRecommendationsSection block={blocks.jd_recommendations} />}
        {blocks.gap_analysis && <GapAnalysisSection block={blocks.gap_analysis} />}
        {blocks.action_plan && <ActionPlanSection block={blocks.action_plan} />}
      </div>
    );
  }

  return null;
};

export default CareerPlan;
