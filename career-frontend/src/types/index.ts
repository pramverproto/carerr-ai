// ── 提交评估请求 ──────────────────────────────────────────────────────

export interface ResumeExperience {
  company: string;
  title: string;
  duration: string;
  responsibilities: string[];
  key_projects?: { name: string; description?: string; role?: string }[];
}

export interface ResumeSection {
  candidate: {
    name: string;
    age?: number;
    education?: string;
    current_title?: string;
    years_of_experience?: number;
  };
  experiences: ResumeExperience[];
  skills: string[];
  certifications?: string[];
}

export interface BigFiveSection {
  O: number; C: number; E: number; A: number; ES: number;
  facets?: Record<string, number>;
}

export interface RiasecSection {
  R: number; I: number; A: number; S: number; E: number; C: number;
  holland_code?: string;
}

export interface AssessRequest {
  session_id?: string;
  resume: ResumeSection;
  supplement: string;
  bigfive?: BigFiveSection;
  riasec?: RiasecSection;
  quiz_abilities?: {
    verbal: { score: number; percentile: number };
    reasoning: { score: number; percentile: number };
    quantitative: { score: number; percentile: number };
  };
  quiz_knowledge?: {
    business_management: { score: number; percentile: number };
    tech_engineering: { score: number; percentile: number };
    humanities_social: { score: number; percentile: number };
  };
}

// ── /assess 响应 ──────────────────────────────────────────────────────

export interface AssessResponse {
  assessment_id: string;
  status: string;
  elapsed_ms: number;
  summary?: Record<string, unknown>;
}

// ── /report/{assessment_id} 响应 ──────────────────────────────────────

export interface RadarDimension {
  id: string;
  name: string;
  score: number | null;
  confidence: string | null;
  status: string;
  source: string;
}

/**
 * 报告块中的子维度条目（DimReportAgent 输出的扁平结构）。
 * 对应后端 _call_dim_report_agent 返回的 JSON。
 */
export interface SubDimensionEntry {
  id?: string;
  name?: string;
  score?: number | null;
  confidence?: '高' | '中' | '低' | string;
  tag?: 'highlight' | 'focus' | 'neutral' | string;
  star_rating?: number;
  evidence_bullets?: string[];
  meaning_prose?: string;
  caution_prose?: string;           // work_styles 子维度可能有
  career_advice_prose?: string;     // work_values 子维度可能有
  sub_items?: Record<string, number>; // work_styles 的分面得分
  collapsed?: boolean;
  [key: string]: unknown;
}

/** 锁定维度下的推断信号条目（abilities 用 indirect_signals, interests 用 inferred_signals） */
export interface InferenceSignal {
  signal: string;
  implies: string;
}

/** 锁定维度下的能力估计范围（abilities 用） */
export interface EstimateRange {
  id: string;
  name: string;
  range: string;
}

/** 锁定维度的解锁 CTA */
export interface UnlockCTA {
  text: string;
  test_type: string;
}

export interface DimBlock {
  block_id: string;
  status?: 'done' | 'locked' | 'pending' | 'error' | string;
  dimension_label?: string;
  overall_score?: number | null;
  confidence?: string;
  dimension_summary?: string;
  dimension_summary_prose?: string;
  sub_dimensions?: SubDimensionEntry[];
  highlights?: string[];
  focus_areas?: string[];
  // work_styles 特有
  bigfive_display?: Record<string, number>;
  // abilities/interests locked 状态特有
  unlock_intro?: string;
  indirect_signals?: InferenceSignal[];   // abilities 用
  inferred_signals?: InferenceSignal[];   // interests 用
  estimate_ranges?: EstimateRange[];
  unlock_cta?: UnlockCTA;
  inferred_code?: string;
  inferred_roles?: string[];
  suitable_roles?: string[];
  holland_code?: string;
  // work_values 维度级 persona
  persona_tag?: string;
  // skills 维度级技术缺口
  tech_gap?: string[];
  [key: string]: unknown;
}

