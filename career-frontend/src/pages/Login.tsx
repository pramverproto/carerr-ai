import React, { useState } from 'react';
import { Form, Input, Button, Tabs, App } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '@/api/client';
import { useAuthStore } from '@/store/authStore';
import { useAppStore } from '@/store/appStore';

/** 登录/注册后恢复用户最近的评估状态 */
async function restoreSession() {
  const { setAssessmentId, setProfileDraft } = useAppStore.getState();
  try {
    const { data } = await api.archiveList();
    const latest = data.assessments?.[0];
    if (latest?.assessment_id) {
      setAssessmentId(latest.assessment_id);
      // 从评估快照恢复个人信息草稿
      const detail = await api.archiveDetail(latest.assessment_id);
      const p = detail.data.profile;
      if (p) {
        setProfileDraft({
          resume: {
            candidate: {
              name: p.name,
              age: p.age ?? undefined,
              education: p.education,
              current_title: p.current_title,
              years_of_experience: p.years_of_experience ?? undefined,
            },
            experiences: p.experiences,
            skills: p.skills,
            certifications: p.certifications,
          },
          supplement: p.supplement,
        });
      }
    }
  } catch {
    // 恢复失败不阻塞登录流程
  }
}

const Login: React.FC = () => {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');
  const { message } = App.useApp();

  const handleLogin = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const { data } = await api.authLogin(values);
      setAuth(data.token, { user_id: data.user_id, username: data.username });
      message.success('登录成功');
      // restoreSession 是可选的状态恢复，fire-and-forget 不阻塞导航
      restoreSession().catch(() => { /* 失败不影响登录 */ });
      navigate('/', { replace: true });
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (values: { username: string; password: string; email?: string }) => {
    setLoading(true);
    try {
      const { data } = await api.authRegister(values);
      setAuth(data.token, { user_id: data.user_id, username: data.username });
      message.success('注册成功，已自动登录');
      restoreSession().catch(() => { /* 新用户 archiveList 通常空，恢复也无意义 */ });
      navigate('/', { replace: true });
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '注册失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900">
      <div className="w-full max-w-md mx-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Career AI</h1>
          <p className="text-gray-400 text-sm">智能职业规划助手</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          <Tabs
            activeKey={activeTab}
            onChange={(k) => setActiveTab(k as 'login' | 'register')}
            centered
            items={[
              {
                key: 'login',
                label: '登录',
                children: (
                  <Form onFinish={handleLogin} autoComplete="off" size="large">
                    <Form.Item
                      name="username"
                      rules={[{ required: true, message: '请输入用户名' }]}
                    >
                      <Input prefix={<UserOutlined />} placeholder="用户名" />
                    </Form.Item>
                    <Form.Item
                      name="password"
                      rules={[{ required: true, message: '请输入密码' }]}
                    >
                      <Input.Password prefix={<LockOutlined />} placeholder="密码" />
                    </Form.Item>
                    <Form.Item>
                      <Button type="primary" htmlType="submit" loading={loading} block>
                        登录
                      </Button>
                    </Form.Item>
                    <div className="text-center">
                      <span className="text-gray-400 text-sm">
                        没有账号？
                        <button
                          type="button"
                          className="text-blue-500 hover:text-blue-600 ml-1"
                          onClick={() => setActiveTab('register')}
                        >
                          立即注册
                        </button>
                      </span>
                    </div>
                  </Form>
                ),
              },
              {
                key: 'register',
                label: '注册',
                children: (
                  <Form onFinish={handleRegister} autoComplete="off" size="large">
                    <Form.Item
                      name="username"
                      rules={[
                        { required: true, message: '请输入用户名' },
                        { min: 2, max: 30, message: '用户名 2-30 个字符' },
                      ]}
                    >
                      <Input prefix={<UserOutlined />} placeholder="用户名（唯一）" />
                    </Form.Item>
                    <Form.Item
                      name="password"
                      rules={[
                        { required: true, message: '请输入密码' },
                        { min: 8, message: '密码至少 8 个字符' },
                      ]}
                    >
                      <Input.Password prefix={<LockOutlined />} placeholder="密码" />
                    </Form.Item>
                    <Form.Item name="email">
                      <Input prefix={<MailOutlined />} placeholder="邮箱（可选）" />
                    </Form.Item>
                    <Form.Item>
                      <Button type="primary" htmlType="submit" loading={loading} block>
                        注册
                      </Button>
                    </Form.Item>
                    <div className="text-center">
                      <span className="text-gray-400 text-sm">
                        已有账号？
                        <button
                          type="button"
                          className="text-blue-500 hover:text-blue-600 ml-1"
                          onClick={() => setActiveTab('login')}
                        >
                          去登录
                        </button>
                      </span>
                    </div>
                  </Form>
                ),
              },
            ]}
          />
        </div>
      </div>
    </div>
  );
};

export default Login;
