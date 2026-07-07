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
- **配置口径**：原 12 条【待确认】项已于 2026-07-06 经用户授权全部裁决固化（各 YAML 末尾 confirmed_decisions），程序按 auto_rule 自动化口径执行；推定类判定（如公告扫描无命中→推定审计正常）在报告中必须标注推定口径
- **一键工作流**：用户说「执行市场日报」「分析 <股票代码>」「执行月度迭代」「执行季度迭代」时，分别按 prompts/ 目录下对应的主控指令文件执行

## Git 自动同步（用户 2026-07-07 授权，标准行为，无需逐次确认）

本项目用于双电脑（经 OneDrive 同步目录）无缝协作，用户已明确、持久地授权以下自动化行为，
适用范围仅限本仓库（`investment-workbench`），无需每次重新征求同意：

- **`run.py` 每条命令执行完毕后自动 `git add -A && git commit && git push`**（见 `src/utils/git_sync.py` 的
  `auto_sync()`，已接入 `run.py` 的 `main()`）。这是既有机制，Claude 不需要重复实现，只需知悉其存在。
- **Claude 在本项目中直接编辑代码/配置/文档后，应在完成一组有意义的改动后主动执行
  `git add -A && git commit -m "..." && git push`，不必等待用户说"帮我提交"或"推送一下"。**
  提交信息应简明描述改动内容（沿用仓库现有的中文提交信息风格）。
- 仅限本仓库、仅限"提交 + 推送到 origin/main"这一操作范围；不代表授权其他仓库或其他类型的
  破坏性操作（如 force-push、reset --hard、删除分支等）——这些仍需按通用安全规范单独确认。
- `data/` 目录被 `.gitignore` 排除（缓存与状态文件），由 OneDrive 的实时文件同步负责跨机同步，
  与 Git 同步互补，不冲突。
- 若推送失败（网络问题、远程有未拉取的提交等），如实告知用户失败原因，不要静默重试或强推。

## 常用命令

```
python run.py daily                 # 生成当日市场日报
python run.py stock <代码>          # 生成个股分析报告
python run.py monthly [YYYY-MM]     # 月度体系迭代（只出草案）
python run.py quarterly [YYYY-Qn]   # 季度深度迭代（只出草案）
python run.py backup-config         # 备份当前config到config/history
python run.py rollback <版本号>      # 回滚config到指定历史版本
python run.py judge-state           # 市场状态程序自动初判并落盘
python run.py set-state <状态>       # 人工判定市场状态（7日内优先于程序初判）
python run.py watchlist list|add|remove [代码]   # 择时备选池维护
python -m pytest tests/ -q          # 运行全部测试
```
