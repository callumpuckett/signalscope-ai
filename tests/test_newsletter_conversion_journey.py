from unittest.mock import Mock

import pytest

import app


def test_premium_alias_preserves_campaign_and_uses_existing_page(monkeypatch):
    monkeypatch.setattr(app, 'stripe_checkout_configured', lambda: True)
    client = app.app.test_client()
    response = client.get('/premium?utm_source=beehiiv&utm_campaign=weekly&next=https://example.com')
    assert response.status_code == 301
    assert response.headers['Location'] == '/upgrade?utm_source=beehiiv&utm_campaign=weekly&next=https://example.com'
    page = client.get(response.headers['Location']).get_data(as_text=True)
    assert 'Start Premium — £5/month' in page
    assert 'action="/create-checkout-session"' in page
    assert 'name="csrf_token"' in page


def test_existing_published_issue_gets_research_links_without_regeneration(monkeypatch):
    html = '<html><body><header class="header">Published issue</header><section class="section">Original content</section></body></html>'
    monkeypatch.setattr(app, 'load_latest_published_newsletter_artifact', lambda: {'html': html})
    generate = Mock(side_effect=AssertionError('must not generate'))
    monkeypatch.setattr(app, 'build_weekly_newsletter_issue', generate)
    response = app.app.test_client().get('/newsletter/latest?utm_source=beehiiv')
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert page.count('id="newsletter-research"') == 1
    for destination in ('/universe', '/compare', '/upgrade', '/newsletter'):
        assert f'href="{destination}"' in page
    assert 'Original content' in page
    assert html == '<html><body><header class="header">Published issue</header><section class="section">Original content</section></body></html>'
    generate.assert_not_called()


def test_new_issue_panel_is_not_duplicated(monkeypatch):
    html = '<html><body>' + app.NEWSLETTER_RESEARCH_LINKS + '<section class="section">Issue</section></body></html>'
    monkeypatch.setattr(app, 'load_latest_published_newsletter_artifact', lambda: {'html': html})
    page = app.app.test_client().get('/newsletter/latest').get_data(as_text=True)
    assert page.count('id="newsletter-research"') == 1


@pytest.mark.parametrize('query', [
    'utm_source=beehiiv&utm_medium=email&utm_campaign=weekly&utm_content=premium&utm_term=research&utm_id=issue43&utm_source_platform=beehiiv',
    'utm_source=beehiiv&utm_source=partner&tag=one&tag=two&empty=&flag',
    'utm_campaign=weekly%20brief&utm_content=stock+research&encoded=%2f%2F%26%3D%25&unicode=%C2%A3',
    'next=https%3A%2F%2Fexample.com&line=%0D%0ALocation%3Aevil',
    '',
])
def test_premium_preserves_exact_query_string_and_fixed_destination(query):
    client = app.app.test_client()
    response = client.get('/premium' + ('?' + query if query else ''))
    expected = '/upgrade' + ('?' + query if query else '')
    assert response.status_code == 301
    assert response.headers['Location'] == expected
    final = client.get(response.headers['Location'])
    assert final.status_code == 200
    assert final.request.path == '/upgrade'
    assert final.request.query_string == query.encode('ascii')
