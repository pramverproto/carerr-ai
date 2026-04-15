import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { LogOut, User, Menu, Sun, Moon } from 'lucide-react';
import { useAppStore } from '@/store/appStore';
import { useAuthStore } from '@/store/authStore';
import { useLayoutStore } from '@/store/layoutStore';

type Theme = 'light' | 'dark';

const pathMap: Record<string, string> = {
  '/chat':       'AI 对话',
  '/profile':    '信息完善',
  '/assessment': '能力评估',
  '/career':     '职业规划',
};

const Header: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { resetAll } = useAppStore();
  const authUser = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar);
  const theme = useLayoutStore((s) => s.theme) as Theme;
  const toggleTheme = useLayoutStore((s) => s.toggleTheme);

  const handleLogout = () => {
    logout();
    resetAll();
    navigate('/login', { replace: true });
  };

  const pageTitle = pathMap[location.pathname] || 'Career AI';

  return (
    <header className="fixed left-0 lg:left-64 right-0 top-0 h-16 bg-[#1F2937] flex items-center justify-between px-4 lg:px-6 z-40">
      <div className="flex items-center gap-3">
        {/* 汉堡菜单 — 仅移动端 */}
        <button
          onClick={toggleSidebar}
          className="lg:hidden text-gray-400 hover:text-white transition-colors"
        >
          <Menu size={22} />
        </button>
        <h1 className="text-white text-lg lg:text-xl font-bold">{pageTitle}</h1>
      </div>

      <div className="flex items-center gap-2 lg:gap-3">
        {/* 用户名 + 退出 */}
        <div className="flex items-center gap-2">
          <div className="hidden sm:flex items-center gap-1.5 text-gray-300 text-sm">
            <User size={14} />
            <span>{authUser?.username || '—'}</span>
          </div>
          <button
            onClick={toggleTheme}
            className="text-gray-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-gray-700"
            title={theme === 'dark' ? '切换亮色' : '切换暗色'}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button
            onClick={handleLogout}
            className="flex items-center gap-1 text-gray-400 hover:text-red-400 transition-colors text-xs bg-gray-800 rounded-lg px-2.5 py-1.5"
            title="退出登录"
          >
            <LogOut size={14} />
            <span className="hidden sm:inline">退出</span>
          </button>
        </div>
      </div>
    </header>
  );
};

export default Header;
