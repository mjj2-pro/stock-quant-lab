"""
示例信号：均线交叉（一个"老师"的样子）
====================================================================
这就是"老师"的标准形状。以后你自己写的、或者从别处接来的信号，
都长这个样子：

    输入：ctx —— 只能看到今天和以前（看不到未来，引擎保证）
    输出：Decision("buy" / "sell" / "hold", 理由)

别的什么都不用管。谁对谁错，交给考场去判。

这个信号很笨（均线交叉是老东西），它的作用是演示格式，
不是给你赚钱用的。
"""

from __future__ import annotations

from lab.backtest.engine import Bar, Context, Decision


def _ma(bars: list[Bar], n: int) -> float | None:
    """最近 n 根收盘均价。不够 n 根返回 None（不硬凑）。"""
    if len(bars) < n:
        return None
    return sum(b.close for b in bars[-n:]) / n


def make_ma_cross(fast: int = 5, slow: int = 20):
    """
    快线上穿慢线买入，下穿卖出。

    注意：算的是"今天"和"昨天"两条线的位置，
    数据全部来自 ctx.bars（<= 今天），没有任何未来信息。
    """

    def signal(ctx: Context) -> Decision:
        need = slow + 1
        hist = ctx.bars.window(need)
        if len(hist) < need:
            return Decision("hold", f"数据不足 {need} 根")

        today_bars = hist
        yday_bars = hist[:-1]

        f_now, s_now = _ma(today_bars, fast), _ma(today_bars, slow)
        f_prev, s_prev = _ma(yday_bars, fast), _ma(yday_bars, slow)
        if None in (f_now, s_now, f_prev, s_prev):
            return Decision("hold", "均线未成形")

        golden = f_prev <= s_prev and f_now > s_now     # 金叉
        dead = f_prev >= s_prev and f_now < s_now       # 死叉

        if not ctx.in_position and golden:
            return Decision("buy", f"MA{fast} 上穿 MA{slow}（金叉）")
        if ctx.in_position and dead:
            return Decision("sell", f"MA{fast} 下穿 MA{slow}（死叉）")
        return Decision("hold", "")

    return signal


SIGNAL = make_ma_cross()
