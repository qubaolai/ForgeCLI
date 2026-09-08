/** 常规页: 每一项的名字、说明、来源与生效时机都由后端配置视图给出。
 *
 * 终端读的是同一份声明 (ADR-0048 决策 6) —— 这里不另写一份会漂的文案。
 */

import { useEffect, useState } from "react";
import type { SettingsFeature } from "@/features/settings/useSettings";
import type { Setting } from "@/types/session";

export function GeneralTab({ settings }: { settings: SettingsFeature }) {
  return (
    <>
      {settings.settings.map((item) => (
        <SettingField key={item.key} item={item} onSave={settings.save} onReset={settings.reset} />
      ))}
    </>
  );
}

function SettingField({
  item,
  onSave,
  onReset,
}: {
  item: Setting;
  onSave: (item: Setting, value: string) => void;
  onReset: (item: Setting) => void;
}) {
  const [value, setValue] = useState(item.value);
  useEffect(() => setValue(item.value), [item.value]);
  const scope = item.level === "app" ? "所有项目" : "当前项目";
  // 来源与生效时机照着终端的详情页说 (ADR-0048 决策 6): 两边读的是同一份声明, 措辞
  // 也就不该各写各的。
  const source = item.overridden ? "已覆盖" : "默认";
  // 说明放在左列名字底下，而不是作为第三个子元素：.setting-field 是
  // `justify-content: space-between` 的两列 flex，多一个子元素会把控件挤到中间，
  // 名字和说明各自换行，整行散掉。
  return (
    <label className="setting-field">
      <span>
        <strong>{item.label || item.key}</strong>
        <small>
          {scope} · {source} · {item.effect}
        </small>
        {item.help && <small className="setting-help">{item.help}</small>}
        {item.overridden && (
          <button type="button" className="setting-reset" onClick={() => onReset(item)}>
            恢复默认（{item.default || "空"}）
          </button>
        )}
      </span>
      <SettingControl item={item} value={value} onValue={setValue} onSave={onSave} />
    </label>
  );
}

/** 布尔项用是/否下拉，而不是让用户往输入框里敲 "true"。它的取值只有两个，
 *  而一个只能靠背字面量才填得对的输入框，等于把校验推给用户。 */
function SettingControl({
  item,
  value,
  onValue,
  onSave,
}: {
  item: Setting;
  value: string;
  onValue: (value: string) => void;
  onSave: (item: Setting, value: string) => void;
}) {
  function pick(next: string) {
    onValue(next);
    onSave(item, next);
  }
  if (item.kind === "bool") {
    return (
      <select value={value} onChange={(event) => pick(event.target.value)}>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
    );
  }
  if (item.choices.length) {
    return (
      <select value={value} onChange={(event) => pick(event.target.value)}>
        {item.choices.map((choice) => (
          <option key={choice}>{choice}</option>
        ))}
      </select>
    );
  }
  return (
    <div>
      <input value={value} onChange={(event) => onValue(event.target.value)} />
      <button onClick={() => onSave(item, value)}>保存</button>
    </div>
  );
}
