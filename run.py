# -*- coding: utf-8 -*-
"""
run.py — 投资研究工作台一键入口
用法：
  python run.py daily                  生成当日市场日报（默认HTML+MD，v4.3.0）
  python run.py daily 20260401        生成指定日期市场日报（历史统计类数据以当日可得为准）
  python run.py stock 600519          生成个股分析报告（默认HTML+MD）
  python run.py stock 600519 --format all    额外生成PDF（可选 md|html|pdf|all）
  python run.py monthly [2026-06]     月度体系迭代（只出草案，绝不改config）
  python run.py quarterly [2026-Q2]   季度深度迭代（只出草案）
  python run.py backup-config [备注]   备份当前config到config/history
  python run.py rollback v001_xxx     回滚config到指定历史版本
  python run.py judge-state           市场状态程序初判并落盘（v4.2.0自动化；人工判定7日内优先）
  python run.py set-state B_neutral   人工判定市场状态（优先于程序初判，7日有效）
  python run.py watchlist list        查看择时备选池
  python run.py watchlist add 600519  加入备选池（入池即承担研究笔记义务）
  python run.py watchlist remove 600519

每条命令执行完毕后自动 git add+commit+push 到 GitHub（双电脑无缝同步，无需手动操作）；
如需临时跳过同步（如离线环境），在命令末尾加 --no-sync。
"""
from __future__ import annotations

import io
import sys
from datetime import datetime

# Windows 控制台 GBK 编码兜底，保证中文输出不乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))


def cmd_daily(day: str | None = None, output_format: str = "html"):
    """市场日报全链路：拉数 → 分析 → 自检 → 渲染 → 归档（默认HTML，v4.3.0）。"""
    from src.data.market_data import get_market_snapshot
    from src.analyzer.market_analyzer import analyze_market
    from src.analyzer.self_check import check_daily_result
    from src.generator.report_writer import render_daily_report
    from src.generator.archiver import archive_daily_report
    from src.utils.file_utils import load_config

    print("① 拉取市场数据……")
    snap = get_market_snapshot(day=day)
    if snap["errors"]:
        print(f"   ⚠️ 部分数据缺失：{list(snap['errors'])}（按缺失处理，不影响出报）")
    print("② 执行市场分析（严格对照 config 铁则）……")
    analysis = analyze_market(snap)
    warnings = check_daily_result(analysis, load_config())
    if warnings:
        print(f"   ⚠️ 逻辑自检发现 {len(warnings)} 条预警（已写入报告第九节）")
    print(f"③ 渲染并归档报告（格式：{output_format}）……")
    md, meta = render_daily_report(analysis, self_check=warnings)
    path = archive_daily_report(md, meta, output_format=output_format)
    print(f"✅ 市场日报已生成：{path}")
    for note in meta.get("format_notes", []):
        print(f"   ℹ️ {note}")
    score = meta['sentiment_score']
    print(f"   情绪观察分：{score if score is not None else '—'}/10 ｜ "
          f"市场状态：{meta['effective_state'] or '未判定（judge-state 补数或 set-state 人工判定）'}")
    for a in meta["alerts"][:3]:
        print(f"   [{a['级别']}] {a['内容'][:60]}")
    return path


def cmd_stock(code: str, output_format: str = "html"):
    """个股分析全链路：拉数 → 规则判定 → 自检 → 渲染 → 归档（默认HTML，v4.3.0）。"""
    from src.data.stock_data import get_stock_snapshot
    from src.analyzer.market_analyzer import MARKET_STATE_FILE
    from src.analyzer.self_check import check_stock_result
    from src.analyzer.stock_analyzer import analyze_stock
    from src.generator.report_writer import render_stock_report
    from src.generator.archiver import archive_stock_report
    from src.utils.file_utils import load_config, read_json

    print(f"① 拉取个股数据（{code}）……")
    snap = get_stock_snapshot(code)
    if snap["errors"]:
        print(f"   ⚠️ 部分数据缺失：{list(snap['errors'])}")
    state = (read_json(MARKET_STATE_FILE) or {}).get("state")
    print("② 对照选股/估值/风控规则逐项判定（含多锚估值）……")
    result = analyze_stock(snap, market_state=state)
    warnings = check_stock_result(result, snap, load_config())
    if warnings:
        print(f"   ⚠️ 逻辑自检发现 {len(warnings)} 条预警（已写入报告第八节）")
    print(f"③ 渲染并归档报告（格式：{output_format}）……")
    md, meta = render_stock_report(snap, result, self_check=warnings)
    path = archive_stock_report(md, meta, output_format=output_format)
    print(f"✅ 个股报告已生成：{path}")
    for note in meta.get("format_notes", []):
        print(f"   ℹ️ {note}")
    print(f"   结论：{result['overall']}")
    mv = result.get("multi_valuation") or {}
    if mv.get("combined"):
        c = mv["combined"]
        print(f"   多锚估值：合理区间 {c['合理区间']} ｜ 安全边际 {c['安全边际价']} ｜ 高估阈 {c['高估阈值']}")
    elif mv.get("diverged"):
        print(f"   多锚估值：方法偏差超30%，未加权（详见报告第五节）")
    print(f"   推定/口径说明项：{len(result['assumptions'])} 条（详见报告第八节）")
    return path


