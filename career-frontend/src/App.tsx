import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, App as AntdApp, theme as antdTheme } from 'antd';
import { useAuthStore } from '@/store/authStore';
import { useThemeSync } from '@/hooks/useThemeSync';
import AppLayout from '@/layouts/AppLayout';
import Login from '@/pages/Login';
import Profile from '@/pages/Profile';
import Assessment from '@/pages/Assessment';
import CareerPlan from '@/pages/CareerPlan';
import PlanProgress from '@/pages/PlanProgress';
import Archive from '@/pages/Archive';
import Chat from '@/pages/Chat';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

const AppInner: React.FC = () => {
  const theme = useThemeSync();

  const themeConfig = {
    algorithm:
      theme === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: '#3b82f6',
      borderRadius: 8,
    },
  };

  return (
    <ConfigProvider theme={themeConfig}>
      <AntdApp>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <AppLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/chat" replace />} />
              <Route path="chat" element={<Chat />} />
              <Route path="profile" element={<Profile />} />
              <Route path="assessment" element={<Assessment />} />
              <Route path="career" element={<CareerPlan />} />
              <Route path="plan" element={<PlanProgress />} />
              <Route path="archive" element={<Archive />} />
              <Route path="*" element={<Navigate to="/profile" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
};

const App: React.FC = () => <AppInner />;

export default App;
