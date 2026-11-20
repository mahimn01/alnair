"""Bounded public-page retrieval. No logins, challenge solving or paid scraping."""
from __future__ import annotations
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, urlunsplit, urljoin
from . import config
from .common import ToolError, Unavailable, untrusted_preview
from .netsafe import NetError, split_url
from .extract import article_to_text, clean, looks_like, json_to_text, pdf_to_text

HEADERS = {"Accept":"text/html,application/xhtml+xml,application/pdf,application/json,text/plain;q=0.9,*/*;q=0.5",
           "Accept-Language":"en-US,en;q=0.9"}
# Honest identifiable UA, not a claim to be a signed-in browser.
BLOCK = re.compile(r"captcha|verify (?:that )?you are human|just a moment|access denied|enable javascript and cookies|subscribe to (?:read|continue)|checking your browser", re.I)


def url_variants(url):
    p=urlsplit(url)
    host=p.netloc
    hosts=[host, host[4:] if host.startswith('www.') else 'www.'+host]
    paths=[p.path, p.path.rstrip('/'), p.path.rstrip('/')+'/']
    return list(dict.fromkeys([url]+[urlunsplit((p.scheme,h,path,p.query,'')) for h in hosts for path in paths]))


class Alternates(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='link' and ('amphtml' in a.get('rel','').split() or
          ('alternate' in a.get('rel','').split() and a.get('type') in ('application/rss+xml','application/atom+xml'))):
            if a.get('href'): self.links.append(a['href'])


def fetch(ctx, url):
    from .primary import _robots_ok
    split_url(url)
    attempts=[]; alternates=[]; started=time.monotonic()

    def retrieve(target, label, archived=False):
        split_url(target)
        if time.monotonic()-started > 100:
            attempts.append(label+': skipped (100-second fallback budget)'); return None
        try:
            if not _robots_ok(ctx,target,timeout=5,attempts=1):
                attempts.append(label+': robots.txt disallows '+target); return None
            resp=ctx.client.get(target,'web',headers=HEADERS,timeout=8,attempts=1,max_bytes=config.MAX_BODY,tolerate_length=archived)
            if label.startswith('archive.today'):
                final=urlsplit(resp.url)
                archive_hosts={'archive.ph','archive.is','archive.today','archive.li','archive.vn','archive.fo','archive.md'}
                capture_path=bool(re.fullmatch(r'/[A-Za-z0-9]{5,10}/?',final.path) or re.match(r'/\d{4}\.\d{2}\.\d{2}-\d{6}/',final.path))
                if (final.hostname or '').removeprefix('www.') not in archive_hosts or not capture_path:
                    attempts.append(label+': no validated existing snapshot (lookup/landing page is not article evidence)'); return None
            typ=looks_like(resp.body,resp.headers.get('content-type',''))
            if typ=='html':
                parser=Alternates(); parser.feed(resp.text())
                if not archived: alternates.extend(urljoin(resp.url,x) for x in parser.links[:3])
                title,text=article_to_text(resp.text())
                # Refusal pages are not evidence; never save a challenge as the requested article.
                if BLOCK.search((title+' '+text)[:2000]) and len(text)<5000:
                    attempts.append(label+': challenge/paywall or refusal page'); return None
            elif typ=='pdf': title,text=url,pdf_to_text(resp.body)
            elif typ=='json': title,text=url,json_to_text(resp.json())
            elif typ=='text': title,text=url,clean(resp.text())
            else: raise ToolError('unsupported content type')
            if not text.strip(): raise ToolError('empty extracted text')
            attempts.append(label+': OK '+resp.url)
            ref=ctx.save('wayback' if archived else 'web',resp.url,title or url,text,raw=resp.body,
                         provider=label,extra={'original_url':url,'attempts':list(attempts),'extraction':'local article extraction; not an LLM summary',
                                               'archived':archived})
            return untrusted_preview(ctx,ref)+'\nAttempts:\n'+'\n'.join(attempts)
        except (NetError,ToolError,Unavailable) as e:
            if isinstance(e,NetError) and e.kind=='blocked': raise ToolError('blocked by safety policy: '+e.message)
            if isinstance(e,ToolError) and 'unsupported content type' in str(e): raise
            attempts.append(label+': '+str(e)); return None

    out=retrieve(url,'web')
    if out: return out
    for variant in url_variants(url):
        if time.monotonic()-started>100: break
        lookup=config.BASES['wayback_available']+'?'+urlencode({'url':variant})
        try:
            r=ctx.client.get(lookup,'wayback',timeout=6,attempts=1)
            snap=(r.json().get('archived_snapshots') or {}).get('closest') or {}
            attempts.append('wayback availability '+variant+': '+(str(snap.get('timestamp')) if snap.get('available') else 'no capture'))
            target=snap.get('url','')
            if snap.get('available') and str(snap.get('status'))=='200' and target:
                # Availability API may return http and toolbar URLs; retain actual capture timestamp.
                p=urlsplit(target)
                if p.hostname not in ('web.archive.org','127.0.0.1'):
                    attempts.append('wayback: refused unexpected snapshot host'); continue
                target=target.replace('http://web.archive.org','https://web.archive.org')
                target=re.sub(r'/web/(\d{14})/',r'/web/\1id_/',target)
                out=retrieve(target,'wayback snapshot '+str(snap.get('timestamp')),True)
                if out: return out
        except (NetError,ToolError) as e:
            attempts.append('wayback availability: '+str(e))
    # Read-only lookup of existing captures only. Never /submit, captcha solving, or paywall bypass.
    out=retrieve(config.BASES['archive_lookup']+'/'+url,'archive.today existing snapshot',True)
    if out: return out
    for target in list(dict.fromkeys(alternates))[:3]:
        if urlsplit(target).hostname != urlsplit(url).hostname:
            attempts.append('publisher alternate: skipped cross-host link'); continue
        out=retrieve(target,'publisher AMP/RSS alternate')
        if out: return out
    attempts.append('Google cache: unavailable (discontinued); AMP/RSS: only publisher-advertised links, never guessed')
    text='BLOCKED/UNAVAILABLE '+url+'\n'+'\n'.join(attempts)+'\nNo article text obtained; no figures verified. I do not try to bypass authentication, paywalls or challenges.\n'
    ref=ctx.save('fetch_attempt',url,'Blocked fetch '+url,text,provider='web',extra={'attempts':attempts})
    raise Unavailable('web',text+'Saved attempt log: '+ref.parts[0], 'SEC/issuer releases, another public source, or a user-supplied copy')
