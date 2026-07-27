"""领域层入口。

该层只放核心业务概念与规则，不依赖 CLI、Rich、文件系统、SDK 或工具运行时。

判定标准是「存在理由」而非「长得像不像值对象」: 换掉 LLM 供应商 / 存储 / CLI 之后
依然成立的概念进 domain; 存在理由是运行时机制的东西 (超时与重试旋钮, 适配器边界 DTO,
指标采样, 带 TOML 往返方法的配置载体) 留在 application —— 即使它也是 frozen dataclass。

允许依赖 `shared/`: 它零依赖 (errors 只声明异常, utils 只有一个时间戳函数), 不会成环。
除此之外 domain 不 import 任何 forgecli 模块。
"""
