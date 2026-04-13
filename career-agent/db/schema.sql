-- ============================================================
-- Career Assessment — 数据库表结构
-- 数据库：PostgreSQL 15+
-- ============================================================

-- ============================================================
-- 1. 候选人
-- ============================================================
CREATE TABLE candidates (
  id            BIGSERIAL    PRIMARY KEY,
  name          VARCHAR(100) NOT NULL,
  age           INT,
  city          VARCHAR(50),
  current_title VARCHAR(200),
  target_role   VARCHAR(200),
  resume_raw    JSONB,        -- 完整简历原始 JSON（experiences/skills/certifications 等）
  supplement    TEXT,         -- 个人补充说明（职业动机、偏好、价值观等）
  created_at    TIMESTAMPTZ  DEFAULT NOW(),
  updated_at    TIMESTAMPTZ  DEFAULT NOW()
);


-- ============================================================
-- 2. 评估会话
-- ============================================================
CREATE TABLE assessments (
  id               BIGSERIAL    PRIMARY KEY,
  assessment_id    VARCHAR(32)  UNIQUE NOT NULL,  -- uuid4().hex[:12]，如 98b4e695f7a4
  candidate_id     BIGINT       REFERENCES candidates(id),
  status           VARCHAR(20)  DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'done', 'partial', 'failed')),
  data_snapshot    JSONB,        -- 评估时的完整输入快照（bigfive/riasec/quiz_abilities 等测试数据）
  total_elapsed_ms INT,
  total_tokens     INT,
  error_log        JSONB,        -- 失败的子维度记录，如 ["skills/1.1: timeout"]
  created_at       TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_assessments_candidate ON assessments (candidate_id);


-- ============================================================
-- 3. 维度 & 子维度得分
--    每行可以是：
--      sub_dimension_id IS NULL  → 维度总分（代码聚合的 overall_score）
--      sub_dimension_id NOT NULL → 子维度分（模型输出的 sub_dimension_result.score）
-- ============================================================
CREATE TABLE dimension_scores (
  id                  BIGSERIAL    PRIMARY KEY,
  assessment_id       VARCHAR(32)  REFERENCES assessments(assessment_id),
  dimension           VARCHAR(50)  NOT NULL,   -- skills / knowledge / abilities / work_styles / interests / work_values
  sub_dimension_id    VARCHAR(20),             -- 1.1 / 3.2 / 6.4，NULL 表示维度总分行
  sub_dimension_name  VARCHAR(100),
  score               NUMERIC(4,2),            -- 该行对应的分数
  confidence          VARCHAR(20),             -- 高 / 中 / 低 / 无法判断
  elapsed_ms          INT,                     -- 该子维度的耗时
  total_tokens        INT,
  created_at          TIMESTAMPTZ  DEFAULT NOW(),

  UNIQUE (assessment_id, dimension, sub_dimension_id)
);

CREATE INDEX idx_dimension_scores_assessment ON dimension_scores (assessment_id);


-- ============================================================
-- 4. 评分项明细（最细粒度）
-- ============================================================
CREATE TABLE item_scores (
  id                   BIGSERIAL    PRIMARY KEY,
  assessment_id        VARCHAR(32)  REFERENCES assessments(assessment_id),
  dimension            VARCHAR(50)  NOT NULL,
  sub_dimension_id     VARCHAR(20)  NOT NULL,   -- 1.1 / 5（interests 整体）
  item_id              VARCHAR(20)  NOT NULL,   -- 1.1.3 / R / I / A / S / E / C
  item_name_zh         VARCHAR(200),
  onet_element_id      VARCHAR(50),             -- 2.A.1.a 等 O*NET 元素 ID
  score                NUMERIC(3,1),            -- null = 无证据
  score_range_low      NUMERIC(3,1),            -- abilities Mode B 下限
  score_range_high     NUMERIC(3,1),            -- abilities Mode B 上限
  anchor_level_hit     INT,                     -- 最近锚点 Level（2/4/6 或 1/4/7）
  confidence           VARCHAR(20),             -- 高 / 中 / 低 / 无法判断
  evidence             TEXT,                    -- 引用的候选人原文证据
  applicable           BOOLEAN,                 -- skills 特有：false = 不适用该候选人
  keyword_match_count  INT,                     -- interests Mode B：命中关键词数量
  matched_keywords     JSONB,                   -- interests Mode B：命中关键词列表（string[]）
  created_at           TIMESTAMPTZ  DEFAULT NOW(),

  UNIQUE (assessment_id, dimension, item_id)
);

