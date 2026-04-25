import React, { useEffect, useState } from 'react';
import { Progress, Skeleton } from 'antd';

const STEPS = [
  '解析你的能力画像',
  '从职位库召回候选岗位',
  'AI 深度匹配 + 路线规划',
  '组装多阶段发展建议',
];

const TOTAL_SEC = 35; // 经验值，整个 /career/match 流程约 30-40s

const CareerMatchSkeleton: React.FC = () => {
  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const timer = setInterval(() => {
      setElapsedSec((Date.now() - startedAt) / 1000);
    }, 250);
    return () => clearInterval(timer);
  }, []);

  // 当前进行到第几步：根据已用时间分段
  const stepIdx = Math.min(
    STEPS.length - 1,
    Math.floor((elapsedSec / TOTAL_SEC) * STEPS.length),
  );
  const pct = Math.min(95, Math.round((elapsedSec / TOTAL_SEC) * 100));

  return (
    <div className="max-w-2xl mx-auto mt-8">
      <div className="text-center mb-6">
        <div className="text-2xl mb-2">🎯</div>
        <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100">
          AI 正在为你规划职业路线
        </h2>
        <p className="text-gray-500 mt-1 text-sm">
          基于能力评估，从数千个岗位中筛选最匹配的发展方向（约 30-40 秒）
        </p>
      </div>

      <Progress percent={pct} status="active" />

      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 p-5 mt-4 space-y-3">
        {STEPS.map((step, i) => {
          const done = i < stepIdx;
          const active = i === stepIdx;
          return (
            <div key={step} className="flex items-center gap-3">
              <span className={`w-5 ${done ? 'text-green-500' : active ? 'text-blue-500' : 'text-gray-300'}`}>
                {done ? '✓' : active ? '▸' : '○'}
              </span>
              <span className={
                done ? 'text-gray-800 dark:text-gray-100'
                : active ? 'text-blue-600 dark:text-blue-400 font-medium'
                : 'text-gray-400'
              }>
                {step}
              </span>
              {active && (
                <span className="text-xs text-gray-400 ml-auto">
                  已用 {elapsedSec.toFixed(0)}s
                </span>
              )}
            </div>
          );
        })}
      </div>

      <p className="text-xs text-gray-400 text-center mt-4">
        生成期间可以先去其他页面看看，等完成后回来查看推荐结果
      </p>

      <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 opacity-50">
        {[1, 2, 3].map((i) => (
          <div key={i} className="rounded-lg border border-gray-200 dark:border-gray-700 p-4">
            <Skeleton active paragraph={{ rows: 2 }} />
          </div>
        ))}
      </div>
    </div>
  );
};

export default CareerMatchSkeleton;
