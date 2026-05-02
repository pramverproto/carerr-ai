import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Space, Spin, Statistic, Table, Tabs, Tag } from 'antd';
import { Activity, Database, RefreshCw, Server, Users } from 'lucide-react';
import { api, type AdminAssessmentItem, type AdminOverview, type AdminResources, type AdminUserItem } from '@/api/client';
import { useAuthStore } from '@/store/authStore';

const statusColor: Record<string, string> = {
  done: 'green',
  partial: 'gold',
  running: 'blue',
  pending: 'default',
  failed: 'red',
  error: 'red',
};

function fmtTime(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—';
}

function StatusTag({ value }: { value?: string | null }) {
  const status = value || 'unknown';
  return <Tag color={statusColor[status] || 'default'}>{status}</Tag>;
}

const Admin: React.FC = () => {
  const isAdmin = useAuthStore((s) => Boolean(s.user?.is_admin));
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [assessments, setAssessments] = useState<AdminAssessmentItem[]>([]);
  const [resources, setResources] = useState<AdminResources | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async (nextStatus = statusFilter) => {
    setError(null);
    setRefreshing(true);
    try {
      const [overviewRes, usersRes, assessmentsRes, resourcesRes] = await Promise.all([
        api.adminOverview(),
        api.adminUsers(),
        api.adminAssessments(nextStatus),
        api.adminResources(),
      ]);
      setOverview(overviewRes.data);
      setUsers(usersRes.data.items);
      setAssessments(assessmentsRes.data.items);
      setResources(resourcesRes.data);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || '后台数据加载失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    if (isAdmin) {
      load();
    } else {
      setLoading(false);
    }
  }, [isAdmin]);

  const statusItems = useMemo(() => Object.entries(overview?.assessment_status || {}), [overview]);

  if (!isAdmin) {
    return (
      <Alert
        type="warning"
        showIcon
        message="需要管理员权限"
        description="当前账号没有后台运维访问权限。"
      />
    );
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Spin />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">后台运维</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">用户、评估任务、计划与数据资源运行状态</p>
        </div>
        <Button
          icon={<RefreshCw size={15} />}
          loading={refreshing}
          onClick={() => load()}
        >
          刷新
        </Button>
      </div>

      {error && <Alert type="error" showIcon message={error} />}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <Card size="small">
          <Statistic title="用户数" value={overview?.metrics.users ?? 0} prefix={<Users size={18} />} />
        </Card>
        <Card size="small">
          <Statistic title="评估任务" value={overview?.metrics.assessments ?? 0} prefix={<Activity size={18} />} />
        </Card>
        <Card size="small">
          <Statistic title="学习计划" value={overview?.metrics.plans ?? 0} prefix={<Server size={18} />} />
        </Card>
        <Card size="small">
          <Statistic title="规划分块" value={overview?.metrics.career_plan_blocks ?? 0} prefix={<Database size={18} />} />
        </Card>
      </div>

      <Tabs
        items={[
          {
            key: 'assessments',
            label: '评估任务',
            children: (
              <div className="space-y-3">
                <Space wrap>
                  <Button
                    size="small"
                    type={!statusFilter ? 'primary' : 'default'}
                    onClick={() => {
                      setStatusFilter(undefined);
                      load(undefined);
                    }}
                  >
                    全部
                  </Button>
                  {statusItems.map(([status, count]) => (
                    <Button
                      key={status}
                      size="small"
                      type={statusFilter === status ? 'primary' : 'default'}
                      onClick={() => {
                        setStatusFilter(status);
                        load(status);
                      }}
                    >
                      {status}({count})
                    </Button>
                  ))}
                </Space>
                <Table
                  rowKey="assessment_id"
                  size="small"
                  dataSource={assessments}
                  pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '评估ID', dataIndex: 'assessment_id', width: 150 },
                    { title: '用户', dataIndex: 'username', render: (v, r) => v || r.user_id || '—' },
                    { title: '姓名', dataIndex: 'name', render: (v) => v || '—' },
                    { title: '当前岗位', dataIndex: 'current_title', render: (v) => v || '—' },
                    { title: '状态', dataIndex: 'status', render: (v) => <StatusTag value={v} /> },
                    { title: '维度', dataIndex: 'dimension_count', width: 70 },
                    { title: '计划', dataIndex: 'plan_count', width: 70 },
                    { title: '创建时间', dataIndex: 'created_at', render: fmtTime },
                    {
                      title: '错误',
                      dataIndex: 'error',
                      ellipsis: true,
                      render: (v) => v || '—',
                    },
                  ]}
                />
              </div>
            ),
          },
          {
            key: 'users',
            label: '用户',
            children: (
              <Table
                rowKey="user_id"
                size="small"
                dataSource={users}
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: 'ID', dataIndex: 'user_id', width: 80 },
                  { title: '用户名', dataIndex: 'username' },
                  { title: '邮箱', dataIndex: 'email', render: (v) => v || '—' },
                  { title: '角色', dataIndex: 'is_admin', render: (v) => v ? <Tag color="blue">管理员</Tag> : <Tag>普通用户</Tag> },
                  { title: '评估数', dataIndex: 'assessment_count', width: 90 },
                  { title: '计划数', dataIndex: 'plan_count', width: 90 },
                  { title: '最近评估', dataIndex: 'last_assessment_at', render: fmtTime },
                  { title: '注册时间', dataIndex: 'created_at', render: fmtTime },
                ]}
              />
            ),
          },
          {
            key: 'failures',
            label: '异常',
            children: (
              <Table
                rowKey="assessment_id"
                size="small"
                dataSource={overview?.recent_failed || []}
                pagination={false}
                columns={[
                  { title: '评估ID', dataIndex: 'assessment_id' },
                  { title: '用户', dataIndex: 'username', render: (v, r) => v || r.user_id || '—' },
                  { title: '状态', dataIndex: 'status', render: (v) => <StatusTag value={v} /> },
                  { title: '更新时间', dataIndex: 'updated_at', render: fmtTime },
                  { title: '错误信息', dataIndex: 'error', ellipsis: true, render: (v) => v || '—' },
                ]}
              />
            ),
          },
          {
            key: 'resources',
            label: '数据资源',
            children: (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <Table
                  rowKey="name"
                  size="small"
                  dataSource={resources?.tables || []}
                  pagination={false}
                  columns={[
                    { title: '数据表', dataIndex: 'name' },
                    { title: '记录数', dataIndex: 'count', width: 120 },
                  ]}
                />
                <div className="space-y-4">
                  <Card size="small" title="服务状态">
                    <Space wrap>
                      <Tag color={resources?.services.mysql ? 'green' : 'red'}>MySQL</Tag>
                      <Tag color={resources?.services.redis ? 'green' : 'red'}>Redis</Tag>
                      <Tag color={resources?.services.vector_index ? 'green' : 'default'}>向量索引</Tag>
                    </Space>
                  </Card>
                  <Table
                    rowKey="agent"
                    size="small"
                    dataSource={resources?.onet_files || []}
                    pagination={false}
                    columns={[
                      { title: 'Agent', dataIndex: 'agent' },
                      { title: '文件', dataIndex: 'file', ellipsis: true },
                      { title: '状态', dataIndex: 'loaded', render: (v) => v ? <Tag color="green">已加载</Tag> : <Tag color="red">缺失</Tag> },
                      { title: '字符数', dataIndex: 'characters', width: 100 },
                    ]}
                  />
                </div>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
};

export default Admin;
