import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Empty, Input, List, message, Modal, Progress, Space, Spin,
  Tag, Timeline, Tooltip, Typography,
} from 'antd';
import {
  BookOpen, CheckCircle2, ChevronDown, ChevronRight, Clock, Compass,
  History, PencilLine, Plus, RefreshCw, Rocket, Sparkles, Target,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { api } from '@/api/client';
import { useAppStore } from '@/store/appStore';
import type {
  LearnModule, LearnOutline, LearnPlanProgress, LearnPlanRoadmap, LearnTask,
  RecentDoneTask,
} from '@/types';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

type Phase =
  | 'idle'             // 进入页面默认态：等用户点击「查看任务计划」
  | 'loading'          // 拉 /plan/current 中
  | 'no_career'        // 用户还没在 CareerPlan 页选 stage
  | 'empty'            // 有 stage 还没生成 plan
  | 'outline_confirm'  // 大纲已生成等用户确认
  | 'planning'         // 正在生成 roadmap + Week1
  | 'ready'            // 正常使用态
  | 'error';           // 出错

interface ConfirmResult {
  grade_score: number;
  grade_comment: string;
  final_contribution: number;
}

// 任务类型 → 标签 + 颜色
const TYPE_META: Record<string, { label: string; color: string }> = {
  reading: { label: '阅读', color: 'blue' },
  coding: { label: '编码', color: 'green' },
  project: { label: '项目', color: 'purple' },
  exercise: { label: '练习', color: 'orange' },
  review: { label: '复盘', color: 'magenta' },
};

const PlanProgress: React.FC = () => {
  const navigate = useNavigate();
  const { assessmentId, selectedCareer, matchData } = useAppStore();

  const [phase, setPhase] = useState<Phase>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const [planId, setPlanId] = useState<string | null>(null);
  const [outline, setOutline] = useState<LearnOutline | null>(null);
  const [roadmap, setRoadmap] = useState<LearnPlanRoadmap | null>(null);
  const [todayTasks, setTodayTasks] = useState<LearnTask[]>([]);
  const [moreTasks, setMoreTasks] = useState<LearnTask[]>([]);
  const [progress, setProgress] = useState<LearnPlanProgress | null>(null);
  const [recentDone, setRecentDone] = useState<RecentDoneTask[]>([]);

  const [userPreference, setUserPreference] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const [dialogTask, setDialogTask] = useState<LearnTask | null>(null);
  const [dialogReflection, setDialogReflection] = useState('');
  const [dialogSubmitting, setDialogSubmitting] = useState(false);
  const [lastConfirmResult, setLastConfirmResult] = useState<ConfirmResult | null>(null);

  // ─── 加载已就绪态的完整数据 ─────────────────────────────────────────
  const loadReady = useCallback(async (pid: string) => {
    const [roadRes, todayRes, progRes, doneRes] = await Promise.all([
      api.learnRoadmap(pid),
      api.learnToday(pid),
      api.learnProgress(pid),
      api.learnRecentDone(pid, 7),
    ]);
    setRoadmap(roadRes.data);
    setOutline({
      modules: roadRes.data.modules,
      total_weight: 100,
      estimated_weeks: roadRes.data.total_weeks || 0,
    });
    setTodayTasks(todayRes.data.tasks);
    setProgress(progRes.data);
    setRecentDone(doneRes.data.tasks);
    setPhase('ready');
  }, []);

  // ─── 初始加载：查有没有已存在的 plan ─────────────────────────────────
  const loadCurrent = useCallback(async () => {
    setPhase('loading');
    try {
      const res = await api.learnCurrent();
      const data = res.data;
      if (!data.plan_id) {
        if (!selectedCareer) {
          setPhase('no_career');
        } else {
          setPhase('empty');
        }
        return;
      }
      setPlanId(data.plan_id);
      if (data.status === 'pending' || data.status === 'error') {
        setOutline({
          modules: data.modules || [],
          total_weight: 100,
          estimated_weeks: data.estimated_weeks || 0,
        });
        setPhase('outline_confirm');
      } else if (data.status === 'ready') {
        await loadReady(data.plan_id);
      } else {
        setPhase('planning');
      }
    } catch (e: unknown) {
      console.error(e);
      setErrorMsg((e as { message?: string })?.message || '加载失败');
      setPhase('error');
    }
  }, [selectedCareer, loadReady]);

  useEffect(() => { loadCurrent(); }, [loadCurrent]);

  // ─── 生成中轮询：切换页面回来后恢复进度 ─────────────────────────────
  useEffect(() => {
    if (phase !== 'planning') return;
    const timer = setInterval(async () => {
      try {
        const res = await api.learnCurrent();
        const data = res.data;
        if (!data.plan_id) return;
        if (data.status === 'pending' || data.status === 'error') {
          setPlanId(data.plan_id);
          setOutline({ modules: data.modules || [], total_weight: 100, estimated_weeks: data.estimated_weeks || 0 });
          setPhase('outline_confirm');
          clearInterval(timer);
        } else if (data.status === 'ready') {
          setPlanId(data.plan_id);
          await loadReady(data.plan_id);
          clearInterval(timer);
        }
      } catch { /* 轮询失败静默忽略 */ }
    }, 3000);
    return () => clearInterval(timer);
  }, [phase, loadReady]);

  // ─── 生成大纲 ─────────────────────────────────────────────────────
  const handleGenerate = async () => {
    if (!assessmentId || !selectedCareer) return;
    setPhase('planning');
    setErrorMsg('');
    try {
      const res = await api.learnGenerate(assessmentId, selectedCareer, userPreference || undefined);
      setPlanId(res.data.plan_id);
      setOutline(res.data.outline);
      setPhase('outline_confirm');
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e as { message?: string })?.message || '生成失败';
      setErrorMsg(msg);
      setPhase('empty');
      message.error('大纲生成失败：' + msg);
    }
  };

  const handleRegenerate = async () => {
    if (!planId) return;
    setRegenerating(true);
    try {
      const res = await api.learnRegenerateOutline(planId, userPreference || undefined);
      setPlanId(res.data.plan_id);
      setOutline(res.data.outline);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '重新生成失败';
      message.error(msg);
    } finally {
      setRegenerating(false);
    }
  };

  // ─── 确认大纲 ─────────────────────────────────────────────────────
  const handleConfirm = async () => {
    if (!planId) return;
    setConfirming(true);
    setPhase('planning');
    try {
      await api.learnConfirmOutline(planId);
      await loadReady(planId);
      message.success('计划已就绪，开始你的第一周学习');
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '路线图生成失败';
      setErrorMsg(msg);
      setPhase('outline_confirm');
      message.error(msg);
    } finally {
      setConfirming(false);
    }
  };

  // ─── 完成任务 ─────────────────────────────────────────────────────
  /** 在本地任务列表里把某个 task 标记为已完成（沉底显示） */
  const markTaskDoneInState = useCallback((taskId: number) => {
    const mark = (tasks: LearnTask[]) =>
      tasks.map((t) => (t.id === taskId ? { ...t, status: 'done' as const } : t));
    setTodayTasks((prev) => mark(prev));
    setMoreTasks((prev) => mark(prev));
  }, []);

  /** 刷新路线图（完成任务可能触发下一周物化） */
  const refreshRoadmap = useCallback(async (pid: string) => {
    try {
      const roadRes = await api.learnRoadmap(pid);
      setRoadmap(roadRes.data);
    } catch {
      /* 非关键，忽略 */
    }
  }, []);

  /** 把刚完成的任务追加到最近完成列表头部 */
  const pushToRecentDone = useCallback((task: LearnTask, result: {
    grade_score: number; grade_comment: string; final_contribution: number;
  }) => {
    setRecentDone((prev) => [{
      id: task.id,
      week_id: task.week_id,
      title: task.title,
      task_type: task.task_type,
      actual_contribution: task.actual_contribution,
      grade_score: result.grade_score,
      grade_comment: result.grade_comment,
      final_contribution: result.final_contribution,
      reflection: null,
      completed_at: new Date().toISOString(),
      week_num: task.week_num,
      week_theme: task.week_theme,
    }, ...prev]);
  }, []);

  /** 直接完成：不写感悟，默认满分 */
  const completeDirectly = async (task: LearnTask) => {
    if (!planId) return;
    const hide = message.loading({ content: '提交中...', duration: 0 });
    try {
      const res = await api.learnCompleteTask(task.id, null);
      setProgress(res.data.progress);
      markTaskDoneInState(task.id);
      pushToRecentDone(task, res.data);
      hide();
      message.success(`+${res.data.final_contribution.toFixed(2)} 分 · 任务已完成`);
      refreshRoadmap(planId);
    } catch (e: unknown) {
      hide();
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '提交失败';
      message.error(msg);
    }
  };

  /** 打开感悟补充对话框（可选操作） */
  const openReflectionDialog = (task: LearnTask) => {
    setDialogTask(task);
    setDialogReflection('');
    setLastConfirmResult(null);
  };

  const submitReflection = async () => {
    if (!dialogTask || !planId) return;
    setDialogSubmitting(true);
    try {
      const reflection = dialogReflection.trim() || null;
      const res = await api.learnCompleteTask(dialogTask.id, reflection);
      setProgress(res.data.progress);
      setLastConfirmResult({
        grade_score: res.data.grade_score,
        grade_comment: res.data.grade_comment,
        final_contribution: res.data.final_contribution,
      });
      markTaskDoneInState(dialogTask.id);
      pushToRecentDone(dialogTask, res.data);
      refreshRoadmap(planId);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '提交失败';
      message.error(msg);
    } finally {
      setDialogSubmitting(false);
    }
  };

  const closeDialog = () => {
    setDialogTask(null);
    setDialogReflection('');
    setLastConfirmResult(null);
  };

  // ─── 新增任务（从队列里拿下一批 pending） ──────────────────────────
  const handleMore = async () => {
    if (!planId) return;
    const excludeIds = [...todayTasks, ...moreTasks].map((t) => t.id);
    try {
      const res = await api.learnMore(planId, excludeIds);
      if (res.data.tasks.length === 0) {
        message.info('暂时没有更多任务了，稍等下一周生成完成');
        return;
      }
      setMoreTasks((prev) => [...prev, ...res.data.tasks]);
      message.success(`已追加 ${res.data.tasks.length} 个任务`);
    } catch (e: unknown) {
      message.error((e as { message?: string })?.message || '加载失败');
    }
  };

  const handleRetryWeek = async (weekNum: number) => {
    if (!planId) return;
    message.loading({ content: `重试 Week ${weekNum} 物化...`, key: 'retry' });
    try {
      await api.learnRetryWeek(planId, weekNum);
      message.success({ content: `Week ${weekNum} 重试完成`, key: 'retry' });
      const roadRes = await api.learnRoadmap(planId);
      setRoadmap(roadRes.data);
    } catch (e: unknown) {
      message.error({
        content: (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          '重试失败',
        key: 'retry',
      });
    }
  };

  // ─── 渲染各阶段 ─────────────────────────────────────────────────────
  if (phase === 'idle' || phase === 'loading') {
    return <div className="flex items-center justify-center h-96"><Spin size="large" /></div>;
  }

  if (phase === 'no_career') {
    return (
      <div className="max-w-2xl mx-auto mt-12">
        <Empty description="请先在「职业规划」页面选择一条学习路线" />
        <div className="flex justify-center mt-4">
          <Button type="primary" icon={<Compass size={16} />} onClick={() => navigate('/career-plan')}>
            前往职业规划
          </Button>
        </div>
      </div>
    );
  }

  // 从 selectedCareer 解析 stage 名：
  //   - "manual:用户输入" → 直接取 ":" 后面的字符串
  //   - "path-xxx-sN" → 从 matchData 里查 stage.title
  const resolveStageTitleFromMatch = (code: string | null): string | undefined => {
    if (!code) return undefined;
    if (code.startsWith('manual:')) {
      return code.slice('manual:'.length).trim() || undefined;
    }
    const m = /^(.+)-s(\d+)$/.exec(code);
    if (!m) return undefined;
    const [, pathCode, stageStr] = m;
    const stageNum = parseInt(stageStr);
    const path = (matchData?.result?.recommended || []).find((p) => p.path_code === pathCode);
    return path?.stages.find((s) => s.stage === stageNum)?.title;
  };

  if (phase === 'empty') {
    const stageTitle = resolveStageTitleFromMatch(selectedCareer);

    return (
      <div className="max-w-3xl mx-auto mt-8 space-y-6">
        <div className="text-center space-y-3">
          <div className="flex items-center justify-center">
            <Rocket className="text-blue-500" size={40} />
          </div>
          <Title level={3} className="!mb-1">开始规划你的学习路径</Title>
          <Paragraph type="secondary">
            AI 将基于你的能力画像和目标岗位
            {stageTitle ? <Text strong className="mx-1">"{stageTitle}"</Text> : null}
            ，生成一份结构化的学习大纲。
          </Paragraph>
        </div>
        <Card>
          <Space direction="vertical" size="middle" className="w-full">
            <div>
              <Text>补充学习偏好（可选）</Text>
              <TextArea
                className="mt-2"
                placeholder="例如：想多学工程落地方向 / 跳过基础直接切入实战"
                value={userPreference}
                onChange={(e) => setUserPreference(e.target.value)}
                rows={3}
                maxLength={200}
                showCount
              />
            </div>
            <Button
              type="primary" size="large" block
              icon={<Sparkles size={16} />}
              onClick={handleGenerate}
              disabled={!selectedCareer}
            >
              生成详细计划
            </Button>
            <Text type="secondary" className="text-xs">
              生成过程需要约 20 秒，请稍候
            </Text>
          </Space>
        </Card>
      </div>
    );
  }

  if (phase === 'planning') {
    return (
      <div className="max-w-xl mx-auto mt-20 text-center space-y-4">
        <Spin size="large" />
        <Title level={4}>正在为你规划学习路线...</Title>
        <Paragraph type="secondary">
          AI 正在拆解目标并安排每周任务，这可能需要 30–120 秒
        </Paragraph>
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div className="max-w-xl mx-auto mt-12">
        <Alert
          type="error" showIcon message="加载失败" description={errorMsg}
          action={<Button onClick={loadCurrent}>重试</Button>}
        />
      </div>
    );
  }

  if (phase === 'outline_confirm' && outline) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        <Card>
          <Space direction="vertical" className="w-full" size="middle">
            <div>
              <Title level={4} className="!mb-1">学习路径大纲</Title>
              <Text type="secondary">
                共 {outline.modules.length} 个模块 · 约 {outline.estimated_weeks} 周 ·
                预计投入 {outline.modules.reduce((s, m) => s + (m.est_hours || 0), 0)} 小时
              </Text>
            </div>
            <ModuleList modules={outline.modules} />
            <div>
              <Text>学习偏好（可选，重新生成时会参考）</Text>
              <TextArea
                className="mt-2"
                value={userPreference}
                onChange={(e) => setUserPreference(e.target.value)}
                rows={2}
                placeholder="例如：想多学工程落地方向"
                maxLength={200}
              />
            </div>
            <Space>
              <Button
                icon={<RefreshCw size={14} />}
                loading={regenerating}
                onClick={handleRegenerate}
              >
                重新生成
              </Button>
              <Button
                type="primary"
                icon={<CheckCircle2 size={14} />}
                loading={confirming}
                onClick={handleConfirm}
              >
                确认，开始排课
              </Button>
            </Space>
          </Space>
        </Card>
      </div>
    );
  }

  // phase === 'ready'
  if (!roadmap || !progress) return null;

  // 排序：pending 在前（按 order_in_queue），done 沉底
  const allDisplayTasks = [...todayTasks, ...moreTasks].slice().sort((a, b) => {
    if (a.status !== b.status) return a.status === 'done' ? 1 : -1;
    return a.order_in_queue - b.order_in_queue;
  });
  // 当前周定位：优先用今日列表里 pending 任务所在的周；否则用第一个未完成的 ready 周；
  // 最后兜底是最早一个 ready 周
  const currentWeek = roadmap.weeks.find((w) =>
    todayTasks.some((t) => t.week_num === w.week_num && t.status === 'pending')
  )
    || roadmap.weeks.find((w) => w.daily_status === 'ready' && w.done_tasks < w.total_tasks)
    || roadmap.weeks.find((w) => w.daily_status === 'materializing')
    || roadmap.weeks.find((w) => w.daily_status === 'ready');

  // 优先用 matchData 里的岗位名；没有再用后端回填的 stage_title；最后兜底 code
  const liveStageTitle = resolveStageTitleFromMatch(roadmap.stage_code)
    || roadmap.stage_title
    || roadmap.stage_code;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <ProgressHeader
        progress={progress}
        stageTitle={liveStageTitle}
        totalWeeks={roadmap.total_weeks}
        currentWeekNum={currentWeek?.week_num}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card
            title={
              <Space>
                <Target size={18} />
                <span>今日焦点</span>
                {currentWeek && (
                  <Tag color="blue">
                    Week {currentWeek.week_num} · {currentWeek.theme}
                  </Tag>
                )}
              </Space>
            }
            extra={
              <Tooltip title="追加一批新任务">
                <Button size="small" icon={<Plus size={14} />} onClick={handleMore}>
                  新增任务
                </Button>
              </Tooltip>
            }
          >
            {allDisplayTasks.length === 0 ? (
              <Empty description="暂无待办任务，稍等下一周生成完成" />
            ) : (
              <List
                dataSource={allDisplayTasks}
                renderItem={(task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    onComplete={() => completeDirectly(task)}
                    onAddReflection={() => openReflectionDialog(task)}
                  />
                )}
              />
            )}
          </Card>

          <RecentDonePanel tasks={recentDone} />
        </div>

        <div className="lg:col-span-1">
          <Card title={<Space><BookOpen size={18} /><span>完整路线图</span></Space>}>
            <RoadmapTimeline
              roadmap={roadmap}
              onRetryWeek={handleRetryWeek}
            />
          </Card>
        </div>
      </div>

      <CompleteDialog
        task={dialogTask}
        reflection={dialogReflection}
        setReflection={setDialogReflection}
        submitting={dialogSubmitting}
        result={lastConfirmResult}
        onSubmit={submitReflection}
        onClose={closeDialog}
      />
    </div>
  );
};