def cmd_monthly(period: str | None = None):
    from src.analyzer.system_iter import run_monthly_iteration
    path = run_monthly_iteration(period)
    print(f"✅ 月度迭代报告已生成：{path}")
    print("   ⚠️ 所有修订仅为草案，不会修改 config/；确认后请手动修改并先执行 backup-config")
    return path


def cmd_quarterly(period: str | None = None):
    from src.analyzer.system_iter import run_quarterly_iteration
    path = run_quarterly_iteration(period)
    print(f"✅ 季度深度复盘报告已生成：{path}")
    print("   ⚠️ 所有修订仅为草案，不会修改 config/")
    return path


def cmd_backup(note: str = ""):
    from src.utils.version_manager import backup_config
    vid = backup_config(note=note or "手动备份")
    print(f"✅ 配置已备份：config/history/{vid}/")
    return vid


def cmd_rollback(version_id: str):
    from src.utils.version_manager import rollback
    result = rollback(version_id)
    print(f"✅ 已回滚到 {version_id}（恢复 {len(result['restored'])} 个文件）")
    print(f"   回滚前配置已自动备份为：{result['safety_backup']}")


def cmd_set_state(state: str):
    """人工判定市场状态（§1.3，优先于程序初判，7日有效）。"""
    from src.analyzer.market_analyzer import MARKET_STATE_FILE, STATE_KEYS
    from src.utils.file_utils import write_json
    if state not in STATE_KEYS:
        print(f"❌ 无效状态。可选：{STATE_KEYS}")
        sys.exit(1)
    write_json(MARKET_STATE_FILE, {"state": state,
                                   "date": datetime.now().strftime("%Y-%m-%d"),
                                   "source": "manual",
                                   "note": "人工判定（§1.3：每周日更新，一周内不改判；优先于程序初判）"})
    print(f"✅ 市场状态已人工记录：{state}（{datetime.now():%Y-%m-%d}，7日内优先于程序初判）")


def cmd_judge_state():
    """市场状态程序初判（v4.2.0 auto_judgment）：量化条件自动核对并落盘。"""
    from src.analyzer.market_analyzer import judge_and_record_state, STATE_NAMES_CN
    result = judge_and_record_state()
    print("—— 市场状态程序初判（v4.2.0）——")
    for c in result["conditions"]:
        mark = {True: "✅", False: "❌", None: "·"}[c["程序判定"]]
        print(f"  {mark} [{c['区']}] {c['条件']}：{c['当前值']}")
    print(f"A区命中 {result['a_hits']} 条 ｜ C区命中 {result['c_hits']} 条 ｜ "
          f"股债利差 {result['equity_bond_spread']:.4f}" if result.get("equity_bond_spread") is not None
          else f"A区命中 {result['a_hits']} 条 ｜ C区命中 {result['c_hits']} 条 ｜ 股债利差数据缺失")
    print(f"初判结果：{STATE_NAMES_CN.get(result['auto_state'], '无法判定')}（{result['auto_basis']}）")
    if result.get("recorded"):
        print(f"✅ 已落盘 data/processed/market_state.json（source=auto）；人工 set-state 可随时覆盖")
    else:
        print(f"ℹ️ 未落盘：{result.get('record_note')}")


