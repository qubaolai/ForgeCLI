/** 工具清单与工具目录的响应形状。 */

export type ToolSpec = {
  name: string;
  title: string;
  description: string;
  declared_capabilities: string[];
  default_timeout_seconds: number;
};

/** `/tools` 的 `all`: 与模式无关的全量展示名, 见后端那个路由的说明。 */
export type ToolDirectoryItem = { name: string; title: string; capabilities: string[] };

export type ToolsResponse = { items: ToolSpec[]; all: ToolDirectoryItem[] };