export interface ReportBlocks {
  header: {
    block_id: string;
    assessment_id: string;
    data_sources: Record<string, boolean>;
    generated_at?: string;
  };
  radar: {
    block_id: string;
    dimensions: RadarDimension[];
    confidence_legend: Record<string, string>;
  };
  overview: {
    block_id: string;
    persona_label?: string;
    narrative_intro?: string;
    top_cards?: { title: string; description: string }[];
    next_direction?: string;
    keywords?: string[];
  };
  skills: DimBlock;
  knowledge: DimBlock;
  abilities: DimBlock;
  work_styles: DimBlock;
  interests: DimBlock;
  work_values: DimBlock;
  action: {
    block_id: string;
    top3_strengths?: {
      ref_dimension?: string;
      ref_sub_id?: string;
      title?: string;
      career_meaning?: string;
      how_to_amplify?: string[];
    }[];
    top3_improvements?: {
      ref_dimension?: string;
      ref_sub_id?: string;
      title?: string;
      current_state?: string;
      target_state?: string;
      action_plan?: Record<string, string>;
      expected_outcome?: string;
    }[];
  };
  unlock: {
    block_id: string;
    items?: { test_type: string; locked: boolean; title: string; duration_min: number; teaser: string }[];
  };
  methodology: {
    block_id: string;
    framework?: string;
    scale?: string;
    score_guide?: Record<string, string>;
    disclaimer?: string;
    [key: string]: unknown;
  };
}

export interface ReportResponse {
  assessment_id: string;
  blocks: ReportBlocks;
}

// ── /career/match 响应 ────────────────────────────────────────────────

/** 旧版单岗位推荐（兼容保留） */
export interface CareerRecommendation {
  onetsoc_code: string;
  title: string;
  match_reason?: string;
  match_score?: number;
  key_gaps?: string[];
  jd_market_signal?: string;
  typical_jd_skills?: string[];
  source?: string;  // "onet+jd" | "jd_direct"
  [key: string]: unknown;
}

/** 路线中的单个阶段 */
export interface CareerPathStage {
  stage: number;
  title: string;
  timeframe: string;
  salary_range: string | null;
  match_score?: number;          // Stage 1 有
  key_skills: string[];
  match_reason?: string;         // Stage 1 有
  key_gaps?: string[];           // Stage 1 有
  transition_from_prev?: string; // Stage 2+ 有
}

/** 一条职业路线推荐 */
export interface CareerPathRecommendation {
  path_name: string;
  path_code: string;             // path-xxxxxxxx
  path_summary: string;
  overall_score: number;
  market_signal: string;
  stages: CareerPathStage[];
}

export interface CareerMatchResponse {
  assessment_id: string;
  result: {
    recommended?: CareerPathRecommendation[];
    // 兼容旧版 agent 直接返回数组的情况
    [key: string]: unknown;
  };
  elapsed_ms: number;
}

/** 路线进度 */
export interface CareerPathProgress {
  path_code: string;
  path_data: CareerPathRecommendation;
  current_stage: number;
  stage_history: { stage: number; completed_at: string; user_note: string }[];
}

// ── /career/plan 响应 ─────────────────────────────────────────────────

// ─── Block 1: match_overview ───────────────────────────────────────

export interface DimComparison {
  dimension: string;
  label: string;
  candidate_score: number;
  onet_required: number;
  gap: number;
  status: string;
}

export interface KeyFactor {
  factor: string;
  impact: 'positive' | 'negative' | 'neutral';
  note: string;
}

export interface MatchOverviewBlock {
  block_id: 'match_overview';
  occupation_title: string;
  onetsoc_code: string;
  rule_based: {
    weight: number;
    score: number;
    dim_comparison: DimComparison[];
  };
  llm_analysis: {
    weight: number;
    score: number;
    narrative: string;
    key_factors: KeyFactor[];
  };
  final_score: number;
  verdict: '高度匹配' | '中高匹配' | '潜力匹配' | '不建议';
}

// ─── Block 2: jd_recommendations ──────────────────────────────────

export interface JDMatchAnalysis {
  strengths: string[];
  concerns: string[];
  entry_difficulty: 'easy' | 'moderate' | 'hard';
  verdict: string;
}

export interface JDPosition {
  rank: number;
  title: string;
  company_type: string;
  salary_range: string | null;
  match_score: number;
  full_jd: string;
  key_responsibilities: string[];
  required_qualifications: string[];
  role_explanation: string;
  match_analysis: JDMatchAnalysis;
}

export interface JdRecommendationsBlock {
  block_id: 'jd_recommendations';
  positions: JDPosition[];
}

// ─── Block 3: gap_analysis ────────────────────────────────────────

export interface GapItem {
  area: string;
  severity: 'high' | 'medium' | 'low';
  required: string;
  current: string;
  how_to_close: string;
  related_dimension: string;
}

