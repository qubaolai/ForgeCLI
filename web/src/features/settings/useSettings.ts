/** 配置项: 读, 改, 恢复默认。
 *
 * 三个动作都直落后端同一组用例, 终端读写的是同一份声明 (ADR-0048 决策 6)。
 */

import { useCallback, useState } from "react";
import { api } from "../../api/client";
import type { Setting } from "../../types";

export function useSettings(onError: (message: string) => void) {
  const [settings, setSettings] = useState<Setting[]>([]);

  const load = useCallback(async () => {
    const result = await api<{ items: Setting[] }>("/settings");
    setSettings(result.items);
  }, []);

  const write = useCallback(async (key: string, init: RequestInit) => {
    try {
      await api(`/settings/${key}`, init);
      await load();
    } catch (reason) { onError((reason as Error).message); }
  }, [load, onError]);

  return {
    settings,
    load,
    save: (item: Setting, value: string) =>
      write(item.key, { method: "PATCH", body: JSON.stringify({ value }) }),
    /** 恢复默认 = 删掉这条覆盖, 重新继承 SCHEMA 默认值 —— 不是"把当前默认值写成一条
     *  覆盖": 那样在默认值改版之后, 用户会被钉在一个他从没选过的旧值上。 */
    reset: (item: Setting) => write(item.key, { method: "DELETE" }),
  };
}
