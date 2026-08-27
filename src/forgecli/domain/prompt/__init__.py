"""提示词的块结构: PromptBlock / PromptBlockId / PromptSnapshot.

这里只剩不变量 (缓存划分, 派生指纹). Forge 撰写的正文原先与它同住一个包, ADR-0039 之后
全部搬去了 `application/prompt/templates/` —— 那些文字没有一个 domain 消费方, 八个消费方
全在 application, 让它们坐在 domain 只是历史.

包门面不转导出任何东西 (见 scripts/check_arch.py 的环检查).
"""
