"""上下文管理的纯值对象 (ADR-0032).

预算与压缩记录都是无依赖的值: 循环拿预算判"该不该压", 驱动方拿记录落盘. 编排住在
application/context, 真正读写 artifact 文件的实现住在 infrastructure.

包门面不转导出任何东西 (见 scripts/check_arch.py 的环检查).
"""
