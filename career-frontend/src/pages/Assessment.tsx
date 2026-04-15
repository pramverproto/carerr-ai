import React, { useEffect, useRef, useState } from 'react';
import { Button, Tag, Spin, Alert, Collapse, Progress } from 'antd';
import { useNavigate } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import { useAppStore } from '@/store/appStore';
import { useLayoutStore } from '@/store/layoutStore';
import { api } from '@/api/client';
import DimBlock from '@/components/assessment/DimBlock';
import AssessmentSkeleton from '@/components/skeletons/AssessmentSkeleton';
import type { ReportBlocks } from '@/types';

const { Panel } = Collapse;

const ANCHORS = [
  { id: 'header',      label: '数据来源' },
  { id: 'radar',       label: '六维雷达' },
  { id: 'overview',    label: '综合画像' },
  { id: 'skills',      label: '技能' },
  { id: 'knowledge',   label: '知识' },
  { id: 'abilities',   label: '认知' },
  { id: 'work_styles', label: '特质' },
  { id: 'interests',   label: '兴趣' },
  { id: 'work_values', label: '价值观' },
  { id: 'action',      label: '行动建议' },
  { id: 'unlock',      label: '解锁' },
  { id: 'methodology', label: '方法论' },
];

const DIM_COLORS: Record<string, string> = {
  skills:      '#1677ff',
  knowledge:   '#52c41a',
  abilities:   '#722ed1',
  work_styles: '#fa8c16',
  interests:   '#eb2f96',
  work_values: '#13c2c2',
};

