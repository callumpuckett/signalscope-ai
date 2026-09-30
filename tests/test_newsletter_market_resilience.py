from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app


CUTOFF = datetime(2026, 4, 3, 9, tzinfo=ZoneInfo('Europe/London'))


@pytest.fixture
def market_store(monkeypatch):
    state = {'snapshots': {}}
    monkeypatch.setattr(app, 'load_newsletter_market_snapshots', lambda: deepcopy(state))
    def update(name, updater):
        assert name == 'market_snapshots'
        updater(state)
        return True
    monkeypatch.setattr(app, 'newsletter_storage_update', update)
    monkeypatch.setattr(app, 'get_stock_universe', lambda: [])
    monkeypatch.setattr(app, 'NEWSLETTER_WEEKLY_TRACKED_TICKERS', ['SPY', 'QQQ'])
    return state


def history(price=100):
    return pd.DataFrame({'Close': [price]}, index=pd.to_datetime(['2026-04-02T19:00:00Z']))


@pytest.mark.parametrize('cutoff', [CUTOFF, datetime(2026, 4, 5, 9, tzinfo=ZoneInfo('Europe/London'))])
def test_holiday_weekend_uses_latest_observation_before_cutoff(monkeypatch, cutoff):
    data = pd.concat([history(), pd.DataFrame({'Close': [999]}, index=pd.to_datetime(['2026-04-06T19:00:00Z']))])
    provider = Mock(return_value=data)
    monkeypatch.setattr(app, 'safe_history', provider)
    point = app.fetch_newsletter_market_point('SPY', cutoff)
    assert point['price'] == 100
    assert point['price_timestamp'] == '2026-04-02T19:00:00+00:00'
    assert provider.call_args.kwargs['start'] < '2026-04-02'
    assert provider.call_args.kwargs['end'] > cutoff.date().isoformat()


@pytest.mark.parametrize('failure', [pd.DataFrame(), RuntimeError('temporary failure')])
def test_failed_refresh_preserves_good_prices(monkeypatch, market_store, failure):
    provider = Mock(return_value=history())
    monkeypatch.setattr(app, 'safe_history', provider)
    good = app.collect_newsletter_market_snapshot(CUTOFF)
    assert good['available_count'] == 2
    if isinstance(failure, Exception):
        provider.side_effect = failure
    else:
        provider.return_value = failure
    refreshed = app.collect_newsletter_market_snapshot(CUTOFF, force_refresh=True)
    assert refreshed == good
    assert refreshed['snapshot_id'] == good['snapshot_id']
    assert market_store['snapshots'][app.newsletter_snapshot_store_key(CUTOFF)]['instruments'] == good['instruments']


def test_empty_snapshot_retries_and_recovers(monkeypatch, market_store):
    provider = Mock(return_value=pd.DataFrame())
    monkeypatch.setattr(app, 'safe_history', provider)
    assert app.collect_newsletter_market_snapshot(CUTOFF)['available_count'] == 0
    provider.return_value = history()
    assert app.collect_newsletter_market_snapshot(CUTOFF)['available_count'] == 2


def test_partial_failure_renders_valid_movers(monkeypatch, market_store):
    provider = Mock(return_value=history())
    monkeypatch.setattr(app, 'safe_history', provider)
    previous = app.collect_newsletter_market_snapshot(CUTOFF)
    provider.side_effect = lambda ticker, **kwargs: history(105) if ticker == 'SPY' else pd.DataFrame()
    current = app.collect_newsletter_market_snapshot(datetime(2026, 4, 10, 9, tzinfo=ZoneInfo('Europe/London')))
    comparison = app.compare_newsletter_market_snapshots(previous, current)
    assert [row['ticker'] for row in comparison['comparisons']] == ['SPY']
    draft = app.build_free_weekly_newsletter(window=app.newsletter_weekly_window(CUTOFF), comparison=comparison)
    with app.app.app_context():
        rendered = app.render_newsletter_issue_body(draft)
    assert '+5.00%' in rendered
    assert 'Verified weekly news coverage was unavailable' in rendered


