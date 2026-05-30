from flask import Flask, render_template_string, redirect, url_for, request, session
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import csv
import json
import os

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
    "https://signalscope-ai-1-0v3g.onrender.com/checkout-success"
)
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
STRIPE_CANCEL_URL = os.environ.get(
    "STRIPE_CANCEL_URL",
    "https://signalscope-ai-1-0v3g.onrender.com/upgrade"
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
            "reason": "Included in the 100-stock SignalScope universe. This keeps the live dashboard complete until the full scanner CSV/API feed is connected.",
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
            "reason": "This ticker is not currently inside the AI recommendation table, so SignalScope marks it as WATCH and gives it a balanced preview score until stronger scanner data is available.",
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


def get_premium_report(symbol, ai_context):
    cleaned_symbol = symbol.strip().upper()

    return {
        "headline": f"{cleaned_symbol} Pro Intelligence",
        "summary": "Premium view: confidence, signal strength, risk and next move in seconds.",
        "confidence": ai_context["confidence"],
        "meter": confidence_meter(ai_context["confidence"]),
        "strength": signal_strength_label(ai_context["confidence"]),
        "risk": ai_context["risk_view"],
        "next_move": ai_context["watch_next"],
        "pro_angle": "Pro turns the stock page into a fast decision panel, not a long report.",
    }


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
    if not NEWSAPI_KEY:
        return []

    params = urlencode({
        "q": MARKET_NEWS_QUERY,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": limit,
        "apiKey": NEWSAPI_KEY,
    })
    url = f"https://newsapi.org/v2/everything?{params}"

    try:
        req = Request(url, headers={"User-Agent": "SignalScopeAI/1.0"})
        with urlopen(req, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))

        articles = payload.get("articles", [])
        output = []

        for article in articles:
            title = (article.get("title") or "").strip()
            source = ((article.get("source") or {}).get("name") or "Market News").strip()
            article_url = (article.get("url") or "/").strip()
            published_at = (article.get("publishedAt") or "").strip()

            if not title:
                continue

            output.append({
                "title": title,
                "source": source,
                "url": article_url,
                "published_at": published_at,
            })

        return output
    except Exception:
        return []


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

    for article in live_articles:
        title = article.get("title", "Market headline")
        matched_stocks = match_news_to_stocks(title)
        primary_stock = matched_stocks[0] if matched_stocks else "SPY"
        stock_text = ", ".join(matched_stocks)
        direction, signal_influence, impact_score = score_news_impact(title)
        source = article.get("source", "Market News")

        headlines.append({
            "label": "LIVE NEWS",
            "text": f"{source}: {title} — may affect {stock_text}",
            "url": f"/stock/{primary_stock}",
            "premium_text": f"{source}: {title} — {impact_score} impact score. {direction}; {signal_influence}. Linked: {stock_text}.",
        })

    if headlines:
        return headlines[:8]

    for item in impact_radar:
        stocks = item.get("stocks", [])
        primary_stock = stocks[0] if stocks else "SPY"
        stock_text = ", ".join(stocks[:4]) if stocks else "major markets"
        title = item.get("title", "Market Impact Watch")
        impact = item.get("impact", "Medium")
        sectors = item.get("sectors", "global markets")
        direction = item.get("direction", "market sensitivity active")
        score = item.get("impact_score", "Premium score")
        watch_next = item.get("watch_next", "Watch for fresh headlines and market reaction.")

        headlines.append({
            "label": "HEADLINE",
            "text": f"{title}: {impact} impact theme may affect {stock_text}",
            "url": f"/stock/{primary_stock}",
            "premium_text": f"{title}: {score} impact score — may affect {stock_text}. {direction}.",
        })

        headlines.append({
            "label": "MARKET WATCH",
            "text": f"{sectors}: latest policy or macro themes may affect linked stocks including {stock_text}",
            "url": f"/stock/{primary_stock}",
            "premium_text": f"{sectors}: premium watch next — {watch_next}",
        })

    if not headlines:
        headlines.append({
            "label": "HEADLINE",
            "text": "Market-impact headlines will appear here as themes update and may affect linked stocks",
            "url": "/",
            "premium_text": "Premium market-impact headlines will appear here with impact score and what-to-watch-next context",
        })

    return headlines

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
        "live_headlines": build_live_headlines(recommendations, impact_radar),
        "newsapi_configured": bool(NEWSAPI_KEY),
    }


