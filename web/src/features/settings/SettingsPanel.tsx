/** 设置面板: 一个模态框 + 左侧竖排 tab。窄屏时 tab 转到顶部。 */

import { useEffect, useState } from "react";
import { Alert, Button, Grid, Modal, Spin, Tabs } from "antd";
import type { Administration } from "@/features/administration/useAdministration";
import { GeneralTab } from "@/features/settings/GeneralTab";
import { ModelSettings } from "@/features/settings/models/ModelSettings";
import { RecoveryTab } from "@/features/settings/RecoveryTab";
import { SecurityTab } from "@/features/settings/SecurityTab";
import { StatusTab } from "@/features/settings/StatusTab";
import type { SettingsFeature } from "@/features/settings/useSettings";

export type SettingsTab = "general" | "models" | "security" | "recovery" | "status";

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
  const screens = Grid.useBreakpoint();
  const [activeTab, setActiveTab] = useState(initialTab);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const load = {
    general: settings.load,
    models: admin.loadModels,
    security: admin.loadSecurity,
    recovery: admin.loadRecovery,
    status: admin.loadStatus,
  }[activeTab];

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    load()
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [load, retry]);
  const items = [
    { key: "general", label: "常规", children: <GeneralTab settings={settings} /> },
    { key: "models", label: "模型", children: <ModelSettings admin={admin} /> },
    { key: "security", label: "安全与工具", children: <SecurityTab admin={admin} /> },
    { key: "recovery", label: "恢复", children: <RecoveryTab admin={admin} /> },
    { key: "status", label: "状态", children: <StatusTab admin={admin} /> },
  ];
  return (
    <Modal
      className="forge-settings-modal"
      open
      title="设置"
      onCancel={onClose}
      footer={null}
      width={1040}
      destroyOnHidden
      styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }}
    >
      {error && (
        <Alert
          type="error"
          title={error}
          action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>}
        />
      )}
      <Spin spinning={loading}>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => {
            setLoading(true);
            setActiveTab(key as SettingsTab);
          }}
          tabPlacement={screens.md ? "start" : "top"}
          destroyOnHidden
          items={items.map((item) => ({ ...item, children: loading || error ? null : item.children }))}
        />
      </Spin>
    </Modal>
  );
}
