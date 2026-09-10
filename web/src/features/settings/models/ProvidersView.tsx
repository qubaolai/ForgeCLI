/** 供应商页。
 *
 * 自建的单独一组: 它们没有注册表默认值可以回落, 而且用户需要一眼看出哪几家是自己加的。
 * 内置与自建由后端的 builtin 标记分开 —— 前端手抄一份内置清单的话, 加一家内置供应商
 * 就会让它出现在"自建"那一组下, 而不会有任何东西报错。
 */

import { Button, Collapse, Flex, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { Administration } from "@/features/administration/useAdministration";
import { providerCollapseItem } from "@/features/settings/models/ProviderEditor";
import type { KnownProvider } from "@/types/admin";

export function ProvidersView({ admin, onOpenAdd }: { admin: Administration; onOpenAdd: () => void }) {
  const items = (known: KnownProvider[]) =>
    known.flatMap((entry) => {
      const provider = admin.providerSettings.find((item) => item.id === entry.id);
      if (!provider) return [];
      return [
        providerCollapseItem({
          provider,
          known: entry,
          fields: admin.providerFields,
          onSaveProvider: admin.saveProviderFields,
        }),
      ];
    });
  const builtin = items(admin.knownProviders.filter((item) => item.builtin !== false));
  const custom = items(admin.knownProviders.filter((item) => item.builtin === false));

  return (
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="flex-start" gap="small" wrap>
        <Flex vertical>
          <Typography.Text strong>供应商</Typography.Text>
          <Typography.Text type="secondary">
            配置兼容端点与密钥环境变量；API 密钥不会在页面中读取或保存。
          </Typography.Text>
        </Flex>
        <Button type="primary" icon={<PlusOutlined />} onClick={onOpenAdd}>
          添加供应商
        </Button>
      </Flex>
      <Collapse size="small" items={builtin} />
      {custom.length > 0 && (
        <>
          <Typography.Text strong>自建供应商</Typography.Text>
          <Collapse size="small" items={custom} />
        </>
      )}
      <Typography.Text type="secondary">
        目前只有 OpenAI Compatible <Typography.Text code>/chat/completions</Typography.Text>{" "}
        协议有对应的适配器；Ollama、vLLM 等本地端点属于这一类。
      </Typography.Text>
    </Flex>
  );
}