html = """
<!DOCTYPE html>
<html>
<head>
<title>SignalScope AI</title>
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
.main{flex:1;padding:48px;overflow-y:auto;max-width:1500px;margin:0 auto;}
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
.live-alert-track{display:flex;gap:28px;white-space:nowrap;padding:13px 16px;animation:tickerMove 42s linear infinite;}
.live-alert-strip:hover .live-alert-track{animation-play-state:paused;}
.live-headline{display:inline-flex;align-items:center;gap:10px;color:#e5e7eb;text-decoration:none;font-weight:800;}
.live-headline:hover{text-decoration:none;color:white;}
.live-tag{display:inline-block;background:rgba(0,255,170,0.12);border:1px solid rgba(0,255,170,0.20);color:#bbf7d0;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;letter-spacing:0.08em;text-transform:uppercase;}
.live-premium-tag{display:inline-block;background:rgba(255,184,107,0.14);border:1px solid rgba(255,184,107,0.24);color:#fed7aa;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:950;letter-spacing:0.08em;text-transform:uppercase;}
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
    <div class="logo">SignalScope AI</div>

    <div class="nav-section-label">Main Menu</div>
    <div class="menu-help">Use these tabs to jump straight to the section you need.</div>
    <a class="nav-link tab-button {% if active_tab == 'overview' %}active-tab{% endif %}" href="/?tab=overview">🏠 Overview</a>
    <a class="nav-link tab-button {% if active_tab == 'signals' %}active-tab{% endif %}" href="/?tab=signals">📊 AI Signals</a>
    <a class="nav-link tab-button {% if active_tab == 'radar' %}active-tab{% endif %}" href="/?tab=radar">🌍 Impact Radar</a>
    <a class="nav-link tab-button {% if active_tab == 'watchlist' %}active-tab{% endif %}" href="/?tab=watchlist">📋 AI Watchlist</a>

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
    <div class="live-alert-strip" aria-label="Live market headlines">
        <div class="live-alert-header">
            <span class="live-dot"></span>
            Market News Impact Feed
            <span style="color:#94a3b8;font-weight:800;letter-spacing:0;text-transform:none;">Updated {{ ticker_updated }}{% if newsapi_configured %} • Live news enabled{% else %} • Theme mode{% endif %}</span>
        </div>
        <div class="live-alert-track">
            {% for headline in live_headlines %}
            <a class="live-headline" href="{{ headline.url }}">
                <span class="{% if owner_logged_in %}live-premium-tag{% else %}live-tag{% endif %}">{% if owner_logged_in %}Premium Impact{% else %}Headline{% endif %}</span>
                {% if owner_logged_in %}{{ headline.premium_text }}{% else %}{{ headline.text }}{% endif %}
            </a>
            {% endfor %}
            {% for headline in live_headlines %}
            <a class="live-headline" href="{{ headline.url }}">
                <span class="{% if owner_logged_in %}live-premium-tag{% else %}live-tag{% endif %}">{% if owner_logged_in %}Premium Impact{% else %}Headline{% endif %}</span>
                {% if owner_logged_in %}{{ headline.premium_text }}{% else %}{{ headline.text }}{% endif %}
            </a>
            {% endfor %}
        </div>
    </div>
    <div id="overview-section" class="dashboard-section {% if active_tab == 'overview' %}active-section{% endif %}">
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
            <small>{{ item.market }} • {{ item.symbol }}</small>
            <h3><a class="stock-link" href="/stock/{{ item.symbol }}">{{ item.label }}</a></h3>
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
        <table><tr><th>Ticker</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in buy_rows %}<tr><td class="buy"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No BUY signals are currently active in your latest scanner output.</div>{% endif %}
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium signal breakdown active</h3><p>You have full premium access. Use the linked tickers above to open the premium stock intelligence pages.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock full AI signal breakdown</h3><p>Pro includes full conviction rankings, live alerts and deeper AI reasoning.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="hold-panel" class="card panel">
        <h2>Monitor Zone — HOLD Signals</h2>
        {% if hold_rows %}
        <table><tr><th>Ticker</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in hold_rows %}<tr><td class="hold"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No HOLD signals are currently active.</div>{% endif %}
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium HOLD analysis active</h3><p>You have full premium access to deeper HOLD interpretation and premium stock pages.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock deeper HOLD analysis</h3><p>Pro shows whether HOLD stocks are preparing to flip into BUY or SELL signals.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="sell-panel" class="card panel">
        <h2>Risk Warning — SELL Signals</h2>
        {% if sell_rows %}
        <table><tr><th>Ticker</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in sell_rows %}<tr><td class="sell"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No SELL signals are currently active.</div>{% endif %}
        {% if owner_logged_in %}<div class="notice"><h3>✅ Premium downside warnings active</h3><p>You have full premium access to downside warnings and premium risk interpretation.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock full downside warnings</h3><p>Pro includes live bearish alerts and AI-driven risk warnings.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Pro — £5/month</a></div>{% endif %}
    </div>

    <div id="conviction-panel" class="card panel">
        <h2>Premium Focus — Highest AI Conviction</h2>
        <table><tr><th>Ticker</th><th>Conviction</th><th>AI Insight</th></tr>{% for item in conviction_rows %}<tr><td class="buy"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
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
            <tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>Sector</th><th>AI Reason</th></tr>
            {% for item in recommendations %}
            <tr class="signal-row" data-ticker="{{ item.ticker }}" data-signal="{{ item.signal }}" data-sector="{{ item.sector or 'AI Watchlist' }}">
                <td><a class="stock-link" href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td>
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
        <p style="color:#94a3b8;line-height:1.7;">Browse every ticker currently supported by the AI recommendation table and open any stock page directly.</p>
    </div>
    <div class="card" id="watchlist">
        <p style="color:#00ffaa;font-weight:900;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 10px 0;">AI Recommendations Page</p>
        <h2>AI Recommendations</h2>
        <p style="color:#94a3b8;line-height:1.7;">This section shows your AI recommendation table. Click any ticker to open its live stock chart page.</p>
        <table>
            <tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>AI Reason</th></tr>
            {% for item in recommendations %}
            <tr><td><a class="stock-link" href="/stock/{{ item.ticker }}">{{ item.ticker }}</a></td><td class="{% if item.signal == 'BUY' %}buy{% elif item.signal == 'SELL' %}sell{% else %}hold{% endif %}">{{ item.signal }}</td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>
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
                    <a class="impact-stock" href="/stock/{{ stock }}">{{ stock }}</a>
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
</div>

<script>
function showDashboardSection(sectionId, button){var sections=document.querySelectorAll('.dashboard-section');sections.forEach(function(section){section.classList.remove('active-section');});var target=document.getElementById(sectionId);if(target){target.classList.add('active-section');target.scrollIntoView({behavior:'smooth',block:'start'});}var buttons=document.querySelectorAll('.tab-button');buttons.forEach(function(btn){btn.classList.remove('active-tab');});if(button){button.classList.add('active-tab');}}
function togglePanel(panelId){var panel=document.getElementById(panelId);var button=document.querySelector('[aria-controls="'+panelId+'"]');if(panel.classList.contains('open')){panel.classList.remove('open');if(button){button.setAttribute('aria-expanded','false');}}else{panel.classList.add('open');if(button){button.setAttribute('aria-expanded','true');}panel.scrollIntoView({behavior:'smooth',block:'start'});}}
function flashTarget(element){if(!element){return;}element.classList.remove('highlight-target');void element.offsetWidth;element.classList.add('highlight-target');}
function openPanelAndJump(panelId){var panel=document.getElementById(panelId);var button=document.querySelector('[aria-controls="'+panelId+'"]');if(!panel){return;}panel.classList.add('open');if(button){button.setAttribute('aria-expanded','true');}panel.scrollIntoView({behavior:'smooth',block:'start'});flashTarget(panel);}
function showSearchMessage(message){var messageBox=document.getElementById('searchMessage');if(!messageBox){return;}messageBox.textContent=message;messageBox.style.display='block';}
function runSmartSearch(event){event.preventDefault();var input=document.getElementById('smartSearchInput');if(!input){return false;}var query=input.value.trim().toUpperCase();if(!query){showSearchMessage('Type a ticker or section name first.');return false;}var map={'APPLE':'AAPL','AAPL':'AAPL','TESLA':'TSLA','TSLA':'TSLA','NVIDIA':'NVDA','NVDA':'NVDA','MICROSOFT':'MSFT','MSFT':'MSFT','AMAZON':'AMZN','AMZN':'AMZN','GOOGLE':'GOOGL','ALPHABET':'GOOGL','META':'META','FACEBOOK':'META','S&P 500':'^GSPC','SP500':'^GSPC','S&P':'^GSPC','NASDAQ':'^IXIC','FTSE':'^FTSE','FTSE 100':'^FTSE','HSBC':'HSBA.L','BP':'BP.L','ASTRAZENECA':'AZN.L','SHELL':'SHEL.L'};if(map[query]){window.location.href='/stock/'+encodeURIComponent(map[query]);return false;}if(['AI','RECOMMENDATIONS','AI RECOMMENDATIONS','WATCHLIST'].includes(query)){window.location.href='/?tab=watchlist';return false;}if(['BUY','BUYS','BUY SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=buy-panel';return false;}if(['HOLD','HOLDS','HOLD SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=hold-panel';return false;}if(['SELL','SELLS','SELL SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=sell-panel';return false;}if(['CONVICTION','HIGH CONVICTION','TOP'].includes(query)){window.location.href='/?tab=signals&open=conviction-panel';return false;}if(['POLITICS','POLITICAL','GEOPOLITICS','GEOPOLITICAL','RADAR','MARKET IMPACT','IMPACT RADAR'].includes(query)){window.location.href='/?tab=radar';return false;}
if(['PRO','UPGRADE','PAYMENT','SUBSCRIPTION'].includes(query)){window.location.href='/upgrade';return false;}if(/^[A-Z0-9.^-]{1,12}$/.test(query)){window.location.href='/stock/'+encodeURIComponent(query);return false;}showSearchMessage('No matching stock or section found. Try Apple, AAPL, S&P 500, Nasdaq, BUY, SELL, AI or Pro.');return false;}
function setSignalFilter(signal){var select=document.getElementById('signalFilterValue');if(select){select.value=signal;}document.querySelectorAll('[data-signal-filter]').forEach(function(button){button.classList.toggle('active-filter',button.getAttribute('data-signal-filter')===signal);});applySignalFilters();}
function resetSignalFilters(){var tickerInput=document.getElementById('tickerFilterInput');var sectorSelect=document.getElementById('sectorFilterSelect');if(tickerInput){tickerInput.value='';}if(sectorSelect){sectorSelect.value='ALL';}setSignalFilter('ALL');}
function applySignalFilters(){var tickerInput=document.getElementById('tickerFilterInput');var sectorSelect=document.getElementById('sectorFilterSelect');var signalSelect=document.getElementById('signalFilterValue');var tickerQuery=tickerInput ? tickerInput.value.trim().toUpperCase() : '';var selectedSector=sectorSelect ? sectorSelect.value : 'ALL';var selectedSignal=signalSelect ? signalSelect.value : 'ALL';var rows=document.querySelectorAll('.signal-row');var visibleCount=0;rows.forEach(function(row){var rowTicker=(row.getAttribute('data-ticker')||'').toUpperCase();var rowSignal=row.getAttribute('data-signal')||'';var rowSector=row.getAttribute('data-sector')||'AI Watchlist';var tickerMatch=!tickerQuery || rowTicker.includes(tickerQuery);var signalMatch=selectedSignal==='ALL' || rowSignal===selectedSignal;var sectorMatch=selectedSector==='ALL' || rowSector===selectedSector;var shouldShow=tickerMatch && signalMatch && sectorMatch;row.classList.toggle('hidden-signal-row',!shouldShow);if(shouldShow){visibleCount+=1;}});var status=document.getElementById('signalFilterStatus');if(status){var signalText=selectedSignal==='ALL'?'all signals':selectedSignal+' signals';var sectorText=selectedSector==='ALL'?'all sectors':selectedSector;var tickerText=tickerQuery?(' matching '+tickerQuery):'';status.textContent='Showing '+visibleCount+' stocks for '+signalText+', '+sectorText+tickerText+'.';}}
window.addEventListener('load',function(){var params=new URLSearchParams(window.location.search);var openPanel=params.get('open');if(openPanel){openPanelAndJump(openPanel);}if(window.location.pathname==='/ai-recommendations'){window.location.href='/?tab=watchlist';}applySignalFilters();});
</script>
</body>
</html>
"""


