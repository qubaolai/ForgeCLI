"""记忆的读写编排 (ADR-0033). 对外唯一入口是 MemoryService.

**本包不认识 application/security, 也不被 application/agent_loop 认识.** 前者是决策 3
的核心 (记忆在任何情况下都不参与安全裁决), 后者是 ADR-0010 的字面要求. 两条都由
scripts/check_arch.py 的 SIBLING_BANS 守住 —— 写在注释里不算 (ADR-0028 规则 A1).

包门面不转导出任何东西 (见 scripts/check_arch.py 的环检查).
"""
