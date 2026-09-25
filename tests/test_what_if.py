import copy
import time
from decimal import Decimal
from unittest.mock import Mock

import pytest
import app


@pytest.fixture
def outlook():
    now = time.time()
    return app.build_return_outlook({
        'quoteType': 'EQUITY', 'currency': 'USD', 'regularMarketPrice': 100,
        'regularMarketTime': now, 'targetMeanPrice': 114.6,
        'targetLowPrice': 80, 'targetHighPrice': 150, 'numberOfAnalystOpinions': 12,
    }, now=now)


@pytest.fixture
def page(monkeypatch, outlook):
    monkeypatch.setattr(app, 'get_stock_universe', lambda: [
        {'ticker': 'MSFT', 'name': 'Microsoft Corporation', 'search_text': 'msft microsoft corporation'},
        {'ticker': 'AAPL', 'name': 'Apple Inc.', 'search_text': 'aapl apple inc.'},
    ])
    monkeypatch.setattr(app, 'premium_entitlement_record', lambda **kwargs: None)
    provider = Mock(return_value=outlook)
    monkeypatch.setattr(app, 'what_if_return_outlook', provider)
    return app.app.test_client(), provider


def premium(monkeypatch):
    monkeypatch.setattr(app, 'premium_entitlement_record', lambda **kwargs: {'premium_active': True, 'entitlement_version': 1})


def test_page_search_selection_and_unavailable_symbol(page):
    client, provider = page
    response = client.get('/what-if')
    assert response.status_code == 200
    assert b'What could your investment look like?' in response.data
    provider.assert_not_called()
    found = client.get('/what-if?q=Microsoft').get_data(as_text=True)
    assert '/what-if?symbol=MSFT' in found
    provider.assert_not_called()
    assert b'No matching stocks found' in client.get('/what-if?q=does-not-exist').data
    assert b'Choose a supported stock' in client.get('/what-if?symbol=INVALID').data
    provider.assert_not_called()
    response = client.get('/what-if?symbol=MSFT')
    assert response.status_code == 200
    provider.assert_called_once_with('MSFT')
    assert '£1,146' in response.get_data(as_text=True)


@pytest.mark.parametrize('query', ['', '&amount=250', '&amount=custom&custom_amount=9999'])
def test_free_always_gets_full_thousand_pound_result_before_upgrade(page, query):
    client, _ = page
    html = client.get('/what-if?symbol=MSFT' + query).get_data(as_text=True)
    assert '£1,000 <span>today' in html
    assert '£1,146' in html and '+£146 (+14.6%)' in html
    assert html.index('£1,146') < html.index('Want to explore different investment amounts?')
    assert 'name="amount"' not in html
    assert 'name="custom_amount"' not in html
    assert 'Analyst targets are not forecasts or guarantees' in html
    assert 'Dividends are excluded.' in html


@pytest.mark.parametrize(('amount', 'expected'), [('250', '£286.50'), ('500', '£573'), ('1000', '£1,146'), ('custom&custom_amount=1234.56', '£1,414.81')])
def test_premium_amount_controls_and_calculation(page, monkeypatch, amount, expected):
    premium(monkeypatch)
    html = page[0].get('/what-if?symbol=MSFT&amount=' + amount).get_data(as_text=True)
    for value in ['250', '500', '1000', 'custom']:
        assert f'value="{value}"' in html
    assert expected in html
    assert 'How Return Outlook works' in html
    assert 'Unlock What If?' not in html


@pytest.mark.parametrize('raw', ['0', '-1', 'NaN', 'Infinity', '1e3', '1.001', '1000000001', '', '<script>'])
def test_invalid_premium_amount_makes_no_provider_request(page, monkeypatch, raw):
    premium(monkeypatch)
    response = page[0].get('/what-if', query_string={'symbol': 'MSFT', 'amount': 'custom', 'custom_amount': raw})
    assert b'role="alert"' in response.data
    assert b'class="illustrative-value"' not in response.data
    page[1].assert_not_called()


def test_negative_outlook_uses_existing_percentage_and_no_invented_scenarios(page, outlook):
    outlook['metric'] = '-14.6%'
    html = page[0].get('/what-if?symbol=MSFT').get_data(as_text=True)
    assert '£854' in html
    assert '−£146 (-14.6%)' in html
    assert 'transformation negative' in html
    assert 'Bull case' not in html and 'Downside case' not in html


