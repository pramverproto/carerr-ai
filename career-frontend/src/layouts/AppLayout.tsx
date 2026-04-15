import React from 'react';
import Sidebar from '@/components/layout/Sidebar';
import Header from '@/components/layout/Header';
import AnimatedOutlet from '@/components/layout/AnimatedOutlet';

const AppLayout: React.FC = () => {
  return (
    <>
      <Sidebar />
      <Header />
      {/* 桌面端留侧边栏空间；移动端全宽 */}
      <main className="fixed left-0 lg:left-[268px] right-0 lg:right-6 top-[64px] lg:top-[84px] bottom-0 lg:bottom-6 overflow-y-auto z-30">
        <div className="min-h-full bg-white dark:bg-gray-800 lg:rounded-2xl lg:border lg:border-gray-200 dark:lg:border-gray-700 lg:shadow-sm p-4 lg:p-6">
          <AnimatedOutlet />
        </div>
      </main>
    </>
  );
};

export default AppLayout;
