from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import app


LONDON = ZoneInfo("Europe/London")
PUBLISHED_AT = "2026-07-20T12:00:00Z"


def raw_article(
    *,
    title="Stock markets rise after central bank rate decision",
    source="Reuters",
    url="https://www.reuters.com/markets/rates/decision",
    published_at=PUBLISHED_AT,
    article_id="article-1",
    description="Professional market coverage.",
    summary="A concise financial summary.",
):
    return {
        "title": title,
        "source": source,
        "url": url,
        "publishedAt": published_at,
        "id": article_id,
        "description": description,
        "summary": summary,
    }


def weekly_window():
    return app.newsletter_weekly_window(
        datetime(2026, 7, 24, 10, 0, tzinfo=LONDON)
    )


def select_articles(articles, limit=12):
    with (
        patch.object(app, "NEWSAPI_KEY", "configured"),
        patch.object(app, "fetch_newsapi_weekly_articles", return_value=articles),
        patch.object(app, "fetch_gdelt_weekly_articles", return_value=[]),
        patch.object(app, "load_newsletter_story_history", return_value={"stories": {}}),
    ):
        return app.fetch_weekly_news_articles(weekly_window(), limit=limit)


@pytest.mark.parametrize(
    ("source", "url", "expected_tier"),
    [
        ("Reuters", "https://www.reuters.com/markets/story", 2),
        ("Associated Press", "https://apnews.com/article/markets", 2),
        ("BBC Business", "https://www.bbc.co.uk/news/business-1", 3),
        ("Bloomberg", "https://www.bloomberg.com/news/articles/1", 3),
        ("MarketWatch", "https://www.marketwatch.com/story/markets", 3),
    ],
)
def test_recognised_reputable_sources_are_eligible(source, url, expected_tier):
    quality = app.newsletter_source_quality({"source": source, "url": url})
    assert quality["eligible"] is True
    assert quality["tier"] == expected_tier


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("Sky News", "https://news.sky.com/story/markets-1"),
        ("Fox News", "https://www.foxnews.com/politics/market-policy"),
        ("Al Jazeera", "https://www.aljazeera.com/economy/markets"),
        ("The Telegraph", "https://www.telegraph.co.uk/business/markets/"),
        ("NBC News", "https://www.nbcnews.com/business/economy/story"),
    ],
)
def test_expanded_established_news_allowlist_is_eligible(source, url):
    quality = app.newsletter_source_quality({"source": source, "url": url})
    assert quality["eligible"] is True
    assert quality["tier"] == 4


def test_domain_matching_normalises_case_and_allows_real_mobile_subdomains():
    quality = app.newsletter_source_quality({
        "source": "Reuters",
        "url": "HTTPS://MOBILE.REUTERS.COM/markets/story",
    })
    assert quality["eligible"] is True
    assert quality["display_source"] == "Reuters"


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("Reuters", "https://reuters.com.untrusted-example.net/markets/story"),
        (
            "U.S. Securities and Exchange Commission",
            "https://sec.gov.untrusted-example.net/newsroom/release",
        ),
    ],
)
def test_misleading_domain_containing_trusted_name_is_rejected(source, url):
    article = raw_article(
        source=source,
        url=url,
    )

    articles, status = select_articles([article])

    assert articles == []
    assert status["source_rejection_reasons"] == {
        "untrusted_or_misleading_domain": 1,
    }


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("Reddit", "https://www.reddit.com/r/stocks/comments/example"),
        ("Anonymous Stock Forum", "https://forums.example/market-tip"),
    ],
)
def test_social_or_forum_sources_are_rejected(source, url):
    articles, status = select_articles([
        raw_article(source=source, url=url),
    ])

    assert articles == []
    assert status["low_authority_sources_excluded"] == 1


def test_yahoo_finance_requires_a_recognised_original_publisher():
    yahoo_only = app.newsletter_source_quality({
        "source": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/market-story.html",
    })
    syndicated = app.newsletter_source_quality({
        "source": "Reuters via Yahoo Finance",
        "url": "https://finance.yahoo.com/news/reuters-market-story.html",
    })

    assert yahoo_only["eligible"] is False
    assert syndicated["eligible"] is True
    assert syndicated["tier"] == 2
    assert syndicated["original_source"] is False
    assert syndicated["display_source"] == "Reuters"