CREATE INDEX idx_item_scores_assessment ON item_scores (assessment_id);
CREATE INDEX idx_item_scores_dimension  ON item_scores (assessment_id, dimension);


-- ============================================================
-- 5. 评估报告（壳）
-- ============================================================
CREATE TABLE assessment_reports (
  id             BIGSERIAL    PRIMARY KEY,
  assessment_id  VARCHAR(32)  REFERENCES assessments(assessment_id),
  format         VARCHAR(20)  DEFAULT 'markdown'
                 CHECK (format IN ('markdown', 'html', 'pdf')),
  full_content   TEXT,         -- 所有 section 拼装后的完整报告（冗余存，方便直接输出）
  version        INT           DEFAULT 1,
  created_at     TIMESTAMPTZ  DEFAULT NOW(),

  UNIQUE (assessment_id, version)
);


-- ============================================================
-- 6. 报告分段内容（可溯源）
--    每个模块单独一行，记录生成元信息
-- ============================================================
CREATE TABLE report_sections (
  id             BIGSERIAL    PRIMARY KEY,
  assessment_id  VARCHAR(32)  REFERENCES assessments(assessment_id),
  report_id      BIGINT       REFERENCES assessment_reports(id),

  -- 模块标识
  -- 枚举值：
  --   narrative_summary   总览叙事摘要
  --   strength_top3       TOP3 优势 + 业务价值
  --   growth_top3         TOP3 成长领域 + 行动处方
  --   dimension_skills            技能维度画像
  --   dimension_knowledge         知识维度画像
  --   dimension_abilities         认知能力维度画像
  --   dimension_work_styles       工作特质维度画像
  --   dimension_interests         职业兴趣分型解读
  --   dimension_work_values       工作价值观解读
  --   score_table         维度得分总览表（规则生成）
  --   method_note         评估方法说明（固定模板）
  section_key    VARCHAR(50)  NOT NULL,

  content        TEXT,         -- 该模块的 Markdown 文本内容
  source         VARCHAR(20)  NOT NULL
                 CHECK (source IN ('llm', 'rule', 'human')),  -- 生成来源
  generated_by   VARCHAR(100),                -- 模型名称，如 claude-sonnet-4-6，human-edited 时为编辑人
  prompt_version VARCHAR(20),                 -- 生成时使用的提示词版本，如 v1.2
  input_snapshot JSONB,                       -- 生成该模块时实际喂给模型的输入数据快照
  created_at     TIMESTAMPTZ  DEFAULT NOW(),

  UNIQUE (report_id, section_key)
);

CREATE INDEX idx_report_sections_report      ON report_sections (report_id);
CREATE INDEX idx_report_sections_assessment  ON report_sections (assessment_id);


-- ============================================================
-- 7. 职业规划报告分块存储
--    每个 Block 独立一行，前端按 block_id 拼装完整报告。
--    block_id 枚举值：
--      gap_overview    岗位匹配总览（维度级 Gap）
--      gap_detail      子维度精细 Gap 分析
--      jd_supplement   JD 市场补充
--      action_plan     分阶段行动计划（Sub-Agent 生成）
--      resume_advice   简历优化建议
-- ============================================================
CREATE TABLE IF NOT EXISTS career_plan_blocks (
  id             BIGSERIAL    PRIMARY KEY,
  assessment_id  VARCHAR(32)  NOT NULL,
  onetsoc_code   VARCHAR(20)  NOT NULL,
  block_id       VARCHAR(50)  NOT NULL,
  block_json     JSON         NOT NULL,
  generated_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

  UNIQUE (assessment_id, onetsoc_code, block_id)
);

CREATE INDEX idx_career_plan_blocks_assessment ON career_plan_blocks (assessment_id, onetsoc_code);
