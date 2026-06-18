from flask import Flask, render_template_string, redirect, url_for, request, session, jsonify
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
import csv
import json
import os
import pandas as pd
import ssl
import time


def is_production_environment():
    return (
        os.environ.get("RENDER", "").strip().lower() == "true"
        or os.environ.get("FLASK_ENV", "").strip().lower() == "production"
        or os.environ.get("ENVIRONMENT", "").strip().lower() == "production"
    )


def configure_session_security(flask_app, secret_key, production):
    configured_secret = str(secret_key or "").strip()

    if production and len(configured_secret) < 32:
        raise RuntimeError("A strong session secret of at least 32 characters is required in production.")

    flask_app.secret_key = configured_secret or "stockradar-local-development-only"
    flask_app.config.update(
        SESSION_COOKIE_SECURE=bool(production),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    import stripe
except ImportError:
    stripe = None

app = Flask(__name__)
IS_PRODUCTION = is_production_environment()
SESSION_SECRET = (
    os.environ.get("SIGNALSCOPE_SECRET_KEY")
    or os.environ.get("SESSION_SECRET")
    or os.environ.get("SECRET_KEY")
    or ""
)
configure_session_security(app, SESSION_SECRET, IS_PRODUCTION)
OWNER_EMAIL = os.environ.get("SIGNALSCOPE_OWNER_EMAIL", "").strip().lower()
OWNER_PASSWORD = os.environ.get("SIGNALSCOPE_OWNER_PASSWORD", "")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "").strip()
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
PRODUCTION_BASE_URL = "https://signalscope-ai-1-0v3g.onrender.com"
DEFAULT_STRIPE_SUCCESS_URL = (
    f"{PRODUCTION_BASE_URL}/checkout-success?session_id={{CHECKOUT_SESSION_ID}}"
)
DEFAULT_STRIPE_CANCEL_URL = f"{PRODUCTION_BASE_URL}/upgrade"


def configured_url(environment_name, default):
    return os.environ.get(environment_name, default)


