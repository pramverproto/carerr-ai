import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AssessRequest, ReportResponse, CareerMatchResponse, CareerPlanResponse, PlanSchedule } from '@/types';


type AssessStatus = 'idle' | 'loading' | 'done' | 'error';

interface AppState {
  assessmentId: string | null;
  profileDraft: Partial<AssessRequest> | null;
  reportData: ReportResponse | null;
  matchData: CareerMatchResponse | null;
  planData: CareerPlanResponse | null;
  selectedCareer: string | null;
  assessStatus: AssessStatus;
  assessError: string | null;
  matchLoading: boolean;
  matchError: string | null;
  planLoading: boolean;
  planError: string | null;
  currentPlanId: string | null;
  cachedPlan: PlanSchedule | null;  // in-memory only, not persisted

  setAssessmentId: (id: string | null) => void;
  setProfileDraft: (draft: Partial<AssessRequest>) => void;
  setAssessStatus: (status: AssessStatus, error?: string) => void;
  setReportData: (data: ReportResponse | null) => void;
  setMatchData: (data: CareerMatchResponse | null) => void;
  setSelectedCareer: (code: string | null) => void;
  setPlanData: (data: CareerPlanResponse | null) => void;
  setMatchLoading: (v: boolean) => void;
  setMatchError: (e: string | null) => void;
  setPlanLoading: (v: boolean) => void;
  setPlanError: (e: string | null) => void;
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
      assessError: null,
      matchLoading: false,
      matchError: null,
      planLoading: false,
      planError: null,
      currentPlanId: null,
      cachedPlan: null,

      setAssessmentId: (id) => set({ assessmentId: id }),
      setProfileDraft: (draft) => set({ profileDraft: draft }),
      setAssessStatus: (status, error) =>
        set({ assessStatus: status, assessError: error ?? null }),
      setReportData: (data) => set({ reportData: data }),
      setMatchData: (data) => set({ matchData: data }),
      setSelectedCareer: (code) => set({ selectedCareer: code }),
      setPlanData: (data) => set({ planData: data }),
      setMatchLoading: (v) => set({ matchLoading: v }),
      setMatchError: (e) => set({ matchError: e }),
      setPlanLoading: (v) => set({ planLoading: v }),
      setPlanError: (e) => set({ planError: e }),
      setCurrentPlanId: (id) => set({ currentPlanId: id }),
      setCachedPlan: (plan) => set({ cachedPlan: plan }),
      resetDownstream: () =>
        set({
          reportData: null,
          matchData: null,
          planData: null,
          selectedCareer: null,
          matchLoading: false,
          matchError: null,
          planLoading: false,
          planError: null,
          currentPlanId: null,
          cachedPlan: null,
        }),
      resetPlan: () =>
        set({ selectedCareer: null, planData: null, planError: null }),
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
          currentPlanId: null,
        }),
    }),
    {
      name: 'career-app',
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
        }
      },
    },
  ),
);