// ====================================================================
//  子组件
// ====================================================================

const ModuleList: React.FC<{ modules: LearnModule[] }> = ({ modules }) => (
  <List
    dataSource={modules}
    renderItem={(m, idx) => (
      <List.Item key={m.id}>
        <div className="w-full">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <Tag color="blue">{idx + 1}</Tag>
                <Text strong>{m.title}</Text>
              </div>
              {m.completion_criteria && (
                <Paragraph type="secondary" className="!mb-0 !mt-1 text-sm">
                  ✓ {m.completion_criteria}
                </Paragraph>
              )}
            </div>
            <div className="text-right shrink-0">
              <div><Text strong>{m.weight.toFixed(0)}%</Text></div>
              <div><Text type="secondary" className="text-xs">{m.est_hours}h</Text></div>
            </div>
          </div>
        </div>
      </List.Item>
    )}
  />
);

const ProgressHeader: React.FC<{
  progress: LearnPlanProgress;
  stageTitle: string;
  totalWeeks: number | null;
  currentWeekNum?: number;
}> = ({ progress, stageTitle, totalWeeks, currentWeekNum }) => {
  const pct = Math.min(100, progress.total_pct);
  return (
    <Card>
      <Space direction="vertical" className="w-full">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <Text type="secondary" className="text-sm">当前目标</Text>
            <div><Text strong className="text-lg">{stageTitle}</Text></div>
          </div>
          <div className="text-right">
            <Text type="secondary" className="text-sm">
              {currentWeekNum ? `Week ${currentWeekNum}` : '—'}
              {totalWeeks ? ` / ${totalWeeks}` : ''}
            </Text>
            <div>
              <Text strong className="text-lg">{pct.toFixed(1)}%</Text>
            </div>
          </div>
        </div>
        <Progress
          percent={pct}
          status={pct >= 100 ? 'success' : 'active'}
          strokeColor={{ from: '#1677ff', to: '#52c41a' }}
        />
        <div className="flex gap-4 text-sm">
          <Text type="secondary">
            已完成 <Text strong>{progress.done_count}</Text> / {progress.total_count} 个任务
          </Text>
        </div>
      </Space>
    </Card>
  );
};

