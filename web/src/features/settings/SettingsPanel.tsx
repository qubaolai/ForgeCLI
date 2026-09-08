/** 设置面板的外壳: 遮罩, 标题, 左侧 tab 导航。
 *
 * 每个 tab 一个文件, 各自只吃它要的那一片。面板本身不认识任何一项配置 —— 它以前
 * 收二十四个 prop 只为了转发给下面, 加一个字段要改四个文件, 而漏掉一个不会报错。
 */

import { useState } from "react";
import type { Administration } from "@/features/administration/useAdministration";
import { GeneralTab } from "@/features/settings/GeneralTab";
import { ModelSettings } from "@/features/settings/models/ModelSettings";
import { RecoveryTab } from "@/features/settings/RecoveryTab";
import { SecurityTab } from "@/features/settings/SecurityTab";
import { StatusTab } from "@/features/settings/StatusTab";
import type { SettingsFeature } from "@/features/settings/useSettings";

export type SettingsTab = "general" | "models" | "security" | "recovery" | "status";

const TABS: Array<[SettingsTab, string]> = [
  ["general", "常规"],
  ["models", "模型"],
  ["security", "安全与工具"],
  ["recovery", "恢复"],
  ["status", "状态"],
];

export function SettingsPanel({
  admin,
  settings,
  initialTab = "general",
  onClose,
}: {
  admin: Administration;
  settings: SettingsFeature;
  /** 打开时停在哪个 tab。只在挂载时读一次, 之后由面板自己管。 */
  initialTab?: SettingsTab;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  return (
    <div
      className="modal-backdrop settings-layer"
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="settings-panel">
        <header>
          <div>
            <h2>设置</h2>
            <p>应用级与当前项目配置</p>
          </div>
          <button onClick={onClose}>×</button>
        </header>
        <div className="settings-body">
          <nav>
            {TABS.map(([value, label]) => (
              <button key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>
                {label}
              </button>
            ))}
          </nav>
          <div className="settings-fields">
            {tab === "general" && <GeneralTab settings={settings} />}
            {tab === "models" && <ModelSettings admin={admin} />}
            {tab === "security" && <SecurityTab admin={admin} />}
            {tab === "recovery" && <RecoveryTab admin={admin} />}
            {tab === "status" && <StatusTab admin={admin} />}
          </div>
        </div>
      </section>
    </div>
  );
}
