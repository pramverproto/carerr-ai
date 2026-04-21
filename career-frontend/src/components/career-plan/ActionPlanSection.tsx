import React from 'react';
import { Tag } from 'antd';
import type { ActionPlanBlock } from '@/types';
import {
  PHASE_BG, PHASE_BORDER, PHASE_COLORS, PHASE_TEXT,
  SEVERITY_COLOR, SEVERITY_LABEL,
} from './constants';

function ActionPlanSection({ block }: { block: ActionPlanBlock }) {
  return (
    <section id="plan-action_plan" className="mb-6">
      <h3 className="font-bold text-gray-800 dark:text-gray-100 text-lg mb-4">分阶段行动计划</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {block.phases.map((phase, pi) => (
          <div
            key={phase.phase_id}
            className={`rounded-xl border p-4 ${PHASE_BG[pi]} ${PHASE_BORDER[pi]}`}
            style={{ borderLeftWidth: 4, borderLeftColor: PHASE_COLORS[pi] }}
          >
            <p className={`font-bold text-sm mb-1 ${PHASE_TEXT[pi]}`}>{phase.label}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{phase.focus}</p>
            <div className="space-y-3">
              {phase.actions.map((action, ai) => (
                <div key={ai} className="bg-white dark:bg-gray-800 rounded-lg p-3 shadow-sm">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">📌 {action.item}</span>
                    {action.severity && (
                      <Tag color={SEVERITY_COLOR[action.severity] || 'default'} className="text-xs">
                        {SEVERITY_LABEL[action.severity] || action.severity}
                      </Tag>
                    )}
                  </div>
                  <p className="text-gray-600 dark:text-gray-300 text-xs mt-1"><strong>行动：</strong>{action.action}</p>
                  <p className="text-gray-600 dark:text-gray-300 text-xs mt-0.5"><strong>产出：</strong>{action.deliverable}</p>
                  <p className="text-blue-600 dark:text-blue-400 text-xs mt-0.5"><strong>资源：</strong>{action.resource}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default React.memo(ActionPlanSection);
