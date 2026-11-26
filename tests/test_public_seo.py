from html.parser import HTMLParser
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pytest
import app


class HeadMetadata(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.meta = {}
        self.canonicals = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("name") or attrs.get("property")] = attrs.get("content")
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs["href"])


@pytest.mark.parametrize("symbol,canonical,label", [
    ("aapl", "AAPL", "Apple Inc. (AAPL)"),
    ("AAPL", "AAPL", "Apple Inc. (AAPL)"),
    ("BAE.L", "BA.L", "BAE Systems (BA.L)"),
    ("MSFT", "MSFT", 'Microsoft & Research "Example" (MSFT)'),
])
def test_public_stock_metadata_uses_canonical_identity(symbol, canonical, label):
    with (
        patch.object(app, "safe_history", return_value=None),
        patch.object(app, "get_dividend_context", return_value={}),
        patch.object(app, "premium_has_access", return_value=False),
        patch.dict(app.app.jinja_env.globals, {"stock_display_label": lambda symbol: label}),
        patch.object(app, "get_recommendations", return_value=[]),
        patch.object(app, "get_stock_universe", return_value=[]),
    ):
        response = app.app.test_client().get(
            f"/stock/{symbol}?range=1y&source=example", follow_redirects=True,
        )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    head = HeadMetadata(page)
    url = f"https://www.stockradarhq.com/stock/{canonical}"
    assert head.canonicals == [url]
    assert head.meta["og:url"] == url
    assert head.meta["og:title"] == label + " Stock Research & Analysis | StockRadar HQ"
    assert head.meta["twitter:title"] == head.meta["og:title"]
    assert label in head.meta["description"]
    assert "public signal and confidence preview" in head.meta["description"]
    assert head.meta["description"] == head.meta["og:description"] == head.meta["twitter:description"]
    assert "Premium locked preview" in page


def test_opportunities_public_preview_metadata():
    with (
        patch.object(app, "premium_has_access", return_value=False),
        patch.object(app, "get_recommendations", return_value=[]),
        patch.object(app, "newsletter_storage_load") as load,
    ):
        response = app.app.test_client().get("/opportunities?source=example")
    assert response.status_code == 200
    head = HeadMetadata(response.get_data(as_text=True))
    assert head.canonicals == ["https://www.stockradarhq.com/opportunities"]
    assert head.meta["og:url"] == head.canonicals[0]
    assert head.meta["og:title"] == head.meta["twitter:title"]
    assert head.meta["description"] == head.meta["og:description"] == head.meta["twitter:description"]
    assert "Full ranked research requires Premium." in head.meta["description"]
    assert b"Premium preview" in response.data
    load.assert_not_called()


def test_sitemap_adds_opportunities_and_omits_unsupported_dates():
    with patch.object(app, "get_stock_universe", return_value=[{"ticker": "AAPL"}, {"ticker": "AAPL"}]):
        response = app.app.test_client().get("/sitemap.xml")
    assert response.status_code == 200
    root = ET.fromstring(response.data)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in root.findall("s:url/s:loc", ns)]
    for path in ("/", "/how-it-works", "/upgrade", "/opportunities", "/stock/AAPL"):
        assert urls.count("https://www.stockradarhq.com" + path) == 1
    assert not root.findall("s:url/s:lastmod", ns)


@pytest.fixture
def offline_stock(monkeypatch):
    monkeypatch.setattr(app, "safe_history", lambda *a, **kw: None)
    monkeypatch.setattr(app, "get_dividend_context", lambda *a: {})
    monkeypatch.setattr(app, "get_recommendations", lambda: [])
    monkeypatch.setattr(app, "premium_has_access", lambda: False)
    monkeypatch.setattr(app, "get_stock_universe", lambda: [{"ticker": "AAPL", "name": "Apple"}])


@pytest.mark.parametrize("range_key", list(app.CHART_RANGES) + ["invalid"])
def test_every_chart_range_remains_usable_with_one_canonical(offline_stock, range_key):
    response = app.app.test_client().get("/stock/AAPL?range=" + range_key)
    assert response.status_code == 200
    assert HeadMetadata(response.get_data(as_text=True)).canonicals == [
        "https://www.stockradarhq.com/stock/AAPL"]
    assert "X-Robots-Tag" not in response.headers


@pytest.mark.parametrize("source,target", [("aapl", "AAPL"), ("BAE.L", "BA.L"), ("Apple", "AAPL")])
def test_ticker_aliases_redirect_permanently_preserving_query(offline_stock, source, target):
    response = app.app.test_client().get(f"/stock/{source}?range=6mo&source=research")
    assert response.status_code == 301
    assert response.headers["Location"] == f"/stock/{target}?range=6mo&source=research"


