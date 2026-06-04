from flask import Flask, render_template_string, redirect, url_for, request, session
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
import csv
import json
import os
import ssl

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    import stripe
except ImportError:
    stripe = None

app = Flask(__name__)
app.secret_key = os.environ.get("SIGNALSCOPE_SECRET_KEY") or os.environ.get("SECRET_KEY", "signalscope-local-dev-secret-key-change-before-production")
OWNER_EMAIL = os.environ.get("SIGNALSCOPE_OWNER_EMAIL", "").strip().lower()
OWNER_PASSWORD = os.environ.get("SIGNALSCOPE_OWNER_PASSWORD", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_SUCCESS_URL = os.environ.get(
    "STRIPE_SUCCESS_URL",
    "https://stockradar-ai-1-0v3g.onrender.com/checkout-success"
)
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
LAST_NEWS_FETCH_STATUS = {
    "provider": "none",
    "status": "not_started",
    "errors": [],
}


# --- Helper for fetching JSON from URL with fallback for local SSL certificate errors ---
def fetch_url_json(url, timeout=8):
    request_obj = Request(url, headers={"User-Agent": "StockRadarAI/1.0"})

    try:
        with urlopen(request_obj, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        error_text = str(exc)
        reason = getattr(exc, "reason", None)
        reason_text = str(reason) if reason else ""

        if (
            isinstance(exc, ssl.SSLCertVerificationError)
            or isinstance(exc, URLError) and "CERTIFICATE_VERIFY_FAILED" in reason_text
            or "CERTIFICATE_VERIFY_FAILED" in error_text
            or "certificate verify failed" in error_text.lower()
        ):
            local_dev_context = ssl._create_unverified_context()
            with urlopen(request_obj, timeout=timeout, context=local_dev_context) as response:
                return json.loads(response.read().decode("utf-8"))

        raise
STRIPE_CANCEL_URL = os.environ.get(
    "STRIPE_CANCEL_URL",
    "https://stockradar-ai-1-0v3g.onrender.com/upgrade"
)

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

def stripe_checkout_configured():
    return bool(stripe and STRIPE_SECRET_KEY and STRIPE_PRICE_ID)

def owner_has_access():
    return session.get("owner_logged_in") is True


def owner_login_configured():
    return bool(OWNER_EMAIL and OWNER_PASSWORD)


CHART_RANGES = {
    "1h": {"label": "1 hour", "period": "1d", "interval": "5m"},
    "24h": {"label": "24 hours", "period": "1d", "interval": "15m"},
    "1mo": {"label": "1 month", "period": "1mo", "interval": "1d"},
    "6mo": {"label": "6 months", "period": "6mo", "interval": "1d"},
    "1y": {"label": "1 year", "period": "1y", "interval": "1d"},
    "ytd": {"label": "YTD", "period": "ytd", "interval": "1d"},
}


DEFAULT_RECOMMENDATIONS = [
    {"ticker": "AAPL", "signal": "HOLD", "confidence": "65%", "reason": "Momentum is stable, but conviction has not crossed the BUY threshold."},
    {"ticker": "MSFT", "signal": "BUY", "confidence": "82%", "reason": "Strong quality profile and positive AI momentum setup."},
    {"ticker": "NVDA", "signal": "BUY", "confidence": "88%", "reason": "High conviction technology momentum with strong scanner strength."},
    {"ticker": "TSLA", "signal": "HOLD", "confidence": "61%", "reason": "Volatility remains high, but the scanner is not yet flagging a decisive move."},
    {"ticker": "AMZN", "signal": "BUY", "confidence": "79%", "reason": "Positive trend and improving AI signal profile."},
    {"ticker": "GOOGL", "signal": "HOLD", "confidence": "67%", "reason": "Balanced setup with no major downside warning in the current scanner."},
    {"ticker": "META", "signal": "BUY", "confidence": "81%", "reason": "Strong relative momentum and improving conviction score."},
    {"ticker": "NFLX", "signal": "HOLD", "confidence": "58%", "reason": "Mixed short-term trend. Worth monitoring for a stronger signal."},
    {"ticker": "AMD", "signal": "BUY", "confidence": "76%", "reason": "AI semiconductor exposure gives positive momentum, but conviction is not yet extreme."},
    {"ticker": "INTC", "signal": "SELL", "confidence": "64%", "reason": "Scanner is flagging weaker momentum versus stronger chip peers."},
    {"ticker": "SMH", "signal": "BUY", "confidence": "80%", "reason": "Semiconductor ETF remains a high-interest AI-market proxy."},
    {"ticker": "SPY", "signal": "HOLD", "confidence": "60%", "reason": "Broad market trend is balanced. Watch for stronger index confirmation."},
    {"ticker": "QQQ", "signal": "BUY", "confidence": "75%", "reason": "Growth and technology exposure keep momentum positive."},
    {"ticker": "BP.L", "signal": "HOLD", "confidence": "55%", "reason": "Energy exposure is sensitive to oil and geopolitical headlines."},
    {"ticker": "HSBA.L", "signal": "HOLD", "confidence": "57%", "reason": "Banking exposure is balanced with no strong directional signal."},
    {"ticker": "AZN.L", "signal": "BUY", "confidence": "72%", "reason": "Defensive healthcare profile with positive quality characteristics."},
    {"ticker": "SHEL.L", "signal": "HOLD", "confidence": "59%", "reason": "Energy trend needs stronger confirmation before a BUY signal."},
    {"ticker": "RR.L", "signal": "BUY", "confidence": "78%", "reason": "Positive UK industrial momentum and strong watchlist interest."},
]

CSV_CANDIDATES = [
    "ai_recommendations.csv",
    "recommendations.csv",
    "stock_recommendations.csv",
    "signals.csv",
]

TRACKED_STOCK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "ORCL", "CRM", "ADBE", "INTC", "CSCO", "QCOM", "IBM", "NOW", "SHOP", "UBER",
    "JPM", "BAC", "GS", "MS", "WFC", "C", "V", "MA", "AXP", "PYPL",
    "XOM", "CVX", "COP", "SLB", "OXY", "BP.L", "SHEL.L", "HSBA.L", "LLOY.L", "BARC.L",
    "AZN.L", "GSK.L", "ULVR.L", "DGE.L", "RIO.L", "BHP.L", "VOD.L", "BT-A.L", "TSCO.L", "SBRY.L",
    "LLY", "JNJ", "PFE", "MRK", "ABBV", "UNH", "TMO", "ABT", "NVO", "ISRG",
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS", "KO", "PEP", "PG",
    "BA", "CAT", "GE", "DE", "LMT", "RTX", "NOC", "HON", "UPS", "FDX",
    "SPY", "QQQ", "DIA", "IWM", "SMH", "GLD", "SLV", "USO", "TLT", "HYG",
    "^GSPC", "^IXIC", "^DJI", "^RUT", "^FTSE", "^N225", "^HSI", "BTC-USD", "ETH-USD", "SOL-USD",
]


SECTOR_MAP = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Semiconductors", "AMZN": "Consumer / Cloud",
    "GOOGL": "Technology", "META": "Technology", "TSLA": "EV / Growth", "AVGO": "Semiconductors",
    "AMD": "Semiconductors", "NFLX": "Media", "JPM": "Banks", "BAC": "Banks", "GS": "Banks",
    "MS": "Banks", "WFC": "Banks", "C": "Banks", "V": "Payments", "MA": "Payments",
    "AXP": "Payments", "PYPL": "Payments", "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "SLB": "Energy", "OXY": "Energy", "BP.L": "UK Energy", "SHEL.L": "UK Energy",
    "HSBA.L": "UK Banks", "LLOY.L": "UK Banks", "BARC.L": "UK Banks", "AZN.L": "UK Healthcare",
    "GSK.L": "UK Healthcare", "ULVR.L": "UK Consumer", "DGE.L": "UK Consumer",
    "RIO.L": "UK Materials", "BHP.L": "UK Materials", "VOD.L": "UK Telecoms",
    "BT-A.L": "UK Telecoms", "TSCO.L": "UK Retail", "SBRY.L": "UK Retail",
    "LLY": "Healthcare", "JNJ": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare",
    "ABBV": "Healthcare", "UNH": "Healthcare", "TMO": "Healthcare", "ABT": "Healthcare",
    "NVO": "Healthcare", "ISRG": "Healthcare", "SPY": "ETF", "QQQ": "ETF", "DIA": "ETF",
    "IWM": "ETF", "SMH": "ETF", "GLD": "Commodity ETF", "SLV": "Commodity ETF",
    "USO": "Commodity ETF", "TLT": "Bond ETF", "HYG": "Bond ETF",
}


def generated_signal_for_ticker(ticker, index):
    if ticker in {"NVDA", "MSFT", "AAPL", "AVGO", "LLY", "SPY", "QQQ", "SMH", "COST", "AMZN", "GOOGL", "META"}:
        return "BUY", "82%"
    if ticker in {"TSLA", "PYPL", "INTC", "VOD.L", "BT-A.L", "USO", "SLV", "BTC-USD"}:
        return "SELL", "44%"
    if index % 9 == 0:
        return "BUY", "76%"
    if index % 11 == 0:
        return "SELL", "42%"
    return "HOLD", "58%"


