import React from 'react';
import { Tag, Collapse } from 'antd';
import type { JdRecommendationsBlock, JDPosition } from '@/types';

const { Panel } = Collapse;

function JDPositionCard({ pos, index }: { pos: JDPosition; index: number }) {
  const difficultyLabel = { easy: '容易入行', moderate: '中等难度', hard: '难度较高' };
  const difficultyColor = { easy: 'green', moderate: 'blue', hard: 'orange' };
  const diff = pos.match_analysis?.entry_difficulty || 'moderate';

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl shadow-sm overflow-hidden">
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

      {pos.role_explanation && (
        <div className="px-5 py-3 bg-blue-50 dark:bg-blue-900/20 border-b border-blue-100 dark:border-blue-800">
          <p className="text-xs text-blue-600 dark:text-blue-400 font-medium mb-1">这个岗位在做什么</p>
          <p className="text-sm text-blue-800 dark:text-blue-300 leading-relaxed">{pos.role_explanation}</p>
        </div>
      )}

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

export default React.memo(JdRecommendationsSection);