const TaskCard: React.FC<{
  task: LearnTask;
  onComplete: () => void;
  onAddReflection: () => void;
}> = ({ task, onComplete, onAddReflection }) => {
  const typeMeta = TYPE_META[task.task_type] || { label: task.task_type, color: 'default' };
  const isDone = task.status === 'done';
  return (
    <List.Item
      className={isDone ? 'opacity-60 bg-gray-50 dark:bg-gray-800/40' : ''}
      actions={isDone ? [
        <Tag key="done" color="success" icon={<CheckCircle2 size={12} />}>已完成</Tag>,
      ] : [
        <Tooltip key="reflect" title="可选：补充学习感悟（不影响得分）">
          <Button size="small" icon={<PencilLine size={14} />} onClick={onAddReflection}>
            补充感悟
          </Button>
        </Tooltip>,
        <Button
          key="done"
          type="primary"
          size="small"
          icon={<CheckCircle2 size={14} />}
          onClick={onComplete}
        >
          完成
        </Button>,
      ]}
    >
      <div className="w-full">
        <div className="flex items-center gap-2 flex-wrap">
          <Tag color={typeMeta.color}>{typeMeta.label}</Tag>
          <Text strong delete={isDone}>{task.title}</Text>
          <Tooltip title="预计用时">
            <Tag icon={<Clock size={12} />}>{task.est_minutes} 分钟</Tag>
          </Tooltip>
          <Tooltip title="完成后获得的进度值">
            <Tag color="gold">+{task.actual_contribution.toFixed(2)} 分</Tag>
          </Tooltip>
        </div>
        {task.description && !isDone && (
          <Paragraph type="secondary" className="!mb-0 !mt-1 text-sm">
            {task.description}
          </Paragraph>
        )}
        {task.completion_criteria && !isDone && (
          <Text type="secondary" className="text-xs">
            ✓ 完成标准：{task.completion_criteria}
          </Text>
        )}
      </div>
    </List.Item>
  );
};

