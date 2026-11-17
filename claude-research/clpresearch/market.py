"""Free delayed market snapshots: price history, an options-implied move, and earnings dates.

Plain English: three read-only lookups that save what the provider said, word for word, so a
number can be quoted and re-checked later. Nothing here places a trade, logs in, or uses a paid
or keyed feed.

  history   daily bars and the last COMPLETED session close (Yahoo chart, then Stooq).
  options   the options market's price for a move through the first expiry after an earnings
            date (Cboe delayed chain, then Yahoo). The at-the-money strike is chosen from the
            option quotes themselves, so a stale or after-hours stock quote cannot skew it.
  earnings  the expected next report date and analyst forecast (Nasdaq), past report dates
            from the company's own SEC 8-K "Item 2.02" filings, then Yahoo as a last resort.

Yahoo needs the same anonymous cookie + "crumb" its own website uses for visitors who are not
signed in. It is kept in memory for one call, never written to disk, and is not a login.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from . import config
from .common import Ctx, ToolError, Unavailable, untrusted_preview
from .netsafe import NetError

TZ = ZoneInfo('America/Toronto')
# Nasdaq's and Yahoo's public endpoints only answer browser-compatible clients (anything else is left to time out).
# This generic string carries no personal details and is sent to those two providers only.
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
    'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'en-US,en;q=0.9',
}
UNDERLYING_TOLERANCE = 0.015   # stock quote and option-implied price further apart than 1.5% = not the same moment
WIDE_SPREAD = 0.25             # a leg whose bid/ask gap is over 25% of its mid is a thin market


def symbol_check(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not re.fullmatch(r'[A-Z0-9^][A-Z0-9.^=-]{0,19}', symbol):
        raise ToolError('invalid ticker')
    return symbol


def stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ).isoformat() + ' America/Toronto'


def saved(ctx: Ctx, url: str, symbol: str, provider: str, data: dict, raw: bytes | None, **extra) -> str:
    text = json.dumps({'symbol': symbol, 'provider': provider, 'retrieved_at': datetime.now(TZ).isoformat(),
                       'timezone': 'America/Toronto', 'warning': 'Free/delayed snapshot, not a real-time trading feed. Check source timestamps.', **data}, indent=2, ensure_ascii=False)
    ref = ctx.save('market', url, provider + ' ' + symbol, text, raw=raw, provider=provider, extra=extra)
    return untrusted_preview(ctx, ref, lines=55, chars=7000)


def unavailable(ctx: Ctx, symbol: str, area: str, errors: list[str]) -> str:
    out = saved(ctx, 'market://' + area + '/' + symbol, symbol, 'unavailable', {'status': 'UNAVAILABLE', 'attempts': errors}, None)
    raise Unavailable('market_' + area, '; '.join(errors) + '\n' + out, 'No paid provider or login attempted; user-supplied data can be saved separately')


# =========================================================================================
# Yahoo anonymous session (cookie + crumb), in memory only
# =========================================================================================
def yahoo_session(ctx: Ctx) -> tuple[str, str]:
    """(cookie header, crumb) for Yahoo's public JSON endpoints, or NetError/ToolError. One bounded attempt each step."""
    first = ctx.client.get(config.BASES['yahoo_fc'], 'yahoo', headers=BROWSER_HEADERS, ttl=0, attempts=1, timeout=10, ok_statuses=(200, 404))
    if not first.cookies:
        raise ToolError('Yahoo set no anonymous session cookie')
    cookie = '; '.join(first.cookies)
    resp = ctx.client.get(config.BASES['yahoo'] + '/v1/test/getcrumb', 'yahoo', headers={**BROWSER_HEADERS, 'Cookie': cookie}, ttl=0, attempts=1, timeout=10)
    crumb = resp.text().strip()
    if not crumb or len(crumb) > 64 or '<' in crumb or ' ' in crumb:
        raise ToolError('Yahoo returned no usable crumb')
    return cookie, crumb


def _yahoo_json(ctx: Ctx, path: str, params: dict, session: tuple[str, str] | None = None) -> tuple[dict, bytes, str]:
    """(json, raw bytes, url without the crumb) from a Yahoo endpoint that needs the anonymous session.
    Pass `session` to reuse one cookie+crumb for a second request in the same call."""
    cookie, crumb = session or yahoo_session(ctx)
    public = config.BASES['yahoo'] + path + ('?' + urlencode(params) if params else '')   # what gets saved: no crumb in it
    url = public + ('&' if params else '?') + urlencode({'crumb': crumb})
    resp = ctx.client.get(url, 'yahoo', headers={**BROWSER_HEADERS, 'Cookie': cookie}, ttl=0, attempts=1, timeout=12)
    return resp.json(), resp.body, public


