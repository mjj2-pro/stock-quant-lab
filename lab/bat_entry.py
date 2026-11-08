"""
双击入口的脑子 —— bat 只负责启动，中文全在这里输出
====================================================================
为什么这样拆：
    Windows 的 cmd 按系统代码页（GBK）读 .bat 文件，
    如果 bat 本身是 UTF-8 存的，中文会变成乱码，甚至把 echo 行当成命令执行。
    所以 bat 里一个中文字都不能有（纯 ASCII），所有提示文字搬到这里来。

用法（由 bat 调用）：
    python lab\\bat_entry.py backtest
    python lab\\bat_entry.py selftest
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BAR = "=" * 60
THIN = "-" * 60


def _utf8_stdout() -> None:
    """保证中文和符号在任何控制台都能打出来，不因 GBK 编码炸掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def backtest_flow() -> int:
    print(BAR)
    print("    考  场  ·  回 测  验 证")
    print(BAR)
    print()
    print("  把一只股票的历史数据跑一遍，看某个「想法」到底赚不赚钱。")
    print("  算上了：涨停买不进、跌停卖不出、T+1、佣金、印花税、滑点。")
    print()
    print(THIN)

    try:
        code = input("  股票代码（直接回车 = 000001 平安银行）: ").strip()
    except EOFError:
        code = ""
    code = code or "000001"

    try:
        start = input("  起始日期 YYYYMMDD（直接回车 = 20240101）: ").strip()
    except EOFError:
        start = ""
    start = start or "20240101"

    if not (code.isdigit() and len(code) == 6):
        print(f"\n  !! 「{code}」看起来不是 6 位数字代码，已按原样尝试。")

    print()
    print("  正在跑。第一次会联网抓数据（几秒到半分钟），之后走本地缓存。")
    print(THIN)
    print()

    # 交给真正的入口
    sys.argv = ["run_smoke.py", code, start]
    from lab.run_smoke import main as run_main
    rc = run_main()

    print()
    print(THIN)
    try:
        from lab.report.html_report import OUT_DIR
        print("  成绩单网页在：")
        print(f"    {OUT_DIR}")
        print("  没自动弹出来的话，去这个文件夹双击最新的 .html")
    except Exception:  # noqa: BLE001
        pass
    print(THIN)
    return rc


def selftest_flow() -> int:
    print(BAR)
    print("    考 场 自 检  ·  证 明 它 不 作 弊")
    print(BAR)
    print()
    print("  这里跑 10 项检查。任何一项不过，回测出来的胜率都不能信。")
    print()
    print("  最关键的是第 9 项：让「瞎买」跑 24 组随机行情，")
    print("  扣完成本必须亏钱 —— 如果它让你赚，说明考场有漏洞。")
    print()
    print(THIN)

    from lab.backtest.selftest import main as st_main
    rc = st_main()

    print(THIN)
    if rc == 0:
        print("  结果：考场干净，可以往里请老师。")
    else:
        print("  结果：考场有问题！修好之前，不要相信任何回测数字。")
    return rc


def main() -> int:
    _utf8_stdout()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if cmd == "selftest":
        return selftest_flow()
    return backtest_flow()


if __name__ == "__main__":
    raise SystemExit(main())