/**
 * 根据 week 的任务完成数和 daily_status 推断"学习进度状态"。
 * 状态：
 *   - completed: 已物化且所有任务完成
 *   - in_progress: 已物化，有 done 也有 pending（= 当前周）
 *   - ready_unstarted: 已物化但一个任务都没做
 *   - materializing: 正在后台生成
 *   - future: skeleton 状态（还未生成）
 *   - error: 生成失败
 */
type WeekProgressState = 'completed' | 'in_progress' | 'ready_unstarted'
  | 'materializing' | 'future' | 'error';

function inferWeekState(w: { daily_status: string; total_tasks: number; done_tasks: number }): WeekProgressState {
  if (w.daily_status === 'error') return 'error';
  if (w.daily_status === 'skeleton') return 'future';
  if (w.daily_status === 'materializing') return 'materializing';
  // ready
  if (w.total_tasks === 0) return 'ready_unstarted';
  if (w.done_tasks >= w.total_tasks) return 'completed';
  if (w.done_tasks > 0) return 'in_progress';
  return 'ready_unstarted';
}

const WEEK_STATE_META: Record<WeekProgressState, { color: string; text: string }> = {
  completed:       { color: 'green',  text: '已完成' },
  in_progress:     { color: 'blue',   text: '进行中' },
  ready_unstarted: { color: 'cyan',   text: '待开始' },
  materializing:   { color: 'blue',   text: '生成中...' },
  future:          { color: 'gray',   text: '未展开' },
  error:           { color: 'red',    text: '生成失败' },
};

