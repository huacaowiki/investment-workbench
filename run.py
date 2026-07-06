# -*- coding: utf-8 -*-
"""
run.py — 投资研究工作台一键入口
用法：
  python run.py daily                  生成当日市场日报
  python run.py daily 20260401        生成指定日期市场日报（历史统计类数据以当日可得为准）
  python run.py stock 600519          生成个股分析报告
  python run.py monthly [2026-06]     月度体系迭代（只出草案，绝不改config）
  python run.py quarterly [2026-Q2]   季度深度迭代（只出草案）
  python run.py backup-config [备注]   备份当前config到config/history
  python run.py rollback v001_xxx     回滚config到指定历史版本
  python run.py set-state B_neutral   记录人工判定的市场状态（§1.3每周日执行）
"""
from __future__ import annotations

import io
import sys
from datetime import datetime

# Windows 控制台 GBK 编码兜底，保证中文输出不乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))


def cmd_daily(day: str | None = None):
    """市场日报全链路：拉数 → 分析 → 渲染 → 归档。"""
    from src.data.market_data import get_market_snapshot
    from src.analyzer.market_analyzer import analyze_market
    from src.generator.report_writer import render_daily_report
    from src.generator.archiver import archive_daily_report

    print("① 拉取市场数据……")
    snap = get_market_snapshot(day=day)
    if snap["errors"]:
        print(f"   ⚠️ 部分数据缺失：{list(snap['errors'])}（按缺失处理，不影响出报）")
    print("② 执行市场分析（严格对照 config 铁则）……")
    analysis = analyze_market(snap)
    print("③ 渲染并归档报告……")
    md, meta = render_daily_report(analysis)
    path = archive_daily_report(md, meta)
    print(f"✅ 市场日报已生成：{path}")
    print(f"   情绪观察分：{meta['sentiment_score']}/10 ｜ 市场状态：{meta['effective_state'] or '未判定（请周日人工判定后 python run.py set-state <状态>）'}")
    for a in meta["alerts"][:3]:
        print(f"   [{a['级别']}] {a['内容'][:60]}")
    return path


def cmd_stock(code: str):
    """个股分析全链路：拉数 → 规则判定 → 渲染 → 归档。"""
    from src.data.stock_data import get_stock_snapshot
    from src.analyzer.market_analyzer import MARKET_STATE_FILE
    from src.analyzer.stock_analyzer import analyze_stock
    from src.generator.report_writer import render_stock_report
    from src.generator.archiver import archive_stock_report
    from src.utils.file_utils import read_json

    print(f"① 拉取个股数据（{code}）……")
    snap = get_stock_snapshot(code)
    if snap["errors"]:
        print(f"   ⚠️ 部分数据缺失：{list(snap['errors'])}")
    state = (read_json(MARKET_STATE_FILE) or {}).get("state")
    print("② 对照选股/估值/风控规则逐项判定……")
    result = analyze_stock(snap, market_state=state)
    print("③ 渲染并归档报告……")
    md, meta = render_stock_report(snap, result)
    path = archive_stock_report(md, meta)
    print(f"✅ 个股报告已生成：{path}")
    print(f"   结论：{result['overall']}")
    print(f"   待人工核验项：{len(result['manual_items'])} 条（详见报告第八节）")
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
    """记录每周日人工判定的市场状态（§1.3），供仓位映射与择时门槛使用。"""
    from src.analyzer.market_analyzer import MARKET_STATE_FILE, STATE_KEYS
    from src.utils.file_utils import write_json
    if state not in STATE_KEYS:
        print(f"❌ 无效状态。可选：{STATE_KEYS}")
        sys.exit(1)
    write_json(MARKET_STATE_FILE, {"state": state,
                                   "date": datetime.now().strftime("%Y-%m-%d"),
                                   "note": "人工判定（§1.3：每周日更新，一周内不改判）"})
    print(f"✅ 市场状态已记录：{state}（{datetime.now():%Y-%m-%d}）")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd, args = sys.argv[1], sys.argv[2:]
    dispatch = {
        "daily": lambda: cmd_daily(args[0] if args else None),
        "stock": lambda: cmd_stock(args[0]) if args else print("❌ 用法：python run.py stock <代码>"),
        "monthly": lambda: cmd_monthly(args[0] if args else None),
        "quarterly": lambda: cmd_quarterly(args[0] if args else None),
        "backup-config": lambda: cmd_backup(" ".join(args)),
        "rollback": lambda: cmd_rollback(args[0]) if args else print("❌ 用法：python run.py rollback <版本号>"),
        "set-state": lambda: cmd_set_state(args[0]) if args else print("❌ 用法：python run.py set-state <A_undervalued|B_low|B_neutral|B_high|C_overvalued>"),
    }
    if cmd not in dispatch:
        print(f"❌ 未知命令 {cmd}\n{__doc__}")
        sys.exit(1)
    dispatch[cmd]()


if __name__ == "__main__":
    main()
