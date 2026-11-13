"""Bounded Yahoo chart transport; no cookie/quote-summary bootstrap or invented data."""
import json
import math
import ssl
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import certifi
import pandas as pd


def fetch_chart_history(symbol, *, period="1mo", interval="1d", timeout=6,
                        auto_adjust=False, actions=True, **kwargs):
    if kwargs:
        raise ValueError("Unsupported direct chart parameters")
    query = urlencode({"range": period, "interval": interval,
                       "events": "div,splits", "includeAdjustedClose": "true"})
    request = Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?{query}",
        headers={"Accept": "application/json", "User-Agent": "StockRadar/1.0"},
    )
    with urlopen(request, timeout=min(max(float(timeout), 1), 8),
                 context=ssl.create_default_context(cafile=certifi.where())) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("Chart response exceeds size limit")
    payload = json.loads(body)
    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if chart.get("error") or len(results) != 1:
        raise ValueError("Chart result unavailable")
    result = results[0]
    meta = result.get("meta") or {}
    if str(meta.get("symbol", "")).upper() != symbol.upper():
        raise ValueError("Chart symbol does not match request")
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quotes = (indicators.get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    if not timestamps or len(timestamps) != len(closes):
        raise ValueError("Chart observations incomplete")
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose")
    columns = {"Close": closes}
    if adjusted is not None:
        if len(adjusted) != len(timestamps):
            raise ValueError("Adjusted observations incomplete")
        columns["Adj Close"] = adjusted
    frame = pd.DataFrame(columns, index=pd.to_datetime(timestamps, unit="s", utc=True))
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame[frame.index <= pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=5)]
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    frame = frame[frame["Close"].map(lambda value: pd.notna(value) and math.isfinite(value) and value > 0)]
    if frame.empty:
        raise ValueError("No validated chart prices")
    if auto_adjust:
        if "Adj Close" not in frame:
            raise ValueError("Adjusted history unavailable")
        frame["Close"] = frame["Adj Close"]
    if actions:
        frame["Dividends"] = 0.0
        for dividend in (result.get("events", {}).get("dividends") or {}).values():
            stamp = pd.to_datetime(dividend.get("date"), unit="s", utc=True)
            amount = dividend.get("amount")
            if stamp in frame.index and isinstance(amount, (int, float)) and math.isfinite(amount) and amount >= 0:
                frame.loc[stamp, "Dividends"] = amount
    frame.attrs.update(source="Yahoo Finance chart", currency=meta.get("currency", ""),
                       exchange_timezone=meta.get("exchangeTimezoneName", "UTC"),
                       provider_quote_at=meta.get("regularMarketTime"), retrieved_at=time.time())
    return frame