def test_sitemap_canonicalises_and_deduplicates_aliases():
    with patch.object(app, "get_stock_universe", return_value=[
        {"ticker": "BAE.L"}, {"ticker": "BA.L"}, {"ticker": "aapl"}, {"ticker": "AAPL"},
    ]):
        response = app.app.test_client().get("/sitemap.xml")
    urls = [node.text for node in ET.fromstring(response.data).iter(
        "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert len(urls) == len(set(urls))
    assert "https://www.stockradarhq.com/stock/BA.L" in urls
    assert "https://www.stockradarhq.com/stock/AAPL" in urls
    assert not any("?" in url or "BAE.L" in url or "/premium-decision/" in url
                   or url.endswith(("/manage-subscription", "/feedback")) for url in urls)


@pytest.mark.parametrize("path", ["/premium-decision/AAPL", "/api/market-news", "/admin/unknown",
                                     "/login", "/signup", "/healthz", "/universe?q=Apple"])
def test_utility_and_locked_pages_are_not_search_landing_pages(offline_stock, path):
    with patch.object(app, "build_intelligence_report") as report:
        response = app.app.test_client().get(path)
    assert response.headers["X-Robots-Tag"] == "noindex, follow"
    report.assert_not_called()


def test_unknown_ticker_preserves_page_without_indexing_generated_content(offline_stock):
    response = app.app.test_client().get("/stock/UNKNOWN123")
    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex, follow"


def test_structured_data_is_public_metadata_only_and_script_safe(offline_stock):
    import json
    import re
    with patch.dict(app.app.jinja_env.globals, {"stock_display_label": lambda s: '</script><script>alert("x")</script>'}):
        response = app.app.test_client().get("/stock/AAPL")
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', response.get_data(as_text=True))
    data = json.loads(match.group(1))
    assert data["@type"] == "WebPage"
    assert data["url"] == "https://www.stockradarhq.com/stock/AAPL"
    assert set(data) == {"@context", "@type", "name", "description", "url", "isPartOf"}
    assert "</script>" not in match.group(1)


def test_robots_allows_canonical_and_noindex_discovery():
    response = app.app.test_client().get("/robots.txt")
    assert "Allow: /" in response.get_data(as_text=True)
    assert "Disallow:" not in response.get_data(as_text=True)
    assert "Sitemap: https://www.stockradarhq.com/sitemap.xml" in response.get_data(as_text=True)


def test_latest_newsletter_adds_metadata_without_changing_artifact_or_body(monkeypatch):
    html = '<html><head><title>Published weekly issue</title></head><body id="stockradar-primary-navigation-styles"><section id="newsletter-research">Published research</section></body></html>'
    artifact = {"html": html}
    monkeypatch.setattr(app, "load_latest_published_newsletter_artifact", lambda: artifact)
    response = app.app.test_client().get('/newsletter/latest')
    page = response.get_data(as_text=True)
    head = HeadMetadata(page)
    assert head.canonicals == ['https://www.stockradarhq.com/newsletter/latest']
    assert 'StockRadar HQ' in head.meta['description']
    assert page.split('<body', 1)[1] == html.split('<body', 1)[1]
    assert artifact['html'] == html
    assert 'X-Robots-Tag' not in response.headers


def test_latest_newsletter_does_not_duplicate_existing_metadata(monkeypatch):
    html = '<html><head><link rel="canonical" href="https://www.stockradarhq.com/newsletter/latest"><meta name="description" content="Existing published description"></head><body id="stockradar-primary-navigation-styles"><section id="newsletter-research">Published research</section></body></html>'
    monkeypatch.setattr(app, "load_latest_published_newsletter_artifact", lambda: {"html": html})
    page = app.app.test_client().get('/newsletter/latest').get_data(as_text=True)
    head = HeadMetadata(page)
    assert head.canonicals == ['https://www.stockradarhq.com/newsletter/latest']
    assert head.meta['description'] == 'Existing published description'


def test_home_metadata_identifies_investment_platform_and_site_name():
    import json
    import re
    with app.app.test_request_context('/'):
        head_html = app.render_template_string(app.html.split('</head>', 1)[0] + '</head>')
    head = HeadMetadata(head_html)
    assert head.canonicals == ['https://www.stockradarhq.com/']
    assert head.meta['og:title'] == 'StockRadar HQ — Stock Market Research & Investment Analysis'
    assert head.meta['description'] == head.meta['og:description'] == head.meta['twitter:description']
    assert '£5/month' in head.meta['description']
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', head_html)
    assert json.loads(match.group(1))['name'] == 'StockRadar HQ'
