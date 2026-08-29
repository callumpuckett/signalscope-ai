#!/usr/bin/env python3
"""Read-only browser smoke tests for a local or production StockRadar deploy."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError,
    sync_playwright,
)


PRODUCTION_URL = "https://www.stockradarhq.com"
DESKTOP_VIEWPORT = {"width": 1440, "height": 1000}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
DEFAULT_TIMEOUT_MS = 20_000
IGNORED_CONSOLE_ERRORS = (
    "a listener indicated an asynchronous response",
)


class SmokeFailure(AssertionError):
    """Release-blocking smoke-test failure."""


@dataclass
class BrowserSignals:
    base_origin: str
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)

    def attach(self, page: Page) -> None:
        page.on("console", self._console)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on("requestfailed", self._request_failed)
        page.on("response", self._response)

    def _console(self, message) -> None:
        if message.type != "error":
            return
        text = str(message.text or "").strip()
        if any(ignored in text.lower() for ignored in IGNORED_CONSOLE_ERRORS):
            return
        self.console_errors.append(text[:500])

    def _request_failed(self, request) -> None:
        if _is_critical_url(request.url, self.base_origin):
            reason = request.failure or "request failed"
            self.failed_requests.append(f"{_safe_path(request.url)}: {reason}"[:500])

    def _response(self, response) -> None:
        if not _is_critical_url(response.url, self.base_origin):
            return
        if response.status >= 500:
            self.bad_responses.append(f"HTTP {response.status} {_safe_path(response.url)}")
        elif response.status == 404 and response.request.resource_type in {
            "document",
            "script",
            "stylesheet",
        }:
            self.bad_responses.append(f"HTTP 404 {_safe_path(response.url)}")

    def assert_clean(self, label: str) -> None:
        problems = {
            "console errors": self.console_errors,
            "page errors": self.page_errors,
            "failed critical requests": self.failed_requests,
            "bad critical responses": self.bad_responses,
        }
        details = [f"{name}: {values}" for name, values in problems.items() if values]
        if details:
            raise SmokeFailure(f"{label}: " + "; ".join(details))


@dataclass
class SmokeReport:
    checks: list[str] = field(default_factory=list)

    def passed(self, message: str) -> None:
        self.checks.append(message)
        print(f"PASS {message}")


def _normalise_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, a query, or a fragment")
    return raw


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _safe_path(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.path or "/"


def _is_critical_url(url: str, base_origin: str) -> bool:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin == base_origin or parsed.netloc == "challenges.cloudflare.com"


def _url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url}/", path.lstrip("/"))


def _goto(page: Page, base_url: str, path: str, timeout_ms: int):
    response = page.goto(_url(base_url, path), wait_until="domcontentloaded", timeout=timeout_ms)
    if response is None:
        raise SmokeFailure(f"{path}: navigation returned no response")
    if response.status >= 400:
        raise SmokeFailure(f"{path}: unexpected HTTP {response.status}")
    return response


def _assert_visible(locator, label: str) -> None:
    if locator.count() == 0 or not locator.first.is_visible():
        raise SmokeFailure(f"{label}: expected visible UI was not found")


def interaction_blockers(page: Page, target_selector: str) -> list[str]:
    """Return reasons a target cannot receive normal pointer interaction."""
    return page.locator(target_selector).evaluate(
        """target => {
            const reasons = [];
            const style = getComputedStyle(target);
            const bodyStyle = getComputedStyle(document.body);
            if (style.pointerEvents === 'none') reasons.push('target pointer-events is none');
            if (bodyStyle.pointerEvents === 'none') reasons.push('body pointer-events is none');
            const rect = target.getBoundingClientRect();
            const x = Math.min(innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
            const y = Math.min(innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
            const top = document.elementFromPoint(x, y);
            if (top && top !== target && !target.contains(top)) {
                reasons.push(`target obscured by ${top.tagName.toLowerCase()}#${top.id || ''}.${String(top.className || '')}`);
            }
            for (const el of document.querySelectorAll('body *')) {
                if (el === target || target.contains(el) || !el.getBoundingClientRect) continue;
                const s = getComputedStyle(el);
                if (!['fixed', 'absolute'].includes(s.position) || s.pointerEvents === 'none' || s.visibility === 'hidden' || s.display === 'none') continue;
                const r = el.getBoundingClientRect();
                if (r.width >= innerWidth * 0.9 && r.height >= innerHeight * 0.9 && Number(s.opacity || 1) > 0.01) {
                    reasons.push(`full-page pointer layer ${el.tagName.toLowerCase()}#${el.id || ''}.${String(el.className || '')}`);
                }
            }
            return [...new Set(reasons)];
        }"""
    )


def _assert_interactable(page: Page, selector: str, label: str) -> None:
    locator = page.locator(selector)
    _assert_visible(locator, label)
    locator.scroll_into_view_if_needed()
    reasons = interaction_blockers(page, selector)
    if reasons:
        raise SmokeFailure(f"{label}: {'; '.join(reasons)}")


def _assert_no_global_loading_blocker(page: Page) -> None:
    blockers = page.evaluate(
        """() => [...document.querySelectorAll('[aria-busy="true"], .loading, .loader, .spinner')]
            .filter(el => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && r.width >= innerWidth * .8 && r.height >= innerHeight * .8;
            })
            .map(el => `${el.tagName.toLowerCase()}#${el.id || ''}.${String(el.className || '')}`)"""
    )
    if blockers:
        raise SmokeFailure(f"full-page loading state remained active: {blockers}")


def _desktop_smoke(browser: Browser, base_url: str, timeout_ms: int, report: SmokeReport) -> None:
    context = browser.new_context(viewport=DESKTOP_VIEWPORT)
    signals = BrowserSignals(_origin(base_url))
    page = context.new_page()
    signals.attach(page)
    try:
        _goto(page, base_url, "/healthz", timeout_ms)
        if page.locator("body").inner_text().lower().find('"status":"ok"') < 0 and "ok" not in page.locator("body").inner_text().lower():
            raise SmokeFailure("/healthz did not report ok")
        report.passed("health endpoint")

        _goto(page, base_url, "/", timeout_ms)
        _assert_visible(page.get_by_role("heading", name="Learn to think like an investor."), "homepage hero")
        _assert_no_global_loading_blocker(page)
        _assert_interactable(page, "#smartSearchInput", "homepage stock search")
        report.passed("desktop homepage renders without an interaction blocker")

        nav_checks = (
            ("Search", "#stock-search", None),
            ("Investment Compass", "/beginner", "Build your starter profile"),
            ("How It Works", "/how-it-works", "How StockRadar Works"),
            ("Newsletter", "/newsletter", "StockRadar Weekly"),
            ("Premium", "/upgrade", "Understand the signal before you act."),
            ("Login", "/login", "🔐 Login"),
        )
        for label, destination, heading in nav_checks:
            _goto(page, base_url, "/", timeout_ms)
            nav = page.get_by_role("navigation", name="Primary navigation")
            link = nav.get_by_role("link", name=label, exact=True)
            _assert_visible(link, f"{label} navigation")
            link.click(timeout=timeout_ms)
            if destination.startswith("#"):
                page.wait_for_function(
                    "selector => location.hash === selector && document.querySelector(selector)",
                    arg=destination,
                    timeout=timeout_ms,
                )
            else:
                page.wait_for_url(f"**{destination}", wait_until="domcontentloaded", timeout=timeout_ms)
                if heading:
                    _assert_visible(page.get_by_role("heading", name=heading, exact=True), f"{label} destination")
        report.passed("desktop top navigation destinations")

        for query, expected_path in (("MSFT", "/stock/MSFT"), ("Microsoft", "/stock/MSFT"), ("AAPL", "/stock/AAPL")):
            _goto(page, base_url, "/", timeout_ms)
            search = page.get_by_role("searchbox", name="Search a stock or ETF")
            search.fill(query)
            if search.input_value() != query:
                raise SmokeFailure(f"stock search did not retain {query}")
            page.get_by_role("button", name="View free report").click(timeout=timeout_ms)
            page.wait_for_url(f"**{expected_path}", wait_until="domcontentloaded", timeout=timeout_ms)
        _assert_visible(page.get_by_role("heading", name="Apple Inc. (AAPL) Stock Detail"), "Apple report")
        report.passed("desktop MSFT, Microsoft, and AAPL search navigation")

        _goto(page, base_url, "/", timeout_ms)
        search = page.get_by_role("searchbox", name="Search a stock or ETF")
        search.fill("MSFT")
        page.get_by_role("button", name="View free report").click(timeout=timeout_ms)
        page.wait_for_url("**/stock/MSFT", wait_until="domcontentloaded", timeout=timeout_ms)
        _assert_visible(page.get_by_role("heading", name="Microsoft Corporation (MSFT) Stock Detail"), "Microsoft report")
        logo = page.locator('img[alt="Microsoft Corporation logo"]').first
        _assert_visible(logo, "Microsoft company logo")
        if not logo.evaluate("img => img.complete && img.naturalWidth > 0"):
            raise SmokeFailure("Microsoft company logo did not load")
        _assert_visible(page.locator("#stock-chart-shell"), "Microsoft stock chart")
        if page.locator("#stockChart").count() != 1:
            raise SmokeFailure("Microsoft stock chart canvas did not initialize")
        report.passed("Microsoft identity, logo, report, and chart")

        _goto(page, base_url, "/newsletter", timeout_ms)
        _assert_visible(page.locator('input[type="email"]'), "newsletter signup input")
        _assert_visible(page.get_by_role("button", name="Join Free"), "newsletter signup control")
        if _origin(base_url) == PRODUCTION_URL:
            _assert_visible(page.locator(".cf-turnstile"), "production Turnstile widget")
            if page.locator('script[src*="challenges.cloudflare.com/turnstile"]').count() != 1:
                raise SmokeFailure("production Turnstile script is missing")
        latest = _goto(page, base_url, "/newsletter/latest", timeout_ms)
        if "text/html" not in (latest.headers.get("content-type") or ""):
            raise SmokeFailure("/newsletter/latest did not return HTML")
        _assert_visible(page.get_by_role("heading").first, "latest newsletter issue")
        rss = _goto(page, base_url, "/newsletter/rss", timeout_ms)
        content_type = rss.headers.get("content-type") or ""
        if not any(value in content_type for value in ("rss", "xml")):
            raise SmokeFailure("/newsletter/rss did not return an RSS/XML content type")
        report.passed("newsletter signup UI, Turnstile, latest issue, and RSS")
        signals.assert_clean("desktop browser")
        report.passed("desktop console and critical network checks")
    finally:
        context.close()


def _mobile_smoke(browser: Browser, base_url: str, timeout_ms: int, report: SmokeReport) -> None:
    context = browser.new_context(viewport=MOBILE_VIEWPORT, is_mobile=True)
    signals = BrowserSignals(_origin(base_url))
    page = context.new_page()
    signals.attach(page)
    try:
        _goto(page, base_url, "/", timeout_ms)
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
        if overflow:
            raise SmokeFailure("mobile homepage has horizontal overflow")
        menu = page.get_by_role("button", name="Menu")
        _assert_visible(menu, "mobile menu")
        menu.click(timeout=timeout_ms)
        nav = page.get_by_role("navigation", name="Primary navigation")
        for label in ("Search", "Investment Compass", "How It Works", "Newsletter", "Premium", "Login"):
            _assert_visible(nav.get_by_role("link", name=label, exact=True), f"mobile {label} navigation")
        report.passed("390x844 navigation and horizontal layout")

        menu.click(timeout=timeout_ms)
        search = page.get_by_role("searchbox", name="Search a stock or ETF")
        search.fill("MSFT")
        if search.input_value() != "MSFT":
            raise SmokeFailure("mobile stock search did not retain MSFT")
        page.get_by_role("button", name="View free report").click(timeout=timeout_ms)
        page.wait_for_url("**/stock/MSFT", wait_until="domcontentloaded", timeout=timeout_ms)
        _assert_visible(page.get_by_role("heading", name="Microsoft Corporation (MSFT) Stock Detail"), "mobile Microsoft report")
        _assert_visible(page.locator("#stock-chart-shell"), "mobile Microsoft stock chart")
        report.passed("390x844 MSFT search-to-report journey")

        for label, destination in (("Login", "/login"), ("Premium", "/upgrade")):
            _goto(page, base_url, "/", timeout_ms)
            page.get_by_role("button", name="Menu").click(timeout=timeout_ms)
            page.get_by_role("navigation", name="Primary navigation").get_by_role(
                "link", name=label, exact=True
            ).click(timeout=timeout_ms)
            page.wait_for_url(f"**{destination}", wait_until="domcontentloaded", timeout=timeout_ms)
        report.passed("390x844 Login and Premium navigation")
        signals.assert_clean("mobile browser")
        report.passed("mobile console and critical network checks")
    finally:
        context.close()


def _self_test(playwright: Playwright, headed: bool) -> None:
    browser = playwright.chromium.launch(headless=not headed)
    page = browser.new_page(viewport=DESKTOP_VIEWPORT)
    try:
        page.set_content(
            """<!doctype html><style>*{box-sizing:border-box}body{margin:0}</style>
            <main><label for='search'>Search</label><input id='search'><button>Open</button></main>"""
        )
        if interaction_blockers(page, "#search"):
            raise SmokeFailure("self-test rejected a usable page")
        page.set_content(
            """<!doctype html><style>*{box-sizing:border-box}body{margin:0}
            #blocker{position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.01)}</style>
            <main><label for='search'>Search</label><input id='search'><button>Open</button></main>
            <div id='blocker' aria-label='stuck loading overlay'></div>"""
        )
        blockers = interaction_blockers(page, "#search")
        if not blockers:
            raise SmokeFailure("self-test failed to detect a full-page pointer-blocking overlay")
        print("PASS self-test accepts a usable page")
        print("PASS self-test rejects a health-200-style page with blocked interaction")
    finally:
        browser.close()


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("STOCKRADAR_SMOKE_BASE_URL", PRODUCTION_URL),
        help="Local or production base URL (default: %(default)s)",
    )
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--headed", action="store_true", help="Show Chromium while checks run")
    parser.add_argument("--self-test", action="store_true", help="Prove the interaction-blocker detector works")
    parser.add_argument("--json-output", help="Optional path for a credential-free pass report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        base_url = _normalise_base_url(args.base_url)
        with sync_playwright() as playwright:
            if args.self_test:
                _self_test(playwright, args.headed)
                return 0
            browser = playwright.chromium.launch(headless=not args.headed)
            report = SmokeReport()
            try:
                _desktop_smoke(browser, base_url, args.timeout_ms, report)
                _mobile_smoke(browser, base_url, args.timeout_ms, report)
            finally:
                browser.close()
        payload = {"status": "passed", "base_origin": _origin(base_url), "checks": report.checks}
        if args.json_output:
            with open(args.json_output, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
        print(f"PASS StockRadar smoke gate ({len(report.checks)} checks)")
        return 0
    except (PlaywrightError, SmokeFailure, TimeoutError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