# =========================================================================================
# Price history
# =========================================================================================
def history(ctx: Ctx, symbol: str, period: str = '1mo') -> str:
    symbol = symbol_check(symbol)
    if period not in ('5d', '1mo', '3mo', '6mo', '1y', '5y'):
        raise ToolError('period must be 5d, 1mo, 3mo, 6mo, 1y or 5y')
    errors = []
    url = config.BASES['yahoo'] + '/v8/finance/chart/' + quote(symbol) + '?' + urlencode({'range': period, 'interval': '1d', 'events': 'div,splits'})
    try:
        resp = ctx.client.get(url, 'yahoo', ttl=0, attempts=2, timeout=12)
        d = resp.json()['chart']
        r = (d.get('result') or [])[0]
        meta = r['meta']
        q = r['indicators']['quote'][0]
        rows = []
        exchange = ZoneInfo(meta.get('exchangeTimezoneName') or 'America/New_York')
        now = datetime.now(exchange)
        for i, t in enumerate(r.get('timestamp') or []):
            close = (q.get('close') or [])[i]
            if close is None:
                continue
            day = datetime.fromtimestamp(t, exchange).date()
            # Exclude today's still-forming bar from the latest completed close.
            end = meta.get('currentTradingPeriod', {}).get('regular', {}).get('end')
            cutoff = datetime.fromtimestamp(end, exchange) if end and datetime.fromtimestamp(end, exchange).date() == day else datetime.combine(day, time(16, 5), exchange)
            complete = day < now.date() or (day == now.date() and now >= cutoff)
            rows.append({'date': day.isoformat(), 'timestamp': stamp(t), 'open': q.get('open', [None] * len(r['timestamp']))[i],
                         'high': q.get('high', [None] * len(r['timestamp']))[i], 'low': q.get('low', [None] * len(r['timestamp']))[i],
                         'close': close, 'volume': q.get('volume', [None] * len(r['timestamp']))[i], 'completed_session': complete})
        if not rows:
            raise ToolError('no daily bars')
        completed = [x for x in rows if x['completed_session']]
        return saved(ctx, url, symbol, 'yahoo', {'currency': meta.get('currency'), 'period': period,
                     'provider_quote_time': stamp(meta['regularMarketTime']) if meta.get('regularMarketTime') else None,
                     'latest_completed_close': completed[-1] if completed else None, 'prices_adjustment': 'OHLC provider values; dividends/splits in raw payload; not total return',
                     'daily_bars': rows}, resp.body)
    except (NetError, ToolError, KeyError, IndexError, ValueError) as e:
        errors.append('yahoo: ' + str(e))
    # Stooq US-equity daily CSV, no promise of adjusted/real-time prices.
    if not re.fullmatch('[A-Z][A-Z0-9.]*', symbol):
        return unavailable(ctx, symbol, 'history', errors + ['Stooq US ticker fallback does not support this symbol'])
    days = {'5d': 10, '1mo': 31, '3mo': 93, '6mo': 186, '1y': 366, '5y': 1830}[period]
    today = date.today()
    url = config.BASES['stooq'] + '/q/d/l/?' + urlencode({'s': symbol.lower() + '.us', 'i': 'd', 'd1': (today - timedelta(days=days)).strftime('%Y%m%d'), 'd2': today.strftime('%Y%m%d')})
    try:
        resp = ctx.client.get(url, 'stooq', ttl=0, attempts=2, timeout=12)
        rows = list(csv.DictReader(io.StringIO(resp.text())))
        rows = [r for r in rows if r.get('Date') and r.get('Close') not in ('', 'N/D', None)]
        if not rows:
            raise ToolError('no Stooq daily bars')
        rows = sorted(rows, key=lambda r: r['Date'])
        completed = [r for r in rows if r['Date'] < today.isoformat()]
        return saved(ctx, url, symbol, 'stooq', {'currency': 'USD (US equity fallback)', 'latest_completed_close': completed[-1] if completed else None,
                     'daily_bars': rows, 'adjustment_policy': 'provider-defined; not cross-provider comparable without checking', 'fallback_attempts': errors}, resp.body)
    except (NetError, ToolError, ValueError) as e:
        return unavailable(ctx, symbol, 'history', errors + ['stooq: ' + str(e)])


