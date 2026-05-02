import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  AssessRequest,
  ReportResponse,
  CareerMatchResponse,
  CareerPlanResponse,
  PlanSchedule,
  CareerPathRecommendation,
} from '@/types';


type AssessStatus = 'idle' | 'loading' | 'done' | 'error';
type CareerStep = 'idle' | 'match_loading' | 'select' | 'plan_loading' | 'report';

export interface AssessProgressSnapshot {
  status: string;
  error: string | null;
  completedDimensions: string[];
  summaryDone: boolean;
  completedBlocks: number;
}

interface AppState {
  assessmentId: string | null;
  profileDraft: Partial<AssessRequest> | null;
  reportData: ReportResponse | null;
  matchData: CareerMatchResponse | null;
  planData: CareerPlanResponse | null;
  selectedCareer: string | null;
  assessStatus: AssessStatus;
  assessStartedAt: number | null;  // 评估开始的时间戳，用于刷新后基于时间还原进度
  assessError: string | null;
  assessmentPollingEnabled: boolean;
  assessmentProgress: AssessProgressSnapshot | null;
  matchLoading: boolean;
  matchError: string | null;
  planLoading: boolean;
  planError: string | null;
  careerStep: CareerStep;
  selectedPath: CareerPathRecommendation | null;
  currentStage: number;
  planningStartedAt: number | null;
  currentPlanId: string | null;
  cachedPlan: PlanSchedule | null;  // in-memory only, not persisted

  setAssessmentId: (id: string | null) => void;
  setProfileDraft: (draft: Partial<AssessRequest>) => void;
  setAssessStatus: (status: AssessStatus, error?: string) => void;
  setAssessmentPollingEnabled: (enabled: boolean) => void;
  setAssessmentProgress: (progress: AssessProgressSnapshot | null) => void;
  setReportData: (data: ReportResponse | null) => void;
  setMatchData: (data: CareerMatchResponse | null) => void;
  setSelectedCareer: (code: string | null) => void;
  setPlanData: (data: CareerPlanResponse | null) => void;
  setMatchLoading: (v: boolean) => void;
  setMatchError: (e: string | null) => void;
  setPlanLoading: (v: boolean) => void;
  setPlanError: (e: string | null) => void;
  setCareerStep: (step: CareerStep | ((prev: CareerStep) => CareerStep)) => void;
  setSelectedPath: (path: CareerPathRecommendation | null) => void;
  setCurrentStage: (stage: number) => void;
  setPlanningStartedAt: (ts: number | null) => void;
  setCurrentPlanId: (id: string | null) => void;
  setCachedPlan: (plan: PlanSchedule | null) => void;
  resetDownstream: () => void;
  resetPlan: () => void;
  resetAll: () => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      assessmentId: null,
      profileDraft: null,
      reportData: null,
      matchData: null,
      planData: null,
      selectedCareer: null,
      assessStatus: 'idle',
      assessStartedAt: null,
      assessError: null,
      assessmentPollingEnabled: false,
      assessmentProgress: null,
      matchLoading: false,
      matchError: null,
      planLoading: false,
      planError: null,
      careerStep: 'idle',
      selectedPath: null,
      currentStage: 1,
      planningStartedAt: null,
      currentPlanId: null,
      cachedPlan: null,

      setAssessmentId: (id) => set({ assessmentId: id }),
      setProfileDraft: (draft) => set({ profileDraft: draft }),
      setAssessStatus: (status, error) =>
        set({
          assessStatus: status,
          assessError: error ?? null,
          // 进入 loading 时记录开始时间；其它状态清空
          assessStartedAt: status === 'loading' ? (Date.now()) : null,
        }),
      setAssessmentPollingEnabled: (enabled) => set({ assessmentPollingEnabled: enabled }),
      setAssessmentProgress: (progress) => set({ assessmentProgress: progress }),
      setReportData: (data) => set({ reportData: data }),
      setMatchData: (data) => set({ matchData: data }),
      setSelectedCareer: (code) => set({ selectedCareer: code }),
      setPlanData: (data) => set({ planData: data }),
      setMatchLoading: (v) => set({ matchLoading: v }),
      setMatchError: (e) => set({ matchError: e }),
      setPlanLoading: (v) => set({ planLoading: v }),
      setPlanError: (e) => set({ planError: e }),
      setCareerStep: (step) =>
        set((state) => ({
          careerStep: typeof step === 'function' ? step(state.careerStep) : step,
        })),
      setSelectedPath: (path) => set({ selectedPath: path }),
      setCurrentStage: (stage) => set({ currentStage: stage }),
      setPlanningStartedAt: (ts) => set({ planningStartedAt: ts }),
      setCurrentPlanId: (id) => set({ currentPlanId: id }),
      setCachedPlan: (plan) => set({ cachedPlan: plan }),
      resetDownstream: () =>
        set({
          reportData: null,
          matchData: null,
          planData: null,
          selectedCareer: null,
          assessmentProgress: null,
          assessmentPollingEnabled: false,
          matchLoading: false,
          matchError: null,
          planLoading: false,
          planError: null,
          careerStep: 'idle',
          selectedPath: null,
          currentStage: 1,
          planningStartedAt: null,
          currentPlanId: null,
          cachedPlan: null,
        }),
      resetPlan: () =>
        set({
          selectedCareer: null,
          planData: null,
          planError: null,
          careerStep: 'idle',
          selectedPath: null,
          currentStage: 1,
          planningStartedAt: null,
        }),
      resetAll: () =>
        set({
          assessmentId: null,
          profileDraft: null,
          reportData: null,
          matchData: null,
          planData: null,
          selectedCareer: null,
          assessStatus: 'idle',
          assessError: null,
          assessmentProgress: null,
          assessmentPollingEnabled: false,
          careerStep: 'idle',
          selectedPath: null,
          currentStage: 1,
          planningStartedAt: null,
          currentPlanId: null,
        }),
    }),
    {
      name: 'career-app',
      version: 2,  // 路线推荐格式变更，旧 localStorage 自动失效
      // cachedPlan 仅存内存，不写 localStorage（数据量大）
      partialize: (state) => {
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        const { cachedPlan: _cp, ...rest } = state;
        return rest;
      },
      // loading 状态不持久化，刷新后重置
      onRehydrateStorage: () => (state) => {
        if (state?.assessStatus === 'loading') {
          state.assessStatus = 'idle';
        }
        if (state) {
          state.planLoading = false;
          state.matchLoading = false;
          state.assessmentPollingEnabled = Boolean(state.assessmentId && !state.reportData);
          if (state.planData && state.selectedCareer) {
            state.careerStep = 'report';
          } else if (state.matchData) {
            state.careerStep = 'select';
          } else {
            state.careerStep = 'idle';
          }
          state.planningStartedAt = null;
        }
      },
    },
  ),
);
