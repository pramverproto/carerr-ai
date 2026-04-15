import React from 'react';
import { Skeleton } from 'antd';

const ArchiveSkeleton: React.FC = () => (
  <div className="flex h-full overflow-hidden">
    {/* Left panel */}
    <div className="w-80 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 p-4 space-y-3">
      <Skeleton active paragraph={{ rows: 1 }} title={{ width: '50%' }} />
      {[1, 2, 3].map((i) => (
        <div key={i} className="border border-gray-200 dark:border-gray-600 rounded-lg p-3">
          <Skeleton active paragraph={{ rows: 2 }} title={{ width: '60%' }} />
        </div>
      ))}
    </div>
    {/* Right panel */}
    <div className="flex-1 p-6">
      <Skeleton active paragraph={{ rows: 8 }} />
    </div>
  </div>
);

export default ArchiveSkeleton;
