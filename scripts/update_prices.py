"""Fetch last closing prices and write prices.json for the site ticker.

Run daily by .github/workflows/update-prices.yml after the US market close.
Primary source: Yahoo Finance (via yfinance). Fallback: Stooq daily CSV.
If a symbol fails on both, its previous entry in prices.json is kept so a
transient outage never empties the ticker.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

SYMBOLS = ["NVDA", "VOO", "MSFT", "SMH", "GOOGL", "QQQ", "AMZN", "AMD"]
OUT = Path(__file__).resolve().parent.parent / "prices.json"
NY = ZoneInfo("America/New_York")


def _drop_unfinished_session(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """If run during US trading hours, the last row is an intraday print, not a close."""
    now = datetime.now(NY)
    if rows and rows[-1][0] == now.date().isoformat() and now.time() < time(16, 15):
        return rows[:-1]
    return rows


def from_yahoo(symbol: str) -> list[tuple[str, float]]:
    import yfinance as yf

    df = yf.Ticker(symbol).history(period="10d", interval="1d", auto_adjust=False)
    df = df.dropna(subset=["Close"])
    return [(idx.date().isoformat(), float(c)) for idx, c in df["Close"].items()]


def from_stooq(symbol: str) -> list[tuple[str, float]]:
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    with urllib.request.urlopen(url, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append((row["Date"], float(row["Close"])))
        except (KeyError, ValueError, TypeError):
            continue
    return rows[-10:]


def quote(symbol: str) -> dict | None:
    for source in (from_yahoo, from_stooq):
        try:
            rows = _drop_unfinished_session(source(symbol))
        except Exception as e:  # network/parse errors -> try next source
            print(f"{symbol}: {source.__name__} failed: {e}", file=sys.stderr)
            continue
        if len(rows) >= 2:
            (_, prev), (date, close) = rows[-2], rows[-1]
            return {
                "symbol": symbol,
                "date": date,
                "close": round(close, 2),
                "prev_close": round(prev, 2),
                "change_pct": round((close / prev - 1) * 100, 2),
            }
    return None


def main() -> int:
    old = {}
    if OUT.exists():
        try:
            old = {q["symbol"]: q for q in json.loads(OUT.read_text()).get("quotes", [])}
        except Exception:
            pass

    quotes = []
    for sym in SYMBOLS:
        q = quote(sym) or old.get(sym)
        if q:
            quotes.append(q)
        else:
            print(f"{sym}: no data and no previous value; skipped", file=sys.stderr)

    if not quotes:
        print("No quotes at all; leaving prices.json unchanged.", file=sys.stderr)
        return 1

    as_of = max(q["date"] for q in quotes)
    OUT.write_text(json.dumps({"as_of": as_of, "quotes": quotes}, indent=2) + "\n")
    print(f"Wrote {len(quotes)} quotes as of {as_of}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
