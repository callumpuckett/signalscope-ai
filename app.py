from flask import Flask, Response, render_template_string, redirect, url_for, request, session, jsonify
from datetime import datetime, time as dt_time, timedelta, timezone
from email.utils import format_datetime
from email.message import EmailMessage
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
import csv
import json
import os
import pandas as pd
import re
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


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


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


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
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PREMIUM_PAYMENTS_ENABLED = os.environ.get("PREMIUM_PAYMENTS_ENABLED", "").strip().lower() == "true"
PRODUCTION_BASE_URL = "https://www.stockradarhq.com"
RENDER_FALLBACK_BASE_URL = "https://signalscope-ai-1-0v3g.onrender.com"
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
PREMIUM_ENTITLEMENTS_PATH = os.path.join(APP_ROOT, "premium_entitlements.json")
DEFAULT_STRIPE_SUCCESS_URL = (
    f"{PRODUCTION_BASE_URL}/checkout-success?session_id={{CHECKOUT_SESSION_ID}}"
)
DEFAULT_STRIPE_CANCEL_URL = f"{PRODUCTION_BASE_URL}/upgrade"


def configured_url(environment_name, default):
    return os.environ.get(environment_name, default)


STRIPE_SUCCESS_URL = configured_url("STRIPE_SUCCESS_URL", DEFAULT_STRIPE_SUCCESS_URL)
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
NEWSLETTER_EMBED_HTML = os.environ.get("NEWSLETTER_EMBED_HTML", "").strip()
NEWSLETTER_EMAIL_ENABLED = os.environ.get("NEWSLETTER_EMAIL_ENABLED", "").strip().lower() == "true"
NEWSLETTER_SMTP_HOST = os.environ.get("NEWSLETTER_SMTP_HOST", "").strip()
NEWSLETTER_SMTP_PORT = int(os.environ.get("NEWSLETTER_SMTP_PORT", "587"))
NEWSLETTER_SMTP_USERNAME = os.environ.get("NEWSLETTER_SMTP_USERNAME", "").strip()
NEWSLETTER_SMTP_PASSWORD = os.environ.get("NEWSLETTER_SMTP_PASSWORD", "")
NEWSLETTER_FROM_EMAIL = (
    os.environ.get("NEWSLETTER_FROM_EMAIL", "").strip()
    or SUPPORT_EMAIL
    or NEWSLETTER_SMTP_USERNAME
)
NEWSLETTER_CRON_SECRET = os.environ.get("NEWSLETTER_CRON_SECRET", "").strip()
NEWSLETTER_SUBSCRIBERS_PATH = os.path.join(APP_ROOT, "newsletter_subscribers.json")
NEWSLETTER_DELIVERY_LOG_PATH = os.path.join(APP_ROOT, "newsletter_delivery_log.json")
NEWSLETTER_SEND_LOCK_DIR = os.path.join(APP_ROOT, ".newsletter_locks")
NEWSLETTER_AUTO_SEND_ENABLED = (
    os.environ.get(
        "NEWSLETTER_AUTO_SEND_ENABLED",
        "true" if IS_PRODUCTION else "false",
    )
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)
NEWSLETTER_AUTO_SEND_HOUR_LONDON = int(os.environ.get("NEWSLETTER_AUTO_SEND_HOUR_LONDON", "9"))
NEWSLETTER_AUTO_SEND_MINUTE_LONDON = int(os.environ.get("NEWSLETTER_AUTO_SEND_MINUTE_LONDON", "0"))
NEWSLETTER_AUTO_SEND_CHECK_INTERVAL_SECONDS = int(
    os.environ.get("NEWSLETTER_AUTO_SEND_CHECK_INTERVAL_SECONDS", "300")
)
NEWSLETTER_SEND_LOCK_STALE_SECONDS = 60 * 60
NEWSLETTER_AUTO_SEND_THREAD_STARTED = False
BEEHIIV_API_KEY = os.environ.get("BEEHIIV_API_KEY", "").strip()
BEEHIIV_PUBLICATION_ID = os.environ.get("BEEHIIV_PUBLICATION_ID", "").strip()
BEEHIIV_AUTOSEND_ENABLED = (
    os.environ.get("BEEHIIV_AUTOSEND_ENABLED", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
BEEHIIV_API_BASE_URL = "https://api.beehiiv.com/v2"
BEEHIIV_REQUEST_TIMEOUT_SECONDS = 20
NEWSLETTER_BEEHIIV_STATE_PATH = os.path.join(APP_ROOT, "newsletter_beehiiv_state.json")
BEEHIIV_WEEKLY_BULK_SENDER = "beehiiv_manual"
BEEHIIV_CREATE_POST_BLOCKED = True
BEEHIIV_EXPORT_SUBJECT = "StockRadar Weekly: This week’s market signals"
BEEHIIV_EXPORT_PREVIEW = "Your 5-minute plain-English market brief is ready."
BEEHIIV_EXPORT_DISCLAIMER = (
    "StockRadar provides educational market information and research tools only. "
    "It is not personal financial advice."
)
LAST_NEWS_FETCH_STATUS = {
    "provider": "none",
    "status": "not_started",
    "errors": [],
}
DASHBOARD_CACHE_TTL_SECONDS = int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "300"))
MARKET_NEWS_REFRESH_INTERVAL_MS = 300000
MARKET_NEWS_TICKER_LIMIT = 6
DASHBOARD_CACHE = {
    "timestamp": 0,
    "data": None,
}
RECOMMENDATIONS_CACHE_TTL_SECONDS = int(os.environ.get("RECOMMENDATIONS_CACHE_TTL_SECONDS", "300"))
RECOMMENDATIONS_CACHE = {
    "timestamp": 0,
    "rows": None,
}
WEEKLY_NEWSLETTER_ISSUE_CACHE = {
    "issue_date": None,
    "issue_status": None,
    "generated_at": None,
    "issue": None,
}
WEEKLY_NEWSLETTER_PREVIEW_CACHE_TTL_SECONDS = 900

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

def stripe_credentials_configured():
    return bool(stripe and STRIPE_SECRET_KEY and STRIPE_PRICE_ID)


def stripe_checkout_configured():
    return bool(PREMIUM_PAYMENTS_ENABLED and stripe_credentials_configured())


ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "paid"}
INACTIVE_SUBSCRIPTION_STATUSES = {
    "canceled",
    "incomplete",
    "incomplete_expired",
    "past_due",
    "payment_failed",
    "unpaid",
}


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def normalize_email(value):
    return str(value or "").strip().lower()


def stripe_value(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def stripe_nested_value(obj, *keys):
    current = obj
    for key in keys:
        current = stripe_value(current, key)
        if current in (None, ""):
            return None
    return current


def stripe_identifier(value):
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(stripe_value(value, "id", "") or "").strip()


def subscription_status_is_active(status):
    cleaned = str(status or "").strip().lower()
    if cleaned in ACTIVE_SUBSCRIPTION_STATUSES:
        return True
    if cleaned in INACTIVE_SUBSCRIPTION_STATUSES:
        return False
    return False


def load_premium_entitlements():
    try:
        with open(PREMIUM_ENTITLEMENTS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"records": []}
    except Exception:
        app.logger.exception("Failed to read premium entitlement storage.")
        return {"records": []}

    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data

    return {"records": []}


def save_premium_entitlements(data):
    try:
        directory = os.path.dirname(PREMIUM_ENTITLEMENTS_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = f"{PREMIUM_ENTITLEMENTS_PATH}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, PREMIUM_ENTITLEMENTS_PATH)
        return True
    except Exception:
        app.logger.exception("Failed to write premium entitlement storage.")
        return False


def entitlement_matches(record, customer_id="", subscription_id="", email=""):
    record_email = normalize_email(record.get("customer_email"))
    return bool(
        customer_id and record.get("stripe_customer_id") == customer_id
        or subscription_id and record.get("stripe_subscription_id") == subscription_id
        or email and record_email == normalize_email(email)
    )


def update_premium_entitlement(
    customer_id="",
    subscription_id="",
    email="",
    subscription_status="",
    premium_active=False,
    event_type="",
):
    customer_id = stripe_identifier(customer_id)
    subscription_id = stripe_identifier(subscription_id)
    email = normalize_email(email)

    if not any([customer_id, subscription_id, email]):
        app.logger.warning("Skipping premium entitlement update without Stripe identifiers.")
        return None

    data = load_premium_entitlements()
    records = data.setdefault("records", [])
    now = utc_timestamp()

    matching_record = None
    for record in records:
        if entitlement_matches(record, customer_id, subscription_id, email):
            matching_record = record
            break

    if matching_record is None:
        matching_record = {"created_at": now}
        records.append(matching_record)

    if email:
        matching_record["customer_email"] = email
    if customer_id:
        matching_record["stripe_customer_id"] = customer_id
    if subscription_id:
        matching_record["stripe_subscription_id"] = subscription_id

    matching_record["subscription_status"] = str(subscription_status or "").strip().lower()
    matching_record["premium_active"] = bool(premium_active)
    matching_record["updated_at"] = now
    if event_type:
        matching_record["last_event"] = event_type

    return matching_record if save_premium_entitlements(data) else None


def premium_entitlement_active(customer_id="", subscription_id="", email=""):
    customer_id = stripe_identifier(customer_id)
    subscription_id = stripe_identifier(subscription_id)
    email = normalize_email(email)

    if not any([customer_id, subscription_id, email]):
        return False

    data = load_premium_entitlements()
    matches = [
        record for record in data.get("records", [])
        if entitlement_matches(record, customer_id, subscription_id, email)
    ]

    if not matches:
        return False

    latest = sorted(matches, key=lambda item: item.get("updated_at", ""), reverse=True)[0]
    return latest.get("premium_active") is True


def checkout_session_email(checkout_session):
    return (
        stripe_value(checkout_session, "customer_email")
        or stripe_nested_value(checkout_session, "customer_details", "email")
        or stripe_nested_value(checkout_session, "metadata", "email")
        or ""
    )


def checkout_session_payment_verified(checkout_session):
    payment_status = str(stripe_value(checkout_session, "payment_status", "") or "").lower()
    checkout_status = str(stripe_value(checkout_session, "status", "") or "").lower()
    return payment_status == "paid" if payment_status else checkout_status == "complete"


def remember_premium_session_identifiers(customer_id="", subscription_id="", email=""):
    customer_id = stripe_identifier(customer_id)
    subscription_id = stripe_identifier(subscription_id)
    email = normalize_email(email)

    if customer_id:
        session["stripe_customer_id"] = customer_id
    if subscription_id:
        session["stripe_subscription_id"] = subscription_id
    if email:
        session["premium_email"] = email


def owner_has_access():
    return session.get("owner_logged_in") is True


def premium_has_access():
    if owner_has_access() or session.get("premium_active") is True:
        return True

    return premium_entitlement_active(
        customer_id=session.get("stripe_customer_id"),
        subscription_id=session.get("stripe_subscription_id"),
        email=session.get("premium_email"),
    )


def owner_login_configured():
    return bool(OWNER_EMAIL and OWNER_PASSWORD)


def disclaimer_footer():
    return """
    <footer style="margin:32px auto 0;padding:18px 0 0;border-top:1px solid rgba(255,255,255,0.10);color:#94a3b8;font-size:12px;line-height:1.65;max-width:1180px;">
        <div>
            <strong style="color:#cbd5e1;">Educational only.</strong>
            StockRadar provides educational market information and research tools only. It does not provide personal financial, investment, tax, or legal advice. BUY, HOLD, and SELL signals are research prompts—not instructions or promises. Investments can fall as well as rise, and you may lose money. Consider your circumstances and seek advice from a regulated professional where appropriate.
        </div>
        <div style="margin-top:10px;">
            <strong style="color:#cbd5e1;">Trust basics.</strong>
            Payments are handled securely by Stripe when Premium checkout is available. StockRadar does not store your full card details. Newsletter emails are used to send StockRadar updates and market briefs.
        </div>
        <nav aria-label="Legal and support links" style="display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;">
            <a href="/newsletter" style="color:#94a3b8;">Newsletter</a>
            <a href="/how-it-works" style="color:#94a3b8;">How It Works</a>
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
<title>{{ meta_title }}</title>
<meta name="description" content="{{ meta_description }}">
<link rel="canonical" href="{{ canonical_url }}">
<meta property="og:title" content="{{ meta_title }}">
<meta property="og:description" content="{{ meta_description }}">
<meta property="og:type" content="website">
<meta property="og:url" content="{{ canonical_url }}">
<meta property="og:site_name" content="StockRadar">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{{ meta_title }}">
<meta name="twitter:description" content="{{ meta_description }}">
<style>
:root{--font-hero:clamp(36px,5vw,44px);--font-section:24px;--font-card-title:18px;--font-body:15px;--font-small:13px;--font-kicker:11px;--font-cta:14px;}
body{margin:0;background:radial-gradient(circle at 15% 0%,rgba(0,255,170,0.10),transparent 30%),linear-gradient(135deg,#08111c,#101827);color:#dbe4ee;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
.wrap{max-width:900px;margin:0 auto;}
.card{background:rgba(17,27,39,0.94);border:1px solid rgba(148,163,184,0.16);border-radius:26px;padding:32px;box-shadow:0 24px 70px rgba(0,0,0,0.28);}
h1{font-size:var(--font-hero);line-height:1.08;margin:0 0 18px;}
h2{font-size:var(--font-section);line-height:1.18;margin:26px 0 8px;}
p,li{color:#b9c5d2;line-height:1.72;font-size:var(--font-body);}
a{color:#38bdf8;}
.back{display:inline-block;margin-bottom:22px;font-weight:900;text-decoration:none;}
.prompt-list{display:grid;gap:10px;padding:0;margin:22px 0;list-style:none;}
.prompt-list li{padding:14px 16px;border-radius:16px;background:rgba(148,163,184,0.07);border:1px solid rgba(148,163,184,0.12);color:#c6d0da;}
.feedback-cta{display:inline-block;margin-top:8px;padding:14px 18px;border-radius:15px;background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;text-decoration:none;font-weight:950;box-shadow:0 14px 34px rgba(0,255,170,0.12);}
.feedback-email{color:#dce6ef;font-weight:900;}
.section-grid,.signal-guide{display:grid;gap:14px;margin:20px 0;}
.section-grid{grid-template-columns:repeat(2,1fr);}.signal-guide{grid-template-columns:repeat(3,1fr);}
.info-section,.signal-explainer{padding:19px;border-radius:18px;background:rgba(148,163,184,0.065);border:1px solid rgba(148,163,184,0.13);}
.info-section h2,.signal-explainer h2{margin:0 0 8px;font-size:var(--font-card-title);line-height:1.2;}
.info-section p,.signal-explainer p{margin:0;}
.signal-explainer.buy{border-color:rgba(74,222,163,0.24);}.signal-explainer.buy h2{color:#86efac;}
.signal-explainer.hold{border-color:rgba(245,185,79,0.26);}.signal-explainer.hold h2{color:#f4cf79;}
.signal-explainer.sell{border-color:rgba(248,113,113,0.25);}.signal-explainer.sell h2{color:#fca5a5;}
.research-flow{display:grid;gap:9px;counter-reset:flow;margin:18px 0;padding:0;list-style:none;}
.research-flow li{counter-increment:flow;padding:12px 14px 12px 48px;position:relative;border-radius:14px;background:rgba(7,17,24,0.55);border:1px solid rgba(148,163,184,0.10);}
.research-flow li::before{content:counter(flow);position:absolute;left:14px;top:12px;width:23px;height:23px;border-radius:999px;display:grid;place-items:center;background:rgba(74,222,163,0.14);color:#86efac;font-size:12px;font-weight:950;}
.weekly-cta{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-top:22px;padding:20px;border-radius:18px;background:linear-gradient(135deg,rgba(74,222,163,0.12),rgba(245,185,79,0.10));border:1px solid rgba(74,222,163,0.20);}
.weekly-cta h2,.weekly-cta p{margin:0;}.weekly-cta p{margin-top:5px;}
.weekly-cta a{display:inline-flex;align-items:center;justify-content:center;white-space:nowrap;flex:0 0 auto;padding:12px 16px;border-radius:14px;background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;text-decoration:none;font-weight:950;font-size:var(--font-cta);line-height:1.1;}
@media(max-width:700px){:root{--font-hero:clamp(32px,9vw,38px);--font-section:22px;}body{padding:24px;}h1{font-size:var(--font-hero);}.section-grid,.signal-guide{grid-template-columns:1fr;}.weekly-cta{align-items:flex-start;flex-direction:column;}}
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


PUBLIC_PAGE_METADATA = {
    "Privacy Policy": (
        "/privacy",
        "How StockRadar handles account, support, technical and payment-related information.",
    ),
    "Terms of Use": (
        "/terms",
        "Terms for using StockRadar educational market research tools and public services.",
    ),
    "Refund Policy": (
        "/refund-policy",
        "StockRadar cancellation, billing-period access and refund review policy.",
    ),
    "Risk Disclaimer": (
        "/risk-disclaimer",
        "Important investment risk information for users of StockRadar educational market research tools.",
    ),
    "Contact": (
        "/contact",
        "Contact the StockRadar team for account, subscription, cancellation, refund or general support.",
    ),
    "Manage Subscription": (
        "/manage-subscription",
        "Information about managing or cancelling a StockRadar subscription.",
    ),
    "StockRadar Feedback": (
        "/feedback",
        "Share feedback about StockRadar clarity, usefulness, trust and mobile usability.",
    ),
    "How StockRadar Works": (
        "/how-it-works",
        "Learn how to read StockRadar market news, affected stocks and BUY, HOLD and SELL research prompts.",
    ),
}


def render_legal_page(title, content):
    page_path, meta_description = PUBLIC_PAGE_METADATA.get(
        title,
        (request.path, f"{title} information for StockRadar users."),
    )
    meta_title = title if title.startswith("StockRadar") else f"{title} — StockRadar"
    return render_template_string(
        legal_page_html,
        title=title,
        content=content,
        meta_title=meta_title,
        meta_description=meta_description,
        canonical_url=f"{PRODUCTION_BASE_URL}{page_path}",
    )


error_page_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} — StockRadar</title>
<style>
body{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.13),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;display:flex;align-items:center;justify-content:center;}
.card{width:min(760px,100%);background:rgba(15,23,42,0.94);border:1px solid rgba(255,255,255,0.11);border-radius:28px;padding:34px;box-shadow:0 28px 82px rgba(0,0,0,0.42);}
.code{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;}
h1{font-size:44px;line-height:1.04;margin:12px 0 16px;}
p{color:#cbd5e1;line-height:1.75;}
.links{display:flex;flex-wrap:wrap;gap:12px;margin-top:22px;}
a{display:inline-block;color:#050505;background:linear-gradient(135deg,#00ffaa,#ffb86b);padding:12px 16px;border-radius:14px;text-decoration:none;font-weight:950;}
a.secondary{color:#dbeafe;background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.12);}
@media(max-width:700px){body{padding:24px;}h1{font-size:34px;}}
</style>
</head>
<body>
<main class="card">
    <div class="code">{{ code }}</div>
    <h1>{{ heading }}</h1>
    <p>{{ message }}</p>
    {% if support_html %}<p>{{ support_html | safe }}</p>{% endif %}
    <div class="links">
        <a href="/">Dashboard</a>
        <a class="secondary" href="/universe">Explore Stocks</a>
        <a class="secondary" href="/feedback">Feedback</a>
        <a class="secondary" href="/contact">Contact</a>
    </div>
</main>
</body>
</html>
"""


def render_error_page(code, title, heading, message, support_html=""):
    return render_template_string(
        error_page_html,
        code=code,
        title=title,
        heading=heading,
        message=message,
        support_html=support_html,
    )


@app.errorhandler(404)
def page_not_found(error):
    return render_error_page(
        "404",
        "Page Not Found",
        "This page could not be found.",
        "The address may be incorrect, or the page may have moved.",
    ), 404


@app.errorhandler(500)
def internal_server_error(error):
    if SUPPORT_EMAIL:
        support_html = render_template_string(
            'For support, email <a class="secondary" href="mailto:{{ support_email }}">{{ support_email }}</a>.',
            support_email=SUPPORT_EMAIL,
        )
    else:
        support_html = 'Please use the <a class="secondary" href="/contact">StockRadar contact page</a>.'

    return render_error_page(
        "500",
        "Something Went Wrong",
        "Something went wrong.",
        "StockRadar could not complete this request. Please try again shortly.",
        support_html=support_html,
    ), 500


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
DIVIDEND_CONTEXT_CACHE_TTL_SECONDS = 3600
DIVIDEND_CONTEXT_CACHE = {}

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
    "MAERSK": "MAERSK-B.CO",
    "MAERSK B": "MAERSK-B.CO",
    "MAERSK A": "MAERSK-A.CO",
    "A P MOLLER MAERSK": "MAERSK-B.CO",
    "AP MOLLER MAERSK": "MAERSK-B.CO",
    "A.P. MOLLER MAERSK": "MAERSK-B.CO",
    "BAE.L": "BA.L",
    "BAE SYSTEMS": "BA.L",
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


EQUIVALENT_SHARE_CLASS_PRIMARY = {
    "MAERSK-A.CO": "MAERSK-B.CO",
}


def generated_signal_source_ticker(ticker):
    cleaned_ticker = str(ticker or "").strip().upper()
    return EQUIVALENT_SHARE_CLASS_PRIMARY.get(cleaned_ticker, cleaned_ticker)


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
                else "Included in the StockRadar universe as a research watchlist name. Signal strength may update as more market, scanner and news data becomes available."
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


def build_homepage_free_report_preview(recommendations=None):
    """Build a free-only Microsoft preview from data already loaded for the homepage."""
    for item in recommendations or []:
        if str(item.get("ticker") or "").strip().upper() != "MSFT":
            continue

        signal = clean_signal(item.get("signal"), item.get("confidence"))
        confidence = normalise_confidence(item.get("confidence"))
        strength = signal_strength_label(confidence)
        return {
            "company_name": "Microsoft",
            "ticker": "MSFT",
            "signal": signal,
            "confidence": confidence,
            "strength": strength,
            "is_current": True,
            "explanation": (
                f"The current {signal} signal is StockRadar's latest free research prompt for Microsoft."
            ),
            "research_next": (
                "Open the live report to review the current signal, strength and chart context."
            ),
        }

    return {
        "company_name": "Microsoft",
        "ticker": "MSFT",
        "signal": "",
        "confidence": "",
        "strength": "",
        "is_current": False,
        "explanation": "Example preview — open the live report for the current signal.",
        "research_next": "The live Microsoft report is the authoritative current view.",
    }


def build_premium_decision_brief(recommendations=None):
    rows = list(recommendations or get_recommendations())
    buy_rows, hold_rows, sell_rows, conviction_rows = split_rows(rows)
    signal_rank = {"BUY": 3, "HOLD": 2, "WATCH": 2, "SELL": 1}

    def ranked(candidates):
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda item: (
                signal_rank.get(str(item.get("signal", "")).upper(), 2),
                confidence_number(item.get("confidence", "0%")),
            ),
            reverse=True,
        )[0]

    def brief_item(item):
        if not item:
            return None
        ticker = str(item.get("ticker", "")).strip().upper()
        return {
            "ticker": ticker,
            "label": stock_display_label(ticker),
            "signal": item.get("signal", "HOLD"),
            "confidence": item.get("confidence", "50%"),
            "sector": item.get("sector") or SECTOR_MAP.get(ticker, "AI Watchlist"),
            "reason": item.get("reason") or "Current StockRadar research context is available.",
        }

    etf_tickers = {"SPY", "QQQ", "DIA", "IWM", "SMH", "GLD", "SLV", "TLT", "HYG", "VUSA.L", "^GSPC", "^IXIC", "^FTSE"}
    non_us_candidates = [
        item for item in rows
        if "." in str(item.get("ticker", "")) or str(item.get("ticker", "")).startswith("^FTSE")
    ]
    caution_candidates = sell_rows or sorted(hold_rows, key=lambda item: confidence_number(item.get("confidence", "0%")))
    watch_candidates = [item for item in hold_rows if item not in caution_candidates[:1]]

    return {
        "strongest": brief_item((conviction_rows or buy_rows or rows or [None])[0]),
        "caution": brief_item(caution_candidates[0] if caution_candidates else None),
        "market_setup": brief_item(ranked([item for item in rows if str(item.get("ticker", "")).upper() in etf_tickers])),
        "non_us": brief_item(ranked(non_us_candidates)),
        "watchlist": brief_item((watch_candidates or hold_rows or rows or [None])[0]),
    }

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
    generated_universe_prompt = False

    for item in recommendations:
        if item["ticker"].strip().upper() == cleaned_symbol:
            matching_item = item
            break

    if matching_item is None:
        universe_match = None
        universe_index = 0
        signal_source_symbol = generated_signal_source_ticker(cleaned_symbol)
        signal_source_index = None
        universe_rows = get_stock_universe()

        for index, item in enumerate(universe_rows):
            item_ticker = str(item.get("ticker", "")).strip().upper()
            if item_ticker == cleaned_symbol:
                universe_match = item
                universe_index = index
            if item_ticker == signal_source_symbol:
                signal_source_index = index

        if universe_match is not None:
            generated_universe_prompt = True
            generated_index = signal_source_index if signal_source_index is not None else universe_index
            signal, confidence = generated_signal_for_ticker(signal_source_symbol, generated_index)
            company_name = str(universe_match.get("name") or cleaned_symbol).strip()
            sector = str(universe_match.get("sector") or "diversified market").strip()
            exchange = str(universe_match.get("exchange") or "its listed market").strip()

            if signal == "BUY":
                reason = (
                    f"{company_name} is included in the StockRadar expanded universe. "
                    f"The current research prompt is positive for this {sector} name on {exchange}, "
                    "supported by its sector exposure and deterministic watchlist screening pattern. "
                    "Confirm with live price action, news and fundamentals before making any decision."
                )
            elif signal == "SELL":
                reason = (
                    f"{company_name} is included in the StockRadar expanded universe. "
                    f"The current research prompt is cautious for this {sector} name on {exchange}, "
                    "meaning downside risk or a weaker relative setup should be reviewed before further action. "
                    "Confirm with live price action, news and fundamentals before making any decision."
                )
            else:
                reason = (
                    f"{company_name} is included in the StockRadar expanded universe. "
                    f"The current research prompt is balanced for this {sector} name on {exchange}, "
                    "meaning it is worth monitoring but does not show a strong directional prompt yet. "
                    "Confirm with live price action, news and fundamentals before making any decision."
                )

            matching_item = {
                "ticker": cleaned_symbol,
                "signal": signal,
                "confidence": confidence,
                "reason": reason,
            }
        else:
            matching_item = {
                "ticker": cleaned_symbol,
                "signal": "WATCH",
                "confidence": "50%",
                "reason": "This ticker is not currently inside the AI recommendation table, so StockRadar marks it as WATCH and gives it a balanced preview score until stronger scanner data is available.",
            }

    confidence_value = confidence_number(matching_item["confidence"])
    signal = matching_item["signal"]

    if generated_universe_prompt and signal == "BUY" and confidence_value >= 80:
        momentum_view = "Strong upside research setup"
        risk_view = "Medium risk — the positive generated prompt still needs confirmation from live price action, news and fundamentals."
        watch_next = "Watch for sustained price strength, supportive company news and fundamentals that confirm the research prompt."
    elif generated_universe_prompt and signal == "BUY":
        momentum_view = "Positive setup building"
        risk_view = "Medium risk — the generated prompt is constructive, but it is not a high-conviction instruction."
        watch_next = "Watch for improving price strength, supportive news and stronger fundamental confirmation."
    elif generated_universe_prompt and signal == "SELL":
        momentum_view = "Weak or defensive setup"
        risk_view = "Higher risk — the generated prompt is cautious and should be checked against current price action, news and fundamentals."
        watch_next = "Watch whether the stock stabilises, relative strength improves or new information changes the cautious setup."
    elif generated_universe_prompt and signal == "HOLD":
        momentum_view = "Neutral / balanced setup"
        risk_view = "Balanced risk — the generated prompt does not currently show a strong positive or negative direction."
        watch_next = "Watch for a clearer trend, meaningful company news or fundamental changes before drawing a stronger conclusion."
    elif signal == "BUY" and confidence_value >= 80:
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


def build_today_context(stock_context, news_context=None):
    """Present the existing stock-page context without recalculating it."""
    context = stock_context or {}
    signal = str(context.get("signal") or "WATCH").strip().upper()
    signal_wording = {
        "BUY": (
            "The current StockRadar signal is constructive. Existing evidence is positive, "
            "but the signal remains a research prompt rather than a prediction or instruction."
        ),
        "HOLD": (
            "The current StockRadar signal is balanced. Existing evidence is mixed, so further "
            "confirmation is needed before reaching a stronger directional conclusion."
        ),
        "SELL": (
            "The current StockRadar signal is cautious. Existing evidence is highlighting weaker "
            "conditions or downside pressure that require closer research."
        ),
        "WATCH": (
            "This ticker is currently a watchlist research candidate. StockRadar does not yet have "
            "enough evidence for a stronger directional prompt."
        ),
    }
    confidence = context.get("confidence", "")
    strength_label = context.get("strength_label", "")

    if isinstance(news_context, dict):
        news_text = str(
            news_context.get("context")
            or news_context.get("summary")
            or news_context.get("text")
            or ""
        ).strip()
    else:
        news_text = str(news_context or "").strip()

    return {
        "signal": signal,
        "confidence": confidence,
        "strength_label": strength_label,
        "confidence_label": " · ".join(
            str(value).strip() for value in (confidence, strength_label) if str(value).strip()
        ),
        "plain_english_summary": signal_wording.get(signal, signal_wording["WATCH"]),
        "setup_reason": context.get("reason", ""),
        "risk_today": context.get("risk_view", ""),
        "watch_next": context.get("watch_next", ""),
        "momentum_view": context.get("momentum_view", ""),
        "news_context": news_text,
    }


def build_business_education(symbol, sector, company_name, role_profile=None):
    """Build stable sector or fund education from locally available identity data."""
    cleaned_symbol = canonical_stock_symbol(symbol)
    sector_text = str(sector or "").strip()
    sector_lower = sector_text.lower()
    role_profile = role_profile or classify_portfolio_role(cleaned_symbol)
    role_key = str(role_profile.get("key") or "research").strip().lower()
    display_name = str(company_name or cleaned_symbol).strip()

    broad_market_etfs = {
        "SPY", "DIA", "IWM", "VUSA", "VUSA.L", "VUAG", "VUAG.L",
        "VWRP", "VWRP.L", "VWRL", "VWRL.L",
    }
    sector_or_technology_etfs = {"QQQ", "SMH"}
    bond_etfs = {"TLT", "HYG"}
    commodity_etfs = {"GLD", "SLV", "USO"}
    is_etf = (
        cleaned_symbol in broad_market_etfs
        or cleaned_symbol in sector_or_technology_etfs
        or cleaned_symbol in bond_etfs
        or cleaned_symbol in commodity_etfs
        or "etf" in sector_lower
        or role_key in {"broad_market_etf", "core_etf"}
    )

    common = {
        "basis_label": "General sector education",
        "symbol": cleaned_symbol,
        "company_name": display_name,
        "sector": sector_text or "General research candidate",
        "is_etf": is_etf,
        "holdings_check": "",
    }

    if is_etf:
        if cleaned_symbol in broad_market_etfs or role_key == "broad_market_etf":
            education = {
                "education_type": "Broad-market ETF",
                "business_model": "This fund provides exposure to a broad market index through a basket of underlying securities.",
                "growth_drivers": "Performance usually reflects the underlying companies, index weighting, economic conditions and overall market sentiment.",
                "business_risks": "Concentration in the largest companies, sector weighting, market declines and overlap with existing holdings.",
                "holdings_check": "Inspect the largest holdings, sector weights, index method, fees and overlap with other funds or individual stocks.",
                "research_question": "What are the fund's largest underlying exposures, and do I already own them elsewhere?",
            }
        elif cleaned_symbol in bond_etfs or "bond" in sector_lower:
            education = {
                "education_type": "Bond ETF",
                "business_model": "This fund holds a portfolio of bonds or fixed-income securities.",
                "growth_drivers": "Performance usually reflects interest rates, bond yields, credit quality, maturity profile and economic conditions.",
                "business_risks": "Interest-rate risk, credit risk, inflation, duration risk and liquidity.",
                "holdings_check": "Inspect duration, maturity profile, credit quality, fees, yield sources and the largest issuer exposures.",
                "research_question": "What are the fund's duration, credit quality and main sources of income risk?",
            }
        elif cleaned_symbol in commodity_etfs or "commodity" in sector_lower:
            education = {
                "education_type": "Commodity ETF",
                "business_model": "This fund provides exposure to a commodity or commodity-linked instruments.",
                "growth_drivers": "Performance usually reflects commodity prices, supply and demand, geopolitics, currency movements and futures-market structure.",
                "business_risks": "High volatility, tracking differences, futures costs and concentration in one commodity.",
                "holdings_check": "Inspect whether exposure comes from physical assets, futures or related securities, plus fees and long-term tracking differences.",
                "research_question": "How closely does this fund track the underlying commodity over longer periods?",
            }
        else:
            education = {
                "education_type": "Sector or technology ETF",
                "business_model": "This fund provides targeted exposure to one sector, industry or investment theme.",
                "growth_drivers": "Performance usually reflects the companies within that sector and the market conditions affecting the theme.",
                "business_risks": "Sector concentration, overlap between holdings, high valuations and stronger volatility than a broad-market fund.",
                "holdings_check": "Inspect the largest holdings, their weights, sector or theme purity, fees and overlap with funds or stocks already held.",
                "research_question": "Is this fund adding genuinely new exposure, or increasing a theme I already own?",
            }

        return {
            **common,
            **education,
            "strengthen_case": "A durable performance case generally needs the underlying exposure and index or fund structure to keep matching the investor's research purpose.",
            "weaken_case": "The case may weaken if concentration, overlap, tracking, fees or the underlying market exposure no longer match that purpose.",
        }

    templates = {
        "technology": {
            "education_type": "Technology and software",
            "business_model": "Technology companies may make money through software, cloud services, subscriptions, devices, platforms, advertising or digital services.",
            "growth_drivers": "Customer adoption, recurring revenue, product development, pricing power and expanding digital demand.",
            "business_risks": "Competition, regulation, high valuation, product disruption, cyber risk and changing technology cycles.",
            "strengthen_case": "Durable customer demand, recurring revenue growth, strong margins and evidence of a lasting competitive advantage.",
            "weaken_case": "Slowing demand, weaker margins, excessive valuation, loss of market share or rising regulatory pressure.",
            "research_question": "What gives this business a durable advantage over competitors?",
        },
        "semiconductors": {
            "education_type": "Semiconductors",
            "business_model": "Semiconductor companies may make money by designing, manufacturing or supplying chips and related technology.",
            "growth_drivers": "Data-centre demand, artificial intelligence, devices, industrial demand, manufacturing capacity and product leadership.",
            "business_risks": "Industry cycles, supply constraints, customer concentration, competition, geopolitical exposure and capital intensity.",
            "strengthen_case": "Product leadership, diversified demand, reliable capacity and evidence that investment is producing durable returns.",
            "weaken_case": "A cycle downturn, lost product leadership, customer concentration, supply disruption or inefficient capital spending.",
            "research_question": "How dependent is the company on a small number of products, customers or end markets?",
        },
        "consumer_staples": {
            "education_type": "Consumer staples",
            "business_model": "Consumer-staples companies generally make money from frequently purchased products and established brands.",
            "growth_drivers": "Pricing power, volume growth, new products, distribution and geographic expansion.",
            "business_risks": "Input costs, weaker consumer demand, private-label competition, brand weakness and limited growth.",
            "strengthen_case": "Resilient demand, trusted brands, effective distribution and pricing that protects margins without materially reducing volumes.",
            "weaken_case": "Persistent volume declines, weaker brands, rising costs, lost shelf space or price increases that damage demand.",
            "research_question": "Can the company raise prices without materially weakening demand?",
        },
        "consumer": {
            "education_type": "Consumer or retail",
            "business_model": "Consumer and retail companies make money by selling products or services directly to customers through stores, online platforms or distribution networks.",
            "growth_drivers": "Customer demand, store or platform growth, pricing, product mix, loyalty and operating efficiency.",
            "business_risks": "Economic weakness, changing consumer behaviour, competition, inventory problems and margin pressure.",
            "strengthen_case": "Repeat demand, customer loyalty, healthy inventory, improving efficiency and evidence that pricing and product mix support margins.",
            "weaken_case": "Falling demand, excess inventory, lost customers, discounting or sustained cost and margin pressure.",
            "research_question": "What makes customers continue choosing this business over alternatives?",
        },
        "banks": {
            "education_type": "Banks and financials",
            "business_model": "Banks generally make money through lending, deposits, interest margins, fees and other financial services.",
            "growth_drivers": "Lending demand, deposit growth, interest margins, fee income and economic activity.",
            "business_risks": "Credit losses, regulation, economic weakness, funding pressure, market stress and interest-rate changes.",
            "strengthen_case": "Resilient credit quality, stable funding, disciplined lending and diverse fee or interest income.",
            "weaken_case": "Rising defaults, deposit pressure, shrinking margins, weak lending demand or greater regulatory and capital strain.",
            "research_question": "How resilient is the bank if credit conditions weaken?",
        },
        "payments": {
            "education_type": "Payments",
            "business_model": "Payments businesses may make money by charging transaction, processing, network or service fees.",
            "growth_drivers": "Transaction growth, digital-payment adoption, international expansion and additional financial services.",
            "business_risks": "Competition, regulation, fraud, economic weakness, pricing pressure and technological disruption.",
            "strengthen_case": "Growing transaction volume, reliable networks, disciplined risk controls and services that deepen customer use.",
            "weaken_case": "Slower volumes, price pressure, fraud losses, regulation or technology that reduces the value of the network.",
            "research_question": "Does the company benefit from growing transaction volume without taking excessive credit risk?",
        },
        "energy": {
            "education_type": "Energy",
            "business_model": "Energy companies may make money through producing, refining, transporting or selling energy products and services.",
            "growth_drivers": "Production, energy demand, commodity prices, project execution and capital discipline.",
            "business_risks": "Commodity-price volatility, geopolitics, regulation, project costs, environmental liabilities and the economic cycle.",
            "strengthen_case": "Disciplined spending, reliable projects, resilient cash generation and operations that can withstand weaker commodity prices.",
            "weaken_case": "Falling commodity prices, cost overruns, weaker production, poor capital discipline or rising regulatory and environmental costs.",
            "research_question": "How dependent are profits and cash flow on current commodity prices?",
        },
        "healthcare": {
            "education_type": "Healthcare",
            "business_model": "Healthcare companies may make money through medicines, medical devices, diagnostics, insurance or healthcare services.",
            "growth_drivers": "Product development, approvals, patient demand, demographic trends and successful commercial execution.",
            "business_risks": "Clinical failure, regulation, patent expiry, reimbursement pressure and product concentration.",
            "strengthen_case": "A diverse product base, successful development, durable patient demand and evidence of effective commercial execution.",
            "weaken_case": "Clinical setbacks, patent loss, reimbursement pressure, regulation or dependence on too few products.",
            "research_question": "How dependent is the business on one product, treatment or regulatory outcome?",
        },
        "industrial": {
            "education_type": "Industrial or defence",
            "business_model": "Industrial and defence companies may make money from equipment, engineering, manufacturing, services and long-term contracts.",
            "growth_drivers": "Order growth, infrastructure spending, government budgets, productivity investment and project execution.",
            "business_risks": "Contract delays, cost overruns, political changes, economic cycles and supply-chain pressure.",
            "strengthen_case": "A durable order book, disciplined contract delivery, resilient margins and reliable execution.",
            "weaken_case": "Delayed orders, cost overruns, weaker budgets, supply disruption or poor contract execution.",
            "research_question": "How reliable are the company's order book, margins and contract execution?",
        },
        "telecoms": {
            "education_type": "Telecoms",
            "business_model": "Telecom companies generally make money through mobile, broadband, network and communication services.",
            "growth_drivers": "Subscriber growth, pricing, network usage, service expansion and operating efficiency.",
            "business_risks": "High debt, regulation, competition, capital spending and slow growth.",
            "strengthen_case": "Stable subscribers, sensible pricing, efficient network investment and sustainable cash generation.",
            "weaken_case": "Customer losses, heavy debt, price competition, weak growth or capital spending that persistently exceeds cash generation.",
            "research_question": "Can the company fund network investment while maintaining sustainable cash flow?",
        },
        "fallback": {
            "education_type": "General research candidate",
            "business_model": "This business may earn money by selling products, services or access to assets within its market.",
            "growth_drivers": "Customer demand, pricing, product or service development, operating efficiency and expansion into relevant markets.",
            "business_risks": "Competition, weaker demand, rising costs, regulation, execution problems and financial pressure.",
            "strengthen_case": "Clear customer demand, durable advantages, disciplined execution and resilient cash generation.",
            "weaken_case": "Lost demand, weaker margins, greater competition, poor execution or a less resilient financial position.",
            "research_question": "What are the main sources of revenue, and which evidence shows they may be durable?",
        },
    }

    if "semiconductor" in sector_lower:
        template_key = "semiconductors"
    elif any(word in sector_lower for word in ("technology", "software", "cloud", "data analytics")):
        template_key = "technology"
    elif any(word in sector_lower for word in ("consumer staple", "consumer defensive", "staples")) or cleaned_symbol in {"KO", "PEP", "PG"}:
        template_key = "consumer_staples"
    elif "payment" in sector_lower:
        template_key = "payments"
    elif any(word in sector_lower for word in ("bank", "financial")):
        template_key = "banks"
    elif any(word in sector_lower for word in ("energy", "commodity", "materials")):
        template_key = "energy"
    elif "healthcare" in sector_lower:
        template_key = "healthcare"
    elif any(word in sector_lower for word in ("industrial", "aerospace", "defence", "defense", "space")):
        template_key = "industrial"
    elif "telecom" in sector_lower:
        template_key = "telecoms"
    elif any(word in sector_lower for word in ("consumer", "retail", "media", "restaurant", "ev")):
        template_key = "consumer"
    else:
        template_key = "fallback"

    return {**common, **templates[template_key]}


def classify_portfolio_role(symbol):
    cleaned_symbol = str(symbol or "").strip().upper()
    sector = SECTOR_MAP.get(cleaned_symbol, "").lower()

    broad_market_etfs = {"SPY", "DIA", "IWM", "VUSA", "VUSA.L", "VUAG", "VUAG.L", "VWRP", "VWRP.L", "VWRL", "VWRL.L"}
    core_etfs = {"SPY", "QQQ", "DIA", "IWM", "SMH", "GLD", "SLV", "USO", "TLT", "HYG", "VUSA", "VUAG", "VWRP", "VWRL"}
    index_symbols = {"^GSPC", "^IXIC", "^DJI", "^RUT", "^FTSE", "^N225", "^HSI"}
    crypto_symbols = {"BTC-USD", "ETH-USD", "SOL-USD"}
    dividend_compounders = {"KO", "PEP", "PG", "MCD", "WMT", "JNJ", "ABBV"}

    if cleaned_symbol in broad_market_etfs:
        return {
            "key": "broad_market_etf",
            "label": "Broad-market ETF",
            "decision_use": "Use as diversified market exposure and a possible portfolio building block before adding narrower company or sector risk.",
            "concentration_note": "Check the fund's largest holdings because broad exposure can still be concentrated in the biggest companies or sectors.",
        }

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

    if cleaned_symbol in dividend_compounders:
        return {
            "key": "dividend",
            "label": "Dividend compounder / consumer staples",
            "decision_use": "Use as a quality, defensive or income-oriented research candidate after checking business durability and dividend sustainability.",
            "concentration_note": "Several dividend or consumer-staples holdings can share slow-growth, valuation, debt and interest-rate sensitivities.",
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


def build_portfolio_builder_education(symbol, role_profile):
    cleaned_symbol = canonical_stock_symbol(symbol)
    role_profile = role_profile or classify_portfolio_role(cleaned_symbol)
    role_key = str(role_profile.get("key") or "research").strip().lower()
    role_label = str(role_profile.get("label") or "General research candidate").strip()
    sector = str(SECTOR_MAP.get(cleaned_symbol) or "").strip().lower()

    is_broad_fund = role_key == "broad_market_etf"
    is_fund = is_broad_fund or role_key == "core_etf" or "etf" in sector
    is_growth = role_key == "growth" or any(
        word in sector for word in ("technology", "software", "cloud", "semiconductor", "growth")
    )
    is_bank = "bank" in sector
    is_energy = any(word in sector for word in ("energy", "commodity", "materials"))
    is_dividend = role_key == "dividend"
    is_defensive = role_key == "defensive"

    role_copy = {
        "broad_market_etf": {
            "meaning": "This type of holding may serve as a broad foundation by spreading exposure across many companies.",
            "use": "Investors often use broad-market funds as core holdings, then add narrower ideas only when those ideas have a clear purpose.",
            "caution": "Broad does not mean overlap-free: the largest companies and sectors can still drive a meaningful share of returns.",
        },
        "core_etf": {
            "meaning": "This type of holding may serve as a fund-based building block or a targeted source of market exposure.",
            "use": "Investors often use funds to reach many securities at once, while checking whether the fund is broad, sector-specific or theme-specific.",
            "caution": "An ETF label alone does not make a holding diversified or suitable as a core position.",
        },
        "index": {
            "meaning": "This type of holding may serve as a benchmark for understanding how a market segment is performing.",
            "use": "Investors often compare individual holdings with an index to separate company-specific results from the wider market backdrop.",
            "caution": "A market index is a reference point, not a complete answer about portfolio construction or personal suitability.",
        },
        "growth": {
            "meaning": "This type of holding may serve as a targeted growth or technology satellite within a broader portfolio.",
            "use": "Investors often use a single growth company to pursue a specific opportunity while keeping its company-specific risk visible.",
            "caution": "High expectations, valuation and volatility can make a strong business a demanding holding.",
        },
        "quality": {
            "meaning": "This type of holding may serve as a quality compounder focused on durable cash flow, scale or brand strength.",
            "use": "Investors often research quality companies for long-term compounding while comparing business strength with valuation.",
            "caution": "Quality can already be widely owned through large funds, and a strong company can still be bought at a demanding price.",
        },
        "dividend": {
            "meaning": "This type of holding may serve as a dividend, quality or defensive satellite when its cash flows remain durable.",
            "use": "Investors often use established dividend companies for income context and steadier business exposure.",
            "caution": "A familiar brand or attractive yield does not guarantee dividend growth or protect against valuation, debt and business risks.",
        },
        "defensive": {
            "meaning": "This type of holding may serve as defensive exposure where demand can be steadier across the economic cycle.",
            "use": "Investors often use defensive companies to reduce reliance on one growth theme or economic outcome.",
            "caution": "Defensive does not mean risk-free; regulation, debt, competition and valuation still matter.",
        },
        "cyclical": {
            "meaning": "This type of holding may serve as targeted cyclical exposure linked to an industry or economic driver.",
            "use": "Investors often use cyclical companies as satellites when they want deliberate exposure to a sector, cycle or macro condition.",
            "caution": "A good company can still be affected by rates, credit, commodity prices or the economic cycle.",
        },
        "industrial": {
            "meaning": "This type of holding may serve as targeted industrial or defence exposure within a diversified portfolio.",
            "use": "Investors often use industrial companies as satellites tied to contracts, infrastructure, capital spending or economic activity.",
            "caution": "Order books can add visibility, but costs, contracts, policy and the economic cycle still affect outcomes.",
        },
        "research": {
            "meaning": "This type of holding may serve as a general research candidate once its purpose and main risk drivers are clear.",
            "use": "Investors often define a holding's role before deciding whether it adds genuinely different exposure.",
            "caution": "If the role is unclear, it is harder to judge overlap, concentration and what evidence would weaken the case.",
        },
    }.get(role_key)

    if role_copy is None:
        role_copy = {
            "meaning": "This type of holding may serve as a general research candidate once its purpose and main risk drivers are clear.",
            "use": "Investors often define a holding's role before deciding whether it adds genuinely different exposure.",
            "caution": "If the role is unclear, it is harder to judge overlap, concentration and what evidence would weaken the case.",
        }

    if is_broad_fund:
        overlap = "Check the fund's largest holdings before assuming it adds entirely new diversification. Broad funds can still share many of the same large companies."
        core_label = "Often considered for a core role"
        core_or_satellite = "Broad-market funds are often used as core holdings because they spread exposure across many companies. Their breadth, index design and existing portfolio overlap still need checking."
        mistake = "Owning several ETFs does not guarantee diversification if they hold many of the same companies."
        principle = "Diversification means spreading underlying risks, not simply owning more ticker symbols."
    elif is_fund:
        overlap = "Check the fund's largest holdings, sector weights and index method before assuming it adds entirely new diversification."
        core_label = "Core or satellite depends on fund breadth"
        core_or_satellite = "A broadly diversified fund may support a core role. A sector, theme or commodity fund is generally better understood as a satellite because its risk drivers are narrower."
        mistake = "Owning several ETFs does not guarantee diversification if they hold many of the same companies."
        principle = "Core holdings provide broad structure; satellite holdings add targeted opportunities and risks."
    elif is_growth:
        overlap = "If you already own several large technology companies or technology-heavy ETFs, this may increase exposure to the same growth drivers."
        core_label = "Generally a satellite holding"
        core_or_satellite = "A single growth stock is usually better understood as a satellite holding because company-specific risk and valuation risk remain high."
        mistake = "Owning several technology stocks and a technology-heavy ETF can create more concentration than the number of holdings suggests."
        principle = "Several holdings can still behave like one large bet if they depend on the same theme."
    elif is_bank:
        overlap = "Several bank holdings can respond to the same interest-rate, credit and economic conditions. Different company names do not remove those shared drivers."
        core_label = "Generally a satellite holding"
        core_or_satellite = "A bank is generally better understood as a satellite because its performance can depend heavily on one industry and the economic cycle."
        mistake = "Owning several banks may still leave a portfolio dependent on the same rate, credit and economic conditions."
        principle = "Several holdings can still behave like one large bet if they depend on the same theme."
    elif is_energy:
        overlap = "Owning several energy companies may still leave a portfolio exposed to the same commodity prices and geopolitical risks."
        core_label = "Generally a satellite holding"
        core_or_satellite = "An energy or commodity-linked company is generally better understood as a satellite because its results can depend heavily on an industry cycle."
        mistake = "Several different energy companies may still depend on the same commodity cycle."
        principle = "Several holdings can still behave like one large bet if they depend on the same theme."
    elif is_dividend:
        overlap = "Several dividend companies may look diversified while sharing similar slow-growth, debt or interest-rate risks."
        core_label = "A defensive satellite, not a complete core"
        core_or_satellite = "A dividend or consumer-staples company may support portfolio balance, but it remains a single-company position rather than a complete core portfolio."
        mistake = "Building a portfolio only around high yields can concentrate risk in slower-growing, indebted or rate-sensitive businesses."
        principle = "Position size often matters as much as stock selection."
    elif is_defensive:
        overlap = "Several defensive companies can still depend on similar economic, regulatory or income-related conditions."
        core_label = "A defensive satellite, not a complete core"
        core_or_satellite = "A defensive stock may support portfolio balance, but it remains a single-company position rather than a complete core portfolio."
        mistake = "Several defensive holdings can still share regulatory, debt, valuation or slow-growth risks."
        principle = "Core holdings provide broad structure; satellite holdings add targeted opportunities and risks."
    else:
        overlap = str(role_profile.get("concentration_note") or "Check whether this duplicates a sector, theme, fund holding or risk already present elsewhere.")
        core_label = "Usually assessed as a satellite first"
        core_or_satellite = "A single company is generally better understood as a satellite because company-specific risk remains, even when the business is high quality."
        mistake = "Adding more holdings does not automatically improve diversification. What matters is whether the underlying risks are genuinely different."
        principle = "Position size often matters as much as stock selection."

    first_check = (
        "Do I understand what the fund actually owns?"
        if is_fund
        else "Do I understand what the business actually does?"
    )

    return {
        "role_key": role_key,
        "role_label": role_label,
        "role_meaning": role_copy["meaning"],
        "role_use": role_copy["use"],
        "role_caution": role_copy["caution"],
        "overlap": overlap,
        "core_label": core_label,
        "core_or_satellite": core_or_satellite,
        "position_size": "A strong company can still become a poor portfolio decision if one position becomes too large. Position size determines how much a single mistake or sharp price move can affect the whole portfolio.",
        "position_question": "If this holding fell sharply, would the size of the position disrupt the wider plan?",
        "checklist": [
            first_check,
            "Does this duplicate a sector, theme or ETF exposure I already have?",
            "Is this a core holding or a satellite?",
            "Am I comfortable with the likely volatility?",
            "What evidence would make the investment case weaker?",
            "Is the position size small enough to avoid dominating the portfolio?",
            "Am I adding this because of research rather than recent price excitement?",
        ],
        "mistake": mistake,
        "principle": principle,
    }


DIVIDEND_ETF_TICKERS = {
    "SPY", "QQQ", "DIA", "IWM", "SMH", "GLD", "SLV", "USO", "TLT", "HYG",
    "VUSA", "VUSA.L", "VUAG", "VUAG.L", "VWRP", "VWRP.L", "VWRL", "VWRL.L",
}


FUNDAMENTAL_CURRENCY_SYMBOLS = {
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
    "JPY": "¥",
    "CAD": "C$",
    "AUD": "A$",
}


def fundamental_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number) or abs(number) == float("inf"):
        return None
    return number


def fundamental_currency(info):
    currency = str(
        (info or {}).get("financialCurrency")
        or (info or {}).get("currency")
        or ""
    ).strip()
    return "GBP" if currency.lower() == "gbp" else currency.upper()


def format_fundamental_currency(value, currency, compact=False):
    number = fundamental_number(value)
    if number is None:
        return ""

    absolute = abs(number)
    suffix = ""
    if compact:
        for threshold, compact_suffix in (
            (1_000_000_000_000, "T"),
            (1_000_000_000, "B"),
            (1_000_000, "M"),
        ):
            if absolute >= threshold:
                absolute /= threshold
                suffix = compact_suffix
                break

    decimals = 1 if suffix else 2
    formatted = f"{absolute:.{decimals}f}".rstrip("0").rstrip(".") + suffix
    currency_code = str(currency or "").strip().upper()
    currency_prefix = FUNDAMENTAL_CURRENCY_SYMBOLS.get(currency_code)
    if currency_prefix:
        formatted = f"{currency_prefix}{formatted}"
    elif currency_code:
        formatted = f"{currency_code} {formatted}"
    return f"-{formatted}" if number < 0 else formatted


def format_fundamental_decimal(value):
    number = fundamental_number(value)
    if number is None:
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_next_earnings(info, now=None):
    info = info or {}
    raw_timestamp = info.get("earningsTimestamp")
    if raw_timestamp in (None, ""):
        raw_timestamp = info.get("earningsTimestampStart")

    if isinstance(raw_timestamp, datetime):
        earnings_date = raw_timestamp
        if earnings_date.tzinfo is None:
            earnings_date = earnings_date.replace(tzinfo=timezone.utc)
    else:
        timestamp = fundamental_number(raw_timestamp)
        if timestamp is None or timestamp <= 0:
            return ""
        try:
            earnings_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if earnings_date <= current_time:
        return ""

    formatted = earnings_date.strftime("%d %B %Y").lstrip("0")
    if info.get("isEarningsDateEstimate") is True:
        formatted += " · Estimated"
    return formatted


def build_key_fundamentals(info, is_etf=False, now=None):
    info = info if isinstance(info, dict) else {}
    if is_etf:
        return []

    metrics = []
    currency = fundamental_currency(info)

    market_cap = fundamental_number(info.get("marketCap"))
    if market_cap is not None and market_cap > 0:
        metrics.append({
            "key": "market-cap",
            "label": "Market Cap",
            "value": format_fundamental_currency(market_cap, currency, compact=True),
        })

    trailing_pe = fundamental_number(info.get("trailingPE"))
    if trailing_pe is not None and trailing_pe > 0:
        metrics.append({
            "key": "pe-ratio",
            "label": "P/E Ratio",
            "value": format_fundamental_decimal(trailing_pe),
        })

    trailing_eps = fundamental_number(info.get("trailingEps"))
    if trailing_eps is not None:
        metrics.append({
            "key": "trailing-eps",
            "label": "Trailing EPS",
            "value": format_fundamental_currency(trailing_eps, currency),
        })

    beta = fundamental_number(info.get("beta"))
    if beta is not None:
        metrics.append({
            "key": "beta",
            "label": "Beta",
            "value": format_fundamental_decimal(beta),
        })

    earnings_date = format_next_earnings(info, now=now)
    if earnings_date:
        metrics.append({
            "key": "next-earnings",
            "label": "Next Earnings",
            "value": earnings_date,
        })

    return metrics


def get_dividend_context(symbol):
    cleaned_symbol = canonical_stock_symbol(symbol)
    now = time.time()
    cached = DIVIDEND_CONTEXT_CACHE.get(cleaned_symbol)
    if cached and now - cached["timestamp"] < DIVIDEND_CONTEXT_CACHE_TTL_SECONDS:
        return dict(cached["context"])

    universe_item = next(
        (
            item for item in get_stock_universe()
            if str(item.get("ticker") or "").strip().upper() == cleaned_symbol
        ),
        {},
    )
    sector = str(
        universe_item.get("sector")
        or SECTOR_MAP.get(cleaned_symbol, "")
    ).strip()
    info = {}
    info_loaded = False

    if yf is not None and cleaned_symbol:
        try:
            ticker_object = yf.Ticker(cleaned_symbol)
            get_info = getattr(ticker_object, "get_info", None)
            if callable(get_info):
                info = get_info() or {}
            else:
                info = getattr(ticker_object, "info", {}) or {}
            info_loaded = isinstance(info, dict)
            if not info_loaded:
                info = {}
        except Exception:
            app.logger.info("Dividend metadata unavailable for %s.", cleaned_symbol)
            info = {}

    quote_type = str(info.get("quoteType") or "").strip().upper()
    category = str(info.get("category") or "").strip()
    fund_markers = ("ETF", "FUND", "INDEX")
    sector_category_text = f"{sector} {category}".upper()
    is_etf = (
        cleaned_symbol in DIVIDEND_ETF_TICKERS
        or quote_type in {"ETF", "MUTUALFUND"}
        or any(marker in sector_category_text for marker in fund_markers)
    )

    def percentage_text(value):
        number = fundamental_number(value)
        if number is None or number < 0:
            return "Not available"
        percentage = number * 100 if number <= 1 else number
        return f"{percentage:.2f}".rstrip("0").rstrip(".") + "%"

    def amount_text(value):
        number = fundamental_number(value)
        if number is None or number < 0:
            return "Not available"
        return f"{number:.4f}".rstrip("0").rstrip(".") + " per share annually"

    def date_text(value):
        if value is None or value == "":
            return "Not available"
        if isinstance(value, datetime):
            parsed = value
        else:
            number = fundamental_number(value)
            if number is None:
                return "Not available"
            try:
                parsed = datetime.fromtimestamp(number, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return "Not available"
        return parsed.strftime("%d %B %Y")

    reported_yield_value = info.get("dividendYield")
    trailing_yield_value = info.get("trailingAnnualDividendYield")
    annual_value = (
        info.get("forwardAnnualDividendRate")
        if info.get("forwardAnnualDividendRate") is not None
        else info.get("trailingAnnualDividendRate")
    )
    market_price = (
        info.get("currentPrice")
        if info.get("currentPrice") is not None
        else info.get("regularMarketPrice")
    )
    ex_dividend_value = info.get("exDividendDate")
    payout_value = info.get("payoutRatio")

    def yield_text():
        trailing_yield = fundamental_number(trailing_yield_value)
        if trailing_yield is not None and trailing_yield > 0:
            return percentage_text(trailing_yield)

        annual_amount = fundamental_number(annual_value)
        price = fundamental_number(market_price)
        if annual_amount is not None and annual_amount > 0 and price is not None and price > 0:
            return f"{annual_amount / price * 100:.2f}".rstrip("0").rstrip(".") + "%"

        reported_yield = fundamental_number(reported_yield_value)
        if reported_yield is None or reported_yield <= 0:
            return "Not available"
        percentage = reported_yield * 100 if reported_yield <= 0.2 else reported_yield
        return f"{percentage:.2f}".rstrip("0").rstrip(".") + "%"

    yield_number = fundamental_number(trailing_yield_value)
    if yield_number is None:
        yield_number = fundamental_number(reported_yield_value)
    annual_number = fundamental_number(annual_value)
    has_dividend_data = (
        (yield_number is not None and yield_number > 0)
        or (annual_number is not None and annual_number > 0)
        or date_text(ex_dividend_value) != "Not available"
    )
    dividend_label = "Distribution" if is_etf else "Dividend"

    if is_etf:
        beginner_explanation = (
            "ETF distributions are payments made from income received by the fund’s "
            "underlying holdings. Some ETFs distribute income as cash, while accumulating "
            "ETFs may reinvest income inside the fund instead. Distribution amounts can "
            "change over time."
        )
        no_data_message = (
            "No regular cash distribution found from the available data. Some ETFs "
            "reinvest income or may not currently show distribution data."
        )
        frequency_note = (
            "Distribution frequency varies by fund and share class. Check the fund’s "
            "official schedule before relying on a payment date."
        )
    else:
        beginner_explanation = (
            "Dividends are cash payments some companies make to shareholders. They are "
            "usually paid from company profits, but they are not guaranteed. A higher "
            "yield can look attractive, but it can also signal risk if the market expects "
            "the payment to be cut."
        )
        no_data_message = "No regular dividend found for this ticker from the available data."
        frequency_note = (
            "Payment frequency varies by company and market. Check the company’s official "
            "announcements for the current schedule."
        )

    context = {
        "ticker": cleaned_symbol,
        "is_etf": is_etf,
        "has_dividend_data": has_dividend_data,
        "dividend_label": dividend_label,
        "dividend_yield": yield_text(),
        "annual_dividend": amount_text(annual_value),
        "ex_dividend_date": date_text(ex_dividend_value),
        "payout_ratio": percentage_text(payout_value),
        "fundamentals": build_key_fundamentals(info, is_etf=is_etf),
        "dividend_frequency_note": frequency_note,
        "beginner_explanation": beginner_explanation,
        "risk_note": (
            "Dividend and distribution data is educational only. Yield changes when the "
            "share price or payment amount changes. A high yield is not automatically a "
            "good investment and may indicate elevated risk."
        ),
        "data_available": info_loaded,
        "no_data_message": no_data_message,
        "source_note": (
            "Source: Yahoo Finance data accessed through yfinance. Values may be delayed, "
            "incomplete or unavailable; confirm important details with the company or fund."
            if info_loaded
            else "Source data is currently unavailable from yfinance."
        ),
    }
    DIVIDEND_CONTEXT_CACHE[cleaned_symbol] = {
        "timestamp": now,
        "context": dict(context),
    }

    return context


# --- Helper for rendering dividend/distribution snapshot HTML for stock pages ---
def render_dividend_snapshot_html(dividend_context):
    context = dividend_context or {}
    label = context.get("dividend_label") or "Dividend"
    is_etf = bool(context.get("is_etf"))
    section_title = "Distribution snapshot" if is_etf else "Dividend snapshot"
    has_data = bool(context.get("has_dividend_data"))
    no_data_message = context.get("no_data_message") or "No regular dividend or distribution found from the available data."

    rows = ""
    if has_data:
        rows = f"""
        <div class=\"dividend-metric\"><span>{label} yield</span><strong>{context.get('dividend_yield', 'Not available')}</strong></div>
        <div class=\"dividend-metric\"><span>Annual {label.lower()}</span><strong>{context.get('annual_dividend', 'Not available')}</strong></div>
        <div class=\"dividend-metric\"><span>Ex-dividend date</span><strong>{context.get('ex_dividend_date', 'Not available')}</strong></div>
        """
        if not is_etf:
            rows += f"""
            <div class=\"dividend-metric\"><span>Payout ratio</span><strong>{context.get('payout_ratio', 'Not available')}</strong></div>
            """
    else:
        rows = f"""
        <div class=\"dividend-empty\">{no_data_message}</div>
        """

    return f"""
    <section class=\"card dividend-card\" aria-label=\"{section_title}\">
        <p class=\"kicker\">Income education</p>
        <h2>{section_title}</h2>
        <div class=\"dividend-grid\">{rows}</div>
        <p>{context.get('beginner_explanation', '')}</p>
        <p class=\"dividend-note\">{context.get('dividend_frequency_note', '')}</p>
        <p class=\"dividend-risk\">{context.get('risk_note', '')}</p>
        <p class=\"muted\">{context.get('source_note', '')}</p>
    </section>
    """


LEARNING_HIGH_GROWTH_TICKERS = {
    "NVDA", "AMD", "AVGO", "TSLA", "PLTR", "SPCX", "COIN", "MSTR",
}

LEARNING_DIVIDEND_TICKERS = {
    "KO", "PEP", "PG", "JNJ", "MCD", "WMT", "PFE", "MRK", "ABBV",
    "VZ", "T", "XOM", "CVX", "BP.L", "SHEL.L", "HSBA.L", "LLOY.L",
    "BARC.L", "AZN.L", "GSK.L", "VOD.L", "BT-A.L",
}


def build_stock_learning_lesson(symbol, signal, role_profile):
    cleaned_symbol = canonical_stock_symbol(symbol)
    role_key = str((role_profile or {}).get("key") or "research").strip().lower()
    mapped_sector = str(SECTOR_MAP.get(cleaned_symbol) or "").strip()
    universe_sector = ""

    if not mapped_sector:
        universe_item = next(
            (
                item for item in get_stock_universe()
                if str(item.get("ticker") or "").strip().upper() == cleaned_symbol
            ),
            {},
        )
        universe_sector = str(universe_item.get("sector") or "").strip()

    sector = mapped_sector or universe_sector or "Diversified"
    sector_lower = sector.lower()
    crypto_symbols = {"BTC-USD", "ETH-USD", "SOL-USD"}

    if role_key in {"core_etf", "index"} or cleaned_symbol in DIVIDEND_ETF_TICKERS:
        return {
            "rule": "ETF or market index",
            "lesson": "Broad-market ETFs help reduce company-specific risk through diversification.",
            "why": "One company can face a serious setback. A broad fund spreads exposure across many businesses, although market-wide losses can still affect it.",
            "question": "Does this fund genuinely broaden my portfolio, or does it repeat exposure I already own?",
        }

    if cleaned_symbol in crypto_symbols:
        return {
            "rule": "Crypto asset",
            "lesson": "High volatility makes position sizing and risk management more important.",
            "why": "Large price swings can make a small holding dominate how the whole portfolio feels and performs.",
            "question": "Could I tolerate a sharp fall without abandoning my wider investing plan?",
        }

    if "bank" in sector_lower or "financial services" in sector_lower:
        return {
            "rule": "Bank or financial sector",
            "lesson": "Bank performance is influenced by interest rates, lending activity and the wider economy.",
            "why": "Loan demand, funding costs and customer defaults can change together as economic conditions move.",
            "question": "What could changing rates or weaker borrowers mean for this bank’s earnings?",
        }

    if any(word in sector_lower for word in ("energy", "commodity", "materials")):
        return {
            "rule": "Energy or commodity sector",
            "lesson": "Energy companies are often driven by commodity prices as much as company performance.",
            "why": "A well-run business can still face lower profits when oil, gas or other commodity prices fall.",
            "question": "Am I judging the company separately from the commodity cycle affecting it?",
        }

    if "healthcare" in sector_lower:
        return {
            "rule": "Healthcare sector",
            "lesson": "Healthcare demand can remain resilient when weaker economic conditions affect other sectors.",
            "why": "Healthcare can behave differently from growth stocks, but regulation, product pipelines and company-specific risks still matter.",
            "question": "Which risks belong to the healthcare sector, and which are specific to this company?",
        }

    if cleaned_symbol in LEARNING_DIVIDEND_TICKERS:
        return {
            "rule": "Dividend-oriented company",
            "lesson": "Dividend investing is about sustainable cash generation, not simply chasing the highest yield.",
            "why": "Dividends are paid from business cash and can be reduced. A high yield is not free money if the underlying company is weakening.",
            "question": "Could the business keep funding this dividend through a difficult period?",
        }

    if any(word in sector_lower for word in ("consumer defensive", "consumer staple", "staples")):
        return {
            "rule": "Consumer staples sector",
            "lesson": "Consumer staple companies often prioritise steady cash generation rather than rapid growth.",
            "why": "Everyday products can support more stable demand, although competition, costs and valuation still affect returns.",
            "question": "Am I expecting steady compounding from this business, or unrealistic growth?",
        }

    if cleaned_symbol in LEARNING_HIGH_GROWTH_TICKERS:
        return {
            "rule": "High-growth company",
            "lesson": "High-growth businesses can produce strong gains but also experience larger drawdowns.",
            "why": "Their prices often reflect high expectations, so disappointment can cause sharp moves even when the business keeps growing.",
            "question": "How much growth is already expected, and could I stay patient through volatility?",
        }

    if any(word in sector_lower for word in ("technology", "software", "cloud", "semiconductor")) or role_key == "growth":
        return {
            "rule": "Technology or growth role",
            "lesson": "Quality technology companies can remain expensive for long periods, so patience and business quality both matter.",
            "why": "Long-term investors often compare durable growth and competitive strength with the price they are being asked to pay.",
            "question": "Would I still value this business if its share price moved sideways for a year?",
        }

    if any(word in sector_lower for word in ("industrial", "aerospace", "defence", "defense")) or role_key == "industrial":
        return {
            "rule": "Industrial or defence sector",
            "lesson": "Industrial businesses often reward investors who understand cycles, contracts and long project timelines.",
            "why": "Orders can provide useful visibility, but costs, policy changes and economic slowdowns can still affect delivery and profits.",
            "question": "Which part of the investment case depends on the economic cycle or future contracts?",
        }

    if role_key == "defensive":
        return {
            "rule": "Defensive portfolio role",
            "lesson": "Defensive businesses can add balance, but lower volatility does not mean no risk.",
            "why": "Stable demand may soften economic pressure while debt, regulation, competition and valuation remain important.",
            "question": "What makes this business defensive, and what could still weaken it?",
        }

    if role_key in {"cyclical", "quality"}:
        return {
            "rule": "Cyclical or quality portfolio role",
            "lesson": "A strong business and a suitable investment price are related, but they are not the same thing.",
            "why": "Investors often study business durability, economic sensitivity and valuation together instead of relying on one attractive feature.",
            "question": "Which assumption would matter most if I reviewed a similar company?",
        }

    signal_wording = {
        "BUY": "Positive research views still need a clear reason, a risk check and patience.",
        "SELL": "Cautious research views are most useful when they identify which evidence could change.",
    }.get(str(signal or "").upper(), "Balanced research views become more useful when investors define what would change their minds.")

    return {
        "rule": "General research discipline",
        "lesson": signal_wording,
        "why": "A repeatable research process helps investors compare opportunities without treating any single signal as a complete answer.",
        "question": "If another company looked similar, would the same investing principle still apply?",
    }


def get_premium_report(symbol, ai_context):
    cleaned_symbol = symbol.strip().upper()
    signal = ai_context.get("signal", "HOLD")
    confidence_value = confidence_number(ai_context.get("confidence", "0%"))
    role_profile = classify_portfolio_role(cleaned_symbol)

    strength = signal_strength_label(ai_context["confidence"])
    portfolio_role = role_profile["label"]
    decision_use = role_profile["decision_use"]
    concentration_note = role_profile["concentration_note"]
    role_key = role_profile.get("key", "research")
    learning_lesson = build_stock_learning_lesson(cleaned_symbol, signal, role_profile)
    portfolio_builder = build_portfolio_builder_education(cleaned_symbol, role_profile)

    if signal == "SELL":
        risk_level = "Higher caution"
        confidence_read = "The scanner is flagging weakness, so the useful action is risk review rather than chasing upside."
    elif confidence_value >= 80:
        risk_level = "Medium risk"
        confidence_read = "Confidence is strong for the current signal, but it still needs price, news and portfolio-fit confirmation."
    elif confidence_value >= 60:
        risk_level = "Medium / watch closely"
        confidence_read = "Confidence is useful but not decisive, so treat this as a research prompt rather than a conclusion."
    else:
        risk_level = "Early / watchlist"
        confidence_read = "Confidence is not strong enough for a firm read, so the next trigger matters more than the score."

    if role_key == "growth":
        portfolio_fit_points = [
            "Adds growth exposure and may increase sensitivity to AI, technology, momentum or crypto-style volatility.",
            "Check duplicate exposure if you already own Nasdaq-heavy ETFs, mega-cap technology or other high-growth names.",
            "Best treated as a satellite idea unless it is already part of your deliberate core allocation.",
        ]
    elif role_key in {"defensive", "core_etf", "broad_market_etf", "index"}:
        portfolio_fit_points = [
            "May add defensive balance or broad market context rather than a narrow growth bet.",
            "Check whether it overlaps with existing ETFs or defensive holdings before adding more.",
            "Useful for comparing whether the portfolio is too concentrated in one sector or style.",
        ]
    elif role_key in {"cyclical", "industrial"}:
        portfolio_fit_points = [
            "Adds cyclical, industrial or macro-sensitive exposure that can behave differently from growth stocks.",
            "Check whether you already have bank, energy, commodity, defence or economically sensitive holdings.",
            "Position sizing matters because cycles, policy headlines and commodity moves can drive the thesis.",
        ]
    elif role_key == "quality":
        portfolio_fit_points = [
            "May add quality or durable business exposure, but quality stocks can still duplicate mega-cap or consumer exposure.",
            "Check valuation, sector weight and overlap with existing ETFs before increasing exposure.",
            "Useful as a core-style equity candidate only if the risk and time horizon fit.",
        ]
    else:
        portfolio_fit_points = [
            "Treat as a research candidate until you can clearly describe its portfolio role.",
            "Check whether it duplicates a sector, theme, ETF holding or risk you already own.",
            "Decide whether it is core, satellite, dividend, defensive, cyclical or speculative before acting.",
        ]

    if signal == "BUY" and confidence_value >= 80:
        readiness = "Strong research candidate"
        action_frame = "Research further before buying; the signal is strong, but still needs risk and portfolio-fit checks."
        signal_meaning = "The current StockRadar inputs lean constructive with stronger confidence. That makes this a research priority, not an instruction to buy."
        stronger_evidence = "Confidence stays above 80% while price action, company news and fundamentals continue to support the constructive setup."
        weaker_evidence = "Confidence falls, momentum fades or new business information challenges the reason behind the signal."
        common_mistake = "Chasing a strong BUY label without checking valuation, overlap, risk tolerance or what would invalidate the research case."
        investor_lesson = "A positive signal is a starting point for research, not a reason to ignore valuation, risk or portfolio overlap."
    elif signal == "BUY":
        readiness = "Positive but not automatic"
        action_frame = "Worth researching, but wait for stronger evidence if risk or valuation feels stretched."
        signal_meaning = "The current inputs lean constructive, but confidence is not strong enough to treat the signal as a conclusion."
        stronger_evidence = "Confidence improves, price strength holds and relevant news or fundamentals support the same direction."
        weaker_evidence = "Confidence weakens, price strength fades or the supporting business context becomes less convincing."
        common_mistake = "Treating an early BUY prompt as certainty instead of waiting for confirmation and checking portfolio fit."
        investor_lesson = "A positive signal is a starting point for research, not a reason to ignore valuation, risk or portfolio overlap."
    elif signal == "SELL":
        readiness = "Caution zone"
        action_frame = "Avoid rushing in. Understand why the scanner is flagging weakness before considering exposure."
        signal_meaning = "The scanner is highlighting weakness or downside pressure. Use it to review risk, not to assume the stock must fall."
        stronger_evidence = "The stock stabilises, relative strength improves and new evidence weakens the current caution case."
        weaker_evidence = "Lower highs, weaker confidence or negative business evidence continue to reinforce the cautious setup."
        common_mistake = "Assuming SELL guarantees a collapse, or reacting without checking the time horizon and the evidence behind the warning."
        investor_lesson = "A cautious signal is a prompt to examine risk and evidence, not proof that a stock must fall."
    else:
        readiness = "Watch and learn"
        action_frame = "Keep on the watchlist until the signal, confidence or thesis becomes clearer."
        signal_meaning = "The current inputs do not show a clear constructive or caution direction. The useful decision is what evidence to wait for."
        stronger_evidence = "A clearer trend, confidence upgrade or supportive company evidence turns the balanced setup into a stronger research case."
        weaker_evidence = "Confidence deteriorates, price action weakens or new information increases uncertainty around the setup."
        common_mistake = "Reading HOLD or WATCH as ‘do nothing forever’ instead of defining the trigger that would prompt another review."
        investor_lesson = "A balanced signal teaches you to define what evidence would change your view rather than acting without a clear trigger."

    score_breakdown = [
        {"label": "Signal strength", "text": f"{signal} signal with {strength.lower()} strength based on the current StockRadar confidence input."},
        {"label": "Confidence", "text": confidence_read},
        {"label": "Portfolio role", "text": f"{portfolio_role}: {decision_use}"},
        {"label": "Risk level", "text": f"{risk_level}. {ai_context['risk_view']}"},
        {"label": "Concentration warning", "text": concentration_note},
        {"label": "Watch-next trigger", "text": ai_context["watch_next"]},
    ]

    checklist = [
        "Similar exposure: do I already own this sector, ETF theme, mega-cap cluster or risk somewhere else?",
        "Time horizon: would this still make sense for my planned holding period, not just this week's chart?",
        f"Risk fit: does the {risk_level.lower()} setup match the amount I can tolerate moving up and down?",
        f"Watch trigger: what should I look for next? {ai_context['watch_next']}",
        f"Portfolio role: is this core, satellite, dividend/income, defensive, cyclical or speculative? Current read: {portfolio_role}.",
        "Stop rule: what news, price action or business evidence would make me wait or walk away?",
    ]


    return {
        "headline": f"{stock_display_label(cleaned_symbol)} Premium Decision Panel",
        "summary": "Free shows the current signal. Premium explains what it may mean, why it matters, where it fits and what to check before acting.",
        "confidence": ai_context["confidence"],
        "meter": confidence_meter(ai_context["confidence"]),
        "strength": strength,
        "risk": ai_context["risk_view"],
        "risk_level": risk_level,
        "next_move": ai_context["watch_next"],
        "pro_angle": "Use Premium as a structured decision check, not as a buy/sell instruction.",
        "portfolio_role": portfolio_role,
        "decision_use": decision_use,
        "concentration_note": concentration_note,
        "portfolio_fit_points": portfolio_fit_points,
        "readiness": readiness,
        "action_frame": action_frame,
        "signal_reason": ai_context["reason"],
        "momentum_view": ai_context["momentum_view"],
        "signal_meaning": signal_meaning,
        "confidence_read": confidence_read,
        "stronger_evidence": stronger_evidence,
        "weaker_evidence": weaker_evidence,
        "common_mistake": common_mistake,
        "investor_lesson": investor_lesson,
        "learning_lesson": learning_lesson,
        "portfolio_builder": portfolio_builder,
        "score_breakdown": score_breakdown,
        "checklist": checklist,
    }


def compare_signal_rank(signal):
    return {
        "BUY": 3,
        "HOLD": 2,
        "WATCH": 2,
        "SELL": 1,
    }.get(str(signal or "").upper(), 2)


def build_compare_stock_context(symbol):
    cleaned_symbol = canonical_stock_symbol(symbol)
    ai_context = get_stock_ai_context(cleaned_symbol)
    premium_report = get_premium_report(cleaned_symbol, ai_context)
    dividend_context = get_dividend_context(cleaned_symbol)

    income_text = dividend_context.get("no_data_message")
    if dividend_context.get("has_dividend_data"):
        income_text = (
            f"{dividend_context.get('dividend_label', 'Dividend')} yield: "
            f"{dividend_context.get('dividend_yield', 'Not available')}. "
            f"Annual {dividend_context.get('dividend_label', 'Dividend').lower()}: "
            f"{dividend_context.get('annual_dividend', 'Not available')}."
        )

    return {
        "symbol": cleaned_symbol,
        "label": stock_display_label(cleaned_symbol),
        "ai": ai_context,
        "report": premium_report,
        "dividend": dividend_context,
        "income_text": income_text,
        "confidence_value": confidence_number(ai_context.get("confidence", "0%")),
        "signal_rank": compare_signal_rank(ai_context.get("signal")),
    }


def build_compare_stock_teaser_context(symbol):
    cleaned_symbol = canonical_stock_symbol(symbol)
    return {
        "symbol": cleaned_symbol,
        "label": stock_display_label(cleaned_symbol),
        "ai": get_stock_ai_context(cleaned_symbol),
    }


def compare_strength_summary(left, right):
    left_signal = left["ai"].get("signal", "HOLD")
    right_signal = right["ai"].get("signal", "HOLD")

    if left_signal == "SELL" and right_signal == "SELL":
        return (
            "Neither stock currently shows a stronger constructive research case. "
            "Both are in caution-review territory, so compare what could change the risk picture before drawing conclusions."
        )

    if left["signal_rank"] == right["signal_rank"]:
        if abs(left["confidence_value"] - right["confidence_value"]) < 5:
            return (
                f"{left['label']} and {right['label']} look broadly similar on the current signal layer. "
                "Use the portfolio role, risk notes and watch-next triggers to decide which research case is clearer."
            )
        stronger = left if left["confidence_value"] > right["confidence_value"] else right
        other = right if stronger is left else left
        return (
            f"{stronger['label']} has the stronger current research case by signal confidence versus {other['label']}. "
            "That is not a guaranteed winner; it means the next step is to review risk, valuation, portfolio overlap and the watch-next trigger."
        )

    stronger = left if left["signal_rank"] > right["signal_rank"] else right
    other = right if stronger is left else left
    return (
        f"{stronger['label']} has the stronger current research case because its signal is more constructive than {other['label']} right now. "
        "This is educational context only, not a buy instruction or a return forecast."
    )


def before_you_choose_checklist(left, right):
    return [
        f"Similar exposure: do I already own either {left['label']} or {right['label']} through an ETF, sector fund or similar company?",
        "Time horizon: which research case still makes sense for my planned holding period if short-term momentum fades?",
        f"Risk fit: am I more comfortable with {left['report']['risk_level'].lower()} or {right['report']['risk_level'].lower()}?",
        "Portfolio role: which one has the clearer purpose - core, satellite, dividend/income, defensive, cyclical or speculative?",
        "Watch-next trigger: what price, headline, signal change or confidence update would make me review the comparison again?",
    ]


def compare_portfolio_role_education(left, right):
    left_builder = left["report"]["portfolio_builder"]
    right_builder = right["report"]["portfolio_builder"]
    left_key = left_builder["role_key"]
    right_key = right_builder["role_key"]
    fund_keys = {"broad_market_etf", "core_etf"}

    if left_key == right_key:
        overlap_summary = (
            f"{left['label']} and {right['label']} may serve a similar portfolio role. "
            "Compare their underlying holdings and risk drivers before treating them as diversification."
        )
    elif left_key in fund_keys and right_key in fund_keys:
        overlap_summary = (
            "Both are funds, but two fund names can still hold many of the same companies. "
            "Compare their largest holdings, sector weights and index methods."
        )
    elif left_key == "growth" or right_key == "growth":
        overlap_summary = (
            "Check whether either holding repeats technology, mega-cap or growth exposure already present through the other holding or a broad fund."
        )
    elif left_key == "cyclical" and right_key == "cyclical":
        overlap_summary = (
            "Both holdings may depend on economic or sector cycles. Different industries can still respond to shared macro conditions."
        )
    else:
        overlap_summary = (
            "Different role labels do not prove diversification. Compare the underlying companies, sectors and economic drivers before assuming the risks are genuinely different."
        )

    return {
        "left": left_builder,
        "right": right_builder,
        "overlap_summary": overlap_summary,
    }


compare_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Compare Stocks — StockRadar</title>
<meta name="description" content="Compare two stocks with StockRadar Premium decision context. Free users can preview the tool; Premium unlocks the full comparison.">
<style>
*{box-sizing:border-box;}
body{margin:0;background:radial-gradient(circle at 18% 8%,rgba(0,255,170,0.12),transparent 28%),linear-gradient(135deg,#08111c,#101827);color:#e5edf5;font-family:Arial,sans-serif;min-height:100vh;padding:42px 22px;}
.wrap{max-width:1120px;margin:0 auto;}
a{color:#38bdf8;font-weight:900;text-decoration:none;}
.card{background:linear-gradient(180deg,rgba(18,29,42,0.97),rgba(12,22,33,0.97));border:1px solid rgba(148,163,184,0.16);border-radius:28px;padding:30px;box-shadow:0 24px 70px rgba(0,0,0,0.30);margin-bottom:22px;}
.kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
h1{font-size:clamp(38px,6vw,58px);line-height:1.04;margin:0 0 16px 0;letter-spacing:0;}
h2{margin:0 0 12px 0;color:#f8fafc;}
h3{margin:0 0 8px 0;color:#f8fafc;}
p,li,td{color:#cbd5e1;line-height:1.7;}
.muted{color:#94a3b8;}
form{display:grid;grid-template-columns:1fr 1fr auto;gap:10px;margin-top:20px;align-items:end;}
label{display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;font-weight:950;margin-bottom:7px;}
input{width:100%;border:1px solid rgba(148,163,184,0.24);background:#07111d;color:#e5edf5;border-radius:15px;padding:14px 15px;font-size:16px;font-weight:800;}
button,.button{border:0;display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#061018;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;text-decoration:none;text-align:center;}
.ghost-button{display:inline-block;border:1px solid rgba(148,163,184,0.22);background:rgba(148,163,184,0.08);color:#e2e8f0;border-radius:15px;padding:13px 16px;font-weight:950;text-decoration:none;margin:6px 8px 0 0;}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;}
.three-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:16px;}
.box{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;line-height:1.6;}
.box strong{display:block;color:#f8fafc;font-size:18px;margin-bottom:6px;}
.signal-pill{display:inline-block;border-radius:999px;padding:7px 11px;font-weight:950;font-size:12px;letter-spacing:0.04em;background:rgba(245,158,11,0.13);color:#fde68a;}
.signal-pill.buy{background:rgba(34,197,94,0.14);color:#bbf7d0;}
.signal-pill.sell{background:rgba(239,68,68,0.14);color:#fecaca;}
.signal-pill.hold,.signal-pill.watch{background:rgba(245,158,11,0.14);color:#fde68a;}
table{width:100%;border-collapse:collapse;margin-top:14px;}
th,td{text-align:left;padding:13px;border-bottom:1px solid rgba(255,255,255,0.08);vertical-align:top;}
th{color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
.premium-tool-note{display:inline-block;background:rgba(255,184,107,0.13);border:1px solid rgba(255,184,107,0.26);color:#fed7aa;border-radius:999px;padding:7px 11px;font-weight:950;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:12px;}
.locked{background:linear-gradient(135deg,rgba(127,29,29,0.34),rgba(245,158,11,0.11));border:1px solid rgba(248,113,113,0.32);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;box-shadow:0 18px 46px rgba(0,0,0,0.22);}
.summary{background:rgba(0,255,170,0.09);border:1px solid rgba(0,255,170,0.18);border-radius:20px;padding:18px;color:#d1fae5;line-height:1.7;}
.warning{background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.20);border-radius:20px;padding:18px;color:#fde68a;line-height:1.7;}
ul{padding-left:22px;margin:12px 0 0;}
@media(max-width:820px){body{padding:24px 16px;}.card{padding:24px 20px;border-radius:24px;}form,.grid,.three-grid{grid-template-columns:1fr;}button,.button,.ghost-button{width:100%;}.ghost-button{margin-right:0;}table{display:block;overflow-x:auto;}th,td{min-width:150px;}}
</style>
</head>
<body>
<div class="wrap">
    <a href="/">← Back to dashboard</a>
    <div class="card">
        <span class="premium-tool-note">Premium feature</span>
        <p class="kicker">Compare Stocks</p>
        <h1>Compare two stocks before choosing what to research next.</h1>
        <p>Free users can enter two tickers and see the locked teaser. Premium unlocks the full comparison: confidence, portfolio role, risk, income context, fit notes and what to watch next.</p>
        <form method="get" action="/compare">
            <div>
                <label for="symbol_a">First ticker</label>
                <input id="symbol_a" name="symbol_a" value="{{ symbol_a or '' }}" placeholder="MSFT" autocomplete="off">
            </div>
            <div>
                <label for="symbol_b">Second ticker</label>
                <input id="symbol_b" name="symbol_b" value="{{ symbol_b or '' }}" placeholder="GOOGL" autocomplete="off">
            </div>
            <button type="submit">Compare</button>
        </form>
        {% if error_message %}<p class="warning">{{ error_message }}</p>{% endif %}
        {% if not has_pair %}
        <div class="three-grid">
            <div class="box"><strong>Signal vs decision</strong><span>Free pages show the signal. Premium explains which decision checks matter before comparing two names.</span></div>
            <div class="box"><strong>Portfolio fit</strong><span>Compare whether one stock adds growth, defensive balance, income context or duplicate exposure risk.</span></div>
            <div class="box"><strong>Examples</strong><span><a href="/compare/MSFT/GOOGL">MSFT vs GOOGL</a><br><a href="/compare/KO/MCD">KO vs MCD</a><br><a href="/compare/SPY/QQQ">SPY vs QQQ</a></span></div>
        </div>
        {% endif %}
    </div>

    {% if has_pair and not has_premium_access %}
    <div class="card">
        <p class="kicker">Locked Premium Comparison</p>
        <h2>{{ left.label }} vs {{ right.label }}</h2>
        <p><strong>Free shows each signal. Premium compares the decision.</strong></p>
        <p class="muted">You can check each stock page for the free signal. The side-by-side decision read is a Premium feature.</p>
        <div class="grid">
            <div class="box">
                <strong>{{ left.label }}</strong>
                <span class="signal-pill {{ left.ai.signal|lower }}">{{ left.ai.signal }}</span>
                <p class="muted">Free confidence preview: {{ left.ai.confidence }}</p>
                <a href="/stock/{{ left.symbol }}">Open free stock page</a>
            </div>
            <div class="box">
                <strong>{{ right.label }}</strong>
                <span class="signal-pill {{ right.ai.signal|lower }}">{{ right.ai.signal }}</span>
                <p class="muted">Free confidence preview: {{ right.ai.confidence }}</p>
                <a href="/stock/{{ right.symbol }}">Open free stock page</a>
            </div>
        </div>
        <div class="locked" style="margin-top:18px;">
            <strong>Locked Premium teaser:</strong> Unlock the full comparison layer for Decision Score read, Portfolio Fit notes, risk level, dividend/income context, watch-next triggers and a Before You Choose checklist.
        </div>
        <a class="button" href="/upgrade" style="margin-top:16px;">Unlock Premium - £5/month</a>
        <p class="muted">Educational research only. No guaranteed winner, no buy/sell instruction and no return promise.</p>
    </div>
    {% endif %}

    {% if has_pair and has_premium_access %}
    <div class="card">
        <p class="kicker">Premium Comparison</p>
        <h2>{{ left.label }} vs {{ right.label }}</h2>
        <div class="summary"><strong>Key difference to research first</strong><br>{{ strength_summary }}</div>
    </div>

    <div class="card">
        <h2>Signal and Confidence Comparison</h2>
        <table>
            <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Signal strength</th><th>Decision score read</th></tr>
            <tr><td><a href="/stock/{{ left.symbol }}">{{ left.label }}</a></td><td><span class="signal-pill {{ left.ai.signal|lower }}">{{ left.ai.signal }}</span></td><td>{{ left.ai.confidence }}</td><td>{{ left.ai.strength_label }}</td><td>{{ left.report.readiness }}. {{ left.report.action_frame }}</td></tr>
            <tr><td><a href="/stock/{{ right.symbol }}">{{ right.label }}</a></td><td><span class="signal-pill {{ right.ai.signal|lower }}">{{ right.ai.signal }}</span></td><td>{{ right.ai.confidence }}</td><td>{{ right.ai.strength_label }}</td><td>{{ right.report.readiness }}. {{ right.report.action_frame }}</td></tr>
        </table>
    </div>

    <div class="card">
        <h2>Portfolio Role and Risk</h2>
        <table>
            <tr><th>Stock</th><th>Portfolio role</th><th>Risk level</th><th>Watch-next trigger</th></tr>
            <tr><td>{{ left.label }}</td><td>{{ left.report.portfolio_role }}<br><span class="muted">{{ left.report.decision_use }}</span></td><td>{{ left.report.risk_level }}<br><span class="muted">{{ left.ai.risk_view }}</span></td><td>{{ left.ai.watch_next }}</td></tr>
            <tr><td>{{ right.label }}</td><td>{{ right.report.portfolio_role }}<br><span class="muted">{{ right.report.decision_use }}</span></td><td>{{ right.report.risk_level }}<br><span class="muted">{{ right.ai.risk_view }}</span></td><td>{{ right.ai.watch_next }}</td></tr>
        </table>
    </div>

    <section class="card" aria-labelledby="compare-portfolio-roles-heading">
        <p class="kicker">Premium portfolio education</p>
        <h2 id="compare-portfolio-roles-heading">Compare portfolio roles</h2>
        <p>Two strong companies may still serve the same role or expose a portfolio to the same risks.</p>
        <div class="grid">
            <article class="box">
                <strong>{{ left.label }}</strong>
                <span>{{ portfolio_role_comparison.left.role_label }}</span>
                <p class="muted"><b>Core or satellite:</b> {{ portfolio_role_comparison.left.core_label }}. {{ portfolio_role_comparison.left.core_or_satellite }}</p>
            </article>
            <article class="box">
                <strong>{{ right.label }}</strong>
                <span>{{ portfolio_role_comparison.right.role_label }}</span>
                <p class="muted"><b>Core or satellite:</b> {{ portfolio_role_comparison.right.core_label }}. {{ portfolio_role_comparison.right.core_or_satellite }}</p>
            </article>
        </div>
        <p class="warning"><strong>Similar exposure to check:</strong> {{ portfolio_role_comparison.overlap_summary }}</p>
        <p class="muted">Portfolio examples are general education only. Appropriate diversification and position size depend on personal circumstances, goals and risk tolerance.</p>
    </section>

    <div class="card">
        <h2>Dividend / Income Context</h2>
        <div class="grid">
            <div class="box"><strong>{{ left.label }}</strong><span>{{ left.income_text }}</span><p class="muted">{{ left.dividend.source_note }}</p></div>
            <div class="box"><strong>{{ right.label }}</strong><span>{{ right.income_text }}</span><p class="muted">{{ right.dividend.source_note }}</p></div>
        </div>
    </div>

    <div class="card">
        <h2>Portfolio Fit Notes</h2>
        <div class="grid">
            <div class="box">
                <strong>{{ left.label }}</strong>
                <ul>{% for item in left.report.portfolio_fit_points %}<li>{{ item }}</li>{% endfor %}</ul>
                <p class="warning">{{ left.report.concentration_note }}</p>
            </div>
            <div class="box">
                <strong>{{ right.label }}</strong>
                <ul>{% for item in right.report.portfolio_fit_points %}<li>{{ item }}</li>{% endfor %}</ul>
                <p class="warning">{{ right.report.concentration_note }}</p>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>Before You Choose</h2>
        <p>Use this as a research checklist before treating either stock as a stronger candidate.</p>
        <ul>{% for item in checklist %}<li>{{ item }}</li>{% endfor %}</ul>
        <p class="muted">StockRadar compares research context only. It does not know your full financial situation and does not provide personal investment advice.</p>
    </div>
    {% endif %}
    {{ disclaimer_footer() | safe }}
</div>
</body>
</html>
"""


def render_compare_page(symbol_a="", symbol_b=""):
    raw_a = str(symbol_a or "").strip()
    raw_b = str(symbol_b or "").strip()
    error_message = ""
    left = None
    right = None
    has_pair = False
    strength_summary = ""
    checklist = []
    portfolio_role_comparison = None
    has_premium_access = premium_has_access()

    if raw_a or raw_b:
        if not raw_a or not raw_b:
            error_message = "Enter two tickers to compare, for example MSFT and GOOGL."
        else:
            if has_premium_access:
                left = build_compare_stock_context(raw_a)
                right = build_compare_stock_context(raw_b)
            else:
                left = build_compare_stock_teaser_context(raw_a)
                right = build_compare_stock_teaser_context(raw_b)
            has_pair = True
            if left["symbol"] == right["symbol"]:
                error_message = "Choose two different tickers so the comparison is useful."
                has_pair = False
            elif has_premium_access:
                strength_summary = compare_strength_summary(left, right)
                checklist = before_you_choose_checklist(left, right)
                portfolio_role_comparison = compare_portfolio_role_education(left, right)

    return render_template_string(
        compare_html,
        symbol_a=raw_a,
        symbol_b=raw_b,
        left=left,
        right=right,
        has_pair=has_pair,
        has_premium_access=has_premium_access,
        error_message=error_message,
        strength_summary=strength_summary,
        checklist=checklist,
        portfolio_role_comparison=portfolio_role_comparison,
    )


@app.route("/compare")
def compare():
    return render_compare_page(
        request.args.get("symbol_a", ""),
        request.args.get("symbol_b", ""),
    )


@app.route("/compare/<symbol_a>/<symbol_b>")
def compare_direct(symbol_a, symbol_b):
    cleaned_a = canonical_stock_symbol(symbol_a)
    cleaned_b = canonical_stock_symbol(symbol_b)
    if cleaned_a != symbol_a.strip().upper() or cleaned_b != symbol_b.strip().upper():
        return redirect(url_for("compare_direct", symbol_a=cleaned_a, symbol_b=cleaned_b))
    return render_compare_page(cleaned_a, cleaned_b)


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
    <meta name="description" content="Search the StockRadar stock universe by ticker or company and open plain-English signal research pages.">
    <link rel="canonical" href="https://www.stockradarhq.com/universe">
    <meta property="og:title" content="Stock Universe — StockRadar">
    <meta property="og:description" content="Search supported stocks, funds and market names in the StockRadar research universe.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://www.stockradarhq.com/universe">
    <meta property="og:site_name" content="StockRadar">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="Stock Universe — StockRadar">
    <meta name="twitter:description" content="Search supported stocks, funds and market names in the StockRadar research universe.">
    <style>
    body{margin:0;background:radial-gradient(circle at 12% 0%,rgba(0,255,170,0.10),transparent 30%),linear-gradient(135deg,#08111c,#101827);color:#dbe4ee;font-family:Arial,sans-serif;min-height:100vh;padding:42px;}
    .wrap{max-width:1180px;margin:0 auto;}
    .card{background:linear-gradient(180deg,rgba(18,29,42,0.97),rgba(12,22,33,0.97));border:1px solid rgba(148,163,184,0.16);border-radius:28px;padding:30px;box-shadow:0 24px 70px rgba(0,0,0,0.30);margin-bottom:22px;}
    .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
    h1{font-size:44px;line-height:1.04;margin:0 0 14px 0;letter-spacing:-0.04em;}
    p{color:#b9c5d2;line-height:1.75;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    form{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap;}
    input{flex:1;min-width:260px;border:1px solid rgba(148,163,184,0.20);background:#0b1521;color:#e6edf4;border-radius:15px;padding:14px 15px;font-size:15px;}
    button{border:0;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;}
    .newsletter-button{display:inline-block;margin-top:8px;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;padding:13px 17px;border-radius:15px;font-weight:950;text-decoration:none;}
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
            <p>Search {{ total_count }} supported tickers by company name or symbol, then open a stock page for signal context, chart availability and research prompts.</p>
            <form method="get" action="/universe">
                <input name="q" value="{{ query }}" placeholder="Search ticker or company name, e.g. AAPL or Apple">
                <button type="submit">Search</button>
            </form>
        </div>

        <div class="card">
            <p class="kicker">StockRadar Weekly</p>
            <h2>Get the weekly StockRadar watchlist</h2>
            <p>A concise weekly brief covering what’s strengthening, what’s weakening and what deserves further research.</p>
            <a class="newsletter-button" href="/newsletter">Join StockRadar Weekly</a>
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

    if not premium_has_access():
        locked_html = """
        <!DOCTYPE html>
        <html>
        <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Premium Decision Panel — StockRadar</title>
        <style>
        *{box-sizing:border-box;}
        body{margin:0;background:linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
        .wrap{max-width:920px;margin:0 auto;}
        .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:34px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
        .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
        h1{font-size:42px;line-height:1.08;margin:0 0 16px 0;letter-spacing:0;}
        p{color:#cbd5e1;line-height:1.7;}
        a{color:#38bdf8;font-weight:900;text-decoration:none;}
        .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:12px;}
        .locked{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}
        .preview-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0;}
        .preview{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:16px;line-height:1.6;color:#cbd5e1;}
        .preview strong{display:block;color:white;margin-bottom:5px;}
        @media(max-width:760px){body{padding:24px 16px;}.card{padding:24px 20px;border-radius:24px;}.preview-grid{grid-template-columns:1fr;}h1{font-size:32px;}.button{display:block;text-align:center;}}
        </style>
        </head>
        <body>
        <div class="wrap">
            <a href="/stock/{{ symbol }}">← Back to {{ stock_display_label(symbol) }}</a>
            <div class="card">
                <p class="kicker">Premium Decision Layer</p>
                <h1>{{ stock_display_label(symbol) }} Decision Panel</h1>
                <p><strong>Free shows the signal. Premium explains the decision.</strong> It helps you ask better questions before acting without revealing the full Premium answer in this preview.</p>
                <div class="preview-grid">
                    <div class="preview"><strong>Why is this signal showing?</strong>Unlock the plain-English reasoning behind the headline prompt.</div>
                    <div class="preview"><strong>What could weaken it?</strong>See the risk evidence that would make the setup less useful.</div>
                    <div class="preview"><strong>Could it duplicate exposure?</strong>Check possible sector, ETF, theme or mega-cap overlap.</div>
                    <div class="preview"><strong>Where might it fit?</strong>Review core, satellite, defensive, cyclical, income or speculative context.</div>
                    <div class="preview"><strong>What should I watch next?</strong>Define the next signal, price or business evidence to review.</div>
                    <div class="preview"><strong>What mistake should I avoid?</strong>See the common beginner trap linked to this type of signal.</div>
                </div>
                <div class="locked"><strong>Locked preview:</strong> Premium does not promise better returns. It gives you a clearer decision-support checklist for interpreting {{ stock_display_label(symbol) }}.</div>
                <a class="button" href="/upgrade">Unlock Premium</a>
            </div>
            {{ disclaimer_footer() | safe }}
        </div>
        </body>
        </html>
        """
        return render_template_string(locked_html, symbol=cleaned_symbol, context=ai_context)

    panel_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ report.headline }} — StockRadar</title>
    <style>
    *{box-sizing:border-box;}
    :root{--navy:#08111c;--panel:#111d2b;--panel-deep:#0b1623;--text:#f1f5f9;--muted:#aebdca;--green:#4adea3;--amber:#f0c36a;}
    body{margin:0;background:radial-gradient(circle at 12% 6%,rgba(0,255,170,0.10),transparent 30%),linear-gradient(135deg,var(--navy),#101827);color:var(--text);font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
    .wrap{max-width:1120px;margin:0 auto;}
    .back{display:inline-block;margin-bottom:22px;color:#69c9f2;font-weight:900;text-decoration:none;}
    .card{background:linear-gradient(180deg,rgba(18,29,42,0.98),rgba(12,22,33,0.98));border:1px solid rgba(148,163,184,0.16);border-radius:28px;padding:30px;box-shadow:0 24px 70px rgba(0,0,0,0.32);margin-bottom:20px;}
    .summary-card{padding:36px;background:linear-gradient(135deg,rgba(12,47,48,0.94),rgba(28,38,49,0.98) 62%,rgba(72,52,27,0.74));border-color:rgba(74,222,163,0.28);}
    .kicker{color:var(--green);font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:11px;margin:0 0 9px;}
    h1{font-size:clamp(34px,4.2vw,48px);line-height:1.06;margin:0 0 12px;letter-spacing:0;}
    h2{font-size:clamp(23px,2.5vw,30px);line-height:1.16;margin:0 0 12px;color:var(--text);}
    h3{font-size:19px;line-height:1.25;margin:0 0 12px;color:var(--text);}
    p,li{color:var(--muted);line-height:1.65;}
    ul{padding-left:22px;margin:12px 0 0;}
    li{margin-bottom:8px;}
    .identity-line{max-width:780px;margin:0 0 20px;}
    .badges{display:flex;gap:9px;flex-wrap:wrap;margin:18px 0;}
    .badge{display:inline-flex;align-items:center;min-height:34px;padding:7px 11px;border-radius:999px;border:1px solid rgba(148,163,184,0.22);background:rgba(7,17,28,0.60);color:#e5edf5;font-size:12px;font-weight:950;text-transform:uppercase;letter-spacing:0.07em;}
    .badge.buy{border-color:rgba(74,222,128,0.34);color:#bbf7d0;}.badge.hold,.badge.watch{border-color:rgba(240,195,106,0.38);color:#fde68a;}.badge.sell{border-color:rgba(251,113,133,0.32);color:#fecdd3;}
    .readiness{font-size:clamp(27px,3.3vw,38px);margin:0 0 10px;color:#fff;}
    .action-frame{max-width:880px;margin:0;color:#e5edf5;font-size:18px;line-height:1.55;font-weight:800;}
    .disclaimer{margin:18px 0 0;color:#9fb0bf;font-size:13px;line-height:1.55;}
    .decision-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:20px;}
    .decision-card{background:rgba(14,25,38,0.94);border:1px solid rgba(148,163,184,0.16);border-radius:22px;padding:22px;min-width:0;}
    .card-label{display:block;margin-bottom:8px;color:var(--green);font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;}
    .primary-answer{display:block;color:#fff;font-size:20px;font-weight:950;line-height:1.3;overflow-wrap:anywhere;}
    .support{display:block;margin-top:9px;color:var(--muted);font-size:14px;line-height:1.55;overflow-wrap:anywhere;}
    .support strong{color:#dce8f1;}
    .reminder{display:block;margin-top:11px;color:#93a6b7;font-size:12px;line-height:1.5;}
    .caution{background:linear-gradient(145deg,rgba(106,76,24,0.34),rgba(14,25,38,0.96));border-color:rgba(240,195,106,0.30);}
    .caution .card-label{color:#f6d88a;}
    .lesson{background:rgba(74,222,163,0.07);border-color:rgba(74,222,163,0.20);}
    .lesson p{margin:0;color:#d9eee6;font-size:16px;}
    .learning-card{background:linear-gradient(145deg,rgba(21,42,55,0.96),rgba(13,27,40,0.98));border-color:rgba(105,201,242,0.22);}
    .learning-card>p{margin:0;}.learning-subtitle{color:#c5d7e3;font-size:15px;}
    .learning-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px;}
    .learning-part{min-width:0;padding:16px;border-radius:16px;background:rgba(7,17,28,0.52);border:1px solid rgba(148,163,184,0.14);}
    .learning-part span{display:block;margin-bottom:7px;color:#86d8f5;font-size:10px;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;}
    .learning-part p{margin:0;color:#d7e2ea;font-size:14px;line-height:1.58;}
    .learning-goal{margin-top:16px!important;color:#9fb0bf!important;font-size:13px;line-height:1.55;}
    .supporting h2{margin-bottom:6px;}.supporting>p{margin:0 0 16px;}
    details{background:rgba(7,17,28,0.56);border:1px solid rgba(148,163,184,0.14);border-radius:17px;margin-top:10px;overflow:hidden;}
    summary{display:flex;align-items:center;min-height:48px;padding:13px 16px;color:#eef5fa;font-weight:900;cursor:pointer;line-height:1.35;}
    summary:focus-visible{outline:3px solid rgba(105,201,242,0.72);outline-offset:-3px;}
    .detail-body{padding:0 16px 16px;}.detail-body p:last-child{margin-bottom:0;}
    .breakdown{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;}
    .breakdown-item{background:rgba(148,163,184,0.06);border-radius:14px;padding:14px;color:var(--muted);line-height:1.55;}
    .breakdown-item strong{display:block;color:#e9f1f7;margin-bottom:4px;}
    .button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;border-radius:15px;padding:12px 18px;font-weight:950;text-decoration:none;margin-top:14px;}
    .identity-note{margin:18px 0 0;color:#aebdca;font-size:13px;line-height:1.55;}
    @media(max-width:900px){body{padding:24px 16px;}.card,.summary-card{padding:24px 20px;border-radius:24px;}.decision-grid{grid-template-columns:repeat(2,minmax(0,1fr));}.decision-card:last-child{grid-column:1/-1;}.breakdown,.learning-grid{grid-template-columns:1fr;}}
    @media(max-width:640px){.decision-grid{grid-template-columns:1fr;}.decision-card:last-child{grid-column:auto;}.action-frame{font-size:16px;}.badges{gap:7px;}.badge{font-size:11px;}.button{width:100%;text-align:center;}.summary-card{padding:24px 18px;}}
    </style>
    </head>
    <body>
    <main class="wrap">
        <a class="back" href="/stock/{{ symbol }}">← Back to {{ stock_display_label(symbol) }}</a>

        <section class="card summary-card" aria-labelledby="premium-summary-heading">
            <p class="kicker">Premium decision summary</p>
            <h1 id="premium-summary-heading">{{ stock_display_label(symbol) }}</h1>
            <p class="identity-line">Each Premium report is designed to help you understand the decision process, not simply copy a signal.</p>
            <div class="badges" aria-label="Current signal and confidence">
                <span class="badge {{ context.signal|lower }}">Signal: {{ context.signal }}</span>
                <span class="badge">Confidence: {{ report.confidence }}</span>
            </div>
            <h2 class="readiness">{{ report.readiness }}</h2>
            <p class="action-frame">{{ report.action_frame }}</p>
            <p class="disclaimer">Educational decision support only. This is a research prompt, not a personalised recommendation.</p>
        </section>

        <section class="decision-grid" aria-label="Practical decision points">
            <article class="decision-card">
                <span class="card-label">Risk to check</span>
                <strong class="primary-answer">{{ report.risk_level }}</strong>
                <span class="support">{{ report.risk }}</span>
            </article>
            <article class="decision-card">
                <span class="card-label">Portfolio fit</span>
                <strong class="primary-answer">{{ report.portfolio_role }}</strong>
                <span class="support">{{ report.decision_use }}</span>
                <span class="support"><strong>Exposure check:</strong> {{ report.concentration_note }}</span>
                <span class="reminder">Portfolio fit depends on your own goals, circumstances, time horizon and existing exposure.</span>
            </article>
            <article class="decision-card">
                <span class="card-label">Watch next</span>
                <strong class="primary-answer">Next research trigger</strong>
                <span class="support">{{ report.next_move }}</span>
                <span class="support"><strong>What would improve the case:</strong> {{ report.stronger_evidence }}</span>
            </article>
        </section>

        <aside class="card caution" aria-labelledby="mistake-heading">
            <span class="card-label">Investor caution</span>
            <h2 id="mistake-heading">Common mistake to avoid</h2>
            <p>{{ report.common_mistake }}</p>
        </aside>

        <section class="card lesson" aria-labelledby="lesson-heading">
            <span class="card-label">Investor lesson</span>
            <h2 id="lesson-heading">What this teaches you</h2>
            <p>{{ report.investor_lesson }}</p>
        </section>

        <section class="card learning-card" aria-labelledby="learning-heading">
            <span class="card-label">Premium investing principle</span>
            <h2 id="learning-heading">Learn From This Stock</h2>
            <p class="learning-subtitle">This investing principle can help with future decisions.</p>
            <div class="learning-grid">
                <div class="learning-part"><span>Lesson</span><p>{{ report.learning_lesson.lesson }}</p></div>
                <div class="learning-part"><span>Why investors care</span><p>{{ report.learning_lesson.why }}</p></div>
                <div class="learning-part"><span>Question to ask yourself</span><p>{{ report.learning_lesson.question }}</p></div>
            </div>
            <p class="learning-goal">The goal is to help you understand investing principles, not memorise stock signals.</p>
        </section>

        <section class="card supporting" aria-labelledby="supporting-heading">
            <p class="kicker">Supporting detail</p>
            <h2 id="supporting-heading">Explore the reasoning</h2>
            <p>Open the sections that are useful for your research. The essential decision points stay visible above.</p>
            <details open>
                <summary>Why this signal?</summary>
                <div class="detail-body">
                    <p><strong>Plain-English meaning:</strong> {{ report.signal_meaning }}</p>
                    <p><strong>Why it is showing:</strong> {{ report.signal_reason }}</p>
                    <p><strong>How to read confidence:</strong> {{ report.confidence_read }}</p>
                </div>
            </details>
            <details>
                <summary>What would weaken the case?</summary>
                <div class="detail-body"><p>{{ report.weaker_evidence }}</p></div>
            </details>
            <details>
                <summary>Portfolio fit checklist</summary>
                <div class="detail-body">
                    <ul>{% for item in report.portfolio_fit_points %}<li>{{ item }}</li>{% endfor %}</ul>
                    <a class="button" href="/portfolio-fit">Check Portfolio Fit</a>
                </div>
            </details>
            <details>
                <summary>Before you decide checklist</summary>
                <div class="detail-body"><ul>{% for item in report.checklist %}<li>{{ item }}</li>{% endfor %}</ul></div>
            </details>
            <details>
                <summary>Decision score breakdown</summary>
                <div class="detail-body">
                    <p>This structured research read uses the existing signal, confidence, risk and portfolio-role context. It is not a precise prediction.</p>
                    <div class="breakdown">
                        {% for item in report.score_breakdown %}<div class="breakdown-item"><strong>{{ item.label }}</strong>{{ item.text }}</div>{% endfor %}
                    </div>
                </div>
            </details>
            <p class="identity-note">Premium helps you practise risk awareness, portfolio thinking, evidence-based research and patience so you can make more independent decisions.</p>
        </section>
        {{ disclaimer_footer() | safe }}
    </main>
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
    caution_candidates = sell_rows or sorted(hold_rows, key=lambda item: confidence_number(item["confidence"]))
    highest_risk = caution_candidates[0] if caution_candidates else None
    caution_label = "Current SELL warning" if sell_rows else "Lowest-confidence HOLD watch"
    quality_names = [item for item in recommendations if item["ticker"] in {"MSFT", "AAPL", "GOOGL", "AMZN", "META", "V", "MA", "COST"}]
    defensive_names = [item for item in recommendations if item["ticker"] in {"KO", "MCD", "JNJ", "PG", "PEP", "WMT", "AZN.L", "GSK.L"}]
    growth_names = [item for item in recommendations if item["ticker"] in {"NVDA", "AMD", "TSLA", "SMH", "QQQ", "BTC-USD", "ETH-USD", "SOL-USD"}]
    decision_brief = build_premium_decision_brief(recommendations)

    theme_counts = {
        "Quality compounders": len(quality_names),
        "Growth / AI satellites": len(growth_names),
        "Defensive balance": len(defensive_names),
        "Current BUY signals": len(buy_rows),
        "Current SELL warnings": len(sell_rows),
    }

    if not premium_has_access():
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
        h1{font-size:42px;line-height:1.08;margin:0 0 16px 0;letter-spacing:0;}
        p,li{color:#cbd5e1;line-height:1.7;}
        a{color:#38bdf8;font-weight:900;text-decoration:none;}
        .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:14px;}
        .locked{margin-top:20px;background:linear-gradient(135deg,rgba(0,255,170,0.12),rgba(255,184,107,0.13),rgba(56,189,248,0.08));border:1px solid rgba(255,184,107,0.34);border-radius:24px;padding:22px;color:#f8fafc;line-height:1.65;box-shadow:0 22px 58px rgba(0,0,0,0.30);}
        .locked strong{display:block;color:#fed7aa;font-size:18px;margin-bottom:6px;}
        .future-feature{margin-top:20px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.22);border-radius:18px;padding:16px;color:#fde68a;line-height:1.65;}
        .future-feature strong{display:block;color:#fbbf24;margin-bottom:5px;}
        @media(max-width:760px){body{padding:24px 16px;}.card{padding:24px 20px;border-radius:24px;}h1{font-size:32px;}.button{display:block;text-align:center;}}
        </style>
        </head>
        <body>
        <div class="wrap">
            <a href="/">← Back to dashboard</a>
            <div class="card">
                <p class="kicker">Premium Watchlist Intelligence</p>
                <h1>Turn a list of stocks into a decision review.</h1>
                <p>Free shows the watchlist signals. Premium turns them into a dashboard: what looks strongest, what needs caution, and whether the list is leaning too heavily into one style of exposure.</p>
                <ul>
                    <li>Today's Decision Brief: strongest setup, caution zone, ETF/market setup, UK/non-US idea and watchlist idea</li>
                    <li>Strongest current signal with context for why it deserves review</li>
                    <li>Caution zone so weaker setups are not ignored</li>
                    <li>Quality, growth and defensive buckets with plain-English purpose</li>
                    <li>Theme concentration read before adding duplicate exposure</li>
                    <li>Compare Stocks lets Premium users review two tickers side by side before choosing what to research next</li>
                </ul>
                <div class="future-feature">
                    <strong>Coming later: Dividend Dip Tracker</strong>
                    Dividend/distribution snapshots are already live on stock detail pages where data is available. Dividend Dip Tracker is still planned as a future scanner for dividend-related watchlist moves, ex-dividend effects and possible yield-trap risks.
                    <small style="display:block;margin-top:7px;color:#cbd5e1;">Future Premium research feature · Not live yet · Not financial advice</small>
                </div>
                <div class="locked"><strong>Premium Watchlist is locked</strong>Upgrade to turn the watchlist into a clearer decision review: strongest signal, caution zone, ETF/market setup, style buckets and concentration context.</div>
                <a class="button" href="/upgrade">Explore Premium - £5/month</a>
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
    *{box-sizing:border-box;}
    body{margin:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,170,0.15),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;min-height:100vh;padding:46px;}
    .wrap{max-width:1180px;margin:0 auto;}
    .card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:32px;box-shadow:0 30px 85px rgba(0,0,0,0.42);margin-bottom:22px;}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px;}
    .box{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;line-height:1.6;}
    .box strong{display:block;color:white;font-size:18px;margin-bottom:6px;}
    .box span,p,li{color:#cbd5e1;line-height:1.7;}
    .kicker{color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 10px 0;}
    h1{font-size:44px;line-height:1.08;margin:0 0 16px 0;letter-spacing:0;}
    h2{margin:0 0 12px 0;color:#f8fafc;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    table{width:100%;border-collapse:collapse;margin-top:16px;}
    th,td{text-align:left;padding:13px;border-bottom:1px solid rgba(255,255,255,0.08);vertical-align:top;}
    th{color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
    .note{background:rgba(0,255,170,0.09);border:1px solid rgba(0,255,170,0.18);border-radius:20px;padding:18px;color:#d1fae5;line-height:1.7;}
    .future-feature{background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.22);border-radius:20px;padding:18px;color:#fde68a;line-height:1.7;}
    .future-feature strong{display:block;color:#fbbf24;font-size:18px;margin-bottom:5px;}
    @media(max-width:900px){body{padding:24px 16px;}.card{padding:24px 20px;border-radius:24px;}.grid{grid-template-columns:1fr;}h1{font-size:32px;}table{display:block;overflow-x:auto;}th,td{min-width:120px;}.button{display:block;text-align:center;}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a href="/">← Back to dashboard</a>
        <div class="card">
            <p class="kicker">Premium Watchlist Intelligence</p>
            <h1>Decision review for the current StockRadar universe.</h1>
            <p>This turns the signal table into a portfolio-style dashboard: strongest signal, caution zone, ETF/market setup, quality/growth/defensive buckets and theme concentration.</p>
            <div class="grid">
                <div class="box"><strong>Strongest signal</strong>{% if strongest %}<span><a href="/stock/{{ strongest.ticker }}">{{ stock_display_label(strongest.ticker) }}</a> — {{ strongest.signal }} • {{ strongest.confidence }}. Start here, then check risk and portfolio overlap before acting.</span>{% else %}<span>No conviction row available.</span>{% endif %}</div>
                <div class="box"><strong>Caution stock</strong>{% if highest_risk %}<span>{{ caution_label }}: <a href="/stock/{{ highest_risk.ticker }}">{{ stock_display_label(highest_risk.ticker) }}</a> — {{ highest_risk.signal }} • {{ highest_risk.confidence }}. Review what could weaken the thesis before adding exposure.</span>{% else %}<span>No caution row available.</span>{% endif %}</div>
                <div class="box"><strong>Compare next</strong><span>Use Compare Stocks when two names look interesting and you need the decision context side by side.</span><br><a href="/compare">Open Compare Stocks</a></div>
            </div>
        </div>

        <div class="card">
            <h2>Today's Decision Brief</h2>
            <p>A compact Premium scan from the current StockRadar universe. Use it to choose what deserves deeper research first.</p>
            <div class="grid">
                {% if decision_brief.strongest %}<div class="box"><strong>Strongest setup</strong><span><a href="/stock/{{ decision_brief.strongest.ticker }}">{{ decision_brief.strongest.label }}</a> — {{ decision_brief.strongest.signal }} • {{ decision_brief.strongest.confidence }}. {{ decision_brief.strongest.reason }}</span></div>{% endif %}
                {% if decision_brief.caution %}<div class="box"><strong>Caution zone</strong><span><a href="/stock/{{ decision_brief.caution.ticker }}">{{ decision_brief.caution.label }}</a> — {{ decision_brief.caution.signal }}. Review risk before adding exposure.</span></div>{% endif %}
                {% if decision_brief.market_setup %}<div class="box"><strong>ETF / market setup</strong><span><a href="/stock/{{ decision_brief.market_setup.ticker }}">{{ decision_brief.market_setup.label }}</a> — {{ decision_brief.market_setup.signal }} • broad-market context.</span></div>{% endif %}
                {% if decision_brief.non_us %}<div class="box"><strong>UK / non-US idea</strong><span><a href="/stock/{{ decision_brief.non_us.ticker }}">{{ decision_brief.non_us.label }}</a> — {{ decision_brief.non_us.signal }} • regional diversification prompt.</span></div>{% endif %}
                {% if decision_brief.watchlist %}<div class="box"><strong>Watchlist idea</strong><span><a href="/stock/{{ decision_brief.watchlist.ticker }}">{{ decision_brief.watchlist.label }}</a> — check what would make this setup stronger or weaker.</span></div>{% endif %}
                <div class="box"><strong>How to use it</strong><span>Open the stock page, review risk and portfolio fit, then compare candidates before making any independent decision.</span></div>
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
            <p>Quality buckets matter because durable businesses can still become duplicate exposure if you already own broad ETFs or several mega-cap names.</p>
            <table>
                <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in quality_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Quality compounder</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Growth and AI satellites</h2>
            <p>Growth buckets matter because the strongest upside stories often share the same risks: valuation, rates, AI spending cycles and momentum reversals.</p>
            <table>
                <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in growth_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Controlled growth satellite</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h2>Defensive balance candidates</h2>
            <p>Defensive buckets matter because they can reduce dependence on one growth theme, but they still need dividend, debt, valuation and business-quality checks.</p>
            <table>
                <tr><th>Stock</th><th>Signal</th><th>Confidence</th><th>Role</th></tr>
                {% for item in defensive_names[:8] %}
                <tr><td><a href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.signal }}</td><td>{{ item.confidence }}</td><td>Defensive balance</td></tr>
                {% endfor %}
            </table>
            <div class="note">Premium read: do not just chase the strongest BUY signal. Review whether your next addition improves the overall mix.</div>
            <a class="button" href="/portfolio-fit">Check Portfolio Fit</a>
        </div>
        <div class="card">
            <div class="future-feature">
                <strong>Coming later: Dividend Dip Tracker</strong>
                Dividend/distribution snapshots are already live on stock detail pages where data is available. Dividend Dip Tracker is still planned as a future scanner for dividend-related watchlist moves, ex-dividend effects and possible yield-trap risks.
                <small style="display:block;margin-top:7px;color:#cbd5e1;">Future Premium research feature · Not live yet · Not financial advice</small>
            </div>
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
        caution_label=caution_label,
        theme_counts=theme_counts,
        quality_names=quality_names,
        growth_names=growth_names,
        defensive_names=defensive_names,
        decision_brief=decision_brief,
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

    if not premium_has_access():
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
        h1{font-size:42px;line-height:1.08;margin:0 0 16px 0;letter-spacing:0;}
        p,li{color:#cbd5e1;line-height:1.7;}
        a{color:#38bdf8;font-weight:900;text-decoration:none;}
        .button{display:inline-block;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;text-decoration:none;margin-top:12px;}
        .locked{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.65;}
        @media(max-width:760px){body{padding:24px 16px;}.card{padding:24px 20px;border-radius:24px;}h1{font-size:32px;}.button{display:block;text-align:center;}}
        </style>
        </head>
        <body>
        <div class="wrap">
            <a href="/">← Back to dashboard</a>
            <div class="card">
                <p class="kicker">Premium Portfolio Fit Checker</p>
                <h1>Check whether a stock actually fits your portfolio.</h1>
                <p>Premium Portfolio Fit turns a list of holdings into a structure review: growth exposure, defensive balance, dividend or income context, sector concentration and duplicate exposure risk.</p>
                <ul>
                    <li>Portfolio role split</li>
                    <li>Growth, AI and sector concentration warnings</li>
                    <li>Dividend or income-style context where the role looks defensive or yield-sensitive</li>
                    <li>Core versus satellite balance</li>
                    <li>Duplicate exposure checks before adding a similar stock or ETF</li>
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
    h1{font-size:44px;line-height:1.08;margin:0 0 16px 0;letter-spacing:0;}
    h2{margin:0 0 12px 0;color:#f8fafc;}
    a{color:#38bdf8;font-weight:900;text-decoration:none;}
    textarea{width:100%;min-height:130px;background:#020617;border:1px solid rgba(255,255,255,0.13);border-radius:18px;color:white;padding:16px;font-weight:800;outline:none;line-height:1.6;}
    button,.button{display:inline-block;border:none;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;border-radius:15px;padding:14px 18px;font-weight:950;cursor:pointer;text-decoration:none;margin-top:16px;}
    .note{background:rgba(0,255,170,0.09);border:1px solid rgba(0,255,170,0.18);border-radius:20px;padding:18px;color:#d1fae5;line-height:1.7;}
    .warning{background:rgba(239,68,68,0.09);border:1px solid rgba(239,68,68,0.20);border-radius:20px;padding:18px;color:#fecaca;line-height:1.7;}
    @media(max-width:1000px){body{padding:24px 16px;}.card{padding:24px 20px;border-radius:24px;}.grid{grid-template-columns:1fr;}h1{font-size:32px;}button,.button{width:100%;text-align:center;}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <a href="/">← Back to dashboard</a>
        <div class="card">
            <p class="kicker">Premium Portfolio Fit Checker</p>
            <h1>Does the next stock actually fit?</h1>
            <p>Enter current holdings separated by commas. StockRadar will classify the structure and flag growth exposure, defensive balance, income-style context, sector concentration and duplicate exposure before you add more complexity.</p>
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
        This review groups holdings by role so you can see whether the portfolio leans toward growth, defensive balance, income-style exposure, cyclicals or unclassified research ideas.
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
            <h2>How to read the fit</h2>
            <ul>
                <li><strong>Growth exposure:</strong> too many technology, AI, crypto or momentum names can make the portfolio move as one trade.</li>
                <li><strong>Defensive balance:</strong> healthcare, consumer staples, telecom or broad ETFs may reduce dependence on high-growth themes, but still need valuation and debt checks.</li>
                <li><strong>Dividend or income context:</strong> income-style holdings should be checked for payout sustainability, debt and whether the yield is masking weak growth.</li>
                <li><strong>Sector concentration:</strong> several different tickers can still point to the same economic driver, such as rates, oil, AI spending or consumer demand.</li>
                <li><strong>Duplicate exposure:</strong> ETFs may already hold the same mega-cap stocks you are considering as individual positions.</li>
            </ul>
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


def newsletter_data_confidence(item):
    reason = str(item.get("reason") or "").strip().lower()
    confidence = confidence_number(item.get("confidence"))

    if not reason or "included in the 100-stock stockradar universe" in reason:
        return "Low"
    if confidence >= 80:
        return "High"
    if confidence >= 60:
        return "Medium"
    return "Low"


def newsletter_status(signal, ticker=""):
    if ticker == "SPCX":
        return "High Risk"
    if signal == "BUY":
        return "Positive"
    if signal == "SELL":
        return "Caution"
    return "Steady"


def newsletter_plain_english_takeaway(signal, ticker=""):
    if ticker == "SPCX":
        return (
            "SPCX remains high-growth, high-volatility satellite exposure with limited "
            "public-market history. It is a watch item, not a strong-buy signal."
        )
    if signal == "BUY":
        return (
            "The available signal data is constructive and may deserve further research. "
            "This is not an instruction to buy."
        )
    if signal == "SELL":
        return (
            "The available signal data is weakening, so risk deserves closer review. "
            "This is not an instruction to sell."
        )
    return (
        "The evidence is mixed or stable, so waiting for a clearer change may be sensible. "
        "This is not an instruction to hold."
    )


def newsletter_reader_safe_reason(item, ticker=""):
    reason = str(item.get("reason") or "").strip()

    if ticker == "SPCX":
        return (
            "SPCX remains high-growth, high-volatility satellite exposure with limited "
            "public-market history."
        )
    if (
        not reason
        or "included in the 100-stock stockradar universe" in reason.lower()
        or "full scanner csv/api feed" in reason.lower()
    ):
        return "Signal is steady in the current StockRadar universe and remains on the watchlist."
    return reason


def newsletter_headline_is_relevant(article):
    headline = str(article.get("title") or "").strip().lower()
    if not headline:
        return False

    healthcare_exclusion_terms = (
        "fda panel", "flu vaccine", "vaccine", "mrna",
        "clinical trial", "drug approval", "medical study",
    )
    healthcare_market_override_terms = (
        "stock", "stocks", "shares", "market", "markets", "earnings",
        "revenue", "profit", "guidance", "analyst", "nasdaq", "nyse",
        "s&p", "dow", "ftse", "listed", "ticker",
    )
    if (
        any(term in headline for term in healthcare_exclusion_terms)
        and not any(term in headline for term in healthcare_market_override_terms)
    ):
        return False

    priority_terms = (
        "stock market", "stocks", "equities", "shares", "earnings",
        "interest rate", "interest rates", "rate cut", "rate hike",
        "federal reserve", "the fed", "bank of england", "inflation",
        "artificial intelligence", " ai ", "semiconductor", "chipmaker",
        "defence", "defense", "technology", "tech stocks", "wall street",
        "ftse", "s&p 500", "nasdaq", "dow jones", "bond yields",
    )
    market_context_terms = (
        "stock", "stocks", "shares", "equity", "equities", "market",
        "earnings", "revenue", "profit", "forecast", "guidance",
        "investor", "listed", "sector", "index", "fund",
    )
    general_health_terms = (
        "health", "healthcare", "medicine", "medical", "drug", "drugs",
        "disease", "patient", "patients", "clinical", "trial", "research",
        "science", "scientist", "hospital", "vaccine",
    )
    padded_headline = f" {headline} "
    if any(term in padded_headline for term in priority_terms):
        return True

    listed_company_terms = (
        "apple", "microsoft", "nvidia", "tesla", "amazon", "alphabet",
        "google", "meta", "jpmorgan", "goldman", "exxon", "shell", "visa",
        "mastercard", "pfizer", "merck", "abbvie", "unitedhealth",
        "eli lilly", "novo nordisk", "astrazeneca", "gsk", "boeing",
        "lockheed", "raytheon", "intel", "amd", "broadcom",
    )
    if any(term in headline for term in listed_company_terms):
        return True

    if any(term in headline for term in general_health_terms):
        return any(term in headline for term in market_context_terms)

    return False


NEWSLETTER_MEGA_CAP_TECH_TICKERS = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"}
NEWSLETTER_MARKET_TRACKER_TICKERS = ("SPY", "^GSPC", "VUSA.L", "QQQ", "DIA", "IWM", "SMH", "^FTSE")


def newsletter_item_sector(item, ticker=""):
    ticker = str(ticker or item.get("ticker") or "").strip().upper()
    sector = str(item.get("sector") or SECTOR_MAP.get(ticker) or "").strip()
    if ticker.startswith("^"):
        return "Market Index"
    return sector or "Diversified"


def newsletter_is_market_proxy(item, ticker=""):
    ticker = str(ticker or item.get("ticker") or "").strip().upper()
    sector = newsletter_item_sector(item, ticker).lower()
    return (
        ticker in NEWSLETTER_MARKET_TRACKER_TICKERS
        or ticker.startswith("^")
        or "etf" in sector
        or "index" in sector
    )


def newsletter_is_uk_or_non_us(item, ticker=""):
    ticker = str(ticker or item.get("ticker") or "").strip().upper()
    country = str(item.get("country") or item.get("region") or "").strip().upper()
    sector = newsletter_item_sector(item, ticker).lower()
    return ticker.endswith(".L") or ticker == "^FTSE" or country in {"UK", "GB"} or sector.startswith("uk ")


def newsletter_signal_item(item):
    ticker = str(item.get("ticker") or "").strip().upper()
    signal = clean_signal(item.get("signal"), item.get("confidence"))
    return {
        "ticker": ticker,
        "name": stock_display_label(ticker),
        "signal": signal,
        "confidence": normalise_confidence(item.get("confidence")),
        "badge": "WATCH" if ticker == "SPCX" else f"{signal} pattern",
        "status": newsletter_status(signal, ticker),
        "reason": newsletter_reader_safe_reason(item, ticker),
        "plain_english_takeaway": newsletter_plain_english_takeaway(signal, ticker),
        "data_confidence": newsletter_data_confidence(item),
        "sector": newsletter_item_sector(item, ticker),
        "is_market_proxy": newsletter_is_market_proxy(item, ticker),
        "is_uk_or_non_us": newsletter_is_uk_or_non_us(item, ticker),
    }


def newsletter_ranked_signal_candidates(recommendations, signals):
    desired = {signal.upper() for signal in signals}
    candidates = [
        item for item in recommendations
        if str(item.get("ticker") or "").strip()
        and clean_signal(item.get("signal"), item.get("confidence")) in desired
    ]
    return sorted(
        candidates,
        key=lambda item: confidence_number(item.get("confidence")),
        reverse=True,
    )


def build_balanced_newsletter_items(recommendations, signals, limit=4, max_market_proxies=2):
    ranked_rows = newsletter_ranked_signal_candidates(recommendations, signals)
    selected = []
    seen = set()
    mega_cap_count = 0
    market_proxy_count = 0

    def add_item(item):
        nonlocal mega_cap_count, market_proxy_count
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen:
            return False
        is_market_proxy = newsletter_is_market_proxy(item, ticker)
        if is_market_proxy and market_proxy_count >= max_market_proxies:
            return False
        if ticker in NEWSLETTER_MEGA_CAP_TECH_TICKERS and mega_cap_count >= 1:
            return False
        selected.append(item)
        seen.add(ticker)
        if is_market_proxy:
            market_proxy_count += 1
        if ticker in NEWSLETTER_MEGA_CAP_TECH_TICKERS:
            mega_cap_count += 1
        return True

    priority_groups = (
        lambda item: newsletter_is_market_proxy(item),
        lambda item: newsletter_is_uk_or_non_us(item),
        lambda item: str(item.get("ticker") or "").strip().upper() not in NEWSLETTER_MEGA_CAP_TECH_TICKERS,
        lambda item: True,
    )
    for matcher in priority_groups:
        for item in ranked_rows:
            if len(selected) >= limit:
                break
            if matcher(item):
                add_item(item)
        if len(selected) >= limit:
            break

    return [newsletter_signal_item(item) for item in selected[:limit]]


def build_newsletter_caution_items(recommendations, limit=4, max_market_proxies=2):
    caution = build_balanced_newsletter_items(
        recommendations,
        ("SELL",),
        limit,
        max_market_proxies=max_market_proxies,
    )
    if len(caution) >= limit:
        return caution

    seen = {item["ticker"] for item in caution}
    hold_rows = sorted(
        newsletter_ranked_signal_candidates(recommendations, ("HOLD",)),
        key=lambda item: confidence_number(item.get("confidence")),
    )
    for item in hold_rows:
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        if ticker in NEWSLETTER_MEGA_CAP_TECH_TICKERS and any(
            row["ticker"] in NEWSLETTER_MEGA_CAP_TECH_TICKERS for row in caution
        ):
            continue
        caution.append(newsletter_signal_item(item))
        seen.add(ticker)
        if len(caution) >= limit:
            break
    return caution


def build_newsletter_market_tracker(recommendations, limit=6):
    by_ticker = {
        str(item.get("ticker") or "").strip().upper(): item
        for item in recommendations
        if item.get("ticker")
    }
    tracker = []
    for ticker in NEWSLETTER_MARKET_TRACKER_TICKERS:
        item = by_ticker.get(ticker)
        if not item:
            continue
        tracker.append(newsletter_signal_item(item))
        if len(tracker) >= limit:
            break
    return tracker


def build_newsletter_market_week_summary(trending, best_area, risk_area):
    if trending and trending[0].get("headline") != "No relevant market headlines are available":
        headlines = [item["headline"] for item in trending[:3] if item.get("headline")]
        if headlines:
            return (
                "This week, the visible news feed centred on: "
                + "; ".join(headlines)
                + ". StockRadar treats these as context, then checks whether the signal table confirms or challenges the mood."
            )

    return (
        f"With limited relevant headlines available, the weekly read leans on StockRadar signals. "
        f"{best_area} showed the clearest constructive cluster, while {risk_area} is the main area to review for caution."
    )


def build_newsletter_signal_highlights(recommendations, limit=5):
    highlights = []
    seen = set()
    for group in (
        build_balanced_newsletter_items(recommendations, ("BUY",), 2, max_market_proxies=1),
        build_balanced_newsletter_items(recommendations, ("HOLD",), 2, max_market_proxies=1),
        build_newsletter_caution_items(recommendations, 2, max_market_proxies=1),
    ):
        for item in group:
            if item["ticker"] in seen:
                continue
            highlights.append(item)
            seen.add(item["ticker"])
            if len(highlights) >= limit:
                return highlights
    return highlights


def build_newsletter_recommendation_universe():
    recommendation_rows = get_recommendations() or []
    output = [dict(item) for item in recommendation_rows]
    seen = {
        str(item.get("ticker") or "").strip().upper()
        for item in output
        if item.get("ticker")
    }
    universe_rows = get_stock_universe() or []
    universe_index = {
        str(item.get("ticker") or "").strip().upper(): index
        for index, item in enumerate(universe_rows)
        if item.get("ticker")
    }

    for index, item in enumerate(universe_rows):
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen:
            continue

        signal_source = generated_signal_source_ticker(ticker)
        signal_index = universe_index.get(signal_source, index)
        signal, confidence = generated_signal_for_ticker(signal_source, signal_index)
        company_name = str(item.get("name") or ticker).strip()
        sector = str(item.get("sector") or "Diversified").strip()

        output.append({
            "ticker": ticker,
            "signal": signal,
            "confidence": confidence,
            "reason": (
                f"{company_name} is part of the StockRadar tracked stock universe. "
                f"Its current {signal} research prompt reflects deterministic screening "
                f"within the {sector} group and should be checked against live price action, "
                "news and fundamentals."
            ),
            "sector": sector,
        })
        seen.add(ticker)

    return output


def build_newsletter_watchlist(recommendations, limit=8):
    watchlist = []
    seen = set()
    for group in (
        build_newsletter_market_tracker(recommendations, 2),
        build_balanced_newsletter_items(recommendations, ("BUY",), 3, max_market_proxies=1),
        build_balanced_newsletter_items(recommendations, ("HOLD",), 3, max_market_proxies=1),
        build_newsletter_caution_items(recommendations, 3, max_market_proxies=1),
    ):
        for item in group:
            if item["ticker"] in seen:
                continue
            watchlist.append(item)
            seen.add(item["ticker"])
            if len(watchlist) >= limit:
                return watchlist

    return watchlist


def build_free_weekly_newsletter():
    recommendations = build_newsletter_recommendation_universe()
    buy_count, hold_count, sell_count, _ = calculate_counts(recommendations)
    total_count = len(recommendations)
    highlights = build_newsletter_signal_highlights(recommendations)

    sector_strength = {}
    sector_risk = {}
    for item in recommendations:
        sector = str(item.get("sector") or "Other").strip()
        signal = clean_signal(item.get("signal"), item.get("confidence"))
        if signal == "BUY":
            sector_strength[sector] = sector_strength.get(sector, 0) + 1
        elif signal == "SELL":
            sector_risk[sector] = sector_risk.get(sector, 0) + 1

    best_area = (
        max(sector_strength, key=sector_strength.get)
        if sector_strength
        else "Signal pending"
    )
    risk_area = (
        max(sector_risk, key=sector_risk.get)
        if sector_risk
        else "No concentrated risk signal"
    )

    if not total_count:
        market_mood = "Data unavailable"
        market_pulse = "StockRadar tracked stock universe data is currently unavailable. No market direction is inferred."
    elif buy_count > sell_count * 1.5:
        market_mood = "Constructive, with selectivity"
        market_pulse = (
            f"{buy_count} constructive patterns, {hold_count} steady patterns and "
            f"{sell_count} caution patterns appear across the StockRadar tracked stock universe."
        )
    elif sell_count > buy_count:
        market_mood = "Cautious"
        market_pulse = (
            f"Caution patterns ({sell_count}) outnumber constructive patterns ({buy_count}). "
            f"{hold_count} names remain steady or unresolved across the StockRadar tracked stock universe."
        )
    else:
        market_mood = "Mixed"
        market_pulse = (
            f"The StockRadar tracked stock universe is balanced: {buy_count} constructive, "
            f"{hold_count} steady and {sell_count} caution patterns."
        )

    try:
        live_articles = fetch_live_market_news(limit=12)
    except Exception:
        live_articles = []

    relevant_articles = [
        article for article in live_articles
        if newsletter_headline_is_relevant(article)
    ]
    trending = [
        {
            "headline": str(article.get("title") or "Headline unavailable").strip(),
            "source": str(article.get("source") or "Market News").strip(),
        }
        for article in relevant_articles[:3]
        if article.get("title")
    ]

    if not trending:
        trending = [{
            "headline": "No relevant market headlines are available",
            "source": "StockRadar feed check",
        }]

    market_week_summary = build_newsletter_market_week_summary(trending, best_area, risk_area)
    market_tracker = build_newsletter_market_tracker(recommendations)
    what_looked_strong = build_balanced_newsletter_items(recommendations, ("BUY",), 4)
    what_looked_weak = build_newsletter_caution_items(recommendations, 4)

    forecasting = []
    if best_area != "Signal pending":
        forecasting.append(
            f"{best_area} has the largest cluster of constructive patterns, so confirmation or fading strength may matter next."
        )
    else:
        forecasting.append("Sector-strength forecasting is pending because recommendation data is unavailable.")

    if risk_area != "No concentrated risk signal":
        forecasting.append(
            f"{risk_area} has the largest cluster of caution patterns, making it an area to review for weakening sentiment."
        )
    else:
        forecasting.append("No single sector currently dominates the caution signals.")

    if relevant_articles:
        forecasting.append(
            "Repeated headlines can change sentiment quickly; watch whether the current news themes begin to alter signal strength."
        )
    else:
        forecasting.append(
            "Live news is unavailable, so the forecast view is limited to existing StockRadar signal data."
        )

    risk_notes = []
    if sell_count:
        risk_notes.append(
            f"{sell_count} caution patterns are present. They are research prompts to examine downside risk, not instructions to sell."
        )
    else:
        risk_notes.append("No SELL-pattern concentration is visible in the current feed.")
    if not relevant_articles:
        risk_notes.append("No relevant market headlines passed the current filter, so headline-driven risks may be incomplete.")
    risk_notes.append(
        "SPCX remains high-growth, high-volatility satellite exposure with limited public-market history."
    )

    draft = {
        "title": "StockRadar Weekly Brief",
        "generated_at": datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        "opening_line": "Your Friday market brief is ready.",
        "opening_note": (
            "StockRadar turns market noise into plain-English signal checks across stocks, "
            "ETFs and index-style market proxies where supported."
        ),
        "market_mood": market_mood,
        "main_theme": (
            trending[0]["headline"]
            if relevant_articles
            else "Signals first while relevant market headlines are unavailable"
        ),
        "best_looking_area": best_area,
        "risk_area": risk_area,
        "market_pulse": market_pulse,
        "market_week_summary": market_week_summary,
        "tracked_universe_count": total_count,
        "market_tracker": market_tracker,
        "what_looked_strong": what_looked_strong,
        "what_looked_weak": what_looked_weak,
        "signal_highlights": highlights,
        "trending_vs_forecasting": {
            "trending": trending,
            "forecasting": forecasting,
        },
        "watchlist": build_newsletter_watchlist(recommendations),
        "risk_check": risk_notes,
        "disclaimer": (
            "StockRadar provides educational market information and research tools only. "
            "It is not personal financial advice. BUY, HOLD, and SELL signals are research "
            "prompts, not instructions or promises."
        ),
    }
    draft["plain_text"] = build_newsletter_plain_text(draft)
    return draft


def build_newsletter_plain_text(draft):
    lines = [
        draft["title"],
        f"Issue date: {draft.get('issue_date_label', 'Not available')}",
        f"Issue status: {draft.get('issue_status', 'Preview')}",
        f"Last refreshed: {draft.get('last_refreshed', draft['generated_at'])}",
        "",
        "OPENING",
        draft.get("opening_line", "Your Friday market brief is ready."),
        draft.get("opening_note", ""),
        "",
        "THIS WEEK IN THE MARKET",
        f"Mood: {draft['market_mood']}",
        draft["market_pulse"],
        draft.get("market_week_summary", ""),
        f"Main theme: {draft['main_theme']}",
        f"Best-looking area: {draft['best_looking_area']}",
        f"Risk area: {draft['risk_area']}",
        "",
        "WHAT LOOKED STRONG",
    ]

    strong_items = draft.get("what_looked_strong") or draft.get("signal_highlights") or []
    if strong_items:
        for item in strong_items:
            lines.extend([
                f"- {item['name']} — {item['badge']} · {item['status']} · confidence: {item['data_confidence']}",
                f"  Why: {item['reason']}",
                f"  Plain English: {item['plain_english_takeaway']}",
            ])
    else:
        lines.append("- Signal data unavailable.")

    lines.extend(["", "WHAT LOOKED WEAK / CAUTION ZONE"])
    caution_items = draft.get("what_looked_weak") or []
    if caution_items:
        for item in caution_items:
            lines.extend([
                f"- {item['name']} — {item['badge']} · {item['status']} · confidence: {item['data_confidence']}",
                f"  Review: {item['reason']}",
            ])
    else:
        lines.append("- No caution examples are available in the current signal set.")

    lines.extend(["", "MARKET TRACKER"])
    for item in draft.get("market_tracker", []):
        lines.append(
            f"- {item['name']} — {item['signal']} · {item['confidence']}: {item['reason']}"
        )

    lines.extend(["", "NEWS-FEED THEMES"])
    for item in draft["trending_vs_forecasting"]["trending"]:
        lines.append(f"- {item['headline']} ({item['source']})")

    lines.extend(["", "WHAT MAY MATTER NEXT"])
    for item in draft["trending_vs_forecasting"]["forecasting"]:
        lines.append(f"- {item}")

    lines.extend(["", "STOCKRADAR SIGNAL WATCHLIST"])
    for item in draft["watchlist"]:
        lines.append(
            f"- {item['name']} — {item['badge']} · {item['status']}: {item['reason']}"
        )

    lines.extend(["", "RISK CHECK"])
    for item in draft["risk_check"]:
        lines.append(f"- {item}")

    if draft.get("premium_note"):
        lines.extend([
            "",
            "PREMIUM RESEARCH PREVIEW",
            draft["premium_note"],
            f"Explore Premium: {PRODUCTION_BASE_URL}/upgrade",
            f"Premium Watchlist: {PRODUCTION_BASE_URL}/premium-watchlist",
            f"Latest issue: {PRODUCTION_BASE_URL}/newsletter/latest",
            "Research prompts only. Premium tools are not financial advice or buy/sell instructions.",
        ])

    lines.extend(["", draft["disclaimer"]])
    return "\n".join(lines)


def get_weekly_newsletter_issue_date(now=None):
    """Return the Friday issue date used for StockRadar Weekly."""
    london_timezone = ZoneInfo("Europe/London")
    london_now = now or datetime.now(london_timezone)

    if london_now.tzinfo is None:
        london_now = london_now.replace(tzinfo=london_timezone)
    else:
        london_now = london_now.astimezone(london_timezone)

    today = london_now.date()
    weekday = today.weekday()

    if weekday <= 4:
        return today + timedelta(days=4 - weekday)

    return today - timedelta(days=weekday - 4)


def get_weekly_newsletter_status(now=None):
    london_timezone = ZoneInfo("Europe/London")
    london_now = now or datetime.now(london_timezone)
    if london_now.tzinfo is None:
        london_now = london_now.replace(tzinfo=london_timezone)
    else:
        london_now = london_now.astimezone(london_timezone)

    weekday = london_now.weekday()
    if weekday < 4:
        issue_date = get_weekly_newsletter_issue_date(london_now)
        return {
            "key": "preview",
            "label": "Preview",
            "message": f"Preview for Friday {issue_date.strftime('%d %B %Y')}",
            "rss_label": "Preview issue",
            "is_final": False,
        }
    if weekday == 4 and london_now.time() < newsletter_auto_send_time():
        return {
            "key": "friday_preview",
            "label": "Friday preview",
            "message": "Friday issue in progress",
            "rss_label": "Preview issue",
            "is_final": False,
        }
    return {
        "key": "final",
        "label": "Final end-of-week issue",
        "message": "Final end-of-week issue",
        "rss_label": "Final end-of-week issue",
        "is_final": True,
    }


def newsletter_cache_is_fresh(issue_date, issue_status, now=None):
    cached_issue = WEEKLY_NEWSLETTER_ISSUE_CACHE.get("issue")
    cached_generated_at = WEEKLY_NEWSLETTER_ISSUE_CACHE.get("generated_at")
    if (
        cached_issue is None
        or cached_generated_at is None
        or WEEKLY_NEWSLETTER_ISSUE_CACHE.get("issue_date") != issue_date
        or WEEKLY_NEWSLETTER_ISSUE_CACHE.get("issue_status") != issue_status["key"]
    ):
        return False

    if issue_status["is_final"]:
        return True

    london_timezone = ZoneInfo("Europe/London")
    london_now = now or datetime.now(london_timezone)
    if london_now.tzinfo is None:
        london_now = london_now.replace(tzinfo=london_timezone)
    else:
        london_now = london_now.astimezone(london_timezone)

    generated_at = cached_generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=london_timezone)
    else:
        generated_at = generated_at.astimezone(london_timezone)

    age_seconds = (london_now - generated_at).total_seconds()
    return 0 <= age_seconds <= WEEKLY_NEWSLETTER_PREVIEW_CACHE_TTL_SECONDS


def newsletter_issue_date(now=None):
    return get_weekly_newsletter_issue_date(now)


def newsletter_issue_metadata(now=None, generated_at=None, issue_status=None):
    london_timezone = ZoneInfo("Europe/London")
    london_now = now or datetime.now(london_timezone)
    if london_now.tzinfo is None:
        london_now = london_now.replace(tzinfo=london_timezone)
    else:
        london_now = london_now.astimezone(london_timezone)

    issue_date = get_weekly_newsletter_issue_date(london_now)
    status = issue_status or get_weekly_newsletter_status(london_now)
    refreshed_at = generated_at or london_now
    return {
        "title": f"StockRadar Weekly — Friday {issue_date.strftime('%d %B %Y')}",
        "guid": f"stockradar-weekly-{issue_date.isoformat()}",
        "published_at": refreshed_at,
        "issue_date": issue_date.isoformat(),
        "issue_date_label": f"Friday {issue_date.strftime('%d %B %Y')}",
        "issue_status": status["label"],
        "issue_status_key": status["key"],
        "issue_status_message": status["message"],
        "rss_status_label": status["rss_label"],
        "is_final": status["is_final"],
        "generated_at": refreshed_at,
        "generated_at_label": refreshed_at.strftime("%d %B %Y, %H:%M %Z"),
    }


def build_weekly_newsletter_issue(now=None, force_refresh=False):
    london_timezone = ZoneInfo("Europe/London")
    london_now = now or datetime.now(london_timezone)
    if london_now.tzinfo is None:
        london_now = london_now.replace(tzinfo=london_timezone)
    else:
        london_now = london_now.astimezone(london_timezone)

    status = get_weekly_newsletter_status(london_now)
    metadata = newsletter_issue_metadata(london_now, issue_status=status)
    issue_date = metadata["issue_date"]

    if (
        not force_refresh
        and newsletter_cache_is_fresh(issue_date, status, london_now)
    ):
        return WEEKLY_NEWSLETTER_ISSUE_CACHE["issue"]

    refreshed_at = london_now
    metadata = newsletter_issue_metadata(
        london_now,
        generated_at=refreshed_at,
        issue_status=status,
    )
    draft = build_free_weekly_newsletter()
    recommendations = build_newsletter_recommendation_universe()
    buy_rows, hold_rows, sell_rows, conviction_rows = split_rows(recommendations)
    full_signal_rows = {
        signal: [
            item for item in recommendations
            if clean_signal(item.get("signal"), item.get("confidence")) == signal
        ]
        for signal in ("BUY", "HOLD", "SELL")
    }

    def issue_signal_rows(rows, limit):
        output = []
        ranked_rows = sorted(
            rows,
            key=lambda item: confidence_number(item.get("confidence")),
            reverse=True,
        )
        for item in ranked_rows[:limit]:
            ticker = str(item.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            output.append({
                "ticker": ticker,
                "name": stock_display_label(ticker),
                "signal": clean_signal(item.get("signal"), item.get("confidence")),
                "confidence": normalise_confidence(item.get("confidence")),
                "reason": newsletter_reader_safe_reason(item, ticker),
            })
        return output

    draft["title"] = metadata["title"]
    draft["issue_date_label"] = metadata["issue_date_label"]
    draft["issue_status"] = metadata["issue_status"]
    draft["issue_status_message"] = metadata["issue_status_message"]
    draft["is_final"] = metadata["is_final"]
    draft["last_refreshed"] = metadata["generated_at_label"]
    draft["preview_refresh_note"] = (
        "This preview refreshes as StockRadar signals, market news and tracked-stock "
        "data update before the Friday end-of-week issue."
        if not metadata["is_final"]
        else ""
    )
    draft["market_tracker"] = build_newsletter_market_tracker(recommendations)
    draft["what_looked_strong"] = build_balanced_newsletter_items(recommendations, ("BUY",), 4)
    draft["what_looked_weak"] = build_newsletter_caution_items(recommendations, 4)
    draft["signal_watch"] = {
        "strongest_buy": build_balanced_newsletter_items(full_signal_rows["BUY"] or buy_rows, ("BUY",), 3),
        "notable_hold": build_balanced_newsletter_items(full_signal_rows["HOLD"] or hold_rows, ("HOLD",), 3),
        "caution_sell": build_newsletter_caution_items(full_signal_rows["SELL"] or sell_rows, 3),
        "highest_conviction": issue_signal_rows(conviction_rows, 3),
    }
    draft["premium_note"] = (
        "Premium adds the reasoning layer behind these signals: risk read, decision context, "
        "portfolio-fit checks and what to review before acting. It does not provide personal "
        "financial advice or guaranteed outcomes."
    )
    draft["disclaimer"] = (
        "StockRadar provides educational market information and research tools only. "
        "It is not personal financial advice. BUY, HOLD, and SELL signals are research "
        "prompts, not buy/sell instructions or promises."
    )
    draft["plain_text"] = build_newsletter_plain_text(draft)

    issue = {
        "draft": draft,
        "metadata": metadata,
        "summary": draft.get("market_pulse") or "Market pulse data unavailable.",
    }
    WEEKLY_NEWSLETTER_ISSUE_CACHE["issue_date"] = issue_date
    WEEKLY_NEWSLETTER_ISSUE_CACHE["issue_status"] = status["key"]
    WEEKLY_NEWSLETTER_ISSUE_CACHE["generated_at"] = refreshed_at
    WEEKLY_NEWSLETTER_ISSUE_CACHE["issue"] = issue
    return issue


newsletter_issue_body_html = """
<section>
<p><strong>Issue date:</strong> {{ draft.issue_date_label }}</p>
<p><strong>Issue status:</strong> {{ draft.issue_status }}</p>
<p><strong>{{ draft.issue_status_message }}</strong></p>
<p><strong>Last refreshed:</strong> {{ draft.last_refreshed }}</p>
{% if draft.preview_refresh_note %}<p>{{ draft.preview_refresh_note }}</p>{% endif %}
<p><strong>{{ draft.opening_line }}</strong></p>
<p>{{ draft.opening_note }}</p>
</section>
<section>
<h2>This week in the market</h2>
<p style="margin-bottom:16px;"><strong>Market mood:</strong> {{ draft.market_mood }}.</p>
<p style="margin-top:0;">{{ draft.market_pulse }}</p>
<p>{{ draft.market_week_summary }}</p>
</section>
<section>
<h2>What looked strong</h2>
{% if draft.what_looked_strong %}
{% for item in draft.what_looked_strong %}
<article>
<h3>{{ item.name }}</h3>
<p><strong>{{ item.signal }} research prompt</strong> · Confidence: {{ item.confidence }} · {{ item.sector }}</p>
<p><strong>Why it appears:</strong> {{ item.reason }}</p>
</article>
{% endfor %}
{% else %}
<p>Strong-signal examples are unavailable in the current feed.</p>
{% endif %}
</section>
<section>
<h2>What looked weak / caution zone</h2>
{% if draft.what_looked_weak %}
{% for item in draft.what_looked_weak %}
<article>
<h3>{{ item.name }}</h3>
<p><strong>{{ item.signal }} research prompt</strong> · Confidence: {{ item.confidence }} · {{ item.sector }}</p>
<p><strong>Review risk:</strong> {{ item.reason }}</p>
</article>
{% endfor %}
{% else %}
<p>Caution examples are unavailable in the current feed.</p>
{% endif %}
</section>
<section>
<h2>Market tracker</h2>
{% if draft.market_tracker %}
<ul>
{% for item in draft.market_tracker %}
<li><strong>{{ item.name }}</strong> — {{ item.signal }} · {{ item.confidence }}: {{ item.reason }}</li>
{% endfor %}
</ul>
{% else %}
<p>Broad market proxy data is unavailable in the current feed.</p>
{% endif %}
</section>
<section>
<h2>StockRadar signal watchlist</h2>
{% set signal_groups = [
    ("Stronger BUY research prompts", draft.signal_watch.strongest_buy),
    ("Notable HOLD / watchlist names", draft.signal_watch.notable_hold),
    ("Caution / SELL research prompts", draft.signal_watch.caution_sell)
] %}
{% for group_title, items in signal_groups %}
{% if items %}
<h3>{{ group_title }}</h3>
{% for item in items[:2] %}
<article>
<h3>{{ item.name }}</h3>
<p><strong>{{ item.signal }} research prompt</strong> · Confidence: {{ item.confidence }}</p>
<p><strong>Why it appears:</strong> {{ item.reason }}</p>
</article>
{% endfor %}
{% endif %}
{% endfor %}
{% if not draft.signal_watch.strongest_buy and not draft.signal_watch.notable_hold and not draft.signal_watch.caution_sell %}
<p>Signal data unavailable.</p>
{% endif %}
</section>
<section>
<h2>News-feed themes</h2>
<ul>
{% for item in draft.trending_vs_forecasting.trending %}
<li>{{ item.headline }} — {{ item.source }}</li>
{% endfor %}
</ul>
<h3>What may matter next</h3>
<ul>
{% for item in draft.trending_vs_forecasting.forecasting %}
<li>{{ item }}</li>
{% endfor %}
</ul>
</section>
<section>
<h2>Balanced signal examples</h2>
<ul>
{% for item in draft.watchlist %}
<li><strong>{{ item.name }}</strong> — {{ item.badge }} · {{ item.status }}: {{ item.reason }}</li>
{% endfor %}
</ul>
</section>
<section>
<h2>Risk check</h2>
<ul>
{% for item in draft.risk_check %}
<li>{{ item }}</li>
{% endfor %}
</ul>
<p><strong>Educational only.</strong> {{ draft.disclaimer }}</p>
</section>
<section>
<h2>Premium research preview</h2>
<p>{{ draft.premium_note }}</p>
<p>
<a href="{{ production_base_url }}/upgrade">Explore Premium</a> ·
<a href="{{ production_base_url }}/premium-watchlist">Preview Premium Watchlist</a> ·
<a href="{{ production_base_url }}/newsletter/latest">Read the latest StockRadar Weekly issue</a>
</p>
<p><strong>Research prompts only.</strong> Premium tools are not financial advice or buy/sell instructions.</p>
</section>
"""


def render_newsletter_issue_body(draft):
    return render_template_string(
        newsletter_issue_body_html,
        draft=draft,
        production_base_url=PRODUCTION_BASE_URL,
    )


def newsletter_storage_timestamp():
    return datetime.now(timezone.utc).isoformat()


def load_json_storage(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return default
    except Exception:
        app.logger.exception("Failed to read JSON storage: %s", path)
        return default

    return data if isinstance(data, dict) else default


def save_json_storage(path, data):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)
        return True
    except Exception:
        app.logger.exception("Failed to write JSON storage: %s", path)
        return False


def valid_newsletter_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", normalize_email(email)))


def load_newsletter_subscribers():
    data = load_json_storage(NEWSLETTER_SUBSCRIBERS_PATH, {"subscribers": []})
    if not isinstance(data.get("subscribers"), list):
        data["subscribers"] = []
    return data


def save_newsletter_subscribers(data):
    return save_json_storage(NEWSLETTER_SUBSCRIBERS_PATH, data)


def load_newsletter_delivery_log():
    data = load_json_storage(NEWSLETTER_DELIVERY_LOG_PATH, {"deliveries": []})
    if not isinstance(data.get("deliveries"), list):
        data["deliveries"] = []
    if not isinstance(data.get("runs"), list):
        data["runs"] = []
    return data


def save_newsletter_delivery_log(data):
    return save_json_storage(NEWSLETTER_DELIVERY_LOG_PATH, data)


def beehiiv_configured():
    return bool(BEEHIIV_API_KEY and BEEHIIV_PUBLICATION_ID)


def newsletter_issue_key(issue):
    issue_date = str(issue.get("metadata", {}).get("issue_date") or "").strip()
    return f"newsletter:{issue_date}" if issue_date else ""


def load_newsletter_beehiiv_state():
    data = load_json_storage(NEWSLETTER_BEEHIIV_STATE_PATH, {"issues": {}})
    if not isinstance(data.get("issues"), dict):
        data["issues"] = {}
    return data


def save_newsletter_beehiiv_state(data):
    return save_json_storage(NEWSLETTER_BEEHIIV_STATE_PATH, data)


def sanitise_newsletter_error(error):
    message = str(error or "unknown_error")
    if BEEHIIV_API_KEY:
        message = message.replace(BEEHIIV_API_KEY, "[redacted]")
    message = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", message)
    return re.sub(r"\s+", " ", message).strip()[:240]


def record_beehiiv_issue_state(issue_key, **updates):
    data = load_newsletter_beehiiv_state()
    current = data["issues"].get(issue_key, {})
    current.update(updates)
    current["issue_key"] = issue_key
    current["updated_at"] = newsletter_storage_timestamp()
    data["issues"][issue_key] = current
    save_newsletter_beehiiv_state(data)
    return current


def beehiiv_api_request(method, path, payload=None, query=None):
    if not beehiiv_configured():
        raise RuntimeError("beehiiv_not_configured")
    url = f"{BEEHIIV_API_BASE_URL}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_object = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {BEEHIIV_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            request_object,
            timeout=BEEHIIV_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as error:
        safe_reason = f"beehiiv_http_{error.code}"
        raise RuntimeError(safe_reason) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("beehiiv_request_failed") from error


def find_existing_beehiiv_issue(issue):
    issue_key = newsletter_issue_key(issue)
    issue_title = str(issue.get("metadata", {}).get("title") or "")
    response = beehiiv_api_request(
        "GET",
        f"/publications/{urllib.parse.quote(BEEHIIV_PUBLICATION_ID)}/posts",
        query={"limit": 100, "page": 1},
    )
    for post in response.get("data", []):
        tags = post.get("content_tags") or []
        if issue_key in tags or str(post.get("title") or "") == issue_title:
            return post
    return None


def build_beehiiv_post_payload(issue):
    issue_url = f"{PRODUCTION_BASE_URL}/newsletter/latest"
    status = "confirmed" if BEEHIIV_AUTOSEND_ENABLED else "draft"
    body_content = (
        f'<p>{xml_escape(str(issue.get("summary") or "Your weekly StockRadar market brief is ready."))}</p>'
        f'<p><a href="{issue_url}">Read the full StockRadar Weekly issue</a></p>'
        "<p>Educational market information only; not personal financial advice.</p>"
    )
    return {
        "title": issue["metadata"]["title"],
        "subtitle": "The 5-minute market signal: what strengthened, what weakened, and what may matter next.",
        "body_content": body_content,
        "status": status,
        "content_tags": [newsletter_issue_key(issue)],
        "email_settings": {
            "email_subject_line": issue["metadata"]["title"],
            "email_preview_text": "Your weekly StockRadar market signal brief is ready.",
        },
    }


def create_beehiiv_issue(issue):
    issue_key = newsletter_issue_key(issue)
    local_state = load_newsletter_beehiiv_state()["issues"].get(issue_key, {})
    if local_state.get("status") in {
        "beehiiv_api_post_blocked",
        "beehiiv_draft_created",
        "beehiiv_scheduled",
        "beehiiv_published",
    }:
        return dict(local_state, duplicate=True)

    if BEEHIIV_CREATE_POST_BLOCKED:
        return record_beehiiv_issue_state(
            issue_key,
            status="beehiiv_api_post_blocked",
            content_generation_status="generated",
            failure_reason="beehiiv_http_403",
        )

    existing = find_existing_beehiiv_issue(issue)
    if existing:
        existing_status = str(existing.get("status") or "").lower()
        state_status = "beehiiv_published" if existing_status not in {"draft", "pending"} else "beehiiv_draft_created"
        return dict(record_beehiiv_issue_state(
            issue_key,
            status=state_status,
            beehiiv_post_id=existing.get("id", ""),
            beehiiv_status=existing_status,
            failure_reason="",
        ), duplicate=True)

    try:
        response = beehiiv_api_request(
            "POST",
            f"/publications/{urllib.parse.quote(BEEHIIV_PUBLICATION_ID)}/posts",
            payload=build_beehiiv_post_payload(issue),
        )
    except RuntimeError as error:
        if str(error) == "beehiiv_http_403":
            return record_beehiiv_issue_state(
                issue_key,
                status="beehiiv_api_post_blocked",
                content_generation_status="generated",
                failure_reason="beehiiv_http_403",
            )
        raise
    post = response.get("data") or {}
    post_id = str(post.get("id") or "")
    if not post_id:
        raise RuntimeError("beehiiv_response_missing_post_id")
    state_status = "beehiiv_published" if BEEHIIV_AUTOSEND_ENABLED else "beehiiv_draft_created"
    return record_beehiiv_issue_state(
        issue_key,
        status=state_status,
        beehiiv_post_id=post_id,
        beehiiv_status="confirmed" if BEEHIIV_AUTOSEND_ENABLED else "draft",
        preview_url=str(post.get("preview_url") or ""),
        last_successful_publish_at=(
            newsletter_storage_timestamp() if BEEHIIV_AUTOSEND_ENABLED else ""
        ),
        failure_reason="",
    )


def build_beehiiv_manual_export(issue):
    draft = issue.get("draft", {})
    metadata = issue.get("metadata", {})
    bullet_candidates = [
        draft.get("market_week_summary"),
        draft.get("market_pulse"),
    ]
    strong = (draft.get("what_looked_strong") or [])[:1]
    weak = (draft.get("what_looked_weak") or [])[:1]
    if strong:
        bullet_candidates.append(
            f"Strength to review: {strong[0].get('name', 'current leaders')} — {strong[0].get('reason', 'see the full issue for context')}"
        )
    if weak:
        bullet_candidates.append(
            f"Risk to review: {weak[0].get('name', 'current caution signals')} — {weak[0].get('reason', 'see the full issue for context')}"
        )
    bullets = []
    for candidate in bullet_candidates:
        clean = re.sub(r"\s+", " ", str(candidate or "")).strip()
        if clean and clean not in bullets:
            bullets.append(clean)
        if len(bullets) == 4:
            break
    if len(bullets) < 2:
        bullets.extend([
            "This week’s StockRadar market signal summary is ready.",
            "Review the full issue for strength, caution and risk context.",
        ][len(bullets):])

    issue_url = f"{PRODUCTION_BASE_URL}/newsletter/latest"
    body_lines = [
        "Welcome to StockRadar Weekly.",
        "",
        *[f"• {item}" for item in bullets],
        "",
        "Read the full issue:",
        issue_url,
        "",
        BEEHIIV_EXPORT_DISCLAIMER,
        "",
        "StockRadar Team",
    ]
    return {
        "issue_date": metadata.get("issue_date", ""),
        "issue_key": newsletter_issue_key(issue),
        "issue_status": metadata.get("issue_status_key", ""),
        "subject": BEEHIIV_EXPORT_SUBJECT,
        "preview_text": BEEHIIV_EXPORT_PREVIEW,
        "email_body": "\n".join(body_lines),
        "issue_url": issue_url,
        "disclaimer": BEEHIIV_EXPORT_DISCLAIMER,
    }


def create_beehiiv_subscription(email):
    clean_email = normalize_email(email)
    if not valid_newsletter_email(clean_email):
        raise ValueError("invalid_email")
    response = beehiiv_api_request(
        "POST",
        f"/publications/{urllib.parse.quote(BEEHIIV_PUBLICATION_ID)}/subscriptions",
        payload={
            "email": clean_email,
            "reactivate_existing": True,
            "send_welcome_email": True,
            "utm_source": "stockradar",
            "utm_medium": "website",
            "utm_campaign": "stockradar_weekly",
            "referring_site": PRODUCTION_BASE_URL,
            "double_opt_override": "not_set",
        },
    )
    subscription = response.get("data") or {}
    if not subscription.get("id"):
        raise RuntimeError("beehiiv_response_missing_subscription_id")
    return subscription


def newsletter_email_configured():
    return bool(
        NEWSLETTER_EMAIL_ENABLED
        and NEWSLETTER_SMTP_HOST
        and NEWSLETTER_FROM_EMAIL
    )


def newsletter_issue_guid(issue):
    return str(issue.get("metadata", {}).get("guid") or "").strip()


def newsletter_issue_lock_path(issue_guid):
    safe_guid = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(issue_guid or "").strip())
    return os.path.join(NEWSLETTER_SEND_LOCK_DIR, f"{safe_guid or 'unknown'}.lock")


def acquire_newsletter_issue_lock(issue_guid):
    lock_path = newsletter_issue_lock_path(issue_guid)
    os.makedirs(NEWSLETTER_SEND_LOCK_DIR, exist_ok=True)

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            lock_age = time.time() - os.path.getmtime(lock_path)
            if lock_age > NEWSLETTER_SEND_LOCK_STALE_SECONDS:
                os.remove(lock_path)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                return None
        except FileExistsError:
            return None
        except Exception:
            app.logger.exception("Failed to inspect newsletter send lock: %s", lock_path)
            return None

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(newsletter_storage_timestamp())
        handle.write("\n")
    return lock_path


def release_newsletter_issue_lock(lock_path):
    if not lock_path:
        return
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return
    except Exception:
        app.logger.exception("Failed to release newsletter send lock: %s", lock_path)


def record_newsletter_run(summary, delivery_type, status=None):
    data = load_newsletter_delivery_log()
    run_status = status
    if not run_status:
        run_status = "completed_with_failures" if summary.get("failed") else "completed"

    data["runs"].append({
        "issue_guid": summary.get("issue_guid", ""),
        "issue_title": summary.get("issue_title", ""),
        "delivery_type": delivery_type,
        "status": run_status,
        "sent": int(summary.get("sent") or 0),
        "skipped": int(summary.get("skipped") or 0),
        "failed": int(summary.get("failed") or 0),
        "completed_at": newsletter_storage_timestamp(),
    })
    return save_newsletter_delivery_log(data)


def newsletter_london_now(now=None):
    london_timezone = ZoneInfo("Europe/London")
    london_now = now or datetime.now(london_timezone)
    if london_now.tzinfo is None:
        return london_now.replace(tzinfo=london_timezone)
    return london_now.astimezone(london_timezone)


def newsletter_auto_send_time():
    return dt_time(
        hour=NEWSLETTER_AUTO_SEND_HOUR_LONDON,
        minute=NEWSLETTER_AUTO_SEND_MINUTE_LONDON,
    )


def newsletter_auto_send_due(now=None):
    london_now = newsletter_london_now(now)
    status = get_weekly_newsletter_status(london_now)
    return (
        london_now.weekday() == 4
        and london_now.time() >= newsletter_auto_send_time()
        and status["is_final"]
    )


def next_newsletter_auto_send_at(now=None):
    london_now = newsletter_london_now(now)
    days_until_friday = (4 - london_now.weekday()) % 7
    candidate_date = london_now.date() + timedelta(days=days_until_friday)
    candidate = datetime.combine(
        candidate_date,
        newsletter_auto_send_time(),
        tzinfo=ZoneInfo("Europe/London"),
    )
    if candidate <= london_now:
        candidate += timedelta(days=7)
    return candidate


def newsletter_status_snapshot(now=None):
    london_now = newsletter_london_now(now)
    issue_status = get_weekly_newsletter_status(london_now)
    metadata = newsletter_issue_metadata(london_now, issue_status=issue_status)
    delivery_log = load_newsletter_delivery_log()
    deliveries = delivery_log.get("deliveries", [])
    runs = delivery_log.get("runs", [])
    current_issue_deliveries = [
        item for item in deliveries
        if str(item.get("issue_guid") or "") == metadata["guid"]
    ]
    last_delivery = max(
        deliveries,
        key=lambda item: str(item.get("sent_at") or ""),
        default={},
    )
    last_run = max(
        runs,
        key=lambda item: str(item.get("completed_at") or ""),
        default={},
    )
    beehiiv_issues = load_newsletter_beehiiv_state().get("issues", {})
    current_beehiiv = beehiiv_issues.get(
        f"newsletter:{metadata['issue_date']}",
        {},
    )
    published_issues = [
        item for item in beehiiv_issues.values()
        if item.get("status") == "beehiiv_published"
    ]
    last_published = max(
        published_issues,
        key=lambda item: str(item.get("last_successful_publish_at") or ""),
        default={},
    )

    return {
        "weekly_bulk_sender": BEEHIIV_WEEKLY_BULK_SENDER,
        "content_generation_status": current_beehiiv.get("content_generation_status", "not_due"),
        "beehiiv_configured": beehiiv_configured(),
        "beehiiv_create_post_blocked": (
            BEEHIIV_CREATE_POST_BLOCKED
            or current_beehiiv.get("status") == "beehiiv_api_post_blocked"
        ),
        "beehiiv_campaign_status": current_beehiiv.get(
            "status",
            "beehiiv_api_post_blocked" if BEEHIIV_CREATE_POST_BLOCKED else "not_due",
        ),
        "transactional_smtp_configured": newsletter_email_configured(),
        "last_successful_beehiiv_publish_at": last_published.get("last_successful_publish_at", ""),
        "last_failure_reason": sanitise_newsletter_error(current_beehiiv.get("failure_reason", "")),
        "auto_send_enabled": NEWSLETTER_AUTO_SEND_ENABLED,
        "cron_secret_configured": bool(NEWSLETTER_CRON_SECRET),
        "current_issue_guid": metadata["guid"],
        "current_issue_status": issue_status["key"],
        "current_issue_is_final": issue_status["is_final"],
        "next_expected_friday_send_at": next_newsletter_auto_send_at(london_now).isoformat(),
    }


def newsletter_already_delivered(email, issue_guid):
    clean_email = normalize_email(email)
    if not clean_email or not issue_guid:
        return False

    data = load_newsletter_delivery_log()
    return any(
        normalize_email(item.get("email")) == clean_email
        and str(item.get("issue_guid") or "") == issue_guid
        for item in data.get("deliveries", [])
    )


def record_newsletter_delivery(email, issue, delivery_type):
    clean_email = normalize_email(email)
    issue_guid = newsletter_issue_guid(issue)
    if not clean_email or not issue_guid:
        return False

    data = load_newsletter_delivery_log()
    if newsletter_already_delivered(clean_email, issue_guid):
        return True

    data["deliveries"].append({
        "email": clean_email,
        "issue_guid": issue_guid,
        "issue_title": issue.get("metadata", {}).get("title", ""),
        "delivery_type": delivery_type,
        "sent_at": newsletter_storage_timestamp(),
    })
    return save_newsletter_delivery_log(data)


def upsert_newsletter_subscriber(email):
    clean_email = normalize_email(email)
    data = load_newsletter_subscribers()
    now = newsletter_storage_timestamp()

    for subscriber in data["subscribers"]:
        if normalize_email(subscriber.get("email")) == clean_email:
            subscriber["active"] = True
            subscriber["updated_at"] = now
            save_newsletter_subscribers(data)
            return subscriber, False

    subscriber = {
        "email": clean_email,
        "active": True,
        "created_at": now,
        "updated_at": now,
        "welcome_issue_guid": "",
        "welcome_sent_at": "",
    }
    data["subscribers"].append(subscriber)
    save_newsletter_subscribers(data)
    return subscriber, True


def mark_subscriber_welcome_sent(email, issue):
    clean_email = normalize_email(email)
    issue_guid = newsletter_issue_guid(issue)
    if not clean_email or not issue_guid:
        return False

    data = load_newsletter_subscribers()
    now = newsletter_storage_timestamp()
    for subscriber in data["subscribers"]:
        if normalize_email(subscriber.get("email")) == clean_email:
            subscriber["welcome_issue_guid"] = issue_guid
            subscriber["welcome_sent_at"] = now
            subscriber["updated_at"] = now
            return save_newsletter_subscribers(data)
    return False


def build_newsletter_email_html(issue):
    draft = issue["draft"]
    body_html = render_newsletter_issue_body(draft)
    return f"""<!doctype html>
<html>
<body style="margin:0;background:#08111c;color:#dbe4ee;font-family:Arial,sans-serif;padding:24px;">
<div style="max-width:760px;margin:0 auto;background:#101827;border:1px solid #263344;border-radius:18px;padding:24px;">
<h1 style="color:#f8fafc;">{xml_escape(str(issue["metadata"]["title"]))}</h1>
{body_html}
<p style="color:#94a3b8;font-size:12px;line-height:1.6;">You are receiving this because you subscribed to StockRadar Weekly. Educational research only, not financial advice.</p>
</div>
</body>
</html>"""


def send_newsletter_email(email, issue):
    clean_email = normalize_email(email)
    if not valid_newsletter_email(clean_email):
        return {"sent": False, "skipped": True, "reason": "invalid_email"}
    if not newsletter_email_configured():
        return {"sent": False, "skipped": True, "reason": "email_not_configured"}

    message = EmailMessage()
    message["Subject"] = issue["metadata"]["title"]
    message["From"] = NEWSLETTER_FROM_EMAIL
    message["To"] = clean_email
    if SUPPORT_EMAIL:
        message["Reply-To"] = SUPPORT_EMAIL
    message.set_content(issue["draft"]["plain_text"])
    message.add_alternative(build_newsletter_email_html(issue), subtype="html")

    try:
        if NEWSLETTER_SMTP_PORT == 465:
            with smtplib.SMTP_SSL(NEWSLETTER_SMTP_HOST, NEWSLETTER_SMTP_PORT, timeout=15) as smtp:
                if NEWSLETTER_SMTP_USERNAME or NEWSLETTER_SMTP_PASSWORD:
                    smtp.login(NEWSLETTER_SMTP_USERNAME, NEWSLETTER_SMTP_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(NEWSLETTER_SMTP_HOST, NEWSLETTER_SMTP_PORT, timeout=15) as smtp:
                smtp.starttls()
                if NEWSLETTER_SMTP_USERNAME or NEWSLETTER_SMTP_PASSWORD:
                    smtp.login(NEWSLETTER_SMTP_USERNAME, NEWSLETTER_SMTP_PASSWORD)
                smtp.send_message(message)
        return {"sent": True, "skipped": False, "reason": "sent"}
    except Exception:
        app.logger.exception("Newsletter email send failed for %s", clean_email)
        return {"sent": False, "skipped": False, "reason": "send_failed"}


def deliver_newsletter_issue_to_subscriber(email, issue, delivery_type="weekly"):
    clean_email = normalize_email(email)
    issue_guid = newsletter_issue_guid(issue)
    if not valid_newsletter_email(clean_email):
        return {"email": clean_email, "status": "skipped", "reason": "invalid_email"}
    if newsletter_already_delivered(clean_email, issue_guid):
        return {"email": clean_email, "status": "skipped", "reason": "already_sent"}

    result = send_newsletter_email(clean_email, issue)
    if result["sent"]:
        record_newsletter_delivery(clean_email, issue, delivery_type)
        if delivery_type == "welcome_latest":
            mark_subscriber_welcome_sent(clean_email, issue)
        return {"email": clean_email, "status": "sent", "reason": "sent"}

    return {
        "email": clean_email,
        "status": "skipped" if result.get("skipped") else "failed",
        "reason": result.get("reason", "unknown"),
    }


def send_weekly_newsletter_to_eligible_subscribers(delivery_type="weekly"):
    issue = build_weekly_newsletter_issue()
    subscribers = load_newsletter_subscribers().get("subscribers", [])
    issue_guid = newsletter_issue_guid(issue)
    summary = {
        "issue_guid": issue_guid,
        "issue_title": issue["metadata"]["title"],
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "details": [],
    }

    lock_path = acquire_newsletter_issue_lock(issue_guid)
    if lock_path is None:
        active_count = sum(1 for subscriber in subscribers if subscriber.get("active") is True)
        summary["skipped"] = active_count
        summary["details"].append({
            "email": "",
            "status": "skipped",
            "reason": "send_in_progress",
        })
        record_newsletter_run(summary, delivery_type, status="send_in_progress")
        return summary

    try:
        for subscriber in subscribers:
            if subscriber.get("active") is not True:
                summary["skipped"] += 1
                summary["details"].append({
                    "email": normalize_email(subscriber.get("email")),
                    "status": "skipped",
                    "reason": "inactive",
                })
                continue

            result = deliver_newsletter_issue_to_subscriber(
                subscriber.get("email"),
                issue,
                delivery_type=delivery_type,
            )
            summary["details"].append(result)
            if result["status"] == "sent":
                summary["sent"] += 1
            elif result["status"] == "failed":
                summary["failed"] += 1
            else:
                summary["skipped"] += 1
    finally:
        release_newsletter_issue_lock(lock_path)

    record_newsletter_run(summary, delivery_type)
    return summary


def newsletter_automation_not_due_summary(reason, now=None):
    london_now = newsletter_london_now(now)
    issue_status = get_weekly_newsletter_status(london_now)
    metadata = newsletter_issue_metadata(london_now, issue_status=issue_status)
    return {
        "issue_guid": metadata["guid"],
        "issue_title": metadata["title"],
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "automation_due": False,
        "reason": reason,
        "details": [{
            "email": "",
            "status": "skipped",
            "reason": reason,
        }],
    }


def run_due_newsletter_automation(delivery_type="auto", now=None):
    if not newsletter_auto_send_due(now):
        return newsletter_automation_not_due_summary("not_due", now)
    london_now = newsletter_london_now(now)
    metadata = newsletter_issue_metadata(london_now)
    issue_key = f"newsletter:{metadata['issue_date']}"
    existing_state = load_newsletter_beehiiv_state()["issues"].get(issue_key, {})
    if (
        existing_state.get("status") == "beehiiv_api_post_blocked"
        and existing_state.get("content_generation_status") == "generated"
    ):
        return dict(
            existing_state,
            automation_due=True,
            duplicate=True,
            reason="manual_beehiiv_handoff_required",
        )
    if not beehiiv_configured():
        state = record_beehiiv_issue_state(
            issue_key,
            status="failed",
            content_generation_status="not_due",
            failure_reason="beehiiv_not_configured",
        )
        return dict(state, automation_due=True, reason="beehiiv_not_configured")

    lock_path = acquire_newsletter_issue_lock(issue_key)
    if lock_path is None:
        state = load_newsletter_beehiiv_state()["issues"].get(issue_key, {})
        return dict(
            state,
            issue_key=issue_key,
            automation_due=True,
            duplicate=True,
            reason="workflow_in_progress",
        )

    record_beehiiv_issue_state(
        issue_key,
        status="generating",
        content_generation_status="generating",
        failure_reason="",
    )
    try:
        issue = build_weekly_newsletter_issue(now=london_now)
        record_beehiiv_issue_state(
            issue_key,
            status="generated",
            content_generation_status="generated",
        )
        if BEEHIIV_CREATE_POST_BLOCKED:
            result = record_beehiiv_issue_state(
                issue_key,
                status="beehiiv_api_post_blocked",
                content_generation_status="generated",
                failure_reason="beehiiv_http_403",
            )
        else:
            result = create_beehiiv_issue(issue)
        result["automation_due"] = True
        result["content_generation_status"] = "generated"
        return result
    except Exception as error:
        safe_reason = sanitise_newsletter_error(error)
        app.logger.error("Beehiiv newsletter workflow failed: %s", safe_reason)
        state = record_beehiiv_issue_state(
            issue_key,
            status="failed",
            content_generation_status=(
                "generated" if load_newsletter_beehiiv_state()["issues"].get(issue_key, {}).get("content_generation_status") == "generated"
                else "failed"
            ),
            failure_reason=safe_reason,
        )
        return dict(state, automation_due=True, reason=safe_reason)
    finally:
        release_newsletter_issue_lock(lock_path)


def newsletter_auto_send_loop():
    while True:
        try:
            summary = run_due_newsletter_automation(delivery_type="auto")
            if summary.get("automation_due"):
                app.logger.info(
                    "Newsletter Beehiiv workflow checked %s: status=%s",
                    summary.get("issue_key"),
                    summary.get("status"),
                )
        except Exception:
            app.logger.exception("Newsletter auto-send scheduler failed")
        time.sleep(max(60, NEWSLETTER_AUTO_SEND_CHECK_INTERVAL_SECONDS))


def start_newsletter_auto_send_scheduler():
    global NEWSLETTER_AUTO_SEND_THREAD_STARTED
    if NEWSLETTER_AUTO_SEND_THREAD_STARTED or not NEWSLETTER_AUTO_SEND_ENABLED:
        return False

    thread = threading.Thread(
        target=newsletter_auto_send_loop,
        name="stockradar-newsletter-auto-send",
        daemon=True,
    )
    thread.start()
    NEWSLETTER_AUTO_SEND_THREAD_STARTED = True
    app.logger.info(
        "Newsletter auto-send scheduler started; next expected Friday send at %s",
        next_newsletter_auto_send_at().isoformat(),
    )
    return True


newsletter_landing_html = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StockRadar Weekly — Free Market Newsletter</title>
<meta name="description" content="Join StockRadar Weekly for a concise weekly market brief covering market pulse, signal highlights, watchlist moves and risk checks.">
<link rel="canonical" href="https://www.stockradarhq.com/newsletter">
<meta property="og:title" content="StockRadar Weekly — Free Market Newsletter">
<meta property="og:description" content="The 5-minute market signal: what is strengthening, what is weakening and what may matter next.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.stockradarhq.com/newsletter">
<meta property="og:site_name" content="StockRadar">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="StockRadar Weekly — Free Market Newsletter">
<meta name="twitter:description" content="The 5-minute market signal: what is strengthening, what is weakening and what may matter next.">
<link rel="alternate" type="application/rss+xml" title="StockRadar Weekly RSS" href="/newsletter/rss">
<style>
*{box-sizing:border-box;}:root{--font-hero:clamp(40px,5vw,52px);--font-section:clamp(26px,2.4vw,34px);--font-body:16px;--font-small:13px;--font-kicker:11px;--font-cta:14px;}body{margin:0;min-height:100vh;padding:42px 22px;background:radial-gradient(circle at 18% 8%,rgba(0,255,170,.11),transparent 30%),linear-gradient(135deg,#08111c,#101827);color:#dbe4ee;font-family:Arial,sans-serif;font-size:var(--font-body);}
.wrap{max-width:900px;margin:0 auto;}.back{color:#69c9f2;text-decoration:none;font-weight:900;}.hero{margin-top:24px;padding:46px;border-radius:30px;background:linear-gradient(180deg,rgba(18,29,42,.97),rgba(12,22,33,.97));border:1px solid rgba(148,163,184,.16);box-shadow:0 24px 70px rgba(0,0,0,.30);}
.eyebrow{color:#4adea3;font-size:var(--font-kicker);font-weight:950;letter-spacing:.13em;text-transform:uppercase;}h1{color:#f2f5f8;font-size:var(--font-hero);line-height:1.04;margin:14px 0 18px;letter-spacing:0;}p{color:#b9c5d2;line-height:1.7;font-size:var(--font-body);}.signup{margin-top:28px;padding:24px;border-radius:22px;background:#0d1826;border:1px solid rgba(74,222,163,.22);}form{display:flex;gap:10px;flex-wrap:wrap;}input{flex:1;min-width:240px;border:1px solid rgba(148,163,184,.24);background:#07111d;color:#e5edf5;border-radius:14px;padding:14px 15px;font-size:16px;}button{display:inline-flex;align-items:center;justify-content:center;white-space:nowrap;border:0;border-radius:14px;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#061018;font-weight:950;font-size:var(--font-cta);line-height:1.1;padding:13px 18px;cursor:pointer;}.status{margin:0 0 16px;padding:13px 14px;border-radius:14px;background:rgba(74,222,163,.10);border:1px solid rgba(74,222,163,.22);color:#d1fae5;font-size:14px;line-height:1.55;}.status.error{background:rgba(248,113,113,.10);border-color:rgba(248,113,113,.24);color:#fecaca;}.fallback{color:#f4cf79;font-weight:900;margin:18px 0 0;font-size:var(--font-small);}.notes{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-top:24px;}.note{padding:18px;border-radius:18px;background:rgba(148,163,184,.07);color:#c6d0da;line-height:1.6;font-size:14px;}.bridge{margin-top:22px;padding:18px;border-radius:18px;background:linear-gradient(135deg,rgba(74,222,163,.10),rgba(56,189,248,.08));border:1px solid rgba(74,222,163,.18);color:#d1fae5;font-size:15px;}.feed-link{display:inline-block;margin-top:22px;color:#69c9f2;font-size:14px;font-weight:900;text-decoration:none;}@media(max-width:700px){:root{--font-hero:clamp(32px,9vw,38px);--font-section:clamp(24px,6vw,28px);}body{padding:24px 16px}.hero{padding:28px}.notes{grid-template-columns:1fr;}button,input{width:100%;}}
</style>
</head>
<body>
<div class="wrap">
<a class="back" href="/">← Back to StockRadar</a>
<main class="hero">
<div class="eyebrow">Free weekly market signal</div>
<h1>StockRadar Weekly</h1>
<p><strong>The 5-minute market signal for investors who want clarity without noise.</strong></p>
<p>Free market context, signal highlights and risk prompts for everyday investors.</p>
<section class="signup" aria-label="Newsletter signup">
{% if subscription_message %}
<p class="status {% if subscription_error %}error{% endif %}">{{ subscription_message }}</p>
{% endif %}
<form method="POST" action="/newsletter">
<input type="email" name="email" placeholder="you@example.com" autocomplete="email" required>
<button type="submit">Join Free</button>
</form>
<p class="fallback">Free to join. After signup, the latest issue is emailed automatically if email delivery is configured. The regular weekly issue normally arrives Friday.</p>
<p class="fallback">Your email is used to send StockRadar Weekly, StockRadar updates and market briefs. Signals are educational research prompts, not personalised financial advice.</p>
{% if newsletter_embed_html %}
<details style="margin-top:16px;">
<summary style="color:#69c9f2;font-weight:900;cursor:pointer;">Use alternate signup form</summary>
{{ newsletter_embed_html | safe }}
</details>
{% endif %}
</section>
<div class="notes">
<div class="note"><strong>What strengthened</strong><br>A concise recap of areas showing stronger signals.</div>
<div class="note"><strong>What weakened</strong><br>Plain-English notes on caution areas and risk context.</div>
<div class="note"><strong>BUY/HOLD/SELL prompts</strong><br>Key research prompts from the StockRadar signal table.</div>
<div class="note"><strong>Market mood</strong><br>Context on what may matter next for everyday investors.</div>
<div class="note"><strong>Premium preview</strong><br>Where useful, a bridge to deeper decision context.</div>
</div>
<p class="bridge">StockRadar Weekly gives you the market signal. Premium gives you the decision layer.</p>
<a class="feed-link" href="/newsletter/latest">Read the latest issue</a>
<span aria-hidden="true"> · </span>
<a class="feed-link" href="/newsletter/rss">RSS feed</a>
</main>
{{ disclaimer_footer() | safe }}
</div>
</body>
</html>
"""


newsletter_latest_html = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ issue.title }} — StockRadar</title>
<link rel="alternate" type="application/rss+xml" title="StockRadar Weekly RSS" href="/newsletter/rss">
<style>
*{box-sizing:border-box;}body{margin:0;padding:34px 20px;background:radial-gradient(circle at 18% 8%,rgba(0,255,170,.10),transparent 30%),linear-gradient(135deg,#08111c,#101827);color:#dbe4ee;font-family:Arial,sans-serif;}.wrap{max-width:860px;margin:0 auto;}a{color:#69c9f2;font-weight:900;text-decoration:none;}.header,.section{background:linear-gradient(180deg,rgba(18,29,42,.97),rgba(12,22,33,.97));border:1px solid rgba(148,163,184,.16);border-radius:24px;padding:26px;margin-bottom:18px;}.kicker{color:#4adea3;font-size:12px;font-weight:950;letter-spacing:.12em;text-transform:uppercase;}h1{color:#f2f5f8;font-size:clamp(36px,7vw,54px);line-height:1.04;margin:12px 0;}h2,h3{color:#eef2f6;}h2{margin:0 0 14px;}h3{margin:20px 0 8px;}p,li{color:#b9c5d2;line-height:1.72;}.meta{color:#91a3b4;}.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(74,222,163,.10);color:#b8f5d5;font-size:12px;font-weight:950;text-transform:uppercase;}.status{margin-left:7px;color:#f4cf79;font-size:12px;font-weight:900;}.signal{padding:18px 0;border-bottom:1px solid rgba(148,163,184,.12);}.signal:last-child{border-bottom:0;padding-bottom:0;}.confidence{color:#91a3b4;font-size:12px;font-weight:900;}.top-links{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:18px;}@media(max-width:700px){body{padding:24px 16px}.header,.section{padding:22px;}}
</style>
</head>
<body>
<div class="wrap">
<nav class="top-links"><a href="/newsletter">← Newsletter signup</a><a href="/newsletter/rss">RSS feed</a></nav>
<header class="header">
<div class="kicker">The 5-minute market signal</div>
<h1>{{ issue.title }}</h1>
<p class="meta">Issue date: {{ issue.issue_date_label }}</p>
<p class="meta">Issue status: {{ issue.issue_status }}</p>
<p><strong>{{ issue.issue_status_message }}</strong></p>
<p class="meta">Last refreshed: {{ issue.generated_at_label }}</p>
{% if draft.preview_refresh_note %}<p>{{ draft.preview_refresh_note }}</p>{% endif %}
<p><strong>{{ draft.opening_line }}</strong></p>
<p>{{ draft.opening_note }}</p>
<p style="margin-bottom:16px;"><strong>Market mood:</strong> {{ draft.market_mood }}.</p>
<p style="margin-top:0;">{{ draft.market_pulse }}</p>
<p>{{ draft.market_week_summary }}</p>
</header>
<section class="section">
<h2>This Week in the Market</h2>
<h3>News-feed themes</h3>
<ul>{% for item in draft.trending_vs_forecasting.trending %}<li>{{ item.headline }} — {{ item.source }}</li>{% endfor %}</ul>
<h3>What may matter next</h3>
<ul>{% for item in draft.trending_vs_forecasting.forecasting %}<li>{{ item }}</li>{% endfor %}</ul>
</section>
<section class="section">
<h2>What Looked Strong</h2>
{% if draft.what_looked_strong %}
{% for item in draft.what_looked_strong %}
<article class="signal">
<h3>{{ item.name }}</h3>
<p><strong>{{ item.signal }} research prompt · {{ item.confidence }}</strong></p>
<p><strong>Area:</strong> {{ item.sector }}</p>
<p><strong>Why it appears:</strong> {{ item.reason }}</p>
</article>
{% endfor %}
{% else %}
<p>Strong-signal examples are unavailable in the current feed.</p>
{% endif %}
</section>
<section class="section">
<h2>What Looked Weak / Caution Zone</h2>
{% if draft.what_looked_weak %}
{% for item in draft.what_looked_weak %}
<article class="signal">
<h3>{{ item.name }}</h3>
<p><strong>{{ item.signal }} research prompt · {{ item.confidence }}</strong></p>
<p><strong>Area:</strong> {{ item.sector }}</p>
<p><strong>Review risk:</strong> {{ item.reason }}</p>
</article>
{% endfor %}
{% else %}
<p>Caution examples are unavailable in the current feed.</p>
{% endif %}
</section>
<section class="section">
<h2>Market Tracker</h2>
{% if draft.market_tracker %}
<ul>{% for item in draft.market_tracker %}<li><strong>{{ item.name }}</strong> — {{ item.signal }} · {{ item.confidence }}: {{ item.reason }}</li>{% endfor %}</ul>
{% else %}
<p>Broad market proxy data is unavailable in the current feed.</p>
{% endif %}
</section>
<section class="section">
<h2>StockRadar Signal Watchlist</h2>
{% set signal_groups = [
    ("Stronger BUY research prompts", draft.signal_watch.strongest_buy),
    ("Notable HOLD / watchlist names", draft.signal_watch.notable_hold),
    ("Caution / SELL research prompts", draft.signal_watch.caution_sell)
] %}
{% for group_title, items in signal_groups %}
{% if items %}
<h3>{{ group_title }}</h3>
{% for item in items[:2] %}
<article class="signal">
<h3>{{ item.name }}</h3>
<p><strong>{{ item.signal }} research prompt · {{ item.confidence }}</strong></p>
<p><strong>Why it appears:</strong> {{ item.reason }}</p>
</article>
{% endfor %}
{% endif %}
{% endfor %}
{% if not draft.signal_watch.strongest_buy and not draft.signal_watch.notable_hold and not draft.signal_watch.caution_sell %}
<p>Signal data unavailable.</p>
{% endif %}
</section>
<section class="section">
<h2>Balanced Signal Examples</h2>
<ul>{% for item in draft.watchlist %}<li><strong>{{ item.name }}</strong> — {{ item.badge }} · {{ item.status }}: {{ item.reason }}</li>{% endfor %}</ul>
</section>
<section class="section">
<h2>Risk check</h2>
<ul>{% for item in draft.risk_check %}<li>{{ item }}</li>{% endfor %}</ul>
<p><strong>Educational only.</strong> {{ draft.disclaimer }}</p>
</section>
<section class="section">
<h2>Premium research preview</h2>
<p>{{ draft.premium_note }}</p>
<p><a href="/upgrade">Explore Premium</a> · <a href="/premium-watchlist">Preview Premium Watchlist</a> · <a href="/newsletter/latest">Latest issue</a></p>
<p><strong>Research prompts only.</strong> Premium tools are not financial advice or buy/sell instructions.</p>
</section>
</div>
</body>
</html>
"""


newsletter_preview_html = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Newsletter Draft Preview — StockRadar</title>
<style>
*{box-sizing:border-box;}body{margin:0;padding:34px 20px;background:#020617;color:#e5e7eb;font-family:Arial,sans-serif;}.wrap{max-width:980px;margin:0 auto;}a{color:#38bdf8;font-weight:900;text-decoration:none;}.toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:20px;}.button{border:0;border-radius:14px;padding:12px 16px;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#020617;font-weight:950;cursor:pointer;}.card{background:rgba(15,23,42,.94);border:1px solid rgba(255,255,255,.1);border-radius:24px;padding:26px;margin-bottom:18px;}.kicker{color:#00ffaa;font-size:12px;font-weight:950;letter-spacing:.12em;text-transform:uppercase;}h1{font-size:42px;margin:10px 0;}h2{margin:0 0 16px;}p,li{color:#cbd5e1;line-height:1.65;}.meta{color:#94a3b8;}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;}.signal{padding:18px;border-radius:18px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.08);}.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(0,255,170,.12);color:#bbf7d0;font-size:12px;font-weight:950;text-transform:uppercase;}.status{margin-left:7px;color:#fde68a;font-weight:900;font-size:12px;}.confidence{color:#94a3b8;font-size:12px;font-weight:900;}.copy-panel{display:none;}pre{white-space:pre-wrap;word-wrap:break-word;background:#000;border-radius:18px;padding:22px;color:#e5e7eb;line-height:1.55;}@media(max-width:700px){.grid{grid-template-columns:1fr;}h1{font-size:34px;}}
</style>
</head>
<body>
<div class="wrap">
<div class="toolbar">
<a href="/owner">← Owner area</a>
<button class="button" type="button" onclick="copyNewsletter()">Copy newsletter</button>
</div>
<main id="newsletter-preview">
<section class="card">
<div class="kicker">Owner draft preview · not sent</div>
<h1>{{ draft.title }}</h1>
<p class="meta">Generated {{ draft.generated_at }}</p>
<p><strong>Market mood:</strong> {{ draft.market_mood }}</p>
<p>{{ draft.market_pulse }}</p>
<p><strong>Main theme:</strong> {{ draft.main_theme }}</p>
<p><strong>Best-looking area:</strong> {{ draft.best_looking_area }} · <strong>Risk area:</strong> {{ draft.risk_area }}</p>
</section>
<section class="card">
<h2>Signal highlights</h2>
{% if draft.signal_highlights %}
<div class="grid">
{% for item in draft.signal_highlights %}
<article class="signal">
<h3>{{ item.name }}</h3>
<span class="badge">{{ item.badge }}</span><span class="status">{{ item.status }}</span>
<p><strong>Why it appears:</strong> {{ item.reason }}</p>
<p>{{ item.plain_english_takeaway }}</p>
<div class="confidence">Data confidence: {{ item.data_confidence }}</div>
</article>
{% endfor %}
</div>
{% else %}
<p>Signal data unavailable.</p>
{% endif %}
</section>
<section class="card">
<h2>Trending vs forecasting</h2>
<h3>Trending now</h3>
<ul>{% for item in draft.trending_vs_forecasting.trending %}<li>{{ item.headline }} — {{ item.source }}</li>{% endfor %}</ul>
<h3>What may matter next</h3>
<ul>{% for item in draft.trending_vs_forecasting.forecasting %}<li>{{ item }}</li>{% endfor %}</ul>
</section>
<section class="card">
<h2>Watchlist</h2>
<div class="grid">
{% for item in draft.watchlist %}
<article class="signal"><h3>{{ item.name }}</h3><span class="badge">{{ item.badge }}</span><span class="status">{{ item.status }}</span><p>{{ item.reason }}</p><div class="confidence">Data confidence: {{ item.data_confidence }}</div></article>
{% endfor %}
</div>
</section>
<section class="card">
<h2>Risk check</h2>
<ul>{% for item in draft.risk_check %}<li>{{ item }}</li>{% endfor %}</ul>
<p>{{ draft.disclaimer }}</p>
</section>
</main>
<section class="card">
<h2>Plain text version</h2>
<pre id="newsletter-copy">{{ draft.plain_text }}</pre>
</section>
</div>
<script>
function copyNewsletter(){
    var text=document.getElementById('newsletter-copy').innerText;
    navigator.clipboard.writeText(text).then(function(){
        var button=document.querySelector('.button');
        button.textContent='Copied';
        setTimeout(function(){button.textContent='Copy newsletter';},1600);
    }).catch(function(){
        window.getSelection().selectAllChildren(document.getElementById('newsletter-copy'));
    });
}
</script>
</body>
</html>
"""


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
        "premium_decision_brief": build_premium_decision_brief(recommendations),
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


def is_public_live_market_headline(item):
    return (
        isinstance(item, dict)
        and str(item.get("label", "")).upper() == "LIVE NEWS"
        and str(item.get("article_url", "")).startswith("http")
    )


def serialize_market_news_items(headlines, limit=MARKET_NEWS_TICKER_LIMIT):
    items = []

    for headline in headlines[:limit]:
        if not is_public_live_market_headline(headline):
            continue

        stock_links = []
        source_stock_links = [
            stock for stock in headline.get("stock_links", [])
            if isinstance(stock, dict)
        ]
        for stock in source_stock_links[:4]:
            ticker = str(stock.get("ticker") or "").strip().upper()
            stock_links.append({
                "ticker": ticker,
                "url": str(stock.get("url") or (f"/stock/{ticker}" if ticker else "/")).strip(),
                "display_label": str(stock.get("display_label") or stock_display_label(ticker or "SPY")).strip(),
                "signal_class": str(stock.get("signal_class") or "hold").strip().lower(),
                "action_text": str(stock.get("action_text") or stock.get("signal") or "HOLD").strip(),
            })

        items.append({
            "source": str(headline.get("source") or "StockRadar Market Impact Feed").strip(),
            "published_label": str(headline.get("published_label") or "Theme watch").strip(),
            "headline": str(headline.get("headline") or "Market headlines are reconnecting").strip(),
            "article_url": str(headline.get("article_url") or "/").strip(),
            "impact_score": str(headline.get("impact_score") or "Pending").strip(),
            "direction": str(headline.get("direction") or "Theme watch").strip(),
            "stock_links": stock_links,
            "stock_links_total": len(source_stock_links),
        })

    return items
html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StockRadar — AI-Assisted Market Signals</title>
<meta name="description" content="Learn to think like an investor with AI-assisted market signals, practical decision support, live market news and investing education from StockRadar.">
<link rel="canonical" href="https://www.stockradarhq.com/">
<meta property="og:title" content="StockRadar — AI-Assisted Market Signals">
<meta property="og:description" content="Learn to think like an investor with AI-assisted market signals, practical decision support, live market news and investing education from StockRadar.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.stockradarhq.com/">
<meta property="og:site_name" content="StockRadar">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="StockRadar — AI-Assisted Market Signals">
<meta name="twitter:description" content="Learn to think like an investor with AI-assisted market signals, practical decision support, live market news and investing education from StockRadar.">
<style>
*{box-sizing:border-box;}
:root{--font-hero:clamp(40px,4.4vw,52px);--font-section:clamp(26px,2.4vw,34px);--font-card-title:19px;--font-body:15px;--font-small:13px;--font-kicker:11px;--font-cta:14px;}
html{scroll-behavior:smooth;}
body{margin:0;background:radial-gradient(circle at 18% 8%,rgba(0,255,170,0.11),transparent 30%),radial-gradient(circle at 90% 12%,rgba(245,185,79,0.08),transparent 28%),linear-gradient(135deg,#08111c,#101827);color:#dbe4ee;font-family:Arial,sans-serif;display:flex;min-height:100vh;font-size:var(--font-body);}
body.public-home{display:block;}
a{color:#38bdf8;text-decoration:none;font-weight:800;}
a:hover{text-decoration:underline;}
.sidebar{width:280px;min-height:100vh;padding:28px;background:rgba(7,17,24,0.92);border-right:1px solid rgba(148,163,184,0.12);position:sticky;top:0;}
.logo{display:block;max-width:224px;margin-bottom:18px;text-decoration:none;}
.logo-img{display:block;width:100%;max-width:224px;max-height:62px;height:auto;object-fit:contain;}
.logo-fallback{display:none;font-size:25px;font-weight:950;background:linear-gradient(135deg,#fff,#00ffaa,#ffb86b);-webkit-background-clip:text;color:transparent;}
.nav-link{display:block;padding:13px 14px;border-radius:16px;color:#cbd7e3;margin:8px 0;background:rgba(148,163,184,0.055);text-decoration:none;font-weight:850;line-height:1.25;}
.nav-link:hover{background:rgba(0,255,170,0.10);text-decoration:none;}
.nav-section-label{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.13em;font-weight:950;margin:18px 0 8px 0;}
.tab-button{display:block;border:1px solid transparent;width:100%;text-align:left;cursor:pointer;font-family:inherit;text-decoration:none;appearance:none;-webkit-appearance:none;}
.tab-button.active-tab{background:rgba(0,255,170,0.16);color:white;border:1px solid rgba(0,255,170,0.24);box-shadow:0 12px 32px rgba(0,255,170,0.08);}
.pro-button{background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;font-weight:950;}
.menu-divider{height:1px;background:rgba(255,255,255,0.08);margin:18px 0;}
.menu-help{color:#94a3b8;font-size:12px;line-height:1.55;margin:10px 0 14px 0;}
.owner-box{margin-top:20px;color:#94a3b8;font-size:13px;line-height:1.6;}
.main{flex:1;padding:44px;overflow-y:auto;max-width:1500px;margin:0 auto;display:flex;flex-direction:column;}
.public-main{max-width:1180px;overflow:visible;}
.top-intel-layout{display:grid;grid-template-columns:1fr;gap:14px;align-items:start;margin-bottom:22px;}
.public-header{position:relative;z-index:60;width:100%;padding:16px 28px;background:rgba(7,17,24,0.94);border-bottom:1px solid rgba(148,163,184,0.12);backdrop-filter:blur(18px);}
.public-header-inner{display:flex;align-items:center;justify-content:space-between;gap:24px;width:min(1180px,100%);margin:0 auto;}
.public-header .logo{width:190px;margin:0;flex:0 0 auto;}
.public-header .logo-img{max-width:190px;max-height:52px;}
.public-nav-links{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap;}
.public-nav-link{display:inline-flex;align-items:center;justify-content:center;padding:10px 12px;border-radius:13px;color:#d6e0e9;text-decoration:none;font-size:13px;font-weight:900;white-space:nowrap;}
.public-nav-link:hover{background:rgba(148,163,184,0.09);text-decoration:none;}
.public-nav-primary{background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;}
.public-nav-primary:hover{background:linear-gradient(135deg,#5bedaF,#f4cc7b);color:#071018;}
.top-intel-layout .live-alert-strip{margin-bottom:0;}
.top-intel-layout.single-intel{grid-template-columns:1fr;}
.top-intel-layout.single-intel .top-bar{max-width:620px;width:100%;}
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
.hero-search-panel{max-width:820px;margin-top:28px;padding-top:24px;border-top:1px solid rgba(148,163,184,0.15);scroll-margin-top:18px;}
.hero-search-panel h2{margin:0 0 6px;color:#eef2f6;font-size:22px;line-height:1.2;}
.hero-search-panel>p{margin:0 0 14px;color:#aebdca;line-height:1.5;}
.hero-search-panel .smart-search{width:100%;max-width:none;}
.hero-search-panel .smart-search label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
.hero-search-panel .smart-search-row{border-color:rgba(74,222,163,0.28);box-shadow:0 22px 60px rgba(0,0,0,0.30),0 0 44px rgba(0,255,170,0.08);}
.hero-search-panel .smart-search button{min-width:150px;}
.suggested-searches{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:13px;}
.suggested-searches-label{color:#8fa1b2;font-size:12px;font-weight:850;margin-right:2px;}
.suggested-search-chip{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:8px 12px;border-radius:999px;background:rgba(148,163,184,0.08);border:1px solid rgba(148,163,184,0.16);color:#dbe7f0;font-size:12px;font-weight:900;text-decoration:none;}
.suggested-search-chip:hover{background:rgba(74,222,163,0.10);border-color:rgba(74,222,163,0.26);text-decoration:none;}
.example-reassurance{color:#91a3b4;font-size:13px;line-height:1.5;margin:12px 0 0;}
.product-steps{padding:22px 26px;}
.product-steps-header{text-align:center;margin:0 auto 15px;max-width:720px;}
.product-steps-header h2{margin:0 0 7px;color:#eef2f6;font-size:var(--font-section);}
.product-steps-header p{margin:0;color:#9fb0bf;line-height:1.55;}
.product-step-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;}
.product-step{padding:15px 16px;border-radius:17px;background:rgba(2,6,23,0.28);border:1px solid rgba(148,163,184,0.12);}
.product-step-number{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;margin-bottom:9px;border-radius:999px;background:rgba(74,222,163,0.13);color:#86efac;font-weight:950;}
.product-step strong{display:block;color:#e8eef4;font-size:16px;margin-bottom:5px;}
.product-step span{display:block;color:#9fb0bf;font-size:13px;line-height:1.45;}
.free-report-preview{padding:24px 28px;}
.free-report-preview-header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:14px;}
.free-report-preview h2{margin:0 0 8px;color:#eef2f6;font-size:var(--font-section);}
.free-report-preview-name{display:block;color:#f8fafc;font-size:20px;line-height:1.25;}
.free-report-preview-ticker{color:#8fa1b2;font-size:13px;font-weight:900;letter-spacing:0.08em;}
.free-report-signal{display:inline-flex;align-items:center;justify-content:center;min-width:74px;padding:9px 12px;border-radius:999px;background:rgba(245,158,11,0.13);border:1px solid rgba(245,158,11,0.24);font-size:13px;font-weight:950;}
.free-report-signal.buy{background:rgba(34,197,94,0.13);border-color:rgba(34,197,94,0.25);}
.free-report-signal.sell{background:rgba(239,68,68,0.13);border-color:rgba(239,68,68,0.25);}
.free-report-meta{color:#aebdca;font-size:13px;font-weight:850;margin:0 0 10px;}
.free-report-explanation,.free-report-next{max-width:780px;color:#b8c5d1;line-height:1.55;margin:0 0 9px;}
.free-report-next strong{color:#dce6ef;}
.free-report-preview .cta-primary{margin-top:6px;}
.live-alert-strip{position:relative;top:auto;z-index:1;width:100%;max-width:100%;margin-bottom:0;background:linear-gradient(90deg,rgba(0,255,170,0.12),rgba(56,189,248,0.10),rgba(255,184,107,0.10));border:1px solid rgba(255,255,255,0.12);border-radius:18px;overflow:hidden;box-shadow:0 18px 48px rgba(0,0,0,0.24);backdrop-filter:blur(18px);}
.live-alert-header{display:flex;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.08);font-weight:950;color:white;text-transform:uppercase;letter-spacing:0.08em;font-size:12px;}
.live-dot{width:9px;height:9px;border-radius:999px;background:#22c55e;box-shadow:0 0 18px rgba(34,197,94,0.8);}
.live-alert-track{overflow:hidden;white-space:nowrap;padding:5px 0;mask-image:linear-gradient(90deg,transparent,#000 3%,#000 97%,transparent);}
.live-alert-loop{display:flex;gap:18px;width:max-content;align-items:center;animation:tickerMove 58s linear infinite;will-change:transform;}
.live-alert-track:hover .live-alert-loop,.live-alert-track:focus-within .live-alert-loop{animation-play-state:paused;}
.live-headline{display:inline-flex;flex:0 0 660px;scroll-snap-align:start;align-items:center;gap:7px;min-width:0;max-width:none;min-height:30px;color:#dbe4ee;text-decoration:none;font-weight:800;border-right:1px solid rgba(148,163,184,0.24);padding:4px 18px 4px 0;white-space:nowrap;overflow:hidden;}
.live-headline:first-child{margin-left:10px;}
.live-news-empty{color:#a9b7c5;font-size:13px;line-height:1.5;padding:13px 14px;}
.live-headline-main{display:flex;align-items:center;gap:10px;line-height:1.35;}
.live-headline-main a:last-child{color:#e5e7eb;text-decoration:none;}
.live-headline-details{display:flex;align-items:center;gap:8px;flex-wrap:nowrap;padding-left:2px;}
.live-news-meta{display:none;}
.live-news-title{display:block;color:#eef2f6;font-size:13px;font-weight:900;line-height:1.2;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:120px;max-width:320px;flex:1 1 280px;}
.market-news-stocks{display:inline-flex;align-items:center;gap:4px;flex:0 0 auto;padding-left:0;}
.market-news-stocks::before,.market-news-impact::before{content:"·";color:#64748b;font-weight:900;margin-right:1px;}
.market-news-impact .live-meta{display:none;}
.market-news-stocks .live-affected-label{font-size:10px;letter-spacing:0.01em;margin-right:0;color:#94a3b8;text-transform:none;}
.market-news-stocks .live-stock-link{gap:3px;padding:2px 5px;font-size:10px;line-height:1.15;white-space:nowrap;text-transform:none;border-radius:5px;}
.market-news-stocks .live-stock-link::before{display:none;}
.market-news-stocks .live-stock-action{font-size:9px;padding-left:0;border-left:0;font-weight:800;}
.live-stock-more,.live-market-wide{display:inline-flex;align-items:center;padding:0 2px;font-size:10px;font-weight:850;line-height:1;white-space:nowrap;color:#94a3b8;background:none;border:0;}
.market-news-impact{display:inline-flex;align-items:center;gap:4px;flex:0 0 auto;padding-left:0;}
.market-news-impact .live-score{display:inline;color:#94a3b8;background:none;border:0;border-radius:0;padding:0;font-size:10px;font-weight:800;white-space:nowrap;}
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
.live-stock-link:hover{filter:brightness(1.18);color:white;text-decoration:none;}
@keyframes tickerMove{0%{transform:translateX(0);}100%{transform:translateX(-50%);}}
@media(prefers-reduced-motion:reduce){.live-alert-track{overflow-x:auto;mask-image:none;scrollbar-width:thin;}.live-alert-loop{animation:none;width:auto;padding-right:10px;}.ticker-duplicate{display:none;}}
.card,.market-card{background:linear-gradient(180deg,rgba(18,29,42,0.96),rgba(12,22,33,0.96));padding:28px;border-radius:26px;margin-bottom:22px;border:1px solid rgba(148,163,184,0.15);box-shadow:0 22px 65px rgba(0,0,0,0.28),inset 0 1px 0 rgba(255,255,255,0.035);}
.card h2{font-size:var(--font-section);line-height:1.14;}
.card p,.market-card p,td{font-size:var(--font-body);}
.hero-card{padding:clamp(28px,4vw,48px);background:linear-gradient(145deg,rgba(18,35,45,0.98),rgba(14,23,36,0.98));border-color:rgba(74,222,163,0.20);}
.hero-card h1{color:#f2f5f8;max-width:980px;margin:0 0 18px;font-size:var(--font-hero);line-height:1.04;letter-spacing:0;}
.hero-card .hero-subtitle{color:#bdc9d5;line-height:1.68;max-width:900px;font-size:16px;margin:0;}
.public-hero .hero-subtitle{max-width:720px;font-size:18px;line-height:1.55;}
.hero-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px;}
.cta-primary,.cta-secondary{display:inline-block;padding:14px 18px;border-radius:15px;text-decoration:none;font-weight:950;font-size:var(--font-cta);line-height:1.1;}
.cta-primary{background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;box-shadow:0 14px 34px rgba(0,255,170,0.12);}
.cta-secondary{background:rgba(148,163,184,0.09);border:1px solid rgba(148,163,184,0.20);color:#e2e8f0;}
	.premium-home-card{display:block;padding:20px 22px;background:linear-gradient(135deg,rgba(14,44,50,0.96),rgba(34,29,38,0.90));border-color:rgba(255,184,107,0.22);}
	.premium-home-header{max-width:860px;margin:0 auto 14px;text-align:left;}
	.premium-home-card h2{font-size:clamp(30px,2.4vw,38px);line-height:1.08;margin:0 0 8px;color:#f8fafc;letter-spacing:0;}
	.premium-home-card p{color:#cbd5e1;line-height:1.55;margin:0;font-size:15px;}
	.premium-home-kicker{color:#fbbf24;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:11px;margin:0 0 7px;}
	.premium-example-card{max-width:980px;margin:16px auto 0;padding:18px;border-radius:20px;background:linear-gradient(135deg,rgba(6,17,28,0.72),rgba(18,36,45,0.66));border:1px solid rgba(255,184,107,0.18);}
	.premium-example-header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:14px;}
	.premium-example-header strong{display:block;color:#f8fafc;font-size:20px;line-height:1.18;margin-top:4px;}
	.premium-example-pill{display:inline-flex;align-items:center;white-space:nowrap;border-radius:999px;padding:7px 10px;background:rgba(245,185,79,0.12);border:1px solid rgba(245,185,79,0.24);color:#fde68a;font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.09em;}
	.premium-example-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;}
	.premium-example-item{padding:14px;border-radius:15px;background:rgba(2,6,23,0.44);border:1px solid rgba(255,255,255,0.08);min-height:96px;}
	.premium-example-item strong{display:block;color:#eaf2f8;font-size:14px;line-height:1.2;margin-bottom:5px;}
	.premium-example-item span{display:block;color:#aebdca;font-size:13px;line-height:1.42;}
	.premium-example-note{margin-top:12px;color:#a8b6c6;font-size:12px;line-height:1.5;}
	.premium-brief-card{max-width:980px;margin:16px auto 0;padding:18px;border-radius:20px;background:rgba(7,17,28,0.60);border:1px solid rgba(74,222,163,0.18);}
	.premium-brief-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:14px;}
	.premium-brief-item{padding:14px;border-radius:15px;background:rgba(2,6,23,0.42);border:1px solid rgba(255,255,255,0.08);min-height:104px;}
	.premium-brief-item small{display:block;color:#fbbf24;font-size:10px;font-weight:950;text-transform:uppercase;letter-spacing:0.09em;margin-bottom:6px;}
	.premium-brief-item strong{display:block;color:#f8fafc;font-size:14px;line-height:1.25;margin-bottom:5px;}
	.premium-brief-item span{display:block;color:#aebdca;font-size:12px;line-height:1.42;}
	.premium-home-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:stretch;max-width:980px;margin:16px auto 0;}
.premium-home-feature{background:rgba(7,17,28,0.58);border:1px solid rgba(255,255,255,0.09);border-radius:18px;padding:18px;min-height:124px;}
.premium-home-feature strong{display:block;color:#f8fafc;margin-bottom:5px;font-size:18px;line-height:1.2;}
.premium-home-feature span{color:#aebdca;font-size:14px;line-height:1.45;}
.premium-home-tier{background:linear-gradient(145deg,rgba(7,17,28,0.72),rgba(26,45,45,0.58));border-color:rgba(74,222,163,0.18);}
.premium-home-actions{display:flex;align-items:center;justify-content:center;margin-top:14px;}
.premium-home-cta-banner{display:flex;align-items:center;justify-content:space-between;gap:16px;width:min(780px,100%);padding:15px 18px;border-radius:18px;background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;text-decoration:none;font-weight:950;box-shadow:0 18px 46px rgba(0,255,170,0.16);}
.premium-home-cta-banner:hover{text-decoration:none;filter:brightness(1.04);}
.premium-home-cta-banner span{display:block;font-size:15px;line-height:1.15;}
.premium-home-cta-banner small{display:block;color:rgba(7,16,24,0.76);font-size:12px;line-height:1.35;font-weight:850;text-align:right;}
.trust-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 22px;}
.trust-item{padding:15px 17px;border-radius:17px;background:rgba(14,25,37,0.90);border:1px solid rgba(148,163,184,0.13);color:#aebdca;line-height:1.5;font-size:13px;}
.trust-item strong{display:block;color:#dce6ef;margin-bottom:3px;}
.signal-snapshot-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:22px;}
.signal-snapshot{display:block;padding:20px;border-radius:20px;background:rgba(14,25,37,0.92);border:1px solid rgba(148,163,184,0.14);text-decoration:none;}
.signal-snapshot strong{display:block;font-size:30px;color:#f1f5f9;margin:5px 0;}
.signal-snapshot span{color:#9eafbe;font-size:13px;}
.newsletter-cta-card{display:flex;align-items:center;justify-content:space-between;gap:24px;background:linear-gradient(135deg,rgba(12,44,43,0.90),rgba(39,31,24,0.82));border-color:rgba(74,222,163,0.18);}
.newsletter-cta-card .cta-primary{display:inline-flex;align-items:center;justify-content:center;white-space:nowrap;flex:0 0 auto;min-height:42px;padding:12px 20px;line-height:1.1;border-radius:14px;text-align:center;}
.newsletter-cta-card h2{font-size:var(--font-section);line-height:1.14;}
.newsletter-cta-card p{color:#bdc9d5;line-height:1.65;margin-bottom:0;font-size:var(--font-body);}
.summary-grid,.market-grid,.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:18px;margin-bottom:22px;}
.feature-grid{grid-template-columns:repeat(3,1fr);}
.summary-card{cursor:pointer;transition:transform 0.18s ease,box-shadow 0.18s ease,border-color 0.18s ease;position:relative;overflow:hidden;border:none;text-align:left;color:white;font-family:inherit;width:100%;}
.summary-card:hover{transform:translateY(-4px);border-color:rgba(0,255,170,0.30);}
.summary-card h2{font-size:42px;margin:0 0 4px 0;}
.summary-card p{color:#94a3b8;margin:0;font-weight:800;}
.market-card small{color:#94a3b8;text-transform:uppercase;letter-spacing:0.10em;font-weight:900;font-size:11px;}
.market-card h3{font-size:var(--font-card-title);line-height:1.25;margin:10px 0;color:#eef2f6;}
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
.notice p{color:#b9c5d2;line-height:1.68;}
.upgrade-cta{display:inline-block;margin-top:8px;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;padding:12px 16px;border-radius:14px;font-weight:950;}
.empty-state{color:#94a3b8;padding:18px;background:rgba(255,255,255,0.04);border-radius:16px;margin-top:12px;}
.signal-guide-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px;}
.signal-guide-card{background:rgba(255,255,255,0.055);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;line-height:1.55;}
.signal-guide-card strong{display:block;color:white;margin-bottom:6px;font-size:17px;line-height:1.25;}
.signal-guide-card span{color:#94a3b8;font-size:13px;}
.premium-signal-callout{margin-top:18px;padding:18px;border-radius:20px;background:linear-gradient(135deg,rgba(0,255,170,0.12),rgba(255,184,107,0.08));border:1px solid rgba(0,255,170,0.18);color:#d1fae5;line-height:1.65;}
.highlight-target{animation:targetPulse 1.4s ease;}
@keyframes targetPulse{0%{box-shadow:0 0 0 0 rgba(0,255,170,0.42);}100%{box-shadow:0 0 0 18px rgba(0,255,170,0);}}
.impact-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:18px;}
.impact-card{background:linear-gradient(180deg,rgba(15,23,42,0.94),rgba(12,12,12,0.94));border:1px solid rgba(255,255,255,0.10);border-radius:24px;padding:22px;box-shadow:0 24px 65px rgba(0,0,0,0.30);}
.impact-card small{display:block;color:#00ffaa;text-transform:uppercase;letter-spacing:0.12em;font-size:11px;font-weight:950;margin-bottom:10px;}
.impact-card h3{font-size:20px;line-height:1.25;margin:0 0 10px 0;}
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
	@media(max-width:900px){:root{--font-hero:clamp(32px,9vw,38px);--font-section:clamp(24px,6vw,28px);}body{flex-direction:column;}.sidebar{width:100%;min-height:auto;position:relative;top:auto;padding:18px 16px;overflow-x:auto;white-space:nowrap;border-right:0;border-bottom:1px solid rgba(148,163,184,0.12);}.sidebar .logo{display:inline-block;max-width:170px;margin-bottom:12px;vertical-align:middle;}.sidebar .logo-img{max-width:170px;max-height:46px;}.sidebar .nav-section-label,.sidebar .menu-help,.sidebar .menu-divider,.sidebar .owner-box{display:none;}.sidebar .nav-link{display:inline-block;width:auto;padding:10px 12px;margin:0 6px 0 0;font-size:13px;}.main{padding:20px 16px;width:100%;}.top-bar{position:relative;justify-content:stretch;}.smart-search{width:100%;}.live-alert-strip{width:100%;}.live-alert-header{padding:8px 11px;font-size:11px;}.live-alert-track{padding:5px 0;}.live-alert-loop{gap:14px;animation-duration:48s;}.live-headline{flex-basis:540px;min-height:28px;padding:3px 14px 3px 0;gap:6px;}.live-news-title{font-size:12px;max-width:225px;}.market-news-stocks{display:inline-flex;}.market-news-stocks .live-affected-label{font-size:9px;}.market-news-impact .live-meta{display:none;}.market-news-impact .live-score{font-size:9px;}.summary-grid,.market-grid,.feature-grid,.impact-grid,.radar-summary,.signal-guide-grid,.filter-grid,.trust-strip,.signal-snapshot-grid{grid-template-columns:1fr;}.product-step-grid{grid-template-columns:1fr;}.premium-example-grid{grid-template-columns:repeat(2,minmax(0,1fr));}.premium-brief-grid{grid-template-columns:repeat(2,minmax(0,1fr));}.premium-home-grid{grid-template-columns:repeat(2,minmax(0,1fr));max-width:720px;}.newsletter-cta-card{align-items:flex-start;flex-direction:column;}.hero-card{padding:28px 22px}.hero-card h1{font-size:var(--font-hero);line-height:1.05;}.hero-card .hero-subtitle{font-size:15px;}.hero-actions a,.premium-price-row a{width:100%;text-align:center;}}
	@media(max-width:640px){.public-header{padding:12px 14px;}.public-header-inner{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:9px 12px;}.public-header .logo{width:154px;}.public-header .logo-img{max-width:154px;max-height:42px;}.public-nav-links{grid-column:1/-1;justify-content:flex-start;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;}.public-nav-link{padding:9px 6px;font-size:12px;white-space:normal;text-align:center;}.public-nav-primary{grid-column:2;grid-row:1;min-width:86px;}.public-nav-links .public-nav-primary{grid-column:auto;grid-row:auto;}.product-steps,.free-report-preview{padding:20px 18px;}.hero-search-panel{margin-top:22px;padding-top:20px;}.hero-search-panel .smart-search-row{flex-direction:column;}.hero-search-panel .smart-search button{width:100%;min-height:42px;}.suggested-searches-label{width:100%;}.suggested-search-chip{flex:1 1 calc(50% - 8px);}.free-report-preview-header{gap:12px;}.free-report-preview .cta-primary{width:100%;text-align:center;}.premium-home-card{padding:20px 18px;}.premium-home-header{text-align:left;margin-bottom:14px;}.premium-home-card h2{font-size:clamp(28px,7vw,34px);}.premium-example-header{flex-direction:column;gap:8px;}.premium-example-grid,.premium-home-grid{grid-template-columns:1fr;max-width:none;}.premium-home-feature{min-height:0;padding:16px;}.premium-home-cta-banner{align-items:flex-start;flex-direction:column;gap:6px;width:100%;}.premium-home-cta-banner small{text-align:left;}}
</style>
</head>
<body class="{% if is_public_home %}public-home{% else %}dashboard-view{% endif %}" data-public-home="{{ 'true' if is_public_home else 'false' }}">
{% if is_public_home %}
<header class="public-header">
    <div class="public-header-inner">
        <a class="logo" href="/" aria-label="StockRadar home"><img class="logo-img" src="/static/stockradar-main-logo.png" alt="StockRadar" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block';"><span class="logo-fallback">StockRadar</span></a>
        <nav class="public-nav-links" aria-label="Primary navigation">
            <a class="public-nav-link public-nav-primary" href="#stock-search">Search</a>
            <a class="public-nav-link" href="/how-it-works">How It Works</a>
            <a class="public-nav-link" href="/newsletter">Newsletter</a>
            <a class="public-nav-link" href="/upgrade">Premium</a>
        </nav>
    </div>
</header>
{% else %}
<div class="sidebar">
    <a class="logo" href="/" aria-label="StockRadar"><img class="logo-img" src="/static/stockradar-main-logo.png" alt="StockRadar" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block';"><span class="logo-fallback">StockRadar</span></a>
    <div class="nav-section-label">Main Menu</div>
    <div class="menu-help">Use these tabs to jump straight to the section you need.</div>
     <a class="nav-link tab-button {% if active_tab == 'overview' %}active-tab{% endif %}" href="/?tab=overview">🏠 Overview</a>
    <a class="nav-link" href="/beginner">🌱 Investment Compass</a>
    <a class="nav-link tab-button {% if active_tab == 'signals' %}active-tab{% endif %}" href="/?tab=signals">📊 AI Signals</a>
    <a class="nav-link tab-button {% if active_tab == 'radar' %}active-tab{% endif %}" href="/?tab=radar">🌍 Impact Radar</a>
    <a class="nav-link tab-button {% if active_tab == 'watchlist' %}active-tab{% endif %}" href="/?tab=watchlist">📋 AI Watchlist</a>
    <a class="nav-link" href="/premium-watchlist">🧠 Premium Watchlist</a>
    <a class="nav-link" href="/compare">⚖️ Compare Stocks <span style="color:#ffb86b;font-size:11px;font-weight:950;">Premium</span></a>
    <div class="nav-section-label">Risk Check</div>
        <a class="nav-link" href="/portfolio-fit">🧩 Portfolio Fit</a>
        <a class="nav-link" href="/universe">🌍 Stock Universe</a>
    <div class="menu-divider"></div>

    <div class="nav-section-label">Account</div>
    {% if owner_logged_in %}
        <a class="nav-link pro-button" href="/owner">✅ Premium Active</a>
        <a class="nav-link" href="/logout">🚪 Logout</a>
    {% elif has_premium_access %}
        <a class="nav-link pro-button" href="/manage-subscription">✅ Premium Active</a>
        <a class="nav-link" href="/logout">🚪 End Premium Session</a>
    {% else %}
        <a class="nav-link pro-button" href="/upgrade">🚀 Upgrade to Premium — £5/month</a>
        <a class="nav-link" href="/login">🔐 Login</a>
    {% endif %}
    <div class="owner-box">Premium unlocks full AI reasoning, risk reads, next-move analysis and market intelligence.</div>
</div>
{% endif %}


<div class="main {% if is_public_home %}public-main{% endif %}" id="main-content" tabindex="-1">
    <div class="top-intel-layout {% if not live_headlines %}single-intel{% endif %}">
        <div class="live-alert-strip" aria-label="Live market headlines">
            <div class="live-alert-header">
                <span class="live-dot"></span>
                Market News
<span id="marketNewsStatus" data-live-news-active="{{ 'true' if live_news_active else 'false' }}">Local time: loading{% if live_news_active %} • Live headlines{% else %} • Feed reconnecting{% endif %}</span>
            </div>
            {% if live_headlines %}
            <div class="live-alert-track" id="marketNewsTrack" aria-live="polite" data-refresh-interval="{{ market_news_refresh_interval_ms }}">
                <div class="live-alert-loop" id="marketNewsLoop">
                {% for repeat in range(2) %}
                {% for headline in live_headlines[:market_news_ticker_limit] %}
                <span class="live-headline {% if repeat %}ticker-duplicate{% endif %}" {% if repeat %}aria-hidden="true"{% endif %}>
                    <span class="live-news-meta">{{ headline.get('source', 'StockRadar Market Impact Feed') }} • {{ headline.get('published_label', 'Theme watch') }}</span>
                    <a class="live-news-title" href="{{ headline.get('article_url', '/') }}" {% if repeat %}tabindex="-1"{% endif %} {% if headline.get('article_url', '').startswith('http') %}target="_blank" rel="noopener noreferrer"{% endif %}>{{ headline.get('headline', 'Market headlines are reconnecting') }}</a>
                    <span class="live-headline-details market-news-stocks">
                        <span class="live-affected-label">Affected:</span>
                        {% if headline.get('stock_links') %}
                        {% for stock in headline.get('stock_links', [])[:2] %}
                        <a class="live-stock-link {{ stock.get('signal_class', 'hold') }}" href="{{ stock.get('url', '/') }}" {% if repeat %}tabindex="-1"{% endif %}>{{ stock.get('display_label') or stock_display_label(stock.get('ticker', 'SPY')) }} <span class="live-stock-action">{{ stock.get('action_text', stock.get('signal', 'HOLD'))|title }}</span></a>
                        {% endfor %}
                        {% set stock_link_total = headline.get('stock_links_total', headline.get('stock_links', [])|length) %}
                        {% if stock_link_total > 2 %}
                        <span class="live-stock-more">+{{ stock_link_total - 2 }} more</span>
                        {% endif %}
                        {% else %}
                        <span class="live-market-wide">Market-wide</span>
                        {% endif %}
                    </span>
                    <span class="live-headline-details market-news-impact">
                        <span class="live-score">Impact {{ headline.get('impact_score', 'Pending') }}</span>
                        <span class="live-meta">{{ headline.get('direction', 'Theme watch') }}</span>
                    </span>
                </span>
                {% endfor %}
                {% endfor %}
                </div>
            </div>
            {% else %}
            <div class="live-alert-track" id="marketNewsTrack" aria-live="polite" data-refresh-interval="{{ market_news_refresh_interval_ms }}">
                <div class="live-news-empty" id="marketNewsEmpty">Market headlines temporarily unavailable. StockRadar will refresh when the feed reconnects.</div>
            </div>
            {% endif %}
        </div>

        {% if not is_public_home %}
        <div class="top-bar" aria-label="Quick search and navigation">
            <form class="smart-search" onsubmit="return runSmartSearch(event)">
                <label for="smartSearchInput">Quick Search</label>
                <div class="smart-search-row">
                    <input id="smartSearchInput" type="search" placeholder="Type a ticker, S&P 500, BUY, AI, Premium..." autocomplete="off" aria-label="Type to search stocks, indexes or dashboard sections">
                    <button type="submit">Search</button>
                </div>
                <div class="search-hint">Type and press Enter or Search. Try: Apple, Tesla, Nvidia, Microsoft, S&P 500, Nasdaq, BUY, AI or Premium.</div>
                <div id="searchMessage" class="search-message" role="status"></div>
            </form>
        </div>
        {% endif %}
    </div>

	    <div class="card hero-card {% if is_public_home %}public-hero{% endif %}" id="investment-compass-card">
        {% if is_public_home %}
	        <h1>Learn to think like an investor.</h1>
	        <p class="hero-subtitle">Search any stock or ETF and get a clear, plain-English research summary in seconds.</p>
            <section class="hero-search-panel" id="stock-search" aria-labelledby="stock-search-heading">
                <h2 id="stock-search-heading">Start with a company you already know</h2>
                <p>Search any stock or ETF.</p>
                <form class="smart-search" onsubmit="return runSmartSearch(event)">
                    <label for="smartSearchInput">Search a stock or ETF</label>
                    <div class="smart-search-row">
                        <input id="smartSearchInput" type="search" placeholder="Try Microsoft, Apple, SPY or MSFT" autocomplete="off" aria-label="Search a stock or ETF">
                        <button type="submit">View free report</button>
                    </div>
                    <div id="searchMessage" class="search-message" role="status"></div>
                </form>
                <div class="suggested-searches" aria-label="Suggested example reports">
                    <span class="suggested-searches-label">Try an example</span>
                    <a class="suggested-search-chip" href="/stock/MSFT">Microsoft — MSFT</a>
                    <a class="suggested-search-chip" href="/stock/AAPL">Apple — AAPL</a>
                    <a class="suggested-search-chip" href="/stock/AMZN">Amazon — AMZN</a>
                    <a class="suggested-search-chip" href="/stock/SPY">S&amp;P 500 ETF — SPY</a>
                </div>
                <p class="example-reassurance">New to investing? Start with a company or fund you already recognise.</p>
            </section>
        {% else %}
	        <p style="color:#4adea3;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 12px;">AI-assisted market research</p>
	        <h1>Learn to think like an investor.</h1>
	        <p class="hero-subtitle">We help people become better investors through plain-English market signals, practical decision support and investing education—without the noise.</p>
	        <p style="color:#91a3b4;font-size:14px;line-height:1.6;margin:18px 0 0;">Start free with StockRadar Weekly, explore the live signal dashboard, then upgrade when you want the reasoning and risk read behind each signal.</p>
            <div class="hero-actions">
                <a class="cta-primary" href="/newsletter">Join Free</a>
                <a class="cta-secondary" href="/upgrade">Upgrade to Premium — £5/month</a>
                <a class="cta-secondary" href="/?tab=signals">Explore Signals</a>
            </div>
            <p style="color:#91a3b4;font-size:13px;line-height:1.6;margin:18px 0 0;">Signals are research prompts, not financial advice. <a href="/how-it-works">How it works</a></p>
        {% endif %}
    </div>

    {% if is_public_home %}
    <section class="card product-steps" id="how-stockradar-works" aria-labelledby="product-steps-heading">
        <div class="product-steps-header">
            <h2 id="product-steps-heading">Start with one stock</h2>
        </div>
        <div class="product-step-grid">
            <div class="product-step"><span class="product-step-number">1</span><strong>Search</strong><span>Choose a company or fund you recognise.</span></div>
            <div class="product-step"><span class="product-step-number">2</span><strong>Understand</strong><span>See the current signal in plain English.</span></div>
            <div class="product-step"><span class="product-step-number">3</span><strong>Research</strong><span>Learn what evidence and risks may matter next.</span></div>
        </div>
    </section>

    <section class="card free-report-preview" id="free-report-preview" aria-labelledby="free-report-preview-heading">
        <div class="free-report-preview-header">
            <div>
                <h2 id="free-report-preview-heading">See a free report in action</h2>
                <strong class="free-report-preview-name">{{ free_report_preview.company_name }}</strong>
                <span class="free-report-preview-ticker">{{ free_report_preview.ticker }}</span>
            </div>
            {% if free_report_preview.is_current %}
            <span class="free-report-signal {{ free_report_preview.signal|lower }}">{{ free_report_preview.signal }}</span>
            {% else %}
            <span class="free-report-signal">Live report</span>
            {% endif %}
        </div>
        {% if free_report_preview.is_current %}
        <p class="free-report-meta">Signal strength: {{ free_report_preview.strength }}</p>
        {% endif %}
        <p class="free-report-explanation">{{ free_report_preview.explanation }}</p>
        <p class="free-report-next"><strong>Research next:</strong> {{ free_report_preview.research_next }}</p>
        <a class="cta-primary" href="/stock/MSFT">View Microsoft’s free report</a>
    </section>
    {% endif %}

	    <div class="card premium-home-card" id="premium-decision-section">
	        <div class="premium-home-header">
	            <p class="premium-home-kicker">Premium preview</p>
	            <h2>Trading apps show you the market. Premium helps you understand the signal.</h2>
	            <p>Free tells you what the scanner is flagging. Premium is the calm decision-support layer: why it matters, what risk to check, where it may fit and what to research next.</p>
	        </div>
	        <div class="premium-example-card" aria-label="Static Premium example preview">
	            <div class="premium-example-header">
	                <div>
	                    <p class="premium-home-kicker">Example only</p>
	                    <strong>Microsoft signal preview</strong>
	                </div>
	                <span class="premium-example-pill">Static preview</span>
	            </div>
	            <div class="premium-example-grid">
	                <div class="premium-example-item"><strong>AI reasoning</strong><span>Why the current setup may deserve research, in plain English.</span></div>
	                <div class="premium-example-item"><strong>Risk read</strong><span>What could weaken the signal or make the setup less useful.</span></div>
	                <div class="premium-example-item"><strong>Decision context</strong><span>How confidence, momentum and valuation questions fit together.</span></div>
	                <div class="premium-example-item"><strong>Clearer signal</strong><span>What to check before treating a BUY/HOLD/SELL prompt as actionable research.</span></div>
	            </div>
	            <p class="premium-example-note">Example only — not personalised financial advice. Premium does not tell you what to buy or sell.</p>
	        </div>
	        <div class="premium-brief-card" aria-label="Premium Decision Brief preview">
	            <p class="premium-home-kicker">{% if has_premium_access %}Today's Decision Brief{% else %}Locked Decision Brief preview{% endif %}</p>
	            <h3 style="color:#f8fafc;margin:0 0 6px;font-size:22px;line-height:1.2;">What Premium helps you sort first.</h3>
	            <p>{% if has_premium_access %}A quick decision-support scan from the current StockRadar universe.{% else %}Premium turns the signal table into a practical review of strongest setups, caution names and portfolio role.{% endif %}</p>
	            <div class="premium-brief-grid">
	                {% if has_premium_access %}
	                {% if premium_decision_brief.strongest %}
	                <div class="premium-brief-item"><small>Strongest setup</small><strong>{{ premium_decision_brief.strongest.label }}</strong><span>{{ premium_decision_brief.strongest.signal }} • {{ premium_decision_brief.strongest.confidence }} research prompt</span></div>
	                {% endif %}
	                {% if premium_decision_brief.caution %}
	                <div class="premium-brief-item"><small>Caution zone</small><strong>{{ premium_decision_brief.caution.label }}</strong><span>{{ premium_decision_brief.caution.signal }} • risk to check before adding exposure</span></div>
	                {% endif %}
	                {% if premium_decision_brief.market_setup %}
	                <div class="premium-brief-item"><small>ETF / market setup</small><strong>{{ premium_decision_brief.market_setup.label }}</strong><span>{{ premium_decision_brief.market_setup.signal }} • broad-market context</span></div>
	                {% endif %}
	                {% if premium_decision_brief.non_us %}
	                <div class="premium-brief-item"><small>UK / non-US idea</small><strong>{{ premium_decision_brief.non_us.label }}</strong><span>{{ premium_decision_brief.non_us.signal }} • regional diversification prompt</span></div>
	                {% endif %}
	                {% if premium_decision_brief.watchlist %}
	                <div class="premium-brief-item"><small>Watchlist idea</small><strong>{{ premium_decision_brief.watchlist.label }}</strong><span>Review what would make the setup stronger or weaker.</span></div>
	                {% endif %}
	                {% else %}
	                <div class="premium-brief-item"><small>Strongest setup</small><strong>Locked</strong><span>See which current signal deserves research first — and why.</span></div>
	                <div class="premium-brief-item"><small>Caution zone</small><strong>Locked</strong><span>Keep the main risk prompt visible before acting.</span></div>
	                <div class="premium-brief-item"><small>Portfolio context</small><strong>Locked</strong><span>Review role and possible duplicate exposure.</span></div>
	                <div class="premium-brief-item"><small>Watch next</small><strong>Locked</strong><span>Know what evidence should trigger another review.</span></div>
	                {% endif %}
	            </div>
	            <p class="premium-example-note">Preview uses existing StockRadar signals only. It is educational research context, not a personal recommendation.</p>
	        </div>
	        <div class="premium-home-grid" aria-label="Premium features">
            <div class="premium-home-feature"><strong>Decision Score</strong><span>See how strong the signal looks as a research prompt.</span></div>
            <div class="premium-home-feature"><strong>AI Reasoning</strong><span>Read the plain-English context behind the signal.</span></div>
            <div class="premium-home-feature"><strong>Risk Read</strong><span>Spot caution points and what could weaken the case.</span></div>
            <div class="premium-home-feature"><strong>Portfolio Fit</strong><span>Check role, concentration and duplicate exposure.</span></div>
            <div class="premium-home-feature"><strong>Compare Stocks</strong><span>Review two tickers side by side before choosing.</span></div>
            <div class="premium-home-feature"><strong>Premium Watchlist</strong><span>Turn signals into strongest, caution and theme views.</span></div>
            <div class="premium-home-feature"><strong>Before You Act</strong><span>Use a checklist before treating a signal as actionable research.</span></div>
            <div class="premium-home-feature premium-home-tier"><strong>Free</strong><span>Signal preview, market news, basic research prompts and StockRadar Weekly.</span></div>
            <div class="premium-home-feature premium-home-tier"><strong>Premium</strong><span>Full reasoning, risk read, portfolio fit, comparisons and before-you-act checks.</span></div>
        </div>
        <div class="premium-home-actions">
            {% if has_premium_access %}
            <a class="premium-home-cta-banner" href="/premium-watchlist"><span>Open Premium Watchlist</span><small>Free shows the signal. Premium explains the decision.</small></a>
            {% else %}
            <a class="premium-home-cta-banner" href="/upgrade"><span>Upgrade to Premium — £5/month</span><small>Free shows the signal. Premium explains the decision.</small></a>
            {% endif %}
        </div>
    </div>

    <div class="trust-strip" aria-label="How to use StockRadar">
        <div class="trust-item"><strong>Educational research tool</strong>Plain-English market context for independent research.</div>
        <div class="trust-item"><strong>Prompts, not instructions</strong>BUY, HOLD and SELL patterns are starting points to investigate.</div>
        <div class="trust-item"><strong>Risk still matters</strong>Check concentration, time horizon and portfolio fit before acting.</div>
    </div>

    {% if not is_public_home %}
    <div class="signal-snapshot-grid" aria-label="Current signal overview">
        <a class="signal-snapshot" href="/?tab=signals&open=buy-panel"><span>Constructive patterns</span><strong class="buy">{{ buy_count }}</strong><span>Explore BUY research prompts</span></a>
        <a class="signal-snapshot" href="/?tab=signals&open=hold-panel"><span>Steady patterns</span><strong class="hold">{{ hold_count }}</strong><span>Review HOLD and watch signals</span></a>
        <a class="signal-snapshot" href="/?tab=signals&open=sell-panel"><span>Caution patterns</span><strong class="sell">{{ sell_count }}</strong><span>Check weakening signals and risk</span></a>
    </div>
    {% endif %}

    <div class="card newsletter-cta-card" id="newsletter-cta">
        <div>
            <p style="color:#4adea3;font-weight:950;text-transform:uppercase;letter-spacing:0.13em;font-size:12px;margin:0 0 8px;">Free weekly market signal</p>
            <h2 style="color:#eef2f6;margin:0 0 8px;">StockRadar Weekly</h2>
            <p>Get the 5-minute market signal every week — what’s strengthening, what’s weakening, and what may matter next. Free market context, signal highlights and risk prompts for everyday investors.</p>
        </div>
        <a class="cta-primary" href="/newsletter">Join Free</a>
    </div>
    {% if not is_public_home %}
    <div id="overview-section" class="dashboard-section {% if active_tab == 'overview' %}active-section{% endif %}">
    <div class="card">
        <h2>Market status</h2>
        <p style="color:#94a3b8;line-height:1.7;">
            UK market status: <span class="status-pill {% if market_status.uk_status == 'OPEN' %}status-open{% else %}status-closed{% endif %}">{{ market_status.uk_status }}</span>
            &nbsp; Exchange time: {{ market_status.uk_time }} London<br><br>
            US market status: <span class="status-pill {% if market_status.us_status == 'OPEN' %}status-open{% else %}status-closed{% endif %}">{{ market_status.us_status }}</span>
            &nbsp; Exchange time: {{ market_status.us_time }} New York
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
        {% if has_premium_access %}
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
        {% if has_premium_access %}<div class="notice"><h3>✅ Premium signal breakdown active</h3><p>You have full premium access. Use the linked tickers above to open the premium stock intelligence pages.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock full AI signal breakdown</h3><p>Premium adds conviction context, risk reads and deeper signal reasoning.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Premium — £5/month</a></div>{% endif %}
    </div>

    <div id="hold-panel" class="card panel">
        <h2>Monitor Zone — HOLD Signals</h2>
        {% if hold_rows %}
        <table><tr><th>Stock</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in hold_rows %}<tr><td class="hold"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No HOLD signals are currently active.</div>{% endif %}
        {% if has_premium_access %}<div class="notice"><h3>✅ Premium HOLD analysis active</h3><p>You have full premium access to deeper HOLD interpretation and premium stock pages.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock deeper HOLD analysis</h3><p>Premium explains what would make a HOLD more useful, riskier or worth waiting on.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Premium — £5/month</a></div>{% endif %}
    </div>

    <div id="sell-panel" class="card panel">
        <h2>Risk Warning — SELL Signals</h2>
        {% if sell_rows %}
        <table><tr><th>Stock</th><th>Confidence</th><th>AI Reason</th></tr>{% for item in sell_rows %}<tr><td class="sell"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% else %}<div class="empty-state">No SELL signals are currently active.</div>{% endif %}
        {% if has_premium_access %}<div class="notice"><h3>✅ Premium downside warnings active</h3><p>You have full premium access to downside warnings and premium risk interpretation.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock full downside warnings</h3><p>Premium adds risk interpretation, concentration checks and watch-next triggers for weaker setups.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Premium — £5/month</a></div>{% endif %}
    </div>

    <div id="conviction-panel" class="card panel">
        <h2>Premium Focus — Highest AI Conviction</h2>
        <table><tr><th>Stock</th><th>Conviction</th><th>AI Insight</th></tr>{% for item in conviction_rows %}<tr><td class="buy"><a class="stock-link" href="/stock/{{ item.ticker }}">{{ stock_display_label(item.ticker) }}</a></td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>{% endfor %}</table>
        {% if has_premium_access %}<div class="notice"><h3>✅ Premium AI-ranked opportunities active</h3><p>You have full premium access to the AI watchlist, conviction engine and premium market intelligence.</p></div>{% else %}<div class="notice"><h3>🔒 Unlock premium conviction intelligence</h3><p>Premium turns High Conviction into a research shortlist with deeper AI reasoning, risk read and what-to-watch-next context on each linked stock page.</p><a class="upgrade-cta" href="/upgrade">Upgrade to Premium — £5/month</a></div>{% endif %}
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
        <h2>Less noise. Better decisions.</h2>
        <p style="color:#94a3b8;line-height:1.7;">Stop drowning in financial news. StockRadar filters market noise into clear signals, practical decision support and investing lessons that help you build confidence over time.</p>
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
                {% if has_premium_access %}
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
                {% if has_premium_access %}
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
        {% if has_premium_access %}<div class="card"><h2>Premium Active</h2><p>Your account has premium access. Upgrade prompts are hidden and premium intelligence is unlocked.</p></div>{% else %}<div class="card"><h2>Premium Preview</h2><p>Signal interpretation, decision context and portfolio-fit checks.</p></div>{% endif %}
        <div class="card"><h2>Daily Value</h2><p>Use the dashboard to check what is strengthening, weakening and worth watching.</p></div>
    </div>
    </div>
    {% endif %}
    {{ disclaimer_footer() | safe }}
</div>

<script>
function showDashboardSection(sectionId, button){var sections=document.querySelectorAll('.dashboard-section');sections.forEach(function(section){section.classList.remove('active-section');});var target=document.getElementById(sectionId);if(target){target.classList.add('active-section');target.scrollIntoView({behavior:'smooth',block:'start'});}var buttons=document.querySelectorAll('.tab-button');buttons.forEach(function(btn){btn.classList.remove('active-tab');});if(button){button.classList.add('active-tab');}}
function togglePanel(panelId){var panel=document.getElementById(panelId);var button=document.querySelector('[aria-controls="'+panelId+'"]');if(panel.classList.contains('open')){panel.classList.remove('open');if(button){button.setAttribute('aria-expanded','false');}}else{panel.classList.add('open');if(button){button.setAttribute('aria-expanded','true');}panel.scrollIntoView({behavior:'smooth',block:'start'});}}
function flashTarget(element){if(!element){return;}element.classList.remove('highlight-target');void element.offsetWidth;element.classList.add('highlight-target');}
function openPanelAndJump(panelId){var panel=document.getElementById(panelId);var button=document.querySelector('[aria-controls="'+panelId+'"]');if(!panel){return;}panel.classList.add('open');if(button){button.setAttribute('aria-expanded','true');}panel.scrollIntoView({behavior:'smooth',block:'start'});flashTarget(panel);}
function showSearchMessage(message){var messageBox=document.getElementById('searchMessage');if(!messageBox){return;}messageBox.textContent=message;messageBox.style.display='block';}
function runSmartSearch(event){event.preventDefault();var input=document.getElementById('smartSearchInput');if(!input){return false;}var publicHome=document.body.getAttribute('data-public-home')==='true';var query=input.value.trim().toUpperCase();if(!query){showSearchMessage(publicHome?'Type a company, ticker or ETF first.':'Type a ticker or section name first.');return false;}var map={'APPLE':'AAPL','AAPL':'AAPL','TESLA':'TSLA','TSLA':'TSLA','NVIDIA':'NVDA','NVDA':'NVDA','MICROSOFT':'MSFT','MSFT':'MSFT','AMAZON':'AMZN','AMZN':'AMZN','GOOGLE':'GOOGL','ALPHABET':'GOOGL','META':'META','FACEBOOK':'META','SPCX':'SPCX','SPACEX':'SPCX','SPACE X':'SPCX','SPAX.PVT':'SPCX','MAERSK':'MAERSK-B.CO','MAERSK B':'MAERSK-B.CO','MAERSK A':'MAERSK-A.CO','A P MOLLER MAERSK':'MAERSK-B.CO','AP MOLLER MAERSK':'MAERSK-B.CO','BAE.L':'BA.L','BAE SYSTEMS':'BA.L','S&P 500':'^GSPC','SP500':'^GSPC','S&P':'^GSPC','NASDAQ':'^IXIC','FTSE':'^FTSE','FTSE 100':'^FTSE','HSBC':'HSBA.L','BP':'BP.L','ASTRAZENECA':'AZN.L','SHELL':'SHEL.L'};if(map[query]){window.location.href='/stock/'+encodeURIComponent(map[query]);return false;}if(publicHome){if(/^[A-Z0-9.^-]{1,12}$/.test(query)){window.location.href='/stock/'+encodeURIComponent(query);return false;}showSearchMessage('No matching stock or ETF found. Try Microsoft, Apple, SPY or MSFT.');return false;}if(['AI','RECOMMENDATIONS','AI RECOMMENDATIONS','WATCHLIST'].includes(query)){window.location.href='/?tab=watchlist';return false;}if(['BUY','BUYS','BUY SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=buy-panel';return false;}if(['HOLD','HOLDS','HOLD SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=hold-panel';return false;}if(['SELL','SELLS','SELL SIGNALS'].includes(query)){window.location.href='/?tab=signals&open=sell-panel';return false;}if(['CONVICTION','HIGH CONVICTION','TOP'].includes(query)){window.location.href='/?tab=signals&open=conviction-panel';return false;}if(['POLITICS','POLITICAL','GEOPOLITICS','GEOPOLITICAL','RADAR','MARKET IMPACT','IMPACT RADAR'].includes(query)){window.location.href='/?tab=radar';return false;}
if(['PRO','PREMIUM','UPGRADE','PAYMENT','SUBSCRIPTION'].includes(query)){window.location.href='/upgrade';return false;}if(/^[A-Z0-9.^-]{1,12}$/.test(query)){window.location.href='/stock/'+encodeURIComponent(query);return false;}showSearchMessage('No matching stock or section found. Try Apple, AAPL, S&P 500, Nasdaq, BUY, SELL, AI or Premium.');return false;}
function setSignalFilter(signal){var select=document.getElementById('signalFilterValue');if(select){select.value=signal;}document.querySelectorAll('[data-signal-filter]').forEach(function(button){button.classList.toggle('active-filter',button.getAttribute('data-signal-filter')===signal);});applySignalFilters();}
function resetSignalFilters(){var tickerInput=document.getElementById('tickerFilterInput');var sectorSelect=document.getElementById('sectorFilterSelect');if(tickerInput){tickerInput.value='';}if(sectorSelect){sectorSelect.value='ALL';}setSignalFilter('ALL');}
function applySignalFilters(){var tickerInput=document.getElementById('tickerFilterInput');var sectorSelect=document.getElementById('sectorFilterSelect');var signalSelect=document.getElementById('signalFilterValue');var tickerQuery=tickerInput ? tickerInput.value.trim().toUpperCase() : '';var selectedSector=sectorSelect ? sectorSelect.value : 'ALL';var selectedSignal=signalSelect ? signalSelect.value : 'ALL';var rows=document.querySelectorAll('.signal-row');var visibleCount=0;rows.forEach(function(row){var rowTicker=(row.getAttribute('data-ticker')||'').toUpperCase();var rowSignal=row.getAttribute('data-signal')||'';var rowSector=row.getAttribute('data-sector')||'AI Watchlist';var tickerMatch=!tickerQuery || rowTicker.includes(tickerQuery);var signalMatch=selectedSignal==='ALL' || rowSignal===selectedSignal;var sectorMatch=selectedSector==='ALL' || rowSector===selectedSector;var shouldShow=tickerMatch && signalMatch && sectorMatch;row.classList.toggle('hidden-signal-row',!shouldShow);if(shouldShow){visibleCount+=1;}});var status=document.getElementById('signalFilterStatus');if(status){var signalText=selectedSignal==='ALL'?'all signals':selectedSignal+' signals';var sectorText=selectedSector==='ALL'?'all sectors':selectedSector;var tickerText=tickerQuery?(' matching '+tickerQuery):'';status.textContent='Showing '+visibleCount+' stocks for '+signalText+', '+sectorText+tickerText+'.';}}
function makeMarketNewsItem(item,duplicate){var card=document.createElement('span');card.className='live-headline'+(duplicate?' ticker-duplicate':'');if(duplicate){card.setAttribute('aria-hidden','true');}var meta=document.createElement('span');meta.className='live-news-meta';meta.textContent=(item.source||'StockRadar Market Impact Feed')+' • '+(item.published_label||'Theme watch');card.appendChild(meta);var title=document.createElement('a');title.className='live-news-title';title.href=item.article_url||'/';title.textContent=item.headline||'Market headlines are reconnecting';if((item.article_url||'').indexOf('http')===0){title.target='_blank';title.rel='noopener noreferrer';}if(duplicate){title.tabIndex=-1;}card.appendChild(title);var allStockLinks=item.stock_links||[];var stockLinks=allStockLinks.slice(0,2);var stocks=document.createElement('span');stocks.className='live-headline-details market-news-stocks';var affected=document.createElement('span');affected.className='live-affected-label';affected.textContent='Affected:';stocks.appendChild(affected);if(stockLinks.length){stockLinks.forEach(function(stock){var link=document.createElement('a');link.className='live-stock-link '+(stock.signal_class||'hold');link.href=stock.url||'/';if(duplicate){link.tabIndex=-1;}link.appendChild(document.createTextNode(stock.display_label||stock.ticker||'SPY'));var action=document.createElement('span');action.className='live-stock-action';var actionText=stock.action_text||stock.signal||'HOLD';action.textContent=actionText.charAt(0).toUpperCase()+actionText.slice(1).toLowerCase();link.appendChild(action);stocks.appendChild(link);});var stockTotal=Math.max(parseInt(item.stock_links_total||allStockLinks.length,10),allStockLinks.length);if(stockTotal>2){var more=document.createElement('span');more.className='live-stock-more';more.textContent='+'+(stockTotal-2)+' more';stocks.appendChild(more);}}else{var marketWide=document.createElement('span');marketWide.className='live-market-wide';marketWide.textContent='Market-wide';stocks.appendChild(marketWide);}card.appendChild(stocks);var impact=document.createElement('span');impact.className='live-headline-details market-news-impact';var score=document.createElement('span');score.className='live-score';score.textContent='Impact '+(item.impact_score||'Pending');var direction=document.createElement('span');direction.className='live-meta';direction.textContent=item.direction||'Theme watch';impact.appendChild(score);impact.appendChild(direction);card.appendChild(impact);return card;}
function browserLocalTimeLabel(date){var fallbackTimeZone='Europe/London';var browserTimeZone=fallbackTimeZone;try{browserTimeZone=Intl.DateTimeFormat().resolvedOptions().timeZone||fallbackTimeZone;}catch(error){browserTimeZone=fallbackTimeZone;}var options={hour:'2-digit',minute:'2-digit',timeZone:browserTimeZone,timeZoneName:'short'};try{return new Intl.DateTimeFormat(undefined,options).format(date||new Date());}catch(error){return new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',timeZone:fallbackTimeZone,timeZoneName:'short'}).format(date||new Date());}}
function updateMarketNewsStatus(liveActive){var status=document.getElementById('marketNewsStatus');if(!status){return;}if(typeof liveActive!=='boolean'){liveActive=status.getAttribute('data-live-news-active')==='true';}status.setAttribute('data-live-news-active',liveActive?'true':'false');status.textContent='Local time: '+browserLocalTimeLabel(new Date())+(liveActive?' • Live headlines':' • Feed reconnecting');}
function renderMarketNews(items){var track=document.getElementById('marketNewsTrack');if(!track){return;}track.innerHTML='';if(!items||!items.length){var empty=document.createElement('div');empty.className='live-news-empty';empty.textContent='Market headlines temporarily unavailable. StockRadar will refresh when the feed reconnects.';track.appendChild(empty);return;}var loop=document.createElement('div');loop.className='live-alert-loop';for(var repeat=0;repeat<2;repeat+=1){items.forEach(function(item){loop.appendChild(makeMarketNewsItem(item,repeat===1));});}track.appendChild(loop);}
function refreshMarketNews(){var track=document.getElementById('marketNewsTrack');if(!track){return;}fetch('/api/market-news',{headers:{'Accept':'application/json'},cache:'no-store'}).then(function(response){if(!response.ok){throw new Error('market news refresh failed');}return response.json();}).then(function(payload){if(payload&&Array.isArray(payload.items)&&payload.items.length){renderMarketNews(payload.items);updateMarketNewsStatus(!!payload.live_news_active);}else if(payload){updateMarketNewsStatus(!!payload.live_news_active);if(!track.querySelector('.live-headline')){renderMarketNews([]);}}else if(!track.querySelector('.live-headline')){renderMarketNews([]);}}).catch(function(){/* Keep existing headlines visible if refresh fails. */});}
function scheduleMarketNewsRefresh(){var track=document.getElementById('marketNewsTrack');if(!track){return;}var interval=parseInt(track.getAttribute('data-refresh-interval')||'300000',10);if(!interval||interval<60000){interval=300000;}window.setInterval(refreshMarketNews,interval);}
window.addEventListener('load',function(){var params=new URLSearchParams(window.location.search);var openPanel=params.get('open');if(openPanel){openPanelAndJump(openPanel);}if(window.location.pathname==='/ai-recommendations'){window.location.href='/?tab=watchlist';}applySignalFilters();updateMarketNewsStatus();scheduleMarketNewsRefresh();});
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
<title>StockRadar Premium — Research Tools</title>
<meta name="description" content="Preview StockRadar Premium research tools for deeper signal context, watchlist intelligence and portfolio-fit checks.">
<link rel="canonical" href="https://www.stockradarhq.com/upgrade">
<meta property="og:title" content="StockRadar Premium — Research Tools">
<meta property="og:description" content="Preview deeper StockRadar signal context, watchlist intelligence and portfolio-fit research tools.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.stockradarhq.com/upgrade">
<meta property="og:site_name" content="StockRadar">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="StockRadar Premium — Research Tools">
<meta name="twitter:description" content="Preview deeper StockRadar signal context, watchlist intelligence and portfolio-fit research tools.">
<style>
*{box-sizing:border-box;}
:root{--font-hero:clamp(40px,5vw,52px);--font-section:clamp(26px,2.4vw,34px);--font-card-title:18px;--font-body:16px;--font-small:13px;--font-kicker:11px;--font-cta:14px;}
body{background:radial-gradient(circle at 18% 8%,rgba(0,255,170,0.18),transparent 30%),radial-gradient(circle at 86% 12%,rgba(255,184,107,0.14),transparent 28%),linear-gradient(135deg,#050505,#111827);color:white;font-family:Arial,sans-serif;margin:0;min-height:100vh;padding:54px;}
.wrap{max-width:1100px;margin:0 auto;}
.back{color:#38bdf8;text-decoration:none;font-weight:900;display:inline-block;margin-bottom:24px;}
.hero{display:grid;grid-template-columns:1.15fr 0.85fr;gap:24px;align-items:stretch;}
.card{background:linear-gradient(180deg,rgba(23,23,23,0.96),rgba(14,14,14,0.96));border:1px solid rgba(255,255,255,0.11);border-radius:30px;padding:34px;box-shadow:0 30px 85px rgba(0,0,0,0.45),inset 0 1px 0 rgba(255,255,255,0.07);}
.badge{display:inline-block;color:#00ffaa;background:rgba(0,255,170,0.10);border:1px solid rgba(0,255,170,0.22);padding:9px 13px;border-radius:999px;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;font-size:var(--font-kicker);}
h1{font-size:var(--font-hero);line-height:1.04;letter-spacing:0;margin:14px 0 16px 0;background:linear-gradient(135deg,#ffffff,#00ffaa,#ffb86b);-webkit-background-clip:text;color:transparent;}
h2{font-size:var(--font-section);line-height:1.16;margin:0 0 12px 0;}
p{color:#cbd5e1;line-height:1.68;font-size:var(--font-body);}
.feature{display:flex;gap:12px;align-items:flex-start;margin:15px 0;color:#e5e7eb;line-height:1.55;}
.tick{color:#00ffaa;font-weight:950;}
.price{font-size:50px;font-weight:950;letter-spacing:0;margin:10px 0;color:white;}
.price span{font-size:17px;color:#94a3b8;letter-spacing:0;}
.pay-box{background:rgba(5,5,5,0.52);border:1px solid rgba(255,255,255,0.13);border-radius:24px;padding:24px;margin-top:20px;}
.fake-input{width:100%;background:#020617;border:1px solid rgba(255,255,255,0.14);border-radius:16px;padding:15px;color:#94a3b8;margin-bottom:12px;font-weight:800;}
.button{display:inline-flex;align-items:center;justify-content:center;white-space:nowrap;text-align:center;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#050505;padding:15px 20px;border-radius:16px;text-decoration:none;font-weight:950;font-size:var(--font-cta);margin-top:12px;margin-right:10px;box-shadow:0 22px 60px rgba(0,255,170,0.20);line-height:1.1;}
.button.secondary{background:rgba(255,255,255,0.08);color:white;border:1px solid rgba(255,255,255,0.13);box-shadow:none;}
.note{font-size:13px;color:#94a3b8;margin-top:14px;line-height:1.55;}
.trust-points{display:grid;gap:10px;margin-top:16px;}
.trust-point{padding:12px 13px;border-radius:14px;background:rgba(148,163,184,0.07);border:1px solid rgba(148,163,184,0.12);color:#b9c5d2;font-size:13px;line-height:1.5;}
.trust-point strong{display:block;color:#e5edf5;margin-bottom:3px;}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:24px;}
.mini{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:18px;color:#e5e7eb;line-height:1.55;}
.mini strong{display:block;color:white;margin-bottom:6px;font-size:var(--font-card-title);line-height:1.25;}
.difference-card{margin-top:24px;background:linear-gradient(135deg,rgba(9,36,42,0.95),rgba(22,24,35,0.94));border-color:rgba(56,189,248,0.22);}
.difference-lead{max-width:820px;color:#dbeafe;font-size:18px;line-height:1.6;}
.brief-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-top:18px;}
.brief-card{background:rgba(7,17,28,0.66);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:16px;line-height:1.55;}
.brief-card small{display:block;color:#fbbf24;font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:7px;}
.brief-card strong{display:block;color:#f8fafc;font-size:16px;line-height:1.25;margin-bottom:5px;}
.brief-card span{display:block;color:#aebdca;font-size:13px;line-height:1.45;}
.active-card{background:linear-gradient(135deg,rgba(0,255,170,0.16),rgba(56,189,248,0.10));border-color:rgba(0,255,170,0.24);}
.future-card{margin-top:24px;background:linear-gradient(135deg,rgba(245,158,11,0.10),rgba(15,23,42,0.72));border-color:rgba(245,158,11,0.24);}
.future-card .future-label{color:#fbbf24;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;font-size:var(--font-kicker);margin:0 0 10px;}
.future-card h2{color:#f8fafc;}
@media(max-width:850px){:root{--font-hero:clamp(32px,9vw,38px);--font-section:clamp(24px,6vw,28px);}body{padding:24px 16px;}.hero,.grid,.brief-grid{grid-template-columns:1fr;}.card{padding:24px 20px;border-radius:24px;}h1{font-size:var(--font-hero);}.button{width:100%;margin-right:0;}.price{font-size:46px;}}
</style>
</head>
<body>
<div class="wrap">
    <a class="back" href="/">← Back to Dashboard</a>

    {% if has_premium_access %}
    <div class="card active-card">
        <span class="badge">Premium active</span>
<h1><span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:999px;background:#45e6a8;color:#071018;font-weight:950;font-size:13px;margin-right:7px;vertical-align:middle;" aria-hidden="true">✓</span> Premium is already active.</h1>
        <p>Premium access is active for this session. You do not need to purchase again. Premium decision panels, watchlist intelligence and portfolio-fit checks are unlocked.</p>
        <a class="button" href="/stock/AAPL">Open Premium Stock Page</a>
        <a class="button secondary" href="/">Return to Dashboard</a>
    </div>
    {% else %}
    <div class="hero">
        <div class="card">
            <span class="badge">StockRadar Premium</span>
            <h1>Understand the signal before you act.</h1>
            <p><strong>Trading apps show you the market. StockRadar Premium helps you understand what the signal is trying to tell you.</strong></p>
            <p>Free tells you the signal. Premium explains why it matters, what risk to check, where the stock may fit and what to research next.</p>
            <div class="feature"><span class="tick">✓</span><span><strong>Why this signal?</strong> Read the reasoning behind the headline BUY, HOLD or SELL research prompt.</span></div>
            <div class="feature"><span class="tick">✓</span><span><strong>What could go wrong?</strong> Check risk level, concentration warning and caution notes before adding exposure.</span></div>
            <div class="feature"><span class="tick">✓</span><span><strong>What deserves attention?</strong> Use Premium Watchlist to sort strongest setups, caution names and portfolio buckets.</span></div>
        </div>
        <div class="card">
            <span class="badge">{% if premium_payments_enabled %}Premium plan{% else %}Premium preview{% endif %}</span>
            <div class="price">£5 <span>/ month</span></div>
            <p class="note"><strong style="color:#cbd5e1;">£5/month. Cancel anytime.</strong> Cancellation stops future billing, with access continuing until the end of the current billing period.</p>
            {% if premium_payments_enabled %}
            <p>One monthly subscription unlocks the Premium research toolkit. It is designed to help you ask better questions, not to tell you what to buy or sell.</p>
            <div class="note" style="padding:12px;border-radius:14px;background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.16);color:#bae6fd;"><strong>Controlled early access:</strong> Checkout is explicitly enabled for the current environment.</div>
            <p class="note">£5/month early access premium subscription. Cancellation requests are handled through <a href="/manage-subscription">Manage Subscription</a> while self-service billing is being built.</p>
            {% else %}
            <p>Premium subscriptions are not open yet. This page previews the planned £5/month research toolkit while StockRadar completes payment readiness checks.</p>
            <div class="note" style="padding:12px;border-radius:14px;background:rgba(245,158,11,0.09);border:1px solid rgba(245,158,11,0.20);color:#fde68a;"><strong>Soft launch:</strong> No payment can be started from this environment unless checkout is explicitly enabled.</div>
            {% endif %}
            <p class="note"><strong style="color:#cbd5e1;">Educational only.</strong> Premium provides research tools and analysis, not financial advice, personalised investment recommendations or return promises.</p>
            <div class="pay-box">
                <p class="note">Premium access provides research tools and analysis only. StockRadar is not financial advice.</p>
                {% if premium_payments_enabled %}
                <form method="POST" action="/create-checkout-session">
                    <button class="button" type="submit" style="border:none;cursor:pointer;width:100%;">Start Premium with Stripe Checkout</button>
                </form>
                <div class="trust-points" aria-label="Payment and subscription trust notes">
                    <div class="trust-point"><strong>Secure Stripe checkout</strong>Payments are handled by Stripe. StockRadar does not store your full card details.</div>
                    <div class="trust-point"><strong>Cancel anytime</strong>Premium is £5/month. Cancellation stops future billing, with access continuing until the end of the current billing period.</div>
                    <div class="trust-point"><strong>Email-linked access</strong>Your Premium access is linked to the email used at checkout. For help, use <a href="/contact">Contact</a>.</div>
                </div>
                <div class="note">Need to cancel later? Visit <a href="/manage-subscription">Manage Subscription</a>. Early access cancellations are handled through support until self-service billing management is added.</div>
                {% else %}
                <a class="button secondary" href="/feedback" style="width:100%;margin-right:0;">Join the testing feedback loop</a>
                <div class="note">Checkout remains disabled during soft launch. No payment details are collected on this page.</div>
                <div class="trust-points" aria-label="Premium trust notes">
                    <div class="trust-point"><strong>Secure Stripe checkout</strong>When Premium checkout is available, payment is handled by Stripe and StockRadar does not store full card details.</div>
                    <div class="trust-point"><strong>Simple pricing</strong>Premium is planned at £5/month with cancel-anytime support.</div>
                    <div class="trust-point"><strong>Email-linked access</strong>Premium access is linked to the email used at checkout. For help, use <a href="/contact">Contact</a>.</div>
                    <div class="trust-point"><strong>Educational only</strong>Signals are research prompts, not instructions, guarantees or personalised financial advice.</div>
                </div>
                {% endif %}
                <div class="note"><a href="/feedback">Send Feedback</a> about the upgrade experience while StockRadar is in early access.</div>
            </div>
        </div>
    </div>
    <div class="card difference-card">
        <span class="badge">Why Premium is different</span>
        <h2 style="margin-top:16px;">A decision-support and education layer — not another broker screen.</h2>
        <p class="difference-lead">Standard trading apps help you view prices and place trades. StockRadar Premium helps you slow down, understand the signal and decide what deserves further research without information overload.</p>
        <div class="grid">
            <div class="mini"><strong>Plain-English signal reasoning</strong>Understand why the current prompt is showing.</div>
            <div class="mini"><strong>Risk read before you act</strong>See what could weaken the research case.</div>
            <div class="mini"><strong>Portfolio-fit context</strong>Check role and possible duplicate exposure.</div>
            <div class="mini"><strong>Watch-next trigger</strong>Know which evidence deserves another look.</div>
            <div class="mini"><strong>Beginner mistake to avoid</strong>Spot the common trap linked to the signal.</div>
            <div class="mini"><strong>Caution zone</strong>Keep weaker setups visible, not hidden by optimism.</div>
            <div class="mini"><strong>Compare stocks with context</strong>Review two choices without declaring a guaranteed winner.</div>
            <div class="mini"><strong>No information overload</strong>Get the simple answer first, with detail only where useful.</div>
        </div>
        <p class="note"><strong style="color:#cbd5e1;">£5/month. Cancel anytime.</strong> Educational decision support only — not financial advice, trade execution or a promise of returns.</p>
    </div>
    <div class="card" style="margin-top:24px;background:linear-gradient(135deg,rgba(14,44,50,0.92),rgba(31,34,45,0.86));border-color:rgba(74,222,163,0.20);">
        <span class="badge">Premium Decision Brief</span>
        <h2>What would Premium help you review today?</h2>
        <p>A compact decision-support view that prioritises what deserves research without exposing the live Premium answers on this free preview.</p>
        <div class="brief-grid">
            <div class="brief-card"><small>Strongest setup</small><strong>Premium answer locked</strong><span>See which current signal deserves research first and why.</span></div>
            <div class="brief-card"><small>Caution zone</small><strong>Premium answer locked</strong><span>Review the main risk prompt before acting.</span></div>
            <div class="brief-card"><small>Portfolio context</small><strong>Premium answer locked</strong><span>Check role, concentration and possible overlap.</span></div>
            <div class="brief-card"><small>Beginner mistake</small><strong>Premium answer locked</strong><span>Spot the common trap before treating a signal as a conclusion.</span></div>
            <div class="brief-card"><small>Watch next</small><strong>Premium answer locked</strong><span>Define the evidence that should trigger another review.</span></div>
        </div>
        <p class="note">Educational only. Premium does not provide personal financial advice or tell you what to trade.</p>
    </div>
    {% endif %}
    {% if not has_premium_access %}
    <div class="card" style="margin-top:24px;background:linear-gradient(135deg,rgba(56,189,248,0.10),rgba(15,23,42,0.78));border-color:rgba(56,189,248,0.22);">
        <span class="badge">Free first step</span>
        <h2>Not ready for Premium yet?</h2>
        <p>Join StockRadar Weekly free and follow the market signal before upgrading.</p>
        <p class="note">Start with the free weekly signal. Upgrade when you want risk read, portfolio fit and full AI reasoning.</p>
        <a class="button secondary" href="/newsletter">Join Free</a>
    </div>
    {% endif %}
    <div class="card future-card">
        <p class="future-label">Coming later · Future Premium research feature · Not live yet</p>
        <h2>Coming later: Dividend Dip Tracker</h2>
        <p>Dividend/distribution snapshots are already live on stock detail pages where data is available.</p>
        <p>Dividend Dip Tracker is still planned as a future scanner for dividend-related watchlist moves, ex-dividend effects, income dips and possible yield-trap risks.</p>
        <p>This future tracker will be a research prompt tool only. It will not be financial advice, a buy signal, or a recommendation to trade around dividends.</p>
        <p class="note"><strong style="color:#fde68a;">Future Premium research feature · Not live yet · Not financial advice</strong></p>
    </div>
    {{ disclaimer_footer() | safe }}
</div>
</body>
</html>
"""


owner_html = """
<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Owner Area</title><style>body{background:#020617;color:white;font-family:Arial;margin:0;padding:60px;}.card{background:#0f172a;padding:40px;border-radius:24px;max-width:820px;margin:auto;border:1px solid rgba(255,255,255,0.08);}a{color:#38bdf8;font-weight:bold;}</style></head><body><div class="card"><h1>👑 Owner Area</h1><p>You are logged in as the owner with premium access.</p><p>This confirms login and premium unlocking are working.</p><p><a href="/">Return to Dashboard</a></p><p><a href="/admin/newsletter-preview">Generate Newsletter Draft</a></p><p><a href="/admin/newsletter/beehiiv-copy">Copy Newsletter for Beehiiv</a></p><p><a href="/stock/AAPL">Open Premium {{ stock_display_label('AAPL') }} Page</a></p>{{ disclaimer_footer() | safe }}</div></body></html>
"""


stock_detail_html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ stock_display_label(symbol) }} Stock Detail</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
	<style>
	.stock-premium-report{margin-bottom:22px;}
	.stock-today-context{margin-bottom:18px;padding:24px;border-radius:24px;background:linear-gradient(145deg,rgba(14,45,48,0.98),rgba(12,26,39,0.98));border:1px solid rgba(74,222,163,0.27);box-shadow:0 20px 56px rgba(0,0,0,0.28);}
	.stock-today-context h2{margin:0 0 7px;color:#f8fafc;font-size:clamp(27px,3vw,34px);line-height:1.15;}
	.stock-today-intro{margin:0;color:#b9cbd7;}
	.stock-today-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin-top:17px;}
	.stock-today-item{min-width:0;padding:16px;border-radius:17px;background:rgba(7,17,28,0.52);border:1px solid rgba(148,163,184,0.14);}
	.stock-today-item:last-child{grid-column:1/-1;}
	.stock-today-item strong{display:block;color:#f8fafc;font-size:18px;line-height:1.3;overflow-wrap:anywhere;}
	.stock-today-item p{margin:7px 0 0;color:#b8c7d3;font-size:14px;line-height:1.55;overflow-wrap:anywhere;}
	.stock-today-note{margin:14px 0 0;color:#99acbb;font-size:12px;line-height:1.5;}
	.stock-premium-summary{background:linear-gradient(135deg,rgba(12,47,48,0.96),rgba(25,38,50,0.98) 62%,rgba(75,53,27,0.76));border:1px solid rgba(74,222,163,0.30);border-radius:28px;padding:34px;box-shadow:0 26px 72px rgba(0,0,0,0.34);}
	.stock-premium-summary h2{font-size:clamp(29px,3.5vw,40px);margin:0 0 10px;}
	.stock-premium-summary .premium-identity{max-width:820px;margin:0 0 18px;}
	.stock-premium-badges{display:flex;flex-wrap:wrap;gap:9px;margin:18px 0;}
	.stock-premium-badge{display:inline-flex;align-items:center;min-height:34px;padding:7px 11px;border:1px solid rgba(148,163,184,0.24);border-radius:999px;background:rgba(7,17,28,0.62);color:#e5edf5;font-size:12px;font-weight:950;text-transform:uppercase;letter-spacing:0.07em;}
	.stock-premium-badge.buy{border-color:rgba(74,222,128,0.34);color:#bbf7d0;}.stock-premium-badge.hold,.stock-premium-badge.watch{border-color:rgba(240,195,106,0.38);color:#fde68a;}.stock-premium-badge.sell{border-color:rgba(251,113,133,0.32);color:#fecdd3;}
	.stock-premium-readiness{display:block;color:#fff;font-size:clamp(26px,3vw,36px);font-weight:950;line-height:1.15;margin-bottom:9px;}
	.stock-premium-action{margin:0;max-width:900px;color:#e7eef4;font-size:18px;font-weight:800;line-height:1.55;}
	.stock-premium-disclaimer{margin:17px 0 0;color:#9fb0bf;font-size:13px;line-height:1.55;}
	.stock-decision-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;margin:18px 0;}
	.stock-decision-card{min-width:0;padding:21px;border:1px solid rgba(148,163,184,0.16);border-radius:21px;background:rgba(14,25,38,0.95);}
	.stock-premium-label{display:block;margin-bottom:8px;color:#86efac;font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;line-height:1.35;}
	.stock-decision-answer{display:block;color:#f8fafc;font-size:20px;font-weight:950;line-height:1.3;overflow-wrap:anywhere;}
	.stock-decision-support{display:block;margin-top:9px;color:#aebdca;font-size:14px;line-height:1.55;overflow-wrap:anywhere;}.stock-decision-support strong{color:#dce8f1;}
	.stock-decision-reminder{display:block;margin-top:11px;color:#93a6b7;font-size:12px;line-height:1.5;}
	.stock-premium-callout{border-radius:22px;padding:22px;margin:16px 0;border:1px solid rgba(240,195,106,0.30);background:linear-gradient(145deg,rgba(106,76,24,0.34),rgba(14,25,38,0.96));}
	.stock-premium-callout h3,.stock-investor-lesson h3,.stock-learning-card h3,.stock-supporting-detail h3{margin:0 0 9px;color:#f8fafc;font-size:23px;line-height:1.2;}
	.stock-premium-callout p,.stock-investor-lesson p{margin:0;}
	.stock-premium-callout .stock-premium-label{color:#f6d88a;}
	.stock-investor-lesson{border-radius:22px;padding:22px;margin:16px 0;border:1px solid rgba(74,222,163,0.20);background:rgba(74,222,163,0.07);}
	.stock-investor-lesson p{color:#d9eee6;font-size:16px;}
	.stock-learning-card{border-radius:22px;padding:22px;margin:16px 0;border:1px solid rgba(105,201,242,0.22);background:linear-gradient(145deg,rgba(21,42,55,0.96),rgba(13,27,40,0.98));}
	.stock-learning-card>p{margin:0;}.stock-learning-subtitle{color:#c5d7e3;}
	.stock-learning-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px;}
	.stock-learning-part{min-width:0;padding:16px;border-radius:16px;background:rgba(7,17,28,0.52);border:1px solid rgba(148,163,184,0.14);}
	.stock-learning-part span{display:block;margin-bottom:7px;color:#86d8f5;font-size:10px;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;}
	.stock-learning-part p{margin:0;color:#d7e2ea;font-size:14px;line-height:1.58;}
	.stock-learning-goal{margin-top:16px!important;color:#9fb0bf!important;font-size:13px;line-height:1.55;}
	.stock-portfolio-builder{border-radius:24px;padding:24px;margin:16px 0;background:linear-gradient(145deg,rgba(19,37,48,0.97),rgba(11,24,36,0.98));border:1px solid rgba(74,222,163,0.20);}
	.stock-portfolio-builder>h3{margin:0 0 8px;color:#f8fafc;font-size:24px;line-height:1.2;}
	.stock-portfolio-builder>p{margin:0;color:#b9cbd7;}
	.stock-portfolio-role{margin-top:18px;padding:19px;border-radius:18px;background:rgba(7,17,28,0.52);border:1px solid rgba(74,222,163,0.17);}
	.stock-portfolio-role h4,.stock-portfolio-card h4,.stock-portfolio-checklist h4,.stock-portfolio-principle h4{margin:0 0 8px;color:#f1f5f9;font-size:17px;line-height:1.3;}
	.stock-portfolio-role strong{display:block;color:#d1fae5;font-size:20px;line-height:1.3;margin-bottom:8px;overflow-wrap:anywhere;}
	.stock-portfolio-role p,.stock-portfolio-card p,.stock-portfolio-principle p{margin:7px 0 0;color:#b8c7d3;font-size:14px;line-height:1.58;}
	.stock-portfolio-role .stock-portfolio-caution{color:#f5d99a;}
	.stock-portfolio-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px;}
	.stock-portfolio-card{min-width:0;padding:17px;border-radius:17px;background:rgba(7,17,28,0.44);border:1px solid rgba(148,163,184,0.14);}
	.stock-portfolio-card strong{display:block;color:#e8f2f7;line-height:1.45;overflow-wrap:anywhere;}
	.stock-portfolio-card.caution{border-color:rgba(240,195,106,0.23);background:rgba(91,65,26,0.18);}
	.stock-position-question{padding:10px 12px;border-radius:12px;background:rgba(105,201,242,0.08);color:#ccebf7!important;font-weight:800;}
	.stock-portfolio-checklist{margin-top:12px;padding:18px;border-radius:18px;background:rgba(7,17,28,0.44);border:1px solid rgba(148,163,184,0.14);}
	.stock-portfolio-checklist ul{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px 26px;margin:10px 0 0;padding-left:21px;}
	.stock-portfolio-checklist li{color:#b8c7d3;line-height:1.5;padding-left:2px;}
	.stock-portfolio-principle{margin-top:12px;padding:17px 18px;border-radius:17px;background:rgba(74,222,163,0.08);border-left:4px solid rgba(74,222,163,0.62);}
	.stock-portfolio-principle p{color:#dcfce7;font-weight:850;font-size:15px;}
	.stock-portfolio-note{margin-top:14px!important;color:#93a6b7!important;font-size:12px!important;line-height:1.55!important;}
	.stock-psychology{border-radius:24px;padding:24px;margin:16px 0;background:linear-gradient(145deg,rgba(29,35,53,0.97),rgba(12,24,37,0.98));border:1px solid rgba(105,201,242,0.20);}
	.stock-psychology>h3{margin:0 0 8px;color:#f8fafc;font-size:24px;line-height:1.2;}
	.stock-psychology>p{margin:0;color:#b9cbd7;}
	.stock-psychology-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:18px;}
	.stock-psychology-card{min-width:0;padding:17px;border-radius:17px;background:rgba(7,17,28,0.48);border:1px solid rgba(148,163,184,0.14);}
	.stock-psychology-card h4{margin:0 0 9px;color:#f1f5f9;font-size:17px;line-height:1.3;}
	.stock-psychology-prompt{margin:0;color:#dcebf3;font-weight:850;line-height:1.55;}
	.stock-psychology-support{margin:9px 0 0;color:#aebdca;font-size:14px;line-height:1.55;}
	.stock-business-education{margin:16px 0;border-radius:24px;background:linear-gradient(145deg,rgba(20,38,49,0.97),rgba(10,22,34,0.98));border:1px solid rgba(105,201,242,0.21);overflow:hidden;}
	.stock-business-education>summary{display:flex;align-items:center;min-height:62px;padding:18px 22px;color:#f8fafc;font-size:23px;font-weight:950;cursor:pointer;line-height:1.2;}
	.stock-business-education>summary:focus-visible{outline:3px solid rgba(105,201,242,0.72);outline-offset:-3px;}
	.stock-business-body{padding:0 22px 22px;}
	.stock-business-basis{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
	.stock-business-basis span{display:inline-flex;padding:7px 10px;border-radius:999px;background:rgba(105,201,242,0.08);border:1px solid rgba(105,201,242,0.17);color:#ccebf7;font-size:11px;font-weight:900;line-height:1.2;}
	.stock-business-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;}
	.stock-business-item{min-width:0;padding:16px;border-radius:16px;background:rgba(7,17,28,0.47);border:1px solid rgba(148,163,184,0.14);}
	.stock-business-item h4{margin:0 0 7px;color:#f1f5f9;font-size:15px;line-height:1.35;}
	.stock-business-item p{margin:0;color:#b8c7d3;font-size:14px;line-height:1.55;overflow-wrap:anywhere;}
	.stock-supporting-detail{border-radius:24px;padding:24px;margin-top:16px;background:rgba(10,21,33,0.92);border:1px solid rgba(148,163,184,0.16);}
	.stock-supporting-detail>p{margin:0 0 15px;}
	.stock-supporting-detail details{background:rgba(7,17,28,0.58);border:1px solid rgba(148,163,184,0.14);border-radius:17px;margin-top:10px;overflow:hidden;}
	.stock-supporting-detail summary{display:flex;align-items:center;min-height:48px;padding:13px 16px;color:#eef5fa;font-weight:900;cursor:pointer;line-height:1.35;}
	.stock-supporting-detail summary:focus-visible{outline:3px solid rgba(105,201,242,0.72);outline-offset:-3px;}
	.stock-detail-body{padding:0 16px 16px;}.stock-detail-body p:last-child{margin-bottom:0;}
	.stock-detail-body ul{padding-left:21px;}.stock-detail-body li{color:#aebdca;line-height:1.6;margin-bottom:8px;}
	.stock-score-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;}
	.stock-score-item{padding:14px;border-radius:14px;background:rgba(148,163,184,0.06);color:#aebdca;line-height:1.55;}.stock-score-item strong{display:block;color:#e9f1f7;margin-bottom:4px;}
	.stock-identity-note{margin:16px 0 0;color:#9fb0bf;font-size:13px;line-height:1.55;}
	.card.stock-fundamentals{padding:20px 22px;border-color:rgba(105,201,242,0.16);background:linear-gradient(180deg,rgba(14,27,40,0.96),rgba(10,21,33,0.96));}
	.stock-fundamentals .kicker{margin-bottom:5px;color:#86d8f5;}
	.stock-fundamentals h2{margin:0 0 12px;font-size:20px;}
	.stock-fundamentals-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;}
	.stock-fundamental-metric{min-width:0;padding:10px 12px;border-radius:14px;background:rgba(7,17,28,0.48);border:1px solid rgba(148,163,184,0.13);}
	.stock-fundamental-metric span{display:block;margin-bottom:4px;color:#91a3b4;font-size:10px;font-weight:950;letter-spacing:0.08em;line-height:1.3;text-transform:uppercase;}
	.stock-fundamental-metric strong{display:block;color:#edf5fa;font-size:17px;line-height:1.2;overflow-wrap:anywhere;}
	@media(max-width:900px){.stock-premium-summary{padding:24px 20px;border-radius:24px;}.stock-decision-grid{grid-template-columns:repeat(2,minmax(0,1fr));}.stock-decision-card:last-child{grid-column:1/-1;}.stock-score-grid,.stock-learning-grid{grid-template-columns:1fr;}.stock-portfolio-checklist ul{grid-template-columns:1fr;}}
	@media(max-width:640px){.stock-today-grid,.stock-decision-grid,.stock-portfolio-grid,.stock-psychology-grid,.stock-business-grid{grid-template-columns:1fr;}.stock-today-item:last-child,.stock-decision-card:last-child{grid-column:auto;}.stock-premium-action{font-size:16px;}.stock-premium-badge{font-size:11px;}.stock-today-context,.stock-portfolio-builder,.stock-psychology,.stock-supporting-detail{padding:20px 16px;}.stock-business-education>summary{padding:17px 16px;font-size:21px;}.stock-business-body{padding:0 16px 18px;}.stock-detail-body .payment-button{width:100%;text-align:center;}.card.stock-fundamentals{padding:16px 14px;}.stock-fundamentals-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;}.stock-fundamental-metric{padding:9px 10px;}.stock-fundamental-metric strong{font-size:15px;}}
	</style>
	<style>
		*{box-sizing:border-box;}:root{--font-hero:clamp(34px,4vw,46px);--font-section:clamp(24px,2.4vw,30px);--font-card-title:18px;--font-body:15px;--font-small:13px;--font-kicker:11px;--font-cta:14px;}body{background:radial-gradient(circle at 12% 6%,rgba(0,255,170,0.11),transparent 30%),linear-gradient(135deg,#08111c,#101827);color:#dbe4ee;font-family:Arial,sans-serif;margin:0;min-height:100vh;padding:48px;}.card{background:linear-gradient(180deg,rgba(18,29,42,0.97),rgba(12,22,33,0.97));padding:30px;border-radius:28px;margin-bottom:22px;border:1px solid rgba(148,163,184,0.16);box-shadow:0 22px 65px rgba(0,0,0,0.30);}h1,h2{color:#f1f5f9;line-height:1.12;letter-spacing:0;}h1{font-size:var(--font-hero);}h2{font-size:var(--font-section);}p{color:#b9c5d2;line-height:1.68;font-size:var(--font-body);}a{color:#69c9f2;text-decoration:none;font-weight:bold;}.kicker{color:#4adea3;font-weight:950;text-transform:uppercase;letter-spacing:.1em;font-size:var(--font-kicker);margin:0 0 8px;}.muted{color:#91a3b4;font-size:13px;}.range-row{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0;}.range-button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:12px 16px;border-radius:15px;background:#111d2b;color:#dbe4ee;text-decoration:none;border:1px solid rgba(148,163,184,0.14);font-weight:800;line-height:1.1;text-align:center;}.range-button.active{background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;}.metric-grid,.ai-grid,.example-report-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:22px;}.metric-grid{grid-template-columns:repeat(4,1fr);}.ai-card,.metric,.example-report-card{background:rgba(14,25,38,0.90);border:1px solid rgba(148,163,184,0.15);border-radius:22px;padding:23px;}.ai-card.warning{background:linear-gradient(145deg,rgba(89,70,28,0.35),rgba(14,25,38,0.94));}.ai-card.risk{background:linear-gradient(145deg,rgba(24,60,78,0.32),rgba(14,25,38,0.94));}.premium-banner,.example-report{background:linear-gradient(135deg,rgba(15,55,50,0.74),rgba(55,42,26,0.60),rgba(20,45,61,0.62));border:1px solid rgba(74,222,163,0.20);border-radius:28px;padding:30px;margin-bottom:22px;}.premium-banner{display:grid;grid-template-columns:1.35fr 0.85fr;gap:24px;align-items:center;box-shadow:0 26px 70px rgba(0,0,0,0.34);}.stock-locked-preview{border-color:rgba(255,184,107,0.34);background:linear-gradient(135deg,rgba(12,47,48,0.92),rgba(81,54,28,0.62),rgba(20,45,61,0.78));}.premium-banner small,.example-report small{display:block;color:#86efac;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;font-size:var(--font-kicker);margin-bottom:8px;}.premium-cta-box{background:rgba(9,18,28,0.84);border:1px solid rgba(255,184,107,0.24);border-radius:22px;padding:22px;text-align:center;}.premium-cta-box strong{display:block;color:#f8fafc;margin-bottom:8px;}.payment-button{display:inline-flex;align-items:center;justify-content:center;white-space:nowrap;background:linear-gradient(135deg,#45e6a8,#f0c36a);color:#071018;border-radius:16px;padding:13px 19px;font-size:var(--font-cta);font-weight:950;text-decoration:none;line-height:1.1;}.payment-note{color:#a8b6c6;font-size:13px;margin-top:12px;line-height:1.55;}.signal-badge,.free-strength,.strength-pill{display:inline-block;margin-top:10px;padding:8px 12px;border-radius:999px;background:rgba(148,163,184,0.09);font-weight:900;font-size:12px;text-transform:uppercase;}.confidence-large,.confidence-score{font-size:36px;font-weight:950;}.free-meter,.confidence-meter{font-size:24px;letter-spacing:0;color:#4adea3;font-weight:950;margin:8px 0;}.dividend-card{border:1px solid rgba(74,222,163,0.18);}.dividend-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0;}.dividend-metric{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.10);border-radius:16px;padding:14px;line-height:1.45;}.dividend-metric span{display:block;color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:0.07em;font-weight:900;margin-bottom:6px;}.dividend-metric strong{display:block;color:#e5f4ff;font-size:17px;}.dividend-empty{background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.20);border-radius:16px;padding:14px;color:#fde68a;line-height:1.65;}.dividend-note{color:#cbd5e1;background:rgba(148,163,184,0.07);border-radius:14px;padding:12px 14px;}.dividend-risk{color:#fecaca;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.16);border-radius:14px;padding:12px 14px;}.chart-card{padding:24px;}.chart-shell{position:relative;width:100%;height:360px;min-height:360px;background:#0a1420;border-radius:18px;padding:16px;overflow:hidden;}.chart-shell canvas{display:block;width:100%!important;height:100%!important;background:transparent;border-radius:12px;padding:0;}.buy{color:#4ade80;font-weight:bold;}.sell{color:#fb7185;font-weight:bold;}.hold{color:#f4c95d;font-weight:bold;}@media(max-width:900px){:root{--font-hero:clamp(32px,9vw,38px);--font-section:clamp(23px,6vw,28px);}body{padding:24px 16px;}.card,.premium-banner,.example-report{padding:24px 20px;border-radius:24px;}.metric-grid,.ai-grid,.premium-banner,.example-report-grid,.dividend-grid{grid-template-columns:1fr;}.payment-button{display:block;text-align:center;}.range-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:18px 0;}.range-button{width:100%;padding:13px 10px;font-size:14px;}.metric{padding:18px;}.metric h2{font-size:24px;line-height:1.12;overflow-wrap:anywhere;}.chart-card{padding:18px 14px;}.chart-shell{height:340px;min-height:340px;padding:12px;border-radius:16px;}}
		.example-report{padding:32px;}.example-report h2{margin:0 0 12px;}.example-report>p{max-width:980px;margin:0 0 22px;}.example-report-grid{align-items:stretch;margin-top:20px;}.example-report-card{display:flex;flex-direction:column;gap:8px;padding:24px;line-height:1.55;}.premium-card-label{display:block;color:#86efac;font-size:12px;font-weight:950;letter-spacing:0.11em;line-height:1.35;text-transform:uppercase;}.premium-card-value{display:block;color:#f8fafc;font-size:20px;font-weight:950;line-height:1.25;overflow-wrap:anywhere;}.premium-card-support{display:block;color:#a8b6c6;font-size:14px;line-height:1.58;margin-top:2px;}.premium-decision-use{margin-top:16px;}.premium-decision-use .premium-card-value{font-size:18px;line-height:1.45;}.example-report-card .confidence-score{line-height:1.05;margin-top:2px;}.example-report-card .confidence-meter{margin:4px 0 2px;}.example-report-actions{margin-top:18px;}.premium-preview-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:18px;}.premium-preview-item{padding:14px;border-radius:15px;background:rgba(7,17,28,0.58);border:1px solid rgba(255,255,255,0.09);line-height:1.45;}.premium-preview-item strong{display:block;color:#f8fafc;font-size:14px;margin-bottom:4px;}.premium-preview-item span{display:block;color:#aebdca;font-size:13px;}@media(max-width:900px){.example-report{padding:24px 20px;}.example-report-card{padding:20px;gap:7px;}.premium-card-value{font-size:18px;}.premium-decision-use .premium-card-value{font-size:17px;}.example-report-actions .payment-button{width:100%;text-align:center;}.premium-preview-grid{grid-template-columns:1fr;}}
		.range-row{align-items:center;gap:8px;margin:18px 0 14px;padding:7px;background:rgba(8,19,31,0.74);border:1px solid rgba(148,163,184,0.14);border-radius:18px;box-shadow:inset 0 1px 0 rgba(255,255,255,0.03);}.range-button{flex:1 1 104px;min-height:38px;padding:10px 12px;border-radius:12px;background:transparent;border:1px solid transparent;color:#aebdca;font-size:13px;font-weight:900;}.range-button:hover{text-decoration:none;background:rgba(148,163,184,0.08);color:#f8fafc;}.range-button.active{background:rgba(74,222,163,0.16);border-color:rgba(74,222,163,0.28);color:#d1fae5;box-shadow:0 10px 28px rgba(0,255,170,0.08);}.metric-grid{gap:12px;margin-bottom:18px;}.metric{padding:18px;border-radius:18px;background:linear-gradient(180deg,rgba(13,24,37,0.92),rgba(9,18,29,0.92));box-shadow:inset 0 1px 0 rgba(255,255,255,0.03);}.metric small{display:block;color:#8ea0b1;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;font-weight:950;margin-bottom:8px;}.metric h2{margin:0;font-size:clamp(21px,2vw,28px);line-height:1.1;overflow-wrap:anywhere;}.chart-card{padding:20px;border-radius:24px;background:linear-gradient(180deg,rgba(12,24,38,0.98),rgba(7,15,26,0.98));border-color:rgba(148,163,184,0.18);box-shadow:0 18px 48px rgba(0,0,0,0.26);}.chart-card-header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:14px;}.chart-card-header h2{font-size:20px;line-height:1.15;margin:0;color:#f8fafc;}.chart-card-header span{display:block;color:#91a3b4;font-size:13px;line-height:1.45;margin-top:4px;}.chart-range-pill{flex:0 0 auto;border-radius:999px;padding:7px 10px;background:rgba(56,189,248,0.10);border:1px solid rgba(56,189,248,0.18);color:#bae6fd;font-size:11px;font-weight:950;text-transform:uppercase;letter-spacing:0.07em;}.chart-shell{height:330px;min-height:330px;padding:14px;border-radius:18px;background:linear-gradient(180deg,rgba(4,12,24,0.98),rgba(8,20,32,0.96));border:1px solid rgba(148,163,184,0.10);}@media(max-width:900px){.range-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin:16px 0 12px;padding:6px;border-radius:16px;}.range-button{min-height:38px;padding:9px 8px;font-size:12px;border-radius:11px;}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:16px;}.metric{padding:14px;border-radius:16px;}.metric small{font-size:10px;letter-spacing:0.07em;margin-bottom:7px;}.metric h2{font-size:20px;line-height:1.08;}.chart-card{padding:16px 12px;border-radius:22px;}.chart-card-header{align-items:flex-start;flex-direction:column;gap:8px;margin-bottom:12px;}.chart-card-header h2{font-size:18px;}.chart-range-pill{font-size:10px;padding:6px 9px;}.chart-shell{height:300px;min-height:300px;padding:10px;border-radius:15px;}}@media(max-width:390px){.metric-grid{grid-template-columns:1fr;}.chart-shell{height:288px;min-height:288px;}}
	</style>
</head>
<body>
<div class="card"><p><a href="/">← Back to Dashboard</a></p><h1>{{ stock_display_label(symbol) }} Stock Detail</h1><p style="color:#94a3b8;">Live chart view for {{ range_label }}. Use the buttons below to change timeframe.</p></div>

	<div class="ai-grid"><div class="ai-card"><small>{% if has_premium_access %}Current Signal{% else %}Free Signal Preview{% endif %}</small><h2 class="{% if ai_context.signal == 'BUY' %}buy{% elif ai_context.signal == 'SELL' %}sell{% elif ai_context.signal == 'HOLD' %}hold{% endif %}">{{ ai_context.signal }}</h2><p>The headline signal shows what the scanner is flagging for {{ stock_display_label(symbol) }}.</p><span class="signal-badge">Live stock page: {{ stock_display_label(symbol) }}</span></div><div class="ai-card warning"><small>{% if has_premium_access %}Current Confidence{% else %}Free Confidence Preview{% endif %}</small><div class="confidence-large">{{ ai_context.confidence }}</div><div class="free-meter">{{ ai_context.confidence_meter }}</div><span class="free-strength">Signal strength: {{ ai_context.strength_label }}</span><p style="margin-top:12px;">The score and meter are a research prompt. Premium explains how to interpret them, what risk to check and what evidence matters next.</p></div><div class="ai-card risk"><small>{% if has_premium_access %}Premium Active{% else %}Premium Preview{% endif %}</small><h2>Decision context</h2>{% if has_premium_access %}<p>The Premium report below puts the simple answer first, followed by practical checks and optional supporting detail.</p><span class="signal-badge">Premium unlocked</span>{% else %}<p>Premium explains the decision layer behind {{ stock_display_label(symbol) }}: risk level, portfolio role, concentration warning and the next trigger to watch.</p><a class="signal-badge" href="/upgrade">Explore Premium</a>{% endif %}</div></div>

{% set fundamentals = dividend_context.get('fundamentals', []) if dividend_context else [] %}
{% if fundamentals %}
<section class="card stock-fundamentals" aria-labelledby="stock-fundamentals-heading">
    <p class="kicker">Supporting data</p>
    <h2 id="stock-fundamentals-heading">Key fundamentals</h2>
    <div class="stock-fundamentals-grid">
        {% for metric in fundamentals %}
        <div class="stock-fundamental-metric" data-fundamental="{{ metric.key }}">
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
        </div>
        {% endfor %}
    </div>
</section>
{% endif %}

	{% if not has_premium_access %}<div class="premium-banner stock-locked-preview"><div><small>Premium locked preview</small><h2>Free shows the signal. Premium explains the decision.</h2><p>Premium is the calm education layer behind the {{ ai_context.signal }} prompt. It helps you understand the questions that matter before acting without adding another screen of market noise.</p><div class="premium-preview-grid"><div class="premium-preview-item"><strong>Why is this signal showing?</strong><span>Unlock the plain-English reasoning behind the current research prompt.</span></div><div class="premium-preview-item"><strong>What could weaken it?</strong><span>See which risk evidence would make the case less useful.</span></div><div class="premium-preview-item"><strong>Could it duplicate exposure?</strong><span>Check possible sector, ETF, theme or mega-cap overlap.</span></div><div class="premium-preview-item"><strong>Where might it fit?</strong><span>Review core, growth, defensive, income, cyclical or speculative context.</span></div><div class="premium-preview-item"><strong>What should I watch next?</strong><span>Define the next signal, price or business evidence to review.</span></div><div class="premium-preview-item"><strong>What mistake should I avoid?</strong><span>See the beginner trap linked to this type of signal.</span></div></div></div><div class="premium-cta-box"><strong>Unlock the {{ stock_display_label(symbol) }} Decision Panel</strong><p>Includes signal meaning, risk checks, portfolio fit, a beginner-mistake warning, watch-next trigger and Before You Act checklist.</p><a class="payment-button" href="/upgrade">Upgrade to Premium</a><div class="payment-note"><a href="/premium-decision/{{ symbol }}">View the locked decision-panel preview</a></div><div class="payment-note">Helpful research context only. No investment advice or return promises.</div></div></div>{% endif %}

{% if dividend_context %}
<div class="card dividend-card">
    <p class="kicker">Income education</p>
    <h2>{{ "Distribution snapshot" if dividend_context.is_etf else "Dividend snapshot" }}</h2>

    {% if dividend_context.has_dividend_data %}
    <div class="dividend-grid">
        <div class="dividend-metric">
            <span>{{ dividend_context.dividend_label }} yield</span>
            <strong>{{ dividend_context.dividend_yield }}</strong>
        </div>
        <div class="dividend-metric">
            <span>Annual {{ dividend_context.dividend_label | lower }}</span>
            <strong>{{ dividend_context.annual_dividend }}</strong>
        </div>
        <div class="dividend-metric">
            <span>Ex-dividend date</span>
            <strong>{{ dividend_context.ex_dividend_date }}</strong>
        </div>
        {% if not dividend_context.is_etf %}
        <div class="dividend-metric">
            <span>Payout ratio</span>
            <strong>{{ dividend_context.payout_ratio }}</strong>
        </div>
        {% endif %}
    </div>
    {% else %}
    <div class="dividend-empty">
        {{ dividend_context.no_data_message }}
    </div>
    {% endif %}

    <p>{{ dividend_context.beginner_explanation }}</p>
    <p class="dividend-note">{{ dividend_context.dividend_frequency_note }}</p>
    <p class="dividend-risk">{{ dividend_context.risk_note }}</p>
    <p class="muted">{{ dividend_context.source_note }}</p>
</div>
{% endif %}

<div class="card" style="background:linear-gradient(135deg,rgba(0,255,170,0.12),rgba(56,189,248,0.08));border-color:rgba(0,255,170,0.22);"><small style="color:#00ffaa;font-weight:950;text-transform:uppercase;letter-spacing:0.1em;">StockRadar Weekly</small><h2>Get weekly signal highlights</h2><p style="color:#cbd5e1;line-height:1.7;">StockRadar Weekly sends a plain-English market signal recap, including what’s strengthening, what’s weakening and what deserves attention next.</p><a class="payment-button" href="/newsletter">Join StockRadar Weekly</a></div>

	{% if has_premium_access and example_report %}
	<section class="stock-premium-report" aria-labelledby="stock-premium-summary-heading">
	    <section class="stock-today-context" aria-labelledby="stock-today-context-heading">
	        <span class="stock-premium-label">Premium current context</span>
	        <h2 id="stock-today-context-heading">Today’s Context</h2>
	        <p class="stock-today-intro">The current StockRadar research prompt, using the same live context shown across this report.</p>
	        <div class="stock-today-grid">
	            <article class="stock-today-item">
	                <span class="stock-premium-label">Current signal</span>
	                <strong>{{ today_context.signal }}</strong>
	                <p>{{ today_context.plain_english_summary }}</p>
	            </article>
	            <article class="stock-today-item">
	                <span class="stock-premium-label">Confidence</span>
	                <strong>{{ today_context.confidence_label }}</strong>
	                {% if today_context.momentum_view %}<p>{{ today_context.momentum_view }}</p>{% endif %}
	            </article>
	            <article class="stock-today-item">
	                <span class="stock-premium-label">Why this setup is showing</span>
	                <strong>Existing StockRadar reason</strong>
	                <p>{{ today_context.setup_reason }}</p>
	                {% if today_context.news_context %}<p><strong>Relevant news context:</strong> {{ today_context.news_context }}</p>{% endif %}
	            </article>
	            <article class="stock-today-item">
	                <span class="stock-premium-label">Risk today</span>
	                <strong>Current risk view</strong>
	                <p>{{ today_context.risk_today }}</p>
	            </article>
	            <article class="stock-today-item">
	                <span class="stock-premium-label">Watch next</span>
	                <strong>Next research trigger</strong>
	                <p>{{ today_context.watch_next }}</p>
	            </article>
	        </div>
	        <p class="stock-today-note">Today’s Context can change as price action, signals and relevant news update.</p>
	    </section>

	    <div class="stock-premium-summary">
	        <span class="stock-premium-label">Premium decision summary</span>
	        <h2 id="stock-premium-summary-heading">{{ stock_display_label(symbol) }}</h2>
	        <p class="premium-identity">Each Premium report is designed to help you understand the decision process, not simply copy a signal.</p>
	        <div class="stock-premium-badges" aria-label="Current signal and confidence">
	            <span class="stock-premium-badge {{ ai_context.signal|lower }}">Signal: {{ ai_context.signal }}</span>
	            <span class="stock-premium-badge">Confidence: {{ example_report.confidence }}</span>
	        </div>
	        <strong class="stock-premium-readiness">{{ example_report.readiness }}</strong>
	        <p class="stock-premium-action">{{ example_report.action_frame }}</p>
	        <p class="stock-premium-disclaimer">Educational decision support only. This is a research prompt, not a personalised recommendation.</p>
	    </div>

	    <div class="stock-decision-grid" aria-label="Practical decision points">
	        <article class="stock-decision-card">
	            <span class="stock-premium-label">Risk to check</span>
	            <strong class="stock-decision-answer">{{ example_report.risk_level }}</strong>
	            <span class="stock-decision-support">{{ example_report.risk }}</span>
	        </article>
	        <article class="stock-decision-card">
	            <span class="stock-premium-label">Portfolio fit</span>
	            <strong class="stock-decision-answer">{{ example_report.portfolio_role }}</strong>
	            <span class="stock-decision-support">{{ example_report.decision_use }}</span>
	            <span class="stock-decision-support"><strong>Exposure check:</strong> {{ example_report.concentration_note }}</span>
	            <span class="stock-decision-reminder">Portfolio fit depends on your own goals, circumstances, time horizon and existing exposure.</span>
	        </article>
	        <article class="stock-decision-card">
	            <span class="stock-premium-label">Watch next</span>
	            <strong class="stock-decision-answer">Next research trigger</strong>
	            <span class="stock-decision-support">{{ example_report.next_move }}</span>
	            <span class="stock-decision-support"><strong>What would improve the case:</strong> {{ example_report.stronger_evidence }}</span>
	        </article>
	    </div>

	    <aside class="stock-premium-callout" aria-labelledby="stock-mistake-heading">
	        <span class="stock-premium-label">Investor caution</span>
	        <h3 id="stock-mistake-heading">Common mistake to avoid</h3>
	        <p>{{ example_report.common_mistake }}</p>
	    </aside>

	    <div class="stock-investor-lesson" aria-labelledby="stock-lesson-heading">
	        <span class="stock-premium-label">Investor lesson</span>
	        <h3 id="stock-lesson-heading">What this teaches you</h3>
	        <p>{{ example_report.investor_lesson }}</p>
	    </div>

	    <div class="stock-learning-card" aria-labelledby="stock-learning-heading">
	        <span class="stock-premium-label">Premium investing principle</span>
	        <h3 id="stock-learning-heading">Learn From This Stock</h3>
	        <p class="stock-learning-subtitle">This investing principle can help with future decisions.</p>
	        <div class="stock-learning-grid">
	            <div class="stock-learning-part"><span>Lesson</span><p>{{ example_report.learning_lesson.lesson }}</p></div>
	            <div class="stock-learning-part"><span>Why investors care</span><p>{{ example_report.learning_lesson.why }}</p></div>
	            <div class="stock-learning-part"><span>Question to ask yourself</span><p>{{ example_report.learning_lesson.question }}</p></div>
	        </div>
	        <p class="stock-learning-goal">The goal is to help you understand investing principles, not memorise stock signals.</p>
	    </div>

	    {% set portfolio = example_report.portfolio_builder %}
	    <section class="stock-portfolio-builder" aria-labelledby="stock-portfolio-builder-heading">
	        <span class="stock-premium-label">Premium portfolio education</span>
	        <h3 id="stock-portfolio-builder-heading">How this may fit in a portfolio</h3>
	        <p>Understand the role, overlap and risk questions to consider before adding another holding.</p>

	        <article class="stock-portfolio-role" aria-labelledby="stock-portfolio-role-heading">
	            <h4 id="stock-portfolio-role-heading">Likely portfolio role</h4>
	            <strong>{{ portfolio.role_label }}</strong>
	            <p>{{ portfolio.role_meaning }}</p>
	            <p><b>How investors often use it:</b> {{ portfolio.role_use }}</p>
	            <p class="stock-portfolio-caution"><b>Educational caution:</b> {{ portfolio.role_caution }}</p>
	        </article>

	        <div class="stock-portfolio-grid">
	            <article class="stock-portfolio-card">
	                <h4>Portfolio overlap to check</h4>
	                <strong>{{ portfolio.overlap }}</strong>
	            </article>
	            <article class="stock-portfolio-card">
	                <h4>Core or satellite?</h4>
	                <strong>{{ portfolio.core_label }}</strong>
	                <p>{{ portfolio.core_or_satellite }}</p>
	            </article>
	            <article class="stock-portfolio-card">
	                <h4>Why position size matters</h4>
	                <p>{{ portfolio.position_size }}</p>
	                <p class="stock-position-question">{{ portfolio.position_question }}</p>
	            </article>
	            <article class="stock-portfolio-card caution">
	                <h4>Portfolio mistake to avoid</h4>
	                <strong>{{ portfolio.mistake }}</strong>
	            </article>
	        </div>

	        <div class="stock-portfolio-checklist">
	            <h4>Before adding this holding</h4>
	            <ul>{% for item in portfolio.checklist %}<li>{{ item }}</li>{% endfor %}</ul>
	        </div>

	        <aside class="stock-portfolio-principle" aria-labelledby="stock-portfolio-principle-heading">
	            <h4 id="stock-portfolio-principle-heading">Portfolio principle</h4>
	            <p>{{ portfolio.principle }}</p>
	        </aside>
	        <p class="stock-portfolio-note">Portfolio examples are general education only. Appropriate diversification and position size depend on personal circumstances, goals and risk tolerance.</p>
	    </section>

	    <section class="stock-psychology" aria-labelledby="stock-psychology-heading">
	        <span class="stock-premium-label">Premium investor psychology</span>
	        <h3 id="stock-psychology-heading">Think Before You Invest</h3>
	        <p>A strong investing process includes checking your own thinking—not just the stock.</p>
	        <div class="stock-psychology-grid">
	            <article class="stock-psychology-card">
	                <h4>Avoid FOMO</h4>
	                <p class="stock-psychology-prompt">Would this still interest me if the price had not risen recently or the stock was not receiving attention?</p>
	                <p class="stock-psychology-support">Recent excitement can make an opportunity feel safer or more urgent than the evidence supports.</p>
	            </article>
	            <article class="stock-psychology-card">
	                <h4>What could prove me wrong?</h4>
	                <p class="stock-psychology-prompt">What evidence would weaken the investment case?</p>
	                <p class="stock-psychology-support">A clear reason to reconsider helps prevent confirmation bias and overconfidence.</p>
	            </article>
	            <article class="stock-psychology-card">
	                <h4>Am I investing or reacting?</h4>
	                <p class="stock-psychology-prompt">Is this decision based on research, or am I reacting to headlines, price moves or other investors?</p>
	                <p class="stock-psychology-support">Separating evidence from emotion can improve decision discipline.</p>
	            </article>
	            <article class="stock-psychology-card">
	                <h4>Patience reminder</h4>
	                <p class="stock-psychology-prompt">Has the investment case changed, or am I simply uncomfortable with short-term movement?</p>
	                <p class="stock-psychology-support">Good investing decisions often come from waiting for evidence rather than rushing to act.</p>
	            </article>
	        </div>
	    </section>

	    <details class="stock-business-education">
	        <summary>Understand the Business</summary>
	        <div class="stock-business-body">
	            <div class="stock-business-basis">
	                <span>{{ business_education.basis_label }}</span>
	                <span>{{ business_education.education_type }}</span>
	                <span>{{ business_education.company_name }}</span>
	            </div>
	            <div class="stock-business-grid">
	                <article class="stock-business-item">
	                    <h4>{{ "What exposure the fund generally provides" if business_education.is_etf else "How the company generally makes money" }}</h4>
	                    <p>{{ business_education.business_model }}</p>
	                </article>
	                <article class="stock-business-item">
	                    <h4>{{ "What usually drives performance" if business_education.is_etf else "Main growth drivers" }}</h4>
	                    <p>{{ business_education.growth_drivers }}</p>
	                </article>
	                <article class="stock-business-item">
	                    <h4>{{ "Concentration and overlap risks" if business_education.is_etf else "Main business risks" }}</h4>
	                    <p>{{ business_education.business_risks }}</p>
	                </article>
	                {% if business_education.is_etf %}
	                <article class="stock-business-item">
	                    <h4>What to inspect in the holdings</h4>
	                    <p>{{ business_education.holdings_check }}</p>
	                </article>
	                {% endif %}
	                <article class="stock-business-item">
	                    <h4>What could strengthen the business case</h4>
	                    <p>{{ business_education.strengthen_case }}</p>
	                </article>
	                <article class="stock-business-item">
	                    <h4>What could weaken the business case</h4>
	                    <p>{{ business_education.weaken_case }}</p>
	                </article>
	                <article class="stock-business-item">
	                    <h4>One question to research next</h4>
	                    <p>{{ business_education.research_question }}</p>
	                </article>
	            </div>
	        </div>
	    </details>

	    <div class="stock-supporting-detail" aria-labelledby="stock-supporting-heading">
	        <span class="stock-premium-label">Supporting detail</span>
	        <h3 id="stock-supporting-heading">Explore the reasoning</h3>
	        <p>Open the sections that are useful for your research. The essential decision points stay visible above.</p>
	        <details open>
	            <summary>Why this signal?</summary>
	            <div class="stock-detail-body">
	                <p><strong>Plain-English meaning:</strong> {{ example_report.signal_meaning }}</p>
	                <p><strong>Why it is showing:</strong> {{ example_report.signal_reason }}</p>
	                <p><strong>How to read confidence:</strong> {{ example_report.confidence_read }}</p>
	            </div>
	        </details>
	        <details>
	            <summary>What would weaken the case?</summary>
	            <div class="stock-detail-body"><p>{{ example_report.weaker_evidence }}</p></div>
	        </details>
	        <details>
	            <summary>Portfolio fit checklist</summary>
	            <div class="stock-detail-body"><ul>{% for item in example_report.portfolio_fit_points %}<li>{{ item }}</li>{% endfor %}</ul><a class="payment-button" href="/portfolio-fit">Check Portfolio Fit</a></div>
	        </details>
	        <details>
	            <summary>Before you decide checklist</summary>
	            <div class="stock-detail-body"><ul>{% for item in example_report.checklist %}<li>{{ item }}</li>{% endfor %}</ul></div>
	        </details>
	        <details>
	            <summary>Decision score breakdown</summary>
	            <div class="stock-detail-body">
	                <p>This structured research read uses the existing signal, confidence, risk and portfolio-role context. It is not a precise prediction.</p>
	                <div class="stock-score-grid">{% for item in example_report.score_breakdown %}<div class="stock-score-item"><strong>{{ item.label }}</strong>{{ item.text }}</div>{% endfor %}</div>
	            </div>
	        </details>
	        <p class="stock-identity-note">Premium helps you practise risk awareness, portfolio thinking, evidence-based research and patience so you can make more independent decisions.</p>
	        <a class="payment-button" href="/premium-decision/{{ symbol }}">Open focused Premium report</a>
	    </div>
	</section>
    {% endif %}

<div class="range-row">{% for key, settings in chart_ranges.items() %}<a class="range-button {% if key == active_range %}active{% endif %}" href="/stock/{{ symbol }}?range={{ key }}">{{ settings.label }}</a>{% endfor %}</div>
<div class="metric-grid"><div class="metric"><small>Range start</small><h2>{{ chart_data.start_price }}</h2></div><div class="metric"><small>Range latest</small><h2>{{ chart_data.end_price }}</h2></div><div class="metric"><small>Range move</small><h2 class="{{ chart_data.direction }}">{{ chart_data.change_amount }}</h2></div><div class="metric"><small>Range % move</small><h2 class="{{ chart_data.direction }}">{{ chart_data.change_percent }}</h2></div></div>
<div class="card chart-card">{% if chart_data.ok %}<div class="chart-card-header"><div><h2>Price chart</h2><span>{{ stock_display_label(symbol) }} close price over {{ range_label }}</span></div><div class="chart-range-pill">{{ range_label }}</div></div><div class="chart-shell"><canvas id="stockChart"></canvas></div>{% else %}<h2>Chart unavailable</h2><p style="color:#fca5a5;">{{ chart_data.error }}</p>{% endif %}</div>
<div class="card"><h2>Since market data began</h2><div class="metric-grid"><div class="metric"><small>Earliest available price</small><h2>{{ lifetime.start_price }}</h2></div><div class="metric"><small>Latest available price</small><h2>{{ lifetime.end_price }}</h2></div><div class="metric"><small>Total growth / decrease</small><h2 class="{{ lifetime.direction }}">{{ lifetime.change_amount }}</h2></div><div class="metric"><small>Total % growth / decrease</small><h2 class="{{ lifetime.direction }}">{{ lifetime.change_percent }}</h2></div></div></div>
{{ disclaimer_footer() | safe }}
<script>
const labels={{ chart_data.labels | tojson }};
const prices={{ chart_data.prices | tojson }};
if(labels.length>0){
    const ctx=document.getElementById('stockChart');
    new Chart(ctx,{type:'line',data:{labels:labels,datasets:[{label:'{{ stock_display_label(symbol) }} close price',data:prices,borderWidth:2,tension:0.25}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'white',boxWidth:12,padding:14}}},layout:{padding:{top:4,right:8,bottom:4,left:4}},scales:{x:{ticks:{color:'#94a3b8',maxTicksLimit:6,autoSkip:true},grid:{color:'rgba(255,255,255,0.08)'}},y:{ticks:{color:'#94a3b8',maxTicksLimit:6},grid:{color:'rgba(255,255,255,0.08)'}}}}});
}
</script>
</body>
</html>
"""


@app.route("/newsletter", methods=["GET", "POST"])
def newsletter():
    subscription_message = ""
    subscription_error = False

    if request.method == "POST":
        submitted_email = normalize_email(request.form.get("email"))
        if not valid_newsletter_email(submitted_email):
            subscription_error = True
            subscription_message = "Please enter a valid email address."
        else:
            try:
                create_beehiiv_subscription(submitted_email)
                subscription_message = (
                    "Subscribed successfully through Beehiiv. Please check your inbox if confirmation is required."
                )
            except Exception as error:
                app.logger.error(
                    "Beehiiv newsletter subscription failed: %s",
                    sanitise_newsletter_error(error),
                )
                subscription_error = True
                subscription_message = (
                    "Newsletter signup is temporarily unavailable. Please try again shortly."
                )

    return render_template_string(
        newsletter_landing_html,
        newsletter_embed_html=NEWSLETTER_EMBED_HTML,
        subscription_message=subscription_message,
        subscription_error=subscription_error,
    )


@app.route("/newsletter/latest")
def newsletter_latest():
    weekly_issue = build_weekly_newsletter_issue()
    return render_template_string(
        newsletter_latest_html,
        draft=weekly_issue["draft"],
        issue=weekly_issue["metadata"],
    )


@app.route("/newsletter/rss")
def newsletter_rss():
    weekly_issue = build_weekly_newsletter_issue()
    draft = weekly_issue["draft"]
    issue = weekly_issue["metadata"]
    feed_url = f"{PRODUCTION_BASE_URL}/newsletter"
    item_url = f"{PRODUCTION_BASE_URL}/newsletter/latest"
    issue_body = render_newsletter_issue_body(draft).replace("]]>", "]]&gt;")
    rss_description = (
        f"{issue['rss_status_label']}: "
        f"{weekly_issue['summary']}"
    )
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>StockRadar Weekly</title>
<link>{xml_escape(feed_url)}</link>
<description>The 5-minute market signal for investors who want clarity without noise.</description>
<language>en-gb</language>
<lastBuildDate>{format_datetime(issue["published_at"])}</lastBuildDate>
<item>
<title>{xml_escape(issue["title"])}</title>
<link>{xml_escape(item_url)}</link>
<guid isPermaLink="false">{xml_escape(issue["guid"])}</guid>
<pubDate>{format_datetime(issue["published_at"])}</pubDate>
<description>{xml_escape(rss_description)}</description>
<content:encoded><![CDATA[{issue_body}]]></content:encoded>
</item>
</channel>
</rss>
"""
    return Response(rss_xml, content_type="application/rss+xml; charset=utf-8")


@app.route("/admin/newsletter-preview")
def admin_newsletter_preview():
    if not owner_has_access():
        return redirect(url_for("login", next=request.path))

    draft = build_free_weekly_newsletter()
    return render_template_string(newsletter_preview_html, draft=draft)


newsletter_beehiiv_copy_html = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beehiiv Newsletter Copy — StockRadar</title>
<style>
*{box-sizing:border-box;}body{margin:0;background:#020617;color:#e5e7eb;font-family:Arial,sans-serif;padding:34px 20px;}.wrap{max-width:920px;margin:0 auto;}.card{background:#0f172a;border:1px solid rgba(255,255,255,.1);border-radius:22px;padding:26px;margin-bottom:18px;}h1,h2{color:#f8fafc;}p{color:#cbd5e1;line-height:1.65;}a{color:#38bdf8;font-weight:900;text-decoration:none;}.instruction{padding:16px;border-radius:14px;background:rgba(74,222,163,.1);border:1px solid rgba(74,222,163,.25);color:#d1fae5;font-weight:900;}label{display:block;margin:20px 0 8px;color:#94a3b8;font-size:12px;font-weight:900;text-transform:uppercase;letter-spacing:.08em;}textarea,input{width:100%;border:1px solid #334155;border-radius:12px;background:#07111d;color:#f8fafc;padding:13px;font:14px/1.55 Arial,sans-serif;}textarea{min-height:90px;resize:vertical;}textarea.body{min-height:320px;}button{margin-top:8px;border:0;border-radius:10px;background:#38bdf8;color:#02111f;font-weight:900;padding:9px 12px;cursor:pointer;}.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.meta div{background:#07111d;border-radius:12px;padding:13px;color:#cbd5e1}.meta strong{display:block;color:#94a3b8;font-size:11px;text-transform:uppercase;margin-bottom:5px;}
</style>
</head>
<body><main class="wrap">
<p><a href="/owner">← Owner area</a></p>
<section class="card">
<h1>Beehiiv-ready newsletter copy</h1>
<p class="instruction">Copy this into Beehiiv and send from Beehiiv.</p>
<div class="meta">
<div><strong>Issue date</strong>{{ export.issue_date }}</div>
<div><strong>Issue key</strong>{{ export.issue_key }}</div>
<div><strong>Current issue status</strong>{{ export.issue_status }}</div>
<div><strong>Beehiiv status</strong>{{ beehiiv_status }}</div>
</div>
<label for="subject">Subject</label><input id="subject" readonly value="{{ export.subject }}"><button type="button" onclick="copyField('subject',this)">Copy subject</button>
<label for="preview">Preview text</label><textarea id="preview" readonly>{{ export.preview_text }}</textarea><button type="button" onclick="copyField('preview',this)">Copy preview</button>
<label for="body">Email body</label><textarea class="body" id="body" readonly>{{ export.email_body }}</textarea><button type="button" onclick="copyField('body',this)">Copy email body</button>
<label for="issue-url">Read online URL</label><input id="issue-url" readonly value="{{ export.issue_url }}"><button type="button" onclick="copyField('issue-url',this)">Copy URL</button>
<label for="disclaimer">Disclaimer</label><textarea id="disclaimer" readonly>{{ export.disclaimer }}</textarea><button type="button" onclick="copyField('disclaimer',this)">Copy disclaimer</button>
</section>
</main>
<script>
function copyField(id,button){var field=document.getElementById(id);field.select();field.setSelectionRange(0,field.value.length);navigator.clipboard.writeText(field.value).then(function(){var old=button.textContent;button.textContent='Copied';setTimeout(function(){button.textContent=old;},1200);});}
</script>
</body></html>
"""


@app.route("/admin/newsletter/beehiiv-copy")
def admin_newsletter_beehiiv_copy():
    if not owner_has_access():
        return redirect(url_for("login", next=request.path))

    issue = build_weekly_newsletter_issue()
    export = build_beehiiv_manual_export(issue)
    state = load_newsletter_beehiiv_state()["issues"].get(export["issue_key"], {})
    beehiiv_status = state.get("status", "beehiiv_api_post_blocked")
    return render_template_string(
        newsletter_beehiiv_copy_html,
        export=export,
        beehiiv_status=beehiiv_status,
    )


newsletter_send_summary_html = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Newsletter Send Summary — StockRadar</title>
<style>
*{box-sizing:border-box;}body{margin:0;background:#020617;color:#e5e7eb;font-family:Arial,sans-serif;padding:34px 20px;}.wrap{max-width:920px;margin:0 auto;}.card{background:rgba(15,23,42,.94);border:1px solid rgba(255,255,255,.1);border-radius:24px;padding:26px;margin-bottom:18px;}a{color:#38bdf8;font-weight:900;text-decoration:none;}button{border:0;border-radius:14px;background:linear-gradient(135deg,#00ffaa,#ffb86b);color:#020617;font-weight:950;padding:13px 16px;cursor:pointer;}p,li{color:#cbd5e1;line-height:1.65;}table{width:100%;border-collapse:collapse;margin-top:12px;}th,td{text-align:left;padding:10px;border-bottom:1px solid rgba(255,255,255,.08);}th{color:#94a3b8;font-size:12px;text-transform:uppercase;}@media(max-width:700px){table{display:block;overflow-x:auto;}}
</style>
</head>
<body>
<div class="wrap">
<p><a href="/owner">← Owner area</a></p>
<div class="card">
<h1>Newsletter send summary</h1>
<p><strong>Current issue:</strong> {{ newsletter_status.current_issue_guid }} · {{ newsletter_status.current_issue_status }}</p>
<p>Weekly bulk sender: {{ newsletter_status.weekly_bulk_sender }} · Beehiiv configured: {{ "yes" if newsletter_status.beehiiv_configured else "no" }} · Create Post blocked: {{ "yes" if newsletter_status.beehiiv_create_post_blocked else "no" }}</p>
<p>Next expected Friday auto-send: {{ newsletter_status.next_expected_friday_send_at }}</p>
{% if not summary %}
<p>Beehiiv Create Post access is blocked. Use the copy/export page and send the campaign manually from Beehiiv.</p>
<p><a href="/admin/newsletter/beehiiv-copy">Open Beehiiv copy/export page</a></p>
{% else %}
<p><strong>Issue:</strong> {{ summary.issue_key or summary.issue_guid }}</p>
<p><strong>Beehiiv status:</strong> {{ summary.status or summary.reason }}</p>
{% if summary.beehiiv_post_id %}<p><strong>Beehiiv post ID:</strong> {{ summary.beehiiv_post_id }}</p>{% endif %}
{% if summary.failure_reason %}<p><strong>Failure:</strong> {{ summary.failure_reason }}</p>{% endif %}
{% endif %}
</div>
{{ disclaimer_footer() | safe }}
</div>
</body>
</html>
"""


@app.route("/admin/newsletter-send", methods=["GET", "POST"])
def admin_newsletter_send():
    if not owner_has_access():
        return redirect(url_for("login", next=request.path))

    summary = None
    if request.method == "POST":
        return redirect(url_for("admin_newsletter_beehiiv_copy"))

    return render_template_string(
        newsletter_send_summary_html,
        summary=summary,
        newsletter_status=newsletter_status_snapshot(),
    )


@app.route("/newsletter/cron/send", methods=["GET", "POST"])
def newsletter_cron_send():
    supplied_secret = (
        request.headers.get("X-Newsletter-Cron-Secret", "")
        or request.args.get("secret", "")
    ).strip()

    if not NEWSLETTER_CRON_SECRET:
        return jsonify({"error": "Newsletter cron secret is not configured."}), 503
    if supplied_secret != NEWSLETTER_CRON_SECRET:
        return jsonify({"error": "Forbidden."}), 403

    summary = run_due_newsletter_automation(delivery_type="cron")
    return jsonify(summary)


# --- Health and diagnostics routes ---
@app.route("/deploy-version")
def deploy_version():
    return jsonify({
        "build": "dividend-snapshot-f1ade12",
        "commit_expected": "f1ade12",
        "dividend_template_expected": True,
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "StockRadar",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "newsapi_configured": bool(NEWSAPI_KEY),
        "dashboard_cache_configured": True,
        "stripe_configured": stripe_credentials_configured(),
        "premium_payments_enabled": PREMIUM_PAYMENTS_ENABLED,
        "owner_login_configured": owner_login_configured(),
        "stock_universe_csv": STOCK_UNIVERSE_CSV,
        "stock_universe_cache_ttl_seconds": STOCK_UNIVERSE_CACHE_TTL_SECONDS,
        "dashboard_cache_ttl_seconds": DASHBOARD_CACHE_TTL_SECONDS,
        "newsletter": newsletter_status_snapshot(),
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
        "stripe_configured": stripe_credentials_configured(),
        "premium_payments_enabled": PREMIUM_PAYMENTS_ENABLED,
        "owner_login_configured": owner_login_configured(),
    }, 200

@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/robots.txt")
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {PRODUCTION_BASE_URL}/sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    public_paths = [
        "/",
        "/newsletter",
        "/newsletter/latest",
        "/how-it-works",
        "/universe",
        "/upgrade",
        "/privacy",
        "/terms",
        "/refund-policy",
        "/risk-disclaimer",
        "/contact",
        "/manage-subscription",
        "/feedback",
    ]
    stock_paths = [
        f"/stock/{quote(item['ticker'], safe='.-')}"
        for item in get_stock_universe()
        if item.get("ticker")
    ]
    public_paths = list(dict.fromkeys(public_paths + stock_paths))
    last_modified = datetime.now(timezone.utc).date().isoformat()
    urls = "\n".join(
        (
            "  <url>\n"
            f"    <loc>{PRODUCTION_BASE_URL}{path}</loc>\n"
            f"    <lastmod>{last_modified}</lastmod>\n"
            "  </url>"
        )
        for path in public_paths
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return Response(content, mimetype="application/xml")


@app.route("/how-it-works")
def how_it_works():
    return render_legal_page(
        "How StockRadar Works",
        """
        <p>StockRadar is designed to make market information easier to interpret without turning research prompts into trading instructions.</p>

        <div class="section-grid">
            <section class="info-section">
                <h2>What StockRadar does</h2>
                <p>StockRadar turns market data, live headlines, affected stocks, watchlists and signal patterns into plain-English research prompts.</p>
            </section>
            <section class="info-section">
                <h2>How Market News works</h2>
                <p>The Market News ticker shows current headlines and stocks or sectors that may be affected. Affected-stock chips provide context—they do not guarantee that a stock will move.</p>
            </section>
        </div>

        <h2>What BUY, HOLD and SELL mean</h2>
        <div class="signal-guide">
            <section class="signal-explainer buy">
                <h2>BUY</h2>
                <p>A stronger research setup. It does not mean “you must buy”.</p>
            </section>
            <section class="signal-explainer hold">
                <h2>HOLD</h2>
                <p>A neutral or watchlist setup. It may be worth monitoring.</p>
            </section>
            <section class="signal-explainer sell">
                <h2>SELL</h2>
                <p>A caution or weakness signal. It does not mean “you must sell immediately”.</p>
            </section>
        </div>

        <h2>How to use StockRadar</h2>
        <ol class="research-flow">
            <li>Read the Market News ticker.</li>
            <li>Check which stocks may be affected.</li>
            <li>Open the relevant stock page.</li>
            <li>Review the signal, confidence, available chart and risk notes.</li>
            <li>Decide whether the setup deserves further independent research.</li>
        </ol>

        <section class="info-section">
            <h2>What StockRadar is not</h2>
            <p>StockRadar is not personal financial advice. It does not know your full portfolio, income, goals, tax position or risk tolerance. Signals are educational research prompts, not personalised instructions or promises.</p>
        </section>

        <section class="weekly-cta">
            <div>
                <h2>Want the weekly version?</h2>
                <p>Get the concise market pulse, signal highlights, watchlist moves and risk checks.</p>
            </div>
            <a href="/newsletter">Join StockRadar Weekly</a>
        </section>
        """,
    )


@app.route("/privacy")
def privacy():
    return render_legal_page(
        "Privacy Policy",
        """
        <p>StockRadar uses the minimum information needed to operate the service, provide requested features, maintain security, and support customers.</p>
        <h2>Information we may process</h2>
        <p>This may include account, newsletter or support details you provide, session information needed for login and premium access, and technical logs used to keep the service reliable and secure.</p>
        <h2>Email and newsletters</h2>
        <p>If you join StockRadar Weekly, your email is used to send newsletter issues, StockRadar updates and market briefs. You can contact support if you need help with newsletter or account information.</p>
        <h2>Payments</h2>
        <p>Payment processing is handled by Stripe when Premium checkout is available. StockRadar does not store full payment-card details.</p>
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
        <p>Premium features require an active subscription. Premium is £5/month where checkout is enabled, unless the upgrade page clearly states otherwise. Your account or subscription access is linked to the email you provide, so you are responsible for providing accurate payment and contact information.</p>
        <p>Payments are handled by Stripe. StockRadar does not store full payment-card details.</p>
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
        support_contact = '<a href="/contact">the StockRadar contact page</a>'

    return render_legal_page(
        "Refund Policy",
        f"""
        <p>StockRadar premium payments are not currently open unless clearly stated on the upgrade page. This policy applies if and when a paid subscription is activated.</p>
        <h2>Cancellation</h2>
        <p>You may cancel a paid subscription at any time. Cancellation stops future billing, and no further subscription payment will be taken after the cancellation becomes effective.</p>
        <p>After cancelling, you will continue to have access to the paid features until the end of your current billing period. Your access will not normally end immediately.</p>
        <h2>Refund requests</h2>
        <p>Subscription payments are generally non-refundable once a billing period has started, because access remains available for the rest of that period. However, refund requests may be reviewed case by case where there has been an accidental duplicate charge, a technical billing error, or another exceptional circumstance.</p>
        <p>To request a cancellation or refund review, contact {support_contact} with the email address associated with your account and relevant payment details. Do not send full payment-card information.</p>
        <h2>Statutory rights</h2>
        <p>Nothing in this policy limits any refund, cancellation, or consumer rights that apply under relevant law.</p>
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
        <p>For support, email <a href="mailto:stock.radar.support@gmail.com">stock.radar.support@gmail.com</a>.</p>
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
        support_contact = '<a href="/contact">the StockRadar contact page</a>'

    return render_legal_page(
        "Manage Subscription",
        f"""
        <p>StockRadar Premium is a £5/month educational research subscription. You may cancel anytime.</p>
        <p>Payments are handled by Stripe. StockRadar does not store your full card details.</p>
        <p>Your subscription access is linked to the email used at checkout. For now, subscription management and cancellation requests are handled through StockRadar support and Stripe records rather than a self-service customer portal.</p>
        <h2>How to cancel</h2>
        <p>Contact {support_contact} using the email address used at checkout and the subject line: <strong>Cancel StockRadar Premium</strong>.</p>
        <p>Cancellation stops future billing. Premium access continues until the end of the current billing period.</p>
        <h2>Refund requests</h2>
        <p>Refund requests are reviewed case by case. This does not affect statutory rights.</p>
        <h2>Educational use</h2>
        <p>StockRadar is educational and informational only and does not provide regulated financial advice.</p>
        """,
    )


@app.route("/feedback")
def feedback():
    return render_legal_page(
        "StockRadar Feedback",
        """
        <p>StockRadar is in soft launch. Short, honest feedback from early testers helps us improve clarity, usefulness and trust before a wider release.</p>
        <p>You do not need to write a long review. A few direct answers are genuinely useful.</p>
        <h2>Useful questions to answer</h2>
        <ul class="prompt-list">
            <li>Did StockRadar make sense within 10 seconds?</li>
            <li>What felt useful?</li>
            <li>What felt confusing or overloaded?</li>
            <li>Would you subscribe to StockRadar Weekly?</li>
            <li>What would make the site feel more trustworthy?</li>
            <li>Did anything look broken on mobile?</li>
        </ul>
        <h2>Send your feedback</h2>
        <p>Email <span class="feedback-email">stock.radar.support@gmail.com</span>. If something looked broken, please include the page, device and browser you used.</p>
        <a class="feedback-cta" href="mailto:stock.radar.support@gmail.com?subject=StockRadar%20Feedback">Send Feedback</a>
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
    data.setdefault("premium_decision_brief", build_premium_decision_brief(data.get("recommendations", [])))
    data.setdefault("market_snapshot", [])
    data.setdefault("market_status", market_status())
    data.setdefault("last_updated", datetime.now().strftime("%d %b %Y, %H:%M"))
    data.setdefault("ticker_updated", datetime.now().strftime("%H:%M"))
    data.setdefault("impact_radar", [])
    data.setdefault("live_headlines", [])

    data["live_headlines"] = [
        item for item in data.get("live_headlines", [])
        if is_public_live_market_headline(item)
    ]

    data.setdefault("newsapi_configured", bool(NEWSAPI_KEY))
    data["live_news_active"] = any(
        is_public_live_market_headline(item)
        for item in data.get("live_headlines", [])
    )
    data["market_news_refresh_interval_ms"] = MARKET_NEWS_REFRESH_INTERVAL_MS
    data["market_news_ticker_limit"] = MARKET_NEWS_TICKER_LIMIT
    data["owner_logged_in"] = owner_has_access()
    data["has_premium_access"] = premium_has_access()
    data["is_public_home"] = (
        not request.args.get("tab")
        and not data["owner_logged_in"]
        and not data["has_premium_access"]
    )
    data["free_report_preview"] = build_homepage_free_report_preview(
        data.get("recommendations", [])
    )
    data["active_tab"] = active_tab
    data["quick_search_query"] = quick_search_query
    data["quick_search_results"] = quick_search_results
    data["universe_preview"] = get_stock_universe()[:12]

    return render_template_string(html, **data)


@app.route("/api/market-news")
def api_market_news():
    data = get_cached_dashboard_data(force_refresh=request.args.get("refresh") == "1") or {}

    if not isinstance(data, dict) or not data.get("market_status"):
        data = prepare_dashboard_data() or {}

    if not isinstance(data, dict):
        data = {}

    live_headlines = [
        item for item in data.get("live_headlines", [])
        if is_public_live_market_headline(item)
    ]

    return jsonify({
        "items": serialize_market_news_items(live_headlines, limit=MARKET_NEWS_TICKER_LIMIT),
        "ticker_updated": data.get("ticker_updated") or datetime.now().strftime("%H:%M"),
        "live_news_active": any(is_public_live_market_headline(item) for item in live_headlines),
        "refresh_interval_ms": MARKET_NEWS_REFRESH_INTERVAL_MS,
    })

@app.route("/ai-recommendations")
def ai_recommendations():
    return redirect(url_for("dashboard", tab="watchlist"))


@app.route("/watchlist")
def watchlist():
    return redirect(url_for("dashboard", tab="watchlist"))


@app.route("/stock/<symbol>")
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
    dividend_context = get_dividend_context(cleaned_symbol)
    has_premium_access = premium_has_access()
    today_context = None
    business_education = None

    if has_premium_access:
        universe_item = next(
            (
                item for item in get_stock_universe()
                if str(item.get("ticker") or "").strip().upper() == cleaned_symbol
            ),
            {},
        )
        sector = str(
            universe_item.get("sector")
            or SECTOR_MAP.get(cleaned_symbol)
            or "General research candidate"
        ).strip()
        company_name = str(
            universe_item.get("name")
            or stock_display_label(cleaned_symbol)
            or cleaned_symbol
        ).strip()
        role_profile = classify_portfolio_role(cleaned_symbol)
        today_context = build_today_context(ai_context)
        business_education = build_business_education(
            cleaned_symbol,
            sector,
            company_name,
            role_profile,
        )

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
        dividend_context=dividend_context,
        has_premium_access=has_premium_access,
        today_context=today_context,
        business_education=business_education,
    )


@app.route("/upgrade")
def upgrade():
    return render_template_string(
        upgrade_html,
        has_premium_access=premium_has_access(),
        premium_payments_enabled=stripe_checkout_configured(),
        premium_decision_brief=build_premium_decision_brief(),
    )


@app.route("/create-checkout-session", methods=["GET", "POST"])
def create_checkout_session():
    if request.method == "GET":
        return redirect(url_for("upgrade"))

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
        <h1>Checkout unavailable</h1>
        <p>Stripe Checkout is not available in this environment. The dashboard remains live and no payment was started.</p>
        <p>Checkout remains disabled until payment credentials are present and the environment is explicitly approved for checkout.</p>
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
            allow_promotion_codes=True,
        )
        return redirect(checkout_session.url, code=303)
    except Exception:
        app.logger.error("Stripe Checkout session creation failed.")
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
        <p>No payment was started. Please return to the upgrade page or contact support if the issue continues.</p>
        <a href="/upgrade">Back to upgrade page</a>
    </div>
</body>
</html>
        """), 502

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
            payment_verified = checkout_session_payment_verified(verified_session)

            if payment_verified:
                customer_id = stripe_identifier(stripe_value(verified_session, "customer"))
                subscription_id = stripe_identifier(stripe_value(verified_session, "subscription"))
                premium_email = checkout_session_email(verified_session)

                session["premium_active"] = True
                remember_premium_session_identifiers(customer_id, subscription_id, premium_email)
                update_premium_entitlement(
                    customer_id=customer_id,
                    subscription_id=subscription_id,
                    email=premium_email,
                    subscription_status="active",
                    premium_active=True,
                    event_type="checkout-success",
                )
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


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    if not stripe or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Stripe webhook is not configured."}), 503

    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            signature,
            STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        return jsonify({"error": "Invalid webhook payload."}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid webhook signature."}), 400

    event_type = str(stripe_value(event, "type", "") or "")
    event_data = stripe_nested_value(event, "data", "object") or {}

    if event_type == "checkout.session.completed":
        customer_id = stripe_identifier(stripe_value(event_data, "customer"))
        subscription_id = stripe_identifier(stripe_value(event_data, "subscription"))
        premium_email = checkout_session_email(event_data)
        payment_verified = checkout_session_payment_verified(event_data)
        status = (
            str(stripe_value(event_data, "payment_status", "") or "").lower()
            or str(stripe_value(event_data, "status", "") or "").lower()
        )
        update_premium_entitlement(
            customer_id=customer_id,
            subscription_id=subscription_id,
            email=premium_email,
            subscription_status=status,
            premium_active=payment_verified,
            event_type=event_type,
        )
    elif event_type in {"customer.subscription.updated", "customer.subscription.deleted"}:
        customer_id = stripe_identifier(stripe_value(event_data, "customer"))
        subscription_id = stripe_identifier(stripe_value(event_data, "id"))
        status = str(stripe_value(event_data, "status", "") or "").lower()
        premium_active = (
            False
            if event_type == "customer.subscription.deleted"
            else subscription_status_is_active(status)
        )
        update_premium_entitlement(
            customer_id=customer_id,
            subscription_id=subscription_id,
            subscription_status=status or "canceled",
            premium_active=premium_active,
            event_type=event_type,
        )
    elif event_type == "invoice.payment_failed":
        update_premium_entitlement(
            customer_id=stripe_identifier(stripe_value(event_data, "customer")),
            subscription_id=stripe_identifier(stripe_value(event_data, "subscription")),
            email=stripe_value(event_data, "customer_email", ""),
            subscription_status="payment_failed",
            premium_active=False,
            event_type=event_type,
        )
    elif event_type == "invoice.payment_succeeded":
        update_premium_entitlement(
            customer_id=stripe_identifier(stripe_value(event_data, "customer")),
            subscription_id=stripe_identifier(stripe_value(event_data, "subscription")),
            email=stripe_value(event_data, "customer_email", ""),
            subscription_status="paid",
            premium_active=True,
            event_type=event_type,
        )
    else:
        return jsonify({"received": True, "ignored": event_type}), 200

    return jsonify({"received": True}), 200


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
    session.pop("premium_active", None)
    return redirect(url_for("dashboard"))


start_newsletter_auto_send_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
