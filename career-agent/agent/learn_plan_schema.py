"""任务计划（Learn Plan）相关的四张新表。

不复用旧 plan_schedules/plan_weeks/plan_daily_tasks。
旧表继续服务 Archive 页，新表服务 PlanProgress 页。

表结构：
  learn_outlines   — 学习大纲（模块+权重），一个计划对应一行
  learn_months     — 月度骨架
  learn_weeks      — 周度骨架 + 懒物化状态
  learn_tasks      — 规范化任务（每任务一行，带感悟/打分/贡献）
"""

CREATE_LEARN_OUTLINES = """
CREATE TABLE IF NOT EXISTS learn_outlines (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    plan_id         VARCHAR(32)  NOT NULL UNIQUE,
    user_id         BIGINT       NOT NULL,
    assessment_id   VARCHAR(32)  NOT NULL,
    stage_code      VARCHAR(40)  NOT NULL,        -- 目标 Stage 的岗位/路径编码
    stage_title     VARCHAR(200) NULL,            -- 目标 Stage 名称（冗余，便于展示）
    modules         JSON         NOT NULL,         -- [{id, title, weight, est_hours, target_dims, completion_criteria}]
    total_weight    FLOAT        NOT NULL DEFAULT 100,
    estimated_weeks INT          NULL,             -- agent 估算的总周数
    total_weeks     INT          NULL,             -- roadmap 确定后的真实周数
    user_preference TEXT         NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending',
                    -- pending: 大纲已生成待用户确认
                    -- confirmed: 用户已确认
                    -- planning: roadmap 生成中
                    -- ready: roadmap+Week1 已就绪，正式使用
                    -- error: 生成失败
    error_msg       TEXT         NULL,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user (user_id),
    KEY idx_assessment (assessment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_LEARN_MONTHS = """
CREATE TABLE IF NOT EXISTS learn_months (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    plan_id         VARCHAR(32)  NOT NULL,
    month_num       INT          NOT NULL,
    theme           VARCHAR(200) NOT NULL,
    month_goal      TEXT         NULL,
    covers_modules  JSON         NULL,             -- [{module_id, share}]
    weight_share    FLOAT        NOT NULL DEFAULT 0,
    status          VARCHAR(20)  NOT NULL DEFAULT 'ready',
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_plan_month (plan_id, month_num),
    KEY idx_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_LEARN_WEEKS = """
CREATE TABLE IF NOT EXISTS learn_weeks (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    plan_id         VARCHAR(32)  NOT NULL,
    month_id        BIGINT UNSIGNED NULL,
    week_num        INT          NOT NULL,          -- 全局周序号 1..N
    week_in_month   INT          NOT NULL DEFAULT 1,
    theme           VARCHAR(200) NOT NULL,
    week_goal       TEXT         NULL,
    covers_modules  JSON         NULL,
    weight_share    FLOAT        NOT NULL DEFAULT 0,
    daily_status    VARCHAR(20)  NOT NULL DEFAULT 'skeleton',
                    -- skeleton / materializing / ready / error
    error_msg       TEXT         NULL,
    materialized_at TIMESTAMP    NULL,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_plan_week (plan_id, week_num),
    KEY idx_plan (plan_id),
    KEY idx_month (month_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_LEARN_TASKS = """
CREATE TABLE IF NOT EXISTS learn_tasks (
    id                    BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    plan_id               VARCHAR(32)   NOT NULL,
    week_id               BIGINT UNSIGNED NOT NULL,
    module_id             VARCHAR(50)   NULL,
    order_in_queue        INT           NOT NULL,   -- 跨周全局排序
    order_in_week         INT           NOT NULL DEFAULT 0,
    title                 VARCHAR(300)  NOT NULL,
    description           TEXT          NULL,
    task_type             VARCHAR(30)   NULL,       -- reading/coding/project/exercise/review
    est_minutes           INT           NULL,
    target_dims           JSON          NULL,
    raw_weight            FLOAT         NULL,       -- agent 原始相对权重
    actual_contribution   FLOAT         NOT NULL DEFAULT 0,  -- 归一化后的全局贡献
    completion_criteria   TEXT          NULL,
    status                VARCHAR(20)   NOT NULL DEFAULT 'pending',  -- pending/done/skipped
    reflection            TEXT          NULL,
    grade_score           FLOAT         NULL,       -- 0.0-1.0
    grade_comment         VARCHAR(500)  NULL,
    final_contribution    FLOAT         NULL,       -- actual_contribution × grade_score
    completed_at          TIMESTAMP     NULL,
    created_at            TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    KEY idx_plan_status (plan_id, status, order_in_queue),
    KEY idx_week (week_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

ALL_CREATE_SQLS = [
    CREATE_LEARN_OUTLINES,
    CREATE_LEARN_MONTHS,
    CREATE_LEARN_WEEKS,
    CREATE_LEARN_TASKS,
]
