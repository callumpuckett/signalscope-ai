"""Conversion copy must reflect permissions without fetching private research."""
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest
import app


def test_landing_is_static_beyond_existing_entitlement_check(monkeypatch):
    monkeypatch.setattr(app, 'premium_has_access', lambda: False)
    for name in ('build_premium_decision_brief', 'build_intelligence_report',
                 'get_recommendations', 'get_stock_ai_context', 'get_cached_dashboard_data',
                 'fetch_url_json', 'get_dividend_context'):
        monkeypatch.setattr(app, name, Mock(side_effect=AssertionError(name + ' must not run')))
    response = app.app.test_client().get('/upgrade')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    assert soup.h1.get_text(' ', strip=True) == 'Stop Guessing. Start Understanding.'
    assert soup.select_one('#report-guide')
    assert not soup.select_one('#intelligence-report')  # no private report masquerading as a sample
    assert 'Report guide · not live stock analysis' in soup.get_text()
    rows = {r.th.get_text(): r.get_text(' ', strip=True) for r in soup.select('.plan-comparison tbody tr')}
    assert 'Heuristic score preview in Opportunities' in rows['Decision Score']
    assert '£1,000 illustration' in rows['What-If']
    assert 'custom investment amount' in rows['What-If']
    assert 'holding count' in rows['Portfolio Intelligence']
    assert 'Full AI Watchlist' in rows['Watchlists']
    assert len(rows) == 8
    assert 'Dividend Dip Tracker' not in soup.get_text()


def test_checkout_forms_keep_csrf_and_existing_post_destination(monkeypatch):
    monkeypatch.setattr(app, 'stripe_checkout_configured', lambda: True)
    monkeypatch.setattr(app, 'premium_has_access', lambda: False)
    monkeypatch.setitem(app.app.config, 'WTF_CSRF_ENABLED', True)
    client = app.app.test_client()
    soup = BeautifulSoup(client.get('/upgrade').data, 'html.parser')
    forms = soup.select('form[action="/create-checkout-session"]')
    assert len(forms) == 2
    for form in forms:
        assert form['method'].upper() == 'POST'
        assert form.select_one('input[name="csrf_token"]')['value']
    create = Mock(return_value=type('Checkout', (), {'url': 'https://checkout.stripe.test/approved'})())
    monkeypatch.setattr(app.stripe.checkout.Session, 'create', create)
    # No CSRF bypass introduced by the marketing form.
    assert client.post('/create-checkout-session').status_code == 400
    create.assert_not_called()
    token = forms[0].select_one('input[name="csrf_token"]')['value']
    response = client.post('/create-checkout-session', data={'csrf_token': token})
    assert response.status_code == 303
    assert response.headers['Location'] == 'https://checkout.stripe.test/approved'
    assert create.call_args.kwargs['mode'] == 'subscription'
    assert create.call_args.kwargs['line_items'] == [{'price': app.STRIPE_PRICE_ID, 'quantity': 1}]
    assert client.get('/create-checkout-session').headers['Location'].endswith('/upgrade')


@pytest.mark.parametrize('premium', [False, True])
def test_contextual_prompt_is_single_and_does_not_expose_report(monkeypatch, premium):
    monkeypatch.setattr(app, 'premium_has_access', lambda: premium)
    monkeypatch.setattr(app, 'stock_history', lambda *a: {'ok': False, 'error': 'Unavailable', 'points': [], 'direction': 'hold'})
    monkeypatch.setattr(app, 'stock_lifetime_growth', lambda *a: {})
    monkeypatch.setattr(app, 'get_dividend_context', lambda *a: {})
    soup = BeautifulSoup(app.app.test_client().get('/stock/MSFT').data, 'html.parser')
    prompts = soup.select('.stock-locked-preview')
    assert len(prompts) == (0 if premium else 1)
    assert bool(soup.select_one('#intelligence-report')) == premium
    if not premium:
        assert len(prompts[0].select('a[href="/upgrade"]')) == 1
        assert prompts[0].select_one('a[href="/premium-decision/MSFT"]')
        assert 'Explore Premium' not in soup.select_one('.ai-grid').get_text()
    else:
        assert not soup.select('a[href="/upgrade"]')


def test_landing_preview_and_tool_links_are_existing_internal_routes(monkeypatch):
    monkeypatch.setattr(app, 'premium_has_access', lambda: False)
    client = app.app.test_client()
    soup = BeautifulSoup(client.get('/upgrade').data, 'html.parser')
    for href in ('/premium-decision/MSFT', '/portfolio-fit', '/compare/MSFT/GOOGL'):
        assert soup.select_one(f'a[href="{href}"]')
        response = client.get(href)
        assert response.status_code == 200
        assert b'/upgrade' in response.data
    preview = client.get('/premium-decision/MSFT').get_data(as_text=True)
    assert 'id="intelligence-report"' not in preview
