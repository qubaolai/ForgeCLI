/** 审批卡片。
 *
 * 卡片上每一档危险程度、每一句提示都来自后端已经算好的字段 —— 界面重新推导一次
 * "这次危不危险", 就是把裁决逻辑复制到了一个没人测的地方 (见 shared/lib/approval)。
 */

import { Alert, Button, Card, Collapse, Flex, Space, Tag, Typography } from "antd";
import type { AlertProps, CollapseProps } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import type { Approval, Severity } from "@/shared/lib/approval";
import {
  canLearn,
  defaultOpenSections,
  headlineOf,
  isConsequential,
  learnHintOf,
  severityOf,
  shownTargetGroups,
} from "@/shared/lib/approval";

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
  const openBy = defaultOpenSections(approval);
  const targets = shownTargetGroups(approval);
  const root = view.workspace_roots[0] ?? "";

  const sections: CollapseProps["items"] = [];
  if (view.script_snapshots.length > 0) {
    sections.push({
      key: "scripts",
      label: <SectionLabel title="脚本正文" meta={scriptMeta(view.script_snapshots)} />,
      children: (
        <Flex vertical gap="small">
          {view.script_snapshots.map((snapshot, index) => (
            <Flex vertical gap={4} key={`${snapshot.path}-${index}`}>
              <Flex justify="space-between" gap="small">
                <Typography.Text code>{snapshot.path || snapshot.origin || "内联脚本"}</Typography.Text>
                <Typography.Text type="secondary">{snapshot.language}</Typography.Text>
              </Flex>
              <Code text={snapshot.source} />
            </Flex>
          ))}
        </Flex>
      ),
    });
  }
  if (view.content_previews.length > 0) {
    sections.push({
      key: "previews",
      label: <SectionLabel title="写入预览" meta={`${view.content_previews.length} 个文件`} />,
      children: (
        <Flex vertical gap="small">
          {view.content_previews.map((preview, index) => (
            <Flex vertical gap={4} key={`${preview.path}-${index}`}>
              <Typography.Text code>{preview.path}</Typography.Text>
              <Code text={preview.content} />
              {preview.truncated && <Typography.Text type="secondary">已截断</Typography.Text>}
            </Flex>
          ))}
        </Flex>
      ),
    });
  }
  if (isConsequential(approval) && targets.length > 0) {
    sections.push({
      key: "targets",
      label: (
        <SectionLabel
          title="目标清单"
          meta={`${targets.reduce((total, group) => total + group.paths.length, 0)} 个路径`}
        />
      ),
      children: (
        <Flex vertical gap="small">
          {targets.map((group) => (
            <Flex vertical gap={2} key={group.label}>
              <Typography.Text strong>{group.label}</Typography.Text>
              {group.paths.map((path) => (
                <Typography.Text code ellipsis={{ tooltip: path }} key={path}>
                  {path}
                </Typography.Text>
              ))}
            </Flex>
          ))}
        </Flex>
      ),
    });
  }
  const openKeys = ["scripts", "previews", "targets"].filter((key) => openBy[key as keyof typeof openBy]);

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
          {view.mode && <Tag>{view.mode}</Tag>}
        </Space>
      }
    >
      <Flex vertical gap="small">
        <Alert
          type={SEVERITY_TYPE[severityOf(approval)]}
          showIcon
          title={approval.mandatory ? "这一档不能记成规则, 每次都会问" : "确认下面的内容再决定"}
        />
        {/* 命令永远可见: 一个安全决策的默认态不该是"什么都没说"。 */}
        <Code text={view.raw_command} />
        {root && (
          <Typography.Text type="secondary" ellipsis={{ tooltip: root }}>
            工作区 {root}
          </Typography.Text>
        )}
        <Space wrap size={4}>
          {view.counts.map((item) => (
            <Tag color={item.count > 0 ? "blue" : "default"} key={item.label}>
              {item.label} {item.count}
            </Tag>
          ))}
        </Space>
        {sections.length > 0 && <Collapse size="small" defaultActiveKey={openKeys} items={sections} />}
        <Typography.Text type="secondary">{learnHintOf(approval)}</Typography.Text>
        <Flex gap="small" justify="flex-end" wrap>
          <Button danger onClick={() => onResolve(approval.approval_id, "deny")}>
            拒绝
          </Button>
          <Button disabled={!canLearn(approval)} onClick={() => onResolve(approval.approval_id, "workspace")}>
            始终允许
          </Button>
          <Button type="primary" onClick={() => onResolve(approval.approval_id, "once")}>
            允许一次
          </Button>
        </Flex>
      </Flex>
    </Card>
  );
}

/** 证据分块。收的是体量, 不是事实的存在 —— 所以标题与条目数在收起时也看得见。 */
function SectionLabel({ title, meta }: { title: string; meta: string }) {
  return (
    <Space>
      <Typography.Text strong>{title}</Typography.Text>
      <Typography.Text type="secondary">{meta}</Typography.Text>
    </Space>
  );
}

function Code({ text }: { text: string }) {
  return (
    <Typography>
      <pre style={{ margin: 0, maxHeight: 240, overflow: "auto" }}>{text}</pre>
    </Typography>
  );
}

function scriptMeta(scripts: Approval["view"]["script_snapshots"]) {
  const lines = scripts.reduce((total, item) => total + item.source.split("\n").length, 0);
  return scripts.length > 1 ? `${scripts.length} 段 · ${lines} 行` : `${lines} 行`;
}
