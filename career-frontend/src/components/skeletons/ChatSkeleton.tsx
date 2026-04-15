import React from 'react';
import { Skeleton } from 'antd';

const ChatSkeleton: React.FC = () => (
  <div className="flex flex-col -m-6" style={{ height: 'calc(100vh - 84px - 48px)' }}>
    <div className="px-6 py-2 border-b border-gray-100 dark:border-gray-700">
      <Skeleton.Input active size="small" style={{ width: 80 }} />
    </div>
    <div className="flex-1 px-6 py-4 space-y-4">
      {[1, 2, 3].map((i) => (
        <React.Fragment key={i}>
          <div className="flex justify-end">
            <div className="flex gap-3 flex-row-reverse max-w-[75%]">
              <Skeleton.Avatar active size={32} />
              <Skeleton.Input active style={{ width: 180, height: 40, borderRadius: 16 }} />
            </div>
          </div>
          <div className="flex justify-start">
            <div className="flex gap-3 max-w-[75%]">
              <Skeleton.Avatar active size={32} />
              <div style={{ width: 280 }}>
                <Skeleton active paragraph={{ rows: 2, width: ['80%', '60%'] }} title={false} />
              </div>
            </div>
          </div>
        </React.Fragment>
      ))}
    </div>
  </div>
);

export default ChatSkeleton;
