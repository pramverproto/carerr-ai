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
    if (err.response?.status === 401 && !_expiredNotified) {
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
    apiClient.get<{ user_id: number; username: string; email?: string }>('/auth/me'),

  // ── Business ─────────────────────────────────────────────────────

  /** POST /assess — 同步，超时5分钟 */
  assess: (data: AssessRequest) =>
    apiClient.post<AssessResponse>('/assess', data, { timeout: 300_000 }),

  /** GET /report/{id} — 6 个维度块并发生成 LLM 可能较慢，给 5 分钟 */
  getReport: (assessmentId: string) =>
    apiClient.get<ReportResponse>(`/report/${assessmentId}`, { timeout: 300_000 }),

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
};
