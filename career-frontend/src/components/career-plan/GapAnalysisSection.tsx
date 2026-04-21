import React from 'react';
import { Tag } from 'antd';
import type { GapAnalysisBlock, GapItem, StrengthItem } from '@/types';
import { SEVERITY_COLOR, SEVERITY_LABEL } from './constants';

function GapAnalysisSection({ block }: { block: GapAnalysisBlock }) {
  return (
    <section id="plan-gap_analysis" className="mb-6">
      <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-2">差距与优势分析</h3>
      {block.summary && (
        <p className="text-gray-500 dark:text-gray-400 text-sm mb-4 leading-relaxed">{block.summary}</p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
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

export default React.memo(GapAnalysisSection);
