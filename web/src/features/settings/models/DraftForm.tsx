/**
 * 一组字段一起改、一次保存。
 *
 * 逐项保存的问题不只是点击次数: 每次保存都要重新拉一遍配置, 而重新拉配置会把同一张表单里
 * 其他还没保存的输入冲掉 —— 用户填了三格, 保存第一格, 另外两格就没了。
 */

import { useEffect, useMemo, useState } from "react";

export type DraftField = { key: string; label: string; value: string; choices?: string[] };

/**
 * 一组字段一起改、一次保存。
 *
 * 逐项保存的问题不只是点击次数: 每次保存都要重新拉一遍配置, 而重新拉配置会把同一张表单里
 * 其他还没保存的输入冲掉 —— 用户填了三格, 保存第一格, 另外两格就没了。
 */
export function DraftForm({
  fields,
  onSave,
  className = "runtime-settings",
}: {
  fields: DraftField[];
  onSave: (changed: Record<string, string>) => void;
  className?: string;
}) {
  const committed = useMemo(
    () => Object.fromEntries(fields.map((field) => [field.key, field.value])),
    [fields],
  );
  const signature = fields.map((field) => `${field.key}=${field.value}`).join("\u0001");
  const [draft, setDraft] = useState<Record<string, string>>(committed);
  // 服务端的值变了 (保存成功, 或别处改动后刷新) 才重置草稿, 不在每次渲染时覆盖输入。
  useEffect(() => {
    setDraft(
      Object.fromEntries(
        signature.split("\u0001").map((pair) => {
          const at = pair.indexOf("=");
          return [pair.slice(0, at), pair.slice(at + 1)];
        }),
      ),
    );
  }, [signature]);
  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => committed[key] !== value),
  );
  const dirty = Object.keys(changed).length;
  return (
    <div className={`draft-form ${className}`}>
      {fields.map((field) => (
        <label
          className={`editable-value ${draft[field.key] !== committed[field.key] ? "dirty" : ""}`}
          key={field.key}
        >
          <span>{field.label}</span>
          {field.choices ? (
            <select
              value={draft[field.key] ?? ""}
              onChange={(event) => setDraft((state) => ({ ...state, [field.key]: event.target.value }))}
            >
              {field.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={draft[field.key] ?? ""}
              onChange={(event) => setDraft((state) => ({ ...state, [field.key]: event.target.value }))}
            />
          )}
        </label>
      ))}
      <div className="draft-actions">
        <span>{dirty ? `${dirty} 项待保存` : "没有改动"}</span>
        <button onClick={() => setDraft(committed)} disabled={!dirty}>
          撤销
        </button>
        <button className="primary" onClick={() => onSave(changed)} disabled={!dirty}>
          保存
        </button>
      </div>
    </div>
  );
}
