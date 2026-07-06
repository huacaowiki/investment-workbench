# 投资研究工作台 · 项目级指令（所有对话必须遵守）

本项目所有投资分析、代码开发、报告输出，严格遵循以下优先级规则，永远以高优先级内容为准：

1. **最高优先级（铁则层）**：`config/` 目录下的所有 YAML 配置文件（investment_system / stock_selection / valuation_model / risk_control），是所有分析结论的唯一执行标准
2. **次优先级**：本次对话中用户明确给出的指令
3. **参考级**：`knowledge/reference/` 目录下的第三方资料，仅可用于补充背景、支撑论据，绝对不得修改、突破既定规则
4. **禁用级**：`knowledge/drafts/` 目录下的草稿素材，日常分析场景禁止调用，仅投资体系迭代场景（执行 prompts/iterate_monthly.md 或 iterate_quarterly.md 时）可读取

若不同层级内容存在冲突，必须主动标注冲突点，并以高优先级内容为准执行。

## 附加铁则

- **禁止修改 config/**：任何工作流（包括体系迭代）都不得直接修改 config/ 下的 YAML；迭代只输出修订草案到 output/iteration/，由用户人工确认后手动修改并通过 `python run.py backup-config` 备份旧版
- **合规边界**：所有输出仅做研究分析与策略框架支撑，不提供具体实盘买卖建议；报告必须保留免责声明
- **配置中的【待确认】项**：程序按各 YAML 内 pending_confirmation 标注的临时口径执行，报告中涉及时须标注
- **一键工作流**：用户说「执行市场日报」「分析 <股票代码>」「执行月度迭代」「执行季度迭代」时，分别按 prompts/ 目录下对应的主控指令文件执行

## 常用命令

```
python run.py daily                 # 生成当日市场日报
python run.py stock <代码>          # 生成个股分析报告
python run.py monthly [YYYY-MM]     # 月度体系迭代（只出草案）
python run.py quarterly [YYYY-Qn]   # 季度深度迭代（只出草案）
python run.py backup-config         # 备份当前config到config/history
python run.py rollback <版本号>      # 回滚config到指定历史版本
python -m pytest tests/ -q          # 运行全部测试
```
