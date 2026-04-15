import React from 'react';
import { Skeleton } from 'antd';

const PlanProgressSkeleton: React.FC = () => (
  <div className="flex flex-col h-full overflow-hidden">
    {/* Header */}
    <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
      <Skeleton active paragraph={{ rows: 1 }} title={{ width: '30%' }} />
      <Skeleton.Input active style={{ width: '100%', height: 8, marginTop: 8 }} />
    </div>
    {/* Week tabs */}
    <div className="flex gap-2 px-6 py-3 border-b border-gray-100 dark:border-gray-700">
      {[1, 2, 3, 4].map((i) => (
        <Skeleton.Button key={i} active style={{ width: 64, height: 48 }} />
      ))}
    </div>
    {/* Day cards */}
    <div className="px-6 py-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {[1, 2, 3].map((i) => (
        <div key={i} className="border border-gray-200 dark:border-gray-600 rounded-xl p-4">
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
      ))}
    </div>
  </div>
);

export default PlanProgressSkeleton;
