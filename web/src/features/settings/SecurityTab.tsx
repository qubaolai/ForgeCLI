/** 安全与工具页: 工作区目录, 学习规则, 当前模式下模型看得见的工具。 */

import { useState } from "react";
import type { Administration } from "@/features/administration/useAdministration";

export function SecurityTab({ admin }: { admin: Administration }) {
  return (
    <>
      <h3>工作区目录</h3>
      {admin.workspaceRoots.map((root, index) => (
        <div className="admin-row" key={root.path}>
          <div>
            <strong>{root.path}</strong>
            <small>{root.access === "write" ? "可读写" : "只读"}</small>
          </div>
          {/* 第一个是主目录, 移不掉 —— 移掉之后这个项目就没有可操作的地方了。 */}
          {index > 0 && <button onClick={() => admin.removeWorkspace(root.path)}>移除</button>}
        </div>
      ))}
      <AddRootRow onAdd={admin.addWorkspace} />

      <h3>学习规则</h3>
      {admin.rules.length ? (
        admin.rules.map((rule) => (
          <div className="admin-row" key={rule.rule_id}>
            <div>
              <strong>{rule.label || rule.rule_id}</strong>
              <small>
                {rule.scope} · {rule.match.mode}
              </small>
            </div>
            <button onClick={() => admin.revokeRule(rule.rule_id)}>撤销</button>
          </div>
        ))
      ) : (
        <p className="empty-copy">没有工作区学习规则。</p>
      )}
      <div className="add-root prune-row">
        <button onClick={admin.pruneRules}>清理已过期 / 已撤销的规则</button>
      </div>

      <h3>当前模式下模型可见的工具</h3>
      <p className="field-help">工具集合由模式的能力上界决定; 换模式会改变这份清单。</p>
      {admin.tools.map((tool) => (
        <details className="model-editor tool-entry" key={tool.name}>
          <summary>
            <span>
              <strong>{tool.name}</strong>
              <small>{tool.title}</small>
            </span>
            <small>{tool.declared_capabilities.join(" · ") || "无声明能力"}</small>
          </summary>
          <div className="model-fields">
            <p className="field-help">{tool.description}</p>
          </div>
        </details>
      ))}
      {!admin.tools.length && <p className="empty-copy">当前模式下没有可见工具。</p>}
    </>
  );
}

function AddRootRow({ onAdd }: { onAdd: (path: string, access: string) => void }) {
  const [path, setPath] = useState("");
  const [access, setAccess] = useState("read");
  return (
    <div className="add-root">
      <input value={path} onChange={(event) => setPath(event.target.value)} placeholder="额外目录路径" />
      <select value={access} onChange={(event) => setAccess(event.target.value)}>
        <option value="read">只读</option>
        <option value="write">读写</option>
      </select>
      <button
        onClick={() => {
          if (path.trim()) {
            onAdd(path, access);
            setPath("");
          }
        }}
      >
        添加
      </button>
    </div>
  );
}
