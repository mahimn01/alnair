import json
import pytest
from clpresearch import tools, extract, scholar
from clpresearch.common import ToolError


def test_article_extraction_omits_navigation():
    html='<html><head><title>Results</title></head><body><nav>BUY CRYPTO TICKERS</nav><article><h1>Results</h1><p>'+('Revenue was 123 million dollars. '*30)+'</p></article><footer>Subscribe menu</footer></body></html>'
    title,text=extract.article_to_text(html)
    assert 'Revenue was 123' in text
    assert 'BUY CRYPTO' not in text and 'Subscribe menu' not in text


def test_new_tools_registered():
    assert {'research_find','market_history','market_options','market_earnings','scholar_discover'} <= set(tools.BY_NAME)


def test_find_saved_passage(ctx):
    ref=ctx.save('web','https://example.com','Test','Alpha\nEPS was 2.45.\nOmega\n')
    code,out=tools.run('research_find',{'source':ref.id,'query':'2.45','context':1},ctx)
    assert code==0 and 'Alpha' in out and 'Omega' in out and ref.parts[0] in out and '#' in out


def test_query_expansion_finance():
    qs=scholar.expand_queries('CEO scandal stock returns')
    assert any('misconduct' in q for q in qs)
    assert any('firm value' in q for q in qs)


def test_crossref_discovery(ctx,mock):
    mock.add('/crossref/works',{'message':{'items':[{'title':['CEO misconduct and firm value'],'DOI':'10.1/example','published':{'date-parts':[[2020]]},'author':[{'given':'A','family':'Smith'}],'URL':'https://doi.org/10.1/example'}]}})
    code,out=tools.run('scholar_discover',{'query':'CEO scandal stock returns','source':'crossref','limit':3},ctx)
    assert code==0 and 'CEO misconduct and firm value' in out
    assert ctx.store.list_sources(kind='search')


def test_straddle_uses_matching_strike_and_valid_quotes():
    from clpresearch.market import straddle
    rows=[{'strike':100,'type':'call','bid':2,'ask':4},{'strike':100,'type':'put','bid':3,'ask':5}, {'strike':105,'type':'call','bid':1,'ask':2}]
    d=straddle(rows,101)
    assert d['strike']==100 and d['move_dollars']==7
    # 2026-11-11: the percent is now measured against the price the options themselves imply
    # (strike + call mid - put mid = 100 + 3 - 4 = 99), not the quoted share price, which can be from another moment.
    assert d['implied_underlying']==99 and d['move_percent']==pytest.approx(700/99)
    assert d['move_percent_of_quoted_underlying']==pytest.approx(700/101)
    with pytest.raises(ToolError): straddle([{'strike':100,'type':'call','bid':4,'ask':2}],100)


def test_history_saves_provider_payload(ctx,mock):
    mock.add('/yahoo/v8/finance/chart/AAPL',{'chart':{'result':[{'meta':{'currency':'USD','regularMarketTime':1700000000},'timestamp':[1699920000], 'indicators':{'quote':[{'open':[100],'high':[105],'low':[99],'close':[103],'volume':[10]}]}}],'error':None}})
    code,out=tools.run('market_history',{'symbol':'AAPL'},ctx)
    assert code==0 and '103' in out
    m=ctx.store.list_sources(kind='market')[0]
    assert m['raw_sha256'] and m['provider']=='yahoo' and m['retrieved_at']


def test_wayback_variants_keep_query():
    from clpresearch.fetch import url_variants
    vs=url_variants('https://www.example.com/article/?tracking=1')
    assert 'https://example.com/article?tracking=1' in vs
    assert 'https://www.example.com/article/?tracking=1' in vs
    assert not any('?' not in v for v in vs)


def test_fetch_saves_raw_clean_and_find(ctx,mock):
    mock.add('/robots.txt','User-agent: *\nAllow: /',ctype='text/plain')
    html='<html><nav>NOISY MENU</nav><article><h1>Revenue</h1><p>'+('Revenue increased 23 percent. '*20)+'</p></article></html>'
    mock.add('/story',html,ctype='text/html')
    code,out=tools.run('research_fetch',{'url':mock.base+'/story'},ctx)
    assert code==0
    m=ctx.store.list_sources(kind='web')[0]
    assert (ctx.store.raw/(m['raw_sha256']+'.bin')).read_bytes()==html.encode()
    text=(ctx.store.docs/m['parts'][0]).read_text()
    assert 'NOISY MENU' not in text and 'Revenue increased 23' in text


def test_wayback_closest_lookup_and_raw_save(ctx,mock):
    mock.add('/robots.txt','User-agent: *\nAllow: /',ctype='text/plain')
    mock.add('/blocked','',status=403)
    mock.add('/wayback_available',{'archived_snapshots':{'closest':{'available':True,'status':'200','timestamp':'20240102030405','url':mock.base+'/wayback/20240102030405id_/original'}}})
    mock.add('/wayback/20240102030405id_', '<article><p>Archived exact earnings were 2.45 dollars.</p></article>',ctype='text/html')
    code,out=tools.run('research_fetch',{'url':mock.base+'/blocked'},ctx)
    assert code==0 and '20240102030405' in out
    m=ctx.store.list_sources(kind='wayback')[0]
    assert m['raw_sha256'] and m['extra']['archived']