@pytest.mark.parametrize('invalid', ['empty', 'expired', 'future', 'malformed'])
def test_unavailable_outlook_switches_to_labelled_hypothetical_mode(page, outlook, invalid):
    if invalid == 'empty':
        outlook['cases'] = {}
    elif invalid == 'expired':
        outlook['quote_timestamp'] = time.time() - 8 * 86400
    elif invalid == 'future':
        outlook['quote_timestamp'] = time.time() + 86400
    else:
        outlook['metric'] = 'NaN%'
    html = page[0].get('/what-if?symbol=MSFT').get_data(as_text=True)
    assert 'Explore a hypothetical scenario' in html
    assert '£1,100' in html
    assert 'not an analyst target, StockRadar forecast or guarantee' in html
    assert 'Based on the current 12-month analyst-target outlook.' not in html


@pytest.mark.parametrize('location', ['public', 'dashboard', 'app'])
def test_navigation_reuses_existing_mobile_header(location):
    with app.app.test_request_context('/what-if'):
        html = app.stockradar_header_navigation(location)
    assert html.count('href="/what-if"') == 1
    assert 'What If?' in html
    assert 'data-stockradar-menu-toggle' in html
    assert 'aria-expanded="false"' in html
    assert 'data-stockradar-menu' in html


@pytest.fixture
def cache_sources(monkeypatch):
    monkeypatch.setattr(app, 'DIVIDEND_CONTEXT_CACHE', {})
    monkeypatch.setattr(app, 'YAHOO_COOLDOWN_UNTIL', 0)
    monkeypatch.setattr(app, 'OPPORTUNITY_PAGE_CACHE', None)
    storage = Mock(return_value={})
    provider = Mock(return_value=app.build_return_outlook({}))
    monkeypatch.setattr(app, 'newsletter_storage_load', storage)
    monkeypatch.setattr(app, 'opportunity_return_outlook', provider)
    return storage, provider


def test_cached_context_reused_for_all_amounts_without_new_fetch(cache_sources, outlook):
    storage, provider = cache_sources
    app.DIVIDEND_CONTEXT_CACHE['MSFT'] = {'timestamp': time.time(), 'context': {
        'income_status': app.INCOME_STATUS_AVAILABLE, 'return_outlook': copy.deepcopy(outlook),
    }}
    for amount in ['250', '500', '1000', '1234.56']:
        cached = app.what_if_return_outlook('MSFT')
        assert app.what_if_illustration(cached, Decimal(amount))
        assert cached['quote_timestamp'] == outlook['quote_timestamp']
    provider.assert_not_called()
    storage.assert_not_called()


@pytest.mark.parametrize('memory', [False, True])
def test_saved_opportunity_outlook_reused_without_enrichment_or_provider(cache_sources, outlook, monkeypatch, memory):
    storage, provider = cache_sources
    snapshot = {'opportunities': [{'ticker': 'MSFT', 'return_outlook': outlook}]}
    state = {'snapshots': {'2026-09-24': snapshot}}
    if memory:
        monkeypatch.setattr(app, 'OPPORTUNITY_PAGE_CACHE', ('2026-09-24', (snapshot, state)))
    else:
        storage.return_value = state
    assert app.what_if_return_outlook('MSFT')['metric'] == '+14.6%'
    provider.assert_not_called()
    if memory:
        storage.assert_not_called()
    else:
        storage.assert_called_once_with('opportunity_radar')


def test_missing_data_uses_existing_outlook_fetch_once(cache_sources):
    storage, provider = cache_sources
    assert not app.what_if_return_outlook('MSFT')['cases']
    provider.assert_called_once_with('MSFT')


def test_unavailable_cache_honours_retry_without_provider(cache_sources):
    _, provider = cache_sources
    app.DIVIDEND_CONTEXT_CACHE['MSFT'] = {'timestamp': time.time(), 'context': {
        'income_status': app.INCOME_STATUS_UNAVAILABLE, 'return_outlook': app.build_return_outlook({}),
    }}
    assert not app.what_if_return_outlook('MSFT')['cases']
    provider.assert_not_called()


