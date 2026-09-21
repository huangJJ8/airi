# 面试与作品集材料

这个文件夹里的所有内容都是**求职材料**——写给要介绍 AIRI 的人，不是写给要运行 AIRI 的用户。
仓库里面向用户的文档在 [`../guides/`](../guides/) 和 [`../architecture/`](../architecture/)。

| 文件 | 什么时候用它 |
| --- | --- |
| [highlights.md](highlights.md) | 你只需要记住最值得记的 5 件事，别的都不用 |
| [airi-pitch.md](airi-pitch.md) | 你有 30 秒、3 分钟或 10 分钟来介绍 AIRI |
| [airi-qa.md](airi-qa.md) | 面试官在问你“为什么当时这么做？” |
| [architecture-walkthrough.md](architecture-walkthrough.md) | 你正在共享屏幕、对着代码逐段讲 |
| [demo-script.md](demo-script.md) | 你正在跑 5 分钟的现场演示 |
| [../resume-project.md](../resume-project.md) | 你正在写简历里那一条项目经历本身 |

## 所有材料共同的底线

要准确。AIRI 整套架构论证的核心就是：一个说法必须能追溯到证据——简历上吹过头，等于跟项目本身自相矛盾。

这么说 | 别说
--- | ---
AI-assisted（AI 辅助） | AI-powered
research and development platform（研发平台） | production-ready system
synthetic data, end-to-end demo（合成数据、端到端演示） | real fraud detection（真实欺诈检测）
2 differentiated scenarios, 91% backend coverage（2 个差异化场景、后端覆盖率） | "improved efficiency by 80%"
deterministic tool layer, human-approved gates（确定性工具层、人工审批的关卡） | fully autonomous agent（完全自主的智能体）

有三条说法是本仓库**不**支持的，绝不能出现在简历、自我介绍或面试回答里：

- 说 AIRI 跑在真实企业数据上，或者具备任何真实的预测能力。
- 说 Spark/Hive/MySQL/IAM 的集成已经验证（它们代码完整、并且刻意失败关闭，但未验证）。
- 说 `examples/` 里的任何统计数字反映了真实的风险表现。

规范表述见 README 的 [限制与已知不足（Limitations）](../../README.md#limitations) 一节。