def test_higher_quality_original_source_wins_duplicate_group_even_when_older():
    shared_title = "Stock markets rise after Bank of England rate decision"
    weak = raw_article(
        title=shared_title,
        source="Forbes",
        url="https://www.forbes.com/sites/example/markets-rise",
        published_at="2026-07-20T14:00:00Z",
        article_id="forbes-copy",
    )
    strong = raw_article(
        title=shared_title,
        source="Reuters",
        url="https://www.reuters.com/markets/uk/rates-decision",
        published_at="2026-07-20T12:00:00Z",
        article_id="reuters-original",
    )

    articles, status = select_articles([weak, strong])

    assert [article["source"] for article in articles] == ["Reuters"]
    assert articles[0]["canonical_url"] == strong["url"]
    assert status["duplicate_stories_excluded"] == 1


def test_official_primary_source_wins_duplicate_over_wire_service():
    shared_title = "SEC regulator announces final bank merger decision"
    reuters = raw_article(
        title=shared_title,
        source="Reuters",
        url="https://www.reuters.com/markets/banks/merger-decision",
        published_at="2026-07-20T14:00:00Z",
        article_id="reuters-report",
    )
    sec = raw_article(
        title=shared_title,
        source="U.S. Securities and Exchange Commission",
        url="https://www.sec.gov/newsroom/press-releases/merger-decision",
        published_at="2026-07-20T12:00:00Z",
        article_id="sec-primary",
    )

    articles, status = select_articles([reuters, sec])

    assert [article["display_source"] for article in articles] == [
        "U.S. Securities and Exchange Commission"
    ]
    assert status["duplicate_stories_excluded"] == 1


def test_reuters_is_selected_and_unknown_republisher_is_rejected():
    shared_title = "Stocks rally after inflation data cools"
    unknown_copy = raw_article(
        title=shared_title,
        source="Daily Viral Markets",
        url="https://viral-markets.example/reposted-story",
        published_at="2026-07-20T15:00:00Z",
        article_id="unknown-copy",
    )
    reuters = raw_article(
        title=shared_title,
        url="https://www.reuters.com/markets/global/inflation-stocks",
        article_id="reuters-original",
    )

    articles, status = select_articles([unknown_copy, reuters])

    assert [article["source"] for article in articles] == ["Reuters"]
    assert status["low_authority_sources_excluded"] == 1


@pytest.mark.parametrize(
    ("field", "unsafe_text"),
    [
        ("title", "Stock market has a bullshit rally after the decision"),
        ("description", "A fucking wild session followed the rate decision."),
        ("summary", "The analyst called the outlook shit."),
    ],
)
def test_unsuitable_language_in_supplied_article_text_is_excluded(field, unsafe_text):
    values = {field: unsafe_text}
    article = raw_article(**values)

    articles, status = select_articles([article])

    assert articles == []
    assert status["unsuitable_language_excluded"] == 1


def test_language_matching_is_case_insensitive_and_punctuation_cannot_bypass_it():
    article = raw_article(title="Stock market faces a F.U.C.K.I.N.G volatile session")

    articles, status = select_articles([article])

    assert articles == []
    assert status["unsuitable_language_excluded"] == 1


def test_partial_character_sequences_inside_legitimate_words_are_not_blocked():
    article = raw_article(
        title="Scunthorpe-listed fund reports stronger stock market returns",
        url="https://www.reuters.com/markets/scunthorpe-fund-results",
    )

    articles, status = select_articles([article])

    assert len(articles) == 1
    assert status["unsuitable_language_excluded"] == 0


def test_official_company_and_regulator_sources_remain_eligible():
    company = app.newsletter_source_quality({
        "source": "Apple Investor Relations",
        "url": "https://investor.apple.com/investor-relations/results",
    })
    regulator = app.newsletter_source_quality({
        "source": "U.S. Securities and Exchange Commission",
        "url": "https://www.sec.gov/newsroom/press-releases/example",
    })

    assert company["eligible"] is True
    assert company["tier"] == 1
    assert company["tier_label"] == "official_primary_source"
    assert company["display_source"] == "Apple Investor Relations"
    assert regulator["eligible"] is True
    assert regulator["tier"] == 1


def test_professionally_worded_serious_market_news_remains_eligible():
    article = raw_article(
        title="Stock markets fall as war and sanctions raise energy risks",
        url="https://www.reuters.com/markets/commodities/war-sanctions-energy",
    )

    articles, status = select_articles([article])

    assert len(articles) == 1
    assert articles[0]["title"] == article["title"]
    assert status["unsuitable_language_excluded"] == 0


