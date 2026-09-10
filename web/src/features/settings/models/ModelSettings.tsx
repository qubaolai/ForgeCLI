/** 设置面板的模型页外壳: 三个子页的导航, 以及两个添加抽屉。
 *
 * 表单该有哪些行全部来自后端的字段声明, 这里不维护一份会漂移的字段表。
 */

import { useState } from "react";
import { Tabs } from "antd";
import type { Administration } from "@/features/administration/useAdministration";
import { AddModelDrawer } from "@/features/settings/models/AddModelDrawer";
import { AddProviderDrawer } from "@/features/settings/models/AddProviderDrawer";
import { GatewayView } from "@/features/settings/models/GatewayView";
import { ModelsView } from "@/features/settings/models/ModelsView";
import { ProvidersView } from "@/features/settings/models/ProvidersView";

export type ModelSettingsView = "models" | "providers" | "gateway";

export function ModelSettings({
  admin,
  initialView = "models",
}: {
  admin: Administration;
  /** 打开时停在哪个子页。只在挂载时读一次, 之后由子导航自己管。 */
  initialView?: ModelSettingsView;
}) {
  const [view, setView] = useState<ModelSettingsView>(initialView);
  const [showAdd, setShowAdd] = useState(false);
  const [showAddProvider, setShowAddProvider] = useState(false);

  return (
    <>
      <Tabs
        activeKey={view}
        onChange={(key) => setView(key as ModelSettingsView)}
        items={[
          {
            key: "models",
            label: "模型",
            children: (
              <ModelsView
                admin={admin}
                onOpenAdd={() => setShowAdd(true)}
                onGoProviders={() => setView("providers")}
              />
            ),
          },
          {
            key: "providers",
            label: "供应商",
            children: <ProvidersView admin={admin} onOpenAdd={() => setShowAddProvider(true)} />,
          },
          { key: "gateway", label: "网关", children: <GatewayView admin={admin} /> },
        ]}
      />

      {showAddProvider && (
        <AddProviderDrawer
          protocols={admin.providerProtocols}
          onClose={() => setShowAddProvider(false)}
          onAdd={admin.addProvider}
        />
      )}

      {showAdd && (
        <AddModelDrawer
          knownProviders={admin.knownProviders}
          providerSettings={admin.providerSettings}
          onClose={() => setShowAdd(false)}
          onEditProvider={() => {
            setShowAdd(false);
            setView("providers");
          }}
          onAdd={admin.addModel}
          onSetCurrent={admin.chooseCurrentModel}
        />
      )}
    </>
  );
}