# =========================================================================================
# Options-implied move
# =========================================================================================
def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _two_sided(row: dict) -> bool:
    """A usable quote: a real bid, and an ask that is not below it."""
    b, a = row.get('bid'), row.get('ask')
    return _num(b) and _num(a) and b > 0 and a >= b


def _mid(row: dict) -> float:
    return (row['bid'] + row['ask']) / 2


def straddle(rows: list[dict], spot: float | None) -> dict:
    """The at-the-money straddle of one expiry: what the market charges for a move in either direction.

    Which strike is "at the money" is read from the options themselves. For a call and a put at the same strike,
    call price − put price = (the price the options imply for the stock) − strike. So the implied stock price is
    strike + call − put, and the at-the-money strike is the listed strike nearest to it. This matters because a
    quote feed can pair an after-hours stock price with option quotes from the session close; picking the strike
    from that stock price would select a deep in-the-money call and report a move that is far too large.

    Refuses (ToolError) rather than guess when the at-the-money strike has a zero, missing or crossed quote."""
    strikes: dict[float, dict] = {}
    for row in rows:
        k = row.get('strike')
        if _num(k) and k > 0:
            strikes.setdefault(k, {})[row['type']] = row
    common = [k for k, v in strikes.items() if 'call' in v and 'put' in v]
    if not common:
        raise ToolError('no same-strike ATM call/put pair')
    priced = [k for k in common if _two_sided(strikes[k]['call']) and _two_sided(strikes[k]['put'])]
    if priced:
        # The strike where call and put cost the same is the one the implied price sits on.
        pivot = min(priced, key=lambda k: abs(_mid(strikes[k]['call']) - _mid(strikes[k]['put'])))
        forward = pivot + _mid(strikes[pivot]['call']) - _mid(strikes[pivot]['put'])
        basis = 'put-call parity at strike %g' % pivot
    elif _num(spot) and spot > 0:
        forward, basis = float(spot), 'quoted underlying (no strike had two-sided quotes on both legs)'
    else:
        raise ToolError('missing positive underlying price')
    k = min(common, key=lambda x: abs(x - forward))
    c, p = strikes[k]['call'], strikes[k]['put']
    if not (_two_sided(c) and _two_sided(p)):
        raise ToolError('ATM bid/ask invalid, crossed or zero; no implied move computed')
    if priced and k != pivot:   # re-read the implied price at the strike actually used
        forward, basis = k + _mid(c) - _mid(p), 'put-call parity at strike %g' % k
    move = _mid(c) + _mid(p)
    warnings = []
    if _num(spot) and spot > 0 and abs(spot - forward) / forward > UNDERLYING_TOLERANCE:
        warnings.append(f'quoted underlying {spot:g} differs from the option-implied price {forward:.2f} by {100 * abs(spot - forward) / forward:.1f}%: '
                        'the stock quote and the option quotes are not from the same moment (for example an after-hours stock price next to session-close option quotes). '
                        'The straddle describes the market at the time of the OPTION quotes, not at the time of the newer share price. The percent below uses the option-implied price.')
    for leg, row in (('call', c), ('put', p)):
        if (row['ask'] - row['bid']) / _mid(row) > WIDE_SPREAD:
            warnings.append(f'{leg} bid/ask is wide ({row["bid"]:g}/{row["ask"]:g}); the mid is a rough estimate')
    return {'strike': k, 'call': c, 'put': p, 'move_dollars': move, 'move_percent': 100 * move / forward,
            'implied_underlying': round(forward, 4), 'implied_underlying_basis': basis, 'quoted_underlying': spot,
            'move_percent_of_quoted_underlying': 100 * move / spot if _num(spot) and spot > 0 else None, 'warnings': warnings,
            'formula': '(call bid+ask)/2 + (put bid+ask)/2 at the listed strike nearest to (strike + call mid − put mid); percent = 100 * straddle / that option-implied price',
            'interpretation': 'ATM straddle proxy through expiry, NOT a pure earnings forecast or a calibrated probability; includes all time/volatility to expiry. Delayed quotes may be stale.'}


