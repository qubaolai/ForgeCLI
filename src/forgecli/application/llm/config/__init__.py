"""LLM 配置切片: 读取 / 校验 / 增删改 .forge/llm.json.

本切片只管"有哪些供应商 / 模型, 参数是什么"的声明与持久化; 运行时怎么发请求由调用
切片负责. 运行时默认模型只共享 llm 顶层的 model_ref 值对象, 不复制这里的模型参数.

**包门面不转导出任何东西.** 它曾经急切 import llm_config_service, 而那个模块回头要
catalog_builder —— 于是 `import ...config.llm_config` 会连带触发一个 import 环.
环之所以一直没炸, 是因为上层包的 __init__ 恰好把顺序摆对了; 那是运气不是设计, 而运气
在有人动 __init__ 的那天就会用完. 一律从子模块 import.
"""
