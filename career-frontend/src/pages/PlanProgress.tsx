import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { ArrowLeft, Plus, Trash2 } from 'lucide-react';
import { App } from 'antd';
import { useAppStore } from '@/store/appStore';
import PlanProgressSkeleton from '@/components/skeletons/PlanProgressSkeleton';
import { api } from '@/api/client';
import type { PlanSchedule, PlanWeek, PlanDay, DailyTask, PlanListItem } from '@/types';

// ── 工具 ──────────────────────────────────────────────────────────────

function today(): string {
  return new Date().toISOString().split('T')[0];
}

/** 计算计划结束日期 = start_date + duration_weeks * 7 天 */
function planEndDate(p: PlanListItem): string {
  const d = new Date(p.start_date + 'T00:00:00');
  d.setDate(d.getDate() + p.duration_weeks * 7);
  return d.toISOString().split('T')[0];
}

function calcProgress(plan: PlanSchedule): { done: number; total: number } {
  let done = 0, total = 0;
  for (const week of plan.weeks) {
    for (const day of week.days) {
      total += day.tasks.length;
      done += day.completed_ids.length;
    }
  }
  return { done, total };
}

function weekProgress(week: PlanWeek): { done: number; total: number } {
  let done = 0, total = 0;
  for (const day of week.days) {
    total += day.tasks.length;
    done += day.completed_ids.length;
  }
  return { done, total };
}

const TASK_TYPE_LABEL: Record<string, string> = {
  study: '学习',
  practice: '实践',
  network: '社交拓展',
  apply: '申请行动',
};

const TASK_TYPE_COLOR: Record<string, string> = {
  study: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300',
  practice: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300',
  network: 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
  apply: 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300',
};

// ── 子组件 ────────────────────────────────────────────────────────────