export interface StrengthItem {
  area: string;
  required: string;
  current: string;
  leverage: string;
  related_dimension: string;
}

export interface GapAnalysisBlock {
  block_id: 'gap_analysis';
  gaps: GapItem[];
  strengths: StrengthItem[];
  summary: string;
}

// ─── Block 4: action_plan ─────────────────────────────────────────

export interface ActionItem {
  item: string;
  severity: string | null;
  action: string;
  deliverable: string;
  resource: string;
}

export interface ActionPhase {
  phase_id: string;
  label: string;
  focus: string;
  actions: ActionItem[];
}

export interface ActionPlanBlock {
  block_id: 'action_plan';
  phases: ActionPhase[];
}

// ─── Block 5: future_outlook ──────────────────────────────────────

export interface FutureStageOutlook {
  stage: number;
  title: string;
  timeframe: string;
  salary_range: string | null;
  key_skills: string[];
  transition_tips: string;
  preparation_now: string;
}

export interface FutureOutlookBlock {
  block_id: 'future_outlook';
  current_stage: number;
  current_title: string;
  next_stages: FutureStageOutlook[];
  path_narrative: string;
}

// ─── Combined ─────────────────────────────────────────────────────

export interface CareerPlanBlocks {
  match_overview?: MatchOverviewBlock;
  jd_recommendations?: JdRecommendationsBlock;
  gap_analysis?: GapAnalysisBlock;
  action_plan?: ActionPlanBlock;
  future_outlook?: FutureOutlookBlock;
}

export interface CareerPlanResponse {
  assessment_id: string;
  onetsoc_code: string;
  status: string;
  blocks: CareerPlanBlocks;
  elapsed_ms: number;
}

// ── /plan-schedule 类型 ───────────────────────────────────────────────

export interface DailyTask {
  id: string;
  title: string;
  duration_min: number;
  type: 'study' | 'practice' | 'network' | 'apply' | string;
  description: string;
}

export interface PlanDay {
  day_number: number;
  date: string;
  tasks: DailyTask[];
  completed_ids: string[];
  task_notes: Record<string, string>;
}

export interface PlanWeek {
  week_number: number;
  theme: string;
  focus: string;
  weekly_goals: string[];
  phase_ref: string | null;
  days: PlanDay[];
}

export interface PlanSchedule {
  plan_id: string;
  assessment_id: string;
  onetsoc_code: string;
  duration_weeks: number;
  start_date: string;
  status: 'weekly_draft' | 'generating_daily' | 'daily_ready' | string;
  weeks: PlanWeek[];
}

export interface PlanListItem {
  plan_id: string;
  duration_weeks: number;
  start_date: string;
  status: string;
  created_at: string;
}

export interface WeeklyPlanResponse {
  plan_id: string;
  status: string;
  weeks: Omit<PlanWeek, 'days'>[];
}

// ── /archive 类型 ────────────────────────────────────────────────────

export interface ArchiveItem {
  assessment_id: string;
  name: string;
  current_title: string;
  education: string;
  status: string;
  created_at: string | null;
  career_count: number;
  plan_count: number;
}

export interface ArchiveDetailProfile {
  name: string;
  age?: number | null;
  education: string;
  current_title: string;
  years_of_experience?: number | null;
  skills: string[];
  certifications?: string[];
  experiences: {
    company: string;
    title: string;
    duration: string;
    responsibilities: string[];
  }[];
  supplement: string;
}

export interface ArchiveDimension {
  score: number | null;
  confidence: string | null;
  summary: string | null;
  highlights: string[];
  focus_areas: string[];
  status: string;
}

export interface ArchiveCareer {
  onetsoc_code: string;
  title?: string;
  match_score?: number | null;
  verdict?: string;
}

export interface ArchivePlan {
  plan_id: string;
  onetsoc_code: string;
  duration_weeks: number;
  start_date: string | null;
  status: string;
  created_at: string | null;
  total_tasks: number;
  completed_tasks: number;
}

export interface ArchiveDetail {
  assessment_id: string;
  status: string;
  created_at: string | null;
  profile: ArchiveDetailProfile;
  dimensions: Record<string, ArchiveDimension>;
  careers: ArchiveCareer[];
  plans: ArchivePlan[];
}

export interface Milestone {
  date: string | null;
  type: 'assessment' | 'career_plan' | 'task_completed' | 'week_completed';
  title: string;
  description: string;
  assessment_id: string;
}