const RoadmapTimeline: React.FC<{
  roadmap: LearnPlanRoadmap;
  onRetryWeek: (weekNum: number) => void;
}> = ({ roadmap, onRetryWeek }) => {
  const items = useMemo(() => roadmap.weeks.map((w) => {
    const state = inferWeekState(w);
    const meta = WEEK_STATE_META[state];
    const isActive = state === 'in_progress';
    return {
      color: meta.color as 'green' | 'blue' | 'cyan' | 'gray' | 'red',
      dot: isActive ? (
        <span className="inline-block w-3 h-3 rounded-full bg-blue-500 animate-pulse" />
      ) : undefined,
      children: (
        <div className={isActive ? 'p-2 -m-2 rounded bg-blue-50 dark:bg-blue-900/20' : ''}>
          <div className="flex items-center gap-2 flex-wrap">
            <Text strong>Week {w.week_num}</Text>
            <Text className={state === 'completed' ? 'line-through opacity-60' : ''}>
              · {w.theme}
            </Text>
            <Tag color={meta.color}>{meta.text}</Tag>
            {w.total_tasks > 0 && state !== 'future' && (
              <Tag>{w.done_tasks}/{w.total_tasks} 任务</Tag>
            )}
            {state === 'error' && (
              <Button size="small" danger onClick={() => onRetryWeek(w.week_num)}>
                重试
              </Button>
            )}
          </div>
          {w.week_goal && state !== 'future' && (
            <Text type="secondary" className="text-sm block">{w.week_goal}</Text>
          )}
          <Text type="secondary" className="text-xs block">
            贡献 {w.weight_share.toFixed(1)}%
          </Text>
        </div>
      ),
    };
  }), [roadmap.weeks, onRetryWeek]);

  return <Timeline items={items} />;
};

