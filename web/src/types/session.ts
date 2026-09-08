/** 会话、正文、计划与配置项的响应形状。
 *
 * 中文名, 说明, 来源与生效时机一律来自后端 SCHEMA, 前端不另写一份 —— 同一个开关在
 * 终端菜单和网页上叫不同名字时, 两边都不会报错, 只会让用户以为是两个开关。
 */

export type Project = {
  project_id: string;
  primary_workspace_root: string;
  workspace_roots: string[];
};

export type Session = {
  session_id: string;
  title: string;
  updated_at: string;
  status: string;
  /** 两个轴, 见 Stance。历史会话可能没有这一项。 */
  mode?: { sandbox: string; approval: string };
};

export type TranscriptEvent = {
  event_id: string;
  type: string;
  created_at: string;
  payload: { role?: string; text?: string; status?: string; turn_id?: string };
};

export type Setting = {
  key: string;
  // 中文名与说明来自后端 SCHEMA，不在这里另写一份：同一个开关在终端菜单和这里叫不同
  // 名字时，两边都不会报错，只会让用户以为是两个开关。
  label: string;
  help: string;
  level: string;
  kind: string;
  value: string;
  choices: string[];
  // 来源与生效时机也来自 SCHEMA (ADR-0048 决策 6)。终端一直显示这两样并支持恢复默认,
  // 这里以前只拿得到有效值 —— 于是同一份声明在两个入口给出的能力不一样。
  default: string;
  overridden: boolean;
  effect: string;
};

export type Planning = {
  markdown?: string;
  plan?: {
    plan_id: string;
    title: string;
    goal: string;
    status: string;
    steps: Array<{ title: string; detail: string }>;
  };
  todo?: {
    items: Array<{ title: string; status: string }>;
  };
};

export type TurnRunState = {
  run_id: string;
  // 在途轮次的会话身份。run_id 标识"这次后台执行", 这一对标识"会话里的第几轮"。
  session_id?: string;
  turn_id?: string;
  status: string;
  response?: { turn_id: string; text: string };
  error?: string;
};

export type PlanIndexView = {
  active_plan_id: string;
  active_todo_id: string;
  plans: Array<{ plan_id: string; revision: number; status: string; title: string; updated_at: string }>;
};
