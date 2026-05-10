import axios from 'axios';
import type {
  AssessRequest, AssessResponse,
  ReportResponse,
  CareerMatchResponse,
  CareerPlanResponse,
  CareerPathProgress,
  PlanSchedule,
  PlanListItem,
  WeeklyPlanResponse,
  ArchiveItem,
  ArchiveDetail,
  Milestone,
  GenerateOutlineResponse,
  ConfirmOutlineResponse,
  TodayTasksResponse,
  CompleteTaskResponse,
  LearnPlanProgress,
  LearnPlanListItem,
  LearnPlanRoadmap,
  LearnPlanSummary,
  LearnTask,
  RecentDoneTask,
} from '@/types';
import { useAuthStore } from '@/store/authStore';

const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: BASE,
  headers: { 'Content-Type': 'application/json' },
});

// ── Request 拦截器：自动附加 JWT token ─────────────────────────────
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Response 拦截器：401 时提示用户登录已过期 ─────────────────────
let _expiredNotified = false;
apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    const hadToken = Boolean(useAuthStore.getState().token);
    if (err.response?.status === 401 && hadToken && !_expiredNotified) {
      _expiredNotified = true;
      // 使用 antd Modal 避免丢失用户正在编辑的数据
      import('antd').then(({ Modal }) => {
        Modal.warning({
          title: '登录已过期',
          content: '请重新登录以继续使用。',
          okText: '去登录',
          onOk() {
            _expiredNotified = false;
            useAuthStore.getState().logout();
            window.location.href = '/login';
          },
        });
      });
    }
    return Promise.reject(err);
  },
);

export interface LoginResponse {
  user_id: number;
  username: string;
  token: string;
  email?: string;
  is_admin?: boolean;
}

export interface AdminOverview {
  metrics: {
    users: number;
    assessments: number;
    plans: number;
    career_plan_blocks: number;
  };
  assessment_status: Record<string, number>;
  recent_failed: AdminAssessmentItem[];
  daily_assessments: { date: string; count: number }[];
}

export interface AdminUserItem {
  user_id: number;
  username: string;
  email?: string | null;
  is_admin: boolean;
  created_at?: string | null;
  assessment_count: number;
  plan_count: number;
  last_assessment_at?: string | null;
}

export interface AdminAssessmentItem {
  assessment_id: string;
  session_id?: string | null;
  user_id?: number | null;
  username?: string | null;
  status: string;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  dimension_count?: number;
  plan_count?: number;
  name?: string;
  current_title?: string;
  education?: string;
}

export interface AdminResources {
  tables: { name: string; count: number }[];
  onet_files: { agent: string; file: string; loaded: boolean; characters: number }[];
  services: { mysql: boolean; redis: boolean; vector_index: boolean };
}

