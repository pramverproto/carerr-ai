import React from 'react';
import { Skeleton } from 'antd';

const CareerMatchSkeleton: React.FC = () => (
  <div>
    <div className="mb-6">
      <Skeleton.Input active style={{ width: 280, height: 28 }} />
      <div className="mt-2">
        <Skeleton.Input active style={{ width: 360, height: 18 }} />
      </div>
    </div>
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {[1, 2, 3].map((i) => (
        <div key={i} className="rounded-xl border border-gray-200 dark:border-gray-600 p-5">
          <div className="flex justify-between mb-3">
            <Skeleton.Input active style={{ width: 140 }} />
            <Skeleton.Avatar active size={32} shape="circle" />
          </div>
          <Skeleton active paragraph={{ rows: 3 }} />
          <div className="mt-4">
            <Skeleton.Button active block />
          </div>
        </div>
      ))}
    </div>
  </div>
);

export default CareerMatchSkeleton;
