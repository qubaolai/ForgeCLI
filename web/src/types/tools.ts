/** 工具清单的响应形状 (GET /tools)。 */

export type ToolSpec = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
};

/** `/tools` 的 `all`: 全部内置工具的展示名, 与规则无关, 见后端那个路由的说明。 */
export type ToolDirectoryItem = { name: string; title: string; read_only: boolean };

export type ToolsResponse = { items: ToolSpec[]; all: ToolDirectoryItem[] };
