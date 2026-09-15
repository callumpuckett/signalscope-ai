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
    assert head.meta["og:title"] == label + " Stock Detail"
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