def test_blocked_fetch_records_attempts_not_fake_article(ctx,mock):
    mock.add('/robots.txt','User-agent: *\nAllow: /',ctype='text/plain')
    mock.add('/blocked','denied',status=403)
    mock.add('/wayback_available',{'archived_snapshots':{}})
    mock.add('/archive_lookup','captcha',status=403)
    code,out=tools.run('research_fetch',{'url':mock.base+'/blocked'},ctx)
    assert code==3 and 'Attempts' not in out  # detailed attempts live in saved failure source
    assert 'wayback availability' in out and 'Google cache' in out
    assert ctx.store.list_sources(kind='fetch_attempt') and not ctx.store.list_sources(kind='web')


def test_cboe_selects_expiry_after_event_and_saves_chain(ctx,mock):
    from datetime import date,timedelta
    event=date.today()+timedelta(days=3); expiry=event+timedelta(days=2)
    def opt(day,typ,bid,ask):return {'option':'AAPL'+day.strftime('%y%m%d')+typ+'00100000','bid':bid,'ask':ask}
    mock.add('/cboe/AAPL.json',{'timestamp':'2026-11-11T11:30:00-05:00','data':{'current_price':101,'options':[
        opt(event,'C',8,9),opt(event,'P',8,9),opt(expiry,'C',2,4),opt(expiry,'P',3,5)]}})
    code,out=tools.run('market_options',{'symbol':'AAPL','earnings_date':event.isoformat()},ctx)
    assert code==0 and 'move_dollars' in out
    m=ctx.store.list_sources(kind='market')[0]
    text=(ctx.store.docs/m['parts'][0]).read_text()
    assert expiry.isoformat() in text and '7.0' in text and m['raw_sha256']
    assert '"chain":' in text


def test_market_earnings_refusal_is_saved(ctx,mock):
    mock.add('/yahoo/v10/finance/quoteSummary/AAPL','auth required',status=401)
    code,out=tools.run('market_earnings',{'symbol':'AAPL'},ctx)
    assert code==3 and 'No reliable universal free consensus' in out
    assert ctx.store.list_sources(kind='market')


def test_stooq_history_fallback(ctx,mock):
    mock.add('/yahoo/v8/finance/chart/AAPL','denied',status=403)
    mock.add('/stooq/q/d/l/','Date,Open,High,Low,Close,Volume\n2024-01-02,1,3,1,2,100\n',ctype='text/csv')
    code,out=tools.run('market_history',{'symbol':'AAPL'},ctx)
    assert code==0 and 'stooq' in out and '2024-01-02' in out


def test_find_rejects_traversal(ctx):
    code,out=tools.run('research_find',{'source':'../secret','query':'anything'},ctx)
    assert code==2 and 'not a path' in out


def test_invalid_atm_does_not_silently_use_remote_strike():
    from clpresearch.market import straddle
    rows=[{'strike':100,'type':'call','bid':0,'ask':4},{'strike':100,'type':'put','bid':2,'ask':3},
          {'strike':150,'type':'call','bid':2,'ask':3},{'strike':150,'type':'put','bid':50,'ask':51}]
    with pytest.raises(ToolError,match='ATM'): straddle(rows,100)


def test_earnings_nasdaq_fallback_saved(ctx,mock):
    mock.add('/yahoo/v10/finance/quoteSummary/AAPL','',status=401)
    mock.add('/nasdaq/api/analyst/AAPL/earnings-date',{'data':{'announcement':'Company expected to report Nov 17, 2026','epsForecast':'2.47'},'status':{'rCode':200}})
    code,out=tools.run('market_earnings',{'symbol':'AAPL'},ctx)
    assert code==0 and 'nasdaq' in out and '2.47' in out
    assert ctx.store.list_sources(kind='market')[0]['raw_sha256']


def test_robots_lookup_can_be_bounded_for_fallbacks(ctx,mock):
    from clpresearch.primary import _robots_ok
    mock.add('/robots.txt','User-agent: *\nAllow: /',ctype='text/plain')
    assert _robots_ok(ctx,mock.base+'/story',timeout=5,attempts=1)


def test_archive_lookup_landing_page_is_not_saved_as_article(ctx,mock):
    mock.add('/robots.txt','User-agent: *\nAllow: /',ctype='text/plain')
    mock.add('/blocked','denied',status=403)
    mock.add('/wayback_available',{'archived_snapshots':{}})
    mock.add('/archive_lookup','<html><title>Archive search</title><p>No results for this URL.</p></html>',ctype='text/html')
    code,out=tools.run('research_fetch',{'url':mock.base+'/blocked'},ctx)
    assert code==3 and not ctx.store.list_sources(kind='wayback')