function scrollTo(id: string) {
  document.getElementById(`block-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function buildRadarOption(blocks: ReportBlocks, isDark = false) {
  const dims = blocks.radar?.dimensions || [];
  const indicator = dims.map((d) => ({ name: d.name.replace(' ', '\n'), max: 7 }));
  const values = dims.map((d) => d.score ?? 0);
  return {
    radar: {
      indicator,
      shape: 'polygon',
      splitNumber: 3,
      axisName: { color: isDark ? '#9CA3AF' : '#555', fontSize: 12 },
      splitLine: { lineStyle: { color: isDark ? '#374151' : '#e8e8e8' } },
      splitArea: { areaStyle: { color: isDark ? ['#1f2937', '#111827'] : ['#fff', '#f9fafb'] } },
      axisLine: { lineStyle: { color: isDark ? '#374151' : '#e8e8e8' } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: values,
        name: '能力画像',
        areaStyle: { opacity: 0.2, color: '#1677ff' },
        lineStyle: { color: '#1677ff', width: 2 },
        itemStyle: { color: '#1677ff' },
      }],
    }],
    tooltip: { trigger: 'item' },
  };
}

const Assessment: React.FC = () => {
  const navigate = useNavigate();
  const { assessmentId, reportData, setReportData } = useAppStore();
  const theme = useLayoutStore((s) => s.theme);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const fetchingRef = useRef(false);

  useEffect(() => {
    if (!assessmentId) return;
    // 缓存命中检查
    if (reportData?.assessment_id === assessmentId) return;
    // 已有请求进行中，不重复发起
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    setLoading(true);
    setError(null);
    api.getReport(assessmentId)
      .then((res) => { setReportData(res.data); })
      .catch((err) => {
        setError(err?.response?.data?.detail || err?.message || '获取报告失败');
      })
      .finally(() => { setLoading(false); fetchingRef.current = false; });
  }, [assessmentId]);

  if (!assessmentId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <p className="text-gray-500">暂无评估记录</p>
        <Button type="primary" onClick={() => navigate('/profile')}>
          开始评估
        </Button>
      </div>
    );
  }

  if (loading) {
    return <AssessmentSkeleton />;
  }

  if (error) {
    return (
      <Alert
        type="error"
        message="加载失败"
        description={error}
        showIcon
        action={
          <Button onClick={() => { setReportData(null); window.location.reload(); }}>
            重试
          </Button>
        }
      />
    );
  }

  if (!reportData) return null;

  const { blocks } = reportData;

  return (
    <div ref={mainRef} className="animate-fade-in">
      {/* 锚点导航 */}
      <div className="sticky top-0 z-10 bg-white dark:bg-gray-800 border-b border-gray-100 dark:border-gray-700 -mx-6 px-6 py-2 mb-6 flex gap-2 overflow-x-auto">
        {ANCHORS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => scrollTo(id)}
            className="text-xs text-gray-500 dark:text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 px-2 py-1 rounded whitespace-nowrap transition-colors"
          >
            {label}
          </button>
        ))}
      </div>

      {/* Block: header */}
      <section id="block-header" className="mb-6 bg-gray-50 dark:bg-gray-700 rounded-xl p-4 border border-gray-100 dark:border-gray-600">
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">
          评估 ID：<span className="font-mono text-gray-700 dark:text-gray-300">{blocks.header?.assessment_id}</span>
        </p>
        <div className="flex flex-wrap gap-2">
          {Object.entries(blocks.header?.data_sources || {}).map(([k, v]) => (
            <Tag key={k} color={v ? 'green' : 'default'}>
              {k}{v ? ' ✓' : ' ✗'}
            </Tag>
          ))}
        </div>
      </section>

      {/* Block: radar */}
      <section id="block-radar" className="mb-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-5">
        <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-4">六维能力雷达图</h3>
        <div className="flex flex-col md:flex-row gap-6 items-start">
          <div className="flex-1 min-h-[300px]">
            <ReactECharts
              option={buildRadarOption(blocks, theme === 'dark')}
              style={{ height: 300 }}
            />
          </div>
          <div className="flex-1">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
                  <th className="text-left py-1">维度</th>
                  <th className="text-center py-1">得分</th>
                  <th className="text-center py-1">置信度</th>
                  <th className="text-center py-1">状态</th>
                </tr>
              </thead>
              <tbody>
                {(blocks.radar?.dimensions || []).map((d) => (
                  <tr key={d.id} className="border-b border-gray-50 dark:border-gray-700">
                    <td className="py-2 flex items-center gap-2">
                      <span
                        className="inline-block w-3 h-3 rounded-full"
                        style={{ background: DIM_COLORS[d.id] || '#888' }}
                      />
                      {d.name}
                    </td>
                    <td className="text-center font-bold text-blue-600">
                      {d.score != null ? d.score.toFixed(1) : '—'}
                    </td>
                    <td className="text-center">
                      {d.confidence ? (
                        <Tag color={d.confidence === '高' ? 'green' : d.confidence === '中' ? 'blue' : 'orange'}>
                          {d.confidence}
                        </Tag>
                      ) : '—'}
                    </td>
                    <td className="text-center">
                      <Tag color={d.status === 'done' ? 'green' : d.status === 'locked' ? 'default' : 'red'}>
                        {d.status === 'done' ? '完成' : d.status === 'locked' ? '锁定' : d.status}
                      </Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Block: overview */}
      {blocks.overview && (
        <section id="block-overview" className="mb-6 bg-gradient-to-br from-blue-50 to-purple-50 dark:from-blue-900/20 dark:to-purple-900/20 rounded-xl border border-blue-100 dark:border-blue-800 p-6">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-1">
            {blocks.overview.persona_label || '综合画像'}
          </h2>
          {blocks.overview.narrative_intro && (
            <p className="text-gray-600 dark:text-gray-300 text-sm leading-relaxed mb-4">{blocks.overview.narrative_intro}</p>
          )}
          {(blocks.overview.top_cards || []).length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
              {(blocks.overview.top_cards || []).map((card, i) => (
                <div key={i} className="bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm border border-white dark:border-gray-700">
                  <p className="font-semibold text-gray-800 dark:text-gray-200 text-sm">{card.title || (card as unknown as string)}</p>
                  {card.description && (
                    <p className="text-gray-500 text-xs mt-1">{card.description}</p>
                  )}
                </div>
              ))}
            </div>
          )}
          {(blocks.overview.keywords || []).length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {(blocks.overview.keywords || []).map((kw, i) => (
                <Tag key={i} color="blue">{kw}</Tag>
              ))}
            </div>
          )}
          {blocks.overview.next_direction && (
            <Alert type="info" message={blocks.overview.next_direction} showIcon />
          )}
        </section>
      )}

      {/* 六维度 Blocks */}
      {(['skills', 'knowledge', 'abilities', 'work_styles', 'interests', 'work_values'] as const).map((dim) => (
        <div key={dim} id={`block-${dim}`}>
          <DimBlock block={blocks[dim]} />
        </div>
      ))}

      {/* Block: action */}
      {blocks.action && (
        <section id="block-action" className="mb-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-5">
          <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-4">行动建议</h3>
          {(blocks.action.top3_strengths || []).length === 0
           && (blocks.action.top3_improvements || []).length === 0 ? (
            <Alert
              type="info"
              showIcon
              message="暂无行动建议"
              description="请补充更完整的个人信息后重新评估"
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-green-700 font-semibold mb-2">核心优势</p>
                {(blocks.action.top3_strengths || []).map((s, i) => (
                  <div
                    key={i}
                    className="bg-green-50 rounded-lg p-3 mb-2 border border-green-100"
                  >
                    <p className="font-medium text-green-800 text-sm">
                      {s.title || s.ref_dimension}
                    </p>
                    {s.career_meaning && (
                      <p className="text-green-700 text-sm mt-1 leading-relaxed">
                        {s.career_meaning}
                      </p>
                    )}
                    {s.how_to_amplify && (
                      <ul className="mt-2 space-y-1">
                        {(Array.isArray(s.how_to_amplify) ? s.how_to_amplify : [s.how_to_amplify]).map((h, j) => (
                          <li
                            key={j}
                            className="text-green-600 text-xs pl-3 border-l-2 border-green-300"
                          >
                            {h}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
              <div>
                <p className="text-orange-700 font-semibold mb-2">优先提升</p>
                {(blocks.action.top3_improvements || []).map((s, i) => (
                  <div
                    key={i}
                    className="bg-orange-50 rounded-lg p-3 mb-2 border border-orange-100"
                  >
                    <p className="font-medium text-orange-800 text-sm">
                      {s.title || s.ref_dimension}
                    </p>
                    {s.current_state && (
                      <p className="text-orange-700 text-xs mt-1">
                        <span className="text-orange-500">现状：</span>
                        {s.current_state}
                      </p>
                    )}
                    {s.target_state && (
                      <p className="text-orange-700 text-xs mt-1">
                        <span className="text-orange-500">目标：</span>
                        {s.target_state}
                      </p>
                    )}
                    {s.action_plan && typeof s.action_plan === 'object' && !Array.isArray(s.action_plan) && Object.keys(s.action_plan).length > 0 && (
                      <div className="mt-2 space-y-0.5">
                        {Object.entries(s.action_plan).map(([period, plan]) => (
                          <p key={period} className="text-gray-600 text-xs">
                            <span className="text-orange-600 font-medium">
                              {period}:
                            </span>{' '}
                            {plan}
                          </p>
                        ))}
                      </div>
                    )}
                    {s.expected_outcome && (
                      <p className="text-gray-500 text-xs mt-2 italic">
                        → {s.expected_outcome}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* Block: unlock */}
      {blocks.unlock && (blocks.unlock.items || []).length > 0 && (
        <section id="block-unlock" className="mb-6 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600 p-5">
          <h3 className="font-bold text-gray-600 dark:text-gray-300 text-lg mb-3">解锁更多分析</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(blocks.unlock.items || []).map((item, i) => (
              <div key={i} className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-600">
                <p className="font-medium text-gray-700 dark:text-gray-200">{item.title}</p>
                <p className="text-gray-400 text-xs mt-1">预计 {item.duration_min} 分钟</p>
                <p className="text-gray-500 text-sm mt-2">{item.teaser}</p>
                <Button size="small" disabled className="mt-3">敬请期待</Button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Block: methodology */}
      <section id="block-methodology" className="mb-6">
        <Collapse ghost>
          <Panel header={<span className="text-gray-400 text-sm">评估方法论说明</span>} key="1">
            <div className="text-sm text-gray-500 space-y-2">
              {blocks.methodology?.framework && (
                <p><strong>框架：</strong>{blocks.methodology.framework}</p>
              )}
              {blocks.methodology?.scale && (
                <p><strong>量表：</strong>{blocks.methodology.scale}</p>
              )}
              {blocks.methodology?.score_guide && (
                <div>
                  <p className="font-medium mb-1">评分体系：</p>
                  {Object.entries(blocks.methodology.score_guide).map(([k, v]) => (
                    <p key={k} className="ml-2">{k}分：{v}</p>
                  ))}
                </div>
              )}
              {blocks.methodology?.disclaimer && (
                <Alert type="warning" message={blocks.methodology.disclaimer} showIcon className="mt-2" />
              )}
            </div>
          </Panel>
        </Collapse>
      </section>

      {/* 底部操作 */}
      <div className="flex justify-center mt-8 mb-4">
        <Button type="primary" size="large" onClick={() => navigate('/career')}>
          进入职业规划 →
        </Button>
      </div>
    </div>
  );
};

export default Assessment;
