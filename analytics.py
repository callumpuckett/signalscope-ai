"""Consent-first analytics. Billing never depends on analytics availability."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.request

from flask import jsonify, request, session, url_for
from flask_wtf.csrf import generate_csrf
from itsdangerous import BadSignature, URLSafeTimedSerializer

MEASUREMENT_ID = "G-6TMSFYY19K"
CONTAINER_ID = "GTM-M45PZW6K"
COOKIE = "sr_analytics_consent"
MAX_AGE = 180 * 86400
STORE = "analytics"
PUBLIC = {
    "dashboard": ("/", "StockRadar"),
    "upgrade": ("/upgrade", "Premium"), "stock_detail": ("/stock", "Stock research"),
    "beginner": ("/beginner", "Investment Compass"),
    "portfolio_fit": ("/portfolio-fit", "Portfolio research"),
    "compare": ("/compare", "Compare stocks"), "compare_direct": ("/compare", "Compare stocks"),
    "what_if": ("/what-if", "What If"), "newsletter": ("/newsletter", "Newsletter"),
    "newsletter_latest": ("/newsletter/latest", "Newsletter issue"),
    "opportunities": ("/opportunities", "Opportunities"),
    "privacy": ("/privacy", "Privacy"), "how_it_works": ("/how-it-works", "Methodology"),
    "stock_universe_page": ("/universe", "Stock universe"),
    "premium_decision": ("/premium-decision", "Intelligence Report"),
    "premium_watchlist": ("/premium-watchlist", "Premium watchlist"),
    "watchlist": ("/watchlist", "Watchlist"),
    "ai_recommendations": ("/ai-recommendations", "Research ideas"),
    "terms": ("/terms", "Terms"), "refund_policy": ("/refund-policy", "Refund policy"),
    "risk_disclaimer": ("/risk-disclaimer", "Risk disclaimer"),
    "contact": ("/contact", "Contact"), "feedback": ("/feedback", "Feedback"),
}


class ConsentAnalytics:
    def __init__(self, app, storage, stripe_value):
        self.app, self.storage, self.value = app, storage, stripe_value
        # Remains off until the supplied container is reviewed and published.
        self.enabled = os.getenv("ANALYTICS_ENABLED", "false").lower() == "true"
        self.api_secret = os.getenv("GA4_API_SECRET", "").strip()
        self.serializer = URLSafeTimedSerializer(app.secret_key, salt="analytics-consent-v1")
        app.add_url_rule("/analytics/consent", "analytics_consent", self.consent, methods=["POST"])
        app.after_request(self.decorate)

    def digest(self, value):
        key = str(self.app.secret_key).encode()
        return hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()

    def choice(self):
        try:
            data = self.serializer.loads(request.cookies.get(COOKIE, ""), max_age=MAX_AGE)
            return data if data.get("choice") in {"accepted", "rejected"} else {}
        except (BadSignature, TypeError, AttributeError):
            return {}

    def update(self, callback):
        store = self.storage()
        if not store or not store.durable:
            return False
        return store.update_state(STORE, callback)

    def consent(self):
        if not self.enabled:
            return jsonify(error="Analytics is not enabled."), 503
        if request.content_length and request.content_length > 2048:
            return jsonify(error="Invalid request."), 400
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error="Invalid request."), 400
        choice = data.get("choice")
        if choice not in {"accepted", "rejected"}:
            return jsonify(error="Choose accept or reject."), 400
        previous = self.choice()
        token = secrets.token_hex(24) if choice == "accepted" else ""
        client = str(secrets.randbelow(10**12)) + "." + str(int(time.time()))
        now = time.time()

        def save(state):
            consents = state.setdefault("consents", {})
            for key in list(consents):
                if consents[key].get("expires", 0) < now:
                    del consents[key]
            if previous.get("token") in consents:
                consents[previous["token"]]["granted"] = False
            if token:
                consents[token] = {"granted": True, "at": now, "expires": now + MAX_AGE,
                                   "client_id": client, "session_id": int(now)}

        try:
            saved = self.update(save)
        except Exception:
            saved = False
        # Rejection always stops browser collection, even during a storage outage.
        if not saved and choice == "accepted":
            return jsonify(error="Preferences could not be saved. Analytics remains off."), 503
        payload = {"choice": choice, "token": token, "client_id": client if token else ""}
        if choice == "rejected":
            session.pop("analytics_session_id", None)
            session.pop("analytics_last_seen", None)
        response = jsonify(choice=choice, saved=saved)
        response.set_cookie(COOKIE, self.serializer.dumps(payload), max_age=MAX_AGE,
                            secure=request.is_secure, httponly=True, samesite="Lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    def decorate(self, response):
        page = PUBLIC.get(request.endpoint)
        if not self.enabled or not page or response.status_code != 200 or response.mimetype != "text/html":
            return response
        choice = self.choice()
        if choice.get("choice") == "accepted":
            now = time.time()
            if now - session.get("analytics_last_seen", 0) >= 1800:
                session["analytics_session_id"] = int(now)
            session["analytics_last_seen"] = now
        config = {"choice": choice.get("choice", ""), "client_id": choice.get("client_id", ""),
                  "session_id": session.get("analytics_session_id"),
                  "page_path": page[0], "page_title": page[1],
                  "csrf": generate_csrf(), "container": CONTAINER_ID, "measurement": MEASUREMENT_ID}
        # No incoming URL, query, email, ticker list or user-entered value is embedded.
        from markupsafe import escape
        fragment = ('<link rel="stylesheet" href="' + url_for("static", filename="analytics.css") + '">'
                    '<div id="sr-analytics" data-config="' + str(escape(json.dumps(config))) + '">'
                    '<section id="sr-consent-banner" role="region" aria-label="Analytics preferences" hidden>'
                    '<p>Help improve StockRadar. With your permission, Google Analytics measures visits and '
                    'Premium journeys. Analytics is optional. <a href="/privacy">Privacy information</a></p>'
                    '<div><button type="button" data-sr-consent="accepted">Accept Analytics</button>'
                    '<button type="button" data-sr-consent="rejected">Reject Analytics</button></div>'
                    '<p id="sr-consent-status" role="status"></p></section>'
                    '<button type="button" id="sr-consent-preferences">Analytics preferences</button></div>'
                    '<script defer src="' + url_for("static", filename="analytics.js") + '"></script>')
        response.set_data(response.get_data(as_text=True).replace("</body>", fragment + "</body>", 1))
        response.headers["Cache-Control"] = "private, no-store"
        return response

    def checkout_created(self, checkout, intent):
        """Called only after Stripe has actually created the Checkout Session."""
        if not self.enabled:
            return
        consent = self.choice()
        if consent.get("choice") != "accepted":
            return
        checkout_id = self.value(checkout, "id", "")
        if not isinstance(checkout_id, str) or not checkout_id.startswith("cs_"):
            return
        reference = self.digest(intent)

        def associate(state):
            valid = state.setdefault("consents", {}).get(consent.get("token"), {})
            if not valid.get("granted") or valid.get("expires", 0) <= time.time():
                return
            checkouts = state.setdefault("checkouts", {})
            now = time.time()
            for key in list(checkouts):
                if checkouts[key].get("expires", 0) < now:
                    del checkouts[key]
            checkouts[reference] = {"consent": consent["token"], "expires": now + 86400,
                                   "session_id": session.get("analytics_session_id", int(now)),
                                   "checkout": self.digest(checkout_id)}

        if self.update(associate):
            self.enqueue("checkout_" + reference, reference, "begin_checkout")

    def paid_checkout(self, checkout, event_created):
        """Only called after the existing signed webhook and entitlement validation."""
        if (not self.enabled or not 0 <= time.time() - event_created <= 7 * 86400
                or self.value(checkout, "livemode") is not True
                or self.value(checkout, "payment_status") != "paid"
                or not isinstance(self.value(checkout, "amount_total"), int)
                or self.value(checkout, "amount_total") <= 0):
            return
        intent = self.value(self.value(checkout, "metadata", {}), "stockradar_checkout_intent", "")
        subscription = self.value(checkout, "subscription")
        if not intent or not isinstance(subscription, str) or not subscription.startswith("sub_"):
            return
        self.enqueue("purchase_" + self.digest(subscription), self.digest(intent), "purchase",
                     checkout_id=self.value(checkout, "id", ""))

    def enqueue(self, event_id, reference, name, checkout_id=None):
        if not self.api_secret:
            return  # Never fall back to browser success pages or unconsented reporting.
        payload = {}

        def claim(state):
            checkout = state.setdefault("checkouts", {}).get(reference, {})
            if checkout_id is not None and checkout.get("checkout") != self.digest(checkout_id):
                return
            consent = state.setdefault("consents", {}).get(checkout.get("consent"), {})
            if (not consent.get("granted") or consent.get("expires", 0) <= time.time()
                    or checkout.get("expires", 0) <= time.time()):
                return
            events = state.setdefault("events", {})
            for key in list(events):
                if events[key].get("at", 0) < time.time() - 400 * 86400:
                    del events[key]
            if event_id in events:
                return
            params = {"session_id": checkout["session_id"], "engagement_time_msec": 1}
            if name == "purchase":
                params["transaction_id"] = event_id
            params["items"] = [{"item_id": "premium_monthly", "item_name": "StockRadar Premium", "quantity": 1}]
            payload.update(client_id=consent["client_id"],
                           consent={"ad_user_data": "DENIED", "ad_personalization": "DENIED"},
                           events=[{"name": name, "params": params}])
            events[event_id] = {"status": "pending", "at": time.time(), "name": name,
                                "consent": checkout["consent"], "payload": dict(payload),
                                "expires": checkout["expires"], "attempts": 0, "next_at": 0}

        if self.update(claim) and payload:
            thread = threading.Thread(target=self.deliver, args=(event_id, payload), daemon=True)
            thread.start()

    def deliver(self, event_id, payload):
        status = "unconfirmed"
        try:
            permitted = []

            def recheck(state):
                record = state.setdefault("events", {}).get(event_id, {})
                consent = state.setdefault("consents", {}).get(record.get("consent"), {})
                now = time.time()
                if record.get("status") in {"accepted", "unconfirmed", "consent_unavailable"}:
                    return
                if record.get("next_at", 0) > now:
                    return
                if (consent.get("granted") and consent.get("expires", 0) > now
                        and record.get("expires", 0) > now and record.get("attempts", 0) < 3):
                    record.update(status="sending", next_at=now + 180,
                                  attempts=record.get("attempts", 0) + 1)
                    permitted.append(True)
                else:
                    record.update(status="consent_unavailable")
                    record.pop("consent", None)
                    record.pop("payload", None)

            if not self.update(recheck) or not permitted:
                status = "consent_unavailable"
                return
            endpoint = "https://www.google-analytics.com/mp/collect?measurement_id=" + MEASUREMENT_ID + "&api_secret=" + self.api_secret
            request_object = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                                    headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request_object, timeout=3) as response:
                status = "accepted" if 200 <= response.status < 300 else "unconfirmed"
        except Exception:
            self.app.logger.warning("Analytics delivery unconfirmed; no payment processing was affected.")
        finally:
            try:
                def finish(state):
                    record = state["events"][event_id]
                    if status == "unconfirmed" and record.get("name") == "purchase" and record.get("attempts", 0) < 3:
                        record.update(status="pending", next_at=time.time() + 60 * 4 ** (record["attempts"] - 1))
                    else:
                        record.update(status=status)
                        record.pop("consent", None)
                        record.pop("payload", None)
                if permitted:
                    self.update(finish)
            except Exception:
                pass

    def retry_pending(self):
        """Bounded purchase recovery, using the existing periodic worker check."""
        if not self.enabled or not self.api_secret:
            return
        store = self.storage()
        if not store or not store.durable:
            return
        state = store.load_state(STORE)
        pending = [(key, record) for key, record in state.get("events", {}).items()
                   if record.get("name") == "purchase" and record.get("status") in {"pending", "sending"}
                   and record.get("next_at", 0) <= time.time()]
        for key, record in pending[:5]:
            thread = threading.Thread(target=self.deliver, args=(key, record["payload"]), daemon=True)
            thread.start()

    def safely(self, operation, *args):
        try:
            operation(*args)
        except Exception as error:
            self.app.logger.warning("Analytics unavailable: operation=%s error_type=%s; billing continues unchanged.",
                                    operation.__name__, type(error).__name__)