def test_amount_changes_share_existing_yahoo_metadata_cache(monkeypatch, outlook):
    premium(monkeypatch)
    monkeypatch.setattr(app, 'DIVIDEND_CONTEXT_CACHE', {})
    monkeypatch.setattr(app, 'OPPORTUNITY_PAGE_CACHE', None)
    monkeypatch.setattr(app, 'YAHOO_COOLDOWN_UNTIL', 0)
    monkeypatch.setattr(app, 'newsletter_storage_load', Mock(return_value={}))
    refresh = Mock(return_value={'income_status': app.INCOME_STATUS_AVAILABLE, 'return_outlook': outlook})
    monkeypatch.setattr(app, '_fetch_dividend_context', refresh)
    client = app.app.test_client()
    for amount in ('250', '500', '1000', 'custom&custom_amount=750'):
        assert client.get('/what-if?symbol=MSFT&amount=' + amount).status_code == 200
    refresh.assert_called_once_with('MSFT')


def test_expired_saved_outlook_uses_existing_refresh(cache_sources, outlook):
    storage, provider = cache_sources
    outlook['quote_timestamp'] = time.time() - 8 * 86400
    storage.return_value = {'snapshots': {'old': {'opportunities': [{'ticker': 'MSFT', 'return_outlook': outlook}]}}}
    app.what_if_return_outlook('MSFT')
    provider.assert_called_once_with('MSFT')


def test_public_header_keeps_what_if_among_desktop_tabs(monkeypatch):
    monkeypatch.setattr(app, 'premium_entitlement_record', lambda **kwargs: None)
    with app.app.test_request_context('/'):
        html = app.stockradar_header_navigation('public')
    assert 'class="public-header-inner"' in html
    assert 'href="/what-if"' in html


def test_zero_outlook_keeps_investment_unchanged(outlook):
    outlook['metric'] = '+0.0%'
    result = app.what_if_illustration(outlook, Decimal('1000'))
    assert result['value'] == '£1,000'
    assert result['change'] == '+£0'


@pytest.mark.parametrize('query', ['Microsoft', 'MSFT', '0QYP.L'])
def test_what_if_prefers_supported_tracked_company_listing(page, monkeypatch, query):
    rows = [app.normalise_universe_row({'ticker': ticker, 'name': 'Microsoft Corporation'})
            for ticker in ['0QYP.L', 'MSFT']]
    monkeypatch.setattr(app, 'get_stock_universe', lambda: rows)
    html = page[0].get('/what-if', query_string={'q': query}).get_data(as_text=True)
    assert html.count('/what-if?symbol=MSFT') == 1
    assert '/what-if?symbol=0QYP.L' not in html
    assert [row['ticker'] for row in app.search_stock_universe('Microsoft')] == ['0QYP.L', 'MSFT']
    page[1].assert_not_called()


def test_what_if_keeps_distinct_names_and_share_classes(monkeypatch):
    rows = [app.normalise_universe_row({'ticker': ticker, 'name': name}) for ticker, name in [
        ('AAA', 'Example Class A'), ('BBB', 'Example Class B'), ('CCC', 'Example Other'),
        ('DDD', 'Only Alternate'),
    ]]
    monkeypatch.setattr(app, 'get_stock_universe', lambda: rows)
    assert [row['ticker'] for row in app.what_if_search_stocks('Example')] == ['AAA', 'BBB', 'CCC']
    assert app.what_if_search_stocks('Only')[0]['ticker'] == 'DDD'


