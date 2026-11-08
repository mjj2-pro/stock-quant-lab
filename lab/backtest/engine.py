"""
T+1 回测引擎 —— 考场
====================================================================
这个文件是整套系统里唯一不能糊弄的部分。

它只干一件事：把「某个信号」放到历史里跑一遍，算出它是赚是亏。
但它必须满足四条铁律，否则算出来的数字是假的：

  铁律一 · 时间对齐
      信号函数在 t 日收盘后做决策，物理上看不到 t+1 及以后的数据。
      如果有人绕过它偷看未来，引擎会抛 LookaheadError 当场炸掉。

  铁律二 · 可交易性
      涨停买不进、跌停卖不出、停牌跳过、当天买当天不许卖（A股 T+1）。
      回测里买到了现实买不到的东西，就是自欺欺人。

  铁律三 · 成本不豁免
      佣金、印花税、滑点，一分不少。
      很多回测的「高胜率」是被零成本喂出来的。

  铁律四 · 可复现
      同样的输入，永远同样的输出。没有随机数，没有隐式状态。

用法见同目录 README.md，或直接看 selftest.py 里的例子。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence


# ══════════════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════════════

class LookaheadError(RuntimeError):
    """信号函数试图访问未来数据。这是回测里最严重的错误，必须当场炸掉。"""


class NoDataError(RuntimeError):
    """K 线数据为空或字段不全。"""


# ══════════════════════════════════════════════════════════════
# 数据
# ══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class Bar:
    """一根日 K 线。所有价格用 float，单位：元。"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    prev_close: float = 0.0
    limit_up: float = 0.0        # 涨停价，0 表示不限（如港股/美股）
    limit_down: float = 0.0      # 跌停价，0 表示不限
    suspended: bool = False      # 停牌

    def __post_init__(self) -> None:
        if self.prev_close <= 0:
            self.prev_close = self.close
        if self.limit_up <= 0:
            self.limit_up = round(self.prev_close * 1.10, 2)
        if self.limit_down <= 0:
            self.limit_down = round(self.prev_close * 0.90, 2)


class BarView:
    """
    只读、且只能看到 [0..i] 的 K 线视图。

    这是防作弊的核心：信号函数拿到的是它，而不是完整的 bars 列表。
    它就算把 self 存起来，也永远看不到 i 之后的东西——因为越界直接抛异常。
    """

    __slots__ = ("_bars", "_i")

    def __init__(self, bars: Sequence[Bar], i: int) -> None:
        self._bars = bars
        self._i = i

    @property
    def today_index(self) -> int:
        return self._i

    def __len__(self) -> int:
        return self._i + 1

    def __getitem__(self, k):
        n = self._i + 1
        if isinstance(k, slice):
            start, stop, step = k.indices(n)
            if step < 0:
                start, stop, step = k.indices(n)
            # 任何切片的终点都被夹到 n 以内（越界不报错，只截断）
            return [self._bars[j] for j in range(start, min(stop, n), step or 1)]
        if k < 0:
            k += n
        if k < 0 or k >= n:
            raise LookaheadError(
                f"信号函数试图访问第 {k} 根 K 线，但今天只到第 {self._i} 根。"
                f"这是偷看未来，回测结果无效。"
            )
        return self._bars[k]

    def window(self, n: int) -> list[Bar]:
        """最近 n 根（含今天）。不够 n 根就返回现有的全部。"""
        if n <= 0:
            return []
        lo = max(0, self._i - n + 1)
        return list(self._bars[lo:self._i + 1])

    @property
    def today(self) -> Bar:
        return self._bars[self._i]


# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class CostConfig:
    """交易成本。默认值按 A 股常见口径，宁可算贵一点。"""
    commission_rate: float = 0.00025   # 佣金 0.025%（双边）
    min_commission: float = 5.0        # 单笔最低 5 元
    stamp_duty_rate: float = 0.0005    # 印花税 0.05%（仅卖出）
    slippage_rate: float = 0.001       # 滑点 0.1%
    lot_size: int = 100                # 一手 100 股


@dataclass(slots=True)
class Config:
    initial_cash: float = 100_000.0
    costs: CostConfig = field(default_factory=CostConfig)
    max_position_pct: float = 1.0      # 单票最大仓位比例
    max_hold_days: int = 0             # >0 时：超过这个天数强制卖出（0=不限）
    allow_same_day_sell: bool = False  # A股必须 False


# ══════════════════════════════════════════════════════════════
# 信号
# ══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class Decision:
    """信号函数的返回值。"""
    action: str = "hold"      # 'buy' | 'sell' | 'hold'
    reason: str = ""          # 为什么（会写进交易流水，方便复盘）
    size_pct: float = 1.0     # 买入时用可用现金的百分之多少

    def __post_init__(self) -> None:
        if self.action not in ("buy", "sell", "hold"):
            raise ValueError(f"未知动作: {self.action}")


