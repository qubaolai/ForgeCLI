"""Shell 解析: 原始命令 -> 可审计的 CommandPlan (ADR-0013 §3 / §6 / §7).

纯函数, 无 IO, 不执行任何东西 —— 包括不执行解码出来的 PowerShell EncodedCommand.
"""
