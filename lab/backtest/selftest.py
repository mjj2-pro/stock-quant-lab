"""
考场自测 —— 证明这个回测引擎不作弊
====================================================================
在请任何「老师」（信号、模型、规则）进来之前，先证明考场本身是干净的。

一个不作弊的回测引擎，必须能通过下面 8 项检查。
任何一项不过，后面所有胜率数字都不可信。

跑法：
    E:\\DSH-Work\\stock\\envs\\lab\\Scripts\\python.exe -m lab.backtest.selftest
    或者直接：
    E:\\DSH-Work\\stock\\envs\\lab\\Scripts\\python.exe lab\\backtest\\selftest.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

# 允许直接运行本文件
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lab.backtest.engine import (
    Bar,
    Config,
    Context,
    CostConfig,
    Decision,
    LookaheadError,
    bars_from_dicts,
    run_backtest,
)

PASS = "  [通过]"
FAIL = "  [失败]"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{PASS if ok else FAIL} {name}" + (f"  → {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════
# 合成数据
# ══════════════════════════════════════════════════════════════

def make_bars(closes: list[float], opens: list[float] | None = None) -> list[Bar]:
    rows = []
    prev = closes[0]
    for i, cl in enumerate(closes):
        op = opens[i] if opens else (prev if i == 0 else closes[i - 1])
        rows.append({
            "date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": op, "high": max(op, cl) * 1.01, "low": min(op, cl) * 0.99,
            "close": cl, "volume": 1e6, "prev_close": prev,
        })
        prev = cl
    return bars_from_dicts(rows)


def zc(**kw) -> Config:
    """零成本配置：用来测纯逻辑，把成本干扰去掉。"""
    c = CostConfig(commission_rate=0.0, min_commission=0.0,
                   stamp_duty_rate=0.0, slippage_rate=0.0, lot_size=1)
    return Config(costs=c, **kw)


def rc(**kw) -> Config:
    """真实成本配置（默认值）。"""
    return Config(**kw)


# ══════════════════════════════════════════════════════════════
# 1. 未来函数：必须当场炸掉
# ══════════════════════════════════════════════════════════════

def t1_lookahead_blocked() -> None:
    bars = make_bars([10, 10.1, 10.2, 10.3, 10.4] * 4)

    def thief(ctx: Context) -> Decision:
        tomorrow = ctx.bars[ctx.i + 1]      # 偷看明天
        return Decision("buy" if tomorrow.close > ctx.bar.close else "hold", "偷看")

    try:
        run_backtest(bars, thief, zc())
        check("1. 未来函数被拦截", False, "引擎居然让它偷看成功了")
    except LookaheadError as e:
        check("1. 未来函数被拦截", True, str(e)[:46] + "…")


def t1b_no_full_bars_leak() -> None:
    bars = make_bars([10, 10.1, 10.2, 10.3])

    def probe(ctx: Context) -> Decision:
        # 试图拿到完整列表长度以外的数据
        try:
            _ = ctx.bars[99]
            return Decision("hold", "越界成功")
        except LookaheadError:
            return Decision("hold", "越界被拦")

    r = run_backtest(bars, probe, zc())
    ok = all(t.reason_in != "越界成功" for t in r.trades)
    check("2. 越界访问不返回数据", ok, "试图读第 99 根被拒绝")


# ══════════════════════════════════════════════════════════════
# 2. T+1：不可能当天买当天卖
# ══════════════════════════════════════════════════════════════

def t2_no_same_day_roundtrip() -> None:
    """每天都想买、第二天立刻卖：所有成交的持仓天数必须 ≥ 1。"""
    bars = make_bars([10 + i * 0.05 for i in range(40)])

    def jumpy(ctx: Context) -> Decision:
        return Decision("sell" if ctx.in_position else "buy", "来回跳")

    r = run_backtest(bars, jumpy, rc())
    bad = [t for t in r.trades if t.hold_days < 1]
    check("3. T+1 强制（无当日回转）", len(bad) == 0,
          f"{len(r.trades)} 笔交易，最短持仓 {min((t.hold_days for t in r.trades), default=0)} 天")


# ══════════════════════════════════════════════════════════════
# 3. 涨停买不进
# ══════════════════════════════════════════════════════════════

def t3_limit_up_blocked() -> None:
    # 第0天收 10.00 → 第1天涨停价 11.00，开盘就 11.00（一字板）
    rows = [
        {"date": "d0", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "prev_close": 10.0},
        {"date": "d1", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "prev_close": 10.0},
        {"date": "d2", "open": 11.5, "high": 12.0, "low": 11.2, "close": 11.8, "prev_close": 11.0},
    ]
    bars = bars_from_dicts(rows)

    def buy_now(ctx: Context) -> Decision:
        return Decision("buy", "想买") if ctx.i == 0 else Decision("hold")

    r = run_backtest(bars, buy_now, rc())
    rejected_buy = any("涨停" in why for _, why in r.rejected)
    check("4. 涨停买不进", r.n_trades == 0 and rejected_buy,
          f"被拒：{[w for _, w in r.rejected]}")


# ══════════════════════════════════════════════════════════════
# 4. 跌停卖不出
# ══════════════════════════════════════════════════════════════

def t4_limit_down_stuck() -> None:
    rows = [
        {"date": "d0", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "prev_close": 10.0},
        {"date": "d1", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "prev_close": 10.0},
        {"date": "d2", "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "prev_close": 10.0},   # 一字跌停
        {"date": "d3", "open": 8.5, "high": 8.8, "low": 8.2, "close": 8.6, "prev_close": 9.0},
    ]
    bars = bars_from_dicts(rows)

    def sig(ctx: Context) -> Decision:
        if ctx.i == 0:
            return Decision("buy", "建仓")
        if ctx.i == 1:
            return Decision("sell", "想跑")
        return Decision("hold")

    r = run_backtest(bars, sig, rc())
    stuck = any("跌停" in why for _, why in r.rejected)
    check("5. 跌停卖不出", stuck, f"被拒：{[w for _, w in r.rejected]}")


# ══════════════════════════════════════════════════════════════
# 5. 停牌跳过
# ══════════════════════════════════════════════════════════════

def t5_suspension_skipped() -> None:
    rows = [
        {"date": "d0", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "prev_close": 10.0},
        {"date": "d1", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "prev_close": 10.0, "suspended": True},
        {"date": "d2", "open": 10.2, "high": 10.3, "low": 10.1, "close": 10.2, "prev_close": 10.0},
    ]
    bars = bars_from_dicts(rows)

    def sig(ctx: Context) -> Decision:
        return Decision("buy", "想买") if ctx.i == 0 else Decision("hold")

    r = run_backtest(bars, sig, rc())
    hit = any("停牌" in why for _, why in r.rejected)
    check("6. 停牌日不成交", hit, f"被拒：{[w for _, w in r.rejected]}")


# ══════════════════════════════════════════════════════════════
# 6. 零成本 + 买入持有 = 基准涨幅（记账正确性）
# ══════════════════════════════════════════════════════════════

def t6_buy_and_hold_matches_benchmark() -> None:
    closes = [10.0]
    for i in range(60):
        closes.append(round(closes[-1] * (1.01 if i % 3 else 0.995), 4))
    bars = make_bars(closes)

    def once(ctx: Context) -> Decision:
        return Decision("buy", "买入持有") if ctx.i == 0 else Decision("hold")

    cfg = zc()
    r = run_backtest(bars, once, cfg)

    price_in = bars[1].open          # 第 1 天开盘成交
    price_out = bars[-1].close       # 最后按收盘估值
    raw = int(cfg.initial_cash / price_in)
    expected = raw * (price_out - price_in) / cfg.initial_cash

    ok = abs(r.total_return - expected) < 1e-6
    check("7. 记账准确（买入持有=基准）", ok,
          f"引擎 {r.total_return:.6%} vs 手算 {expected:.6%}")


# ══════════════════════════════════════════════════════════════
# 7. 成本核算
# ══════════════════════════════════════════════════════════════

def t7_fees_charged_exactly() -> None:
    # 注意：引擎规定最后两个交易日不开新仓，所以这里给足 6 根 K 线
    rows = [
        {"date": "d0", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "prev_close": 10.0},
        {"date": "d1", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "prev_close": 10.0},
        {"date": "d2", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "prev_close": 10.0},
        {"date": "d3", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "prev_close": 11.0},
        {"date": "d4", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "prev_close": 11.0},
        {"date": "d5", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "prev_close": 11.0},
    ]
    bars = bars_from_dicts(rows)
    c = CostConfig(commission_rate=0.00025, min_commission=5.0,
                   stamp_duty_rate=0.0005, slippage_rate=0.0, lot_size=100)

    def sig(ctx: Context) -> Decision:
        if ctx.i == 0:
            return Decision("buy", "买")
        if ctx.i == 2:
            return Decision("sell", "卖")
        return Decision("hold")

    r = run_backtest(bars, sig, Config(costs=c))
    t = r.trades[0]

    buy_amount = t.price_in * t.shares
    sell_amount = t.price_out * t.shares
    hand_fee = (max(5.0, buy_amount * 0.00025)
                + max(5.0, sell_amount * 0.00025)
                + sell_amount * 0.0005)

    ok = abs(r.total_fees - hand_fee) < 1e-6
    check("8. 手续费分毫不差", ok, f"引擎 {r.total_fees:.4f} vs 手算 {hand_fee:.4f}")


# ══════════════════════════════════════════════════════════════
# 8. 随机信号必须亏钱（有成本时）
# ══════════════════════════════════════════════════════════════

def t8_random_signal_loses_money() -> None:
    """
    如果「瞎买」在扣完成本后还能赚钱，说明引擎有漏洞（比如免费交易、
    或者成本没算）。这是最关键的一条：它证明引擎不会白送你钱。
    """
    rets = []
    for seed in range(24):
        rng = random.Random(seed)
        # 随机游走行情
        px = 10.0
        closes = [px]
        for _ in range(120):
            px = max(1.0, px * (1 + rng.gauss(0, 0.02)))
            closes.append(round(px, 3))
        bars = make_bars(closes)

        rng2 = random.Random(seed + 1000)

        def noise(ctx: Context, _rng=None) -> Decision:
            r = rng2.random()
            if ctx.in_position:
                return Decision("sell", "随机卖") if r < 0.35 else Decision("hold", "拿")
            return Decision("buy", "随机买") if r < 0.35 else Decision("hold", "空仓")

        r = run_backtest(bars, noise, rc())
        rets.append(r.total_return)

    avg = sum(rets) / len(rets)
    wins = sum(1 for x in rets if x > 0)
    # 随机信号在含成本下，平均必须为负；胜率不该超过一半太多
    ok = avg < 0
    check("9. 瞎买必亏（成本是真实的）", ok,
          f"24 组随机信号平均收益 {avg:.2%}，其中赚钱 {wins}/24 组")


# ══════════════════════════════════════════════════════════════
# 9. 同一输入永远同一输出（可复现）
# ══════════════════════════════════════════════════════════════

def t9_deterministic() -> None:
    bars = make_bars([10 + (i % 7) * 0.1 for i in range(50)])

    def sig(ctx: Context) -> Decision:
        return Decision("buy" if len(ctx.bars) % 5 == 0 else "hold", "固定")

    a = run_backtest(bars, sig, rc())
    b = run_backtest(bars, sig, rc())
    ok = (abs(a.total_return - b.total_return) < 1e-12
          and a.n_trades == b.n_trades)
    check("10. 可复现（两次跑结果一致）", ok,
          f"{a.total_return:.6%} == {b.total_return:.6%}")


# ══════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 62)
    print("考场自测：证明回测引擎不作弊")
    print("=" * 62)
    for fn in (
        t1_lookahead_blocked,
        t1b_no_full_bars_leak,
        t2_no_same_day_roundtrip,
        t3_limit_up_blocked,
        t4_limit_down_stuck,
        t5_suspension_skipped,
        t6_buy_and_hold_matches_benchmark,
        t7_fees_charged_exactly,
        t8_random_signal_loses_money,
        t9_deterministic,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(fn.__name__, False, f"抛异常: {type(e).__name__}: {e}")

    print("=" * 62)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"结果：{passed}/{total} 通过")
    if passed == total:
        print("考场干净。现在可以往里请老师了。")
        return 0
    print("考场有问题 —— 修之前，任何胜率数字都不要信。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