@dataclass(slots=True)
class Context:
    """交给信号函数的全部信息。注意：没有明日数据，没有全量数据。"""
    i: int
    bars: BarView
    cash: float
    shares: int
    holding_days: int
    equity: float

    @property
    def bar(self) -> Bar:
        return self.bars.today

    @property
    def in_position(self) -> bool:
        return self.shares > 0


SignalFn = Callable[[Context], Decision]


# ══════════════════════════════════════════════════════════════
# 结果
# ══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class Trade:
    date_in: str
    date_out: str
    price_in: float
    price_out: float
    shares: int
    fee_in: float
    fee_out: float
    pnl: float
    pnl_pct: float
    hold_days: int
    reason_in: str
    reason_out: str


@dataclass
class Result:
    initial_cash: float
    final_equity: float
    total_return: float
    max_drawdown: float
    n_trades: int
    win_rate: float
    avg_hold_days: float
    total_fees: float
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (日期, 原因)

    def summary(self) -> str:
        lines = [
            "─" * 58,
            f"期初资金      {self.initial_cash:>14,.2f}",
            f"期末权益      {self.final_equity:>14,.2f}",
            f"总收益率      {self.total_return:>13.2%}",
            f"最大回撤      {self.max_drawdown:>13.2%}",
            f"交易次数      {self.n_trades:>14d}",
            f"胜率          {self.win_rate:>13.2%}",
            f"平均持仓天数  {self.avg_hold_days:>13.2f}",
            f"总手续费      {self.total_fees:>14,.2f}",
        ]
        if self.rejected:
            lines.append(f"被拒订单      {len(self.rejected):>14d}（涨停/跌停/停牌/T+1）")
        lines.append("─" * 58)
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 内部：一笔订单的成交核算
# ══════════════════════════════════════════════════════════════

def _buy_fee(amount: float, c: CostConfig) -> float:
    return max(c.min_commission, amount * c.commission_rate)


def _sell_fee(amount: float, c: CostConfig) -> float:
    return max(c.min_commission, amount * c.commission_rate) + amount * c.stamp_duty_rate


def _is_limit_up_open(bar: Bar) -> bool:
    """开盘就在涨停价上 —— 买不进。"""
    return bar.limit_up > 0 and bar.open >= bar.limit_up - 1e-6


def _is_limit_down_open(bar: Bar) -> bool:
    """开盘就在跌停价上 —— 卖不出。"""
    return bar.limit_down > 0 and bar.open <= bar.limit_down + 1e-6


# ══════════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════════

