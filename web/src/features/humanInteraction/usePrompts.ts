/** 待答提示: 拉取与作答。
 *
 * 请求带代次 (ADR-0048 决策 5): 提示会因为事件到达而被反复拉取, 而一次慢响应晚于
 * 后来的那次返回时, 页面会退回到一张已经答过的卡片。
 *
 * 草稿不在这里 —— 它由卡片组件自己持有, 按 `(session_id, prompt_id)` 挂在 React key
 * 上: 重连时同一条提示的卡片不重建, 草稿留着; 换会话或提示结束时卡片卸载, 草稿跟着走。
 */

import { useCallback, useRef, useState } from "react";
import { api } from "../../api/client";
import type { Prompt } from "../../approvalModel";

export function usePrompts(onError: (message: string) => void) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const version = ++generation.current;
    const result = await api<{ items: Prompt[] }>("/prompts");
    if (version === generation.current) setPrompts(result.items);
  }, []);

  /** 审批与提问同一条路: choice 是点了哪个选项, text 是自己写的那一句。 */
  const resolve = useCallback(async (
    id: string,
    choice: string,
    text = "",
    selectedValues: string[] = [],
    skipped = false,
  ) => {
    try {
      await api(`/prompts/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ choice, text, selected_values: selectedValues, skipped }),
      });
      await load();
    } catch (reason) {
      onError((reason as Error).message);
      // 往上抛: 卡片要据此保留草稿并显示"提交失败, 请重试"。
      throw reason;
    }
  }, [load, onError]);

  return { prompts, load, resolve };
}
