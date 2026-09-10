/** 计划与待办栏, 含回合边界的计划评审决议。 */

import { Button, Empty, Flex, Menu, Space, Steps, Tag, Typography } from "antd";
import { Markdown } from "@/shared/ui/Markdown";
import type { PlanIndexView, Planning } from "@/types/session";

/** 计划状态与待办状态都由后端给, 这里只挑一个颜色。 */
const PLAN_STATUS_COLOR: Record<string, string> = {
  proposed: "gold",
  approved: "green",
  running: "blue",
  rejected: "red",
  done: "green",
};

const TODO_STATUS: Record<string, "finish" | "process" | "wait"> = {
  done: "finish",
  in_progress: "process",
};

export function PlanningPanel({
  planning,
  index,
  onResolve,
  onActivate,
}: {
  planning: Planning;
  index: PlanIndexView | null;
  onResolve: (decision: string) => void;
  onActivate: (planId: string) => void;
}) {
  const plan = planning.plan;
  const todo = planning.todo;
  const others = (index?.plans ?? []).filter((item) => item.plan_id !== index?.active_plan_id);
  if (!plan && !todo && !others.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Flex vertical gap={4}>
            <Typography.Text>当前没有活动计划</Typography.Text>
            <Typography.Text type="secondary">切换到 Plan 模式，让 Forge 先梳理实施方向。</Typography.Text>
          </Flex>
        }
      />
    );
  }
  return (
    <Flex vertical gap="middle">
      {plan && (
        <>
          <Tag color={PLAN_STATUS_COLOR[plan.status]}>{plan.status}</Tag>
          <Markdown content={planning.markdown ?? `# ${plan.title}\n\n${plan.goal}`} />
          {plan.status === "proposed" && (
            <Space wrap>
              <Button danger onClick={() => onResolve("reject")}>
                拒绝
              </Button>
              <Button onClick={() => onResolve("approve")}>同意</Button>
              <Button type="primary" onClick={() => onResolve("approve_and_run")}>
                同意并执行
              </Button>
            </Space>
          )}
        </>
      )}
      {todo?.items?.length ? (
        <Flex vertical gap="small">
          <Typography.Text strong>
            待办 {todo.items.filter((item) => item.status === "done").length}/{todo.items.length}
          </Typography.Text>
          <Steps
            direction="vertical"
            size="small"
            items={todo.items.map((item) => ({
              title: item.title,
              status: TODO_STATUS[item.status] ?? "wait",
            }))}
          />
        </Flex>
      ) : null}
      {others.length > 0 && (
        <Flex vertical gap="small">
          <Typography.Text strong>其他计划</Typography.Text>
          <Menu
            style={{ borderInlineEnd: "none" }}
            onClick={({ key }) => onActivate(key)}
            items={others.map((item) => ({
              key: item.plan_id,
              style: { height: "auto", lineHeight: 1.4, paddingBlock: 6 },
              label: (
                <Flex vertical>
                  <Typography.Text ellipsis>{item.title || item.plan_id}</Typography.Text>
                  <Typography.Text type="secondary">
                    {item.plan_id} · r{item.revision} · {item.status}
                  </Typography.Text>
                </Flex>
              ),
            }))}
          />
        </Flex>
      )}
    </Flex>
  );
}
