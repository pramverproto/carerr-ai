import React, { useState, useEffect, useCallback } from 'react';
import { FolderOpen, Trash2, ChevronRight, User, BarChart3, Briefcase, CalendarCheck, Clock } from 'lucide-react';
import { message } from 'antd';
import { useAppStore } from '@/store/appStore';
import { api } from '@/api/client';
import type { ArchiveItem, ArchiveDetail, Milestone } from '@/types';

// ── 维度中文名映射 ─────────────────────────────────────────────────
const DIM_LABELS: Record<string, string> = {
  skills: '技能',
  knowledge: '知识',
  abilities: '认知能力',
  work_styles: '工作特质',
  interests: '职业兴趣',
  work_values: '工作价值观',
};

// ── 里程碑图标 & 颜色 ─────────────────────────────────────────────
const MILESTONE_STYLE: Record<string, { color: string; bg: string }> = {
  assessment: { color: 'text-blue-600', bg: 'bg-blue-100' },
  career_plan: { color: 'text-purple-600', bg: 'bg-purple-100' },
  task_completed: { color: 'text-green-600', bg: 'bg-green-100' },
  week_completed: { color: 'text-emerald-600', bg: 'bg-emerald-100' },
};

type Tab = 'profile' | 'dimensions' | 'careers' | 'plans';

const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
  { key: 'profile', label: '个人信息', icon: <User size={14} /> },
  { key: 'dimensions', label: '能力维度', icon: <BarChart3 size={14} /> },
  { key: 'careers', label: '职业规划', icon: <Briefcase size={14} /> },
  { key: 'plans', label: '计划进度', icon: <CalendarCheck size={14} /> },
];

