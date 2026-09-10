/** 网关治理页: 缓存, 重试与熔断。分段的配置对用户是同一张表单。 */

import { Flex, Typography } from "antd";
import type { Administration } from "@/features/administration/useAdministration";
import { DraftForm } from "@/features/settings/models/DraftForm";

export function GatewayView({ admin }: { admin: Administration }) {
  const runtime = admin.llmRuntime;
  return (
    <Flex vertical gap="middle">
      <Flex vertical>
        <Typography.Text strong>网关治理</Typography.Text>
        <Typography.Text type="secondary">控制缓存、重试与故障熔断；通常保持默认值即可。</Typography.Text>
      </Flex>
      {runtime && (
        <DraftForm
          fields={[
            {
              key: "cache.enabled",
              label: "缓存开关",
              value: String(runtime.cache.enabled),
              choices: ["false", "true"],
            },
            {
              key: "cache.ttl_seconds",
              label: "缓存 TTL（秒）",
              value: String(runtime.cache.ttl_seconds ?? ""),
            },
            { key: "cache.max_entries", label: "缓存条目上限", value: String(runtime.cache.max_entries) },
            { key: "cache.origins", label: "缓存 origins", value: runtime.cache.origins.join(",") },
            {
              key: "circuit_breaker.enabled",
              label: "熔断开关",
              value: String(runtime.circuit_breaker.enabled),
              choices: ["false", "true"],
            },
            {
              key: "circuit_breaker.failure_threshold",
              label: "失败阈值",
              value: String(runtime.circuit_breaker.failure_threshold),
            },
            {
              key: "circuit_breaker.cooldown_seconds",
              label: "冷却时间（秒）",
              value: String(runtime.circuit_breaker.cooldown_seconds),
            },
            {
              key: "retry.wait_threshold_seconds",
              label: "429 等待阈值（秒）",
              value: String(runtime.retry.wait_threshold_seconds),
            },
          ]}
          onSave={admin.saveLlmRuntime}
        />
      )}
    </Flex>
  );
}
