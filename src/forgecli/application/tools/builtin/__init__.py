"""内置工具 (ADR-0004 §14, ADR-0029 规则三).

八个工具, 五类动作:

    定位  fs_find (按文件名), search_text (按内容), code_definitions (按符号)
    读取  fs_read, git_read
    写入  fs_apply_patch
    执行  shell_run
    过程  plan_* / todo_*

每个工具的能力上界都尽量窄: 窄上界让它们在 plan 档目录过滤中受益 —— 上界里带写能力
的工具在那一档根本不进 ToolCatalog. 只有 shell_run 的上界最宽, 那是设计意图: 长尾都
走它 (ADR-0029 规则二).
"""
