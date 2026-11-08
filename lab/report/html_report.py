"""
成绩单 —— 把回测结果渲染成一个能双击打开的网页
====================================================================
为什么用 HTML 而不是做个窗口程序：
  1. 零依赖，不装任何 GUI 库
  2. 手机上也能看（直接发文件过去就行）
  3. 断网也能看（所有样式图形都内嵌，没有外链）
  4. 不用截图就能存证

生成的是单个 .html 文件，双击用浏览器打开。
"""

from __future__ import annotations

import html
import time
import webbrowser
from pathlib import Path

from lab.backtest.engine import Bar, Result

OUT_DIR = Path(__file__).resolve().parents[1] / "report" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════
# 内嵌 SVG 绘图
# ══════════════════════════════════════════════════════════════

def _poly(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _equity_svg(curve: list[tuple[str, float]], w: int = 900, h: int = 260) -> str:
    if len(curve) < 2:
        return "<p class='muted'>数据不足，画不出曲线</p>"

    vals = [v for _, v in curve]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or max(hi * 0.01, 1.0)
    lo -= pad
    hi += pad
    n = len(vals)

    def px(i: int) -> float:
        return 40 + i * (w - 60) / (n - 1)

    def py(v: float) -> float:
        return h - 30 - (v - lo) / (hi - lo) * (h - 55)

    pts = [(px(i), py(v)) for i, v in enumerate(vals)]
    line = _poly(pts)
    area = f"40,{h-30} " + line + f" {px(n-1)},{h-30}"

    # 基准：起始资金那条横线
    base_y = py(vals[0])
    # 首个交易日 / 最后
    x0, x1 = curve[0][0], curve[-1][0]

    # 涨跌决定主色
    up = vals[-1] >= vals[0]
    main = "#22c55e" if up else "#ef4444"

    return f"""
<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart">
  <defs>
    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{main}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{main}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  <line x1="40" y1="{base_y:.1f}" x2="{w-20}" y2="{base_y:.1f}"
        stroke="#64748b" stroke-width="1" stroke-dasharray="5 5"/>
  <polygon points="{area}" fill="url(#fill)"/>
  <polyline points="{line}" fill="none" stroke="{main}" stroke-width="2.2"
            stroke-linejoin="round" stroke-linecap="round"/>
  <text x="40" y="{base_y-6:.1f}" fill="#94a3b8" font-size="11">起始资金 {vals[0]:,.0f}</text>
  <text x="{w-20}" y="{h-8}" fill="#94a3b8" font-size="11" text-anchor="end">{html.escape(str(x1))}</text>
  <text x="40" y="{h-8}" fill="#94a3b8" font-size="11">{html.escape(str(x0))}</text>
</svg>"""


def _drawdown_svg(curve: list[tuple[str, float]], w: int = 900, h: int = 90) -> str:
    """回撤曲线：越往下越疼，这张图能看出策略是怎么死的。"""
    if len(curve) < 2:
        return ""
    vals = [v for _, v in curve]
    peak = vals[0]
    dd: list[float] = []
    for v in vals:
        peak = max(peak, v)
        dd.append((v - peak) / peak if peak else 0.0)

    n = len(dd)
    worst = min(dd) or -0.01

    def px(i: int) -> float:
        return 40 + i * (w - 60) / (n - 1)

    def py(v: float) -> float:
        return 12 + (1 - v / worst) * (h - 26)

    pts = [(px(i), py(v)) for i, v in enumerate(dd)]
    area = f"40,12 " + _poly(pts) + f" {px(n-1)},12"
    return f"""
<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart ddchart">
  <polygon points="{area}" fill="#ef4444" fill-opacity="0.25"/>
  <polyline points="{_poly(pts)}" fill="none" stroke="#f87171" stroke-width="1.6"/>
  <text x="40" y="{h-4}" fill="#94a3b8" font-size="11">最深回撤 {worst:.2%}</text>
</svg>"""


# ══════════════════════════════════════════════════════════════
# 主渲染
# ══════════════════════════════════════════════════════════════

def render(
    res: Result,
    code: str,
    name: str = "",
    start: str = "",
    end: str = "",
    signal_name: str = "",
    note: str = "",
) -> Path:
    tr = res.total_return
    good = tr > 0
    verdict = "赚钱" if good else "亏钱"
    color = "#22c55e" if good else "#ef4444"

    # 成本占本金的比例 —— 短线最容易忽略的一项
    fee_pct = res.total_fees / res.initial_cash if res.initial_cash else 0

    rows = []
    for t in res.trades[-30:]:
        cls = "win" if t.pnl > 0 else "lose"
        rows.append(
            f"<tr class='{cls}'><td>{html.escape(t.date_in)}</td>"
            f"<td>{html.escape(t.date_out)}</td>"
            f"<td class='num'>{t.shares:,}</td>"
            f"<td class='num'>{t.price_in:.2f}</td>"
            f"<td class='num'>{t.price_out:.2f}</td>"
            f"<td class='num'>{t.pnl_pct:+.2%}</td>"
            f"<td class='num'>{t.hold_days}</td>"
            f"<td class='why'>{html.escape(t.reason_in[:26])}</td></tr>"
        )

    rej = {}
    for _, why in res.rejected:
        rej[why] = rej.get(why, 0) + 1
    rej_rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td class='num'>{v}</td></tr>"
        for k, v in sorted(rej.items(), key=lambda x: -x[1])[:8]
    )

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    title = f"{code} {name}".strip()

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>成绩单 · {html.escape(title)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:20px; background:#0b1120; color:#e2e8f0;
         font-family: "Microsoft YaHei", -apple-system, "Segoe UI", sans-serif;
         line-height:1.55; }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin:0 0 4px; }}
  .sub {{ color:#94a3b8; font-size:13px; margin-bottom:18px; }}
  .big {{ font-size:44px; font-weight:800; color:{color}; line-height:1.1; margin:6px 0 2px; }}
  .big small {{ font-size:15px; font-weight:400; color:#94a3b8; margin-left:8px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
            gap:10px; margin:16px 0 22px; }}
  .card {{ background:#111c33; border:1px solid #1e2b45; border-radius:10px; padding:11px 13px; }}
  .card .k {{ color:#94a3b8; font-size:12px; }}
  .card .v {{ font-size:19px; font-weight:700; margin-top:3px; }}
  .chart {{ width:100%; height:260px; display:block; background:#0e1729;
            border:1px solid #1e2b45; border-radius:10px; }}
  .ddchart {{ height:90px; margin-top:8px; }}
  h2 {{ font-size:15px; margin:26px 0 8px; color:#cbd5e1;
        border-left:3px solid #3b82f6; padding-left:9px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; color:#94a3b8; font-weight:500; padding:7px 6px;
        border-bottom:1px solid #1e2b45; font-size:12px; }}
  td {{ padding:6px; border-bottom:1px solid #16233b; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.why {{ color:#7c8da6; font-size:12px; }}
  tr.win td:nth-child(6) {{ color:#22c55e; }}
  tr.lose td:nth-child(6) {{ color:#ef4444; }}
  .muted {{ color:#64748b; font-size:13px; }}
  .note {{ background:#152238; border-left:3px solid #f59e0b; padding:11px 14px;
           border-radius:6px; font-size:13px; color:#cbd5e1; margin:18px 0; }}
  .foot {{ color:#475569; font-size:12px; margin-top:30px; border-top:1px solid #1e2b45;
           padding-top:12px; }}
</style></head><body><div class="wrap">

<h1>{html.escape(title)}</h1>
<div class="sub">{html.escape(start)} → {html.escape(end)}　　信号：{html.escape(signal_name or "未命名")}</div>

<div class="big">{tr:+.2%}<small>总收益</small></div>

<div class="cards">
  <div class="card"><div class="k">期末权益</div><div class="v">{res.final_equity:,.0f}</div></div>
  <div class="card"><div class="k">最大回撤</div><div class="v" style="color:#f87171">-{res.max_drawdown:.2%}</div></div>
  <div class="card"><div class="k">交易次数</div><div class="v">{res.n_trades}</div></div>
  <div class="card"><div class="k">胜率</div><div class="v">{res.win_rate:.1%}</div></div>
  <div class="card"><div class="k">平均持仓</div><div class="v">{res.avg_hold_days:.1f} 天</div></div>
  <div class="card"><div class="k">手续费 / 本金</div><div class="v" style="color:#fbbf24">{fee_pct:.2%}</div></div>
</div>

<h2>净值曲线</h2>
{_equity_svg(res.equity_curve)}
{_drawdown_svg(res.equity_curve)}

<h2>交易明细（最近 30 笔）</h2>
<table>
<tr><th>买入</th><th>卖出</th><th>股数</th><th>买价</th><th>卖价</th><th>盈亏</th><th>持股</th><th>买入理由</th></tr>
{"".join(rows) if rows else "<tr><td colspan=8 class='muted'>没有成交</td></tr>"}
</table>

<h2>被拒订单（现实中买不到 / 卖不掉的情况）</h2>
<table>
<tr><th>原因</th><th>次数</th></tr>
{rej_rows if rej_rows else "<tr><td colspan=2 class='muted'>没有</td></tr>"}
</table>

<div class="note">
  <b>怎么读这份成绩单：</b><br>
  ① 先看「手续费 / 本金」——这个数字如果接近你的年化目标，说明你在给券商打工。<br>
  ② 再看净值曲线是不是一路向上——如果只在某一段暴涨，那是运气不是能力。<br>
  ③ 最后看回撤图：<b>你能承受的最深那条沟，才是你真实的风险</b>，不是那个漂亮的收益率。
</div>

<div class="foot">
  生成于 {stamp}　·　本地运行，数据未上传　·　
  回测赚钱 ≠ 实盘赚钱（滑点、冲击成本、手抖都算不进去）<br>
  {html.escape(note)}
</div>
</div></body></html>"""

    safe = "".join(c for c in code if c.isalnum() or c in "._-") or "report"
    out = OUT_DIR / f"成绩单-{safe}-{time.strftime('%Y%m%d-%H%M')}.html"
    out.write_text(doc, encoding="utf-8")
    return out


def open_in_browser(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:  # noqa: BLE001
        pass