def test_sensational_clickbait_wording_is_excluded():
    article = raw_article(
        title="You won't believe this stock market secret",
        url="https://www.forbes.com/sites/example/market-secret",
    )

    articles, status = select_articles([article])

    assert articles == []
    assert status["unprofessional_wording_excluded"] == 1


def test_excessive_capitalisation_and_promotional_predictions_are_excluded():
    capitalised = raw_article(
        title="STOCK MARKETS GUARANTEE MASSIVE GAINS THIS WEEK",
        url="https://www.forbes.com/sites/example/all-caps",
    )
    promotion = raw_article(
        title="This stock market pick is going to the moon",
        url="https://www.forbes.com/sites/example/promotion",
        article_id="promotion",
    )

    capitalised_articles, capitalised_status = select_articles([capitalised])
    promotion_articles, promotion_status = select_articles([promotion])

    assert capitalised_articles == []
    assert (
        capitalised_status["unprofessional_wording_excluded"]
        + capitalised_status["excessive_capitalisation_excluded"]
    ) == 1
    assert promotion_articles == []
    assert promotion_status["unprofessional_wording_excluded"] == 1


def test_opinion_path_is_not_presented_as_verified_market_news():
    article = raw_article(
        title="Stock markets may benefit from lower interest rates",
        url="https://www.ft.com/opinion/market-rates-column",
    )

    articles, status = select_articles([article])

    assert articles == []
    assert status["opinion_content_excluded"] == 1


def test_political_story_requires_a_material_financial_connection():
    unrelated = raw_article(
        title="President addresses parliament after election",
        source="BBC News",
        description="",
        summary="",
        url="https://www.bbc.co.uk/news/politics-election",
    )
    material = raw_article(
        title="Stock markets fall as president announces new trade tariffs",
        source="BBC News",
        description="",
        summary="",
        url="https://www.bbc.co.uk/news/business-market-tariffs",
        article_id="material",
    )

    unrelated_articles, unrelated_status = select_articles([unrelated])
    material_articles, _ = select_articles([material])

    assert unrelated_articles == []
    assert unrelated_status["irrelevant_stories_excluded"] == 1
    assert len(material_articles) == 1


def test_publisher_attribution_and_selected_url_are_preserved():
    article = raw_article(
        source="Financial Times",
        url="https://www.ft.com/content/story-id?utm_source=feed&edition=uk",
    )

    articles, _ = select_articles([article])

    assert articles[0]["source"] == "Financial Times"
    assert articles[0]["url"] == article["url"]
    assert articles[0]["canonical_url"] == "https://www.ft.com/content/story-id?edition=uk"


def test_newsletter_renders_cleanly_with_one_story_and_preserves_its_link():
    normalized = app.normalize_weekly_news_article(
        raw_article(),
        "newsapi",
    )
    draft = app.build_free_weekly_newsletter(
        window=weekly_window(),
        articles=[normalized],
        news_status={"coverage_status": "verified"},
    )

    with app.app.app_context():
        rendered = app.render_newsletter_issue_body(draft)

    assert rendered.count("<li>") >= 1
    assert normalized["title"] in rendered
    assert f'href="{normalized["canonical_url"]}"' in rendered
    assert normalized["source"] in rendered
    assert "20 July 2026" in rendered
    assert "StockRadar provides educational market information" in rendered


def test_existing_safe_fallback_renders_when_no_suitable_stories_exist():
    draft = app.build_free_weekly_newsletter(
        window=weekly_window(),
        articles=[],
        news_status={"coverage_status": "verified_no_stories"},
    )

    with app.app.app_context():
        rendered = app.render_newsletter_issue_body(draft)

    assert "Verified weekly news coverage was unavailable" in rendered
    assert "not padded this issue with stale headlines" in rendered


def test_friday_newsletter_schedule_constants_are_unchanged():
    assert app.NEWSLETTER_AUTO_SEND_HOUR_LONDON == 9
    assert app.NEWSLETTER_AUTO_SEND_MINUTE_LONDON == 0
    assert app.NEWSLETTER_WEEKLY_CUTOFF_HOUR_LONDON == 9
    assert app.NEWSLETTER_WEEKLY_CUTOFF_MINUTE_LONDON == 0
