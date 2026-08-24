"""LLM 领域：封闭供应商 + 配置驱动的模型，分「配置」与「调用」两个切片。

- 共享词汇（顶层）：providers（封闭注册表）、model_ref（默认模型引用）、errors。
- 配置切片：llm/config（读写 .forge/llm.json）。
- 调用切片：待接入（adapter 将在 infrastructure/llm/adapters 实现）。
"""
