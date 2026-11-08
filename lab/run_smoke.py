"""
端到端冒烟：真实数据 → 考场 → 结果
====================================================================
用来验证整条链路通不通：
    抓数据 → 存缓存 → 喂考场 → 出成绩单

用法：
    E:\\DSH-Work\\stock\\envs\\lab\\Scripts\\python.exe lab\\run_smoke.py 000001
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.backtest.engine import Config, bars_from_dicts, run_backtest
from lab.data.sources import get_daily
from lab.signals.ma_cross import make_ma_cross


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "000001"
    start = sys.argv[2] if len(sys.argv) > 2 else "20240101"

    print(f"标的 {code}    起始 {start}")
    print("-" * 58)

    rows = get_daily(code, start=start, end="20991231", adjust="qfq")
    if not rows:
        print("!! 没拿到数据。检查代码是否正确、网络是否通、akshare 是否装好。")
        return 1

    print(f"拿到 {len(rows)} 根日线：{rows[0]['date']} → {rows[-1]['date']}")
    src = rows[0].get("source", "?")
    print(f"数据来源：{src}")

    bars = bars_from_dicts(rows)

    # 一个很笨的信号：均线金叉买、死叉卖，且持有不超过 3 天
    # （3 天是照着你的风格设的，不是因为它好）
    cfg = Config(max_hold_days=3)
    res = run_backtest(bars, make_ma_cross(5, 20), cfg)

    print()
    print(res.summary())

    if res.trades:
        print("最近 5 笔：")
        for t in res.trades[-5:]:
            print(f"  {t.date_in} → {t.date_out}  {t.shares}股  "
                  f"{t.pnl_pct:+.2%}  持{t.hold_days}天  [{t.reason_in}]")

    if res.rejected:
        from collections import Counter
        print("\n被拒订单统计（这些是回测里真实买不到/卖不掉的情况）：")
        for why, n in Counter(w for _, w in res.rejected).most_common(6):
            print(f"  {n:>4} 次  {why}")

    # ── 生成成绩单网页 ──
    try:
        from lab.report.html_report import open_in_browser, render
        out = render(
            res, code=code,
            start=rows[0]["date"], end=rows[-1]["date"],
            signal_name="MA5×MA20 均线交叉（笨样本）",
            note="这个信号只用来证明管道通了，不是策略。换掉它再谈成败。",
        )
        print(f"\n成绩单已生成：\n  {out}")
        open_in_browser(out)
    except Exception as e:  # noqa: BLE001
        print(f"\n(成绩单生成失败，不影响上面结果: {type(e).__name__}: {e})")

    print()
    print("⚠️ 这个均线信号是很笨的样本，只用来证明管道通了，不是策略。")
    print("   想比它强？写个自己的信号放进 lab/signals/，让考场判。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