def expand_recommendations(rows):
    output = []
    seen = set()

    for row in rows:
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker or ticker in seen:
            continue

        output.append({
            "ticker": ticker,
            "signal": clean_signal(row.get("signal"), row.get("confidence")),
            "confidence": normalise_confidence(row.get("confidence")),
            "reason": str(row.get("reason") or "AI scanner output is available for this ticker.").strip(),
            "sector": row.get("sector") or SECTOR_MAP.get(ticker, "AI Watchlist"),
        })
        seen.add(ticker)

    for index, ticker in enumerate(TRACKED_STOCK_UNIVERSE):
        if ticker in seen:
            continue

        signal, confidence = generated_signal_for_ticker(ticker, index)
        output.append({
            "ticker": ticker,
            "signal": signal,
            "confidence": confidence,
            "reason": "Included in the 100-stock StockRadar universe. This keeps the live dashboard complete until the full scanner CSV/API feed is connected.",
            "sector": SECTOR_MAP.get(ticker, "AI Watchlist"),
        })
        seen.add(ticker)

        if len(output) >= 100:
            break

    return output[:100]


def clean_signal(value, confidence=None):
    text = str(value or "").strip().upper()

    buy_words = {"BUY", "STRONG BUY", "BULLISH", "LONG", "ACCUMULATE", "OUTPERFORM"}
    sell_words = {"SELL", "STRONG SELL", "BEARISH", "SHORT", "REDUCE", "UNDERPERFORM"}
    hold_words = {"HOLD", "NEUTRAL", "WATCH", "WATCHLIST", "WAIT"}

    if text in buy_words:
        return "BUY"
    if text in sell_words:
        return "SELL"
    if text in hold_words:
        return "HOLD"

    if "BUY" in text or "BULL" in text or "OUTPERFORM" in text:
        return "BUY"
    if "SELL" in text or "BEAR" in text or "UNDERPERFORM" in text:
        return "SELL"
    if "HOLD" in text or "NEUTRAL" in text or "WATCH" in text:
        return "HOLD"

    score = confidence_number(confidence) if confidence is not None else 0
    if score >= 72:
        return "BUY"
    if score <= 45 and score > 0:
        return "SELL"
    return "HOLD"

def normalise_confidence(value):
    text = str(value or "50%").strip()
    if not text:
        return "50%"
    if text.endswith("%"):
        return text
    try:
        return f"{float(text):.0f}%"
    except Exception:
        return text


def get_recommendations():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    for filename in CSV_CANDIDATES:
        path = os.path.join(base_dir, filename)

        if not os.path.exists(path):
            continue

        try:
            rows = []

            with open(path, newline="", encoding="utf-8-sig") as csvfile:
                reader = csv.DictReader(csvfile)

                for row in reader:
                    lower = {str(k).strip().lower(): v for k, v in row.items()}

                    ticker = lower.get("ticker") or lower.get("symbol") or lower.get("stock") or lower.get("name")
                    signal = lower.get("signal") or lower.get("recommendation") or lower.get("rating") or "HOLD"
                    confidence = lower.get("confidence") or lower.get("conviction") or lower.get("score") or "50%"
                    reason = lower.get("reason") or lower.get("ai reason") or lower.get("insight") or lower.get("notes") or "AI scanner output is available for this ticker."

                    if ticker:
                        rows.append({
                            "ticker": str(ticker).strip().upper(),
                            "signal": clean_signal(signal, confidence),
                            "confidence": normalise_confidence(confidence),
                            "reason": str(reason).strip(),
                        })

            if rows:
                return expand_recommendations(rows)
        except Exception:
            continue

    return expand_recommendations(DEFAULT_RECOMMENDATIONS)

def confidence_number(value):
    text = str(value).replace("%", "").strip()

    try:
        return float(text)
    except Exception:
        return 0


def confidence_meter(value):
    number = confidence_number(value)

    if number <= 0:
        return "░░░░░░░░░░"

    filled_blocks = round(number / 10)
    filled_blocks = max(0, min(10, filled_blocks))
    return "█" * filled_blocks + "░" * (10 - filled_blocks)


def signal_strength_label(value):
    number = confidence_number(value)

    if number >= 80:
        return "Strong"
    if number >= 60:
        return "Moderate"
    if number > 0:
        return "Early"

    return "Watchlist"


def split_rows(recommendations):
    buy_rows = [r for r in recommendations if r["signal"] == "BUY"][:20]
    hold_rows = [r for r in recommendations if r["signal"] == "HOLD"][:20]
    sell_rows = [r for r in recommendations if r["signal"] == "SELL"][:20]
    conviction_rows = sorted(recommendations, key=lambda r: confidence_number(r["confidence"]), reverse=True)[:8]
    return buy_rows, hold_rows, sell_rows, conviction_rows


def calculate_counts(recommendations):
    buy_count = sum(1 for r in recommendations if r["signal"] == "BUY")
    hold_count = sum(1 for r in recommendations if r["signal"] == "HOLD")
    sell_count = sum(1 for r in recommendations if r["signal"] == "SELL")
    high_conviction_count = sum(1 for r in recommendations if confidence_number(r["confidence"]) >= 80)
    return buy_count, hold_count, sell_count, high_conviction_count


def get_stock_ai_context(symbol):
    recommendations = get_recommendations()
    cleaned_symbol = symbol.strip().upper()

    matching_item = None

    for item in recommendations:
        if item["ticker"].strip().upper() == cleaned_symbol:
            matching_item = item
            break

    if matching_item is None:
        matching_item = {
            "ticker": cleaned_symbol,
            "signal": "WATCH",
            "confidence": "50%",
            "reason": "This ticker is not currently inside the AI recommendation table, so StockRadar marks it as WATCH and gives it a balanced preview score until stronger scanner data is available.",
        }

    confidence_value = confidence_number(matching_item["confidence"])
    signal = matching_item["signal"]

    if signal == "BUY" and confidence_value >= 80:
        momentum_view = "Strong upside setup"
        risk_view = "Medium risk — strong signals still need confirmation from price action."
        watch_next = "Watch whether the stock keeps holding momentum and whether confidence remains above 80%."
    elif signal == "BUY":
        momentum_view = "Positive setup building"
        risk_view = "Medium risk — signal is positive but not yet at high-conviction strength."
        watch_next = "Watch for confidence moving above 80% or price breaking higher on stronger volume."
    elif signal == "SELL":
        momentum_view = "Weak or defensive setup"
        risk_view = "Higher risk — the AI scanner is flagging downside pressure or weaker quality."
        watch_next = "Watch whether the stock stabilises or continues making lower highs."
    elif signal == "HOLD":
        momentum_view = "Neutral setup"
        risk_view = "Balanced risk — not enough evidence for a strong BUY or SELL view."
        watch_next = "Watch for a move above resistance, a confidence upgrade, or a signal change."
    else:
        momentum_view = "Watchlist setup"
        risk_view = "Balanced risk — this ticker has live data, but it is not currently ranked as a high-conviction BUY or SELL in the AI recommendation table."
        watch_next = "Watch whether the stock earns a stronger AI recommendation, moves above key chart levels, or reaches confidence above 80%."

    return {
        "ticker": matching_item["ticker"],
        "signal": signal,
        "confidence": matching_item["confidence"],
        "confidence_meter": confidence_meter(matching_item["confidence"]),
        "strength_label": signal_strength_label(matching_item["confidence"]),
        "reason": matching_item["reason"],
        "momentum_view": momentum_view,
        "risk_view": risk_view,
        "watch_next": watch_next,
    }


