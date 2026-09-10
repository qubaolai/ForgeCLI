/** 右侧计划栏的外壳: 标题、导出入口与关闭。宽度由外面的 Splitter 管。 */

import { Button, Card, Space } from "antd";
import { CloseOutlined, ExportOutlined } from "@ant-design/icons";
import { PlanningPanel } from "@/features/planning/PlanningPanel";
import type { PlanIndexView, Planning } from "@/types/session";

export function PlanDock({
  onClose,
  planning,
  index,
  onResolve,
  onActivate,
}: {
  onClose: () => void;
  planning: Planning;
  index: PlanIndexView | null;
  onResolve: (decision: string) => void;
  onActivate: (planId: string) => void;
}) {
  return (
    <Card
      title="计划"
      size="small"
      variant="borderless"
      style={{ height: "100%", display: "flex", flexDirection: "column", borderRadius: 0 }}
      styles={{ body: { flex: 1, overflowY: "auto" } }}
      extra={
        <Space size={4}>
          <Button
            size="small"
            icon={<ExportOutlined />}
            disabled={!planning.plan}
            onClick={() => window.open("/api/v1/planning/markdown", "_blank", "noopener,noreferrer")}
          >
            Markdown
          </Button>
          <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} aria-label="隐藏计划" />
        </Space>
      }
    >
      <PlanningPanel planning={planning} index={index} onResolve={onResolve} onActivate={onActivate} />
    </Card>
  );
}
