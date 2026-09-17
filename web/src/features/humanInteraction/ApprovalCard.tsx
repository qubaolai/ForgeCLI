/** 审批卡片 (ADR-0055 决策 4 / 6)。
 *
 * 卡片上每一档危险程度、每一句提示、每一个按钮都来自后端已经算好的字段 —— 界面重新
 * 推导一次"这次危不危险", 就是把裁决逻辑复制到了一个没人测的地方 (见 shared/lib/approval)。
 */

import { Alert, Button, Card, Flex, Space, Tag, Typography } from "antd";
import type { AlertProps } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import type { Approval, Severity } from "@/shared/lib/approval";
import { headlineOf, inputEntries, isLongValue, severityOf, suggestionChoices } from "@/shared/lib/approval";

/** 三档危险程度只决定提示条的颜色; 判定在 shared/lib/approval 里。 */
const SEVERITY_TYPE: Record<Severity, AlertProps["type"]> = {
  calm: "info",
  caution: "warning",
  critical: "error",
};

export function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: Approval;
  onResolve: (id: string, choice: string, text?: string) => void;
}) {
  const view = approval.view;
  const entries = inputEntries(approval);
  const suggestions = suggestionChoices(approval);

  return (
    <Card
      className="attention-card approval-card"
      size="small"
      title={
        <Space>
          <SafetyCertificateOutlined />
          {headlineOf(approval)}
        </Space>
      }
      extra={
        <Space size={4}>
          <Tag color="blue">{view.tool_name}</Tag>
          {view.destructive && <Tag color="red">不可逆</Tag>}
        </Space>
      }
    >
      <Flex vertical gap="small">
        <Alert type={SEVERITY_TYPE[severityOf(approval)]} showIcon title={view.message || "确认下面的内容再决定"} />
        {/* 入参永远可见: 参数里可能出现什么, 用户就该看到什么。 */}
        <Flex vertical gap={4}>
          {entries.map(([key, value]) =>
            isLongValue(value) ? (
              <Flex vertical gap={2} key={key}>
                <Typography.Text strong>{key}</Typography.Text>
                <Code text={value} />
              </Flex>
            ) : (
              <Typography.Text key={key}>
                <Typography.Text strong>{key}</Typography.Text>: <Typography.Text code>{value}</Typography.Text>
              </Typography.Text>
            ),
          )}
        </Flex>
        {view.reason && <Typography.Text type="secondary">裁决: {view.reason}</Typography.Text>}
        <Flex gap="small" justify="flex-end" wrap>
          <Button danger onClick={() => onResolve(approval.approval_id, "no")}>
            拒绝
          </Button>
          {suggestions.map((choice) => (
            <Button key={choice.value} onClick={() => onResolve(approval.approval_id, choice.value)}>
              {choice.label}
            </Button>
          ))}
          <Button type="primary" onClick={() => onResolve(approval.approval_id, "yes")}>
            允许这一次
          </Button>
        </Flex>
      </Flex>
    </Card>
  );
}

function Code({ text }: { text: string }) {
  return (
    <Typography>
      <pre style={{ margin: 0, maxHeight: 240, overflow: "auto" }}>{text}</pre>
    </Typography>
  );
}