def classify_portfolio_role(symbol):
    cleaned_symbol = str(symbol or "").strip().upper()
    sector = SECTOR_MAP.get(cleaned_symbol, "").lower()

    core_etfs = {"SPY", "QQQ", "DIA", "IWM", "SMH", "GLD", "SLV", "USO", "TLT", "HYG", "VUSA", "VUAG", "VWRP", "VWRL"}
    index_symbols = {"^GSPC", "^IXIC", "^DJI", "^RUT", "^FTSE", "^N225", "^HSI"}
    crypto_symbols = {"BTC-USD", "ETH-USD", "SOL-USD"}

    if cleaned_symbol in core_etfs or "etf" in sector:
        return {
            "key": "core_etf",
            "label": "Core ETF / market exposure",
            "decision_use": "Use as broad exposure or a portfolio building block before taking larger single-stock risk.",
            "concentration_note": "Check the fund or index exposure so you do not accidentally double up on the same mega-cap, sector or theme.",
        }

    if cleaned_symbol in index_symbols:
        return {
            "key": "index",
            "label": "Market index benchmark",
            "decision_use": "Use as a benchmark for market direction, not as a direct single-company position.",
            "concentration_note": "Index signals help read the market backdrop before adding individual stock risk.",
        }

    if cleaned_symbol in crypto_symbols:
        return {
            "key": "growth",
            "label": "High-volatility satellite",
            "decision_use": "Use only as a controlled satellite allocation because price moves can be extreme.",
            "concentration_note": "Crypto exposure can dominate portfolio volatility even when position size looks small.",
        }

    if any(word in sector for word in ["semiconductor", "technology", "cloud", "ev", "growth", "media", "software"]):
        return {
            "key": "growth",
            "label": "Growth / technology satellite",
            "decision_use": "Use as a growth research candidate, but keep position size and overlap with other tech names under control.",
            "concentration_note": "This may increase technology, AI, platform or high-growth exposure if you already own similar names or Nasdaq-heavy ETFs.",
        }

    if any(word in sector for word in ["payments", "consumer / cloud", "retail", "consumer"]):
        return {
            "key": "quality",
            "label": "Quality compounder / consumer strength",
            "decision_use": "Use as a quality business research candidate where brand strength, scale, cash flow or platform durability matter.",
            "concentration_note": "Quality names can still become expensive, so check valuation and whether you already own similar mega-cap exposure.",
        }

    if any(word in sector for word in ["healthcare", "uk healthcare"]):
        return {
            "key": "defensive",
            "label": "Defensive healthcare balance",
            "decision_use": "Use to add defensive earnings exposure or healthcare diversification.",
            "concentration_note": "Healthcare can reduce growth-only dependence, but individual drug, regulation and valuation risks still matter.",
        }

    if any(word in sector for word in ["banks", "bank"]):
        return {
            "key": "cyclical",
            "label": "Financial / cyclical exposure",
            "decision_use": "Use as a cyclical or income-sensitive research candidate linked to rates, credit quality and economic conditions.",
            "concentration_note": "Bank exposure can cluster around the same macro risks: rates, defaults, lending demand and market stress.",
        }

    if any(word in sector for word in ["energy", "commodity", "materials"]):
        return {
            "key": "cyclical",
            "label": "Energy / commodity cyclical",
            "decision_use": "Use as cyclical exposure that may behave differently from technology and consumer growth names.",
            "concentration_note": "Energy, commodities and materials can be driven by macro, geopolitics and commodity prices rather than company quality alone.",
        }

    if any(word in sector for word in ["industrial", "aerospace", "defence", "defense"]):
        return {
            "key": "industrial",
            "label": "Industrial / defence compounder",
            "decision_use": "Use as industrial, infrastructure or defence-linked exposure when the business quality and order book support the thesis.",
            "concentration_note": "Industrial and defence names can diversify away from pure technology, but still carry cycle, contract and political risk.",
        }

    if any(word in sector for word in ["telecom", "uk telecom"]):
        return {
            "key": "defensive",
            "label": "Telecom / defensive income candidate",
            "decision_use": "Use as defensive or income-style exposure only after checking debt, growth and dividend sustainability.",
            "concentration_note": "Telecom holdings can look defensive but may be slow-growth and debt-sensitive.",
        }

    return {
        "key": "research",
        "label": "Research candidate",
        "decision_use": "Use the signal as a research prompt, then check business quality, risk, valuation and portfolio fit.",
        "concentration_note": "Check whether this duplicates a sector, theme or risk you already own.",
    }

def get_premium_report(symbol, ai_context):
    cleaned_symbol = symbol.strip().upper()
    signal = ai_context.get("signal", "HOLD")
    confidence_value = confidence_number(ai_context.get("confidence", "0%"))
    role_profile = classify_portfolio_role(cleaned_symbol)

    portfolio_role = role_profile["label"]
    decision_use = role_profile["decision_use"]
    concentration_note = role_profile["concentration_note"]

    if signal == "BUY" and confidence_value >= 80:
        readiness = "Strong research candidate"
        action_frame = "Research further before buying; the signal is strong, but still needs risk and portfolio-fit checks."
    elif signal == "BUY":
        readiness = "Positive but not automatic"
        action_frame = "Worth researching, but wait for stronger evidence if risk or valuation feels stretched."
    elif signal == "SELL":
        readiness = "Caution zone"
        action_frame = "Avoid rushing in. Understand why the scanner is flagging weakness before considering exposure."
    else:
        readiness = "Watch and learn"
        action_frame = "Keep on the watchlist until the signal, confidence or thesis becomes clearer."

    checklist = [
        "Do I understand how this business, fund, index or asset makes money or moves?",
        "Does this fit my time horizon and risk tolerance?",
        "Am I already exposed to the same sector, ETF, theme or mega-cap names?",
        "What would make this investment thesis wrong?",
        "Would I still be comfortable holding this if it fell sharply in the short term?",
    ]


    return {
        "headline": f"{cleaned_symbol} Premium Decision Panel",
        "summary": "Premium view: signal strength, portfolio role, risk fit and what to check before acting.",
        "confidence": ai_context["confidence"],
        "meter": confidence_meter(ai_context["confidence"]),
        "strength": signal_strength_label(ai_context["confidence"]),
        "risk": ai_context["risk_view"],
        "next_move": ai_context["watch_next"],
        "pro_angle": "Premium turns the signal into a structured decision check, not a blind buy/sell instruction.",
        "portfolio_role": portfolio_role,
        "decision_use": decision_use,
        "concentration_note": concentration_note,
        "readiness": readiness,
        "action_frame": action_frame,
        "checklist": checklist,
    }

@app.route("/premium-decision/<symbol>")
def premium_decision(symbol):
    cleaned_symbol = symbol.strip().upper()
    ai_context = get_stock_ai_context(cleaned_symbol)
    report = get_premium_report(cleaned_symbol, ai_context)

    if not owner_has_access():
        locked_html = """
        <!DOCTYPE html>
        <html>
        <head>
        <title>Premium Decision Panel — StockRadar</title>
        <style>
        body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
        .wrap{max-width:920px;margin:0 auto;}
        .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:34px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
        .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
        h1{font-size:42px;line-height:1.05;margin:0 0 16px 0;letter-spacing:-0.04em;}
        p{color:#cbd5e1;line-height:1.7;}
        a{color:#38bdf8;font-weight:900;text-decoration:none;}
        .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:12px;}
        .locked{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}
        </style>
        </head>
        <body>
        <div class="wrap">
            <a href="/stock/{{ symbol }}">← Back to {{ symbol }}</a>
            <div class="card">
                <p class="kicker">Premium Decision Layer</p>
                <h1>{{ symbol }} Decision Panel</h1>
                <p>This panel turns a stock signal into a structured decision check: portfolio role, concentration risk, readiness, and what to watch before acting.</p>
                <div class="locked"><strong>Locked:</strong> Upgrade to unlock the full Premium Decision Panel for {{ symbol }}.</div>
                <a class="button" href="/upgrade">Unlock Premium</a>
            </div>
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html, symbol=cleaned_symbol)

    panel_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <title>{{ report.headline }} — StockRadar</title>
    <style>
    body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
    .wrap{max-width:1120px;margin:0 auto;}
    .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:32px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px;}
    .box{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;line-height:1.6;}
    .box strong{display:block;color:white;font-size:18px;margin-bottom:6px;}
    .box span,p,li{color:#cbd5e1;line-height:1.7;}
    .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
    h1{font-size:44px;line-height:1.04;margin:0 0 16px 0;letter-spacing:-0.04em;}
    h2{margin:0 0 12px 0;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    .meter{font-family:monospace;color:#00ffaa;font-size:20px;letter-spacing:2px;}
    .note{background:rgba(0,255,170,0.09);border:1px solid rgba(0,255,170,0.18);border-radius:20px;padding:18px;color:#d1fae5;line-height:1.7;}
    .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:14px;}
    @media(max-width:900px){body{padding:24px;}.grid{grid-template-columns:1fr;}h1{font-size:34px;}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a href="/stock/{{ symbol }}">← Back to {{ symbol }}</a>

        <div class="card">
            <p class="kicker">Premium Decision Layer</p>
            <h1>{{ report.headline }}</h1>
            <p>{{ report.summary }}</p>

            <div class="grid">
                <div class="box">
                    <strong>Signal</strong>
                    <span>{{ context.signal }} • {{ report.confidence }}</span>
                    <div class="meter">{{ report.meter }}</div>
                </div>

                <div class="box">
                    <strong>Portfolio role</strong>
                    <span>{{ report.portfolio_role }}</span>
                </div>

                <div class="box">
                    <strong>Decision readiness</strong>
                    <span>{{ report.readiness }}</span>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>Decision use</h2>
            <p>{{ report.decision_use }}</p>
            <div class="note">{{ report.action_frame }}</div>
        </div>

        <div class="card">
            <h2>Risk and concentration check</h2>
            <p>{{ report.risk }}</p>
            <p>{{ report.concentration_note }}</p>
        </div>

        <div class="card">
            <h2>Portfolio fit check</h2>
            <p>Already own other stocks or ETFs? Use the Portfolio Fit Checker before increasing position size or adding a similar theme.</p>
            <a class="button" href="/portfolio-fit">Check Portfolio Fit</a>
        </div>

        <div class="card">
            <h2>Before acting, check this</h2>
            <ul>
                {% for item in report.checklist %}
                <li>{{ item }}</li>
                {% endfor %}
            </ul>
        </div>

        <div class="card">
            <h2>Watch next</h2>
            <p>{{ report.next_move }}</p>
            <div class="note">{{ report.pro_angle }}</div>
        </div>
    </div>
    </body>
    </html>
    """

    return render_template_string(
        panel_html,
        symbol=cleaned_symbol,
        context=ai_context,
        report=report,
    )


