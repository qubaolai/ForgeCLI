/** 待答卡片。审批与提问同一条队列 (ADR-0043 决策 3), 所以入口只有 PromptCard 一个,
 * 按 kind 分派到审批卡或问题卡。
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { ChevronIcon, ShieldIcon } from "../../icons";
import {
  approvalOf,
  canLearn,
  defaultOpenSections,
  headlineOf,
  isConsequential,
  learnHintOf,
  severityOf,
  shownTargetGroups,
} from "../../approvalModel";
import type { Approval, Prompt } from "../../approvalModel";
import { QuestionCard } from "../../QuestionCard";
import type { ResolvePrompt } from "../../QuestionCard";

/** 待答队列只有一条, 卡片按 kind 分支 (ADR-0043 决策 11)。 */
export function PromptCard({ prompt, onResolve }: { prompt: Prompt; onResolve: ResolvePrompt }) {
  if (prompt.kind === "question") return <QuestionCard prompt={prompt} onResolve={onResolve} />;
  return (
    <ApprovalCard
      approval={approvalOf(prompt)}
      onResolve={(id, choice, text) => {
        void onResolve(id, choice, text).catch(() => undefined);
      }}
    />
  );
}

export function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: Approval;
  onResolve: (id: string, choice: string, text?: string) => void;
}) {
  const view = approval.view;
  const severity = severityOf(approval);
  const openBy = defaultOpenSections(approval);
  const targets = shownTargetGroups(approval);
  const root = view.workspace_roots[0] ?? "";
  return (
    <section className={`approval-card sev-${severity}`}>
      <span className="ac-stripe" />
      <header>
        <span className="ac-badge">
          <ShieldIcon />
        </span>
        <div className="ac-heading">
          <h2>{headlineOf(approval)}</h2>
          <p>{approval.mandatory ? "这一档不能记成规则, 每次都会问" : "确认下面的内容再决定"}</p>
        </div>
        <span className="ac-chips">
          <span className="ac-chip tool">{view.tool_name}</span>
          {view.mode && <span className="ac-chip">{view.mode}</span>}
        </span>
      </header>

      {/* 命令永远可见: 一个安全决策的默认态不该是"什么都没说"。 */}
      <pre className="ac-cmd">{view.raw_command}</pre>
      {root && (
        <p className="ac-root" title={root}>
          工作区 {root}
        </p>
      )}

      <div className="ac-counts">
        {view.counts.map((item) => (
          <span className="ac-count" data-on={item.count > 0 ? "1" : "0"} key={item.label}>
            {item.label} <b>{item.count}</b>
          </span>
        ))}
      </div>

      <div className="ac-body">
        {view.script_snapshots.length > 0 && (
          <ApprovalSection
            title="脚本正文"
            meta={scriptMeta(view.script_snapshots)}
            defaultOpen={openBy.scripts}
          >
            {view.script_snapshots.map((snapshot, index) => (
              <div className="ac-file" key={`${snapshot.path}-${index}`}>
                <div className="ac-file-head">
                  <code>{snapshot.path || snapshot.origin || "内联脚本"}</code>
                  <span>{snapshot.language}</span>
                </div>
                <pre className="ac-pre">{snapshot.source}</pre>
              </div>
            ))}
          </ApprovalSection>
        )}

        {view.content_previews.length > 0 && (
          <ApprovalSection
            title="写入预览"
            meta={`${view.content_previews.length} 个文件`}
            defaultOpen={openBy.previews}
          >
            {view.content_previews.map((preview, index) => (
              <div className="ac-file" key={`${preview.path}-${index}`}>
                <div className="ac-file-head">
                  <code>{preview.path}</code>
                </div>
                <pre className="ac-pre">{preview.content}</pre>
                {preview.truncated && <p className="ac-trunc">已截断</p>}
              </div>
            ))}
          </ApprovalSection>
        )}

        {isConsequential(approval) && targets.length > 0 && (
          <ApprovalSection
            title="目标清单"
            meta={`${targets.reduce((total, group) => total + group.paths.length, 0)} 个路径`}
            defaultOpen={openBy.targets}
          >
            {targets.map((group) => (
              <div className="ac-tgroup" key={group.label}>
                <span className="ac-tlabel">{group.label}</span>
                <div className="ac-tpaths">
                  {group.paths.map((path) => (
                    <code key={path} title={path}>
                      {path}
                    </code>
                  ))}
                </div>
              </div>
            ))}
          </ApprovalSection>
        )}
      </div>

      <footer>
        <p className="ac-learn">{learnHintOf(approval)}</p>
        <span className="ac-btns">
          <button className="ac-deny" onClick={() => onResolve(approval.approval_id, "deny")}>
            拒绝
          </button>
          <button disabled={!canLearn(approval)} onClick={() => onResolve(approval.approval_id, "workspace")}>
            始终允许
          </button>
          <button className="primary" onClick={() => onResolve(approval.approval_id, "once")}>
            允许一次
          </button>
        </span>
      </footer>
    </section>
  );
}

export function scriptMeta(scripts: Approval["view"]["script_snapshots"]) {
  const lines = scripts.reduce((total, item) => total + item.source.split("\n").length, 0);
  return scripts.length > 1 ? `${scripts.length} 段 · ${lines} 行` : `${lines} 行`;
}

/** 证据分块。收的是体量, 不是事实的存在 —— 所以标题与条目数在收起时也看得见。 */
export function ApprovalSection({
  title,
  meta,
  defaultOpen,
  children,
}: {
  title: string;
  meta: string;
  defaultOpen: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="ac-sec" open={open}>
      <summary
        onClick={(event) => {
          event.preventDefault();
          setOpen((value) => !value);
        }}
      >
        <ChevronIcon className="ac-sec-caret" />
        <b>{title}</b>
        <span className="ac-sec-meta">{meta}</span>
      </summary>
      <div className="ac-sec-body">{children}</div>
    </details>
  );
}