def cmd_watchlist(action: str, code: str | None = None):
    """择时备选池维护（§2.3：30-50只，季度审核；入池=承担研究笔记义务）。"""
    from src.analyzer.stock_analyzer import WATCHLIST_FILE, _watchlist
    from src.data.data_utils import normalize_stock_code
    from src.utils.file_utils import write_json
    stocks = _watchlist()
    if action == "list":
        print(f"备选池共 {len(stocks)} 只（§2.3要求30-50只，覆盖≥5行业）：")
        for s in stocks:
            print(f"  {s.get('code')}  {s.get('name') or ''}  入池 {s.get('added')}")
        return
    if not code:
        print("❌ 用法：python run.py watchlist add|remove <代码>")
        sys.exit(1)
    code = normalize_stock_code(code)
    if action == "add":
        if any(s.get("code") == code for s in stocks):
            print(f"ℹ️ {code} 已在备选池")
            return
        name = None
        try:
            from src.data.stock_data import get_stock_snapshot
            name = (get_stock_snapshot(code).get("basic") or {}).get("名称")
        except Exception:
            pass
        stocks.append({"code": code, "name": name,
                       "added": datetime.now().strftime("%Y-%m-%d")})
        write_json(WATCHLIST_FILE, {"stocks": stocks})
        print(f"✅ 已入池：{code} {name or ''}（共{len(stocks)}只）")
        print("   ⚠️ §2.3义务：入池标的须有研究笔记（商业模式/竞争优势/风险/估值锚），季度审核")
    elif action == "remove":
        stocks = [s for s in stocks if s.get("code") != code]
        write_json(WATCHLIST_FILE, {"stocks": stocks})
        print(f"✅ 已移出备选池：{code}（余{len(stocks)}只）")
    else:
        print("❌ 用法：python run.py watchlist list|add|remove [代码]")


def _sync_after(cmd: str):
    """
    命令执行后自动提交+推送到GitHub（用户2026-07-07授权的标准行为，无需逐次确认）。
    仅同步git跟踪范围内的变更（报告/配置/代码等）；data/缓存等被.gitignore排除的
    内容由OneDrive的实时文件同步负责，两者互补。同步失败不影响命令本身已完成的结果。
    """
    try:
        from src.utils.git_sync import auto_sync
        result = auto_sync(f"run.py {cmd}")
        if result["committed"]:
            icon = "🔄" if result["pushed"] else "⚠️"
            print(f"{icon} Git同步：{result['note']}")
    except Exception as exc:  # noqa: BLE001 —— 同步失败绝不能掩盖命令本身的结果
        print(f"⚠️ Git同步异常（不影响本次操作结果）：{exc}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd, args = sys.argv[1], sys.argv[2:]
    no_sync = "--no-sync" in args
    if no_sync:
        args = [a for a in args if a != "--no-sync"]
    # --format md|html|pdf|all（任务三：缺省html；md=仅MD；pdf/all=MD+HTML+PDF）
    output_format = "html"
    if "--format" in args:
        i = args.index("--format")
        if i + 1 < len(args) and args[i + 1] in ("md", "html", "pdf", "all"):
            output_format = args[i + 1]
            args = args[:i] + args[i + 2:]
        else:
            print("❌ --format 取值：md | html | pdf | all")
            sys.exit(1)
    dispatch = {
        "daily": lambda: cmd_daily(args[0] if args else None, output_format),
        "stock": lambda: cmd_stock(args[0], output_format) if args else print("❌ 用法：python run.py stock <代码>"),
        "monthly": lambda: cmd_monthly(args[0] if args else None),
        "quarterly": lambda: cmd_quarterly(args[0] if args else None),
        "backup-config": lambda: cmd_backup(" ".join(args)),
        "rollback": lambda: cmd_rollback(args[0]) if args else print("❌ 用法：python run.py rollback <版本号>"),
        "set-state": lambda: cmd_set_state(args[0]) if args else print("❌ 用法：python run.py set-state <A_undervalued|B_low|B_neutral|B_high|C_overvalued>"),
        "judge-state": cmd_judge_state,
        "watchlist": lambda: cmd_watchlist(args[0], args[1] if len(args) > 1 else None)
        if args else print("❌ 用法：python run.py watchlist list|add|remove [代码]"),
    }
    if cmd not in dispatch:
        print(f"❌ 未知命令 {cmd}\n{__doc__}")
        sys.exit(1)
    dispatch[cmd]()
    if not no_sync:
        _sync_after(cmd)


if __name__ == "__main__":
    main()