const CompleteDialog: React.FC<{
  task: LearnTask | null;
  reflection: string;
  setReflection: (v: string) => void;
  submitting: boolean;
  result: ConfirmResult | null;
  onSubmit: () => void;
  onClose: () => void;
}> = ({ task, reflection, setReflection, submitting, result, onSubmit, onClose }) => {
  if (!task) return null;
  return (
    <Modal
      title={result ? '🎉 任务已完成' : `补充学习感悟：${task.title}`}
      open={!!task}
      onCancel={onClose}
      footer={
        result ? (
          <Button type="primary" onClick={onClose}>关闭</Button>
        ) : (
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" onClick={onSubmit} loading={submitting}>提交感悟并完成</Button>
          </Space>
        )
      }
      width={600}
      destroyOnClose
    >
      {!result ? (
        <Space direction="vertical" className="w-full">
          <Paragraph type="secondary">
            感悟是可选的自我记录。AI 会针对内容给出反馈评论。
            不填写直接点"完成"也可以拿到满分。
          </Paragraph>
          <TextArea
            value={reflection}
            onChange={(e) => setReflection(e.target.value)}
            rows={6}
            placeholder="可以记录：关键概念、实现细节、遇到的坑、收获的启发..."
            maxLength={1000}
            showCount
          />
        </Space>
      ) : (
        <Space direction="vertical" className="w-full">
          <div className="text-center">
            <div className="text-4xl font-bold text-blue-600">
              +{result.final_contribution.toFixed(2)}
            </div>
            <Text type="secondary">本次获得进度分</Text>
          </div>
          <Alert
            type={result.grade_score >= 0.9 ? 'success' : result.grade_score >= 0.7 ? 'info' : 'warning'}
            message={`AI 评分：${(result.grade_score * 100).toFixed(0)}%`}
            description={result.grade_comment}
            showIcon
          />
        </Space>
      )}
    </Modal>
  );
};