STRIPE_SUCCESS_URL = configured_url("STRIPE_SUCCESS_URL", DEFAULT_STRIPE_SUCCESS_URL)
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
LAST_NEWS_FETCH_STATUS = {
    "provider": "none",
    "status": "not_started",
    "errors": [],
}
DASHBOARD_CACHE_TTL_SECONDS = int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "300"))
DASHBOARD_CACHE = {
    "timestamp": 0,
    "data": None,
}
RECOMMENDATIONS_CACHE_TTL_SECONDS = int(os.environ.get("RECOMMENDATIONS_CACHE_TTL_SECONDS", "300"))
RECOMMENDATIONS_CACHE = {
    "timestamp": 0,
    "rows": None,
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
STRIPE_CANCEL_URL = configured_url("STRIPE_CANCEL_URL", DEFAULT_STRIPE_CANCEL_URL)

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

def stripe_checkout_configured():
    return bool(stripe and STRIPE_SECRET_KEY and STRIPE_PRICE_ID)

def owner_has_access():
    return session.get("owner_logged_in") is True


def owner_login_configured():
    return bool(OWNER_EMAIL and OWNER_PASSWORD)


def disclaimer_footer():
    return """
    <footer style="margin:32px auto 0;padding:18px 0 0;border-top:1px solid rgba(255,255,255,0.10);color:#94a3b8;font-size:12px;line-height:1.65;max-width:1180px;">
        <div>
            <strong style="color:#cbd5e1;">Educational only.</strong>
            StockRadar provides educational market information and research tools only. It does not provide personal financial, investment, tax, or legal advice. BUY, HOLD, and SELL signals are research prompts—not instructions or guarantees. Investments can fall as well as rise, and you may lose money. Consider your circumstances and seek advice from a regulated professional where appropriate.
        </div>
        <nav aria-label="Legal and support links" style="display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;">
            <a href="/privacy" style="color:#94a3b8;">Privacy</a>
            <a href="/terms" style="color:#94a3b8;">Terms</a>
            <a href="/refund-policy" style="color:#94a3b8;">Refund Policy</a>
            <a href="/risk-disclaimer" style="color:#94a3b8;">Risk Disclaimer</a>
            <a href="/manage-subscription" style="color:#94a3b8;">Manage Subscription</a>
            <a href="/feedback" style="color:#94a3b8;">Feedback</a>
            <a href="/contact" style="color:#94a3b8;">Contact</a>
        </nav>
    </footer>
    """


app.jinja_env.globals["disclaimer_footer"] = disclaimer_footer


legal_page_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} — StockRadar</title>
<style>
body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
.wrap{max-width:900px;margin:0 auto;}
.card{background:rgba(15,23,42,0.94);border:1px solid rgba(255,255,255,0.11);border-radius:26px;padding:32px;box-shadow:0 24px 70px rgba(0,0,0,0.35);}
h1{font-size:42px;margin:0 0 18px;}
h2{font-size:22px;margin:26px 0 8px;}
p,li{color:#cbd5e1;line-height:1.75;}
a{color:#38bdf8;}
.back{display:inline-block;margin-bottom:22px;font-weight:900;text-decoration:none;}
@media(max-width:700px){body{padding:24px;}h1{font-size:34px;}}
</style>
</head>
<body>
<div class="wrap">
    <a class="back" href="/">← Back to StockRadar</a>
    <main class="card">
        <h1>{{ title }}</h1>
        {{ content | safe }}
    </main>
    {{ disclaimer_footer() | safe }}
</div>
</body>
</html>
"""


def render_legal_page(title, content):
    return render_template_string(legal_page_html, title=title, content=content)


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
    {"ticker": "PLTR", "signal": "HOLD", "confidence": "63%", "reason": "Palantir has strong AI and data analytics momentum, but valuation and volatility need a controlled research approach."},
    {"ticker": "SPCX", "signal": "HOLD", "confidence": "50%", "reason": "SpaceX is a high-growth space and Starlink-linked research candidate. Treat it as a controlled satellite because valuation, volatility, liquidity and public trading history may be limited."},
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
STOCK_UNIVERSE_CSV = "stock_universe.csv"
STOCK_UNIVERSE_CACHE_TTL_SECONDS = int(os.environ.get("STOCK_UNIVERSE_CACHE_TTL_SECONDS", "300"))
STOCK_UNIVERSE_CACHE = {
    "timestamp": 0,
    "rows": None,
}
STOCK_DISPLAY_LOOKUP_CACHE = {
    "rows": None,
    "lookup": {},
}

TRACKED_STOCK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "PLTR", "SPCX", "AVGO", "AMD", "NFLX",
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
    "PLTR": "Technology / Data Analytics",
    "SPCX": "Space / Aerospace / Growth",
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

STOCK_SYMBOL_ALIASES = {
    "PALANTIR": "PLTR",
    "PALANTIR TECHNOLOGIES": "PLTR",
    "PALANTIR TECHNOLOGIES INC": "PLTR",
    "PALANTIR TECHNOLOGIES INC.": "PLTR",
    "SPACEX": "SPCX",
    "SPACE X": "SPCX",
    "SPAX.PVT": "SPCX",
}

STOCK_SEARCH_ALIASES = {
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "ALPHABET CLASS A": "GOOGL",
    "GOOGLE STOCK": "GOOGL",
}


def canonical_stock_symbol(value):
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    upper_value = cleaned.upper()
    normalized_name = " ".join(
        upper_value.replace("-", " ").replace("_", " ").split()
    )
    alias_match = STOCK_SYMBOL_ALIASES.get(upper_value) or STOCK_SYMBOL_ALIASES.get(normalized_name)
    if alias_match:
        return alias_match
    try:
        for item in get_stock_universe():
            ticker = str(item.get("ticker", "")).strip().upper()
            name = str(item.get("name", "")).strip().upper()
            normalized_item_name = " ".join(
                name.replace("-", " ").replace("_", " ").split()
            )
            if upper_value == ticker or normalized_name == normalized_item_name:
                return ticker
    except Exception:
        pass
    return upper_value


def generated_signal_for_ticker(ticker, index):
    if ticker == "SPCX":
        return "HOLD", "50%"
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
            "reason": (
                "SpaceX is a high-growth space and Starlink-linked research candidate. Treat it as a controlled satellite because valuation, volatility, liquidity and public trading history may be limited."
                if ticker == "SPCX"
                else "Included in the 100-stock StockRadar universe. This keeps the live dashboard complete until the full scanner CSV/API feed is connected."
            ),
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

def normalise_universe_row(row):
    lower = {str(k or "").strip().lower(): v for k, v in row.items()}
    ticker = str(
        lower.get("ticker")
        or lower.get("symbol")
        or lower.get("code")
        or ""
    ).strip().upper()
    name = str(
        lower.get("company")
        or lower.get("company name")
        or lower.get("company_name")
        or lower.get("name")
        or ticker
    ).strip()
    exchange = str(lower.get("exchange") or lower.get("market") or "").strip()
    sector = str(lower.get("sector") or SECTOR_MAP.get(ticker, "Stock Universe")).strip()

    if not ticker:
        return None

    return {
        "ticker": ticker,
        "name": name or ticker,
        "exchange": exchange,
        "sector": sector or "Stock Universe",
        "url": f"/stock/{ticker}",
        "search_text": f"{ticker} {name} {exchange} {sector}".lower(),
    }


def get_stock_universe(force_refresh=False):
    now = time.time()

    if (
        not force_refresh
        and STOCK_UNIVERSE_CACHE["rows"] is not None
        and now - STOCK_UNIVERSE_CACHE["timestamp"] < STOCK_UNIVERSE_CACHE_TTL_SECONDS
    ):
        return STOCK_UNIVERSE_CACHE["rows"]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, STOCK_UNIVERSE_CSV)
    rows = []
    seen = set()

    if os.path.exists(path):
        try:
            with open(path, newline="", encoding="utf-8-sig") as csvfile:
                reader = csv.DictReader(csvfile)

                for raw_row in reader:
                    item = normalise_universe_row(raw_row)
                    if not item or item["ticker"] in seen:
                        continue
                    rows.append(item)
                    seen.add(item["ticker"])
        except Exception:
            app.logger.warning("Stock universe CSV load failed; using fallback universe.")
            rows = []
            seen = set()

    if not rows:
        for item in get_recommendations():
            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker or ticker in seen:
                continue
            rows.append({
                "ticker": ticker,
                "name": ticker,
                "exchange": "",
                "sector": str(item.get("sector") or SECTOR_MAP.get(ticker, "Stock Universe")),
                "url": f"/stock/{ticker}",
                "search_text": f"{ticker} {item.get('sector', '')}".lower(),
            })
            seen.add(ticker)

    STOCK_UNIVERSE_CACHE["timestamp"] = now
    STOCK_UNIVERSE_CACHE["rows"] = rows
    return rows


def stock_display_lookup():
    try:
        rows = get_stock_universe()
        if STOCK_DISPLAY_LOOKUP_CACHE["rows"] is rows:
            return STOCK_DISPLAY_LOOKUP_CACHE["lookup"]

        lookup = {}
        for item in rows:
            ticker = str(item.get("ticker", "")).strip().upper()
            name = str(item.get("name", "")).strip()
            if ticker:
                lookup[ticker] = name

        STOCK_DISPLAY_LOOKUP_CACHE["rows"] = rows
        STOCK_DISPLAY_LOOKUP_CACHE["lookup"] = lookup
        return lookup
    except Exception:
        return {}


def stock_display_label(value):
    canonical = canonical_stock_symbol(value)
    if not canonical:
        return ""

    try:
        name = stock_display_lookup().get(canonical, "")
        if not name or name.upper() == canonical:
            return canonical
        if name.upper().endswith(f"({canonical})"):
            return name
        return f"{name} ({canonical})"
    except Exception:
        pass

    return canonical


app.jinja_env.globals["stock_display_label"] = stock_display_label


def search_stock_universe(query, limit=12):
    cleaned_query = str(query or "").strip().lower()
    if not cleaned_query:
        return []

    normalized_query = " ".join(
        cleaned_query.upper().replace("-", " ").replace("_", " ").split()
    )
    alias_ticker = (
        STOCK_SEARCH_ALIASES.get(normalized_query)
        or STOCK_SYMBOL_ALIASES.get(normalized_query)
    )
    if alias_ticker:
        cleaned_query = alias_ticker.lower()

    exact_matches = []
    prefix_matches = []
    contains_matches = []

    for item in get_stock_universe():
        ticker = item["ticker"].lower()
        name = item["name"].lower()
        search_text = item["search_text"]

        if cleaned_query == ticker:
            exact_matches.append(item)
        elif ticker.startswith(cleaned_query) or name.startswith(cleaned_query):
            prefix_matches.append(item)
        elif cleaned_query in search_text:
            contains_matches.append(item)

    ordered = exact_matches + prefix_matches + contains_matches
    deduped = []
    seen = set()

    for item in ordered:
        if item["ticker"] in seen:
            continue
        deduped.append(item)
        seen.add(item["ticker"])
        if len(deduped) >= limit:
            break

    return deduped

def get_recommendations():
    now = time.time()

    if (
        RECOMMENDATIONS_CACHE["rows"] is not None
        and now - RECOMMENDATIONS_CACHE["timestamp"] < RECOMMENDATIONS_CACHE_TTL_SECONDS
    ):
        return RECOMMENDATIONS_CACHE["rows"]

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
                recommendations = expand_recommendations(rows)
                RECOMMENDATIONS_CACHE["timestamp"] = now
                RECOMMENDATIONS_CACHE["rows"] = recommendations
                return recommendations
        except Exception:
            app.logger.warning("Recommendation CSV load failed; trying next candidate.")
            continue

    recommendations = expand_recommendations(DEFAULT_RECOMMENDATIONS)
    RECOMMENDATIONS_CACHE["timestamp"] = now
    RECOMMENDATIONS_CACHE["rows"] = recommendations
    return recommendations

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

def build_signal_lookup(recommendations):
    return {
        str(item.get("ticker", "")).strip().upper(): clean_signal(item.get("signal", "HOLD"), item.get("confidence"))
        for item in recommendations
        if item.get("ticker")
    }


def build_stock_links_with_signals(tickers, signal_lookup=None):
    signal_lookup = signal_lookup or {}
    links = []

    for ticker in tickers:
        cleaned = str(ticker or "").strip().upper()
        if not cleaned:
            continue

        signal = clean_signal(signal_lookup.get(cleaned, "HOLD"))
        signal_class = signal.lower() if signal in {"BUY", "SELL", "HOLD"} else "hold"

        if signal == "BUY":
            action_text = "Buy"
        elif signal == "SELL":
            action_text = "Sell"
        else:
            action_text = "Hold"

        links.append({
            "ticker": cleaned,
            "display_label": stock_display_label(cleaned),
            "url": f"/stock/{cleaned}",
            "signal": signal,
            "signal_class": signal_class,
            "action_text": action_text,
        })

    return links or [
        {"ticker": "SPY", "display_label": stock_display_label("SPY"), "url": "/stock/SPY", "signal": "HOLD", "signal_class": "hold", "action_text": "Hold"},
        {"ticker": "QQQ", "display_label": stock_display_label("QQQ"), "url": "/stock/QQQ", "signal": "BUY", "signal_class": "buy", "action_text": "Buy"},
    ]

def get_stock_ai_context(symbol):
    recommendations = get_recommendations()
    cleaned_symbol = canonical_stock_symbol(symbol)

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
        "headline": f"{stock_display_label(cleaned_symbol)} Premium Decision Panel",
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

@app.route("/universe")
def stock_universe_page():
    query = request.args.get("q", "").strip()
    all_rows = get_stock_universe()

    if query:
        visible_rows = search_stock_universe(query, limit=250)
    else:
        visible_rows = all_rows[:250]

    universe_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Universe — StockRadar</title>
    <style>
    body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:42px;}
    .wrap{max-width:1180px;margin:0 auto;}
    .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:28px;padding:30px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
    .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
    h1{font-size:44px;line-height:1.04;margin:0 0 14px 0;letter-spacing:-0.04em;}
    p{color:#cbd5e1;line-height:1.7;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    form{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap;}
    input{flex:1;min-width:260px;border:1px solid rgba(255,255,255,0.16);background:rgba(255,255,255,0.07);color:white;border-radius:15px;padding:14px 15px;font-size:15px;}
    button{border:0;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;}
    table{width:100%;border-collapse:collapse;margin-top:16px;}
    th,td{text-align:left;padding:13px;border-bottom:1px solid rgba(255,255,255,0.08);vertical-align:top;}
    th{color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
    .muted{color:#94a3b8;}
    @media(max-width:760px){body{padding:24px;}h1{font-size:34px;}table{font-size:14px;}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a href="/">← Back to dashboard</a>
        <div class="card">
            <p class="kicker">Stock Universe</p>
            <h1>Search the full StockRadar universe.</h1>
            <p>{{ total_count }} tickers are loaded from <strong>stock_universe.csv</strong>. The homepage stays fast by only using a small preview; this page handles the full universe.</p>
            <form method="get" action="/universe">
                <input name="q" value="{{ query }}" placeholder="Search ticker or company name, e.g. AAPL or Apple">
                <button type="submit">Search</button>
            </form>
        </div>

        <div class="card">
            <h2>{% if query %}Search results for “{{ query }}”{% else %}Universe preview{% endif %}</h2>
            <p class="muted">Showing {{ visible_rows|length }} of {{ total_count }} loaded tickers.</p>
            <table>
                <tr><th>Stock</th><th>Company</th><th>Sector</th><th>Exchange</th></tr>
                {% for item in visible_rows %}
                <tr>
                    <td><a href="{{ item.url }}">{{ stock_display_label(item.ticker) }}</a></td>
                    <td>{{ item.name }}</td>
                    <td>{{ item.sector }}</td>
                    <td>{{ item.exchange or "—" }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        {{ disclaimer_footer() | safe }}
    </div>
    </body>
    </html>
    """

    return render_template_string(
        universe_html,
        query=query,
        visible_rows=visible_rows,
        total_count=len(all_rows),
    )


@app.route("/premium-decision/<symbol>")
def premium_decision(symbol):
    cleaned_symbol = canonical_stock_symbol(symbol)
    ai_context = get_stock_ai_context(cleaned_symbol)
    report = get_premium_report(cleaned_symbol, ai_context)

    if not owner_has_access():
        locked_html = """
        <!DOCTYPE html>
        <html>
        <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            <a href="/stock/{{ symbol }}">← Back to {{ stock_display_label(symbol) }}</a>
            <div class="card">
                <p class="kicker">Premium Decision Layer</p>
                <h1>{{ stock_display_label(symbol) }} Decision Panel</h1>
                <p>This panel turns a stock signal into a structured decision check: portfolio role, concentration risk, readiness, and what to watch before acting.</p>
                <div class="locked"><strong>Locked:</strong> Upgrade to unlock the full Premium Decision Panel for {{ stock_display_label(symbol) }}.</div>
                <a class="button" href="/upgrade">Unlock Premium</a>
            </div>
            {{ disclaimer_footer() | safe }}
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html, symbol=cleaned_symbol)

    panel_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
        <a href="/stock/{{ symbol }}">← Back to {{ stock_display_label(symbol) }}</a>

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
        {{ disclaimer_footer() | safe }}
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
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            {{ disclaimer_footer() | safe }}
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html)

    watchlist_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
                <div class="box"><strong>Strongest signal</strong>{% if strongest %}<span><a href="/stock/{{ strongest.ticker }}">{{ stock_display_label(strongest.ticker) }}</a> — {{ strongest.signal }} • {{ strongest.confidence }}</span>{% else %}<span>No conviction row available.</span>{% endif %}</div>
                <div class="box"><strong>Highest caution</strong>{% if highest_risk %}<span><a href="/stock/{{ highest_risk.ticker }}">{{ stock_display_label(highest_risk.ticker) }}</a> — {{ highest_risk.signal }} • {{ highest_risk.confidence }}</span>{% else %}<span>No current SELL warning.</span>{% endif %}</div>
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
                <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in quality_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Quality compounder</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Growth and AI satellites</h2>
            <table>
                <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in growth_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Controlled growth satellite</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Defensive balance candidates</h2>
            <table>
                <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in defensive_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Defensive balance</td></tr>
                {% endfor %}
            </table>
                        <div class="note">Premium read: do not just chase the strongest BUY signal. Review whether your next addition improves the overall mix.</div>
            <a class="button" href="/portfolio-fit">Check Portfolio Fit</a>
        </div>
        {{ disclaimer_footer() | safe }}
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
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            {{ disclaimer_footer() | safe }}
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html)

    portfolio_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            <p>{% if tickers %}{% for ticker in tickers %}{{ stock_display_label(ticker) }}{% if not loop.last %}, {% endif %}{% endfor %}{% else %}None detected{% endif %}</p>
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
        {{ disclaimer_footer() | safe }}
    </div>
    </body>
    </html>
    """

    return render_template_string(portfolio_html, holdings_text=holdings_text, result=result)


def safe_history(ticker, **kwargs):
    if yf is None:
        raise RuntimeError("yfinance is not installed")

    stock = yf.Ticker(ticker)
    kwargs.setdefault("auto_adjust", False)

    try:
        return stock.history(**kwargs)
    except TypeError:
        kwargs.pop("timeout", None)
        return stock.history(**kwargs)


def extract_history_price_series(history, symbol):
    if history is None or history.empty:
        return pd.Series(dtype="float64")

    symbol_token = str(symbol or "").strip().upper()

    for field in ("Close", "Adj Close"):
        field_token = field.upper()
        selected_column = None

        if isinstance(history.columns, pd.MultiIndex):
            candidates = []

            for column in history.columns:
                column_parts = tuple(str(part).strip().upper() for part in column)
                if field_token not in column_parts:
                    continue
                candidates.append((0 if symbol_token in column_parts else 1, column))

            if candidates:
                selected_column = sorted(candidates, key=lambda item: item[0])[0][1]
        else:
            for column in history.columns:
                if str(column).strip().upper() == field_token:
                    selected_column = column
                    break

        if selected_column is None:
            continue

        values = history.loc[:, selected_column]
        if isinstance(values, pd.DataFrame):
            values = values.iloc[:, 0]

        numeric_values = pd.to_numeric(values, errors="coerce").dropna()
        if not numeric_values.empty:
            return numeric_values

    return pd.Series(dtype="float64")


def normalize_history_points(history, symbol):
    prices = extract_history_price_series(history, symbol)
    points = []

    for index, value in prices.items():
        date_value = index.isoformat() if hasattr(index, "isoformat") else str(index)
        points.append({
            "date": date_value,
            "label": str(index)[:16],
            "price": round(float(value), 2),
        })

    return points


def money(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "—"


def stock_history(symbol, range_key):
    symbol = canonical_stock_symbol(symbol)
    settings = CHART_RANGES.get(range_key, CHART_RANGES["1mo"])

    try:
        history = safe_history(
            symbol,
            period=settings["period"],
            interval=settings["interval"],
            timeout=6,
        )

        points = normalize_history_points(history, symbol)
        if not points:
            raise ValueError("Live price data is temporarily unavailable for this ticker.")

        labels = [point["label"] for point in points]
        prices = [point["price"] for point in points]

        start = prices[0]
        end = prices[-1]
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
        app.logger.warning("Chart data fetch failed for %s; using fallback chart data.", symbol)
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
    symbol = canonical_stock_symbol(symbol)
    try:
        history = safe_history(symbol, period="max", interval="1mo", timeout=8)
        close = extract_history_price_series(history, symbol)
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
        app.logger.warning("Lifetime chart data fetch failed for %s; using fallback data.", symbol)
        return {
            "start_price": "—",
            "end_price": "—",
            "change_amount": "—",
            "change_percent": "—",
            "direction": "hold",
        }


def fetch_symbol_snapshot(symbol, label, market):
    symbol = canonical_stock_symbol(symbol)
    try:
        history = safe_history(symbol, period="5d", timeout=4)
        close = extract_history_price_series(history, symbol)
        if close.empty:
            raise ValueError("No data")
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
        app.logger.warning("Market snapshot fetch failed for %s; using fallback data.", symbol)
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


def match_news_to_stocks(title, recommendations=None):
    text = f" {str(title or '').lower()} "
    recommendations = recommendations or []

    available_tickers = {
        str(item.get("ticker", "")).strip().upper()
        for item in recommendations
        if item.get("ticker")
    }
    available_tickers.update(TRACKED_STOCK_UNIVERSE)

    expanded_keywords = {
        **NEWS_STOCK_KEYWORDS,
        "AVGO": ["broadcom", "semiconductor", "chips", "vmware"],
        "INTC": ["intel", "semiconductor", "chips", "foundry"],
        "QCOM": ["qualcomm", "snapdragon", "semiconductor", "chips"],
        "ORCL": ["oracle", "cloud"],
        "CRM": ["salesforce"],
        "ADBE": ["adobe"],
        "CSCO": ["cisco", "networking"],
        "IBM": ["ibm", "watson"],
        "NOW": ["servicenow"],
        "SHOP": ["shopify"],
        "UBER": ["uber", "ride-hailing", "rideshare"],
        "BAC": ["bank of america", "banking", "banks"],
        "MS": ["morgan stanley", "banking", "banks"],
        "WFC": ["wells fargo", "banking", "banks"],
        "C": ["citigroup", "citi", "banking", "banks"],
        "V": ["visa", "payments", "credit card"],
        "MA": ["mastercard", "payments", "credit card"],
        "AXP": ["american express", "amex", "payments"],
        "PYPL": ["paypal", "payments", "fintech"],
        "CVX": ["chevron", "oil", "energy", "crude"],
        "COP": ["conocophillips", "oil", "energy", "crude"],
        "SLB": ["slb", "schlumberger", "oil services", "energy"],
        "OXY": ["occidental", "oil", "energy", "crude"],
        "LLOY.L": ["lloyds", "uk banks", "banking"],
        "BARC.L": ["barclays", "uk banks", "banking"],
        "GSK.L": ["gsk", "glaxosmithkline", "pharma", "healthcare"],
        "ULVR.L": ["unilever", "consumer staples"],
        "DGE.L": ["diageo", "consumer staples"],
        "RIO.L": ["rio tinto", "mining", "copper", "iron ore"],
        "BHP.L": ["bhp", "mining", "copper", "iron ore"],
        "VOD.L": ["vodafone", "telecom"],
        "BT-A.L": ["bt", "bt group", "telecom"],
        "TSCO.L": ["tesco", "supermarket", "grocery"],
        "SBRY.L": ["sainsbury", "sainsbury's", "supermarket", "grocery"],
        "LLY": ["eli lilly", "lilly", "zepbound", "mounjaro", "weight loss drug"],
        "JNJ": ["johnson & johnson", "jnj", "healthcare"],
        "PFE": ["pfizer", "pharma", "vaccine"],
        "MRK": ["merck", "pharma"],
        "ABBV": ["abbvie", "pharma"],
        "UNH": ["unitedhealth", "health insurance", "healthcare"],
        "TMO": ["thermo fisher", "life sciences"],
        "ABT": ["abbott", "medical devices", "healthcare"],
        "NVO": ["novo nordisk", "wegovy", "ozempic"],
        "ISRG": ["intuitive surgical", "robotic surgery"],
        "WMT": ["walmart", "retail"],
        "COST": ["costco", "retail"],
        "HD": ["home depot", "housing", "home improvement"],
        "MCD": ["mcdonald", "mcdonald's", "restaurants"],
        "NKE": ["nike", "sportswear"],
        "SBUX": ["starbucks", "coffee"],
        "DIS": ["disney", "streaming", "theme parks"],
        "KO": ["coca-cola", "coke", "consumer staples"],
        "PEP": ["pepsico", "pepsi", "consumer staples"],
        "PG": ["procter", "procter & gamble", "consumer staples"],
        "BA": ["boeing", "aerospace", "aircraft"],
        "CAT": ["caterpillar", "construction equipment"],
        "GE": ["ge aerospace", "general electric"],
        "DE": ["deere", "john deere", "agriculture equipment"],
        "LMT": ["lockheed", "defence", "defense", "missiles"],
        "RTX": ["rtx", "raytheon", "defence", "defense", "missiles"],
        "NOC": ["northrop", "defence", "defense"],
        "HON": ["honeywell", "industrial", "aerospace"],
        "UPS": ["ups", "parcel", "delivery", "logistics"],
        "FDX": ["fedex", "parcel", "delivery", "logistics"],
        "DIA": ["dow jones", "blue chips"],
        "IWM": ["russell 2000", "small caps", "small-cap"],
        "GLD": ["gold", "safe haven"],
        "SLV": ["silver", "precious metals"],
        "USO": ["oil", "crude", "wti", "brent"],
        "TLT": ["treasury yields", "bonds", "long bonds", "rates"],
        "HYG": ["high yield", "junk bonds", "credit spreads"],
        "SOL-USD": ["solana", "crypto", "cryptocurrency"],
    }

    matches = []

    for ticker in available_tickers:
        keywords = list(expanded_keywords.get(ticker, []))

        clean_ticker = ticker.replace(".L", "").replace("-USD", "").replace("^", "")
        if len(clean_ticker) >= 2:
            keywords.append(clean_ticker.lower())

        sector = SECTOR_MAP.get(ticker)
        if sector:
            keywords.append(sector.lower())

        if any(keyword and keyword in text for keyword in keywords):
            matches.append(ticker)

    priority = {
        "SPY": 1,
        "QQQ": 2,
        "SMH": 3,
        "AAPL": 4,
        "MSFT": 5,
        "NVDA": 6,
        "AMZN": 7,
        "GOOGL": 8,
        "META": 9,
        "TSLA": 10,
    }

    matches = sorted(set(matches), key=lambda ticker: (priority.get(ticker, 100), ticker))

    return matches[:12] or ["SPY", "QQQ"]


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
    signal_lookup = build_signal_lookup(recommendations)
    live_articles = fetch_live_market_news()

    for article in live_articles:
        title = str(article.get("title", "")).strip()

        if not title:
            continue

        matched_stocks = match_news_to_stocks(title, recommendations)
        primary_stock = matched_stocks[0] if matched_stocks else "SPY"
        stock_text = ", ".join(matched_stocks)
        direction, signal_influence, impact_score = score_news_impact(title)
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
"stock_links": build_stock_links_with_signals(matched_stocks, signal_lookup),
            "impact_score": impact_score,
            "direction": direction,
            "source": source,
            "published_label": published_label,
            "premium_text": title,
        })

    if headlines:
        return headlines[:8]

    if NEWSAPI_KEY:
        fallback_headlines = []

        for item in impact_radar[:8]:
            stocks = item.get("stocks", []) or ["SPY", "QQQ"]
            headline = item.get("title", "Market impact theme on watch")
            direction = item.get("direction", "Market sensitivity")
            impact_score = item.get("impact_score", item.get("impact", "Pending"))
            theme = item.get("free_view") or item.get("theme") or headline
            primary_stock = stocks[0] if stocks else "SPY"

            fallback_headlines.append({
                "label": "STOCKRADAR THEME",
                "headline": headline,
                "text": theme,
                "url": f"/stock/{primary_stock}",
                "article_url": f"/stock/{primary_stock}",
                "stock_url": f"/stock/{primary_stock}",
                "stock_text": ", ".join(stocks),
                "stock_links": [{"ticker": ticker, "url": f"/stock/{ticker}"} for ticker in stocks],
                "impact_score": impact_score,
                "direction": direction,
                "source": "StockRadar Market Impact Feed",
                "published_label": "Theme watch",
                "premium_text": item.get("premium_view", theme),
            })

        return fallback_headlines or [{
            "label": "STOCKRADAR THEME",
            "headline": "Live market headlines are temporarily unavailable",
            "text": "Live article headlines are temporarily unavailable, so StockRadar is showing market-impact themes.",
            "url": "/news-health",
            "article_url": "/news-health",
            "stock_url": "/stock/SPY",
            "stock_text": "SPY, QQQ",
"stock_links": build_stock_links_with_signals(["SPY", "QQQ"], build_signal_lookup(recommendations)),
            "impact_score": "Pending",
            "direction": "Live feed check needed",
            "source": "StockRadar Market Impact Feed",
            "published_label": "Theme watch",
            "premium_text": "Live article headlines are temporarily unavailable, so StockRadar is showing market-impact themes.",
        }]

    fallback_headlines = []

    for item in impact_radar[:8]:
        stocks = item.get("stocks", []) or ["SPY", "QQQ"]
        headline = item.get("title", "Market impact theme on watch")
        direction = item.get("direction", "Market sensitivity")
        impact_score = item.get("impact_score", item.get("impact", "Pending"))
        theme = item.get("free_view") or item.get("theme") or headline
        primary_stock = stocks[0] if stocks else "SPY"

        fallback_headlines.append({
            "label": "STOCKRADAR THEME",
            "headline": headline,
            "text": theme,
            "url": f"/stock/{primary_stock}",
            "article_url": f"/stock/{primary_stock}",
            "stock_url": f"/stock/{primary_stock}",
            "stock_text": ", ".join(stocks),
            "stock_links": [{"ticker": ticker, "url": f"/stock/{ticker}"} for ticker in stocks],
            "impact_score": impact_score,
            "direction": direction,
            "source": "StockRadar Market Impact Feed",
            "published_label": "Theme watch",
            "premium_text": item.get("premium_view", theme),
        })

    return fallback_headlines or [{
        "label": "STOCKRADAR THEME",
        "headline": "Market headlines are reconnecting",
        "text": "StockRadar is showing market-impact themes while live article headlines reconnect.",
        "url": "/news-health",
        "article_url": "/news-health",
        "stock_url": "/stock/SPY",
        "stock_text": "SPY, QQQ",
"stock_links": build_stock_links_with_signals(["SPY", "QQQ"], signal_lookup),
        "impact_score": "Pending",
        "direction": "Feed health check active",
        "source": "StockRadar Market Impact Feed",
        "published_label": "Theme watch",
        "premium_text": "StockRadar is showing market-impact themes while live article headlines reconnect.",
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
"stock_links": build_stock_links_with_signals(["SPY", "QQQ"], build_signal_lookup(recommendations)),
        "impact_score": "Pending",
        "direction": "Feed health check active",
        "source": "StockRadar News Feed",
        "published_label": "Live check",
        "premium_text": "Market headlines are reconnecting.",
    }]

def prepare_dashboard_data():
    recommendations = get_recommendations()
    buy_rows, hold_rows, sell_rows, conviction_rows = split_rows(recommendations)
    buy_count, hold_count, sell_count, high_conviction_count = calculate_counts(recommendations)

    market_snapshot = [
        fetch_symbol_snapshot("^GSPC", "S&P 500", "US Index"),
        fetch_symbol_snapshot("^IXIC", "Nasdaq Composite", "US Index"),
        fetch_symbol_snapshot("SPY", "SPDR S&P 500 ETF", "US ETF"),
        fetch_symbol_snapshot("QQQ", "Invesco QQQ", "US ETF"),
        fetch_symbol_snapshot("^FTSE", "FTSE 100", "UK Index"),
        fetch_symbol_snapshot("BP.L", "BP", "UK Stock"),
    ]

    impact_radar = get_market_impact_radar()
    live_headlines = safe_build_live_headlines(recommendations, impact_radar) or []

    blocked_news_phrases = (
        "Market headlines are reconnecting",
        "Add NEWSAPI_KEY",
        "NEWSAPI_KEY",
        "NewsAPI key required",
        "Setup needed",
        "Live market headlines are temporarily unavailable",
    )

    live_headlines = [
        item for item in live_headlines
        if isinstance(item, dict)
        and str(item.get("label", "")).upper() == "LIVE NEWS"
        and str(item.get("article_url", "")).startswith("http")
        and not any(
            phrase in str(item.get(field, ""))
            for phrase in blocked_news_phrases
            for field in ("headline", "text", "direction", "published_label")
        )
    ]

    live_news_active = any(
        str(item.get("label", "")).upper() == "LIVE NEWS"
        and str(item.get("article_url", "")).startswith("http")
        for item in live_headlines
    )

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
        "live_headlines": live_headlines,
        "live_news_active": live_news_active,
        "newsapi_configured": bool(NEWSAPI_KEY),
    }