login_html = """
<!DOCTYPE html>
<html>
<head><title>Login</title><style>body{background:#020617;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;}form{background:#0f172a;padding:40px;border-radius:20px;width:340px;border:1px solid rgba(255,255,255,0.08);}input{width:100%;padding:14px;margin-bottom:15px;border:none;border-radius:10px;}button{width:100%;padding:14px;background:#38bdf8;border:none;border-radius:10px;color:white;font-weight:bold;cursor:pointer;}a{color:#38bdf8;}</style></head>
<body><form method="POST"><h1>🔐 Login</h1><p style="color:#94a3b8;">Sign in to access your account.</p>{% if login_error %}<p style="background:rgba(239,68,68,0.16);border:1px solid rgba(239,68,68,0.35);color:#fecaca;padding:12px;border-radius:10px;font-weight:bold;">{{ login_error }}</p>{% endif %}<input type="email" name="email" placeholder="Email"><input type="password" name="password" placeholder="Password"><button type="submit">Login</button><p style="color:#94a3b8;font-size:13px;margin-top:20px;">Sign in to continue.</p><p><a href="/">Return to Dashboard</a></p></form></body>
</html>
"""


upgrade_html = """
<!DOCTYPE html>
<html>
<head>
<title>SignalScope Pro Upgrade</title>
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
            <span class="badge">SignalScope Pro</span>
            <h1>Unlock full AI stock intelligence.</h1>
            <p>Pro turns each stock page into a premium decision panel: confidence, signal strength, risk read, momentum interpretation and what to watch next.</p>
            <div class="feature"><span class="tick">✓</span><span>Full Premium Pro Intelligence panel on every supported stock page.</span></div>
            <div class="feature"><span class="tick">✓</span><span>Risk read and next-move analysis instead of only a free preview.</span></div>
            <div class="feature"><span class="tick">✓</span><span>Cleaner decision support for users who want faster market context.</span></div>
            <div class="grid">
                <div class="mini"><strong>Free</strong>Signal preview and basic confidence meter.</div>
                <div class="mini"><strong>Pro</strong>Full AI confidence, risk and next move.</div>
                <div class="mini"><strong>Coming next</strong>Market impact radar and premium alerts.</div>
            </div>
        </div>
        <div class="card">
            <span class="badge">Premium plan</span>
            <div class="price">£5 <span>/ month</span></div>
            <p>Start Premium to unlock the full report view.</p>
            <div class="pay-box">
                <form method="POST" action="/create-checkout-session">
                    <button class="button" type="submit" style="border:none;cursor:pointer;width:100%;">Start Premium with Stripe Checkout</button>
                </form>
                <div class="note">Secure payment is handled by Stripe Checkout. Use Stripe test mode first.</div>
            </div>
        </div>
    </div>
    {% endif %}
</div>
</body>
</html>
"""


