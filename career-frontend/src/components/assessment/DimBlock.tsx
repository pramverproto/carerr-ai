import React from 'react';
import { Collapse, Tag, Progress, Alert } from 'antd';
import { LockOutlined } from '@ant-design/icons';
import type {
  DimBlock as DimBlockType,
  SubDimensionEntry,
  InferenceSignal,
  EstimateRange,
} from '@/types';

const { Panel } = Collapse;

const CONFIDENCE_COLOR: Record<string, string> = {
  高: 'green',
  中: 'blue',
  低: 'orange',
};

const TAG_COLOR: Record<string, string> = {
  highlight: 'green',
  focus: 'orange',
  neutral: 'default',
};

const TAG_LABEL: Record<string, string> = {
  highlight: '优势',
  focus: '待提升',
  neutral: '常规',
};

interface Props {
  block: DimBlockType;
}

// ------------------------------------------------------------------ //
//  子维度渲染（扁平结构）                                                //
// ------------------------------------------------------------------ //

function renderSubDimension(entry: SubDimensionEntry, idx: number) {
  try {
    const name = entry.name || `子维度 ${idx + 1}`;
    const score = typeof entry.score === 'number' ? entry.score : null;
    const confidence = entry.confidence as string | undefined;
    const tag = entry.tag as string | undefined;
    const stars = entry.star_rating ?? 0;
    const evidenceBullets = entry.evidence_bullets || [];
    const meaningProse = entry.meaning_prose;
    const cautionProse = entry.caution_prose;
    const careerAdvice = entry.career_advice_prose;
    const subItems = entry.sub_items;

    return (
      <Panel
        key={entry.id || idx}
        header={
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-gray-800">{name}</span>
            {score !== null && (
              <span className="text-blue-600 font-bold">{score.toFixed(1)}</span>
            )}
            {stars > 0 && (
              <span className="text-yellow-500 text-xs">
                {'★'.repeat(stars)}{'☆'.repeat(Math.max(0, 5 - stars))}
              </span>
            )}
            {tag && (
              <Tag color={TAG_COLOR[tag] || 'default'}>{TAG_LABEL[tag] || tag}</Tag>
            )}
            {confidence && (
              <Tag color={CONFIDENCE_COLOR[confidence] || 'default'}>
                置信度 {confidence}
              </Tag>
            )}
          </div>
        }
      >
        {score !== null && (
          <Progress
            percent={Math.round((score / 7) * 100)}
            strokeColor="#1677ff"
            className="mb-3"
          />
        )}

        {/* 解读（必显示） */}
        {meaningProse && (
          <p className="text-gray-600 text-sm leading-relaxed mb-3">
            {meaningProse}
          </p>
        )}

        {/* 证据 bullets */}
        {evidenceBullets.length > 0 && (
          <div className="mb-3">
            <p className="text-xs text-gray-400 mb-1">证据</p>
            <ul className="space-y-1">
              {evidenceBullets.map((bullet, j) => (
                <li
                  key={j}
                  className="text-sm text-gray-600 border-l-2 border-blue-200 pl-3"
                >
                  {bullet}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* work_styles sub_items 分面 */}
        {subItems && Object.keys(subItems).length > 0 && (
          <div className="mb-3">
            <p className="text-xs text-gray-400 mb-2">分面得分</p>
            <div className="grid grid-cols-3 md:grid-cols-4 gap-2">
              {Object.entries(subItems).map(([k, v]) => (
                <div key={k} className="bg-gray-50 rounded px-2 py-1 text-xs">
                  <span className="text-gray-500">{k}</span>
                  <span className="ml-1 text-blue-600 font-medium">
                    {typeof v === 'number' ? v.toFixed(1) : String(v)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* caution prose */}
        {cautionProse && (
          <Alert
            type="warning"
            message={cautionProse}
            showIcon
            className="mb-2"
          />
        )}

        {/* career advice */}
        {careerAdvice && (
          <Alert
            type="success"
            message={careerAdvice}
            showIcon
          />
        )}
      </Panel>
    );
  } catch {
    return (
      <Panel key={idx} header={`子维度 ${idx + 1}`}>
        <p className="text-gray-400 text-sm">数据格式异常</p>
      </Panel>
    );
  }
}

// ------------------------------------------------------------------ //
//  Locked 状态渲染                                                     //
// ------------------------------------------------------------------ //

function renderLocked(block: DimBlockType) {
  const label = block.dimension_label || block.block_id;
  // abilities 字段：indirect_signals + estimate_ranges
  // interests 字段：inferred_signals + inferred_code + inferred_roles
  const signals: InferenceSignal[] =
    (block.indirect_signals as InferenceSignal[]) ||
    (block.inferred_signals as InferenceSignal[]) ||
    [];
  const ranges: EstimateRange[] = (block.estimate_ranges as EstimateRange[]) || [];
  const inferredCode = block.inferred_code;
  const inferredRoles = block.inferred_roles || [];
  const unlockCta = block.unlock_cta;

  return (
    <section className="mb-6 bg-gray-50 rounded-xl p-5 border border-gray-200">
      <div className="flex items-center gap-2 mb-2">
        <LockOutlined className="text-gray-400" />
        <h3 className="font-bold text-gray-600 text-lg">{label}</h3>
        <Tag color="default">未解锁</Tag>
      </div>

      {block.unlock_intro && (
        <p className="text-gray-500 text-sm leading-relaxed mb-4">{block.unlock_intro}</p>
      )}

      {/* 推断信号 */}
      {signals.length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-gray-400 mb-2">从简历中捕捉的信号</p>
          <div className="space-y-2">
            {signals.map((s, i) => (
              <div
                key={i}
                className="bg-white rounded-md p-3 border border-gray-100 text-sm"
              >
                <p className="text-gray-700">{s.signal}</p>
                <p className="text-gray-400 text-xs mt-1">→ 暗示：{s.implies}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* abilities 估计范围 */}
      {ranges.length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-gray-400 mb-2">能力估计范围（1-7）</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            {ranges.map((r) => (
              <div
                key={r.id}
                className="bg-white rounded-md p-3 border border-gray-100"
              >
                <p className="text-sm font-medium text-gray-700">{r.name}</p>
                <p className="text-blue-600 text-lg font-bold">{r.range}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* interests 推断代码 + 角色 */}
      {inferredCode && (
        <div className="mb-3">
          <span className="text-sm text-gray-500">推断 Holland 代码：</span>
          <span className="font-bold text-purple-600 ml-1">{inferredCode}</span>
        </div>
      )}
      {inferredRoles.length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-gray-400 mb-1">可能适合的方向</p>
          <div className="flex flex-wrap gap-1">
            {inferredRoles.map((role, i) => (
              <Tag key={i} color="purple">{role}</Tag>
            ))}
          </div>
        </div>
      )}

      {/* 解锁 CTA */}
      {unlockCta && (
        <Alert
          type="info"
          message={unlockCta.text}
          showIcon
          className="mt-3"
        />
      )}
    </section>
  );
}

// ------------------------------------------------------------------ //
//  pending / error 兜底                                                //
// ------------------------------------------------------------------ //

function renderPending(block: DimBlockType) {
  const label = block.dimension_label || block.block_id;
  return (
    <section className="mb-6 bg-gray-50 rounded-xl p-5 border border-dashed border-gray-300">
      <h3 className="font-bold text-gray-500 text-lg mb-2">{label}</h3>
      <Alert
        type="warning"
        showIcon
        message={
          block.status === 'error'
            ? '该维度评估失败，可能是输入信息不足'
            : '该维度数据暂未就绪'
        }
        description="请返回「个人信息完善」补充更详细的工作经历和个人描述后重新评估"
      />
    </section>
  );
}

// ------------------------------------------------------------------ //
//  主组件                                                              //
// ------------------------------------------------------------------ //

const DimBlock: React.FC<Props> = ({ block }) => {
  if (!block) return null;

  if (block.status === 'locked') {
    return renderLocked(block);
  }

  const label = block.dimension_label || block.block_id;
  const score = block.overall_score;
  const confidence = block.confidence;
  const summary = block.dimension_summary_prose || block.dimension_summary;
  const subDims = block.sub_dimensions || [];

  // 没有任何内容 → 兜底
  if (!summary && subDims.length === 0 && !block.bigfive_display && !block.persona_tag) {
    return renderPending(block);
  }

  return (
    <section className="mb-6 bg-white rounded-xl border border-gray-100 shadow-sm p-5">
      {/* 标题行 */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="font-bold text-gray-800 text-lg">{label}</h3>
          {block.persona_tag && (
            <Tag color="purple">{block.persona_tag}</Tag>
          )}
        </div>
        <div className="flex items-center gap-2">
          {score != null && (
            <div className="flex items-center justify-center w-12 h-12 rounded-full bg-blue-50 border-2 border-blue-200">
              <span className="text-blue-700 font-bold text-lg">{score.toFixed(1)}</span>
            </div>
          )}
          {confidence && (
            <Tag color={CONFIDENCE_COLOR[confidence] || 'default'}>
              {confidence}置信度
            </Tag>
          )}
        </div>
      </div>

      {/* 摘要 */}
      {summary && (
        <p className="text-gray-600 text-sm mb-4 leading-relaxed">{summary}</p>
      )}

      {/* work_styles bigfive 展示 */}
      {block.bigfive_display && (
        <div className="mb-4 grid grid-cols-5 gap-2">
          {Object.entries(block.bigfive_display).map(([k, v]) => (
            <div key={k} className="text-center">
              <div className="text-xs text-gray-500 mb-1">{k}</div>
              <Progress
                type="circle"
                percent={v}
                size={48}
                format={(p) => `${p}`}
              />
            </div>
          ))}
        </div>
      )}

      {/* 子维度 */}
      {subDims.length > 0 && (
        <Collapse ghost className="mt-2">
          {subDims.map((entry, i) => renderSubDimension(entry, i))}
        </Collapse>
      )}

      {/* skills tech_gap */}
      {block.tech_gap && (block.tech_gap as string[]).length > 0 && (
        <div className="mt-4 pt-3 border-t border-gray-100">
          <span className="text-sm text-gray-500">技术缺口：</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {(block.tech_gap as string[]).map((t, i) => (
              <Tag key={i} color="orange">{t}</Tag>
            ))}
          </div>
        </div>
      )}
    </section>
  );
};

export default DimBlock;
