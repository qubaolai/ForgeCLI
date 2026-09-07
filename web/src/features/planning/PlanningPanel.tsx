/** 计划与待办栏, 含回合边界的计划评审决议。 */

import { Markdown } from "../../Markdown";
import type { PlanIndexView, Planning } from "../../types";


export function PlanningPanel({ planning, index, onResolve, onActivate }: { planning: Planning; index: PlanIndexView | null; onResolve: (decision: string) => void; onActivate: (planId: string) => void }) {
  const plan = planning.plan;
  const todo = planning.todo;
  const others = (index?.plans ?? []).filter((item) => item.plan_id !== index?.active_plan_id);
  if (!plan && !todo && !others.length) {
    return <div className="panel-empty"><span>◇</span><p>当前没有活动计划</p><small>切换到 Plan 模式，让 Forge 先梳理实施方向。</small></div>;
  }
  return <div className="plan-panel">
    {plan && <>
      <span className={`status ${plan.status}`}>{plan.status}</span>
      <Markdown content={planning.markdown ?? `# ${plan.title}\n\n${plan.goal}`} />
      {plan.status === "proposed" && <div className="plan-actions"><button onClick={() => onResolve("reject")}>拒绝</button><button onClick={() => onResolve("approve")}>同意</button><button className="primary" onClick={() => onResolve("approve_and_run")}>同意并执行</button></div>}
    </>}
    {todo?.items?.length ? <section className="todo-block">
      <h4>待办 {todo.items.filter((item) => item.status === "done").length}/{todo.items.length}</h4>
      <ol className="todo-list">
        {todo.items.map((item, position) => <li className={`todo-${item.status}`} key={`${position}-${item.title}`}><i />{item.title}</li>)}
      </ol>
    </section> : null}
    {others.length > 0 && <section className="plan-catalog">
      <h4>其他计划</h4>
      {others.map((item) => <button className="plan-entry" key={item.plan_id} onClick={() => onActivate(item.plan_id)}>
        <strong>{item.title || item.plan_id}</strong><small>{item.plan_id} · r{item.revision} · {item.status}</small>
      </button>)}
    </section>}
  </div>;
}

/** 待答队列只有一条, 卡片按 kind 分支 (ADR-0043 决策 11)。 */