_OCC = re.compile(r'^(?P<root>.+?)(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$')


def cboe_rows(options: list[dict], symbol: str, event: date) -> tuple[list[dict], int]:
    """(standard contracts expiring strictly after `event`, number of non-standard contracts skipped).

    Only the ticker's own root is used. Other roots in the same feed (for example adjusted contracts left by a
    split or special dividend) deliver something other than 100 plain shares and must not be mixed in."""
    root = symbol.lstrip('^_')
    rows, skipped = [], 0
    for o in options:
        m = _OCC.match(str(o.get('option', '')))
        if not m:
            continue
        if m['root'] != root:
            skipped += 1
            continue
        expiry = datetime.strptime(m['ymd'], '%y%m%d').date()
        if expiry > event:
            rows.append({'expiry': expiry.isoformat(), 'type': 'call' if m['cp'] == 'C' else 'put', 'strike': int(m['strike']) / 1000,
                         'bid': o.get('bid'), 'ask': o.get('ask'), 'contract': o.get('option'), 'volume': o.get('volume'), 'open_interest': o.get('open_interest'),
                         'last_trade_time': o.get('last_trade_time')})
    return rows, skipped


def _cboe_get(ctx: Ctx, url: str):
    """Cboe's delayed-quote JSON. A refusal (403) is retried once the way its own quote page asks for the file."""
    try:
        return ctx.client.get(url, 'cboe', ttl=0, timeout=15, attempts=2, headers={'Accept': 'application/json'})
    except NetError as exc:
        if exc.status != 403:
            raise
        return ctx.client.get(url, 'cboe', ttl=0, timeout=15, attempts=1,
                              headers={**BROWSER_HEADERS, 'Referer': 'https://www.cboe.com/', 'Origin': 'https://www.cboe.com'})


def options(ctx: Ctx, symbol: str, earnings_date: str) -> str:
    symbol = symbol_check(symbol)
    try:
        event = date.fromisoformat(earnings_date)
    except ValueError:
        raise ToolError('earnings_date must be YYYY-MM-DD from a verified source') from None
    if event < date.today():
        raise ToolError('earnings_date is in the past; no current-chain earnings move can be computed')
    provenance = 'caller supplied: independently verify; NOT confirmed by options feed'
    url = config.BASES['cboe'] + '/' + quote(symbol) + '.json'
    errors = []
    try:
        resp = _cboe_get(ctx, url)
        root = resp.json()
        data = root['data']
        rows, skipped = cboe_rows(data.get('options') or [], symbol, event)
        expiries = sorted({r['expiry'] for r in rows})
        if not expiries:
            raise ToolError('no expiry strictly after earnings date')
        expiry = expiries[0]
        chain = [r for r in rows if r['expiry'] == expiry]
        try:
            calculation = straddle(chain, data.get('current_price'))
        except ToolError as e:
            calculation = {'status': 'UNAVAILABLE', 'reason': str(e)}
        return saved(ctx, url, symbol, 'cboe', {
            'provider_timestamp_raw': root.get('timestamp'), 'provider_timestamp_zone': 'unspecified by payload; do not assume local time',
            'underlying': data.get('current_price'), 'underlying_last_trade_time_raw': data.get('last_trade_time'), 'underlying_session_close': data.get('close'),
            'earnings_date': earnings_date, 'earnings_date_provenance': provenance,
            'selected_expiry': expiry, 'implied_move': calculation, 'chain_contracts': len(chain), 'non_standard_contracts_skipped': skipped,
            'chain': chain, 'chain_location': 'Selected-expiry chain is in saved text; full provider payload including other expiries is saved raw'}, resp.body)
    except (NetError, ToolError, KeyError, ValueError, TypeError) as e:
        errors.append('cboe: ' + str(e))
    # Yahoo, through its anonymous visitor session. Two requests: the expiry list, then the chosen expiry's chain.
    try:
        path = '/v7/finance/options/' + quote(symbol)
        session = yahoo_session(ctx)
        first, _raw, _ = _yahoo_json(ctx, path, {}, session)
        res = ((first.get('optionChain') or {}).get('result') or [{}])[0]
        after = sorted(e for e in res.get('expirationDates') or [] if datetime.fromtimestamp(e, timezone.utc).date() > event)
        if not after:
            raise ToolError('no expiry strictly after earnings date')
        chosen, raw, public = _yahoo_json(ctx, path, {'date': after[0]}, session)
        res = ((chosen.get('optionChain') or {}).get('result') or [{}])[0]
        block = (res.get('options') or [{}])[0]
        expiry = datetime.fromtimestamp(after[0], timezone.utc).date().isoformat()
        chain = [{'expiry': expiry, 'type': typ, 'strike': o.get('strike'), 'bid': o.get('bid'), 'ask': o.get('ask'), 'contract': o.get('contractSymbol'),
                  'volume': o.get('volume'), 'open_interest': o.get('openInterest'), 'last_trade_time': o.get('lastTradeDate')}
                 for typ, key in (('call', 'calls'), ('put', 'puts')) for o in block.get(key) or []]
        spot = (res.get('quote') or {}).get('regularMarketPrice')
        try:
            calculation = straddle(chain, spot)
        except ToolError as e:
            calculation = {'status': 'UNAVAILABLE', 'reason': str(e)}
        return saved(ctx, public, symbol, 'yahoo', {
            'underlying': spot, 'earnings_date': earnings_date, 'earnings_date_provenance': provenance, 'selected_expiry': expiry,
            'implied_move': calculation, 'chain_contracts': len(chain), 'chain': chain, 'fallback_attempts': errors,
            'session': 'anonymous visitor cookie + crumb (no login); the crumb is not stored'}, raw)
    except (NetError, ToolError, KeyError, ValueError, TypeError, IndexError) as e:
        return unavailable(ctx, symbol, 'options', errors + ['yahoo: ' + str(e)])


