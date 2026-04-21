import React from 'react';
import { Tag, Divider } from 'antd';
import type { MatchOverviewBlock, KeyFactor } from '@/types';
import { GAP_STATUS_COLOR, VERDICT_COLOR, IMPACT_COLOR } from './constants';

function MatchOverviewSection({ block }: { block: MatchOverviewBlock }) {
  const verdictColor = VERDICT_COLOR[block.verdict] || '#1677ff';
  const { rule_based, llm_analysis } = block;

  return (
    <section id="plan-match_overview" className="mb-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-5">
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

export default React.memo(MatchOverviewSection);