def test_complete_snapshot_is_reused_without_provider_request(monkeypatch, market_store):
    provider = Mock(return_value=history())
    monkeypatch.setattr(app, 'safe_history', provider)
    good = app.collect_newsletter_market_snapshot(CUTOFF)
    provider.reset_mock()
    assert app.collect_newsletter_market_snapshot(CUTOFF) == good
    provider.assert_not_called()


def test_partial_snapshot_retries_only_missing_instrument(monkeypatch, market_store):
    provider = Mock(side_effect=lambda ticker, **kwargs: history() if ticker == 'SPY' else pd.DataFrame())
    monkeypatch.setattr(app, 'safe_history', provider)
    assert app.collect_newsletter_market_snapshot(CUTOFF)['available_count'] == 1
    provider.reset_mock(side_effect=True)
    provider.return_value = history(105)
    recovered = app.collect_newsletter_market_snapshot(CUTOFF)
    assert recovered['available_count'] == 2
    assert [item['price'] for item in recovered['instruments']] == [100, 105]
    assert [call.args[0] for call in provider.call_args_list] == ['QQQ']


def test_forced_partial_refresh_merges_new_and_cached_prices(monkeypatch, market_store):
    monkeypatch.setattr(app, 'safe_history', Mock(return_value=history()))
    app.collect_newsletter_market_snapshot(CUTOFF)
    monkeypatch.setattr(app, 'safe_history', Mock(side_effect=lambda ticker, **kwargs: history(105) if ticker == 'SPY' else pd.DataFrame()))
    refreshed = app.collect_newsletter_market_snapshot(CUTOFF, force_refresh=True)
    assert [item['price'] for item in refreshed['instruments']] == [105, 100]
    assert refreshed['available_count'] == 2


def test_failed_refresh_preserves_concurrent_success(monkeypatch, market_store):
    monkeypatch.setattr(app, 'safe_history', Mock(return_value=history()))
    good = app.collect_newsletter_market_snapshot(CUTOFF)
    # Simulate another writer committing after this request's initial read.
    monkeypatch.setattr(app, 'load_newsletter_market_snapshots', lambda: {'snapshots': {}})
    monkeypatch.setattr(app, 'safe_history', Mock(side_effect=RuntimeError('temporary')))
    refreshed = app.collect_newsletter_market_snapshot(CUTOFF)
    assert refreshed['snapshot_id'] == good['snapshot_id']
    assert refreshed['instruments'] == good['instruments']


@pytest.mark.parametrize(('result', 'reason'), [
    (pd.DataFrame(), 'empty_response'),
    (ValueError('Yahoo history empty'), 'empty_response'),
    (RuntimeError('temporary'), 'provider_error'),
    (pd.DataFrame({'Close': [100]}, index=pd.to_datetime(['2026-04-06T19:00:00Z'])), 'no_valid_observation_in_range'),
    (history(-1), 'no_valid_observation_in_range'),
])
def test_unavailable_diagnostics(monkeypatch, caplog, result, reason):
    provider = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(app, 'safe_history', provider)
    point = app.fetch_newsletter_market_point('SPY', CUTOFF)
    assert point['price'] is None
    assert point['availability'] == 'unavailable'
    assert f'reason={reason}' in caplog.text


def test_normal_weekday_selects_last_price_at_cutoff(monkeypatch):
    cutoff = datetime(2026, 4, 10, 9, tzinfo=ZoneInfo('Europe/London'))
    data = pd.DataFrame({'Close': [100, 101, 999]}, index=pd.to_datetime([
        '2026-04-09T15:00:00Z', '2026-04-10T08:00:00Z', '2026-04-10T09:00:00Z',
    ]))
    monkeypatch.setattr(app, 'safe_history', Mock(return_value=data))
    assert app.fetch_newsletter_market_point('SPY', cutoff)['price'] == 101