# =========================================================================================
# Earnings dates
# =========================================================================================
def _nasdaq(ctx: Ctx, path: str) -> tuple[dict, str, str]:
    url = config.BASES['nasdaq'] + path
    resp = ctx.client.get(url, 'nasdaq', headers=BROWSER_HEADERS, ttl=0, attempts=2, timeout=12)
    data = resp.json().get('data')
    if not data:
        raise ToolError('no public data returned')
    return data, resp.text(), url


def sec_earnings_releases(ctx: Ctx, symbol: str, limit: int = 8) -> tuple[list[dict], str]:
    """Past earnings releases from the company's own filings: every 8-K that carries Item 2.02
    ("Results of Operations and Financial Condition"), newest first. Past dates only."""
    from .primary import _cik, _sec_get
    cik = _cik(ctx, symbol)
    url = f"{config.BASES['sec_data']}/submissions/CIK{cik}.json"
    rec = (_sec_get(ctx, url).json().get('filings') or {}).get('recent') or {}
    out = []
    for i, form in enumerate(rec.get('form') or []):
        if form == '8-K' and '2.02' in str((rec.get('items') or [''] * (i + 1))[i]).split(','):
            acc = rec['accessionNumber'][i]
            out.append({'filed': rec['filingDate'][i], 'accepted_utc': (rec.get('acceptanceDateTime') or [''] * (i + 1))[i], 'items': rec['items'][i],
                        'url': f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{rec['primaryDocument'][i]}"})
            if len(out) >= limit:
                break
    return out, url


def earnings(ctx: Ctx, symbol: str) -> str:
    symbol = symbol_check(symbol)
    record: dict = {}
    raw: dict = {}
    attempts: list[str] = []
    providers: list[str] = []
    # 1. Nasdaq: the expected next report date and the analyst forecast it publishes (credited there to Zacks).
    try:
        data, body, url = _nasdaq(ctx, '/api/analyst/' + quote(symbol) + '/earnings-date')
        text = ' '.join(str(data.get('reportText') or '').split())
        upcoming = {'source_url': url, 'announcement': data.get('announcement'), 'report_text': text or None,
                    'confirmed_by_company': False,
                    'reliability': "Nasdaq marks this date 'expected*': an estimate until the company announces it. Confirm on the issuer's investor-relations page."}
        m = re.search(r'on\s+(\d{1,2})/(\d{1,2})/(\d{4})', text)
        if m:
            upcoming['expected_date'] = f'{int(m[3]):04d}-{int(m[1]):02d}-{int(m[2]):02d}'
        m = re.search(r'(before market open|after market close|during market hours)', text, re.I)
        if m:
            upcoming['expected_time_of_day'] = m[1].lower()
        m = re.search(r'consensus EPS forecast for the quarter is \$?(-?[\d.]+)', text)
        if m:
            upcoming['consensus_eps_forecast'] = m[1].rstrip('.')
        m = re.search(r'based on\s+(\d+)\s+analysts', text)
        if m:
            upcoming['analysts_in_forecast'] = int(m[1])
        if data.get('epsForecast') is not None:
            upcoming['eps_forecast_field'] = data['epsForecast']
        record['upcoming_nasdaq'] = upcoming
        raw[url] = body
        providers.append('nasdaq')
        attempts.append('nasdaq earnings-date: OK')
    except (NetError, ToolError, ValueError) as e:
        attempts.append('nasdaq earnings-date: ' + str(e))
    # 2. Nasdaq: the last few reported quarters (date reported, EPS, forecast). Optional.
    if 'nasdaq' in providers:
        try:
            data, body, url = _nasdaq(ctx, '/api/company/' + quote(symbol) + '/earnings-surprise')
            rows = ((data.get('earningsSurpriseTable') or {}).get('rows')) or []
            if rows:
                record['reported_quarters_nasdaq'] = {'source_url': url, 'rows': rows[:6], 'note': 'EPS figures as published by Nasdaq (non-GAAP conventions vary); check the company release for the official numbers.'}
                raw[url] = body
                attempts.append('nasdaq earnings-surprise: OK')
        except (NetError, ToolError, ValueError) as e:
            attempts.append('nasdaq earnings-surprise: ' + str(e))
    # 3. SEC: the company's own 8-K Item 2.02 filings. Authoritative for PAST report dates; says nothing about the next one.
    try:
        rows, url = sec_earnings_releases(ctx, symbol)
        if rows:
            record['past_earnings_releases_sec'] = {'source_url': url, 'rows': rows,
                                                    'note': '8-K filings with Item 2.02 (results of operations), newest first. Past dates only; open one with sec_get for the reported figures.'}
            raw[url] = json.dumps(rows)
            providers.append('sec')
            attempts.append(f'sec 8-K item 2.02: OK ({len(rows)} filings)')
        else:
            attempts.append('sec 8-K item 2.02: no such filings listed')
    except (NetError, ToolError, Unavailable, KeyError, ValueError, IndexError) as e:
        attempts.append('sec 8-K item 2.02: ' + str(e))
    # 4. Yahoo calendar + estimates, only when Nasdaq gave no date (it needs the anonymous session and is often rate-limited).
    if 'nasdaq' not in providers:
        try:
            d, body, url = _yahoo_json(ctx, '/v10/finance/quoteSummary/' + quote(symbol), {'modules': 'calendarEvents,earningsTrend'})
            res = (d.get('quoteSummary') or {}).get('result') or []
            if not res:
                raise ToolError('no calendar/estimates returned')
            record['calendar_and_estimates_yahoo'] = {'source_url': url, 'data': res,
                                                      'reliability': 'Provider estimates only, may be provisional. Not a licensed consensus feed; confirm date with issuer IR.'}
            raw[url] = body.decode('utf-8', 'replace')
            providers.append('yahoo')
            attempts.append('yahoo calendar: OK')
        except (NetError, ToolError, ValueError) as e:
            attempts.append('yahoo: ' + str(e))
    if not providers:
        return unavailable(ctx, symbol, 'earnings', attempts + ['No reliable universal free consensus feed. Check issuer IR for confirmed dates; no EPS estimates invented.'])
    if 'upcoming_nasdaq' not in record and 'calendar_and_estimates_yahoo' not in record:
        record['upcoming_status'] = 'UNAVAILABLE: no free source gave the NEXT report date or an analyst forecast this time. Only past dates below. Check the issuer investor-relations page.'
    record['attempts'] = attempts
    record['how_to_read'] = (('upcoming_nasdaq is an EXPECTED date (not confirmed by the company). ' if 'upcoming_nasdaq' in record else '')
                             + 'past_earnings_releases_sec are the company\'s own filings and are authoritative for past dates only. '
                             'The issuer\'s investor-relations page is the authority for the next date.')
    return saved(ctx, 'market://earnings/' + symbol, symbol, '+'.join(providers), record,
                 json.dumps({'note': 'exact provider response bodies, keyed by URL', 'responses': raw}, ensure_ascii=False).encode('utf-8'))
