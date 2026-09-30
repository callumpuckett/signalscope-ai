from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app


def frame(values):
    return pd.DataFrame({'Close': values}, index=pd.date_range('2026-09-01', periods=len(values)))


@pytest.mark.parametrize('failure', [RuntimeError('temporary'), pd.DataFrame(), frame([0, -1])])
def test_expired_history_failure_retains_original_timestamp_and_retries(monkeypatch, failure):
    monkeypatch.setattr(app, 'YAHOO_HISTORY_CACHE', {})
    monkeypatch.setattr(app, 'YAHOO_COOLDOWN_UNTIL', 0)
    ticker = Mock()
    ticker.history.side_effect = [frame([100]), failure, frame([105])]
    with patch.object(app.yf, 'Ticker', return_value=ticker), patch.object(app.time, 'time', return_value=1000) as clock:
        app.safe_history('AAPL', period='1mo')
        clock.return_value = 1301
        retained = app.safe_history('AAPL', period='1mo')
        assert retained.iloc[0, 0] == 100
        cached = next(iter(app.YAHOO_HISTORY_CACHE.values()))
        assert cached['timestamp'] == 1000
        retained.iloc[0, 0] = 999
        clock.return_value = 1400
        assert app.safe_history('AAPL', period='1mo').iloc[0, 0] == 100
        assert ticker.history.call_count == 2
        clock.return_value = 1602
        assert app.safe_history('AAPL', period='1mo').iloc[0, 0] == 105
        assert next(iter(app.YAHOO_HISTORY_CACHE.values()))['timestamp'] == 1602


@pytest.mark.parametrize('failure', [[], RuntimeError('temporary')])
def test_dashboard_news_fallback_preserves_time_and_accepts_recovery(monkeypatch, failure):
    monkeypatch.setattr(app, 'DASHBOARD_CACHE', {})
    monkeypatch.setattr(app, 'get_recommendations', lambda: [])
    monkeypatch.setattr(app, 'get_market_impact_radar', lambda: [])
    monkeypatch.setattr(app, 'get_stock_universe', lambda: [])
    old = {'label': 'LIVE NEWS', 'headline': 'Old verified headline', 'article_url': 'https://example.com/old'}
    new = {**old, 'headline': 'New verified headline'}
    with patch.object(app, 'build_live_headlines', side_effect=[[old], failure, [new]]), patch.object(app.time, 'time', return_value=1000) as clock:
        first = app.get_cached_dashboard_data(include_market_snapshots=False)
        # A distinct saved label makes accidental replacement detectable.
        app.DASHBOARD_CACHE['data']['ticker_updated'] = '09:00'
        clock.return_value = 1000 + app.DASHBOARD_CACHE_TTL_SECONDS + 1
        second = app.get_cached_dashboard_data(include_market_snapshots=False)
        assert second['live_headlines'] == first['live_headlines']
        assert second['ticker_updated'] == '09:00'
        clock.return_value += app.DASHBOARD_CACHE_TTL_SECONDS + 1
        assert app.get_cached_dashboard_data(include_market_snapshots=False)['live_headlines'] == [new]


@pytest.mark.parametrize(('values', 'expected'), [([0], []), ([-5], []), ([100], [100]), ([0, -5, 100], [100])])
def test_chart_rejects_nonpositive_prices(values, expected):
    with patch.object(app, 'safe_history', return_value=frame(values)):
        result = app.stock_history('AAPL', '1mo')
    assert result['prices'] == expected
    assert result['ok'] is bool(expected)


@pytest.mark.parametrize(('timestamp', 'uk', 'us'), [
    ('2026-12-25T15:00:00', 'CLOSED', 'CLOSED'),
    ('2026-09-30T15:00:00', 'OPEN', 'OPEN'),
    ('2026-09-30T07:00:00', 'CLOSED', 'CLOSED'),
    ('2026-10-03T15:00:00', 'CLOSED', 'CLOSED'),
    ('2021-12-24T15:00:00', 'OPEN', 'CLOSED'),
    ('2021-12-27T15:00:00', 'CLOSED', 'OPEN'),
    ('2021-12-28T15:00:00', 'CLOSED', 'OPEN'),
    ('2022-12-26T15:00:00', 'CLOSED', 'CLOSED'),
    ('2022-12-27T15:00:00', 'CLOSED', 'OPEN'),
])
def test_christmas_and_normal_market_hours(timestamp, uk, us):
    now = datetime.fromisoformat(timestamp).replace(tzinfo=ZoneInfo('Europe/London'))
    with patch.object(app, 'datetime', wraps=datetime) as clock:
        clock.now.side_effect = lambda tz: now.astimezone(tz)
        result = app.market_status()
    assert result['uk_status'] == uk
    assert result['us_status'] == us
