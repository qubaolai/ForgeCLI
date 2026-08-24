"""LLM 调用网关切片 (ADR-0011): 端口 + 统一 DTO + 治理件 + 错误类型.

本切片是 application/llm 下与配置切片 (llm/config) 并列的调用切片. complete /
stream / complete_structured 三类调用共用同一控制面; 凭证, 计量, 缓存, 熔断从这里的
端口注入.

**包门面不转导出任何东西.** 它曾经急切 import default_gateway, 于是 import 任何一个
gateway 子模块 (哪怕是零依赖的 errors) 都会连带装配整个 LLM 子系统 —— 也正是这条边
把 catalog_builder -> gateway 变成一个真的 import 环 (见 catalog.py 与 selection.py).
一律从子模块 import; scripts/check_arch.py 的环检查会守住这一点.
"""