const Archive: React.FC = () => {
  const { assessmentId: currentAssessmentId, setAssessmentId, resetDownstream } = useAppStore();

  const [loading, setLoading] = useState(true);
  const [list, setList] = useState<ArchiveItem[]>([]);
  const [milestones, setMilestones] = useState<Milestone[]>([]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArchiveDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>('profile');

  const [deleting, setDeleting] = useState<string | null>(null);

  // ── 初始化加载 ────────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([
      api.archiveList().then(r => setList(r.data.assessments)),
      api.archiveMilestones().then(r => setMilestones(r.data.milestones)),
    ])
      .catch(() => { message.error('档案加载失败，请刷新重试'); })
      .finally(() => setLoading(false));
  }, []);

  // ── 选中评估 → 加载详情 ─────────────────────────────────────────
  const selectAssessment = useCallback(async (id: string) => {
    setSelectedId(id);
    setDetailLoading(true);
    setDetail(null);
    try {
      const res = await api.archiveDetail(id);
      setDetail(res.data);
    } catch {
      message.error('详情加载失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // ── 切换为当前活跃评估 ─────────────────────────────────────────
  const switchToCurrent = useCallback((id: string) => {
    resetDownstream();
    setAssessmentId(id);
  }, [resetDownstream, setAssessmentId]);

  // ── 删除评估 ─────────────────────────────────────────────────────
  const handleDelete = useCallback(async (id: string) => {
    if (!window.confirm('确定要删除该评估吗？所有关联数据（能力报告、职业规划、计划进度）将被永久删除，不可撤销。')) {
      return;
    }
    setDeleting(id);
    try {
      await api.archiveDelete(id);
      setList(prev => prev.filter(a => a.assessment_id !== id));
      setMilestones(prev => prev.filter(m => m.assessment_id !== id));
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
      if (currentAssessmentId === id) {
        resetDownstream();
        setAssessmentId(null);
      }
    } catch {
      message.error('删除失败，请重试');
    } finally {
      setDeleting(null);
    }
  }, [selectedId, currentAssessmentId, resetDownstream, setAssessmentId]);

  // ── Loading ─────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // ── 空状态 ─────────────────────────────────────────────────────
  if (list.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        <div className="text-center">
          <FolderOpen size={48} className="mx-auto mb-4 text-gray-300" />
          <p className="text-lg font-medium mb-2">暂无档案记录</p>
          <p className="text-sm">完成一次能力评估后，您的成长档案将在这里展示。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── 左侧：评估列表 + 里程碑 ──────────────────────────── */}
      <div className="w-80 flex-shrink-0 border-r border-gray-200 flex flex-col bg-gray-50 overflow-y-auto">
        <div className="px-4 py-4 border-b border-gray-200 bg-white">
          <h1 className="text-lg font-bold text-gray-800 flex items-center gap-2">
            <FolderOpen size={20} />
            成长档案
          </h1>
          <p className="text-xs text-gray-400 mt-1">共 {list.length} 次评估</p>
        </div>

        {/* 评估卡片列表 */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {list.map(item => {
            const isCurrent = item.assessment_id === currentAssessmentId;
            const isSelected = item.assessment_id === selectedId;
            return (
              <div
                key={item.assessment_id}
                onClick={() => selectAssessment(item.assessment_id)}
                className={`relative p-3 rounded-lg cursor-pointer transition-all border ${
                  isSelected
                    ? 'border-blue-400 bg-blue-50 shadow-sm'
                    : 'border-gray-200 bg-white hover:border-blue-200 hover:shadow-sm'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm text-gray-800 truncate">
                        {item.name || '未命名'}
                      </span>
                      {isCurrent && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-600 rounded font-medium flex-shrink-0">
                          当前
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 mt-0.5 truncate">
                      {item.current_title || item.education || '—'}
                    </p>
                    <div className="flex items-center gap-3 mt-1.5 text-[11px] text-gray-400">
                      <span>{item.career_count} 个职业规划</span>
                      <span>{item.plan_count} 个计划</span>
                    </div>
                    <p className="text-[11px] text-gray-400 mt-1">
                      {item.created_at ? new Date(item.created_at).toLocaleDateString('zh-CN') : '—'}
                    </p>
                  </div>
                  <div className="flex flex-col items-center gap-1 flex-shrink-0">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                      item.status === 'done'
                        ? 'bg-green-100 text-green-600'
                        : item.status === 'failed'
                          ? 'bg-red-100 text-red-600'
                          : 'bg-yellow-100 text-yellow-600'
                    }`}>
                      {item.status === 'done' ? '完成' : item.status === 'failed' ? '失败' : item.status}
                    </span>
                    <ChevronRight size={14} className={`transition-colors ${isSelected ? 'text-blue-400' : 'text-gray-300'}`} />
                  </div>
                </div>

                {/* 操作按钮 */}
                <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-100">
                  {!isCurrent && item.status === 'done' && (
                    <button
                      onClick={(e) => { e.stopPropagation(); switchToCurrent(item.assessment_id); }}
                      className="text-[11px] text-blue-600 hover:text-blue-800 transition-colors"
                    >
                      切换为当前
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(item.assessment_id); }}
                    disabled={deleting === item.assessment_id}
                    className="text-[11px] text-gray-400 hover:text-red-500 transition-colors disabled:opacity-50 ml-auto flex items-center gap-1"
                  >
                    <Trash2 size={11} />
                    {deleting === item.assessment_id ? '删除中…' : '删除'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {/* 里程碑时间线 */}
        {milestones.length > 0 && (
          <div className="border-t border-gray-200 bg-white px-4 py-3">
            <h3 className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1.5">
              <Clock size={12} />
              成长里程碑
            </h3>
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {milestones.slice(0, 20).map((m, i) => {
                const style = MILESTONE_STYLE[m.type] || MILESTONE_STYLE.assessment;
                return (
                  <div key={i} className="flex items-start gap-2">
                    <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${style.bg}`} />
                    <div className="flex-1 min-w-0">
                      <p className={`text-[11px] font-medium ${style.color}`}>{m.title}</p>
                      <p className="text-[10px] text-gray-400 truncate">{m.description}</p>
                      <p className="text-[10px] text-gray-300">
                        {m.date ? new Date(m.date).toLocaleDateString('zh-CN') : '—'}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── 右侧：详情面板 ──────────────────────────────────── */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {!selectedId ? (
          <div className="flex items-center justify-center h-full text-gray-400">
            <div className="text-center">
              <ChevronRight size={40} className="mx-auto mb-2 text-gray-200" />
              <p className="text-sm">选择左侧的评估记录查看详情</p>
            </div>
          </div>
        ) : detailLoading ? (
          <div className="flex items-center justify-center h-full">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : detail ? (
          <>
            {/* Tab 栏 */}
            <div className="flex-shrink-0 flex items-center gap-1 px-6 py-3 border-b border-gray-200 bg-white">
              {TABS.map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all ${
                    activeTab === tab.key
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'text-gray-500 hover:bg-gray-100'
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Tab 内容 */}
            <div className="flex-1 overflow-y-auto p-6">
              {activeTab === 'profile' && <ProfileTab detail={detail} />}
              {activeTab === 'dimensions' && <DimensionsTab detail={detail} />}
              {activeTab === 'careers' && <CareersTab detail={detail} />}
              {activeTab === 'plans' && <PlansTab detail={detail} />}
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            加载失败，请重试
          </div>
        )}
      </div>
    </div>
  );
};

// ── Tab: 个人信息快照 ──────────────────────────────────────────────

const ProfileTab: React.FC<{ detail: ArchiveDetail }> = ({ detail }) => {
  const p = detail.profile;
  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-base font-bold text-gray-800 mb-3">基本信息</h2>
        <div className="grid grid-cols-2 gap-4">
          <InfoField label="姓名" value={p.name} />
          <InfoField label="年龄" value={p.age != null ? `${p.age} 岁` : undefined} />
          <InfoField label="学历" value={p.education} />
          <InfoField label="当前职位" value={p.current_title} />
          <InfoField label="工作年限" value={p.years_of_experience != null ? `${p.years_of_experience} 年` : undefined} />
        </div>
      </div>

      {p.skills.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">技能</h3>
          <div className="flex flex-wrap gap-1.5">
            {p.skills.map((s, i) => (
              <span key={i} className="text-xs px-2 py-1 bg-blue-50 text-blue-700 rounded">{s}</span>
            ))}
          </div>
        </div>
      )}

      {p.certifications && p.certifications.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">证书</h3>
          <div className="flex flex-wrap gap-1.5">
            {p.certifications.map((c, i) => (
              <span key={i} className="text-xs px-2 py-1 bg-green-50 text-green-700 rounded">{c}</span>
            ))}
          </div>
        </div>
      )}

      {p.experiences.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">工作经历</h3>
          <div className="space-y-3">
            {p.experiences.map((exp, i) => (
              <div key={i} className="border border-gray-200 rounded-lg p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-medium text-sm text-gray-800">{exp.company}</span>
                  <span className="text-xs text-gray-400">·</span>
                  <span className="text-sm text-gray-600">{exp.title}</span>
                </div>
                <p className="text-xs text-gray-400 mb-1">{exp.duration}</p>
                {exp.responsibilities?.length > 0 && (
                  <ul className="text-xs text-gray-600 space-y-0.5 list-disc list-inside">
                    {exp.responsibilities.map((r, j) => (
                      <li key={j}>{r}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {p.supplement && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">个人补充</h3>
          <p className="text-sm text-gray-600 whitespace-pre-wrap bg-gray-50 rounded-lg p-3">{p.supplement}</p>
        </div>
      )}

      <p className="text-xs text-gray-400">
        评估时间：{detail.created_at ? new Date(detail.created_at).toLocaleString('zh-CN') : '—'}
      </p>
    </div>
  );
};

const InfoField: React.FC<{ label: string; value?: string | null }> = ({ label, value }) => (
  <div>
    <p className="text-xs text-gray-400">{label}</p>
    <p className="text-sm text-gray-800">{value || '—'}</p>
  </div>
);

// ── Tab: 能力维度 ──────────────────────────────────────────────────

const DimensionsTab: React.FC<{ detail: ArchiveDetail }> = ({ detail }) => {
  const dims = detail.dimensions;
  const dimEntries = Object.entries(dims);

  if (dimEntries.length === 0) {
    return <p className="text-gray-400 text-sm">暂无维度数据</p>;
  }

  return (
    <div className="space-y-4 max-w-2xl">
      {/* 得分总览 */}
      <div className="grid grid-cols-3 gap-3">
        {dimEntries.map(([key, dim]) => (
          <div key={key} className="border border-gray-200 rounded-lg p-3 text-center">
            <p className="text-xs text-gray-500 mb-1">{DIM_LABELS[key] || key}</p>
            <p className="text-2xl font-bold text-gray-800">
              {dim.score != null ? dim.score.toFixed(1) : '—'}
            </p>
            {dim.confidence && (
              <p className="text-[10px] text-gray-400">置信度: {dim.confidence}</p>
            )}
          </div>
        ))}
      </div>

      {/* 详细信息 */}
      {dimEntries.map(([key, dim]) => (
        <div key={key} className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-800 mb-2">{DIM_LABELS[key] || key}</h3>
          {dim.summary && (
            <p className="text-xs text-gray-600 mb-2">{dim.summary}</p>
          )}
          <div className="grid grid-cols-2 gap-3">
            {dim.highlights.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-green-600 mb-1">亮点</p>
                <ul className="text-xs text-gray-600 space-y-0.5 list-disc list-inside">
                  {dim.highlights.map((h, i) => <li key={i}>{h}</li>)}
                </ul>
              </div>
            )}
            {dim.focus_areas.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-orange-600 mb-1">待提升</p>
                <ul className="text-xs text-gray-600 space-y-0.5 list-disc list-inside">
                  {dim.focus_areas.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

// ── Tab: 职业规划 ──────────────────────────────────────────────────

const CareersTab: React.FC<{ detail: ArchiveDetail }> = ({ detail }) => {
  if (detail.careers.length === 0) {
    return <p className="text-gray-400 text-sm">暂无职业规划记录</p>;
  }

  return (
    <div className="space-y-3 max-w-2xl">
      {detail.careers.map(c => (
        <div key={c.onetsoc_code} className="border border-gray-200 rounded-lg p-4 flex items-center justify-between">
          <div>
            <p className="font-medium text-sm text-gray-800">{c.title || c.onetsoc_code}</p>
            <p className="text-xs text-gray-400 mt-0.5">O*NET: {c.onetsoc_code}</p>
          </div>
          <div className="text-right">
            {c.match_score != null && (
              <p className="text-lg font-bold text-blue-600">{c.match_score.toFixed(1)}</p>
            )}
            {c.verdict && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                c.verdict === '高度匹配' ? 'bg-green-100 text-green-600' :
                c.verdict === '中高匹配' ? 'bg-blue-100 text-blue-600' :
                'bg-yellow-100 text-yellow-600'
              }`}>
                {c.verdict}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

// ── Tab: 计划进度 ──────────────────────────────────────────────────

const PlansTab: React.FC<{ detail: ArchiveDetail }> = ({ detail }) => {
  if (detail.plans.length === 0) {
    return <p className="text-gray-400 text-sm">暂无计划进度记录</p>;
  }

  return (
    <div className="space-y-3 max-w-2xl">
      {detail.plans.map(p => {
        const pct = p.total_tasks > 0 ? Math.round((p.completed_tasks / p.total_tasks) * 100) : 0;
        return (
          <div key={p.plan_id} className="border border-gray-200 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <p className="text-sm font-medium text-gray-800">
                  {p.duration_weeks} 周计划
                </p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {p.start_date || '—'} · {p.onetsoc_code}
                </p>
              </div>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                p.status === 'daily_ready' ? 'bg-green-100 text-green-600' :
                p.status === 'generating_daily' ? 'bg-yellow-100 text-yellow-600' :
                'bg-gray-100 text-gray-600'
              }`}>
                {p.status === 'daily_ready' ? '进行中' : p.status === 'generating_daily' ? '生成中' : p.status}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="text-xs text-gray-500 flex-shrink-0">
                {p.completed_tasks}/{p.total_tasks} ({pct}%)
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default Archive;
