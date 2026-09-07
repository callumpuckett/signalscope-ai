import itertools
from html import unescape
import re
from unittest.mock import patch
import pytest
import app


def form(**changes):
    return dict(goal='growth', horizon='10plus', risk='medium', experience='new', style='simple', amount=None, **{}) | changes


@pytest.mark.parametrize('goal,risk,horizon,style', list(itertools.product(('growth','income','learning','balanced'), ('low','medium','high'), ('short','2to5','5to10','10plus'), ('simple','stocks','active'))))
def test_allocations_unchanged(goal,risk,horizon,style):
    result=app.build_beginner_result(form(goal=goal,risk=risk,horizon=horizon,style=style))
    if horizon=='short':
        expected=(0,0,0,0); profile='Short-term saver, not a stock-market starter yet'
    elif risk=='low' or goal in ('income','balanced'):
        expected=(75,10,10,5); profile='Cautious beginner investor'
    elif risk=='high' and horizon in ('10plus','5to10') and style in ('stocks','active'):
        expected=(60,25,5,10); profile='Growth-focused beginner investor'
    else:
        expected=(70,15,10,5); profile='Balanced long-term beginner investor'
    assert tuple(result[k] for k in ('etf','quality','defensive','learning')) == expected
    assert result['profile']==profile


@pytest.mark.parametrize('changes,key', [({},'growth'),({'goal':'balanced'},'balanced'),({'risk':'low'},'cautious'),({'goal':'income'},'income'),({'risk':'high','style':'active'},'higher-growth')])
@pytest.mark.parametrize('access', ['free','premium','owner'])
def test_rendered_examples(changes,key,access):
    client=app.app.test_client()
    with client.session_transaction() as session:
        if access=='owner': session['owner_logged_in']=True
        if access=='premium': session['stripe_subscription_id']='sub_examples_test'
    with patch.object(app,'premium_entitlement_record',return_value={'premium_active':True,'entitlement_version':1} if access=='premium' else None):
        response=client.post('/beginner',data=form(**changes) | {'amount':''})
    assert response.status_code==200
    result=unescape(response.get_data(as_text=True)).split('id="beginner-result"')[1].split('Next steps')[0]
    cards=result.split('<div class="model-box">')[1:]
    assert len(cards)==4
    for card,symbols in zip(cards,app.BEGINNER_EXAMPLE_PROFILES[key]):
        assert card.count('class="portfolio-example"')==(1 if access=='free' else 3)
        assert ('Premium Portfolio Matches' in card)==(access=='free')
        assert ('Unlock Premium' in card)==(access=='free')
        for symbol in symbols[:1 if access=='free' else 3]:
            assert app.stock_display_label(symbol) in card or (app.BEGINNER_EXAMPLE_ASSETS[symbol][0] or symbol) in card
    assert ('href="/upgrade"' in result)==(access=='free')
    for symbol in re.findall(r'href="/stock/([^"]+)"',result):
        assert symbol in app.stock_display_lookup()
        assert app.app.url_map.bind('localhost').match('/stock/'+symbol)[0]=='stock_detail'
    for phrase in ('we recommend you buy','you should buy','best stock for you','buy this investment'):
        assert phrase not in result.lower()


def test_zero_allocations_and_missing_research_destinations():
    with app.app.test_request_context():
        short=form(horizon='short')
        assert app.build_beginner_examples(short,app.build_beginner_result(short))=={}
        with patch.object(app,'stock_display_lookup',return_value={}):
            examples=app.build_beginner_examples(form(),app.build_beginner_result(form()))
        assert all(e['url'] is None for group in examples['buckets'].values() for e in group)
    response=app.app.test_client().post('/beginner',data=form(horizon='short') | {'amount':''})
    assert 'class="portfolio-example"' not in response.get_data(as_text=True)


def test_initial_invalid_and_mobile_layout():
    client=app.app.test_client()
    for response in (client.get('/beginner'),client.post('/beginner',data={'goal':'invalid'})):
        assert 'class="portfolio-example"' not in response.get_data(as_text=True)
    assert '.grid,.form-grid,.model-grid{grid-template-columns:1fr;}' in app.beginner_html
    assert '.model-box{min-width:0;overflow-wrap:anywhere;}' in app.beginner_html
    assert 'min-height:44px;padding:10px 0' in app.beginner_html
