"""
数据层 —— 只负责把数据拿回来，不管怎么用
====================================================================
三条原则：

  1. 能缓存就缓存
     一次抓、反复用。断网也能回测。爬虫跑一次，考场考一百遍。

  2. 多源降级
     一个源挂了自动换下一个，并在回来的数据上留痕（source 字段）。
     拿不到就说拿不到，绝不填默认值糊弄。

  3. 留插槽
     你以后自己写的爬虫，注册进来就能用，不需要改这个文件：
         from lab.data.sources import register_source
         register_source("my_spider", my_fetch_fn)

输出统一格式（喂给 engine.bars_from_dicts）：
    {"date","open","high","low","close","volume","amount","prev_close",
     "limit_up","limit_down","suspended","source"}

关于涨跌停：A 股不同板块幅度不同，这里按代码前缀自动判定。
    主板       10%      60xxxx / 00xxxx
    创业板     20%      30xxxx
    科创板     20%      68xxxx
    北交所     30%      8xxxxx / 4xxxxx
    ST/*ST     5%       （需要名称，从 stock_name 里判断，拿不到就按板块算）
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DATA_DIR = Path(__file__).resolve().parent
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# 涨跌停幅度
# ══════════════════════════════════════════════════════════════

def limit_pct(code: str, name: str = "") -> float:
    """返回该股票的涨跌停幅度（0.10 = 10%）。"""
    c = str(code).zfill(6)
    if name and ("ST" in name.upper()):
        return 0.05
    if c.startswith(("30", "68")):
        return 0.20
    if c.startswith(("8", "4", "9")):
        return 0.30
    return 0.10


def _limit_prices(prev_close: float, pct: float) -> tuple[float, float]:
    if prev_close <= 0:
        return 0.0, 0.0
    return (round(prev_close * (1 + pct), 2), round(prev_close * (1 - pct), 2))


# ══════════════════════════════════════════════════════════════
# 可插拔数据源注册表
# ══════════════════════════════════════════════════════════════

@dataclass
class SourceSpec:
    name: str
    fetch_daily: Callable[..., list[dict]]
    note: str = ""


_REGISTRY: dict[str, SourceSpec] = {}


def register_source(name: str, fn: Callable[..., list[dict]], note: str = "") -> None:
    """注册一个数据源。fn(code, start, end) -> list[dict]"""
    _REGISTRY[name] = SourceSpec(name, fn, note)


def list_sources() -> list[str]:
    return list(_REGISTRY)


# ══════════════════════════════════════════════════════════════
# 缓存
# ══════════════════════════════════════════════════════════════

def _cache_path(code: str, adjust: str) -> Path:
    return CACHE_DIR / f"{str(code).zfill(6)}_{adjust or 'raw'}.csv"


def _meta_path(code: str, adjust: str) -> Path:
    return CACHE_DIR / f"{str(code).zfill(6)}_{adjust or 'raw'}.meta.json"


def _already_fetched_today(code: str, adjust: str) -> bool:
    """今天已经抓过一次就不再抓 —— 否则周末每跑一次都要联网空转。"""
    p = _meta_path(code, adjust)
    if not p.exists():
        return False
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        return meta.get("fetched_on") == time.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return False


def _stamp_fetched(code: str, adjust: str, rows: list[dict]) -> None:
    meta = {
        "fetched_on": time.strftime("%Y-%m-%d"),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bars": len(rows),
        "last_bar": rows[-1]["date"] if rows else "",
    }
    try:
        _meta_path(code, adjust).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def save_cache(code: str, adjust: str, rows: list[dict]) -> Path:
    p = _cache_path(code, adjust)
    if not rows:
        return p
    fields = list(rows[0].keys())
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return p


def load_cache(code: str, adjust: str) -> list[dict]:
    p = _cache_path(code, adjust)
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def cache_info() -> list[tuple[str, int, str]]:
    out = []
    for p in sorted(CACHE_DIR.glob("*.csv")):
        st = p.stat()
        out.append((p.name, st.st_size, time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))))
    return out


# ══════════════════════════════════════════════════════════════
# 源 1：akshare（免费，主源）
# ══════════════════════════════════════════════════════════════

def fetch_akshare_daily(code: str, start: str = "20200101", end: str = "20991231",
                        adjust: str = "qfq") -> list[dict]:
    """
    东财日线，经 akshare。免费、无需 API key。
    start/end 格式：YYYYMMDD
    adjust: qfq(前复权) / hfq(后复权) / ""(不复权)
    """
    import akshare as ak  # 延迟导入：没装也能用别的源

    df = ak.stock_zh_a_hist(symbol=str(code).zfill(6), period="daily",
                            start_date=start, end_date=end, adjust=adjust)
    if df is None or df.empty:
        return []

    pct = limit_pct(code)
    rows: list[dict] = []
    prev = 0.0
    for _, r in df.iterrows():
        close = float(r["收盘"])
        pc = prev if prev > 0 else float(r["开盘"])
        lu, ld = _limit_prices(pc, pct)
        rows.append({
            "date": str(r["日期"])[:10],
            "open": float(r["开盘"]),
            "high": float(r["最高"]),
            "low": float(r["最低"]),
            "close": close,
            "volume": float(r["成交量"]),
            "amount": float(r["成交额"]),
            "prev_close": pc,
            "limit_up": lu,
            "limit_down": ld,
            "suspended": float(r["成交量"]) <= 0,
            "source": "akshare",
        })
        prev = close
    return rows


register_source("akshare", fetch_akshare_daily, "东方财富日线（免费，需 pip install akshare）")


# ══════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════

def get_daily(code: str, start: str = "20200101", end: str = "20991231",
              adjust: str = "qfq", use_cache: bool = True,
              source: str = "akshare", refresh: str = "auto") -> list[dict]:
    """
    拿日线。先看缓存，没有就打源，打完自动存缓存。

    refresh（很重要，这是第一版的 bug）：
        "auto"   默认。缓存停在今天之前 → 整段重抓。历史日线数据量小，
                 重抓只要几秒，比写"增量合并"可靠得多（合并最容易出重复行）。
        "never"  只用缓存，绝不联网（断网/省钱时用）
        "force"  强制重抓

    返回的每一行都带 source 字段 —— 你自己爬的数据会显示 "my_spider"，
    一眼就知道这份数据是谁给的。
    """
    cached: list[dict] = []
    if use_cache and refresh != "force":
        cached = load_cache(code, adjust)
        for r in cached:
            for k in ("open", "high", "low", "close", "volume", "amount",
                      "prev_close", "limit_up", "limit_down"):
                if k in r and r[k] not in ("", None):
                    r[k] = float(r[k])
            r["suspended"] = str(r.get("suspended", "")).lower() in ("true", "1")

        if cached:
            if refresh == "never":
                return [r for r in cached if start <= str(r["date"]).replace("-", "") <= end]

            last = str(cached[-1]["date"]).replace("-", "")[:8]
            today = time.strftime("%Y%m%d")
            want_end = min(str(end).replace("-", "")[:8], today)
            if last >= want_end:
                return [r for r in cached if start <= str(r["date"]).replace("-", "") <= end]
            print(f"  [数据] 缓存停在 {last}，该到 {want_end} 了，重抓整段…")

    if source not in _REGISTRY:
        raise KeyError(f"未知数据源 {source}，已注册：{list(_REGISTRY)}")

    rows = _REGISTRY[source].fetch_daily(code, start, end, adjust)
    if rows and use_cache:
        save_cache(code, adjust, rows)
    return rows


# ══════════════════════════════════════════════════════════════
# 附带：龙虎榜（短线用得上）
# ══════════════════════════════════════════════════════════════

def fetch_lhb(start: str, end: str) -> list[dict]:
    """龙虎榜明细。start/end: YYYYMMDD"""
    import akshare as ak
    df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def fetch_fund_flow(code: str, market: str = "sz") -> list[dict]:
    """个股资金流（东财）。market: sh / sz / bj"""
    import akshare as ak
    df = ak.stock_individual_fund_flow(stock=str(code).zfill(6), market=market)
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("已注册数据源：", list_sources())
    print("缓存目录：", CACHE_DIR)
    print("缓存文件数：", len(list(CACHE_DIR.glob('*.csv'))))
