import React from 'react';
import { Skeleton } from 'antd';

const AssessmentSkeleton: React.FC = () => (
  <div className="space-y-6">
    {/* Anchor nav */}
    <div className="flex gap-2 border-b border-gray-100 dark:border-gray-700 pb-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <Skeleton.Button key={i} active size="small" style={{ width: 48, height: 24 }} />
      ))}
    </div>

    {/* Header section */}
    <div className="bg-gray-50 dark:bg-gray-700 rounded-xl p-4">
      <Skeleton active paragraph={{ rows: 1 }} />
    </div>

    {/* Radar chart */}
    <div className="rounded-xl border border-gray-100 dark:border-gray-700 p-5">
      <Skeleton.Input active style={{ width: 180, marginBottom: 16 }} />
      <div className="flex flex-col md:flex-row gap-6">
        <div className="flex-1 flex items-center justify-center py-8">
          <Skeleton.Avatar active size={200} shape="circle" />
        </div>
        <div className="flex-1">
          <Skeleton active paragraph={{ rows: 6 }} />
        </div>
      </div>
    </div>

    {/* Overview */}
    <div className="rounded-xl border border-gray-100 dark:border-gray-700 p-6">
      <Skeleton active title={{ width: '40%' }} paragraph={{ rows: 3 }} />
    </div>

    {/* Dimension blocks */}
    {[1, 2].map((i) => (
      <div key={i} className="rounded-xl border border-gray-100 dark:border-gray-700 p-5">
        <Skeleton active paragraph={{ rows: 4 }} />
      </div>
    ))}
  </div>
);

export default AssessmentSkeleton;
