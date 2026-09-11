"""Agent 主循环 (ADR-0010, ADR-0049): 受控 ReAct 内核与它的规则.

本包是 application 下与 agent_turn 并列的编排内核层. 循环只产出结构化意图
(LoopAction / LoopStop), 执行工具一律由 AgentTurnService 经安全管线完成. MVP 唯一实现是
``builtin_loop.BuiltinAgentLoop``.

循环内部按 ADR-0049 分成两层:

- ``builtin_loop`` 只剩控制流: 调模型, 派工具, 回填结果, 收尾. 它负责执行处置.
- ``rules/`` 是"下一步做什么"的判断, 每条一个文件, 在它关心的时机上给一个处置
  (``verdicts``). 规则表怎么跑, 顺序怎么校验, 见 ``rule_table``; 规则能看到什么, 见
  ``rule.LoopView``; 规则能写的只有 ``ledger.TurnLedger``.

本包对外的扩展点是 ``rule.LoopRule``: 加一条判断就是加一个文件, 再在
``rules/__init__.py`` 那份带理由的列表里加一行.

循环的词汇 (动作 / 状态 / 停止原因) 住在 domain.agent, 本包不转手再导出领域类型.
事件总线不在本包: 观察事件的范围是整个 turn (ADR-0016), 由 application.agent_run 承载,
循环只是它的发布者之一.
"""
