"""主 Agent 系统提示词的编译 (ADR-0018).

编译顺序与读取规则属这一层; 纯值对象在 domain/agent/prompt, 文件读取在
infrastructure/prompt.

不在这里转手再导出一遍: 调用方按模块路径直接 import, 一个没人用的门面只会让"改个符号
要同步几处"变成三处. 与 domain/agent 对 run_events 的处理一致.
"""