@app.route("/premium-watchlist")
def premium_watchlist():
    recommendations = get_recommendations()
    buy_rows, hold_rows, sell_rows, conviction_rows = split_rows(recommendations)

    strongest = conviction_rows[0] if conviction_rows else None
    highest_risk = sell_rows[0] if sell_rows else None
    quality_names = [item for item in recommendations if item["ticker"] in {"MSFT", "AAPL", "GOOGL", "AMZN", "META", "V", "MA", "COST"}]
    defensive_names = [item for item in recommendations if item["ticker"] in {"KO", "MCD", "JNJ", "PG", "PEP", "WMT", "AZN.L", "GSK.L"}]
    growth_names = [item for item in recommendations if item["ticker"] in {"NVDA", "AMD", "TSLA", "SMH", "QQQ", "BTC-USD", "ETH-USD", "SOL-USD"}]

    theme_counts = {
        "Quality compounders": len(quality_names),
        "Growth / AI satellites": len(growth_names),
        "Defensive balance": len(defensive_names),
        "Current BUY signals": len(buy_rows),
        "Current SELL warnings": len(sell_rows),
    }

    if not owner_has_access():
        locked_html = """
        <!DOCTYPE html>
        <html>
        <head>
        <title>Premium Watchlist Intelligence — StockRadar</title>
        <style>
        body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
        .wrap{max-width:920px;margin:0 auto;}
        .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:34px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
        .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
        h1{font-size:42px;line-height:1.05;margin:0 0 16px 0;letter-spacing:-0.04em;}
        p,li{color:#cbd5e1;line-height:1.7;}
        a{color:#38bdf8;font-weight:900;text-decoration:none;}
        .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:12px;}
        .locked{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}
        </style>
        </head>
        <body>
        <div class="wrap">
            <a href="/">← Back to dashboard</a>
            <div class="card">
                <p class="kicker">Premium Watchlist Intelligence</p>
                <h1>Turn a list of stocks into a decision review.</h1>
                <p>Premium Watchlist Intelligence highlights strongest signals, caution names, portfolio roles and theme concentration.</p>
                <ul>
                    <li>Strongest current signal</li>
                    <li>Highest caution stock</li>
                    <li>Quality, growth and defensive buckets</li>
                    <li>Theme concentration read</li>
                </ul>
                <div class="locked"><strong>Locked:</strong> Upgrade to unlock the full watchlist intelligence layer.</div>
                <a class="button" href="/upgrade">Unlock Premium</a>
            </div>
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html)

    watchlist_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <title>Premium Watchlist Intelligence — StockRadar</title>
    <style>
    body{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.15),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
    .wrap{max-width:1180px;margin:0 auto;}
    .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:32px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px;}
    .box{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;line-height:1.6;}
    .box strong{display:block;color:white;font-size:18px;margin-bottom:6px;}
    .box span,p,li{color:#cbd5e1;line-height:1.7;}
    .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
    h1{font-size:44px;line-height:1.04;margin:0 0 16px 0;letter-spacing:-0.04em;}
    h2{margin:0 0 12px 0;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    table{width:100%;border-collapse:collapse;margin-top:16px;}
    th,td{text-align:left;padding:13px;border-bottom:1px solid rgba(255,255,255,0.08);vertical-align:top;}
    th{color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
    .note{background:rgba(0,255,170,0.09);border:1px solid rgba(0,255,170,0.18);border-radius:20px;padding:18px;color:#d1fae5;line-height:1.7;}
    @media(max-width:900px){body{padding:24px;}.grid{grid-template-columns:1fr;}h1{font-size:34px;}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a href="/">← Back to dashboard</a>
        <div class="card">
            <p class="kicker">Premium Watchlist Intelligence</p>
            <h1>Decision review for the current StockRadar universe.</h1>
            <p>This turns the signal table into a portfolio-style review: strongest opportunity, caution zones, role buckets and theme concentration.</p>
            <div class="grid">
                <div class="box"><strong>Strongest signal</strong>{% if strongest %}<span><a href="/stock/{{ strongest.ticker }}">{{ strongest.ticker }}</a> — {{ strongest.signal }} • {{ strongest.confidence }}</span>{% else %}<span>No conviction row available.</span>{% endif %}</div>
                <div class="box"><strong>Highest caution</strong>{% if highest_risk %}<span><a href="/stock/{{ highest_risk.ticker }}">{{ highest_risk.ticker }}</a> — {{ highest_risk.signal }} • {{ highest_risk.confidence }}</span>{% else %}<span>No current SELL warning.</span>{% endif %}</div>
                <div class="box"><strong>Review habit</strong><span>Use this page monthly before adding more risk.</span></div>
            </div>
        </div>

        <div class="card">
            <h2>Theme concentration</h2>
            <table>
                <tr><th>Theme</th><th>Count</th><th>Premium read</th></tr>
                {% for theme, count in theme_counts.items() %}
                <tr><td>{{ theme }}</td><td>{{ count }}</td><td>Use this to spot whether the opportunity set is leaning too heavily into one style or risk bucket.</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Quality names to review</h2>
            <table>
                <tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in quality_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Quality compounder</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Growth and AI satellites</h2>
            <table>
                <tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in growth_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Controlled growth satellite</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Defensive balance candidates</h2>
            <table>
                <tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in defensive_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Defensive balance</td></tr>
                {% endfor %}
            </table>
                        <div class="note">Premium read: do not just chase the strongest BUY signal. Review whether your next addition improves the overall mix.</div>
            <a class="button" href="/portfolio-fit">Check Portfolio Fit</a>
        </div>
    </div>
    </body>
    </html>
    """
    return render_template_string(
        watchlist_html,
        strongest=strongest,
        highest_risk=highest_risk,
        theme_counts=theme_counts,
        quality_names=quality_names,
        growth_names=growth_names,
        defensive_names=defensive_names,
    )


