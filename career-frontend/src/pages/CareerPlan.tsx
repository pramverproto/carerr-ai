import React, { lazy, Suspense, useEffect, useState } from 'react';
import { Button, Spin, Alert, Steps, Tag, Input, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store/appStore';
import CareerMatchSkeleton from '@/components/skeletons/CareerMatchSkeleton';
import MatchOverviewSection from '@/components/career-plan/MatchOverviewSection';
import { api } from '@/api/client';
import type {
  CareerPathRecommendation,
  CareerPathStage,
  CareerPlanBlocks,
  FutureOutlookBlock,
} from '@/types';

// 懒加载 Block 2/3/4：用户进入 'report' 步骤后才下载这些 chunk
const JdRecommendationsSection = lazy(() => import('@/components/career-plan/JdRecommendationsSection'));
const GapAnalysisSection = lazy(() => import('@/components/career-plan/GapAnalysisSection'));
const ActionPlanSection = lazy(() => import('@/components/career-plan/ActionPlanSection'));

// ── 工具函数 ──────────────────────────────────────────────────────────

function scrollTo(id: string) {
  document.getElementById(`plan-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

const SectionFallback = () => (
  <div className="mb-6 flex justify-center py-8">
    <Spin />
  </div>
);

const PLAN_STEPS = [
  '分析综合匹配度（Block 1）',
  '生成高匹配岗位推荐（Block 2）',
  '分析差距与优势（Block 3）',
  '生成分阶段行动计划（Block 4）',
];

// ── CareerPathCard ────────────────────────────────────────────────────

interface PlannedInfo {
  onetsoc_code: string;
  title?: string;
  final_score?: number;
  verdict?: string;
}

/** 路线时间线可视化 */
function PathTimeline({ stages }: { stages: CareerPathStage[] }) {
  return (
    <div className="flex items-center gap-0 my-3 overflow-x-auto">
      {stages.map((s, i) => (
        <React.Fragment key={s.stage}>
          <div className="flex flex-col items-center min-w-[90px]">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold ${
                i === 0
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-300'
              }`}
            >
              {s.stage}
            </div>
            <p className="text-xs font-medium text-gray-700 dark:text-gray-200 mt-1 text-center leading-tight">
              {s.title}
            </p>
            <p className="text-[10px] text-gray-400 dark:text-gray-500">{s.timeframe}</p>
            {s.salary_range && (
              <p className="text-[10px] text-green-600 dark:text-green-400">{s.salary_range}</p>
            )}
          </div>
          {i < stages.length - 1 && (
            <div className="flex-1 min-w-[20px] h-px bg-gray-300 dark:bg-gray-600 mx-1 mt-[-16px]" />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

function CareerPathCard({
  rec,
  planned,
  onSelect,
  onView,
  onReplan,
}: {
  rec: CareerPathRecommendation;
  planned?: PlannedInfo;
  onSelect: () => void;
  onView: () => void;
  onReplan: () => void;
}) {
  const stage1 = rec.stages?.[0];
  const score1 = stage1?.match_score ?? null;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-600 shadow-sm p-5 flex flex-col hover:shadow-md transition-shadow">
      {/* 标题行 */}
      <div className="flex items-start justify-between mb-1">
        <h3 className="font-bold text-gray-800 dark:text-gray-100 text-base">{rec.path_name}</h3>
        <div className="flex items-center gap-2 shrink-0">
          {planned && (
            <span className="bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 text-xs font-medium px-2 py-0.5 rounded-full">
              已规划
            </span>
          )}
          <span className="bg-blue-600 text-white text-xs font-bold px-2 py-1 rounded-full whitespace-nowrap">
            综合 {rec.overall_score}分
          </span>
        </div>
      </div>

      {rec.market_signal && (
        <Tag color="cyan" className="mb-1 self-start">{rec.market_signal}</Tag>
      )}

      {rec.path_summary && (
        <p className="text-gray-600 dark:text-gray-300 text-sm mb-2 leading-relaxed">{rec.path_summary}</p>
      )}

      {/* 路线时间线 */}
      <PathTimeline stages={rec.stages || []} />

      {/* 起点匹配详情 */}
      {stage1 && (
        <div className="mt-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg px-3 py-2">
          <p className="text-xs font-medium text-blue-700 dark:text-blue-300 mb-1">
            起点匹配度：{score1 !== null ? `${score1}分` : '—'}
          </p>
          {stage1.match_reason && (
            <p className="text-xs text-gray-600 dark:text-gray-300 mb-1">{stage1.match_reason}</p>
          )}
          {(stage1.key_gaps || []).length > 0 && (
            <div className="mt-1">
              <p className="text-[10px] text-gray-400 dark:text-gray-500">关键差距：</p>
              <ul className="list-disc list-inside text-xs text-orange-600 space-y-0.5">
                {(stage1.key_gaps || []).map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 按钮 */}
      <div className="mt-auto pt-3 flex gap-2">
        {planned ? (
          <>
            <Button type="primary" onClick={onView} className="flex-1">查看规划</Button>
            <Button onClick={onReplan} className="flex-1">重新规划</Button>
          </>
        ) : (
          <Button type="primary" onClick={onSelect} className="w-full">选择这条路线</Button>
        )}
      </div>
    </div>
  );
}

// ================================================================== //
//  Block 5: FutureOutlookSection  后续阶段展望
// ================================================================== //

function FutureOutlookSection({
  block,
  onStageComplete,
}: {
  block: FutureOutlookBlock;
  onStageComplete?: (userNote: string) => void;
}) {
  const [userNote, setUserNote] = useState('');
  const [confirming, setConfirming] = useState(false);

  return (
    <section id="plan-future_outlook" className="mb-6">
      <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-4">后续阶段展望</h3>

      {/* 当前阶段 */}
      <div className="flex items-center gap-2 mb-4">
        <div className="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">
          {block.current_stage}
        </div>
        <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
          当前：{block.current_title}
        </span>
      </div>

      {/* 后续阶段卡片 */}
      <div className="space-y-4">
        {(block.next_stages || []).map((ns) => (
          <div key={ns.stage} className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl p-4 shadow-sm">
            <div className="flex items-center gap-3 mb-2">
              <div className="w-6 h-6 rounded-full bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-300 flex items-center justify-center text-xs font-bold">
                {ns.stage}
              </div>
              <div>
                <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{ns.title}</span>
                <span className="text-xs text-gray-400 dark:text-gray-500 ml-2">
                  {ns.timeframe}{ns.salary_range ? ` · ${ns.salary_range}` : ''}
                </span>
              </div>
            </div>

            {(ns.key_skills || []).length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {ns.key_skills.map((sk, i) => (
                  <Tag key={i} className="text-xs">{sk}</Tag>
                ))}
              </div>
            )}

            {ns.transition_tips && (
              <div className="text-xs text-gray-600 dark:text-gray-300 mb-1">
                <span className="text-gray-400 dark:text-gray-500">过渡建议：</span>
                {ns.transition_tips}
              </div>
            )}
            {ns.preparation_now && (
              <div className="text-xs bg-green-50 dark:bg-green-900/20 rounded px-2 py-1.5 mt-1">
                <span className="text-green-600 dark:text-green-400">现在就能准备：</span>
                <span className="text-green-700 dark:text-green-300">{ns.preparation_now}</span>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 路线叙事 */}
      {block.path_narrative && (
        <div className="mt-4 bg-gray-50 dark:bg-gray-700 rounded-lg px-4 py-3">
          <p className="text-sm text-gray-600 dark:text-gray-300 leading-relaxed italic">
            {block.path_narrative}
          </p>
        </div>
      )}

      {/* 阶段完成确认 */}
      {onStageComplete && (block.next_stages || []).length > 0 && (
        <div className="mt-6 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl p-5">
          <h4 className="font-semibold text-blue-700 dark:text-blue-300 text-sm mb-2">
            准备进入下一阶段？
          </h4>
          <p className="text-xs text-gray-600 dark:text-gray-300 mb-3">
            当你完成了当前阶段「{block.current_title}」的学习计划，或已进入
            「{block.next_stages[0]?.title}」相关岗位，可以开启下一阶段详细规划。
          </p>
          <Input.TextArea
            placeholder="简述你的当前情况（选填）"
            value={userNote}
            onChange={(e) => setUserNote(e.target.value)}
            rows={2}
            className="mb-3"
          />
          <Button
            type="primary"
            loading={confirming}
            onClick={async () => {
              setConfirming(true);
              try {
                await onStageComplete(userNote);
              } finally {
                setConfirming(false);
              }
            }}
          >
            开启下一阶段规划
          </Button>
        </div>
      )}
    </section>
  );
}

// ================================================================== //
//  主页面
// ================================================================== //

type Step = 'idle' | 'match_loading' | 'select' | 'plan_loading' | 'report';

const PLAN_BLOCK_ANCHORS = [
  { id: 'match_overview',     label: '综合评估' },
  { id: 'jd_recommendations', label: '岗位推荐' },
  { id: 'gap_analysis',       label: '差距分析' },
  { id: 'action_plan',        label: '行动计划' },
  { id: 'future_outlook',     label: '阶段展望' },
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
  // 没有 matchData 时进入 idle 态，等用户点击按钮才发起匹配请求
  const [step, setStep] = useState<Step>(() => {
    if (planLoading) return 'plan_loading';
    if (matchLoading) return 'match_loading';
    if (planData && selectedCareer) return 'report';
    if (matchData) return 'select';
    return 'idle';
  });
  const [planStep, setPlanStep] = useState(0);
  const [plannedCodes, setPlannedCodes] = useState<Record<string, PlannedInfo>>({});
  // 当前选中的路线数据（用于传给规划接口）
  const [selectedPath, setSelectedPath] = useState<CareerPathRecommendation | null>(null);
  const [currentStage, setCurrentStage] = useState(1);

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
    // loading 完成后，根据前一状态和已有数据决定下一视图；
    // 没有任何数据时保持 idle，等待用户点击按钮触发
    setStep(prev => {
      if (prev === 'plan_loading' && planData) return 'report';
      if (prev === 'match_loading' && matchData) return 'select';
      if (prev === 'report' && planData) return 'report';
      if (matchData) return 'select';
      return 'idle';
    });
  }, [assessmentId, matchData, matchLoading, planLoading, planData]);

  // ── 获取职业匹配 ─────────────────────────────────────────
  const fetchMatch = (force = false, customStartVal?: string) => {
    if (!assessmentId) return;
    if (!force && (matchData || matchLoading)) return;
    setMatchLoading(true);
    setMatchError(null);
    setStep('match_loading');
    api.matchCareers(assessmentId, force, customStartVal || undefined)
      .then((res) => { setMatchData(res.data); setStep('select'); })
      .catch((err) => {
        setMatchError(err?.response?.data?.detail || err?.message || '职业路线匹配失败');
        setStep('select');
      })
      .finally(() => setMatchLoading(false));
  };

  // ── 初始化：只拉已规划的职业代码（轻量），匹配请求改由用户点击触发 ───
  useEffect(() => {
    if (!assessmentId) return;
    fetchPlannedCodes();
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
  const handleGeneratePlan = (
    path: CareerPathRecommendation,
    stageNum = 1,
  ) => {
    if (!assessmentId) return;
    const stage = path.stages?.[stageNum - 1];
    if (!stage) return;
    const code = `${path.path_code}-s${stageNum}`;
    setSelectedCareer(code);
    setSelectedPath(path);
    setCurrentStage(stageNum);
    setPlanLoading(true);
    setPlanError(null);
    setStep('plan_loading');

    api.careerPlan(
      assessmentId,
      code,
      stage.title,
      JSON.stringify(path),
      stageNum,
    )
      .then((res) => {
        setPlanData(res.data);
        setStep('report');
        setPlannedCodes((prev) => ({
          ...prev,
          [code]: {
            onetsoc_code: code,
            title: res.data.blocks?.match_overview?.occupation_title,
            final_score: res.data.blocks?.match_overview?.final_score,
            verdict: res.data.blocks?.match_overview?.verdict,
          },
        }));
        // 保存路线进度
        api.saveCareerPath(assessmentId, path.path_code, JSON.stringify(path)).catch(() => {});
      })
      .catch((err) => {
        setPlanError(err?.response?.data?.detail || err?.message || '规划生成失败');
        setStep('select');
      })
      .finally(() => setPlanLoading(false));
  };

  // ── 阶段完成确认 → 触发下一阶段规划 ──────────────────────
  const handleStageComplete = async (userNote: string) => {
    if (!assessmentId || !selectedPath) return;
    try {
      await api.confirmStageComplete(
        assessmentId,
        selectedPath.path_code,
        currentStage,
        userNote,
      );
      message.success(`阶段 ${currentStage} 已完成，正在生成下一阶段规划...`);
      // 触发下一阶段
      handleGeneratePlan(selectedPath, currentStage + 1);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail
        || (err as { message?: string })?.message || '阶段确认失败';
      message.error(msg);
    }
  };

  const extractRecs = (): CareerPathRecommendation[] => {
    if (!matchData) return [];
    const result = matchData.result;
    if (Array.isArray(result?.recommended)) return result.recommended as CareerPathRecommendation[];
    if (Array.isArray(result)) return result as CareerPathRecommendation[];
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

  if (step === 'idle') {
    return <IdleEntry onAiRecommend={() => fetchMatch()} navigate={navigate} setSelectedCareer={setSelectedCareer} />;
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
        <button
          onClick={() => { setMatchData(null); setStep('idle'); }}
          className="text-sm text-gray-400 hover:text-gray-600 mb-3 flex items-center gap-1"
        >
          ← 返回选择方式（AI 推荐 / 自己输入）
        </button>
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">为您推荐的职业发展路线</h2>
            <p className="text-gray-500 dark:text-gray-400 mt-1">
              基于您的能力画像，以下职业发展路线与您最为匹配
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
            description="点击右上方「重新匹配」再试一次，或返回上一步选择「自己输入目标岗位」。"
            showIcon
          />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {recs.map((rec) => {
              const code = `${rec.path_code}-s1`;
              return (
                <CareerPathCard
                  key={rec.path_code}
                  rec={rec}
                  planned={plannedCodes[code]}
                  onSelect={() => handleGeneratePlan(rec)}
                  onView={() => handleViewPlan(code)}
                  onReplan={() => handleGeneratePlan(rec)}
                />
              );
            })}
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
    const totalStages = selectedPath?.stages?.length ?? 1;

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
          {selectedPath && (
            <button
              onClick={() => handleGeneratePlan(selectedPath, currentStage)}
              className="text-xs text-gray-400 dark:text-gray-500 hover:text-orange-500 border border-gray-200 dark:border-gray-600 hover:border-orange-300 px-2 py-1 rounded transition-colors whitespace-nowrap"
            >
              重新规划
            </button>
          )}
          <div className="flex gap-2 ml-auto overflow-x-auto">
            {PLAN_BLOCK_ANCHORS.filter(
              (a) => a.id !== 'future_outlook' || blocks.future_outlook,
            ).map(({ id, label }) => (
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

        {/* 路线进度条 */}
        {selectedPath && totalStages > 1 && (
          <div className="mb-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-4">
            <p className="text-xs text-gray-400 dark:text-gray-500 mb-2">{selectedPath.path_name}</p>
            <div className="flex items-center gap-0">
              {selectedPath.stages.map((s, i) => {
                const isCompleted = s.stage < currentStage;
                const isCurrent = s.stage === currentStage;
                return (
                  <React.Fragment key={s.stage}>
                    <div className="flex flex-col items-center min-w-[80px]">
                      <div
                        className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${
                          isCompleted
                            ? 'bg-green-500 text-white'
                            : isCurrent
                              ? 'bg-blue-600 text-white ring-2 ring-blue-300'
                              : 'bg-gray-200 dark:bg-gray-600 text-gray-400 dark:text-gray-400'
                        }`}
                      >
                        {isCompleted ? '✓' : s.stage}
                      </div>
                      <p className={`text-xs mt-1 text-center ${isCurrent ? 'font-semibold text-blue-700 dark:text-blue-300' : 'text-gray-500 dark:text-gray-400'}`}>
                        {s.title}
                      </p>
                      <p className={`text-[10px] ${
                        isCompleted ? 'text-green-500' : isCurrent ? 'text-blue-500' : 'text-gray-400 dark:text-gray-500'
                      }`}>
                        {isCompleted ? '已完成' : isCurrent ? '规划中' : '待开启'}
                      </p>
                    </div>
                    {i < selectedPath.stages.length - 1 && (
                      <div className={`flex-1 min-w-[16px] h-0.5 mx-1 mt-[-18px] ${
                        isCompleted ? 'bg-green-400' : 'bg-gray-200 dark:bg-gray-600'
                      }`} />
                    )}
                  </React.Fragment>
                );
              })}
            </div>
          </div>
        )}

        {planError && <Alert type="error" message={planError} showIcon className="mb-4" />}

        {blocks.match_overview && <MatchOverviewSection block={blocks.match_overview} />}
        <Suspense fallback={<SectionFallback />}>
          {blocks.jd_recommendations && <JdRecommendationsSection block={blocks.jd_recommendations} />}
          {blocks.gap_analysis && <GapAnalysisSection block={blocks.gap_analysis} />}
          {blocks.action_plan && <ActionPlanSection block={blocks.action_plan} />}
        </Suspense>
        {blocks.future_outlook && (
          <FutureOutlookSection
            block={blocks.future_outlook}
            onStageComplete={
              currentStage < totalStages ? handleStageComplete : undefined
            }
          />
        )}
      </div>
    );
  }

  return null;
};

// ────────────────────────────────────────────────────────────────────
//  入口选择：AI 推荐 vs 自己输入目标岗位
// ────────────────────────────────────────────────────────────────────

const IdleEntry: React.FC<{
  onAiRecommend: () => void;
  navigate: ReturnType<typeof useNavigate>;
  setSelectedCareer: (code: string | null) => void;
}> = ({ onAiRecommend, navigate, setSelectedCareer }) => {
  const [mode, setMode] = useState<'choose' | 'manual'>('choose');
  const [manualTitle, setManualTitle] = useState('');

  if (mode === 'manual') {
    return (
      <div className="max-w-xl mx-auto mt-12">
        <button
          onClick={() => setMode('choose')}
          className="text-sm text-gray-400 hover:text-gray-600 mb-4 flex items-center gap-1"
        >
          ← 返回选择
        </button>
        <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100 mb-2">
          输入你的目标岗位
        </h2>
        <p className="text-gray-500 mb-6">
          已经有明确目标？输入岗位名称，跳过推荐直接进入学习计划生成
        </p>
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 p-5 space-y-4">
          <input
            type="text"
            value={manualTitle}
            onChange={(e) => setManualTitle(e.target.value)}
            placeholder="例如：AI Agent 开发工程师 / 前端架构师 / 数据分析师"
            className="w-full px-4 py-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 focus:outline-none focus:border-blue-500"
            maxLength={50}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && manualTitle.trim()) {
                setSelectedCareer(`manual:${manualTitle.trim()}`);
                navigate('/plan');
              }
            }}
          />
          <Button
            type="primary"
            size="large"
            block
            disabled={!manualTitle.trim()}
            onClick={() => {
              setSelectedCareer(`manual:${manualTitle.trim()}`);
              navigate('/plan');
            }}
          >
            进入学习计划
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto mt-12">
      <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100 mb-2 text-center">
        如何确定你的发展方向？
      </h2>
      <p className="text-gray-500 mb-8 text-center">两种方式，按需选择</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <button
          onClick={onAiRecommend}
          className="text-left p-6 bg-white dark:bg-gray-800 rounded-xl border-2 border-blue-200 dark:border-blue-800 hover:border-blue-500 dark:hover:border-blue-400 hover:shadow-md transition-all"
        >
          <div className="text-2xl mb-3">🎯</div>
          <h3 className="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-2">
            AI 推荐路线
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
            基于能力画像，AI 推荐若干条最匹配的发展路线，每条含 3 个递进阶段。
            适合还在探索、想看更多可能性。
          </p>
          <p className="text-xs text-blue-500 mt-3">推荐生成 ≈ 30 秒</p>
        </button>
        <button
          onClick={() => setMode('manual')}
          className="text-left p-6 bg-white dark:bg-gray-800 rounded-xl border-2 border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500 hover:shadow-md transition-all"
        >
          <div className="text-2xl mb-3">✍️</div>
          <h3 className="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-2">
            自己输入目标岗位
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
            已经有清晰目标？跳过推荐，直接为你目标岗位生成学习计划。
            适合方向已定、想立刻开始学习。
          </p>
          <p className="text-xs text-gray-400 mt-3">直接进入下一步</p>
        </button>
      </div>
    </div>
  );
};

export default CareerPlan;