owner_html = """
<!DOCTYPE html>
<html><head><title>Owner Area</title><style>body{background:#020617;color:white;font-family:Arial;margin:0;padding:60px;}.card{background:#0f172a;padding:40px;border-radius:24px;max-width:820px;margin:auto;border:1px solid rgba(255,255,255,0.08);}a{color:#38bdf8;font-weight:bold;}</style></head><body><div class="card"><h1>👑 Owner Area</h1><p>You are logged in as the owner with premium access.</p><p>This confirms login and premium unlocking are working.</p><p><a href="/">Return to Dashboard</a></p><p><a href="/stock/AAPL">Open Premium AAPL Page</a></p></div></body></html>
"""


stock_detail_html = """
<!DOCTYPE html>
<html>
<head>
<title>{{ symbol }} Stock Detail</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{box-sizing:border-box;}body{background:radial-gradient(circle at 12% 6%,rgba(0,255,170,0.18),transparent 28%),linear-gradient(135deg,#050505,#121212,#1f2933);color:white;font-family:Arial,sans-serif;margin:0;min-height:100vh;padding:48px;}.card{background:linear-gradient(180deg,rgba(23,23,23,0.94),rgba(14,14,14,0.94));padding:32px;border-radius:30px;margin-bottom:24px;border:1px solid rgba(255,255,255,0.10);box-shadow:0 28px 82px rgba(0,0,0,0.44);}a{color:#38bdf8;text-decoration:none;font-weight:bold;}.range-row{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0;}.range-button{display:inline-block;padding:13px 17px;border-radius:16px;background:rgba(30,41,59,0.78);color:white;text-decoration:none;border:1px solid rgba(255,255,255,0.07);font-weight:800;}.range-button.active{background:linear-gradient(90deg,#38bdf8,#8b5cf6);}.metric-grid,.ai-grid,.example-report-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:22px;}.metric-grid{grid-template-columns:repeat(4,1fr);}.ai-card,.metric,.example-report-card{background:rgba(15,23,42,0.86);border:1px solid rgba(255,255,255,0.11);border-radius:24px;padding:24px;}.ai-card.warning{background:linear-gradient(145deg,rgba(251,191,36,0.13),rgba(17,24,39,0.92));}.ai-card.risk{background:linear-gradient(145deg,rgba(56,189,248,0.12),rgba(17,24,39,0.92));}.premium-banner,.example-report{background:linear-gradient(135deg,rgba(0,255,170,0.18),rgba(255,184,107,0.12),rgba(56,189,248,0.10));border:1px solid rgba(0,255,170,0.22);border-radius:30px;padding:30px;margin-bottom:24px;}.premium-banner{display:grid;grid-template-columns:1.45fr 0.75fr;gap:24px;align-items:center;}.premium-cta-box{background:rgba(5,5,5,0.58);border:1px solid rgba(255,255,255,0.15);border-radius:24px;padding:22px;text-align:center;}.payment-button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:20px;padding:16px 24px;font-size:16px;font-weight:950;text-decoration:none;}.payment-note{color:#94a3b8;font-size:13px;margin-top:12px;}.signal-badge,.free-strength,.strength-pill{display:inline-block;margin-top:10px;padding:8px 12px;border-radius:999px;background:rgba(255,255,255,0.08);font-weight:900;font-size:12px;text-transform:uppercase;}.confidence-large,.confidence-score{font-size:40px;font-weight:950;}.free-meter,.confidence-meter{font-size:26px;letter-spacing:2px;color:#00ffaa;font-weight:950;margin:8px 0;}.buy{color:#22c55e;font-weight:bold;}.sell{color:#ef4444;font-weight:bold;}.hold{color:#f59e0b;font-weight:bold;}canvas{background:#020617;border-radius:18px;padding:18px;}@media(max-width:900px){body{padding:24px;}.metric-grid,.ai-grid,.premium-banner,.example-report-grid{grid-template-columns:1fr;}}
</style>
</head>
<body>
<div class="card"><p><a href="/">← Back to Dashboard</a></p><h1>{{ symbol }} Stock Detail</h1><p style="color:#94a3b8;">Live chart view for {{ range_label }}. Use the buttons below to change timeframe.</p></div>

<div class="premium-banner"><div><small>Premium AI Intelligence Preview</small><h2>{{ symbol }} intelligence, not just a chart.</h2><p>Every supported stock and index gets the same structure: a useful free preview, then a stronger Pro intelligence panel with deeper AI explanation, risk read, momentum interpretation and what to watch next.</p></div><div class="premium-cta-box">{% if has_premium_access %}<strong>✅ Premium Active</strong><p>You have full premium access for {{ symbol }}.</p><a class="payment-button" href="/owner">Owner Area</a>{% else %}<strong>Unlock the full {{ symbol }} AI report</strong><p>Works across all supported tickers, indexes and AI recommendation stocks.</p><a class="payment-button" href="/upgrade">Upgrade now — £5/month</a><div class="payment-note">Opens the upgrade page. Stripe payment connects next.</div>{% endif %}</div></div>

<div class="ai-grid"><div class="ai-card"><small>Free Signal Preview</small><h2 class="{% if ai_context.signal == 'BUY' %}buy{% elif ai_context.signal == 'SELL' %}sell{% elif ai_context.signal == 'HOLD' %}hold{% endif %}">{{ ai_context.signal }}</h2><p>Every supported stock page gets the same free AI preview. Current signal for {{ symbol }}: {{ ai_context.signal }}.</p><span class="signal-badge">Live stock page: {{ symbol }}</span></div><div class="ai-card warning"><small>Free Confidence Preview</small><div class="confidence-large">{{ ai_context.confidence }}</div><div class="free-meter">{{ ai_context.confidence_meter }}</div><span class="free-strength">Signal strength: {{ ai_context.strength_label }}</span><p style="margin-top:12px;">Free shows the basic score and meter. Pro explains what is driving it for {{ symbol }}.</p></div><div class="ai-card risk"><small>{% if has_premium_access %}Premium Active{% else %}Pro Preview{% endif %}</small><h2>Next Move</h2>{% if has_premium_access %}<p>{{ ai_context.watch_next }}</p><span class="signal-badge">Premium unlocked</span>{% else %}<p>Pro unlocks the full interpretation behind the meter: why the score matters, what risk is building and what to watch next for {{ symbol }}.</p><a class="signal-badge" href="/upgrade">Unlock Premium</a>{% endif %}</div></div>

{% if has_premium_access and example_report %}<div class="example-report"><small>Premium Pro Intelligence</small><h2>{{ example_report.headline }}</h2><p>{{ example_report.summary }}</p><div class="example-report-grid"><div class="example-report-card"><strong>AI Confidence</strong><div class="confidence-score">{{ example_report.confidence }}</div><div class="confidence-meter">{{ example_report.meter }}</div><span class="strength-pill">Signal strength: {{ example_report.strength }}</span></div><div class="example-report-card"><strong>Risk read</strong><span>{{ example_report.risk }}</span></div><div class="example-report-card"><strong>Next move</strong><span>{{ example_report.next_move }}</span></div></div><div class="example-report-card" style="margin-top:16px;"><strong>Why Pro feels premium</strong><span>{{ example_report.pro_angle }}</span></div></div>{% endif %}
{% if not has_premium_access %}<div class="example-report"><small>Premium locked</small><h2>Unlock the full {{ symbol }} Pro Intelligence report</h2><p>Free shows the basic signal, confidence score and meter. Premium unlocks the full AI confidence panel, risk read, next move and clearer decision support.</p><div class="example-report-grid"><div class="example-report-card"><strong>Free preview</strong><div class="confidence-score">{{ ai_context.confidence }}</div><div class="confidence-meter">{{ ai_context.confidence_meter }}</div><span class="strength-pill">Basic signal strength: {{ ai_context.strength_label }}</span></div><div class="example-report-card"><strong>Premium risk read</strong><span>Locked until upgrade.</span></div><div class="example-report-card"><strong>Premium next move</strong><span>Locked until upgrade.</span></div></div><a class="payment-button" href="/upgrade" style="margin-top:18px;">Upgrade to Premium — £5/month</a><div class="payment-note">login unlocks premium automatically for testing.</div></div>{% endif %}

<div class="range-row">{% for key, settings in chart_ranges.items() %}<a class="range-button {% if key == active_range %}active{% endif %}" href="/stock/{{ symbol }}?range={{ key }}">{{ settings.label }}</a>{% endfor %}</div>
<div class="metric-grid"><div class="metric"><small>Range start</small><h2>{{ chart_data.start_price }}</h2></div><div class="metric"><small>Range latest</small><h2>{{ chart_data.end_price }}</h2></div><div class="metric"><small>Range move</small><h2 class="{{ chart_data.direction }}">{{ chart_data.change_amount }}</h2></div><div class="metric"><small>Range % move</small><h2 class="{{ chart_data.direction }}">{{ chart_data.change_percent }}</h2></div></div>
<div class="card">{% if chart_data.ok %}<canvas id="stockChart" height="120"></canvas>{% else %}<h2>Chart unavailable</h2><p style="color:#fca5a5;">{{ chart_data.error }}</p>{% endif %}</div>
<div class="card"><h2>Since market data began</h2><div class="metric-grid"><div class="metric"><small>Earliest available price</small><h2>{{ lifetime.start_price }}</h2></div><div class="metric"><small>Latest available price</small><h2>{{ lifetime.end_price }}</h2></div><div class="metric"><small>Total growth / decrease</small><h2 class="{{ lifetime.direction }}">{{ lifetime.change_amount }}</h2></div><div class="metric"><small>Total % growth / decrease</small><h2 class="{{ lifetime.direction }}">{{ lifetime.change_percent }}</h2></div></div></div>
<script>
const labels={{ chart_data.labels | tojson }};
const prices={{ chart_data.prices | tojson }};
if(labels.length>0){
    const ctx=document.getElementById('stockChart');
    new Chart(ctx,{type:'line',data:{labels:labels,datasets:[{label:'{{ symbol }} close price',data:prices,borderWidth:2,tension:0.25}]},options:{responsive:true,plugins:{legend:{labels:{color:'white'}}},scales:{x:{ticks:{color:'#94a3b8',maxTicksLimit:8},grid:{color:'rgba(255,255,255,0.08)'}},y:{ticks:{color:'#94a3b8'},grid:{color:'rgba(255,255,255,0.08)'}}}}});
}
</script>
</body>
</html>
"""
@app.route("/health")
@app.route("/healthz")
def health():
    return {
        "status": "ok",
        "app": "SignalScope AI",
        "stripe_configured": stripe_checkout_configured(),
        "owner_login_configured": owner_login_configured(),
    }, 200
