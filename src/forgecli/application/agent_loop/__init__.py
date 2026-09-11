"""Agent 主循环 (ADR-0010, ADR-0049): 受控 ReAct 内核与它的规则.

本包是 application 下与 agent_turn 并列的编排内核层. 循环只产出结构化意图
(LoopAction / LoopStop), 执行工具一律由 AgentTurnService 经安全管线完成. MVP 唯一实现是
``builtin_loop.BuiltinAgentLoop``.

循环内部按 ADR-0049 分成两层:

- ``builtin_loop`` 只剩控制流: 调模型, 派工具, 回填结果, 收尾. 它负责执行处置.
- ``rules/`` 是"下一步做什么"的判断, 每条一个文件, 每条就是一个方法, 返回这个时机
  允许的处置 (``verdicts``). 哪个时机按什么顺序跑哪些方法, 全写在 ``rules/__init__.py``
  那张五列的表里; 怎么跑见 ``rule_table``; 规则能看到什么, 见 ``rule.LoopView``; 规则能
  写的只有 ``ledger.TurnLedger``.

加一条判断就是加一个方法, 再在 ``rules/__init__.py`` 对应时机那一列里加一行.

循环的词汇 (动作 / 状态 / 停止原因) 住在 domain.agent, 本包不转手再导出领域类型.
事件总线不在本包: 观察事件的范围是整个 turn (ADR-0016), 由 application.agent_run 承载,
循环只是它的发布者之一.
"""
