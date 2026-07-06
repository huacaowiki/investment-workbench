# investment-workbench 投资研究工作台

个人投资研究流水线：把《花茶个人投资体系 v4.1》结构化为可执行规则，
自动完成数据抓取 → 规则核对 → 报告生成 → 体系迭代闭环。

## 快速开始

```bash
pip install -r requirements.txt
python run.py daily              # 当日市场日报
python run.py stock 600519      # 个股规则核对报告
python run.py monthly           # 月度体系迭代（只出草案）
python -m pytest tests/ -q      # 运行全部测试
```

或在 Claude Code 中直接说：「执行市场日报」「分析 600519」「执行月度迭代」。

## 核心设计

- **铁则层只读**：`config/` 四份 YAML 是唯一执行标准，程序与 AI 均无权修改；
  修改必经 `run.py backup-config` → 人工编辑 → 可随时 `rollback`
- **数据缺失诚实原则**：接口拿不到的字段（审计意见、派现比例等）一律标注待人工核验，绝不臆断
- **知识分层隔离**：reference（参考级，仅作论据）/ drafts（禁用级，仅迭代场景可读）
- **迭代只出草案**：`system_iter` 引擎经测试验证无任何写 config 的代码路径

## 文档

- [使用手册.md](使用手册.md) — 日常操作、双机同步、排错（无需技术背景）
- [项目交付清单.md](项目交付清单.md) — 全部交付物与路径
- [CLAUDE.md](CLAUDE.md) — 项目级 AI 指令（优先级规则）

## 免责声明

本项目全部输出仅为个人研究记录与策略框架支撑，不构成任何证券投资建议。