def get_cached_dashboard_data(force_refresh=False):
    now = time.time()
    cached_data = DASHBOARD_CACHE.get("data")
    cached_timestamp = DASHBOARD_CACHE.get("timestamp", 0)

    if (
        not force_refresh
        and isinstance(cached_data, dict)
        and cached_data.get("market_status")
        and now - cached_timestamp < DASHBOARD_CACHE_TTL_SECONDS
    ):
        return cached_data.copy()

    fresh_data = prepare_dashboard_data()

    if not isinstance(fresh_data, dict):
        fresh_data = {}

    DASHBOARD_CACHE["data"] = fresh_data.copy()
    DASHBOARD_CACHE["timestamp"] = now
    return fresh_data.copy()
html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
.tab-button{display:block;border:1px solid transparent;width:100%;text-align:left;cursor:pointer;font-family:inherit;text-decoration:none;appearance:none;-webkit-appearance:none;}
.tab-button.active-tab{background:rgba(0,255,170,0.16);color:white;border:1px solid rgba(0,255,170,0.24);box-shadow:0 12px 32px rgba(0,255,170,0.08);}
.pro-button{background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;font-weight:950;}
.menu-divider{height:1px;background:rgba(255,255,255,0.08);margin:18px 0;}
.menu-help{color:#94a3b8;font-size:12px;line-height:1.55;margin:10px 0 14px 0;}
.owner-box{margin-top:20px;color:#94a3b8;font-size:13px;line-height:1.6;}
.main{flex:1;padding:48px;overflow-y:auto;max-width:1500px;margin:0 auto;} .top-intel-layout{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(320px,0.8fr);gap:18px;align-items:start;margin-bottom:22px;}
.top-intel-layout .live-alert-strip{margin-bottom:0;}
.top-intel-layout .top-bar{position:relative;top:auto;z-index:1;margin:0;padding:0;justify-content:stretch;backdrop-filter:none;}
.top-intel-layout .smart-search{width:100%;}
@media(max-width:1100px){.top-intel-layout{grid-template-columns:1fr;}.top-intel-layout .top-bar{margin-top:0;}}
.top-bar{display:flex;justify-content:flex-end;align-items:center;margin-bottom:22px;gap:14px;position:sticky;top:0;z-index:50;padding:6px 0 12px 0;backdrop-filter:blur(18px);}
.smart-search{width:min(430px,100%);position:relative;}
.smart-search label{display:block;color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.12em;font-weight:900;margin-bottom:8px;}
.smart-search-row{display:flex;gap:10px;background:linear-gradient(135deg,rgba(38,38,38,0.96),rgba(15,23,42,0.92));border:1px solid rgba(255,255,255,0.12);border-radius:20px;padding:9px;box-shadow:0 22px 60px rgba(0,0,0,0.30),0 0 40px rgba(0,255,170,0.05);}
.smart-search input{flex:1;background:transparent;border:none;color:white;font-size:15px;font-weight:700;outline:none;padding:9px 10px;min-width:0;}
.smart-search input::placeholder{color:#64748b;}
.smart-search button{background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border:none;border-radius:14px;padding:10px 15px;font-weight:950;cursor:pointer;}
.search-hint{color:#94a3b8;font-size:12px;margin-top:8px;line-height:1.45;}
.search-message{display:none;margin-top:8px;color:#ffce4a;font-size:13px;font-weight:800;}
.live-alert-strip{position:sticky;top:0;z-index:60;margin-bottom:22px;background:linear-gradient(90deg,rgba(0,255,170,0.12),rgba(56,189,248,0.10),rgba(255,184,107,0.10));border:1px solid rgba(255,255,255,0.12);border-radius:22px;overflow:hidden;box-shadow:0 22px 60px rgba(0,0,0,0.28);backdrop-filter:blur(18px);}
.live-alert-header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.08);font-weight:950;color:white;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
.live-dot{width:9px;height:9px;border-radius:999px;background:#22c55e;box-shadow:0 0 18px rgba(34,197,94,0.8);}
.live-alert-track{display:flex;gap:24px;white-space:nowrap;padding:13px 16px;animation:tickerMove 52s linear infinite;align-items:stretch;}
.live-alert-strip:hover .live-alert-track{animation-play-state:paused;}
.live-headline{display:inline-flex;flex-direction:column;align-items:flex-start;gap:9px;min-width:520px;max-width:620px;color:#e5e7eb;text-decoration:none;font-weight:800;background:rgba(2,6,23,0.35);border:1px solid rgba(255,255,255,0.08);border-radius:18px;padding:14px 16px;white-space:normal;}
.live-headline-main{display:flex;align-items:center;gap:10px;line-height:1.35;}
.live-headline-main a:last-child{color:#e5e7eb;text-decoration:none;}
.live-headline-details{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding-left:2px;}
.live-news-meta{color:#94a3b8;font-size:12px;font-weight:950;text-transform:none;letter-spacing:0.02em;}
.live-news-title{display:block;color:white;font-size:15px;font-weight:950;line-height:1.35;text-decoration:none;}
.live-news-title:hover{color:#ccfbf1;text-decoration:none;}
.live-affected-label{color:#94a3b8;font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.09em;margin-right:2px;}
.live-headline:hover{text-decoration:none;color:white;}
.live-tag{display:inline-block;background:rgba(0,255,170,0.12);border:1px solid rgba(0,255,170,0.20);color:#bbf7d0;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;letter-spacing:0.08em;text-transform:uppercase;}
 .live-premium-tag{display:inline-block;background:rgba(255,184,107,0.14);border:1px solid rgba(255,184,107,0.24);color:#fed7aa;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;letter-spacing:0.08em;text-transform:uppercase;}
.live-meta{display:inline-flex;align-items:center;gap:8px;color:#94a3b8;font-size:12px;font-weight:900;}
.live-score{display:inline-block;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.22);color:#bae6fd;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;}
.live-stock-link{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:950;text-decoration:none;border:1px solid rgba(255,255,255,0.12);text-transform:uppercase;}
.live-stock-link.buy{background:rgba(34,197,94,0.16);border-color:rgba(34,197,94,0.36);color:#bbf7d0;}
.live-stock-link.sell{background:rgba(239,68,68,0.16);border-color:rgba(239,68,68,0.36);color:#fecaca;}
.live-stock-link.hold{background:rgba(245,158,11,0.16);border-color:rgba(245,158,11,0.36);color:#fde68a;}
.live-stock-link::before{content:"";width:7px;height:7px;border-radius:999px;background:currentColor;box-shadow:0 0 12px currentColor;}
.live-stock-action{font-size:10px;opacity:0.88;border-left:1px solid currentColor;padding-left:6px;}
.live-stock-link:hover{filter:brightness(1.12);color:white;text-decoration:none;}
.live-stock-link:hover{background:rgba(0,255,170,0.12);color:white;text-decoration:none;}
@keyframes tickerMove{0%{transform:translateX(0);}100%{transform:translateX(-50%);}}
.card,.market-card{background:linear-gradient(180deg,rgba(23,23,23,0.94),rgba(14,14,14,0.94));padding:28px;border-radius:28px;margin-bottom:22px;border:1px solid rgba(255,255,255,0.10);box-shadow:0 28px 82px rgba(0,0,0,0.35),inset 0 1px 0 rgba(255,255,255,0.07);}
.summary-grid,.market-grid,.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:18px;margin-bottom:22px;}
.feature-grid{grid-template-columns:repeat(3,1fr);}
.summary-card{cursor:pointer;transition:transform 0.18s ease,box-shadow 0.18s ease,border-color 0.18s ease;position:relative;overflow:hidden;border:none;text-align:left;color:white;font-family:inherit;width:100%;}
.summary-card:hover{transform:translateY(-4px);border-color:rgba(0,255,170,0.30);}
.summary-card h2{font-size:42px;margin:0 0 4px 0;}
.summary-card p{color:#94a3b8;margin:0;font-weight:800;}
.market-card small{color:#94a3b8;text-transform:uppercase;letter-spacing:0.10em;font-weight:900;font-size:11px;}
.market-card h3{font-size:20px;margin:10px 0;}
.status-pill{padding:7px 11px;border-radius:999px;font-weight:950;font-size:12px;}
.status-open{background:rgba(34,197,94,0.14);color:#86efac;}
.status-closed{background:rgba(239,68,68,0.14);color:#fca5a5;}
.buy{color:#22c55e;font-weight:bold;}
.sell{color:#ef4444;font-weight:bold;}
.hold{color:#f59e0b;font-weight:bold;}
table{width:100%;border-collapse:collapse;margin-top:16px;}
th,td{text-align:left;padding:13px;border-bottom:1px solid rgba(255,255,255,0.08);vertical-align:top;}
th{color:#94a3b8;text-transform:uppercase;font-size:12px;letter-spacing:0.08em;}
.panel{display:none;}
.panel.open{display:block;animation:fadeIn 0.22s ease;}
.dashboard-section{display:none;}
.dashboard-section.active-section{display:block;animation:fadeIn 0.22s ease;}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:translateY(0);}}
.notice{margin-top:18px;padding:18px;border-radius:18px;background:rgba(0,255,170,0.08);border:1px solid rgba(0,255,170,0.16);}
.notice h3{margin:0 0 8px 0;}
.notice p{color:#cbd5e1;line-height:1.6;}
.upgrade-cta{display:inline-block;margin-top:8px;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;padding:12px 16px;border-radius:14px;font-weight:950;}
.empty-state{color:#94a3b8;padding:18px;background:rgba(255,255,255,0.04);border-radius:16px;margin-top:12px;}
.signal-guide-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px;}
.signal-guide-card{background:rgba(255,255,255,0.055);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;line-height:1.55;}
.signal-guide-card strong{display:block;color:white;margin-bottom:6px;font-size:16px;}
.signal-guide-card span{color:#94a3b8;font-size:13px;}
.premium-signal-callout{margin-top:18px;padding:18px;border-radius:20px;background:linear-gradient(135deg,rgba(0,255,170,0.12),rgba(255,184,107,0.08));border:1px solid rgba(0,255,170,0.18);color:#d1fae5;line-height:1.65;}
.highlight-target{animation:targetPulse 1.4s ease;}
@keyframes targetPulse{0%{box-shadow:0 0 0 0 rgba(0,255,170,0.42);}100%{box-shadow:0 0 0 18px rgba(0,255,170,0);}}
.impact-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:18px;}
.impact-card{background:linear-gradient(180deg,rgba(15,23,42,0.94),rgba(12,12,12,0.94));border:1px solid rgba(255,255,255,0.10);border-radius:24px;padding:22px;box-shadow:0 24px 65px rgba(0,0,0,0.30);}
.impact-card small{display:block;color:#00ffaa;text-transform:uppercase;letter-spacing:0.12em;font-size:11px;font-weight:950;margin-bottom:10px;}
.impact-card h3{font-size:22px;margin:0 0 10px 0;}
.impact-pill{display:inline-block;padding:7px 11px;border-radius:999px;background:rgba(251,191,36,0.12);color:#fde68a;font-weight:950;font-size:12px;text-transform:uppercase;margin-bottom:12px;}
.impact-score{font-size:34px;font-weight:950;color:white;margin:10px 0 4px 0;letter-spacing:-0.04em;}
.impact-direction{color:#94a3b8;font-size:13px;font-weight:900;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:12px;}
.watch-next{margin-top:12px;padding:13px;border-radius:15px;background:rgba(56,189,248,0.09);border:1px solid rgba(56,189,248,0.18);color:#dbeafe;line-height:1.6;}
.radar-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0;}
.radar-summary-card{background:rgba(255,255,255,0.055);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:16px;}
.radar-summary-card strong{display:block;color:white;font-size:22px;margin-bottom:4px;}
.radar-summary-card span{color:#94a3b8;font-size:13px;font-weight:800;}
.impact-card p{color:#cbd5e1;line-height:1.65;}
.impact-stocks{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;}
.impact-stock{display:inline-block;background:rgba(56,189,248,0.10);border:1px solid rgba(56,189,248,0.20);border-radius:999px;padding:7px 10px;color:#bae6fd;font-size:12px;font-weight:900;}
.premium-impact{margin-top:14px;padding:14px;border-radius:16px;background:rgba(0,255,170,0.08);border:1px solid rgba(0,255,170,0.16);color:#d1fae5;line-height:1.6;}
.locked-impact{margin-top:14px;padding:14px;border-radius:16px;background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.18);color:#fecaca;line-height:1.6;}
.filter-panel{margin-top:18px;background:linear-gradient(135deg,rgba(15,23,42,0.88),rgba(5,5,5,0.72));border:1px solid rgba(255,255,255,0.10);border-radius:24px;padding:18px;}
.filter-grid{display:grid;grid-template-columns:1.1fr 0.9fr 0.8fr;gap:14px;align-items:end;}
.filter-control label{display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;font-weight:950;margin-bottom:8px;}
.filter-control input,.filter-control select{width:100%;background:#020617;border:1px solid rgba(255,255,255,0.13);border-radius:15px;color:white;padding:13px 14px;font-weight:800;outline:none;}
.filter-control input::placeholder{color:#64748b;}
.filter-buttons{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px;}
.filter-button{border:1px solid rgba(255,255,255,0.12);background:rgba(255,255,255,0.06);color:white;border-radius:999px;padding:10px 13px;font-weight:950;cursor:pointer;}
.filter-button.active-filter{background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-color:transparent;}
.filter-status{margin-top:12px;color:#94a3b8;font-size:13px;font-weight:800;}
.hidden-signal-row{display:none;}
@media(max-width:900px){body{flex-direction:column;}.sidebar{width:100%;min-height:auto;position:relative;top:auto;}.main{padding:24px;width:100%;}.top-bar{position:relative;justify-content:stretch;}.smart-search{width:100%;}.live-alert-track{animation-duration:58s;}.summary-grid,.market-grid,.feature-grid,.impact-grid,.radar-summary,.signal-guide-grid,.filter-grid{grid-template-columns:1fr;}}
</style>
</head>
<body>
<div class="sidebar">
    <div class="logo">StockRadar</div>
    <div class="nav-section-label">Main Menu</div>
    <div class="menu-help">Use these tabs to jump straight to the section you need.</div>
     <a class="nav-link tab-button {% if active_tab == 'overview' %}active-tab{% endif %}" href="/?tab=overview">🏠 Overview</a>
    <a class="nav-link" href="/beginner">🌱 Investment Compass</a>
    <a class="nav-link tab-button {% if active_tab == 'signals' %}active-tab{% endif %}" href="/?tab=signals">📊 AI Signals</a>
    <a class="nav-link tab-button {% if active_tab == 'radar' %}active-tab{% endif %}" href="/?tab=radar">🌍 Impact Radar</a>
    <a class="nav-link tab-button {% if active_tab == 'watchlist' %}active-tab{% endif %}" href="/?tab=watchlist">📋 AI Watchlist</a>
    <a class="nav-link" href="/premium-watchlist">🧠 Premium Watchlist</a><h2>Risk and concentration check</h2>
        <a class="nav-link" href="/portfolio-fit">🧩 Portfolio Fit</a>
        <a class="nav-link" href="/universe">🌍 Stock Universe</a>
    <div class="menu-divider"></div>

    <div class="nav-section-label">Account</div>
    {% if owner_logged_in %}
        <a class="nav-link pro-button" href="/owner">✅ Premium Active</a>
        <a class="nav-link" href="/logout">🚪 Logout</a>
    {% else %}
        <a class="nav-link pro-button" href="/upgrade">🚀 Upgrade to Pro — £5/month</a>
        <a class="nav-link" href="/login">🔐 Login</a>
    {% endif %}
    <div class="owner-box">Premium unlocks full AI reasoning, risk reads, next-move analysis and market intelligence.</div>
</div>


<div class="main" id="main-content" tabindex="-1">
    <div class="top-intel-layout">
        <div class="live-alert-strip" aria-label="Live market headlines">
            <div class="live-alert-header">
                <span class="live-dot"></span>
                Market News Impact Feed
Updated {{ ticker_updated }}{% if live_news_active %} • Live headlines{% else %} • Theme mode{% endif %}
            </div>
            <div class="live-alert-track">
                {% for headline in live_headlines %}
                <span class="live-headline">
                    <span class="live-news-meta">{{ headline.get('source', 'StockRadar Market Impact Feed') }} • {{ headline.get('published_label', 'Theme watch') }}</span>
                    <a class="live-news-title" href="{{ headline.get('article_url', '/') }}" {% if headline.get('article_url', '').startswith('http') %}target="_blank" rel="noopener noreferrer"{% endif %}>{{ headline.get('headline', 'Market headlines are reconnecting') }}</a>
                    <span class="live-headline-details">
                        <span class="live-affected-label">Affected stocks:</span>
                        {% for stock in headline.get('stock_links', []) %}
                        <a class="live-stock-link {{ stock.get('signal_class', 'hold') }}" href="{{ stock.get('url', '/') }}">{{ stock.get('display_label') or stock_display_label(stock.get('ticker', 'SPY')) }} <span class="live-stock-action">{{ stock.get('action_text', stock.get('signal', 'HOLD')) }}</span></a>
                        {% endfor %}
                    </span>
                    <span class="live-headline-details">
                        <span class="live-score">Impact: {{ headline.get('impact_score', 'Pending') }}</span>
                        <span class="live-meta">{{ headline.get('direction', 'Theme watch') }}</span>
                    </span>
                </span>
                {% endfor %}
                {% for headline in live_headlines %}
                <span class="live-headline">
                    <span class="live-news-meta">{{ headline.get('source', 'StockRadar Market Impact Feed') }} • {{ headline.get('published_label', 'Theme watch') }}</span>
                    <a class="live-news-title" href="{{ headline.get('article_url', '/') }}" {% if headline.get('article_url', '').startswith('http') %}target="_blank" rel="noopener noreferrer"{% endif %}>{{ headline.get('headline', 'Market headlines are reconnecting') }}</a>
                    <span class="live-headline-details">
                        <span class="live-affected-label">Affected stocks:</span>
                        {% for stock in headline.get('stock_links', []) %}
                        <a class="live-stock-link {{ stock.get('signal_class', 'hold') }}" href="{{ stock.get('url', '/') }}">{{ stock.get('display_label') or stock_display_label(stock.get('ticker', 'SPY')) }} <span class="live-stock-action">{{ stock.get('action_text', stock.get('signal', 'HOLD')) }}</span></a>
                        {% endfor %}
                    </span>
                    <span class="live-headline-details">
                        <span class="live-score">Impact: {{ headline.get('impact_score', 'Pending') }}</span>
                        <span class="live-meta">{{ headline.get('direction', 'Theme watch') }}</span>
                    </span>
                </span>
                {% endfor %}
            </div>
        </div>

        <div class="top-bar" aria-label="Quick search and navigation">
            <form class="smart-search" onsubmit="return runSmartSearch(event)">
                <label for="smartSearchInput">Quick Search</label>
                <div class="smart-search-row">
                    <input id="smartSearchInput" type="search" placeholder="Type a ticker, S&P 500, BUY, AI, Pro..." autocomplete="off" aria-label="Type to search stocks, indexes or dashboard sections">
                    <button type="submit">Search</button>
                </div>
                <div class="search-hint">Type and press Enter or Search. Try: Apple, Tesla, Nvidia, Microsoft, S&P 500, Nasdaq, BUY, AI or Pro.</div>
                <div id="searchMessage" class="search-message" role="status"></div>
            </form>
        </div>
    </div>

    <div class="card" id="investment-compass-card">
        <p style="color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;">AI-powered market research</p>
        <h1 style="margin:0 0 12px 0;font-size:clamp(38px,5vw,64px);line-height:0.98;letter-spacing:-0.05em;">Research stocks with clearer signals, context and risk awareness.</h1>
        <p style="color:#cbd5e1;line-height:1.7;max-width:920px;font-size:17px;">StockRadar is an AI-powered stock market research dashboard. Explore stock signals, risk summaries, chart context and portfolio-fit tools in one place—built to help you research more clearly, not tell you what to buy or sell.</p>
        <div style="display:inline-block;margin-top:2px;padding:9px 12px;border-radius:14px;background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.16);color:#bae6fd;font-size:13px;line-height:1.5;"><strong>Early Access:</strong> StockRadar is currently in early access. Premium features and support processes are still being improved.</div>
        <p style="color:#94a3b8;line-height:1.6;max-width:920px;margin-bottom:0;"><strong style="color:#cbd5e1;">Educational and informational only.</strong> StockRadar is not financial advice, and you remain responsible for your own investment decisions.</p>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;">
            <a class="upgrade-cta" href="/universe">Explore Stocks</a>
            <a class="nav-link pro-button" style="display:inline-block;width:auto;margin:0;" href="/upgrade">Unlock Premium</a>
            <a class="nav-link" style="display:inline-block;width:auto;margin:0;background:rgba(255,255,255,0.06);" href="/beginner">New to investing? Start here</a>
            <a class="nav-link" style="display:inline-block;width:auto;margin:0;background:transparent;color:#94a3b8;" href="/feedback">Send Feedback</a>
        </div>
    </div>
    <div id="overview-section" class="dashboard-section {% if active_tab == 'overview' %}active-section{% endif %}">
<div class="card">
    <h2>Current UK & US Market Status</h2>
    <div class="market-grid">
        <div class="market-card">
            <small>UK Market</small>
            <h3>{{ market_status.uk_status }}</h3>
            <p>London time: {{ market_status.uk_time }}</p>
        </div>

        <div class="market-card">
            <small>US Market</small>
            <h3>{{ market_status.us_status }}</h3>
            <p>New York time: {{ market_status.us_time }}</p>
        </div>
    </div>
</div>
    <div class="card">
        <h2>Current UK & US Market Status</h2>
        <p style="color:#94a3b8;line-height:1.7;">
            UK market status: <span class="status-pill {% if market_status.uk_status == 'OPEN' %}status-open{% else %}status-closed{% endif %}">{{ market_status.uk_status }}</span>
            &nbsp; London time: {{ market_status.uk_time }}<br><br>
            US market status: <span class="status-pill {% if market_status.us_status == 'OPEN' %}status-open{% else %}status-closed{% endif %}">{{ market_status.us_status }}</span>
            &nbsp; New York time: {{ market_status.us_time }}
        </p>
    </div>

    <div class="market-grid">
        {% for item in market_snapshot %}
        <div class="market-card">
            <small>{{ item.market }}</small>
            <h3><a class="stock-link" href="/stock/{{ item.symbol }}">{{ stock_display_label(item.symbol) }}</a></h3>
            <p style="margin:0;">Price: <strong>{{ item.price }}</strong></p>
            <p class="{{ item.direction }}" style="margin-bottom:0;">Move: {{ item.change }}</p>
        </div>
        {% endfor %}
    </div>

    </div>

    <div id="signals-section" class="dashboard-section {% if active_tab == 'signals' %}active-section{% endif %}">
    <div class="card">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">Signal Intelligence</p>
        <h2>AI Signal Intelligence Centre</h2>
        <p style="color:#94a3b8;line-height:1.7;">A cleaner view of opportunity, caution and risk. Free users see the signal preview; Premium users can open the full stock page for confidence, risk read and next-move intelligence.</p>
        {% if owner_logged_in %}
        <div class="premium-signal-callout">✅ Premium active: use the linked tickers below to open full AI confidence, risk read and next-move analysis on each stock page.</div>
        {% else %}
        <div class="premium-signal-callout">🔒 Premium unlocks deeper reasoning behind each signal, including risk read, momentum interpretation and what to watch next.</div>
        {% endif %}
    </div>

       <div id="Starter-Buy-Framework" class="card">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">Starter Buy Framework</p>
        <h2>Start with a clear identity before buying complex stocks or ETFs</h2>
        <p style="color:#94a3b8;line-height:1.7;max-width:980px;">For a new investor, the first decision is not “what is hot today?” It is “what kind of investor am I?” StockRadar should use the AI signals below as research prompts, but the starter list should stay simple, diversified and easy to understand.</p>
        <div class="signal-guide-grid">
            <div class="signal-guide-card"><strong>1. Core first</strong><span>Start with a broad market ETF or simple diversified exposure before adding individual companies.</span></div>
            <div class="signal-guide-card"><strong>2. Quality next</strong><span>Look for businesses you understand, with durable demand, strong brands, cash flow or clear market leadership.</span></div>
            <div class="signal-guide-card"><strong>3. Small learning slice</strong><span>Use a controlled percentage for higher-growth names while you build judgement and avoid overconcentration.</span></div>
            <div class="signal-guide-card"><strong>4. Review monthly</strong><span>Do not chase every signal. Build the habit of checking risk, valuation, concentration and thesis drift.</span></div>
        </div>
        <table>
            <tr><th>Starter bucket</th><th>Example research names</th><th>Why it helps beginners</th><th>How to use StockRadar</th></tr>
            <tr>
                <td><strong>Core ETF base</strong></td>
                <td><a class="stock-link" href="/stock/SPY">{{ stock_display_label('SPY') }}</a>, <a class="stock-link" href="/stock/QQQ">{{ stock_display_label('QQQ') }}</a></td>
                <td>Gives diversified market exposure and reduces the pressure to pick the perfect first stock.</td>
                <td>Check whether the broad market is BUY, HOLD or SELL before adding risk.</td>
            </tr>
            <tr>
                <td><strong>Quality compounders</strong></td>
                <td><a class="stock-link" href="/stock/MSFT">{{ stock_display_label('MSFT') }}</a>, <a class="stock-link" href="/stock/AAPL">{{ stock_display_label('AAPL') }}</a>, <a class="stock-link" href="/stock/GOOGL">{{ stock_display_label('GOOGL') }}</a></td>
                <td>Large, understandable businesses can help beginners connect company quality with long-term investing.</td>
                <td>Use confidence, risk read and AI reason to decide whether to research further, not to blindly buy.</td>
            </tr>
            <tr>
                <td><strong>Growth learning names</strong></td>
                <td><a class="stock-link" href="/stock/NVDA">{{ stock_display_label('NVDA') }}</a>, <a class="stock-link" href="/stock/AMZN">{{ stock_display_label('AMZN') }}</a>, <a class="stock-link" href="/stock/META">{{ stock_display_label('META') }}</a></td>
                <td>Shows how growth, AI, cloud and platform businesses behave, but should stay controlled for beginners.</td>
                <td>Only consider if the signal, confidence and risk view line up with your investor profile.</td>
            </tr>
            <tr>
                <td><strong>Defensive balance</strong></td>
                <td><a class="stock-link" href="/stock/KO">{{ stock_display_label('KO') }}</a>, <a class="stock-link" href="/stock/MCD">{{ stock_display_label('MCD') }}</a>, <a class="stock-link" href="/stock/JNJ">{{ stock_display_label('JNJ') }}</a></td>
                <td>Helps beginners see that not every holding needs to be high-growth technology.</td>
                <td>Use HOLD/BUY signals to understand stability, downside risk and portfolio balance.</td>
            </tr>
        </table>
        <div class="premium-signal-callout">Educational only: this is a starter research framework, not personal financial advice. Beginners should avoid putting all money into one stock, one theme or one signal.</div>
    </div>

    <div class="signal-guide-grid">
        <div class="signal-guide-card"><strong class="buy">BUY</strong><span>Opportunity watch: stocks where the scanner sees stronger momentum or improving conviction.</span></div>
        <div class="signal-guide-card"><strong class="hold">HOLD</strong><span>Monitor zone: stocks that need stronger evidence before becoming an opportunity or risk warning.</span></div>
        <div class="signal-guide-card"><strong class="sell">SELL</strong><span>Risk warning: stocks where the scanner is flagging weaker momentum or downside pressure.</span></div>
        <div class="signal-guide-card"><strong>High Conviction</strong><span>The strongest AI-ranked setups by confidence score, designed to guide premium research.</span></div>
    </div>

    <div class="summary-grid">
        <button class="card summary-card" type="button" onclick="togglePanel('buy-panel')" aria-controls="buy-panel" aria-expanded="false"><h2>{{ buy_count }}</h2><p>BUY Signals</p></button>
        <button class="card summary-card" type="button" onclick="togglePanel('hold-panel')" aria-controls="hold-panel" aria-expanded="false"><h2>{{ hold_count }}</h2><p>HOLD Signals</p></button>
        <button class="card summary-card" type="button" onclick="togglePanel('sell-panel')" aria-controls="sell-panel" aria-expanded="false"><h2>{{ sell_count }}</h2><p>SELL Signals</p></button>
        <button class="card summary-card" type="button" onclick="document.getElementById('full-universe-table').scrollIntoView({behavior:'smooth',block:'start'});" aria-controls="full-universe-table"><h2>{{ total_count }}</h2><p>Total Tracked</p></button>
    </div>

    <div id="buy-panel" class="card panel">
        <h2>Opportunity Watch — BUY Signals</h2>
        {% if buy_rows %}
        <table><tr><th>Stock</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in buy_rows %}<tr><td class="buy"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No BUY signals are currently active in your latest scanner output.</div>{% endif %}
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium signal breakdown active</h3><p>You have full premium access. Use the linked tickers above to open the premium stock intelligence pages.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock full AI signal breakdown</h3><p>Pro includes full conviction rankings, live alerts and deeper AI reasoning.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="hold-panel" class="card panel">
        <h2>Monitor Zone — HOLD Signals</h2>
        {% if hold_rows %}
        <table><tr><th>Stock</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in hold_rows %}<tr><td class="hold"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No HOLD signals are currently active.</div>{% endif %}
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium HOLD analysis active</h3><p>You have full premium access to deeper HOLD interpretation and premium stock pages.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock deeper HOLD analysis</h3><p>Pro shows whether HOLD stocks are preparing to flip into BUY or SELL signals.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="sell-panel" class="card panel">
        <h2>Risk Warning — SELL Signals</h2>
        {% if sell_rows %}
        <table><tr><th>Stock</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in sell_rows %}<tr><td class="sell"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No SELL signals are currently active.</div>{% endif %}
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium downside warnings active</h3><p>You have full premium access to downside warnings and premium risk interpretation.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock full downside warnings</h3><p>Pro includes live bearish alerts and AI-driven risk warnings.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="conviction-panel" class="card panel">
        <h2>Premium Focus — Highest AI Conviction</h2>
        <table><tr><th>Stock</th><th>Conviction</th><th>AI Insight</th></tr>{% for item in conviction_rows %}<tr><td class="buy"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium AI-ranked opportunities active</h3><p>You have full premium access to the AI watchlist, conviction engine and premium market intelligence.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock premium conviction intelligence</h3><p>Premium turns High Conviction into a research shortlist with deeper AI reasoning, risk read and what-to-watch-next context on each linked stock page.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="full-universe-table" class="card">
        <h2>Full 100-Stock Signal Universe</h2>
        <p style="color:#94a3b8;line-height:1.7;">The summary counts above are calculated from every tracked stock below, not just the highlighted examples.</p>

        <div class="filter-panel" aria-label="Signal universe filters">
            <div class="filter-grid">
                <div class="filter-control">
                    <label for="tickerFilterInput">Ticker search</label>
                    <input id="tickerFilterInput" type="search" placeholder="Search AAPL, NVDA, BP.L, BTC-USD..." oninput="applySignalFilters()" autocomplete="off">
                </div>
                <div class="filter-control">
                    <label for="sectorFilterSelect">Sector filter</label>
                    <select id="sectorFilterSelect" onchange="applySignalFilters()">
                        <option value="ALL">All sectors</option>
                        {% for sector in sectors %}
                        <option value="{{ sector }}">{{ sector }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="filter-control">
                    <label for="signalFilterValue">Signal filter</label>
                    <select id="signalFilterValue" onchange="setSignalFilter(this.value)">
                        <option value="ALL">All signals</option>
                        <option value="BUY">BUY only</option>
                        <option value="HOLD">HOLD only</option>
                        <option value="SELL">SELL only</option>
                    </select>
                </div>
            </div>
            <div class="filter-buttons" role="group" aria-label="Quick signal filters">
                <button class="filter-button active-filter" type="button" data-signal-filter="ALL" onclick="setSignalFilter('ALL')">All</button>
                <button class="filter-button" type="button" data-signal-filter="BUY" onclick="setSignalFilter('BUY')">BUY</button>
                <button class="filter-button" type="button" data-signal-filter="HOLD" onclick="setSignalFilter('HOLD')">HOLD</button>
                <button class="filter-button" type="button" data-signal-filter="SELL" onclick="setSignalFilter('SELL')">SELL</button>
                <button class="filter-button" type="button" onclick="resetSignalFilters()">Reset filters</button>
            </div>
            <div id="signalFilterStatus" class="filter-status">Showing all {{ total_count }} tracked stocks.</div>
        </div>

        <table>
            <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Sector</th><th>AI Reason</th></tr>
            {% for item in recommendations %}
            <tr class="signal-row" data-ticker="{{ item.ticker }}" data-signal="{{ item.signal }}" data-sector="{{ item.sector or 'AI Watchlist' }}">
                <td><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td>
                <td class="{% if item.signal == 'BUY' %}buy{% elif item.signal == 'SELL' %}sell{% else %}hold{% endif %}">{{ item.signal }}</td>
                <td>{{ item.confidence }}</td>
                <td>{{ item.sector or 'AI Watchlist' }}</td>
                <td>{{ item.reason }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>

    </div>

    <div id="recommendations-section" class="dashboard-section {% if active_tab == 'watchlist' %}active-section{% endif %}">
    <div class="card">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">Watchlist</p>
        <h2>Full AI Watchlist</h2>
        <p style="color:#94a3b8;line-height:1.7;">Browse every stock currently supported by the AI recommendation table and open any stock page directly.</p>
    </div>

    <div class="card">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">Premium Watchlist Intelligence</p>
        <h2>Turn the watchlist into a decision review</h2>
        <p style="color:#94a3b8;line-height:1.7;max-width:980px;">The normal watchlist shows every tracked signal. Premium Watchlist Intelligence turns that into a structured review: strongest current signal, highest caution name, quality bucket, growth satellite bucket, defensive balance and theme concentration.</p>
        <a class="upgrade-cta" href="/premium-watchlist">Open Premium Watchlist</a>
    </div>
    <div class="card" id="watchlist">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">AI Recommendations Page</p>
        <h2>AI Recommendations</h2>
        <p style="color:#94a3b8;line-height:1.7;">This section shows your AI recommendation table. Click any stock to open its live chart page.</p>
        <table>
            <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>AI Reason</th></tr>
            {% for item in recommendations %}
            <tr><td><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td class="{% if item.signal == 'BUY' %}buy{% elif item.signal == 'SELL' %}sell{% else %}hold{% endif %}">{{ item.signal }}</td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>
            {% endfor %}
        </table>
    </div>

    </div>

    <div id="radar-section" class="dashboard-section {% if active_tab == 'radar' %}active-section{% endif %}">
    <div class="card">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">Market Impact</p>
        <h2>Political & Geopolitical Market Intelligence</h2>
        <p style="color:#94a3b8;line-height:1.7;">Track political, geopolitical and regulatory themes that may affect sectors and individual stocks.</p>
    </div>
    <div class="card"><h2>AI Market Brief</h2><p style="color:#cbd5e1;line-height:1.8;">Your latest AI scanner output is feeding this dashboard, while the market snapshot gives visitors a current UK and US context.</p></div>

    <div class="card" id="market-impact-radar">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">Market Intelligence</p>
        <h2>Market Impact Radar</h2>
        <p style="color:#94a3b8;line-height:1.7;">News-style market themes mapped to the stocks they may affect. Free users see concise impact headlines and linked stocks; Premium users unlock impact scores, deeper AI interpretation and what to watch next.</p>
        <div class="radar-summary">
            <div class="radar-summary-card"><strong>{{ impact_radar|length }}</strong><span>tracked themes</span></div>
            <div class="radar-summary-card"><strong>Live</strong><span>stock links</span></div>
            <div class="radar-summary-card"><strong>Free</strong><span>headline view</span></div>
            <div class="radar-summary-card"><strong>Premium</strong><span>impact score + watch next</span></div>
        </div>
        <div class="impact-grid">
            {% for item in impact_radar %}
            <div class="impact-card">
                <small>{{ item.title }}</small>
                <span class="impact-pill">Impact: {{ item.impact }}</span>
                {% if owner_logged_in %}
                    <div class="impact-score">{{ item.impact_score }}</div>
                    <div class="impact-direction">{{ item.direction }}</div>
                    <h3>{{ item.sectors }}</h3>
                    <p>{{ item.free_view }}</p>
                {% else %}
                    <h3>{{ item.title }}</h3>
                    <p>Basic headline: {{ item.impact }} impact theme affecting {{ item.sectors }}.</p>
                {% endif %}
                <div class="impact-stocks">
                    {% for stock in item.stocks %}
                    <a class="impact-stock" href="/stock/{{ stock }}">{{ stock_display_label(stock) }}</a>
                    {% endfor %}
                </div>
                {% if owner_logged_in %}
                    <div class="premium-impact">✅ Premium AI impact: {{ item.premium_view }}</div>
                    <div class="watch-next"><strong>What to watch next:</strong> {{ item.watch_next }}</div>
                {% else %}
                    <div class="locked-impact">🔒 Premium unlocks the AI impact score, full explanation and what to watch next.</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="feature-grid">
        <div class="card"><h2>Free Access</h2><p>Market overview, limited signal previews and AI watchlist snapshots.</p></div>
        {% if owner_logged_in %}<div class="card"><h2>Premium Active</h2><p>Your account has premium access. Upgrade prompts are hidden and premium intelligence is unlocked.</p></div>{% else %}<div class="card"><h2>Pro Preview</h2><p>Live BUY/SELL alerts, conviction scoring and deeper AI explanations.</p></div>{% endif %}
        <div class="card"><h2>Daily Value</h2><p>Use the dashboard to check what is strengthening, weakening and worth watching.</p></div>
    </div>
    </div>
    {{ disclaimer_footer() | safe }}
</div>

<script>
function showDashboardSection(sectionId, button){var sections=document.querySelectorAll('.dashboard-section');sections.forEach(function(section){section.classList.remove('active-section');});var target=document.getElementById(sectionId);if(target){target.classList.add('active-section');target.scrollIntoView({behavior:'smooth',block:'start'});}var buttons=document.querySelectorAll('.tab-button');buttons.forEach(function(btn){btn.classList.remove('active-tab');});if(button){button.classList.add('active-tab');}}
function togglePanel(panelId){var panel=document.getElementById(panelId);var button=document.querySelector('[aria-controls="'+panelId+'"]');if(panel.classList.contains('open')){panel.classList.remove('open');if(button){button.setAttribute('aria-expanded','false');}}else{panel.classList.add('open');if(button){button.setAttribute('aria-expanded','true');}panel.scrollIntoView({behavior:'smooth',block:'start'});}}
function flashTarget(element){if(!element){return;}element.classList.remove('highlight-target');void element.offsetWidth;element.classList.add('highlight-target');}
function openPanelAndJump(panelId){var panel=document.getElementById(panelId);var button=document.querySelector('[aria-controls="'+panelId+'"]');if(!panel){return;}panel.classList.add('open');if(button){button.setAttribute('aria-expanded','true');}panel.scrollIntoView({behavior:'smooth',block:'start'});flashTarget(panel);}
function showSearchMessage(message){var messageBox=document.getElementById('searchMessage');if(!messageBox){return;}messageBox.textContent=message;messageBox.style.display='block';}
function runSmartSearch(event){event.preventDefault();var input=document.getElementById('smartSearchInput');if(!input){return false;}var query=input.value.trim().toUpperCase();if(!query){showSearchMessage('Type a ticker or section name first.');return false;}var map={'APPLE':'AAPL','AAPL':'AAPL','TESLA':'TSLA','TSLA':'TSLA','NVIDIA':'NVDA','NVDA':'NVDA','MICROSOFT':'MSFT','MSFT':'MSFT','AMAZON':'AMZN','AMZN':'AMZN','GOOGLE':'GOOGL','ALPHABET':'GOOGL','META':'META','FACEBOOK':'META','SPCX':'SPCX','SPACEX':'SPCX','SPACE X':'SPCX','SPAX.PVT':'SPCX','S&P 500':'^GSPC','SP500':'^GSPC','S&P':'^GSPC','NASDAQ':'^IXIC','FTSE':'^FTSE','FTSE 100':'^FTSE','HSBC':'HSBA.L','BP':'BP.L','ASTRAZENECA':'AZN.L','SHELL':'SHEL.L'};if(map[query]){window.location.href='/stock/'+encodeURIComponent(map[query]);return false;}if(['AI','RECOMMENDATIONS','AI RECOMMENDATIONS','WATCHLIST'].includes(query)){window.location.href='/?tab=watchlist';return false;}if(['BUY','BUYS','BUY SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=buy-panel';return false;}if(['HOLD','HOLDS','HOLD SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=hold-panel';return false;}if(['SELL','SELLS','SELL SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=sell-panel';return false;}if(['CONVICTION','HIGH CONVICTION','TOP'].includes(query)){window.location.href='/?tab=signals&open=conviction-panel';return false;}if(['POLITICS','POLITICAL','GEOPOLITICS','GEOPOLITICAL','RADAR','MARKET IMPACT','IMPACT RADAR'].includes(query)){window.location.href='/?tab=radar';return false;}
if(['PRO','UPGRADE','PAYMENT','SUBSCRIPTION'].includes(query)){window.location.href='/upgrade';return false;}if(/^[A-Z0-9.^-]{1,12}$/.test(query)){window.location.href='/stock/'+encodeURIComponent(query);return false;}showSearchMessage('No matching stock or section found. Try Apple, AAPL, S&P 500, Nasdaq, BUY, SELL, AI or Pro.');return false;}
function setSignalFilter(signal){var select=document.getElementById('signalFilterValue');if(select){select.value=signal;}document.querySelectorAll('[data-signal-filter]').forEach(function(button){button.classList.toggle('active-filter',button.getAttribute('data-signal-filter')===signal);});applySignalFilters();}
function resetSignalFilters(){var tickerInput=document.getElementById('tickerFilterInput');var sectorSelect=document.getElementById('sectorFilterSelect');if(tickerInput){tickerInput.value='';}if(sectorSelect){sectorSelect.value='ALL';}setSignalFilter('ALL');}
function applySignalFilters(){var tickerInput=document.getElementById('tickerFilterInput');var sectorSelect=document.getElementById('sectorFilterSelect');var signalSelect=document.getElementById('signalFilterValue');var tickerQuery=tickerInput ? tickerInput.value.trim().toUpperCase() : '';var selectedSector=sectorSelect ? sectorSelect.value : 'ALL';var selectedSignal=signalSelect ? signalSelect.value : 'ALL';var rows=document.querySelectorAll('.signal-row');var visibleCount=0;rows.forEach(function(row){var rowTicker=(row.getAttribute('data-ticker')||'').toUpperCase();var rowSignal=row.getAttribute('data-signal')||'';var rowSector=row.getAttribute('data-sector')||'AI Watchlist';var tickerMatch=!tickerQuery || rowTicker.includes(tickerQuery);var signalMatch=selectedSignal==='ALL' || rowSignal===selectedSignal;var sectorMatch=selectedSector==='ALL' || rowSector===selectedSector;var shouldShow=tickerMatch && signalMatch && sectorMatch;row.classList.toggle('hidden-signal-row',!shouldShow);if(shouldShow){visibleCount+=1;}});var status=document.getElementById('signalFilterStatus');if(status){var signalText=selectedSignal==='ALL'?'all signals':selectedSignal+' signals';var sectorText=selectedSector==='ALL'?'all sectors':selectedSector;var tickerText=tickerQuery?(' matching '+tickerQuery):'';status.textContent='Showing '+visibleCount+' stocks for '+signalText+', '+sectorText+tickerText+'.';}}
window.addEventListener('load',function(){var params=new URLSearchParams(window.location.search);var openPanel=params.get('open');if(openPanel){openPanelAndJump(openPanel);}if(window.location.pathname==='/ai-recommendations'){window.location.href='/?tab=watchlist';}applySignalFilters();});
</script>
</body>
</html>
"""


beginner_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Investment Compass — StockRadar</title>
<style>
*{box-sizing:border-box;}
body{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.15),transparent 28%),radial-gradient(circle at 90% 10%,rgba(255,184,107,0.12),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
a{color:#38bdf8;text-decoration:none;font-weight:900;}
a:hover{text-decoration:underline;}
.wrap{max-width:1180px;margin:0 auto;}
.back{display:inline-block;margin-bottom:22px;}
.hero,.card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:32px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
.kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
h1{font-size:46px;line-height:1.04;margin:0 0 16px 0;letter-spacing:-0.04em;}
h2{margin:0 0 12px 0;}
p{color:#cbd5e1;line-height:1.72;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start;}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.field label{display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;font-weight:950;margin-bottom:8px;}
select,input{width:100%;background:#020617;border:1px solid rgba(255,255,255,0.13);border-radius:15px;color:white;padding:14px;font-weight:800;outline:none;}
button,.button{display:inline-block;border:none;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;text-decoration:none;margin-top:16px;}
.result{border:1px solid rgba(0,255,170,0.20);background:linear-gradient(135deg,rgba(0,255,170,0.12),rgba(56,189,248,0.08));}
.model-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:16px;}
.model-box{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:16px;}
.model-box strong{display:block;font-size:26px;margin-bottom:6px;}
.warning{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}
ul{color:#cbd5e1;line-height:1.75;padding-left:20px;}
.tag{display:inline-block;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.22);color:#bae6fd;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:950;margin:4px 6px 4px 0;}
@media(max-width:900px){body{padding:24px;}.grid,.form-grid,.model-grid{grid-template-columns:1fr;}h1{font-size:34px;}}
</style>
</head>
<body>
<div class="wrap">
    <a class="back" href="/">← Back to dashboard</a>
    <div class="hero">
        <p class="kicker">starter investor profile</p>
        <h1>Start with a simple structure before chasing stock picks.</h1>
        <p>Answer five plain-English questions. StockRadar will give you a beginner profile, a starter allocation model and the key risks to understand before using the AI signals dashboard.</p>
        <div><span class="tag">ETF-first thinking</span><span class="tag">Risk guidance</span><span class="tag">Plain English</span><span class="tag">Educational only</span></div>
    </div>
    <div class="grid">
        <div class="card">
            <h2>Build your starter profile</h2>
<form method="POST" action="/beginner#beginner-result">
{% if result %}
<div id="beginner-result" class="card result">
    <p class="kicker">Your beginner profile</p>
    <h2>{{ result.profile }}</h2>
    <p style="color:#00ffaa;font-weight:950;margin-top:-4px;">✅ Beginner plan created successfully.</p>
    <p>{{ result.summary }}</p>             <div class="form-grid">
                    <div class="field"><label for="goal">Main goal</label><select id="goal" name="goal"><option value="growth">Long-term growth</option><option value="income">Income later</option><option value="learning">Learn investing first</option><option value="balanced">Balanced growth and stability</option></select></div>
                    <div class="field"><label for="horizon">Time horizon</label><select id="horizon" name="horizon"><option value="10plus">10+ years</option><option value="5to10">5–10 years</option><option value="2to5">2–5 years</option><option value="short">Under 2 years</option></select></div>
                    <div class="field"><label for="risk">Risk comfort</label><select id="risk" name="risk"><option value="medium">Medium</option><option value="low">Low</option><option value="high">High</option></select></div>
                    <div class="field"><label for="experience">Experience</label><select id="experience" name="experience"><option value="new">Brand new</option><option value="some">Some basics</option><option value="confident">Confident beginner</option></select></div>
                    <div class="field"><label for="amount">Monthly amount</label><input id="amount" name="amount" type="number" min="0" step="10" placeholder="100"></div>
                    <div class="field"><label for="style">Preferred style</label><select id="style" name="style"><option value="simple">Keep it simple</option><option value="stocks">ETFs plus some stocks</option><option value="active">More active research</option></select></div>
                </div>
                <button type="submit">Create beginner plan</button>
            </form>
        </div>
        <div class="card">
            <h2>What beginners should avoid first</h2>
            <ul>
                <li>Buying random stocks because they are trending online.</li>
                <li>Putting short-term savings or emergency cash into volatile shares.</li>
                <li>Owning only one sector, especially only technology.</li>
                <li>Thinking BUY means guaranteed profit or SELL means guaranteed collapse.</li>
                <li>Checking prices every hour when the plan is long-term.</li>
            </ul>
            <div class="warning"><strong>Important:</strong> StockRadar is educational market software, not personal financial advice. Users should make their own decisions or speak to a regulated adviser.</div>
        </div>
    </div>
   {% if result %}
<div id="beginner-result" class="card result">
    <p class="kicker">Your beginner profile</p>
    <h2>{{ result.profile }}</h2>
    <p style="color:#00ffaa;font-weight:950;margin-top:-4px;">✅ Beginner plan created successfully.</p>
    <p>{{ result.summary }}</p>

    <div class="model-grid">
        <div class="model-box"><strong>{{ result.etf }}%</strong><span>Core ETFs</span></div>
        <div class="model-box"><strong>{{ result.quality }}%</strong><span>Quality stocks</span></div>
        <div class="model-box"><strong>{{ result.defensive }}%</strong><span>Defensive names</span></div>
        <div class="model-box"><strong>{{ result.learning }}%</strong><span>Learning picks</span></div>
    </div>

    <h2 style="margin-top:24px;">Next steps</h2>
    <ul>
        {% for step in result.steps %}
        <li>{{ step }}</li>
        {% endfor %}
    </ul>

<a class="button" href="/?tab=signals#Starter-Buy-Framework">Open AI Signals</a>
{% endif %}
    <div class="card"><h2>How this connects to the main dashboard</h2><p>The beginner path gives the user a structure first. The AI signal table then becomes a research tool instead of a gambling screen.</p></div>
</div>
<script>
window.addEventListener('load', function(){
    var result = document.getElementById('beginner-result');
    if(result){
        result.scrollIntoView({behavior:'smooth', block:'start'});
    }
});
</script>
</body>
</html>
"""


def build_beginner_result(form):
    goal = form.get("goal", "growth")
    horizon = form.get("horizon", "10plus")
    risk = form.get("risk", "medium")
    experience = form.get("experience", "new")
    style = form.get("style", "simple")
    amount = form.get("amount", "").strip()

    if horizon == "short":
        return {
            "profile": "Short-term saver, not a stock-market starter yet",
            "summary": "Because your time horizon is under two years, the priority is capital protection and learning. Stocks can move sharply over short periods, so this route should focus on education before risk-taking.",
            "etf": 0,
            "quality": 0,
            "defensive": 0,
            "learning": 0,
            "steps": [
                "Keep emergency money and short-term savings separate from investing money.",
                "Use the dashboard to learn how markets move before putting real money at risk.",
                "Only consider investing money that can stay invested for several years.",
            ],
        }

    if risk == "low" or goal in {"income", "balanced"}:
        profile = "Cautious beginner investor"
        etf, quality, defensive, learning = 75, 10, 10, 5
        summary = "Your route should start with broad diversification, low complexity and small learning positions. The goal is to build confidence without becoming overexposed to single-stock risk."
    elif risk == "high" and horizon in {"10plus", "5to10"} and style in {"stocks", "active"}:
        profile = "Growth-focused beginner investor"
        etf, quality, defensive, learning = 60, 25, 5, 10
        summary = "You can handle more growth exposure, but the core still needs to be diversified. Individual stocks should support the plan, not dominate it."
    else:
        profile = "Balanced long-term beginner investor"
        etf, quality, defensive, learning = 70, 15, 10, 5
        summary = "This is a sensible middle route: a diversified core, a small quality-stock layer and enough flexibility to learn without overtrading."

    monthly_line = f"At around £{amount} per month, automate the core first and keep stock research controlled." if amount else "Decide a monthly amount first, then split it using the model rather than buying randomly."

    steps = [
        monthly_line,
        "Start with the core ETF bucket before adding individual stocks.",
        "Use BUY, HOLD and SELL signals as research prompts, not automatic instructions.",
        "Avoid putting more than a controlled slice into any single stock while learning.",
        "Review monthly instead of reacting to every daily market move.",
    ]

    if experience == "new":
        steps.insert(1, "Read each stock page in plain English before looking at the confidence score.")

    return {
        "profile": profile,
        "summary": summary,
        "etf": etf,
        "quality": quality,
        "defensive": defensive,
        "learning": learning,
        "steps": steps,
    }


@app.route("/beginner", methods=["GET", "POST"])
def beginner():
    result = None
    result_html = ""

    if request.method == "POST":
        result = build_beginner_result(request.form)
        steps_html = "".join(f"<li>{step}</li>" for step in result["steps"])
        result_html = f"""
        <div id="beginner-result" class="card result">
            <p class="kicker">Your beginner profile</p>
            <h2>{result["profile"]}</h2>
            <p style="color:#00ffaa;font-weight:950;margin-top:-4px;">✅ Beginner plan created successfully.</p>
            <p>{result["summary"]}</p>
            <div class="model-grid">
                <div class="model-box"><strong>{result["etf"]}%</strong><span>Core ETFs</span></div>
                <div class="model-box"><strong>{result["quality"]}%</strong><span>Quality stocks</span></div>
                <div class="model-box"><strong>{result["defensive"]}%</strong><span>Defensive names</span></div>
                <div class="model-box"><strong>{result["learning"]}%</strong><span>Learning picks</span></div>
            </div>
            <h2 style="margin-top:24px;">Next steps</h2>
            <ul>{steps_html}</ul>
            <a class="button" href="/?tab=signals#Starter-Buy-Framework">Open AI Signals</a>
           </div>
        """

    page_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Investment Compass — StockRadar</title>
    <style>
    *{{box-sizing:border-box;}}
    html{{scroll-behavior:smooth;}}
    body{{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.15),transparent 28%),radial-gradient(circle at 90% 10%,rgba(255,184,107,0.12),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}}
    a{{color:#38bdf8;text-decoration:none;font-weight:900;}}
    a:hover{{text-decoration:underline;}}
    .wrap{{max-width:1180px;margin:0 auto;}}
    .back{{display:inline-block;margin-bottom:22px;}}
    .hero,.card{{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:32px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}}
    .kicker{{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}}
    h1{{font-size:46px;line-height:1.04;margin:0 0 16px 0;letter-spacing:-0.04em;}}
    h2{{margin:0 0 12px 0;}}
    p{{color:#cbd5e1;line-height:1.72;}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start;}}
    .form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
    .field label{{display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;font-weight:950;margin-bottom:8px;}}
    select,input{{width:100%;background:#020617;border:1px solid rgba(255,255,255,0.13);border-radius:15px;color:white;padding:14px;font-weight:800;outline:none;}}
    button,.button{{display:inline-block;border:none;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;text-decoration:none;margin-top:16px;}}
    .result{{border:1px solid rgba(0,255,170,0.20);background:linear-gradient(135deg,rgba(0,255,170,0.12),rgba(56,189,248,0.08));}}
    .model-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:16px;}}
    .model-box{{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:16px;}}
    .model-box strong{{display:block;font-size:26px;margin-bottom:6px;}}
    .warning{{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}}
    ul{{color:#cbd5e1;line-height:1.75;padding-left:20px;}}
    .tag{{display:inline-block;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.22);color:#bae6fd;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:950;margin:4px 6px 4px 0;}}
    @media(max-width:900px){{body{{padding:24px;}}.grid,.form-grid,.model-grid{{grid-template-columns:1fr;}}h1{{font-size:34px;}}}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a class="back" href="/">← Back to dashboard</a>
        <div class="hero">
            <p class="kicker">starter investor profile</p>
            <h1>Start with a simple structure before chasing stock picks.</h1>
            <p>Answer five plain-English questions. StockRadar will give you a beginner profile, a starter allocation model and the key risks to understand before using the AI signals dashboard.</p>
            <div><span class="tag">ETF-first thinking</span><span class="tag">Risk guidance</span><span class="tag">Plain English</span><span class="tag">Educational only</span></div>
        </div>
        <div class="grid">
            <div class="card">
                <h2>Build your starter profile</h2>
                <form method="POST" action="/beginner#beginner-result">
                    <div class="form-grid">
                        <div class="field"><label for="goal">Main goal</label><select id="goal" name="goal"><option value="growth">Long-term growth</option><option value="income">Income later</option><option value="learning">Learn investing first</option><option value="balanced">Balanced growth and stability</option></select></div>
                        <div class="field"><label for="horizon">Time horizon</label><select id="horizon" name="horizon"><option value="10plus">10+ years</option><option value="5to10">5–10 years</option><option value="2to5">2–5 years</option><option value="short">Under 2 years</option></select></div>
                        <div class="field"><label for="risk">Risk comfort</label><select id="risk" name="risk"><option value="medium">Medium</option><option value="low">Low</option><option value="high">High</option></select></div>
                        <div class="field"><label for="experience">Experience</label><select id="experience" name="experience"><option value="new">Brand new</option><option value="some">Some basics</option><option value="confident">Confident beginner</option></select></div>
                        <div class="field"><label for="amount">Monthly amount</label><input id="amount" name="amount" type="number" min="0" step="10" placeholder="100"></div>
                        <div class="field"><label for="style">Preferred style</label><select id="style" name="style"><option value="simple">Keep it simple</option><option value="stocks">ETFs plus some stocks</option><option value="active">More active research</option></select></div>
                    </div>
                    <button type="submit">Create beginner plan</button>
                </form>
            </div>
            <div class="card">
                <h2>What beginners should avoid first</h2>
                <ul>
                    <li>Buying random stocks because they are trending online.</li>
                    <li>Putting short-term savings or emergency cash into volatile shares.</li>
                    <li>Owning only one sector, especially only technology.</li>
                    <li>Thinking BUY means guaranteed profit or SELL means guaranteed collapse.</li>
                    <li>Checking prices every hour when the plan is long-term.</li>
                </ul>
                <div class="warning"><strong>Important:</strong> StockRadar is educational market software, not personal financial advice.</div>
            </div>
        </div>
        {result_html}
        <div class="card"><h2>How this connects to the main dashboard</h2><p>The beginner path gives the user a structure first. The AI signal table then becomes a research tool instead of a gambling screen.</p></div>
    </div>
    <script>
    window.addEventListener('load', function(){{
        var result = document.getElementById('beginner-result');
        if(result){{result.scrollIntoView({{behavior:'smooth', block:'start'}});}}
    }});
    </script>
    </body>
    </html>
    """

    return page_html

login_html = """
<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Login</title><style>body{background:#020617;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;}form{background:#0f172a;padding:40px;border-radius:20px;width:340px;border:1px solid rgba(255,255,255,0.08);}input{width:100%;padding:14px;margin-bottom:15px;border:none;border-radius:10px;}button{width:100%;padding:14px;background:#38bdf8;border:none;border-radius:10px;color:white;font-weight:bold;cursor:pointer;}a{color:#38bdf8;}</style></head>
<body><form method="POST"><h1>🔐 Login</h1><p style="color:#94a3b8;">Sign in to access your account.</p>{% if login_error %}<p style="background:rgba(239,68,68,0.16);border:1px solid rgba(239,68,68,0.35);color:#fecaca;padding:12px;border-radius:10px;font-weight:bold;">{{ login_error }}</p>{% endif %}<input type="email" name="email" placeholder="Email"><input type="password" name="password" placeholder="Password"><button type="submit">Login</button><p style="color:#94a3b8;font-size:13px;margin-top:20px;">Sign in to continue.</p><p><a href="/">Return to Dashboard</a></p>{{ disclaimer_footer() | safe }}</form></body>
</html>
"""


upgrade_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StockRadar Pro Upgrade</title>
<style>
*{box-sizing:border-box;}
body{background:radial-gradient(circle at 18% 8%,rgba(0,255,170,0.18),transparent 30%),radial-gradient(circle at 86% 12%,rgba(255,184,107,0.14),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;margin:0;min-height:100vh;padding:54px;}
.wrap{max-width:1100px;margin:0 auto;}
.back{color:#38bdf8;text-decoration:none;font-weight:900;display:inline-block;margin-bottom:24px;}
.hero{display:grid;grid-template-columns:1.15fr 0.85fr;gap:24px;align-items:stretch;}
.card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:34px;box-shadow:0 30px 85px rgba(0,0,0,0.45),inset 0 1px 0 rgba(255,255,255,0.07);}
.badge{display:inline-block;color:#00ffaa;background:rgba(0,255,170,0.10);border:1px solid rgba(0,255,170,0.22);padding:9px 13px;border-radius:999px;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;font-size:12px;}
h1{font-size:54px;line-height:0.94;letter-spacing:-0.06em;margin:14px 0 16px 0;background:linear-gradient(135deg,#ffffff,#00ffaa,#ffb86b);-webkit-background-clip:text;color:transparent;}
h2{font-size:28px;margin:0 0 12px 0;}
p{color:#cbd5e1;line-height:1.7;font-size:16px;}
.feature{display:flex;gap:12px;align-items:flex-start;margin:15px 0;color:#e5e7eb;line-height:1.55;}
.tick{color:#00ffaa;font-weight:950;}
.price{font-size:58px;font-weight:950;letter-spacing:-0.06em;margin:10px 0;color:white;}
.price span{font-size:17px;color:#94a3b8;letter-spacing:0;}
.pay-box{background:rgba(5,5,5,0.52);border:1px solid rgba(255,255,255,0.13);border-radius:24px;padding:24px;margin-top:20px;}
.fake-input{width:100%;background:#020617;border:1px solid rgba(255,255,255,0.14);border-radius:16px;padding:15px;color:#94a3b8;margin-bottom:12px;font-weight:800;}
.button{display:inline-block;text-align:center;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;padding:17px 22px;border-radius:18px;text-decoration:none;font-weight:950;margin-top:12px;margin-right:10px;box-shadow:0 22px 60px rgba(0,255,170,0.20);}
.button.secondary{background:rgba(255,255,255,0.08);color:white;border:1px solid rgba(255,255,255,0.13);box-shadow:none;}
.note{font-size:13px;color:#94a3b8;margin-top:14px;line-height:1.55;}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:24px;}
.mini{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;color:#e5e7eb;line-height:1.55;}
.mini strong{display:block;color:white;margin-bottom:6px;}
.active-card{background:linear-gradient(135deg,rgba(0,255,170,0.16),rgba(56,189,248,0.10));border-color:rgba(0,255,170,0.24);}
@media(max-width:850px){body{padding:24px;}.hero,.grid{grid-template-columns:1fr;}h1{font-size:42px;}}
</style>
</head>
<body>
<div class="wrap">
    <a class="back" href="/">← Back to Dashboard</a>

    {% if owner_logged_in %}
    <div class="card active-card">
        <span class="badge">Premium active</span>
        <h1>✅ Premium is already active.</h1>
        <p>You are logged in with premium/owner access. You do not need to purchase again. Premium stock intelligence, risk reads and next-move analysis are unlocked.</p>
        <a class="button" href="/stock/AAPL">Open Premium Stock Page</a>
        <a class="button secondary" href="/">Return to Dashboard</a>
    </div>
    {% else %}
    <div class="hero">
        <div class="card">
            <span class="badge">StockRadar Pro</span>
            <h1>Unlock Premium research tools.</h1>
            <p>Get the deeper StockRadar research layer across individual stocks, your watchlist and your current portfolio structure.</p>
            <div class="feature"><span class="tick">✓</span><span><strong>Premium Decision Panels</strong> — deeper signal context, risk reads, portfolio role and what to watch next.</span></div>
            <div class="feature"><span class="tick">✓</span><span><strong>Premium Watchlist Intelligence</strong> — review strongest signals, caution names and theme concentration.</span></div>
            <div class="feature"><span class="tick">✓</span><span><strong>Portfolio Fit Checker</strong> — classify holdings and identify concentration risks before adding more exposure.</span></div>
            <div class="grid">
                <div class="mini"><strong>Individual stocks</strong>Premium Decision Panels.</div>
                <div class="mini"><strong>Your watchlist</strong>Premium Watchlist Intelligence.</div>
                <div class="mini"><strong>Your holdings</strong>Portfolio Fit Checker.</div>
            </div>
        </div>
        <div class="card">
            <span class="badge">Premium plan</span>
            <div class="price">£5 <span>/ month</span></div>
            <p>One monthly subscription unlocks the full premium research toolkit.</p>
            <div class="note" style="padding:12px;border-radius:14px;background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.16);color:#bae6fd;"><strong>Early Access:</strong> StockRadar is currently in early access. Premium features and support processes are still being improved.</div>
            <p class="note">£5/month early access premium subscription. Cancellation requests are handled through <a href="/manage-subscription">Manage Subscription</a> while self-service billing is being built.</p>
            <p class="note"><strong style="color:#cbd5e1;">Educational only.</strong> Premium provides research tools and analysis—not financial advice or personalised investment recommendations.</p>
            <div class="pay-box">
                <p class="note">Premium access provides research tools and analysis only. StockRadar is not financial advice.</p>
                <form method="POST" action="/create-checkout-session">
                    <button class="button" type="submit" style="border:none;cursor:pointer;width:100%;">Start Premium with Stripe Checkout</button>
                </form>
                <div class="note">Secure payment is handled by Stripe Checkout. Use Stripe test mode first.</div>
                <div class="note">Need to cancel later? Visit <a href="/manage-subscription">Manage Subscription</a>. Early access cancellations are handled through support until self-service billing management is added.</div>
                <div class="note"><a href="/feedback">Send Feedback</a> about the upgrade experience while StockRadar is in early access.</div>
            </div>
        </div>
    </div>
    {% endif %}
    {{ disclaimer_footer() | safe }}
</div>
</body>
</html>
"""


owner_html = """
<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Owner Area</title><style>body{background:#020617;color:white;font-family:Arial;margin:0;padding:60px;}.card{background:#0f172a;padding:40px;border-radius:24px;max-width:820px;margin:auto;border:1px solid rgba(255,255,255,0.08);}a{color:#38bdf8;font-weight:bold;}</style></head><body><div class="card"><h1>👑 Owner Area</h1><p>You are logged in as the owner with premium access.</p><p>This confirms login and premium unlocking are working.</p><p><a href="/">Return to Dashboard</a></p><p><a href="/stock/AAPL">Open Premium {{ stock_display_label('AAPL') }} Page</a></p>{{ disclaimer_footer() | safe }}</div></body></html>
"""


stock_detail_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ stock_display_label(symbol) }} Stock Detail</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{box-sizing:border-box;}body{background:radial-gradient(circle at 12% 6%,rgba(0,255,170,0.18),transparent 28%),linear-gradient(135deg,#050505,#121212,#1f2933);color:white;font-family:Arial,sans-serif;margin:0;min-height:100vh;padding:48px;}.card{background:linear-gradient(180deg,rgba(23,23,23,0.94),rgba(14,14,14,0.94));padding:32px;border-radius:30px;margin-bottom:24px;border:1px solid rgba(255,255,255,0.10);box-shadow:0 28px 82px rgba(0,0,0,0.44);}a{color:#38bdf8;text-decoration:none;font-weight:bold;}.range-row{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0;}.range-button{display:inline-block;padding:13px 17px;border-radius:16px;background:rgba(30,41,59,0.78);color:white;text-decoration:none;border:1px solid rgba(255,255,255,0.07);font-weight:800;}.range-button.active{background:linear-gradient(90deg,#38bdf8,#8b5cf6);}.metric-grid,.ai-grid,.example-report-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:22px;}.metric-grid{grid-template-columns:repeat(4,1fr);}.ai-card,.metric,.example-report-card{background:rgba(15,23,42,0.86);border:1px solid rgba(255,255,255,0.11);border-radius:24px;padding:24px;}.ai-card.warning{background:linear-gradient(145deg,rgba(251,191,36,0.13),rgba(17,24,39,0.92));}.ai-card.risk{background:linear-gradient(145deg,rgba(56,189,248,0.12),rgba(17,24,39,0.92));}.premium-banner,.example-report{background:linear-gradient(135deg,rgba(0,255,170,0.18),rgba(255,184,107,0.12),rgba(56,189,248,0.10));border:1px solid rgba(0,255,170,0.22);border-radius:30px;padding:30px;margin-bottom:24px;}.premium-banner{display:grid;grid-template-columns:1.45fr 0.75fr;gap:24px;align-items:center;}.premium-cta-box{background:rgba(5,5,5,0.58);border:1px solid rgba(255,255,255,0.15);border-radius:24px;padding:22px;text-align:center;}.payment-button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:20px;padding:16px 24px;font-size:16px;font-weight:950;text-decoration:none;}.payment-note{color:#94a3b8;font-size:13px;margin-top:12px;}.signal-badge,.free-strength,.strength-pill{display:inline-block;margin-top:10px;padding:8px 12px;border-radius:999px;background:rgba(255,255,255,0.08);font-weight:900;font-size:12px;text-transform:uppercase;}.confidence-large,.confidence-score{font-size:40px;font-weight:950;}.free-meter,.confidence-meter{font-size:26px;letter-spacing:2px;color:#00ffaa;font-weight:950;margin:8px 0;}.buy{color:#22c55e;font-weight:bold;}.sell{color:#ef4444;font-weight:bold;}.hold{color:#f59e0b;font-weight:bold;}canvas{background:#020617;border-radius:18px;padding:18px;}@media(max-width:900px){body{padding:24px;}.metric-grid,.ai-grid,.premium-banner,.example-report-grid{grid-template-columns:1fr;}}
</style>
</head>
<body>
<div class="card"><p><a href="/">← Back to Dashboard</a></p><h1>{{ stock_display_label(symbol) }} Stock Detail</h1><p style="color:#94a3b8;">Live chart view for {{ range_label }}. Use the buttons below to change timeframe.</p></div>

<div class="premium-banner"><div><small>Premium AI Intelligence Preview</small><h2>{{ stock_display_label(symbol) }} intelligence, not just a chart.</h2><p>Every supported stock and index gets the same structure: a useful free preview, then a stronger Premium decision panel with deeper AI explanation, risk read, portfolio role and what to watch next.</p></div><div class="premium-cta-box">{% if has_premium_access %}<strong>✅ Premium Active</strong><p>You have full premium access for {{ stock_display_label(symbol) }}.</p><a class="payment-button" href="/premium-decision/{{ symbol }}">Open Decision Panel</a>{% else %}<strong>Unlock the full {{ stock_display_label(symbol) }} Decision Panel</strong><p>Premium adds portfolio role, concentration risk, readiness and before-acting checks.</p><a class="payment-button" href="/premium-decision/{{ symbol }}">Preview Premium Panel</a><div class="payment-note">Non-premium users see the locked preview and upgrade route.</div>{% endif %}</div></div>

<div class="ai-grid"><div class="ai-card"><small>Free Signal Preview</small><h2 class="{% if ai_context.signal == 'BUY' %}buy{% elif ai_context.signal == 'SELL' %}sell{% elif ai_context.signal == 'HOLD' %}hold{% endif %}">{{ ai_context.signal }}</h2><p>Every supported stock page gets the same free AI preview. Current signal for {{ stock_display_label(symbol) }}: {{ ai_context.signal }}.</p><span class="signal-badge">Live stock page: {{ stock_display_label(symbol) }}</span></div><div class="ai-card warning"><small>Free Confidence Preview</small><div class="confidence-large">{{ ai_context.confidence }}</div><div class="free-meter">{{ ai_context.confidence_meter }}</div><span class="free-strength">Signal strength: {{ ai_context.strength_label }}</span><p style="margin-top:12px;">Free shows the basic score and meter. Pro explains what is driving it for {{ stock_display_label(symbol) }}.</p></div><div class="ai-card risk"><small>{% if has_premium_access %}Premium Active{% else %}Pro Preview{% endif %}</small><h2>Next Move</h2>{% if has_premium_access %}<p>{{ ai_context.watch_next }}</p><span class="signal-badge">Premium unlocked</span>{% else %}<p>Pro unlocks the full interpretation behind the meter: why the score matters, what risk is building and what to watch next for {{ stock_display_label(symbol) }}.</p><a class="signal-badge" href="/upgrade">Unlock Premium</a>{% endif %}</div></div>

{% if has_premium_access and example_report %}<div class="example-report"><small>Premium Decision Intelligence</small><h2>{{ example_report.headline }}</h2><p>{{ example_report.summary }}</p><div class="example-report-grid"><div class="example-report-card"><strong>AI Confidence</strong><div class="confidence-score">{{ example_report.confidence }}</div><div class="confidence-meter">{{ example_report.meter }}</div><span class="strength-pill">Signal strength: {{ example_report.strength }}</span></div><div class="example-report-card"><strong>Portfolio role</strong><span>{{ example_report.portfolio_role }}</span></div><div class="example-report-card"><strong>Decision readiness</strong><span>{{ example_report.readiness }}</span></div></div><div class="example-report-card" style="margin-top:16px;"><strong>Premium decision use</strong><span>{{ example_report.decision_use }}</span></div><div style="margin-top:18px;"><a class="payment-button" href="/premium-decision/{{ symbol }}">Open Full Premium Decision Panel</a></div></div>{% endif %}
{% if not has_premium_access %}<div class="example-report"><small>Premium locked</small><h2>Unlock the full {{ stock_display_label(symbol) }} Decision Panel</h2><p>Free shows the basic signal, confidence score and meter. Premium unlocks portfolio role, concentration risk, readiness and before-acting checks.</p><div class="example-report-grid"><div class="example-report-card"><strong>Free preview</strong><div class="confidence-score">{{ ai_context.confidence }}</div><div class="confidence-meter">{{ ai_context.confidence_meter }}</div><span class="strength-pill">Basic signal strength: {{ ai_context.strength_label }}</span></div><div class="example-report-card"><strong>Premium portfolio role</strong><span>Locked until upgrade.</span></div><div class="example-report-card"><strong>Premium decision readiness</strong><span>Locked until upgrade.</span></div></div><a class="payment-button" href="/premium-decision/{{ symbol }}" style="margin-top:18px;">Preview Premium Decision Panel</a><div class="payment-note">The preview opens the locked Premium route and upgrade path.</div></div>{% endif %}

<div class="range-row">{% for key, settings in chart_ranges.items() %}<a class="range-button {% if key == active_range %}active{% endif %}" href="/stock/{{ symbol }}?range={{ key }}">{{ settings.label }}</a>{% endfor %}</div>
<div class="metric-grid"><div class="metric"><small>Range start</small><h2>{{ chart_data.start_price }}</h2></div><div class="metric"><small>Range latest</small><h2>{{ chart_data.end_price }}</h2></div><div class="metric"><small>Range move</small><h2 class="{{ chart_data.direction }}">{{ chart_data.change_amount }}</h2></div><div class="metric"><small>Range % move</small><h2 class="{{ chart_data.direction }}">{{ chart_data.change_percent }}</h2></div></div>
<div class="card">{% if chart_data.ok %}<canvas id="stockChart" height="120"></canvas>{% else %}<h2>Chart unavailable</h2><p style="color:#fca5a5;">{{ chart_data.error }}</p>{% endif %}</div>
<div class="card"><h2>Since market data began</h2><div class="metric-grid"><div class="metric"><small>Earliest available price</small><h2>{{ lifetime.start_price }}</h2></div><div class="metric"><small>Latest available price</small><h2>{{ lifetime.end_price }}</h2></div><div class="metric"><small>Total growth / decrease</small><h2 class="{{ lifetime.direction }}">{{ lifetime.change_amount }}</h2></div><div class="metric"><small>Total % growth / decrease</small><h2 class="{{ lifetime.direction }}">{{ lifetime.change_percent }}</h2></div></div></div>
{{ disclaimer_footer() | safe }}
<script>
const labels={{ chart_data.labels | tojson }};
const prices={{ chart_data.prices | tojson }};
if(labels.length>0){
    const ctx=document.getElementById('stockChart');
    new Chart(ctx,{type:'line',data:{labels:labels,datasets:[{label:'{{ stock_display_label(symbol) }} close price',data:prices,borderWidth:2,tension:0.25}]},options:{responsive:true,plugins:{legend:{labels:{color:'white'}}},scales:{x:{ticks:{color:'#94a3b8',maxTicksLimit:8},grid:{color:'rgba(255,255,255,0.08)'}},y:{ticks:{color:'#94a3b8'},grid:{color:'rgba(255,255,255,0.08)'}}}}});
}
</script>
</body>
</html>
"""

# --- Health and diagnostics routes ---
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "StockRadar",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "newsapi_configured": bool(NEWSAPI_KEY),
        "dashboard_cache_configured": True,
        "stripe_configured": stripe_checkout_configured(),
        "owner_login_configured": owner_login_configured(),
        "stock_universe_csv": STOCK_UNIVERSE_CSV,
        "stock_universe_cache_ttl_seconds": STOCK_UNIVERSE_CACHE_TTL_SECONDS,
        "dashboard_cache_ttl_seconds": DASHBOARD_CACHE_TTL_SECONDS,
    })


@app.route("/news-health")
def news_health():
    articles = fetch_live_market_news(limit=8)
    return {
        "newsapi_configured": bool(NEWSAPI_KEY),
        "live_articles_returned": len(articles),
        "mode": "live_news" if articles else "no_live_articles",
        "provider": LAST_NEWS_FETCH_STATUS.get("provider"),
        "status": LAST_NEWS_FETCH_STATUS.get("status"),
        "errors": LAST_NEWS_FETCH_STATUS.get("errors", []),
        "sample_headlines": [article.get("title") for article in articles],
        "sources": [article.get("source") for article in articles],
    }, 200


@app.route("/healthz")
def healthz():
    return {
        "status": "ok",
        "app": "StockRadar",
        "stripe_configured": stripe_checkout_configured(),
        "owner_login_configured": owner_login_configured(),
    }, 200

@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/privacy")
def privacy():
    return render_legal_page(
        "Privacy Policy",
        """
        <p>StockRadar uses the minimum information needed to operate the service, provide requested features, maintain security, and support customers.</p>
        <h2>Information we may process</h2>
        <p>This may include account or support details you provide, session information needed for login and premium access, and technical logs used to keep the service reliable and secure.</p>
        <h2>Payments</h2>
        <p>Payments are handled by Stripe. StockRadar does not store full payment-card details.</p>
        <h2>Your choices</h2>
        <p>You may contact support to ask about personal information associated with your use of StockRadar, subject to applicable legal requirements.</p>
        """,
    )


@app.route("/terms")
def terms():
    return render_legal_page(
        "Terms of Use",
        """
        <p>By using StockRadar, you agree to use the service lawfully and for educational and informational research purposes.</p>
        <h2>No investment advice</h2>
        <p>StockRadar does not provide regulated financial advice or guarantee investment outcomes. Market information can be delayed, incomplete, or unavailable.</p>
        <h2>Subscriptions</h2>
        <p>Premium features require an active subscription. You are responsible for providing accurate payment and contact information.</p>
        <h2>Service availability</h2>
        <p>Features and data providers may change, pause, or become unavailable. We may update these terms as the service develops.</p>
        """,
    )


@app.route("/refund-policy")
def refund_policy():
    if SUPPORT_EMAIL:
        support_contact = render_template_string(
            '<a href="mailto:{{ support_email }}">{{ support_email }}</a>',
            support_email=SUPPORT_EMAIL,
        )
    else:
        support_contact = "support contact coming soon"

    return render_legal_page(
        "Refund Policy",
        f"""
        <p>Subscriptions can be cancelled through support while self-service cancellation is being built.</p>
        <p>Refund requests can be sent to: {support_contact}</p>
        <p>Refunds are reviewed case by case.</p>
        <p>This does not affect statutory rights.</p>
        """,
    )


@app.route("/risk-disclaimer")
def risk_disclaimer():
    return render_legal_page(
        "Risk Disclaimer",
        """
        <p><strong>StockRadar provides educational and informational market research only. It does not provide regulated financial advice, personalised investment recommendations, brokerage services, or trade execution. Users are responsible for their own investment decisions.</strong></p>
        <p>Investing involves risk. Prices can rise or fall, historical performance does not guarantee future results, and you may lose some or all of the money invested.</p>
        <p>Market data, signals, confidence scores, and analysis may be delayed, incomplete, or incorrect. Consider your circumstances and seek advice from a suitably regulated professional where appropriate.</p>
        """,
    )


@app.route("/contact")
def contact():
    if SUPPORT_EMAIL:
        support_content = """
        <p>For account, subscription, cancellation, refund, or general support, email <a href="mailto:{{ support_email }}">{{ support_email }}</a>.</p>
        """
    else:
        support_content = """
        <p>Support contact coming soon.</p>
        """

    content = render_template_string(support_content, support_email=SUPPORT_EMAIL)
    return render_legal_page("Contact", content)


@app.route("/manage-subscription")
def manage_subscription():
    if SUPPORT_EMAIL:
        support_contact = render_template_string(
            '<a href="mailto:{{ support_email }}">{{ support_email }}</a>',
            support_email=SUPPORT_EMAIL,
        )
    else:
        support_contact = "support contact coming soon"

    return render_legal_page(
        "Manage Subscription",
        f"""
        <p>StockRadar Premium is currently in early access.</p>
        <p>Self-service subscription management is being built. Until then, users can request cancellation through support.</p>
        <h2>How to cancel</h2>
        <p>Contact {support_contact} using the email address used at checkout and the subject line: <strong>Cancel StockRadar Premium</strong>.</p>
        <h2>Refund requests</h2>
        <p>Refund requests are reviewed case by case. This does not affect statutory rights.</p>
        <h2>Educational use</h2>
        <p>StockRadar is educational and informational only and does not provide regulated financial advice.</p>
        """,
    )


@app.route("/feedback")
def feedback():
    if SUPPORT_EMAIL:
        support_contact = render_template_string(
            '<a href="mailto:{{ support_email }}?subject=StockRadar%20Feedback">{{ support_email }}</a>',
            support_email=SUPPORT_EMAIL,
        )
    else:
        support_contact = "support contact coming soon"

    return render_legal_page(
        "StockRadar Feedback",
        f"""
        <p>StockRadar is in early access. Feedback helps improve the dashboard before wider public launch.</p>
        <p>Send feedback to: {support_contact}</p>
        <h2>What to include</h2>
        <ul>
            <li>What page or feature you tested</li>
            <li>What worked well</li>
            <li>What felt confusing, broken, slow, or unclear</li>
            <li>What stock or ticker you searched for, if relevant</li>
            <li>Whether the upgrade flow, legal links, and support pages were easy to understand</li>
        </ul>
        """,
    )


@app.route("/")
def dashboard():
    active_tab = request.args.get("tab", "overview").strip().lower()
    quick_search_query = request.args.get("q", "").strip()
    quick_search_results = search_stock_universe(quick_search_query) if quick_search_query else []

    if active_tab not in {"overview", "signals", "radar", "watchlist"}:
        active_tab = "overview"

    data = get_cached_dashboard_data(force_refresh=request.args.get("refresh") == "1") or {}

    if not isinstance(data, dict) or not data.get("market_status"):
        data = prepare_dashboard_data() or {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("recommendations", [])
    data.setdefault("buy_rows", [])
    data.setdefault("hold_rows", [])
    data.setdefault("sell_rows", [])
    data.setdefault("conviction_rows", [])
    data.setdefault("buy_count", 0)
    data.setdefault("hold_count", 0)
    data.setdefault("sell_count", 0)
    data.setdefault("total_count", 0)
    data.setdefault("sectors", [])
    data.setdefault("high_conviction_count", 0)
    data.setdefault("market_snapshot", [])
    data.setdefault("market_status", market_status())
    data.setdefault("last_updated", datetime.now().strftime("%d %b %Y, %H:%M"))
    data.setdefault("ticker_updated", datetime.now().strftime("%H:%M"))
    data.setdefault("impact_radar", [])
    data.setdefault("live_headlines", [])

    data["live_headlines"] = [
        item for item in data.get("live_headlines", [])
        if str(item.get("label", "")).upper() == "LIVE NEWS"
        and str(item.get("article_url", "")).startswith("http")
    ]

    data.setdefault("newsapi_configured", bool(NEWSAPI_KEY))
    data["live_news_active"] = any(
        str(item.get("label", "")).upper() == "LIVE NEWS"
        and str(item.get("article_url", "")).startswith("http")
        for item in data.get("live_headlines", [])
    )
    data["active_tab"] = active_tab
    data["quick_search_query"] = quick_search_query
    data["quick_search_results"] = quick_search_results
    data["universe_preview"] = get_stock_universe()[:12]

    return render_template_string(html, **data)

@app.route("/ai-recommendations")
def ai_recommendations():
    return redirect(url_for("dashboard", tab="watchlist"))


@app.route("/stock/<path:symbol>")
def stock_detail(symbol):
    requested_symbol = symbol.strip().upper()
    cleaned_symbol = canonical_stock_symbol(symbol)
    active_range = request.args.get("range", "1mo")

    if active_range not in CHART_RANGES:
        active_range = "1mo"

    if cleaned_symbol != requested_symbol:
        return redirect(url_for("stock_detail", symbol=cleaned_symbol, range=active_range))

    chart_data = stock_history(cleaned_symbol, active_range)
    lifetime = stock_lifetime_growth(cleaned_symbol)
    ai_context = get_stock_ai_context(cleaned_symbol)
    example_report = get_premium_report(cleaned_symbol, ai_context)

    return render_template_string(
        stock_detail_html,
        symbol=cleaned_symbol,
        active_range=active_range,
        range_label=CHART_RANGES[active_range]["label"],
        chart_ranges=CHART_RANGES,
        chart_data=chart_data,
        lifetime=lifetime,
        ai_context=ai_context,
        example_report=example_report,
        has_premium_access=owner_has_access(),
    )


@app.route("/upgrade")
def upgrade():
    return render_template_string(upgrade_html, owner_logged_in=owner_has_access())


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    if not stripe_checkout_configured():
        return render_template_string("""
<!doctype html>
<html>
<head>
    <title>Stripe Setup Needed | StockRadar</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{margin:0;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}
        .box{max-width:760px;background:rgba(15,23,42,0.94);border:1px solid rgba(248,113,113,0.30);border-radius:28px;padding:34px;box-shadow:0 24px 80px rgba(0,0,0,0.45);}
        h1{margin:0 0 14px;font-size:32px;}
        p{color:#cbd5e1;line-height:1.7;font-size:16px;}
        code{display:block;background:#020617;color:#bae6fd;border:1px solid rgba(148,163,184,0.22);border-radius:14px;padding:14px;white-space:pre-wrap;margin:14px 0;}
        a{display:inline-block;margin-top:18px;padding:14px 18px;border-radius:16px;background:#00ffaa;color:#020617;text-decoration:none;font-weight:900;}
    </style>
</head>
<body>
    <div class="box">
        <h1>Stripe setup needed</h1>
        <p>Stripe Checkout is connected, but payment credentials are not configured yet. The dashboard is still live and safe to share.</p>
        <code>Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID in your hosting environment when ready.</code>
        <p>Use Stripe test mode first, then switch to live keys only after your Stripe account is verified.</p>
        <a href="/upgrade">Back to upgrade page</a>
    </div>
</body>
</html>
        """), 400

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=STRIPE_SUCCESS_URL or url_for("checkout_success", _external=True),
            cancel_url=STRIPE_CANCEL_URL or url_for("upgrade", _external=True),
            customer_email=OWNER_EMAIL or None,
            allow_promotion_codes=True,
        )
        return redirect(checkout_session.url, code=303)
    except Exception as exc:
        return render_template_string("""
<!doctype html>
<html>
<head>
    <title>Stripe Checkout Paused | StockRadar</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{margin:0;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}
        .box{max-width:780px;background:rgba(15,23,42,0.94);border:1px solid rgba(248,113,113,0.30);border-radius:28px;padding:34px;box-shadow:0 24px 80px rgba(0,0,0,0.45);}
        h1{margin:0 0 14px;font-size:32px;}
        p{color:#cbd5e1;line-height:1.7;font-size:16px;}
        code{display:block;background:#020617;color:#fecaca;border:1px solid rgba(248,113,113,0.24);border-radius:14px;padding:14px;white-space:pre-wrap;margin:14px 0;}
        a{display:inline-block;margin-top:18px;padding:14px 18px;border-radius:16px;background:#00ffaa;color:#020617;text-decoration:none;font-weight:900;}
    </style>
</head>
<body>
    <div class="box">
        <h1>Stripe Checkout paused</h1>
        <p>The checkout route is connected, but Stripe rejected the current payment setup. This does not affect the dashboard.</p>
        <code>{{ error }}</code>
        <p>When Stripe credentials are ready, update the environment variables and restart the app.</p>
        <a href="/upgrade">Back to upgrade page</a>
    </div>
</body>
</html>
        """, error=str(exc)), 400

@app.route("/checkout-success")
def checkout_success():
    checkout_session_id = request.args.get("session_id", "").strip()
    premium_activated = False
    heading = "Payment verification required"
    message = "Premium access has not been activated. Please complete checkout from the upgrade page."
    response_status = 400

    if not stripe_checkout_configured():
        heading = "Payment verification unavailable"
        message = "Premium access has not been activated because Stripe Checkout is not configured."
        response_status = 503
    elif not checkout_session_id:
        message = "Premium access has not been activated because the Stripe checkout session is missing."
    else:
        try:
            verified_session = stripe.checkout.Session.retrieve(checkout_session_id)
            if isinstance(verified_session, dict):
                payment_status = str(verified_session.get("payment_status") or "").lower()
                checkout_status = str(verified_session.get("status") or "").lower()
            else:
                payment_status = str(getattr(verified_session, "payment_status", "") or "").lower()
                checkout_status = str(getattr(verified_session, "status", "") or "").lower()

            payment_verified = (
                payment_status == "paid"
                if payment_status
                else checkout_status == "complete"
            )

            if payment_verified:
                session["owner_logged_in"] = True
                premium_activated = True
                heading = "✅ Premium activated"
                message = "Your verified premium dashboard session is now active."
                response_status = 200
            else:
                heading = "Payment pending"
                message = "Premium access has not been activated because Stripe has not confirmed payment."
        except Exception:
            heading = "Payment verification failed"
            message = "Premium access has not been activated. Please return to the upgrade page and try again."

    return render_template_string("""
<!doctype html>
<html>
<head>
    <title>{{ heading }} | StockRadar</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{margin:0;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}
        .box{max-width:620px;background:rgba(15,23,42,0.92);border:1px solid rgba(148,163,184,0.22);border-radius:28px;padding:34px;box-shadow:0 24px 80px rgba(0,0,0,0.45);}
        h1{margin:0 0 14px;font-size:34px;}
        p{color:#94a3b8;line-height:1.7;font-size:16px;}
        a{display:inline-block;margin-top:18px;padding:14px 18px;border-radius:16px;background:#00ffaa;color:#020617;text-decoration:none;font-weight:900;}
    </style>
</head>
<body>
    <div class="box">
        <h1>{{ heading }}</h1>
        <p>{{ message }}</p>
        <a href="{{ '/' if premium_activated else '/upgrade' }}">{{ 'Return to dashboard' if premium_activated else 'Return to upgrade' }}</a>
    </div>
</body>
</html>
    """,
        heading=heading,
        message=message,
        premium_activated=premium_activated,
    ), response_status


@app.route("/login", methods=["GET", "POST"])
def login():
    login_error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not owner_login_configured():
            login_error = "login is not configured. Set SIGNALSCOPE_OWNER_EMAIL and SIGNALSCOPE_OWNER_PASSWORD in your environment."
        elif email == OWNER_EMAIL and password == OWNER_PASSWORD:
            session["owner_logged_in"] = True
            return redirect(url_for("owner"))
        else:
            login_error = "Invalid owner email or password."

    return render_template_string(login_html, login_error=login_error)


@app.route("/owner")
def owner():
    if not owner_has_access():
        return redirect(url_for("login"))
    return render_template_string(owner_html)


@app.route("/logout")
def logout():
    session.pop("owner_logged_in", None)
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