@app.route("/portfolio-fit", methods=["GET", "POST"])
def portfolio_fit():
    holdings_text = ""
    result = None

    role_labels = {
        "core_etf": "Core ETF / market exposure",
        "index": "Market index benchmark",
        "quality": "Quality compounders",
        "growth": "Growth / technology satellites",
        "defensive": "Defensive balance",
        "cyclical": "Cyclical exposure",
        "industrial": "Industrial / defence exposure",
        "research": "Research / unclassified",
    }

    if request.method == "POST":
        holdings_text = request.form.get("holdings", "").strip()
        raw_holdings = [item.strip().upper() for item in holdings_text.replace("\n", ",").split(",") if item.strip()]
        holdings = []
        seen = set()

        for ticker in raw_holdings:
            if ticker not in seen:
                holdings.append(ticker)
                seen.add(ticker)

        buckets = {key: [] for key in role_labels.keys()}

        for ticker in holdings:
            role_key = classify_portfolio_role(ticker)["key"]
            if role_key not in buckets:
                role_key = "research"
            buckets[role_key].append(ticker)

        total = len(holdings)
        core_count = len(buckets["core_etf"])
        growth_count = len(buckets["growth"])
        quality_count = len(buckets["quality"])
        defensive_count = len(buckets["defensive"])
        research_count = len(buckets["research"])
        cyclical_count = len(buckets["cyclical"]) + len(buckets["industrial"])

        warnings = []
        next_steps = []

        if total == 0:
            warnings.append("Enter at least one holding to generate a portfolio fit review.")
        else:
            if core_count == 0:
                warnings.append("No obvious core ETF base detected. Consider whether the portfolio has enough diversified exposure before adding more individual stocks.")
            if growth_count >= max(3, total // 2):
                warnings.append("Growth satellite exposure looks heavy. Check whether AI, technology or high-volatility names are dominating the portfolio.")
            if defensive_count == 0 and total >= 4:
                warnings.append("No obvious defensive balance detected. A portfolio can be strong but still vulnerable if every holding relies on growth momentum.")
            if cyclical_count >= max(3, total // 2):
                warnings.append("Cyclical exposure looks heavy. Check whether banks, energy, commodities, industrials or defence names are dominating the portfolio.")
            if total < 4:
                warnings.append("Portfolio is still concentrated by holding count. Single-stock moves may have a larger impact.")
            if research_count >= 3:
                warnings.append("Several holdings are unclassified. Review whether these are deliberate positions or random additions.")

            if core_count == 0:
                next_steps.append("Research a simple diversified ETF or broad market base before adding more specialist names.")
            if growth_count > quality_count:
                next_steps.append("Review whether quality compounders or defensive balance could reduce dependence on high-growth themes.")
            if defensive_count == 0:
                next_steps.append("Look at whether defensive balance names improve the mix without chasing momentum.")
            next_steps.append("Use the Premium Decision Panel on any individual stock before increasing position size.")
            next_steps.append("Review this portfolio monthly instead of reacting to daily price moves.")

        if total == 0:
            overall_read = "Waiting for holdings"
        elif core_count > 0 and growth_count <= max(2, total // 3) and defensive_count > 0:
            overall_read = "Balanced structure forming"
        elif growth_count >= max(3, total // 2):
            overall_read = "Growth-heavy structure"
        elif core_count == 0:
            overall_read = "Needs a clearer core"
        else:
            overall_read = "Reasonable but needs review"

        result = {
            "holdings": holdings,
            "total": total,
            "buckets": buckets,
            "role_labels": role_labels,
            "warnings": warnings,
            "next_steps": next_steps,
            "overall_read": overall_read,
        }

    if not owner_has_access():
        locked_html = """
        <!DOCTYPE html>
        <html>
        <head>
        <title>Premium Portfolio Fit Checker — StockRadar</title>
        <style>
        body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
        .wrap{max-width:920px;margin:0 auto;}
        .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:34px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
        .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
        h1{font-size:42px;line-height:1.05;margin:0 0 16px 0;letter-spacing:-0.04em;}
        p,li{color:#cbd5e1;line-height:1.7;}
        a{color:#38bdf8;font-weight:900;text-decoration:none;}
        .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:12px;}
        .locked{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}
        </style>
        </head>
        <body>
        <div class="wrap">
            <a href="/">← Back to dashboard</a>
            <div class="card">
                <p class="kicker">Premium Portfolio Fit Checker</p>
                <h1>Check whether a stock actually fits your portfolio.</h1>
                <p>Premium Portfolio Fit turns a list of holdings into a structure review: core base, quality compounders, growth satellites, defensive balance and concentration warnings.</p>
                <ul>
                    <li>Portfolio role split</li>
                    <li>Growth and AI concentration warnings</li>
                    <li>Core versus satellite balance</li>
                    <li>Suggested next research direction</li>
                </ul>
                <div class="locked"><strong>Locked:</strong> Upgrade to unlock portfolio fit reviews.</div>
                <a class="button" href="/upgrade">Unlock Premium</a>
            </div>
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html)

    portfolio_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <title>Premium Portfolio Fit Checker — StockRadar</title>
    <style>
    *{box-sizing:border-box;}
    body{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.15),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
    .wrap{max-width:1180px;margin:0 auto;}
    .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:32px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
    .grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:18px;margin-top:18px;align-items:stretch;}
    .box{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:20px;line-height:1.6;min-height:150px;overflow-wrap:anywhere;}
    .box strong{display:block;color:white;font-size:18px;margin-bottom:6px;}
    .box span,p,li{color:#cbd5e1;line-height:1.7;}
    .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
    h1{font-size:44px;line-height:1.04;margin:0 0 16px 0;letter-spacing:-0.04em;}
    h2{margin:0 0 12px 0;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    textarea{width:100%;min-height:130px;background:#020617;border:1px solid rgba(255,255,255,0.13);border-radius:18px;color:white;padding:16px;font-weight:800;outline:none;line-height:1.6;}
    button,.button{display:inline-block;border:none;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;text-decoration:none;margin-top:16px;}
    .note{background:rgba(0,255,170,0.09);border:1px solid rgba(0,255,170,0.18);border-radius:20px;padding:18px;color:#d1fae5;line-height:1.7;}
    .warning{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.7;}
    @media(max-width:1000px){body{padding:24px;}.grid{grid-template-columns:1fr;}h1{font-size:34px;}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a href="/">← Back to dashboard</a>
        <div class="card">
            <p class="kicker">Premium Portfolio Fit Checker</p>
            <h1>Does the next stock actually fit?</h1>
            <p>Enter current holdings separated by commas. StockRadar will classify the structure and flag concentration risks before you add more complexity.</p>
            <form method="POST" action="/portfolio-fit#portfolio-result">
                <textarea name="holdings" placeholder="Example: SPY, MSFT, AMZN, GOOGL, NVDA, KO, MCD">{{ holdings_text }}</textarea>
                <button type="submit">Check portfolio fit</button>
            </form>
        </div>

                {% if result %}
<div id="portfolio-result" class="card">
    <p class="kicker">Portfolio Fit Review</p>
    <h2>{{ result.overall_read }}</h2>
    <p>{{ result.total }} holdings reviewed.</p>

    <div class="note" style="margin-top:18px;">
        This review breaks your holdings into core ETF base, quality compounders, growth satellites, defensive balance and research/unclassified names.
    </div>

    <h2 style="margin-top:28px;">Portfolio role split</h2>

    <div class="grid">
        {% for role, tickers in result.buckets.items() %}
        <div class="box">
            <strong>{{ result.role_labels[role] }}</strong>
            <span>{{ tickers|length }} holding{% if tickers|length != 1 %}s{% endif %}</span>
            <p>{{ tickers|join(', ') if tickers else 'None detected' }}</p>
        </div>
        {% endfor %}
    </div>
</div>

        <div class="card">
            <h2>Concentration warnings</h2>
            {% if result.warnings %}
            <ul>
                {% for warning in result.warnings %}
                <li>{{ warning }}</li>
                {% endfor %}
            </ul>
            {% else %}
            <div class="note">No major concentration warning detected from this simple role check.</div>
            {% endif %}
        </div>

        <div class="card">
            <h2>Suggested next research direction</h2>
            <ul>
                {% for step in result.next_steps %}
                <li>{{ step }}</li>
                {% endfor %}
            </ul>
            <div class="note">Educational only: this is a structure review, not personal financial advice.</div>
        </div>
        {% endif %}
    </div>
    </body>
    </html>
    """

    return render_template_string(portfolio_html, holdings_text=holdings_text, result=result)


def safe_history(ticker, **kwargs):
    if yf is None:
        raise RuntimeError("yfinance is not installed")

    stock = yf.Ticker(ticker)

    try:
        return stock.history(**kwargs)
    except TypeError:
        kwargs.pop("timeout", None)
        return stock.history(**kwargs)


def money(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "—"


def stock_history(symbol, range_key):
    settings = CHART_RANGES.get(range_key, CHART_RANGES["1mo"])

    try:
        history = safe_history(
            symbol,
            period=settings["period"],
            interval=settings["interval"],
            timeout=6,
        )

        if history is None or history.empty or "Close" not in history.columns:
            raise ValueError("Live price data is temporarily unavailable for this ticker.")

        close = history["Close"].dropna()

        if close.empty:
            raise ValueError("No close prices available.")

        labels = [str(index)[:16] for index in close.index]
        prices = [round(float(value), 2) for value in close.values]

        start = float(close.iloc[0])
        end = float(close.iloc[-1])
        change = end - start
        percent = (change / start) * 100 if start else 0
        direction = "buy" if change > 0 else "sell" if change < 0 else "hold"

        return {
            "ok": True,
            "labels": labels,
            "prices": prices,
            "start_price": money(start),
            "end_price": money(end),
            "change_amount": f"{change:+.2f}",
            "change_percent": f"{percent:+.2f}%",
            "direction": direction,
            "error": "",
        }

    except Exception as exc:
        return {
            "ok": False,
            "labels": [],
            "prices": [],
            "start_price": "—",
            "end_price": "—",
            "change_amount": "—",
            "change_percent": "—",
            "direction": "hold",
            "error": str(exc),
        }


def stock_lifetime_growth(symbol):
    try:
        history = safe_history(symbol, period="max", interval="1mo", timeout=8)

        if history is None or history.empty or "Close" not in history.columns:
            raise ValueError("No lifetime data available")

        close = history["Close"].dropna()

        if close.empty:
            raise ValueError("No lifetime close data available")

        start = float(close.iloc[0])
        end = float(close.iloc[-1])
        change = end - start
        percent = (change / start) * 100 if start else 0
        direction = "buy" if change > 0 else "sell" if change < 0 else "hold"

        return {
            "start_price": money(start),
            "end_price": money(end),
            "change_amount": f"{change:+.2f}",
            "change_percent": f"{percent:+.2f}%",
            "direction": direction,
        }

    except Exception:
        return {
            "start_price": "—",
            "end_price": "—",
            "change_amount": "—",
            "change_percent": "—",
            "direction": "hold",
        }


def fetch_symbol_snapshot(symbol, label, market):
    try:
        history = safe_history(symbol, period="5d", timeout=4)

        if history is None or history.empty or "Close" not in history.columns:
            raise ValueError("No data")

        close = history["Close"].dropna()
        latest = float(close.iloc[-1])
        previous = float(close.iloc[-2]) if len(close) > 1 else latest
        change = latest - previous
        percent = (change / previous) * 100 if previous else 0
        direction = "buy" if change > 0 else "sell" if change < 0 else "hold"

        return {
            "symbol": symbol,
            "label": label,
            "market": market,
            "price": money(latest),
            "change": f"{change:+.2f} ({percent:+.2f}%)",
            "direction": direction,
        }

    except Exception:
        return {
            "symbol": symbol,
            "label": label,
            "market": market,
            "price": "—",
            "change": "Data unavailable",
            "direction": "hold",
        }


def market_status():
    london_now = datetime.now(ZoneInfo("Europe/London"))
    new_york_now = datetime.now(ZoneInfo("America/New_York"))

    uk_open = london_now.weekday() < 5 and dt_time(8, 0) <= london_now.time() <= dt_time(16, 30)
    us_open = new_york_now.weekday() < 5 and dt_time(9, 30) <= new_york_now.time() <= dt_time(16, 0)

    return {
        "uk_status": "OPEN" if uk_open else "CLOSED",
        "uk_time": london_now.strftime("%H:%M"),
        "us_status": "OPEN" if us_open else "CLOSED",
        "us_time": new_york_now.strftime("%H:%M"),
    }


def build_symbol_universe(recommendations):
    priority = [
        ("^GSPC", "S&P 500", "US Index"),
        ("^IXIC", "Nasdaq Composite", "US Index"),
        ("SPY", "SPDR S&P 500 ETF", "US ETF"),
        ("QQQ", "Invesco QQQ", "US ETF"),
        ("AAPL", "Apple", "US Stock"),
        ("MSFT", "Microsoft", "US Stock"),
        ("NVDA", "Nvidia", "US Stock"),
        ("TSLA", "Tesla", "US Stock"),
        ("BP.L", "BP", "UK Stock"),
        ("HSBA.L", "HSBC", "UK Stock"),
        ("AZN.L", "AstraZeneca", "UK Stock"),
    ]

    seen = {item[0] for item in priority}
    output = list(priority)

    for rec in recommendations:
        ticker = rec["ticker"]

        if ticker not in seen:
            output.append((ticker, ticker, "AI Watchlist"))
            seen.add(ticker)

    return output[:8]




MARKET_NEWS_QUERY = " OR ".join([
    "stock market",
    "Federal Reserve",
    "interest rates",
    "inflation",
    "AI stocks",
    "Nvidia",
    "Apple",
    "Microsoft",
    "Tesla",
    "oil prices",
    "Middle East",
    "semiconductors",
    "cryptocurrency",
])

NEWS_STOCK_KEYWORDS = {
    "AAPL": ["apple", "iphone", "ios", "app store"],
    "MSFT": ["microsoft", "azure", "openai", "copilot"],
    "NVDA": ["nvidia", "gpu", "ai chip", "semiconductor"],
    "AMD": ["amd", "advanced micro devices", "gpu", "semiconductor"],
    "TSLA": ["tesla", "elon musk", "ev", "electric vehicle"],
    "AMZN": ["amazon", "aws", "prime", "ecommerce"],
    "GOOGL": ["google", "alphabet", "youtube", "gemini"],
    "META": ["meta", "facebook", "instagram", "whatsapp"],
    "JPM": ["jpmorgan", "banking", "banks"],
    "GS": ["goldman", "banking", "banks"],
    "XOM": ["exxon", "oil", "energy"],
    "BP.L": ["bp", "oil", "energy"],
    "SHEL.L": ["shell", "oil", "energy"],
    "SMH": ["semiconductor", "chip", "chips"],
    "QQQ": ["nasdaq", "growth stocks", "technology stocks"],
    "SPY": ["s&p 500", "stock market", "wall street"],
    "BTC-USD": ["bitcoin", "crypto", "cryptocurrency"],
    "ETH-USD": ["ethereum", "crypto", "cryptocurrency"],
}

BULLISH_WORDS = ["rise", "rises", "jump", "jumps", "surge", "surges", "gain", "gains", "beats", "record", "upgrade", "bullish", "strong", "growth", "rally"]
BEARISH_WORDS = ["fall", "falls", "drop", "drops", "slump", "slumps", "warning", "miss", "cuts", "cut", "lawsuit", "probe", "risk", "weak", "bearish", "selloff"]


def fetch_live_market_news(limit=8):
    articles = []

    if NEWSAPI_KEY:
        try:
            params = urlencode({
                "country": "us",
                "category": "business",
                "pageSize": limit,
                "apiKey": NEWSAPI_KEY,
            })
            payload = fetch_url_json(f"https://newsapi.org/v2/top-headlines?{params}", timeout=8)

            articles = payload.get("articles", [])

            if articles:
                LAST_NEWS_FETCH_STATUS.update({"provider": "newsapi", "status": "ok", "errors": []})
                return [
                    {
                        "title": str(a.get("title") or "Market headline").strip(),
                        "source": str((a.get("source") or {}).get("name") or "Market News").strip(),
                        "url": str(a.get("url") or "/").strip(),
                        "published_at": str(a.get("publishedAt") or "").strip(),
                    }
                    for a in articles
                    if a.get("title")
                ][:limit]

            LAST_NEWS_FETCH_STATUS.update({
                "provider": "newsapi",
                "status": payload.get("status", "empty"),
                "errors": [payload.get("message", "NewsAPI returned no articles")],
            })
        except Exception as exc:
            LAST_NEWS_FETCH_STATUS.update({
                "provider": "newsapi",
                "status": "error",
                "errors": [str(exc)],
            })

    try:
        params = urlencode({
            "query": "stock market",
            "mode": "artlist",
            "format": "json",
            "maxrecords": limit,
            "sort": "hybridrel",
        })
        payload = fetch_url_json(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}", timeout=8)

        gdelt_articles = payload.get("articles", [])

        if gdelt_articles:
            LAST_NEWS_FETCH_STATUS.update({"provider": "gdelt", "status": "ok", "errors": []})
            return [
                {
                    "title": str(a.get("title") or "Market headline").strip(),
                    "source": str(a.get("domain") or "Market News").strip(),
                    "url": str(a.get("url") or "/").strip(),
                    "published_at": str(a.get("seendate") or "").strip(),
                }
                for a in gdelt_articles
                if a.get("title")
            ][:limit]

    except Exception as exc:
        LAST_NEWS_FETCH_STATUS.update({
            "provider": "gdelt",
            "status": "error",
            "errors": [str(exc)],
        })

    return []


def sentiment_from_direction(direction):
    if "Bullish" in direction:
        return "positive", "BUY / Positive impact"
    if "Bearish" in direction:
        return "negative", "SELL / Negative impact"
    return "neutral", "HOLD / Watch impact"


def score_news_impact(title):
    text = title.lower()
    bullish_hits = sum(1 for word in BULLISH_WORDS if word in text)
    bearish_hits = sum(1 for word in BEARISH_WORDS if word in text)

    if bullish_hits > bearish_hits:
        direction = "Bullish pressure"
        signal_influence = "may support BUY/HOLD conviction"
        score = min(92, 62 + bullish_hits * 8)
    elif bearish_hits > bullish_hits:
        direction = "Bearish pressure"
        signal_influence = "may support HOLD/SELL caution"
        score = min(92, 62 + bearish_hits * 8)
    else:
        direction = "Market sensitivity"
        signal_influence = "may influence watchlist positioning"
        score = 58

    return direction, signal_influence, f"{score}/100"


def match_news_to_stocks(title):
    text = title.lower()
    matches = []

    for ticker, keywords in NEWS_STOCK_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            matches.append(ticker)

    return matches[:5] or ["SPY", "QQQ"]


# --- Helper to build consistent news stock links safely ---
def build_news_stock_links(tickers, signal_lookup=None):
    signal_lookup = signal_lookup or {}
    links = []

    for ticker in tickers:
        cleaned = str(ticker or "").strip().upper()
        if not cleaned:
            continue

        signal = str(signal_lookup.get(cleaned, "HOLD")).strip().upper()
        if signal not in {"BUY", "SELL", "HOLD"}:
            signal = "HOLD"

        links.append({
            "ticker": cleaned,
            "url": f"/stock/{cleaned}",
            "signal": signal,
            "signal_class": signal.lower(),
        })

    return links or [
        {"ticker": "SPY", "url": "/stock/SPY", "signal": "HOLD", "signal_class": "hold"},
        {"ticker": "QQQ", "url": "/stock/QQQ", "signal": "BUY", "signal_class": "buy"},
    ]


def format_news_time(published_at):
    if not published_at:
        return "Fresh"

    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        minutes = int((now - parsed).total_seconds() / 60)

        if minutes < 1:
            return "Just now"
        if minutes < 60:
            return f"{minutes}m ago"

        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"

        return parsed.strftime("%d %b")
    except Exception:
        return "Fresh"


def get_market_impact_radar():
    return [
        {
            "title": "US Politics Risk Watch",
            "impact": "High",
            "impact_score": "84/100",
            "direction": "Volatility risk rising",
            "theme": "Election policy, regulation and government spending can quickly affect market sentiment.",
            "sectors": "Banks, Big Tech, Defence, Energy",
            "stocks": ["JPM", "GS", "META", "GOOGL", "LMT", "RTX", "XOM", "BP.L"],
            "free_view": "Politics-linked volatility risk is elevated across banks, Big Tech, defence and energy.",
            "premium_view": "Premium AI view: monitor whether policy headlines are changing sector leadership. Banks may react to rate and regulation talk, Big Tech to antitrust or AI regulation, defence to spending expectations, and energy to sanctions or supply-chain pressure.",
            "watch_next": "Watch banking regulation language, AI/Big Tech scrutiny, defence budget comments and energy policy headlines.",
        },
        {
            "title": "Geopolitical Tension Watch",
            "impact": "High",
            "impact_score": "88/100",
            "direction": "Energy and defence sensitivity high",
            "theme": "Conflict, sanctions and trade disruption can move oil, defence, shipping, gold and semiconductor-linked stocks.",
            "sectors": "Oil, Defence, Semiconductors, Safe Havens",
            "stocks": ["XOM", "SHEL.L", "BP.L", "RTX", "LMT", "NVDA", "SMH", "GLD"],
            "free_view": "Geopolitical tension is a high-impact theme for energy, defence, safe havens and chip supply chains.",
            "premium_view": "Premium AI view: rising geopolitical tension can support energy and defence exposure while adding risk to semiconductor supply chains. Watch for oil moves, chip-sector weakness, and defence strength at the same time.",
            "watch_next": "Watch oil price moves, defence-stock strength, chip-sector weakness and safe-haven demand.",
        },
        {
            "title": "AI Regulation Watch",
            "impact": "Medium",
            "impact_score": "69/100",
            "direction": "Technology policy pressure building",
            "theme": "AI regulation, data rules and chip export restrictions can affect the highest-profile technology names.",
            "sectors": "AI, Chips, Cloud, Big Tech",
            "stocks": ["NVDA", "AMD", "MSFT", "GOOGL", "META", "AMZN", "SMH", "QQQ"],
            "free_view": "AI regulation is a medium-impact theme for chips, cloud platforms and Big Tech indexes.",
            "premium_view": "Premium AI view: the strongest reaction is likely when regulation, export controls or government scrutiny directly affects chip demand, cloud infrastructure or AI platform margins.",
            "watch_next": "Watch AI regulation headlines, chip export controls, cloud demand commentary and Big Tech legal pressure.",
        },
        {
            "title": "Rates & Inflation Watch",
            "impact": "Medium",
            "impact_score": "72/100",
            "direction": "Rate-sensitive assets on watch",
            "theme": "Inflation data and central-bank language can shift expectations for equities, banks, property and growth stocks.",
            "sectors": "Banks, Property, Growth Tech, Index ETFs",
            "stocks": ["JPM", "GS", "SPY", "QQQ", "MSFT", "AMZN", "LAND.L", "LLOY.L"],
            "free_view": "Rates and inflation remain a key market theme for banks, property, growth stocks and index ETFs.",
            "premium_view": "Premium AI view: falling rate expectations can support growth and property, while sticky inflation can pressure long-duration technology shares and change bank margin expectations.",
            "watch_next": "Watch inflation releases, central-bank speeches, bond yields and rate-cut probability shifts.",
        },
    ]

# --- News-style market impact ticker function ---
def build_live_headlines(recommendations, impact_radar):
    headlines = []
    live_articles = fetch_live_market_news()
    signal_lookup = {
        str(item.get("ticker", "")).strip().upper(): str(item.get("signal", "HOLD")).strip().upper()
        for item in recommendations
    }

    for article in live_articles:
        title = article.get("title", "").strip()

        if not title:
            continue

        matched_stocks = match_news_to_stocks(title)
        primary_stock = matched_stocks[0] if matched_stocks else "SPY"
        stock_text = ", ".join(matched_stocks)
        direction, signal_influence, impact_score = score_news_impact(title)
        impact_class, impact_label = sentiment_from_direction(direction)
        source = str(article.get("source") or "Market News").strip()
        article_url = str(article.get("url") or "/").strip()
        published_label = format_news_time(str(article.get("published_at") or "").strip())

        headlines.append({
            "label": "LIVE NEWS",
            "headline": title,
            "text": title,
            "url": f"/stock/{primary_stock}",
            "article_url": article_url,
            "stock_url": f"/stock/{primary_stock}",
            "stock_text": stock_text,
            "stock_links": build_news_stock_links(matched_stocks, signal_lookup),
            "impact_score": impact_score,
            "direction": direction,
            "source": source,
            "published_label": published_label,
            "premium_text": title,
        })

    if headlines:
        return headlines[:8]

    if NEWSAPI_KEY:
        return [{
            "label": "LIVE NEWS",
            "headline": "Live market headlines are temporarily unavailable",
            "text": "NewsAPI is configured, but no literal article titles were returned.",
            "url": "/",
            "article_url": "/",
            "stock_url": "/stock/SPY",
            "stock_text": "SPY, QQQ",
            "stock_links": build_news_stock_links(["SPY", "QQQ"], signal_lookup),
            "impact_score": "Pending",
            "direction": "Live feed check needed",
            "source": "StockRadar News Feed",
            "published_label": "Live check",
            "premium_text": "NewsAPI is configured, but no literal article titles were returned.",
        }]

    return [{
        "label": "LIVE NEWS",
        "headline": "Add NEWSAPI_KEY to enable literal live market headlines",
        "text": "Add NEWSAPI_KEY to enable literal live market headlines.",
        "url": "/",
        "article_url": "/",
        "stock_url": "/stock/SPY",
        "stock_text": "SPY, QQQ",
        "stock_links": build_news_stock_links(["SPY", "QQQ"], signal_lookup),
        "impact_score": "Pending",
        "direction": "NewsAPI key required",
        "source": "StockRadar News Feed",
        "published_label": "Setup needed",
        "premium_text": "Add NEWSAPI_KEY to enable literal live market headlines.",
    }]

def safe_build_live_headlines(recommendations, impact_radar):
    try:
        headlines = build_live_headlines(recommendations, impact_radar)
        if headlines:
            return headlines
    except Exception as exc:
        LAST_NEWS_FETCH_STATUS.update({
            "provider": "stockradar",
            "status": "render_error",
            "errors": [str(exc)],
        })

    return [{
        "label": "LIVE NEWS",
        "headline": "Market headlines are reconnecting",
        "text": "Market headlines are reconnecting.",
        "url": "/news-health",
        "article_url": "/news-health",
        "stock_url": "/stock/SPY",
        "stock_text": "SPY, QQQ",
        "stock_links": build_news_stock_links(["SPY", "QQQ"], {"SPY": "HOLD", "QQQ": "BUY"}),
        "impact_score": "Pending",
        "direction": "Feed health check active",
        "source": "StockRadar News Feed",
        "published_label": "Live check",
        "premium_text": "Market headlines are reconnecting.",
    }]

def prepare_dashboard_data():
    recommendations = get_recommendations()
    impact_radar = get_market_impact_radar()
    buy_rows, hold_rows, sell_rows, conviction_rows = split_rows(recommendations)
    buy_count, hold_count, sell_count, high_conviction_count = calculate_counts(recommendations)

    universe = build_symbol_universe(recommendations)
    market_snapshot = [fetch_symbol_snapshot(symbol, label, market) for symbol, label, market in universe]

    return {
        "recommendations": recommendations,
        "buy_rows": buy_rows,
        "hold_rows": hold_rows,
        "sell_rows": sell_rows,
        "conviction_rows": conviction_rows,
        "buy_count": buy_count,
        "hold_count": hold_count,
        "sell_count": sell_count,
        "total_count": len(recommendations),
        "sectors": sorted({item.get("sector") or "AI Watchlist" for item in recommendations}),
        "high_conviction_count": high_conviction_count,
        "market_snapshot": market_snapshot,
        "market_status": market_status(),
        "last_updated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "ticker_updated": datetime.now().strftime("%H:%M"),
        "impact_radar": impact_radar,
        "live_headlines": safe_build_live_headlines(recommendations, impact_radar) or [],
        "newsapi_configured": bool(NEWSAPI_KEY),
    }


html = """
<!DOCTYPE html>
<html>
<head>
<title>StockRadar</title>
<style>
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.15),transparent 28%),radial-gradient(circle at 90% 10%,rgba(255,184,107,0.12),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;display:flex;min-height:100vh;}
a{color:#38bdf8;text-decoration:none;font-weight:800;}
a:hover{text-decoration:underline;}
.sidebar{width:280px;min-height:100vh;padding:28px;background:rgba(5,5,5,0.82);border-right:1px solid rgba(255,255,255,0.08);position:sticky;top:0;}
.logo{font-size:25px;font-weight:950;margin-bottom:22px;background:linear-gradient(135deg,#fff,#00ffaa,#ffb86b);-webkit-background-clip:text;color:transparent;}
.nav-link{display:block;padding:14px 14px;border-radius:16px;color:#dbeafe;margin:8px 0;background:rgba(255,255,255,0.04);text-decoration:none;font-weight:900;line-height:1.25;}
.nav-link:hover{background:rgba(0,255,170,0.10);text-decoration:none;}
.nav-section-label{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.13em;font-weight:950;margin:18px 0 8px 0;}
.pro-button{background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;font-weight:950;}
.menu-divider{height:1px;background:rgba(255,255,255,0.08);margin:18px 0;}
.menu-help{color:#94a3b8;font-size:12px;line-height:1.55;margin:10px 0 14px 0;}
.owner-box{margin-top:20px;color:#94a3b8;font-size:13px;line-height:1.6;}
.main{flex:1;padding:34px 48px 48px 48px;overflow-y:auto;max-width:1500px;margin:0 auto;}
.card,.market-card{background:linear-gradient(180deg,rgba(23,23,23,0.94),rgba(14,14,14,0.94));padding:28px;border-radius:28px;margin-bottom:22px;border:1px solid rgba(255,255,255,0.10);box-shadow:0 28px 82px rgba(0,0,0,0.35),inset 0 1px 0 rgba(255,255,255,0.07);}
.kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
h1{font-size:44px;line-height:1.04;margin:0 0 16px 0;letter-spacing:-0.04em;}
h2{margin:0 0 14px 0;}
p,li{color:#cbd5e1;line-height:1.7;}
.button,.upgrade-cta{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;border:none;cursor:pointer;}
.summary-grid,.market-grid,.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:18px;margin-bottom:22px;}
.summary-card{background:linear-gradient(180deg,rgba(23,23,23,0.94),rgba(14,14,14,0.94));padding:24px;border-radius:24px;border:1px solid rgba(255,255,255,0.10);}
.summary-card h2{font-size:42px;margin:0 0 4px 0;}
.summary-card p{color:#94a3b8;margin:0;font-weight:800;}
.market-card small{color:#94a3b8;text-transform:uppercase;letter-spacing:0.10em;font-weight:900;font-size:11px;}
.market-card h3{font-size:20px;margin:10px 0;}
.status-pill{padding:7px 11px;border-radius:999px;font-weight:950;font-size:12px;display:inline-block;}
.status-pill.buy{background:rgba(34,197,94,0.14);color:#bbf7d0;border:1px solid rgba(34,197,94,0.28);}
.status-pill.sell{background:rgba(239,68,68,0.14);color:#fecaca;border:1px solid rgba(239,68,68,0.28);}
.status-pill.hold{background:rgba(245,158,11,0.14);color:#fde68a;border:1px solid rgba(245,158,11,0.28);}
table{width:100%;border-collapse:collapse;margin-top:16px;}
th,td{text-align:left;padding:13px;border-bottom:1px solid rgba(255,255,255,0.08);vertical-align:top;}
th{color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
.signal-buy{color:#86efac;font-weight:950;}
.signal-sell{color:#fca5a5;font-weight:950;}
.signal-hold{color:#fde68a;font-weight:950;}
.live-alert-strip{margin-bottom:22px;background:linear-gradient(90deg,rgba(0,255,170,0.12),rgba(56,189,248,0.10),rgba(255,184,107,0.10));border:1px solid rgba(255,255,255,0.12);border-radius:22px;overflow:hidden;box-shadow:0 22px 60px rgba(0,0,0,0.28);backdrop-filter:blur(18px);}
.live-alert-header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.08);font-weight:950;color:white;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
.live-dot{width:9px;height:9px;border-radius:999px;background:#22c55e;box-shadow:0 0 18px rgba(34,197,94,0.8);}
.live-alert-track{display:flex;gap:18px;overflow-x:auto;padding:13px 16px;align-items:stretch;}
.live-headline{display:inline-flex;flex-direction:column;align-items:flex-start;gap:9px;min-width:420px;max-width:560px;color:#e5e7eb;text-decoration:none;font-weight:800;background:rgba(2,6,23,0.35);border:1px solid rgba(255,255,255,0.08);border-radius:18px;padding:14px 16px;white-space:normal;}
.live-headline-details{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding-left:2px;}
.live-news-meta{color:#94a3b8;font-size:12px;font-weight:950;text-transform:none;letter-spacing:0.02em;}
.live-news-title{display:block;color:white;font-size:15px;font-weight:950;line-height:1.35;text-decoration:none;}
.live-news-title:hover{color:#ccfbf1;text-decoration:none;}
.live-affected-label{color:#94a3b8;font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.09em;margin-right:2px;}
.live-tag{display:inline-block;background:rgba(0,255,170,0.12);border:1px solid rgba(0,255,170,0.20);color:#bbf7d0;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;letter-spacing:0.08em;text-transform:uppercase;}
.live-score{display:inline-block;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.22);color:#bae6fd;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;}
.live-stock-link{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:950;text-decoration:none;border:1px solid rgba(255,255,255,0.12);}
.live-stock-link.buy{background:rgba(34,197,94,0.16);border-color:rgba(34,197,94,0.34);color:#bbf7d0;}
.live-stock-link.sell{background:rgba(239,68,68,0.16);border-color:rgba(239,68,68,0.34);color:#fecaca;}
.live-stock-link.hold{background:rgba(245,158,11,0.16);border-color:rgba(245,158,11,0.34);color:#fde68a;}
.live-stock-link::before{content:"";width:7px;height:7px;border-radius:999px;background:currentColor;box-shadow:0 0 12px currentColor;}
.live-stock-link:hover{filter:brightness(1.12);color:white;text-decoration:none;}
@media(max-width:900px){body{display:block;}.sidebar{width:100%;min-height:auto;position:relative;}.main{padding:24px;}.live-headline{min-width:320px;}h1{font-size:34px;}}
</style>
</head>
<body>
<div class="sidebar">
    <div class="logo">StockRadar</div>
    <a class="nav-link" href="/">Dashboard</a>
    <a class="nav-link" href="/beginner">Investment Compass</a>
    <a class="nav-link" href="/?tab=signals">AI Signals</a>
    <a class="nav-link" href="/?tab=watchlist">Watchlist</a>
    <a class="nav-link" href="/premium-watchlist">Premium Watchlist</a>
    <a class="nav-link" href="/portfolio-fit">Portfolio Fit</a>
    <div class="menu-divider"></div>
    <a class="nav-link pro-button" href="/upgrade">Upgrade</a>
    <div class="owner-box">Educational market software. Not personal financial advice.</div>
</div>

<div class="main">
    <div class="live-alert-strip">
        <div class="live-alert-header">
            <span class="live-dot"></span>
            <span>Market News Feed</span>
        </div>
        <div class="live-alert-track">
            {% for item in live_headlines %}
            <div class="live-headline">
                <a class="live-news-title" href="{{ item.get('article_url', '/') }}" target="_blank" rel="noopener">
                    {{ item.get('headline', 'Market headlines are reconnecting') }}
                </a>
                <div class="live-headline-details">
                    <span class="live-news-meta">{{ item.get('source', 'StockRadar News Feed') }} · {{ item.get('published_label', 'Fresh') }}</span>
                    <span class="live-score">{{ item.get('impact_score', 'Pending') }}</span>
                    <span class="live-tag">{{ item.get('direction', 'Market watch') }}</span>
                    <span class="live-affected-label">Affected:</span>
                    {% for link in item.get('stock_links', []) %}
                    <a class="live-stock-link {{ link.get('signal_class', 'hold') }}" href="{{ link.get('url', '/') }}">{{ link.get('ticker', 'SPY') }} <span>{{ link.get('signal', 'HOLD') }}</span></a>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="card" id="investment-compass-card">
        <p class="kicker">Investment Compass</p>
        <h1>Save time finding where to start.</h1>
        <p>Answer a few simple questions and StockRadar will cut through the noise with a plain-English starting profile, a sensible investment structure, and a clearer research direction. Useful if you are new, returning after a break, or just want to avoid wasting hours searching for the right starting point.</p>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;">
            <a class="upgrade-cta" href="/beginner">Find My Investment Starting Point</a>
            <a class="nav-link" style="display:inline-block;width:auto;margin:0;background:rgba(255,255,255,0.06);" href="/?tab=signals">View AI Signals</a>
        </div>
    </div>

    <div class="card">
        <p class="kicker">StockRadar Dashboard</p>
        <h1>AI market signals, news impact and portfolio direction.</h1>
        <p>Review current BUY, HOLD and SELL signals across the StockRadar universe. Use the news feed first, then check whether the signal fits your portfolio before acting.</p>
    </div>

    <div class="summary-grid">
        <div class="summary-card"><h2>{{ buy_count }}</h2><p>BUY signals</p></div>
        <div class="summary-card"><h2>{{ hold_count }}</h2><p>HOLD signals</p></div>
        <div class="summary-card"><h2>{{ sell_count }}</h2><p>SELL warnings</p></div>
        <div class="summary-card"><h2>{{ high_conviction_count }}</h2><p>High conviction</p></div>
    </div>

    <div class="card">
        <h2>Market snapshot</h2>
        <div class="market-grid">
            {% for item in market_snapshot %}
            <div class="market-card">
                <small>{{ item.get('market', 'Market') }}</small>
                <h3><a href="/stock/{{ item.get('symbol', 'SPY') }}">{{ item.get('label', item.get('symbol', 'SPY')) }}</a></h3>
                <p>{{ item.get('price', '—') }} · {{ item.get('change', 'Data unavailable') }}</p>
                <span class="status-pill {{ item.get('direction', 'hold') }}">{{ item.get('direction', 'hold')|upper }}</span>
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="card" id="signals">
        <h2>AI Signals</h2>
        <table>
            <tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>Reason</th><th>Premium</th></tr>
            {% for item in recommendations %}
            <tr>
                <td><a href="/stock/{{ item.get('ticker', '') }}">{{ item.get('ticker', '') }}</a></td>
                <td class="signal-{{ item.get('signal', 'HOLD')|lower }}">{{ item.get('signal', 'HOLD') }}</td>
                <td>{{ item.get('confidence', '—') }}</td>
                <td>{{ item.get('reason', '') }}</td>
                <td><a href="/premium-decision/{{ item.get('ticker', '') }}">Decision Panel</a></td>
            </tr>
            {% endfor %}
        </table>
    </div>

    <div class="card" id="watchlist">
        <h2>Watchlist Intelligence</h2>
        <p>Use Premium Watchlist Intelligence to review strongest signals, caution names, quality names, growth satellites and defensive balance candidates.</p>
        <a class="button" href="/premium-watchlist">Open Premium Watchlist</a>
        <a class="button" href="/portfolio-fit" style="margin-left:10px;">Check Portfolio Fit</a>
    </div>
</div>
</body>
</html>
"""

# Add root route if not present
@app.route("/")
def dashboard():
    data = prepare_dashboard_data()
    return render_template_string(html, **data)
#
# Flask local run block (add only if not present)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)