import { useRef, useState } from "react";
import type { Prompt } from "./approvalModel";

export type ResolvePrompt = (
  id: string,
  choice: string,
  text?: string,
  selectedValues?: string[],
  skipped?: boolean,
) => Promise<void>;

/** 所有选项均来自后端，文案以纯文本渲染。推荐项不自动选中。 */
export function QuestionCard({ prompt, onResolve }: { prompt: Prompt; onResolve: ResolvePrompt }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const multiple = prompt.selection_mode === "multiple";
  async function answer(skipped: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setError("");
    try {
      await onResolve(prompt.prompt_id, "", skipped ? "" : text.trim(), skipped ? [] : selected, skipped);
    } catch (reason) {
      setError((reason as Error).message || "提交失败，请重试");
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }
  return (
    <section className="question-card" aria-label="需要你的意见">
      <header>
        <span>Forge · 需要你的意见</span>
        <span role="status">{sending ? "正在提交…" : "等待回答"}</span>
      </header>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (selected.length || text.trim()) void answer(false);
        }}
      >
        <h2 id={`question-${prompt.prompt_id}`}>{prompt.title}</h2>
        {prompt.body && <p className="question-description">{prompt.body}</p>}
        <div role="group" aria-labelledby={`question-${prompt.prompt_id}`}>
          {prompt.choices.map((option) => (
            <label className="question-option" key={option.value}>
              <input
                type={multiple ? "checkbox" : "radio"}
                name={prompt.prompt_id}
                value={option.value}
                checked={selected.includes(option.value)}
                disabled={sending}
                onChange={(event) =>
                  setSelected((current) =>
                    multiple
                      ? event.target.checked
                        ? [...current, option.value]
                        : current.filter((value) => value !== option.value)
                      : [option.value],
                  )
                }
              />
              <span>
                <span className="question-option-title">{option.label}</span>
                {prompt.recommended_option_id === option.value && <em>推荐</em>}
                <small>{option.detail}</small>
              </span>
            </label>
          ))}
        </div>
        {prompt.free_text && (
          <label className="question-note">
            补充说明（可选）
            <textarea
              rows={2}
              value={text}
              disabled={sending}
              onChange={(event) => setText(event.target.value)}
              placeholder="也可以不选选项，直接写下你的想法…"
            />
          </label>
        )}
        {error && <p role="alert">{error}</p>}
        <footer>
          <span aria-live="polite">
            {prompt.choices.length
              ? `${multiple ? "可多选" : "单选"} · 已选 ${selected.length} 项`
              : "可以直接回答"}
          </span>
          {prompt.allow_skip && (
            <button type="button" disabled={sending} onClick={() => void answer(true)}>
              跳过本题
            </button>
          )}
          <button className="primary" type="submit" disabled={sending || (!selected.length && !text.trim())}>
            提交回答
          </button>
        </footer>
      </form>
    </section>
  );
}