@pytest.mark.parametrize('amount', ['250', '500', '1000', 'custom'])
def test_custom_input_initial_visibility_and_validation_state(page, monkeypatch, amount):
    from html.parser import HTMLParser
    class Fields(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('class') == 'custom-amount-field':
                self.wrapper = attrs
            if attrs.get('id') == 'custom-amount':
                self.input = attrs
    premium(monkeypatch)
    html = page[0].get('/what-if', query_string={
        'symbol': 'MSFT', 'amount': amount, 'custom_amount': '1234.56',
    }).get_data(as_text=True)
    fields = Fields()
    fields.feed(html)
    assert ('hidden' in fields.wrapper) == (amount != 'custom')
    assert ('disabled' in fields.input) == (amount != 'custom')
    assert fields.input['value'] == '1234.56'




@pytest.mark.parametrize(('scenario', 'custom', 'value', 'change'), [
    ('-10', '', '£900', '−£100 (-10%)'), ('5', '', '£1,050', '+£50 (+5%)'),
    ('10', '', '£1,100', '+£100 (+10%)'), ('20', '', '£1,200', '+£200 (+20%)'),
    ('custom', '12.5', '£1,125', '+£125 (+12.5%)'),
    ('custom', '-100', '£0', '−£1,000 (-100%)'), ('custom', '0', '£1,000', '+£0 (+0%)'),
])
def test_free_hypothetical_scenarios(page, scenario, custom, value, change):
    page[1].return_value = app.build_return_outlook({})
    before = copy.deepcopy(page[1].return_value)
    html = page[0].get('/what-if', query_string={
        'symbol': 'MSFT', 'scenario': scenario, 'custom_percentage': custom, 'amount': '250',
    }).get_data(as_text=True)
    assert value in html and change in html
    assert '£1,000 <span>today' in html
    assert 'Explore a hypothetical scenario' in html
    assert 'This is a user-selected illustration, not an analyst target, StockRadar forecast or guarantee of future returns.' in html
    assert 'name="amount"' not in html
    assert 'Try Microsoft' not in html and 'Try a stock with available outlook data:' not in html
    assert 'Reference price dated' not in html
    assert page[1].return_value == before
    page[1].assert_called_once_with('MSFT')


@pytest.mark.parametrize(('amount', 'custom', 'value'), [
    ('250', '', '£275'), ('500', '', '£550'), ('1000', '', '£1,100'),
    ('custom', '1234.56', '£1,358.02'),
])
def test_premium_hypothetical_preserves_amount_controls(page, monkeypatch, amount, custom, value):
    premium(monkeypatch)
    page[1].return_value = app.build_return_outlook({})
    html = page[0].get('/what-if', query_string={
        'symbol': 'MSFT', 'amount': amount, 'custom_amount': custom, 'scenario': '10',
    }).get_data(as_text=True)
    assert value in html
    assert 'name="amount"' in html and 'name="scenario"' in html
    assert 'Unlock What If?' not in html


@pytest.mark.parametrize('raw', ['-100.01', '1000001', 'NaN', 'Infinity', '1e3', '', '1.001', '<script>'])
def test_invalid_hypothetical_percentage_is_not_calculated(page, raw):
    page[1].return_value = app.build_return_outlook({})
    html = page[0].get('/what-if', query_string={
        'symbol': 'MSFT', 'scenario': 'custom', 'custom_percentage': raw,
    }).get_data(as_text=True)
    assert 'role="alert"' in html
    assert 'class="illustrative-value"' not in html
    assert 'Explore a hypothetical scenario' in html


def test_analyst_mode_ignores_hypothetical_parameters(page, outlook):
    before = copy.deepcopy(outlook)
    html = page[0].get('/what-if?symbol=MSFT&scenario=custom&custom_percentage=999').get_data(as_text=True)
    assert '£1,146' in html and '+£146 (+14.6%)' in html
    assert 'Based on the current 12-month analyst-target outlook.' in html
    assert 'name="scenario"' not in html and 'Explore a hypothetical scenario' not in html
    assert outlook == before


def test_hypothetical_does_not_write_cache_storage_or_request_extra_data(monkeypatch):
    monkeypatch.setattr(app, 'premium_entitlement_record', lambda **kwargs: None)
    now = time.time()
    cache = {'SPCX': {'timestamp': now, 'context': {
        'income_status': app.INCOME_STATUS_UNAVAILABLE, 'return_outlook': app.build_return_outlook({}),
    }}}
    monkeypatch.setattr(app, 'DIVIDEND_CONTEXT_CACHE', cache)
    monkeypatch.setattr(app, 'OPPORTUNITY_PAGE_CACHE', None)
    monkeypatch.setattr(app, 'newsletter_storage_load', Mock(return_value={}))
    provider = Mock(side_effect=AssertionError('No provider call for cached unavailable data'))
    write = Mock(side_effect=AssertionError('Hypothetical values must not be stored'))
    monkeypatch.setattr(app, '_fetch_dividend_context', provider)
    monkeypatch.setattr(app, 'newsletter_storage_update', write)
    before = copy.deepcopy(cache)
    client = app.app.test_client()
    for scenario in ['-10', '5', '10', '20']:
        html = client.get('/what-if?symbol=SPCX&scenario=' + scenario).get_data(as_text=True)
        assert 'Explore a hypothetical scenario' in html
        assert 'class="illustrative-value"' in html
    assert cache == before
    provider.assert_not_called()
    write.assert_not_called()
