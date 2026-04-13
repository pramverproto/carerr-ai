import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
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

const App: React.FC = () => {
  return (
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
  );
};

export default App;