const ProgressBar: React.FC<{ value: number; max: number; className?: string }> = ({
  value, max, className = '',
}) => {
  const pct = max === 0 ? 0 : Math.round((value / max) * 100);
  return (
    <div className={`h-2 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden ${className}`}>
      <div
        className="h-full bg-blue-500 rounded-full transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
};

interface DayCardProps {
  planId: string;
  week: number;
  day: PlanDay;
  onUpdated: (planId: string, weekNum: number, dayNum: number, completedIds: string[], taskNotes: Record<string, string>) => void;
}

const DayCard: React.FC<DayCardProps> = ({ planId, week, day, onUpdated }) => {
  const { message } = App.useApp();
  const [saving, setSaving] = useState(false);
  // noteInputTaskId: which task's note input is open
  const [noteInputTaskId, setNoteInputTaskId] = useState<string | null>(null);
  const [noteText, setNoteText] = useState('');
  const [savingNote, setSavingNote] = useState(false);

  const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
  const dateObj = new Date(day.date + 'T00:00:00');
  const weekday = weekdays[dateObj.getDay()];

  async function toggle(task: DailyTask) {
    const wasChecked = day.completed_ids.includes(task.id);
    const ids = wasChecked
      ? day.completed_ids.filter((i) => i !== task.id)
      : [...day.completed_ids, task.id];
    setSaving(true);
    try {
      await api.updateDayProgress(planId, week, day.day_number, ids);
      onUpdated(planId, week, day.day_number, ids, day.task_notes ?? {});
      // open note input when checking (not unchecking)
      if (!wasChecked) {
        setNoteText(day.task_notes?.[task.id] ?? '');
        setNoteInputTaskId(task.id);
      } else if (noteInputTaskId === task.id) {
        setNoteInputTaskId(null);
      }
    } catch {
      message.error('保存失败，请重试');
    } finally {
      setSaving(false);
    }
  }

  async function saveNote(taskId: string) {
    setSavingNote(true);
    try {
      await api.updateTaskNote(planId, week, day.day_number, taskId, noteText);
      const newNotes = { ...(day.task_notes ?? {}), [taskId]: noteText };
      if (!noteText.trim()) delete newNotes[taskId];
      onUpdated(planId, week, day.day_number, day.completed_ids, newNotes);
      setNoteInputTaskId(null);
    } catch {
      message.error('笔记保存失败');
    } finally {
      setSavingNote(false);
    }
  }

  function openNote(task: DailyTask) {
    setNoteText(day.task_notes?.[task.id] ?? '');
    setNoteInputTaskId(task.id);
  }

  const done = day.completed_ids.length;
  const total = day.tasks.length;

  return (
    <div className="border border-gray-200 dark:border-gray-600 rounded-xl p-4 bg-white dark:bg-gray-800">
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="font-medium text-gray-800 dark:text-gray-100">
            {day.date.slice(5).replace('-', '/')} 周{weekday}
          </span>
          <span className="ml-2 text-sm text-gray-400">{done}/{total} 完成</span>
        </div>
        {saving && <span className="text-xs text-gray-400 animate-pulse">保存中…</span>}
      </div>
      <ProgressBar value={done} max={total} className="mb-3" />
      <ul className="space-y-2">
        {day.tasks.map((task) => {
          const checked = day.completed_ids.includes(task.id);
          const existingNote = day.task_notes?.[task.id];
          const isNoteOpen = noteInputTaskId === task.id;
          return (
            <li key={task.id} className="rounded-lg border border-transparent hover:border-gray-100 dark:hover:border-gray-700 transition-colors">
              <div
                className={`flex items-start gap-3 p-2 rounded-lg cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
                  checked ? 'opacity-70' : ''
                }`}
                onClick={() => toggle(task)}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(task)}
                  onClick={(e) => e.stopPropagation()}
                  className="mt-0.5 w-4 h-4 accent-blue-500 cursor-pointer flex-shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${TASK_TYPE_COLOR[task.type] ?? 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300'}`}>
                      {TASK_TYPE_LABEL[task.type] ?? task.type}
                    </span>
                    <span className="text-xs text-gray-400">{task.duration_min}分钟</span>
                  </div>
                  <p className={`text-sm mt-1 ${checked ? 'line-through text-gray-400 dark:text-gray-500' : 'text-gray-700 dark:text-gray-200'}`}>
                    {task.title}
                  </p>
                  {task.description && (
                    <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{task.description}</p>
                  )}
                  {/* existing note preview */}
                  {checked && existingNote && !isNoteOpen && (
                    <div
                      className="mt-1.5 text-xs text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 rounded px-2 py-1 cursor-pointer hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
                      onClick={(e) => { e.stopPropagation(); openNote(task); }}
                    >
                      💡 {existingNote}
                    </div>
                  )}
                  {/* add note button for checked tasks without note */}
                  {checked && !existingNote && !isNoteOpen && (
                    <button
                      className="mt-1.5 text-xs text-gray-400 hover:text-blue-500 transition-colors"
                      onClick={(e) => { e.stopPropagation(); openNote(task); }}
                    >
                      + 添加完成感悟
                    </button>
                  )}
                </div>
              </div>
              {/* note input panel */}
              {isNoteOpen && (
                <div className="mx-2 mb-2 p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg" onClick={(e) => e.stopPropagation()}>
                  <textarea
                    autoFocus
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    placeholder="写下你的完成总结或学习感悟…"
                    className="w-full text-xs text-gray-700 dark:text-gray-200 bg-transparent resize-none outline-none placeholder-gray-400 dark:placeholder-gray-500 min-h-[60px]"
                    rows={3}
                  />
                  <div className="flex items-center justify-end gap-2 mt-1">
                    <button
                      onClick={() => setNoteInputTaskId(null)}
                      className="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                    >
                      取消
                    </button>
                    <button
                      onClick={() => saveNote(task.id)}
                      disabled={savingNote}
                      className="text-xs text-white bg-blue-500 hover:bg-blue-600 disabled:opacity-50 px-3 py-1 rounded-md transition-colors"
                    >
                      {savingNote ? '保存中…' : '保存'}
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

interface WeekCardProps {
  week: Omit<PlanWeek, 'days'>;
  isSelected: boolean;
  onClick: () => void;
}

const WeekCard: React.FC<WeekCardProps> = ({ week, isSelected, onClick }) => (
  <div
    onClick={onClick}
    className={`border rounded-xl p-4 cursor-pointer transition-all ${
      isSelected
        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 shadow-sm'
        : 'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 hover:border-blue-300 hover:shadow-sm'
    }`}
  >
    <div className="flex items-center gap-2 mb-2">
      <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
        isSelected ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300'
      }`}>
        第{week.week_number}周
      </span>
      {week.phase_ref && (
        <span className="text-xs text-gray-400">{week.phase_ref}</span>
      )}
    </div>
    <p className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{week.theme}</p>
    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{week.focus}</p>
    <ul className="mt-2 space-y-1">
      {(week.weekly_goals ?? []).map((g, i) => (
        <li key={i} className="text-xs text-gray-600 dark:text-gray-300 flex items-start gap-1">
          <span className="text-blue-400 mt-0.5">•</span>
          <span>{g}</span>
        </li>
      ))}
    </ul>
  </div>
);

// ── Step 类型 ─────────────────────────────────────────────────────────
// 'idle'         — 没有计划，显示空状态
// 'config'       — 新建配置表单
// 'weekly_loading' — 正在生成周计划
// 'weekly_review'  — 确认周计划
// 'daily_loading'  — 正在生成每日任务
// 'progress'     — 主视图：展示计划进度

type Step = 'idle' | 'config' | 'weekly_loading' | 'weekly_review' | 'daily_loading' | 'progress';

// ── 主页面 ────────────────────────────────────────────────────────────

const PlanProgress: React.FC = () => {
  const { message } = App.useApp();
  const { assessmentId, planData, selectedCareer, currentPlanId, setCurrentPlanId, cachedPlan, setCachedPlan } = useAppStore();

  // ── 从 store 缓存恢复初始状态（避免导航返回时闪烁） ───
  const hasCached = !!(cachedPlan && cachedPlan.status === 'daily_ready');
  const [step, setStep] = useState<Step>(() => hasCached ? 'progress' : 'idle');
  const [initializing, setInitializing] = useState(() => !hasCached);

  // 历史计划
  const [historyPlans, setHistoryPlans] = useState<PlanListItem[]>([]);

  // 配置表单
  const [durationWeeks, setDurationWeeks] = useState(4);
  const [startDate, setStartDate] = useState(today());

  // weekly review
  const [weeklyDraft, setWeeklyDraft] = useState<Omit<PlanWeek, 'days'>[]>([]);
  const [pendingPlanId, setPendingPlanId] = useState<string | null>(null);
  const [selectedWeekForReview, setSelectedWeekForReview] = useState(0);

  // progress view — 叠加所有已完成的计划（从缓存恢复）
  const [allPlans, setAllPlans] = useState<PlanSchedule[]>(() =>
    hasCached ? [cachedPlan!] : [],
  );
  const [selectedWeek, setSelectedWeek] = useState(0);

  const [error, setError] = useState<string | null>(null);
  const [dailyPollCount, setDailyPollCount] = useState(0);

  // 把所有计划的周扁平化为一个数组，每周记住所属 planId
  // 全局按 week_number 升序（跨计划），保证展示顺序 1→2→3…
  const flatWeeks = useMemo(
    () =>
      allPlans
        .flatMap(p => p.weeks.map(w => ({ ...w, _planId: p.plan_id })))
        .sort((a, b) => a.week_number - b.week_number),
    [allPlans],
  );

  // 首次加载时，定位到第一个还有未完成任务的周（顺序本身不变）
  const didAutoJumpRef = React.useRef(false);
  useEffect(() => {
    if (didAutoJumpRef.current) return;
    if (flatWeeks.length === 0) return;
    const firstPending = flatWeeks.findIndex(w => {
      const { done, total } = weekProgress(w);
      return total === 0 || done < total;
    });
    setSelectedWeek(firstPending >= 0 ? firstPending : 0);
    didAutoJumpRef.current = true;
  }, [flatWeeks]);

  // ── 初始化：加载该职业下所有计划（叠加展示） ────────────────────
  useEffect(() => {
    if (!assessmentId || !selectedCareer) {
      setInitializing(false);
      return;
    }

    api.listPlans(assessmentId, selectedCareer)
      .then(async (res) => {
        const planList = res.data.plans ?? [];
        setHistoryPlans(planList);

        if (planList.length === 0) {
          setInitializing(false);
          return;
        }

        // 加载所有 daily_ready 计划的完整数据
        const readyItems = planList.filter(p => p.status === 'daily_ready');
        const fullPlans = await Promise.all(
          readyItems.map(async (item) => {
            // 命中缓存则直接复用
            if (cachedPlan?.plan_id === item.plan_id && cachedPlan.status === 'daily_ready') {
              return cachedPlan;
            }
            try {
              return (await api.getPlan(item.plan_id)).data;
            } catch {
              return null;
            }
          }),
        );
        const validPlans = fullPlans.filter((p): p is PlanSchedule => p !== null);
        setAllPlans(validPlans);

        if (validPlans.length > 0) {
          const latest = validPlans[validPlans.length - 1];
          setCachedPlan(latest);
          setCurrentPlanId(latest.plan_id);
        }

        // 优先展示已完成计划；只有没有已完成计划时才恢复草稿/生成中状态
        const latest = planList[planList.length - 1];
        if (validPlans.length > 0) {
          // 有已完成的计划 → 直接展示进度，忽略遗留草稿
          setStep('progress');
        } else if (latest.status === 'generating_daily') {
          // 没有已完成计划，但最新的在生成中 → 继续轮询
          setPendingPlanId(latest.plan_id);
          setStep('daily_loading');
          setDailyPollCount(0);
        } else if (latest.status === 'daily_failed') {
          // 每日任务生成失败 → 允许重试
          setPendingPlanId(latest.plan_id);
          setError('每日任务生成失败，请点击重试');
          setStep('config');
        } else if (latest.status === 'weekly_draft') {
          // 没有已完成计划，最新的是草稿 → 让用户继续确认
          try {
            const r = await api.getPlan(latest.plan_id);
            setWeeklyDraft(
              [...r.data.weeks]
                .sort((a, b) => a.week_number - b.week_number)
                .map(({ days: _d, ...rest }) => rest),
            );
            setPendingPlanId(latest.plan_id);
            setStep('weekly_review');
          } catch {
            setStep('idle');
          }
        } else {
          setStep('idle');
        }
      })
      .catch(() => {
        // 降级：用 currentPlanId 尝试加载单个计划
        if (currentPlanId) {
          api.getPlan(currentPlanId)
            .then((r) => {
              if (r.data.status === 'daily_ready') {
                setAllPlans([r.data]);
                setCachedPlan(r.data);
                setStep('progress');
              }
            })
            .catch(() => setCurrentPlanId(null));
        }
      })
      .finally(() => setInitializing(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 轮询 daily 生成状态（最多 60 次 = 5 分钟） ────────────────────
  const MAX_POLL = 60;
  useEffect(() => {
    if (step !== 'daily_loading' || !pendingPlanId) return;
    if (dailyPollCount >= MAX_POLL) {
      setError('每日任务生成超时，请返回重新生成计划');
      setStep('config');
      return;
    }
    const id = setTimeout(async () => {
      try {
        const res = await api.getPlan(pendingPlanId);
        if (res.data.status === 'daily_ready') {
          setAllPlans(prev => [...prev, res.data]);
          setCachedPlan(res.data);
          setCurrentPlanId(pendingPlanId);
          setHistoryPlans(prev => [
            ...prev,
            { plan_id: res.data.plan_id, duration_weeks: res.data.duration_weeks, start_date: res.data.start_date, status: res.data.status, created_at: new Date().toISOString() },
          ]);
          setStep('progress');
        } else if (res.data.status === 'daily_failed') {
          setError('每日任务生成失败，请点击重试');
          setStep('config');
        } else {
          setDailyPollCount((c) => c + 1);
        }
      } catch {
        setDailyPollCount((c) => c + 1);
      }
    }, 5000);
    return () => clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, dailyPollCount, pendingPlanId]);

  // ── 操作 ─────────────────────────────────────────────────────────
  const handleGenWeekly = useCallback(async () => {
    if (!assessmentId || !selectedCareer) return;
    setError(null);
    setStep('weekly_loading');
    try {
      const res = await api.createWeeklyPlan(assessmentId, selectedCareer, durationWeeks, startDate);
      setWeeklyDraft([...res.data.weeks].sort((a, b) => a.week_number - b.week_number));
      setPendingPlanId(res.data.plan_id);
      setStep('weekly_review');
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '生成失败，请重试';
      setError(msg);
      setStep('config');
    }
  }, [assessmentId, selectedCareer, durationWeeks, startDate]);

  const handleConfirm = useCallback(async () => {
    if (!pendingPlanId) return;
    setError(null);
    setStep('daily_loading');
    setDailyPollCount(0);
    try {
      await api.confirmPlan(pendingPlanId);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '确认失败，请重试';
      setError(msg);
      setStep('weekly_review');
    }
  }, [pendingPlanId]);

  const handleDayUpdated = useCallback((planId: string, weekNum: number, dayNum: number, completedIds: string[], taskNotes: Record<string, string>) => {
    setAllPlans(prev => prev.map(p => {
      if (p.plan_id !== planId) return p;
      const updated = {
        ...p,
        weeks: p.weeks.map(w =>
          w.week_number !== weekNum ? w : {
            ...w,
            days: w.days.map(d =>
              d.day_number !== dayNum ? d : { ...d, completed_ids: completedIds, task_notes: taskNotes }
            ),
          }
        ),
      };
      setCachedPlan(updated);
      return updated;
    }));
  }, [setCachedPlan]);

  const startNewPlan = useCallback(() => {
    setDurationWeeks(4);
    // 默认从最近一个计划的结束日期开始；没有历史计划则用今天
    if (historyPlans.length > 0) {
      const last = historyPlans[historyPlans.length - 1];
      const end = planEndDate(last);
      setStartDate(end >= today() ? end : today());
    } else {
      setStartDate(today());
    }
    setError(null);
    setStep('config');
  }, [historyPlans]);

  const [deletingWeek, setDeletingWeek] = useState<number | null>(null);
  const handleDeleteWeek = useCallback(async (planId: string, weekNumber: number) => {
    if (!window.confirm(`确定要删除第 ${weekNumber} 周吗？该周的每日任务和打卡记录将一并清除，不可撤销。`)) {
      return;
    }
    setDeletingWeek(weekNumber);
    try {
      const { data } = await api.deletePlanWeek(planId, weekNumber);
      if (data.plan_deleted) {
        // 该计划所有周都删完了，从 allPlans 移除
        setAllPlans(prev => {
          const next = prev.filter(p => p.plan_id !== planId);
          if (next.length === 0) {
            setCurrentPlanId(null);
            setCachedPlan(null);
            setStep('idle');
          }
          return next;
        });
        setHistoryPlans(prev => prev.filter(p => p.plan_id !== planId));
      } else {
        // 仅移除该周
        setAllPlans(prev => prev.map(p => {
          if (p.plan_id !== planId) return p;
          const updated = { ...p, weeks: p.weeks.filter(w => w.week_number !== weekNumber) };
          setCachedPlan(updated);
          return updated;
        }));
      }
      // 修正 selectedWeek 索引
      setSelectedWeek(idx => Math.max(0, idx - 1));
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '删除失败，请重试';
      setError(msg);
    } finally {
      setDeletingWeek(null);
    }
  }, [setCurrentPlanId, setCachedPlan]);

  // ── 前置检查 ──────────────────────────────────────────────────────
  if (!planData || !selectedCareer) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 dark:text-gray-400">
        <div className="text-center">
          <p className="text-lg font-medium mb-2">请先完成职业规划</p>
          <p className="text-sm">前往「职业规划」页面，选择目标职业并生成规划报告后，再来制定每日计划。</p>
        </div>
      </div>
    );
  }

  if (initializing) {
    return <PlanProgressSkeleton />;
  }

  // ── Step: idle（无计划） ──────────────────────────────────────────
  if (step === 'idle') {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-16 h-16 bg-blue-50 dark:bg-blue-900/20 rounded-2xl flex items-center justify-center mx-auto mb-4">
            <Plus size={28} className="text-blue-400" />
          </div>
          <p className="text-gray-700 dark:text-gray-200 font-medium mb-1">还没有计划</p>
          {error ? (
            <p className="text-sm text-red-500 mb-4">{error}</p>
          ) : (
            <p className="text-sm text-gray-400 mb-6">基于你的职业规划报告，AI 将为你生成可执行的每日任务</p>
          )}
          <button
            onClick={startNewPlan}
            className="bg-blue-600 hover:bg-blue-700 text-white font-medium px-6 py-2.5 rounded-xl transition-colors"
          >
            新建计划
          </button>
        </div>
      </div>
    );
  }

  // ── Step: config ──────────────────────────────────────────────────
  if (step === 'config') {
    return (
      <div className="max-w-lg mx-auto mt-12 px-4">
        <button
          onClick={() => setStep(allPlans.length > 0 ? 'progress' : 'idle')}
          className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 mb-6 transition-colors"
        >
          <ArrowLeft size={16} />
          返回
        </button>

        <h1 className="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-1">新建计划</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">配置计划参数，AI 将为你生成周计划概览供确认</p>

        {historyPlans.length > 0 && (
          <div className="mb-6 text-sm text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-3">
            已有 <b>{historyPlans.length}</b> 个历史计划，新计划将基于已有进度进行增量规划，避免重复内容。
            开始日期已自动设为上期计划结束日。
          </div>
        )}

        {error && (
          <div className="mb-4 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3 flex items-center justify-between">
            <span>{error}</span>
            {pendingPlanId && (
              <button
                onClick={async () => {
                  setError(null);
                  setStep('daily_loading');
                  setDailyPollCount(0);
                  try {
                    await api.retryDailyTasks(pendingPlanId);
                  } catch {
                    setError('重试请求失败，请稍后再试');
                    setStep('config');
                  }
                }}
                className="ml-3 px-3 py-1 bg-red-600 text-white text-xs rounded-lg hover:bg-red-700 transition-colors flex-shrink-0"
              >
                重试生成
              </button>
            )}
          </div>
        )}

        <div className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">
              计划时长：<span className="text-blue-600 font-bold">{durationWeeks} 周</span>
            </label>
            <input
              type="range"
              min={1} max={12} step={1}
              value={durationWeeks}
              onChange={(e) => setDurationWeeks(Number(e.target.value))}
              className="w-full accent-blue-500"
            />
            <div className="flex justify-between text-xs text-gray-400 mt-1">
              <span>1周</span><span>3个月（12周）</span>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">开始日期</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm w-full bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:border-blue-400"
            />
          </div>

          <button
            onClick={handleGenWeekly}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-3 rounded-xl transition-colors"
          >
            生成周计划概览
          </button>
        </div>
      </div>
    );
  }

  // ── Step: weekly_loading ──────────────────────────────────────────
  if (step === 'weekly_loading') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-gray-500 dark:text-gray-400">
        <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm">正在生成周计划概览，约需 20 秒…</p>
      </div>
    );
  }

  // ── Step: weekly_review ───────────────────────────────────────────
  if (step === 'weekly_review') {
    return (
      <div className="max-w-2xl mx-auto px-4 py-8">
        <button
          onClick={() => setStep('config')}
          className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 mb-6 transition-colors"
        >
          <ArrowLeft size={16} />
          返回修改配置
        </button>

        <h1 className="text-xl font-bold text-gray-800 dark:text-gray-100 mb-1">确认周计划</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
          AI 已生成 {weeklyDraft.length} 周计划概览，确认无误后将自动生成每日详细任务
        </p>

        {error && (
          <div className="mb-4 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3">
            {error}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 mb-6">
          {weeklyDraft.map((week, i) => (
            <WeekCard
              key={week.week_number}
              week={week}
              isSelected={selectedWeekForReview === i}
              onClick={() => setSelectedWeekForReview(i)}
            />
          ))}
        </div>

        <button
          onClick={handleConfirm}
          className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-3 rounded-xl transition-colors"
        >
          确认，生成每日计划
        </button>
      </div>
    );
  }

  // ── Step: daily_loading ───────────────────────────────────────────
  if (step === 'daily_loading') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-gray-500 dark:text-gray-400">
        <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm">正在为每周生成每日任务，每周约需 15 秒…</p>
        <p className="text-xs text-gray-400 dark:text-gray-500">完成后将自动跳转，请耐心等待</p>
      </div>
    );
  }

  // ── Step: progress（主视图） ──────────────────────────────────────
  if (allPlans.length === 0) return null;

  // 汇总所有计划的进度
  let totalDone = 0, totalTotal = 0;
  for (const p of allPlans) {
    const r = calcProgress(p);
    totalDone += r.done;
    totalTotal += r.total;
  }

  const currentFlatWeek = flatWeeks[selectedWeek];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 顶部总进度 */}
      <div className="flex-shrink-0 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-600 px-6 py-4">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h1 className="text-lg font-bold text-gray-800 dark:text-gray-100">计划进度</h1>
            <p className="text-xs text-gray-400 mt-0.5">
              共 {allPlans.length} 个计划 · {flatWeeks.length} 周
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <span className="text-2xl font-bold text-blue-600">
                {totalTotal === 0 ? 0 : Math.round((totalDone / totalTotal) * 100)}%
              </span>
              <p className="text-xs text-gray-400">{totalDone}/{totalTotal} 任务完成</p>
            </div>
            <button
              onClick={startNewPlan}
              className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-blue-600 border border-gray-200 dark:border-gray-600 hover:border-blue-300 px-3 py-1.5 rounded-lg transition-colors"
            >
              <Plus size={14} />
              新建计划
            </button>
          </div>
        </div>
        <ProgressBar value={totalDone} max={totalTotal} />
      </div>

      {/* 周标签 — 所有计划的周扁平展示，不同计划间加分隔线 */}
      <div className="flex-shrink-0 flex items-center gap-2 px-6 py-3 border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-700 overflow-x-auto">
        {flatWeeks.map((fw, i) => {
          const wp = weekProgress(fw);
          const active = selectedWeek === i;
          const isNewPlan = i > 0 && fw._planId !== flatWeeks[i - 1]._planId;
          return (
            <React.Fragment key={`${fw._planId}-${fw.week_number}`}>
              {isNewPlan && <div className="w-px h-8 bg-gray-300 dark:bg-gray-500 mx-1 flex-shrink-0" />}
              <button
                onClick={() => setSelectedWeek(i)}
                className={`flex-shrink-0 flex flex-col items-center px-4 py-2 rounded-lg text-xs transition-all ${
                  active
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:border-blue-300'
                }`}
              >
                <span className="font-medium">第{fw.week_number}周</span>
                <span className={active ? 'text-blue-100' : 'text-gray-400'}>
                  {wp.done}/{wp.total}
                </span>
              </button>
            </React.Fragment>
          );
        })}
      </div>

      {/* 周内容 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {currentFlatWeek && (
          <>
            <div className="mb-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <h2 className="text-base font-bold text-gray-800 dark:text-gray-100">{currentFlatWeek.theme}</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{currentFlatWeek.focus}</p>
                </div>
                <button
                  onClick={() => handleDeleteWeek(currentFlatWeek._planId, currentFlatWeek.week_number)}
                  disabled={deletingWeek !== null}
                  className="flex-shrink-0 flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-red-600 border border-gray-200 dark:border-gray-600 hover:border-red-300 px-2.5 py-1 rounded-lg transition-colors disabled:opacity-50"
                >
                  <Trash2 size={12} />
                  {deletingWeek === currentFlatWeek.week_number ? '删除中…' : '删除本周'}
                </button>
              </div>
              <div className="flex items-center gap-2 mt-2">
                {(() => {
                  const wp = weekProgress(currentFlatWeek);
                  return (
                    <>
                      <ProgressBar value={wp.done} max={wp.total} className="flex-1" />
                      <span className="text-xs text-gray-400 flex-shrink-0">
                        {wp.done}/{wp.total}
                      </span>
                    </>
                  );
                })()}
              </div>
            </div>

            {currentFlatWeek.days.length === 0 ? (
              <div className="text-center text-gray-400 text-sm py-10">
                <p>每日任务生成中，请稍后刷新…</p>
                <button
                  onClick={async () => {
                    try {
                      await api.retryDailyTasks(currentFlatWeek._planId);
                      window.location.reload();
                    } catch {
                      message.error('重新生成失败，请稍后重试');
                    }
                  }}
                  className="mt-3 px-4 py-1.5 bg-blue-500 text-white text-xs rounded-lg hover:bg-blue-600 transition-colors"
                >
                  重新生成
                </button>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {currentFlatWeek.days.map((day) => (
                  <DayCard
                    key={day.day_number}
                    planId={currentFlatWeek._planId}
                    week={currentFlatWeek.week_number}
                    day={day}
                    onUpdated={handleDayUpdated}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default PlanProgress;