export const api = {
  // ── Auth ──────────────────────────────────────────────────────────

  /** POST /auth/register */
  authRegister: (data: { username: string; password: string; email?: string }) =>
    apiClient.post<LoginResponse>('/auth/register', data),

  /** POST /auth/login */
  authLogin: (data: { username: string; password: string }) =>
    apiClient.post<LoginResponse>('/auth/login', data),

  /** GET /auth/me */
  authMe: () =>
    apiClient.get<{ user_id: number; username: string; email?: string; is_admin?: boolean }>('/auth/me'),

  // ── Admin ────────────────────────────────────────────────────────

  adminOverview: () =>
    apiClient.get<AdminOverview>('/admin/overview'),

  adminUsers: () =>
    apiClient.get<{ items: AdminUserItem[] }>('/admin/users'),

  adminAssessments: (status?: string) =>
    apiClient.get<{ items: AdminAssessmentItem[] }>(
      status ? `/admin/assessments?status=${encodeURIComponent(status)}` : '/admin/assessments',
    ),

  adminResources: () =>
    apiClient.get<AdminResources>('/admin/resources'),

  // ── Business ─────────────────────────────────────────────────────

  /** POST /assess — 立即返回 assessment_id，后台执行 */
  assess: (data: AssessRequest) =>
    apiClient.post<AssessResponse>('/assess', data, { timeout: 30_000 }),

  /** GET /assess/{id}/status — 查询评估真实进度 */
  assessStatus: (assessmentId: string) =>
    apiClient.get<{
      assessment_id: string;
      status: 'pending' | 'running' | 'done' | 'failed' | string;
      error: string | null;
      completed_dimensions: string[];
      summary_done: boolean;
      completed_report_blocks: string[];
    }>(`/assess/${assessmentId}/status`),

  /** GET /report/{id} — 6 个维度块并发生成 LLM 可能较慢，给 5 分钟 */
  getReport: (assessmentId: string) =>
    apiClient.get<ReportResponse>(`/report/${assessmentId}`, { timeout: 300_000 }),

  /** POST /assess/{id}/dimension/{dim}/retry — 单独重试某个评估维度 */
  assessRetryDimension: (assessmentId: string, dimName: string) =>
    apiClient.post<{ ok: boolean; dimension: string; status: string; overall_score: number | null }>(
      `/assess/${assessmentId}/dimension/${dimName}/retry`,
      {},
      { timeout: 120_000 },
    ),

  /** POST /career/match */
  matchCareers: (assessmentId: string, force = false, customStart?: string) =>
    apiClient.post<CareerMatchResponse>(
      '/career/match',
      { assessment_id: assessmentId, force, ...(customStart ? { custom_start: customStart } : {}) },
      { timeout: 120_000 },
    ),

  /** POST /career/plan — 超时5分钟 */
  careerPlan: (
    assessmentId: string,
    onetsocCode: string,
    title?: string,
    pathData?: string,
    currentStage?: number,
  ) =>
    apiClient.post<CareerPlanResponse>(
      '/career/plan',
      {
        assessment_id: assessmentId,
        onetsoc_code: onetsocCode,
        ...(title ? { title } : {}),
        ...(pathData ? { path_data: pathData } : {}),
        ...(currentStage ? { current_stage: currentStage } : {}),
      },
      { timeout: 300_000 },
    ),

  /** POST /career/save-path — 保存用户选择的路线 */
  saveCareerPath: (assessmentId: string, pathCode: string, pathData: string) =>
    apiClient.post<{ ok: boolean }>('/career/save-path', {
      assessment_id: assessmentId,
      path_code: pathCode,
      path_data: pathData,
    }),

  /** POST /career/stage-complete — 确认完成当前阶段 */
  confirmStageComplete: (
    assessmentId: string,
    pathCode: string,
    completedStage: number,
    userNote?: string,
  ) =>
    apiClient.post<{
      ok: boolean;
      new_stage: number;
      next_stage_info: { stage: number; title: string; timeframe: string } | null;
    }>('/career/stage-complete', {
      assessment_id: assessmentId,
      path_code: pathCode,
      completed_stage: completedStage,
      user_note: userNote || '',
    }),

  /** GET /career/path-progress — 获取路线进度 */
  getPathProgress: (assessmentId: string, pathCode: string) =>
    apiClient.get<CareerPathProgress>(`/career/path-progress/${assessmentId}/${pathCode}`),

  /** POST /plan-schedule/weekly — 生成周计划概览，超时2分钟 */
  createWeeklyPlan: (
    assessmentId: string,
    onetsocCode: string,
    durationWeeks: number,
    startDate: string,
  ) =>
    apiClient.post<WeeklyPlanResponse>(
      '/plan-schedule/weekly',
      { assessment_id: assessmentId, onetsoc_code: onetsocCode, duration_weeks: durationWeeks, start_date: startDate },
      { timeout: 120_000 },
    ),

  /** POST /plan-schedule/{planId}/confirm — 确认周计划，触发每日生成 */
  confirmPlan: (planId: string) =>
    apiClient.post<{ plan_id: string; status: string }>(`/plan-schedule/${planId}/confirm`),

  /** POST /plan-schedule/{planId}/retry-daily — 重试生成每日任务 */
  retryDailyTasks: (planId: string) =>
    apiClient.post<{ plan_id: string; status: string }>(`/plan-schedule/${planId}/retry-daily`),

  /** GET /plan-schedule/{planId} — 获取完整计划+进度 */
  getPlan: (planId: string) =>
    apiClient.get<PlanSchedule>(`/plan-schedule/${planId}`, { timeout: 30_000 }),

  /** PATCH /plan-schedule/{planId}/day/{week}/{day} — 更新打卡 */
  updateDayProgress: (planId: string, week: number, day: number, completedIds: string[]) =>
    apiClient.patch<{ ok: boolean }>(
      `/plan-schedule/${planId}/day/${week}/${day}`,
      { completed_ids: completedIds },
    ),

  /** GET /plan-schedule/list/{assessmentId}/{onetsocCode} — 历史计划列表 */
  listPlans: (assessmentId: string, onetsocCode: string) =>
    apiClient.get<{ plans: PlanListItem[] }>(`/plan-schedule/list/${assessmentId}/${onetsocCode}`),

  /** PATCH /plan-schedule/{planId}/day/{week}/{day}/note/{taskId} — 保存任务感悟 */
  updateTaskNote: (planId: string, week: number, day: number, taskId: string, note: string) =>
    apiClient.patch<{ ok: boolean }>(
      `/plan-schedule/${planId}/day/${week}/${day}/note/${taskId}`,
      { note },
    ),

  /** DELETE /plan-schedule/{planId} — 删除某个计划（级联清理周+每日任务） */
  deletePlan: (planId: string) =>
    apiClient.delete<{ ok: boolean; plan_id: string }>(`/plan-schedule/${planId}`),

  /** DELETE /plan-schedule/{planId}/week/{weekNumber} — 删除计划中的某一周，若为最后一周则级联删除整计划 */
  deletePlanWeek: (planId: string, weekNumber: number) =>
    apiClient.delete<{
      ok: boolean;
      plan_id: string;
      week_number: number;
      plan_deleted: boolean;
    }>(`/plan-schedule/${planId}/week/${weekNumber}`),

  /** POST /resume/extract — 上传简历图片/PDF，使用多模态模型做 OCR+结构化提取 */
  uploadResume: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiClient.post<{
      upload_id: string;
      extracted: {
        name?: string;
        age?: number | null;
        education?: string;
        current_title?: string;
        years_of_experience?: number | null;
        experiences?: {
          company: string;
          title: string;
          duration: string;
          responsibilities: string;
        }[];
        skills?: string[];
        certifications?: string[];
        supplement?: string;
      };
      elapsed_ms: number;
    }>('/resume/extract', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180_000,
    });
  },

  /** GET /career/plan/{id}/{code} — 读取已缓存的职业规划（不触发重新生成） */
  getCareerPlanCached: (assessmentId: string, onetsocCode: string) =>
    apiClient.get<CareerPlanResponse>(`/career/plan/${assessmentId}/${onetsocCode}`),

  /** GET /career/planned-codes/{id} — 查询已生成规划的职业代码 */
  getPlannedCodes: (assessmentId: string) =>
    apiClient.get<{
      assessment_id: string;
      planned: Record<string, { onetsoc_code: string; title?: string; final_score?: number; verdict?: string }>;
    }>(`/career/planned-codes/${assessmentId}`),

  // ── Archive（成长档案）──────────────────────────────────────────────

  /** GET /archive/list — 列出所有历史评估 */
  archiveList: () =>
    apiClient.get<{ assessments: ArchiveItem[] }>('/archive/list'),

  /** GET /archive/{id}/detail — 获取单次评估的完整档案 */
  archiveDetail: (assessmentId: string) =>
    apiClient.get<ArchiveDetail>(`/archive/${assessmentId}/detail`),

  /** GET /archive/milestones — 获取所有成长里程碑 */
  archiveMilestones: () =>
    apiClient.get<{ milestones: Milestone[] }>('/archive/milestones'),

  /** DELETE /archive/{id} — 删除单次评估及所有关联数据 */
  archiveDelete: (assessmentId: string) =>
    apiClient.delete<{ ok: boolean; assessment_id: string }>(`/archive/${assessmentId}`),

  // ── Learn Plan（任务计划，新版）─────────────────────────────────────

  /** POST /plan/generate — 生成学习大纲，超时 90s */
  learnGenerate: (assessmentId: string, stageCode: string, userPreference?: string) =>
    apiClient.post<GenerateOutlineResponse>(
      '/plan/generate',
      { assessment_id: assessmentId, stage_code: stageCode, user_preference: userPreference || null },
      { timeout: 90_000 },
    ),

  /** POST /plan/{id}/regenerate-outline — 重新生成大纲 */
  learnRegenerateOutline: (planId: string, userPreference?: string) =>
    apiClient.post<GenerateOutlineResponse>(
      `/plan/${planId}/regenerate-outline`,
      { user_preference: userPreference || null },
      { timeout: 90_000 },
    ),

  /** POST /plan/{id}/confirm-outline — 确认大纲，触发 roadmap + Week1 物化，超时 3 分钟 */
  learnConfirmOutline: (planId: string) =>
    apiClient.post<ConfirmOutlineResponse>(
      `/plan/${planId}/confirm-outline`,
      {},
      { timeout: 180_000 },
    ),

  /** GET /plan/current — 当前用户最新的学习计划摘要 */
  learnCurrent: () =>
    apiClient.get<LearnPlanSummary>('/plan/current'),

  /** GET /plan/list — 用户所有学习计划列表（可按 assessment_id 过滤） */
  learnList: (assessmentId?: string) =>
    apiClient.get<{ plans: LearnPlanListItem[] }>(
      assessmentId ? `/plan/list?assessment_id=${assessmentId}` : '/plan/list',
    ),

  /** GET /plan/{id}/roadmap — 完整路线图 */
  learnRoadmap: (planId: string) =>
    apiClient.get<LearnPlanRoadmap>(`/plan/${planId}/roadmap`),

  /** GET /plan/{id}/today — 今日任务（前 N 个 pending） */
  learnToday: (planId: string) =>
    apiClient.get<TodayTasksResponse>(`/plan/${planId}/today`),

  /** POST /plan/{id}/more — 再来一批（exclude_ids 是已展示过的 task id） */
  learnMore: (planId: string, excludeIds: number[], limit?: number) =>
    apiClient.post<{ plan_id: string; tasks: LearnTask[] }>(
      `/plan/${planId}/more`,
      { exclude_ids: excludeIds, limit },
    ),

  /** POST /plan/task/{id}/complete — 完成任务 + grader 打分 */
  learnCompleteTask: (taskId: number, reflection: string | null) =>
    apiClient.post<CompleteTaskResponse>(
      `/plan/task/${taskId}/complete`,
      { reflection },
      { timeout: 60_000 },
    ),

  /** GET /plan/{id}/progress — 进度条数据 */
  learnProgress: (planId: string) =>
    apiClient.get<LearnPlanProgress>(`/plan/${planId}/progress`),

  /** GET /plan/{id}/recent-done — 最近完成的任务 */
  learnRecentDone: (planId: string, days = 7) =>
    apiClient.get<{ plan_id: string; days: number; tasks: RecentDoneTask[] }>(
      `/plan/${planId}/recent-done?days=${days}`,
    ),

  /** POST /plan/{id}/week/{n}/retry — 重试某周物化 */
  learnRetryWeek: (planId: string, weekNum: number) =>
    apiClient.post<{ ok: boolean; week_num: number }>(
      `/plan/${planId}/week/${weekNum}/retry`,
      {},
      { timeout: 120_000 },
    ),

  /** DELETE /plan/{id} — 删除整个学习计划 */
  learnDeletePlan: (planId: string) =>
    apiClient.delete<{ ok: boolean; plan_id: string }>(`/plan/${planId}`),
};