@app.route("/favicon.ico")
def favicon():
    return "", 204
@app.route("/")
def home():
    dashboard_data = prepare_dashboard_data()
    active_tab = request.args.get("tab", "overview")
    if active_tab not in {"overview", "signals", "radar", "watchlist"}:
        active_tab = "overview"
    return render_template_string(
        html,
        owner_logged_in=owner_has_access(),
        active_tab=active_tab,
        **dashboard_data,
    )


@app.route("/ai-recommendations")
def ai_recommendations():
    return redirect(url_for("home", tab="watchlist"))


@app.route("/stock/<path:symbol>")
def stock_detail(symbol):
    cleaned_symbol = symbol.strip().upper()
    active_range = request.args.get("range", "1mo")

    if active_range not in CHART_RANGES:
        active_range = "1mo"

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
    <title>Stripe Setup Needed | SignalScope</title>
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
    <title>Stripe Checkout Paused | SignalScope</title>
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
    session["owner_logged_in"] = True
    return render_template_string("""
<!doctype html>
<html>
<head>
    <title>Payment Successful | SignalScope</title>
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
        <h1>✅ Premium activated</h1>
        <p>Your premium dashboard session is now active. You can return to SignalScope and view premium intelligence features.</p>
        <a href="/">Return to dashboard</a>
    </div>
</body>
</html>
    """)


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
    return redirect(url_for("home"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)