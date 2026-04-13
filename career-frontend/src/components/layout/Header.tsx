import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Copy, Check, Hash, LogOut, User, Menu } from 'lucide-react';
import { useAppStore } from '@/store/appStore';
import { useAuthStore } from '@/store/authStore';
import { useLayoutStore } from '@/store/layoutStore';
import { message, Modal, Input } from 'antd';

const pathMap: Record<string, string> = {
  '/chat':       'AI 对话',
  '/profile':    '信息完善',
  '/assessment': '能力评估',
  '/career':     '职业规划',
};

const Header: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { assessmentId, setAssessmentId, resetAll } = useAppStore();
  const authUser = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar);
  const [copied, setCopied] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [inputId, setInputId] = useState('');

  const handleLogout = () => {
    logout();
    resetAll();
    navigate('/login', { replace: true });
  };

  const pageTitle = pathMap[location.pathname] || 'Career AI';

  const handleCopy = () => {
    if (!assessmentId) return;
    navigator.clipboard.writeText(assessmentId).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const handleLoadId = () => {
    const trimmed = inputId.trim();
    if (!trimmed) return;
    setAssessmentId(trimmed);
    message.success('已加载评估 ID');
    setModalOpen(false);
    setInputId('');
  };

  const shortId = assessmentId
    ? assessmentId.slice(0, 8) + '...'
    : null;

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
        {shortId ? (
          <div className="hidden sm:flex items-center gap-2 bg-gray-800 rounded-lg px-3 py-1.5">
            <Hash size={14} className="text-gray-400" />
            <span className="text-gray-300 text-xs font-mono">{shortId}</span>
            <button
              onClick={handleCopy}
              className="text-gray-400 hover:text-white transition-colors"
              title="复制完整 ID"
            >
              {copied ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
            </button>
          </div>
        ) : (
          <button
            onClick={() => setModalOpen(true)}
            className="hidden sm:block text-xs text-gray-400 hover:text-white transition-colors bg-gray-800 rounded-lg px-3 py-1.5"
          >
            输入已有评估 ID
          </button>
        )}

        {/* 用户名 + 退出 */}
        <div className="flex items-center gap-2">
          <div className="hidden sm:flex items-center gap-1.5 text-gray-300 text-sm">
            <User size={14} />
            <span>{authUser?.username || '—'}</span>
          </div>
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

      <Modal
        title="输入已有评估 ID"
        open={modalOpen}
        onOk={handleLoadId}
        onCancel={() => { setModalOpen(false); setInputId(''); }}
        okText="加载"
        cancelText="取消"
      >
        <Input
          placeholder="粘贴 assessment_id（32位）"
          value={inputId}
          onChange={(e) => setInputId(e.target.value)}
          onPressEnter={handleLoadId}
        />
      </Modal>
    </header>
  );
};

export default Header;
