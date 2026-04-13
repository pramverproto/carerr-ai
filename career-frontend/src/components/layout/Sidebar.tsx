import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { FileText, Award, BookOpen, Target, CalendarCheck, FolderOpen, MessageSquare, X } from 'lucide-react';
import { useLayoutStore } from '@/store/layoutStore';

interface MenuItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  path: string;
}

const menuItems: MenuItem[] = [
  { key: 'chat',        label: 'AI 对话',  icon: <MessageSquare size={20} />, path: '/chat' },
  { key: 'profile',     label: '信息完善', icon: <FileText size={20} />,      path: '/profile' },
  { key: 'assessment',  label: '能力评估', icon: <Award size={20} />,         path: '/assessment' },
  { key: 'career',      label: '职业规划', icon: <BookOpen size={20} />,      path: '/career' },
  { key: 'plan',        label: '计划进度', icon: <CalendarCheck size={20} />, path: '/plan' },
  { key: 'archive',     label: '成长档案', icon: <FolderOpen size={20} />,    path: '/archive' },
];

const Sidebar: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { sidebarOpen, closeSidebar } = useLayoutStore();

  const isActive = (path: string): boolean =>
    location.pathname === path || location.pathname.startsWith(path + '/');

  const handleNav = (path: string) => {
    navigate(path);
    closeSidebar();
  };

  return (
    <>
      {/* 移动端遮罩 */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={closeSidebar}
        />
      )}

      {/* 侧边栏 */}
      <aside
        className={`
          fixed left-0 top-0 h-screen w-64 bg-[#1F2937] flex flex-col z-50
          transition-transform duration-300 ease-in-out
          lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        {/* Logo + 关闭按钮 */}
        <div className="flex items-center justify-between px-6 py-5">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-500 rounded-lg flex items-center justify-center">
              <Target size={18} className="text-white" />
            </div>
            <span className="text-white text-xl font-bold">Career AI</span>
          </div>
          <button
            onClick={closeSidebar}
            className="lg:hidden text-gray-400 hover:text-white transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-6 overflow-y-auto">
          <ul className="space-y-1">
            {menuItems.map((item) => {
              const active = isActive(item.path);
              return (
                <li key={item.key}>
                  <button
                    onClick={() => handleNav(item.path)}
                    className={`w-full flex items-center gap-3 px-6 py-3 transition-all duration-200 ${
                      active
                        ? 'text-white bg-gray-800 border-l-4 border-blue-500'
                        : 'text-gray-400 hover:text-white hover:bg-gray-800'
                    }`}
                  >
                    {item.icon}
                    <span className="text-sm font-medium">{item.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
    </>
  );
};

export default Sidebar;