// ====================================================================
//  最近完成任务面板
// ====================================================================

/**
 * 把最近完成的任务按"昨日 / 更早"分桶。
 * 今日完成的不在这里展示（已由"今日焦点"的沉底灰化部分覆盖，避免重复）。
 */
function groupByDateBucket(tasks: RecentDoneTask[]): {
  label: string; items: RecentDoneTask[];
}[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400_000);
  const buckets: Record<string, RecentDoneTask[]> = { yesterday: [], earlier: [] };
  for (const t of tasks) {
    if (!t.completed_at) {
      buckets.earlier.push(t);
      continue;
    }
    const d = new Date(t.completed_at);
    if (d >= today) continue;                          // 今日的跳过，不重复展示
    else if (d >= yesterday) buckets.yesterday.push(t);
    else buckets.earlier.push(t);
  }
  const out: { label: string; items: RecentDoneTask[] }[] = [];
  if (buckets.yesterday.length) out.push({ label: '昨日完成', items: buckets.yesterday });
  if (buckets.earlier.length) out.push({ label: '更早（7 日内）', items: buckets.earlier });
  return out;
}

const RecentDonePanel: React.FC<{ tasks: RecentDoneTask[] }> = ({ tasks }) => {
  const [expanded, setExpanded] = useState(true);
  const buckets = groupByDateBucket(tasks);
  const historyTasks = buckets.flatMap((b) => b.items);
  // 如果筛掉今日之后没有任何历史任务，整块不展示
  if (historyTasks.length === 0) return null;
  const totalScore = historyTasks.reduce((s, t) => s + t.final_contribution, 0);

  return (
    <Card
      title={
        <Space>
          <History size={18} />
          <span>最近完成</span>
          <Tag color="green">{historyTasks.length} 个任务 · +{totalScore.toFixed(1)} 分</Tag>
        </Space>
      }
      extra={
        <Button
          size="small" type="text"
          icon={expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? '收起' : '展开'}
        </Button>
      }
    >
      {expanded && (
        <div className="space-y-4">
          {buckets.map((bucket) => (
            <div key={bucket.label}>
              <div className="mb-2">
                <Text strong className="text-sm">{bucket.label}</Text>
                <Text type="secondary" className="text-xs ml-2">
                  {bucket.items.length} 个 · +{bucket.items.reduce((s, t) => s + t.final_contribution, 0).toFixed(1)} 分
                </Text>
              </div>
              <List
                size="small"
                dataSource={bucket.items}
                renderItem={(t) => <DoneTaskRow key={t.id} task={t} />}
              />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};

const DoneTaskRow: React.FC<{ task: RecentDoneTask }> = ({ task }) => {
  const typeMeta = TYPE_META[task.task_type] || { label: task.task_type, color: 'default' };
  const scorePct = Math.round(task.grade_score * 100);
  const completedTime = task.completed_at
    ? new Date(task.completed_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : '';
  return (
    <List.Item className="!py-2">
      <div className="w-full flex items-start gap-3">
        <CheckCircle2 size={16} className="mt-1 text-green-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Tag color={typeMeta.color}>{typeMeta.label}</Tag>
            <Text className="text-sm">{task.title}</Text>
            <Tag>W{task.week_num}</Tag>
          </div>
          {task.grade_comment && (
            <Text type="secondary" className="text-xs block mt-0.5">
              💬 {task.grade_comment}
            </Text>
          )}
        </div>
        <div className="text-right shrink-0">
          <div className="text-sm font-semibold text-green-600">
            +{task.final_contribution.toFixed(2)}
          </div>
          <div className="text-xs text-gray-400">{scorePct}% · {completedTime}</div>
        </div>
      </div>
    </List.Item>
  );
};

export default PlanProgress;