def run_backtest(
    bars: Sequence[Bar],
    signal_fn: SignalFn,
    cfg: Config | None = None,
) -> Result:
    """
    跑一遍回测。

    时序（很重要，写死在这里，信号函数无法改变）：
        第 t 日 收盘后 → 信号函数决策
        第 t+1 日 开盘 → 按开盘价成交
        第 t+1 日 收盘 → 记净值
    """
    cfg = cfg or Config()
    if not bars:
        raise NoDataError("K 线为空")
    c = cfg.costs

    cash: float = cfg.initial_cash
    shares: int = 0
    buy_index: int = -1
    buy_price: float = 0.0
    buy_fee: float = 0.0
    buy_reason: str = ""
    buy_amount: float = 0.0

    pending: Decision | None = None
    pending_i: int = -1

    trades: list[Trade] = []
    curve: list[tuple[str, float]] = []
    rejected: list[tuple[str, str]] = []
    total_fees: float = 0.0

    for i, bar in enumerate(bars):
        # ── 1. 执行上一个交易日收盘时产生的订单（用今天开盘价） ──
        if pending is not None and pending_i == i - 1:
            d = pending
            pending = None

            if bar.suspended:
                rejected.append((bar.date, f"{d.action}：停牌"))
            elif d.action == "buy" and shares == 0:
                if _is_limit_up_open(bar):
                    rejected.append((bar.date, "买入：开盘涨停，买不进"))
                else:
                    price = bar.open * (1 + c.slippage_rate)
                    budget = cash * min(max(d.size_pct, 0.0), cfg.max_position_pct)
                    raw = int(budget / price // c.lot_size) * c.lot_size
                    # 预留手续费，避免超买
                    while raw > 0 and raw * price + _buy_fee(raw * price, c) > cash:
                        raw -= c.lot_size
                    if raw <= 0:
                        rejected.append((bar.date, "买入：资金不足一手"))
                    else:
                        amount = raw * price
                        fee = _buy_fee(amount, c)
                        cash -= amount + fee
                        shares = raw
                        buy_index = i
                        buy_price = price
                        buy_fee = fee
                        buy_reason = d.reason
                        buy_amount = amount
                        total_fees += fee

            elif d.action == "sell" and shares > 0:
                if not cfg.allow_same_day_sell and buy_index == i:
                    rejected.append((bar.date, "卖出：A股 T+1，当天不能卖"))
                elif _is_limit_down_open(bar):
                    rejected.append((bar.date, "卖出：开盘跌停，卖不出"))
                else:
                    price = bar.open * (1 - c.slippage_rate)
                    amount = shares * price
                    fee = _sell_fee(amount, c)
                    cash += amount - fee
                    total_fees += fee
                    gross_in = buy_amount + buy_fee
                    pnl = (amount - fee) - gross_in
                    trades.append(Trade(
                        date_in=bars[buy_index].date,
                        date_out=bar.date,
                        price_in=buy_price,
                        price_out=price,
                        shares=shares,
                        fee_in=buy_fee,
                        fee_out=fee,
                        pnl=pnl,
                        pnl_pct=pnl / gross_in if gross_in else 0.0,
                        hold_days=i - buy_index,
                        reason_in=buy_reason,
                        reason_out=d.reason,
                    ))
                    shares = 0
                    buy_index = -1
                    buy_price = 0.0
                    buy_fee = 0.0
                    buy_amount = 0.0

        # ── 2. 记净值（用今日收盘价估值） ──
        equity = cash + shares * bar.close
        curve.append((bar.date, equity))

        # ── 3. 收盘后决策 ──
        #     有持仓且到期限 → 强制平仓信号
        #     注意：信号今天发、明天开盘才成交，所以这里减 1，
        #     保证实际持仓天数正好等于 max_hold_days（不会多关一天）。
        if cfg.max_hold_days > 0 and shares > 0 and (i - buy_index) >= cfg.max_hold_days - 1:
            pending, pending_i = Decision("sell", f"到期出局（上限 {cfg.max_hold_days} 天）"), i
            continue

        ctx = Context(
            i=i,
            bars=BarView(bars, i),
            cash=cash,
            shares=shares,
            holding_days=(i - buy_index) if shares > 0 else 0,
            equity=equity,
        )
        try:
            d = signal_fn(ctx)
        except LookaheadError:
            raise
        if d is None:
            continue
        # 最后两个交易日不再开新仓：
        # 倒数第二天开仓 → 最后一天只能按收盘估值结算，等于"当天买当天结算"，
        # 会污染 T+1 统计。宁可少跑两天，也不让账本说谎。
        if d.action != "hold" and i < len(bars) - 2:
            pending, pending_i = d, i

    # ── 4. 收尾：最后一天还拿着，按收盘市值估值（未平仓，不扣卖出成本） ──
    final_equity = cash + shares * bars[-1].close
    if shares > 0:
        last = bars[-1]
        amount = shares * last.close
        gross_in = buy_amount + buy_fee
        pnl = amount - gross_in          # 浮动盈亏：没卖，就不收印花税和佣金
        trades.append(Trade(
            date_in=bars[buy_index].date,
            date_out=last.date + "(未平仓)",
            price_in=buy_price,
            price_out=last.close,
            shares=shares,
            fee_in=buy_fee,
            fee_out=0.0,
            pnl=pnl,
            pnl_pct=pnl / gross_in if gross_in else 0.0,
            hold_days=len(bars) - 1 - buy_index,
            reason_in=buy_reason,
            reason_out="回测结束，按收盘市值估值",
        ))

    # ── 5. 统计 ──
    peak = -1e18
    max_dd = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    closed = [t for t in trades if "(未平仓)" not in t.date_out]
    wins = sum(1 for t in closed if t.pnl > 0)
    win_rate = wins / len(closed) if closed else 0.0
    avg_hold = sum(t.hold_days for t in trades) / len(trades) if trades else 0.0

    return Result(
        initial_cash=cfg.initial_cash,
        final_equity=final_equity,
        total_return=(final_equity - cfg.initial_cash) / cfg.initial_cash,
        max_drawdown=max_dd,
        n_trades=len(closed),
        win_rate=win_rate,
        avg_hold_days=avg_hold,
        total_fees=total_fees,
        equity_curve=curve,
        trades=trades,
        rejected=rejected,
    )


# ══════════════════════════════════════════════════════════════
# 辅助：从字典列表构造 Bar（数据层对接点）
# ══════════════════════════════════════════════════════════════

def bars_from_dicts(rows: Sequence[dict]) -> list[Bar]:
    """
    把 [{date, open, high, low, close, volume, ...}, ...] 转成 Bar 列表。

    数据层（akshare / 爬虫 / CSV）只要能产出这种字典，就能进考场。
    涨跌停价会自动按前收盘推算（A股 ±10%），也可以用 limit_up 字段覆盖。
    """
    out: list[Bar] = []
    prev = 0.0
    for r in rows:
        pc = float(r.get("prev_close") or prev or r.get("close") or 0.0)
        out.append(Bar(
            date=str(r.get("date") or r.get("日期") or ""),
            open=float(r.get("open") or r.get("开盘") or 0.0),
            high=float(r.get("high") or r.get("最高") or 0.0),
            low=float(r.get("low") or r.get("最低") or 0.0),
            close=float(r.get("close") or r.get("收盘") or 0.0),
            volume=float(r.get("volume") or r.get("成交量") or 0.0),
            amount=float(r.get("amount") or r.get("成交额") or 0.0),
            prev_close=pc,
            limit_up=float(r.get("limit_up") or 0.0),
            limit_down=float(r.get("limit_down") or 0.0),
            suspended=bool(r.get("suspended") or False),
        ))
        prev = out[-1].close
    return out
