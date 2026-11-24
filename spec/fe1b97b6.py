import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse, unquote
from typing import Literal

import requests
from pydantic import BaseModel, RootModel
from notte_sdk import NotteClient
from notte_core.errors.actions import ActionExecutionError


class OfferRecord(BaseModel):
    title: str
    jw_id: str
    object_type: Literal['movie', 'tv-show', 'UNKNOWN']
    release_year: int | Literal['UNKNOWN']
    country: str
    country_name: str
    available: bool
    provider: str
    monetization_type: Literal['subscription', 'rent', 'buy', 'free', 'free_ads', 'UNKNOWN']
    quality: Literal['SD', 'HD', '4K', 'UNKNOWN']
    price: str
    currency: str
    audio_languages: list[str] | Literal['UNKNOWN']
    subtitle_languages: list[str] | Literal['UNKNOWN']
    deeplink: str
    source_url: str
    observed_at: str
    flags: list[str]


class OfferRecords(RootModel[list[OfferRecord]]):
    pass


HELPERS = r'''
function embeddedTable() {
  const e = document.querySelector('#__NUXT_DATA__');
  if (!e) return [];
  try { return JSON.parse(e.textContent); } catch (e) { return []; }
}
function stateCache() {
  if (window.__APOLLO_CLIENT__) return window.__APOLLO_CLIENT__.cache.extract();
  const t = embeddedTable(), memo = new Map();
  function decode(i) {
    if (i < 0) return null;
    if (memo.has(i)) return memo.get(i);
    const x=t[i];
    if (!x || typeof x !== 'object') return x;
    if (Array.isArray(x)) {
      if (typeof x[0] === 'string' && ['Reactive','ShallowReactive','Ref','ShallowRef'].includes(x[0])) return decode(x[1]);
      const a=[]; memo.set(i,a); for (const v of x) a.push(decode(v)); return a;
    }
    const o={}; memo.set(i,o); for (const [k,v] of Object.entries(x)) o[k]=decode(v); return o;
  }
  const idx=t.findIndex(x=>x && !Array.isArray(x) && typeof x==='object' && 'ROOT_QUERY' in x);
  return idx<0 ? {} : decode(idx);
}
function deref(v,c) { return v && v.__ref ? c[v.__ref] : v; }
function contents(n,country) {
  if (!n) return {};
  const key=Object.keys(n).find(k=>k.startsWith('content(') && k.includes('"country":"'+country+'"'));
  return key ? n[key] : {};
}
function allLocales() {
  const ctx=window.__nuxt_app_ctx__ && window.__nuxt_app_ctx__['nuxt-app'];
  const app=ctx && ctx.tryUse();
  const live=app && app.$pinia && app.$pinia.state.value.language;
  if (live && live.localesState && live.localesState.length>1) return live.localesState;
  const t=embeddedTable();
  return t.filter(x=>x && !Array.isArray(x) && typeof x==='object' && 'iso_3166_2' in x)
    .map(x=>Object.fromEntries(['iso_3166_2','country','currency','exposed_url_part','full_locale','title_urls_enabled'].map(k=>[k,t[x[k]]])));
}
'''

CONSENT = r'''(()=>{
 const buttons=[...document.querySelectorAll('button')];
 const b=buttons.find(b=>b.textContent.trim()==='I agree' && /privacy|data|history|cookie/i.test((b.parentElement.parentElement.parentElement.textContent||'')));
 if(b){b.click();return 'dismissed';}
 return 'none';
})()'''

SNAPSHOT = r'''(()=>{HELPERS
 const c=stateCache(), path=decodeURI(location.pathname);
 const u=Object.values(c).find(x=>x && x.__typename==='Url' && decodeURI(x.fullPath||'')===path);
 const n=u && deref(u.node,c);
 const country=(allLocales().find(x=>x.webLocale===path.split('/')[1] || x.exposed_url_part===path.split('/')[1])||{}).iso_3166_2;
 const body=document.body ? document.body.innerText : '';
 const nav=performance.getEntriesByType('navigation')[0];
 return JSON.stringify({url:location.href, canonical:document.querySelector('link[rel=canonical]')?.href||'',
 status:nav?.responseStatus||0, pageTitle:document.title, body:body.slice(0,5000),
 ready:!!document.querySelector('#__NUXT_DATA__') || !!n, id:n?.id||'', type:n?.objectType||'',
 content:n ? contents(n,country||'US') : {}, alternates:u?.hrefLangTags||[]});
})()'''.replace('HELPERS', HELPERS)

SEARCH = r'''(()=>{HELPERS
 const c=stateCache(), root=c.ROOT_QUERY||{};
 const key=Object.keys(root).find(k=>k.startsWith('searchTitles(') && k.includes('"country":"US"') && k.includes('"source":"SearchPage"'));
 const edges=key ? root[key].edges||[] : [];
 return JSON.stringify(edges.map(e=>{const n=deref(e.node,c), v=contents(n,'US'); return {id:n.id,type:n.objectType,title:v.title,year:v.originalReleaseYear,path:v.fullPath};}));
})()'''.replace('HELPERS', HELPERS)

LOCALES = r'''(()=>{HELPERS return JSON.stringify(allLocales());})()'''.replace('HELPERS', HELPERS)

FALLBACK = r'''(()=>{HELPERS
 const c=stateCache(), n=c[TARGETKEY]; if(!n) return JSON.stringify({found:false,offers:[]});
 const keys=Object.keys(n).filter(k=>k.startsWith('offers(') && k.includes('"country":"COUNTRYCODE"') && !k.includes('"bestOnly"') && !k.includes('"bestOnlyByPackageAndMonetization"') && !k.includes('"packages"') && !k.includes('"presentationTypes"'));
 const key=keys.find(k=>!k.includes('monetizationTypes'))||keys[0];
 if(!key) return JSON.stringify({found:false,offers:[]});
 const list=n[key].map(r=>{const o={...deref(r,c)};o.package=deref(o.package,c);if(!o.country){o.country='COUNTRYCODE';o.countryFromParentFilter=true;}
 for(const k of Object.keys(o)){if(k.startsWith('audioLanguages('))o.audioLanguages=o[k];}
 return o;});
 return JSON.stringify({found:true,offers:list});
})()'''.replace('HELPERS', HELPERS)


def observed_time() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def text(value) -> str:
    if value is None or value == '':
        return 'UNKNOWN'
    return str(value)


def eval_value(session, script: str):
    for attempt in range(3):
        try:
            value = session.evaluate_js(script)
        except ActionExecutionError:
            if attempt == 2:
                raise
            value = ''
        if value:
            return json.loads(value)
        session.execute(type='wait', time_ms=500 * (attempt + 1))
    raise RuntimeError('JustWatch page evaluation returned no data after three attempts')


def dismiss_consent(session) -> None:
    session.evaluate_js(CONSENT)


def challenge(page) -> bool:
    title = str(page.get('pageTitle', '')).lower()
    body = str(page.get('body', '')).lower()
    return any(k in title for k in ['just a moment', 'attention required', 'security verification']) or any(k in body[:800] for k in ['verifying you are human', 'performing security verification', 'checking your browser'])


def navigate(session, url: str, clock: list[float]):
    for attempt in range(2):
        pause = 2.0 - (time.monotonic() - clock[0])
        if pause > 0:
            time.sleep(pause)
        result = session.execute(type='goto', url=url, wait_until='domcontentloaded')
        clock[0] = time.monotonic()
        for settling in range(3):
            dismiss_consent(session)
            page = eval_value(session, SNAPSHOT)
            if page.get('ready') or page.get('status', 0) >= 400 or challenge(page):
                break
            session.execute(type='wait', time_ms=700)
        if 'HTTP 404' in str(result.message):
            page['status'] = 404
        if challenge(page):
            session.execute(type='captcha_solve', captcha_type='cloudflare')
            page = eval_value(session, SNAPSHOT)
            if challenge(page):
                session.execute(type='wait', time_ms=1000)
                if attempt == 0:
                    continue
                raise RuntimeError('JustWatch navigation failed: security challenge remained after retry')
        return page
    raise RuntimeError('JustWatch navigation failed after two attempts')


def get_locales(session) -> list:
    session.execute(type='click', selector='.country-switcher__button')
    values = []
    for attempt in range(3):
        values = eval_value(session, LOCALES)
        if len(values) > 1:
            break
        session.execute(type='wait', time_ms=500)
    session.execute(type='click', selector='.country-switcher-modal .base-modal__header__icons button')
    if len(values) < 2:
        raise RuntimeError('JustWatch country directory did not load; refusing an incomplete ALL sweep')
    return values


def choose_title(candidates: list, requested: str, kind: str, year: int):
    exact = [x for x in candidates if str(x.get('title', '')).casefold().strip() == requested.casefold().strip()]
    if kind != 'UNKNOWN':
        exact = [x for x in exact if x.get('type') == ('MOVIE' if kind == 'movie' else 'SHOW')]
    if year:
        exact = [x for x in exact if x.get('year') == year]
    if not exact:
        raise ValueError('No observed exact-title match on the JustWatch US search page; specify title, object_type and release_year')
    slug = re.sub(r'[^a-z0-9]+', '-', requested.casefold()).strip('-')
    preferred = [x for x in exact if x.get('path', '').rsplit('/', 1)[-1] == slug]
    pool = preferred or exact
    if len(pool) != 1:
        raise ValueError('Ambiguous exact-title matches on JustWatch; supply object_type and release_year to avoid guessing')
    selected = pool[0]
    alternatives = [x for x in candidates if x.get('id') != selected.get('id')]
    flags = []
    if len(exact) > 1:
        flags.append('exact_slug_selected')
    if alternatives:
        flags.append('discovery_alternatives=' + json.dumps(alternatives, ensure_ascii=False, separators=(',', ':')))
    return selected, flags


def query_offers(session, identifier: str, country: str, kind: str):
    fragment = ('Movie' if kind == 'movie' else 'Show')
    query = ('query GetUrlTitleDetails { node(id:' + json.dumps(identifier) + ') { ... on ' + fragment +
             ' { id offers(country:' + country.upper() + ', platform:WEB, filter:{fallbackToForeignOffers:false}) '
             '{ id country monetizationType presentationType retailPriceValue currency standardWebURL '
             'audioLanguages(language:en) subtitleLanguages package { clearName packageId } } } } }')
    body = json.dumps({'operationName': 'GetUrlTitleDetails', 'query': query})
    script = ('(async()=>{const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),25000);'
              'try {const r=await fetch("https://apis.justwatch.com/graphql",{method:"POST",credentials:"omit",'
              'headers:{"content-type":"application/json"},signal:controller.signal,body:' + json.dumps(body) +
              '});return JSON.stringify({status:r.status,body:await r.json()});}finally{clearTimeout(timer);}})()')
    observed = eval_value(session, script)
    if observed.get('status') != 200:
        raise RuntimeError('JustWatch title-offer request failed for ' + country + ': HTTP ' + str(observed.get('status')))
    payload = observed.get('body') or {}
    if payload.get('errors'):
        raise RuntimeError('JustWatch title-offer response reported errors for ' + country)
    node = (payload.get('data') or {}).get('node')
    if not node or node.get('id') != identifier or not isinstance(node.get('offers'), list):
        raise RuntimeError('JustWatch title-offer response had no matching title for ' + country)
    return node['offers']


def language_value(value, label: str, flags: list[str]):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        flags.append(label + '_unavailable')
        return 'UNKNOWN'
    codes = [bool(re.fullmatch('[a-z]{2}', x)) for x in value]
    if any(codes) and not all(codes):
        flags.append(label + '_ambiguous_mixed_codes_and_names')
        return 'UNKNOWN'
    return value


def unavailable(meta, country, locale, source_url, flags) -> OfferRecord:
    return OfferRecord(title=meta['title'], jw_id=meta['id'], object_type=meta['kind'],
                       release_year=meta['year'], country=country, country_name=text(locale.get('country')),
                       available=False, provider='', monetization_type='UNKNOWN', quality='UNKNOWN',
                       price='UNKNOWN', currency='UNKNOWN', audio_languages='UNKNOWN', subtitle_languages='UNKNOWN',
                       deeplink='UNKNOWN', source_url=source_url, observed_at=observed_time(), flags=flags)


def make_records(offers, meta, country, locale, source_url, common_flags):
    records = []
    mapping = {'FLATRATE': 'subscription', 'RENT': 'rent', 'BUY': 'buy', 'ADS': 'free_ads', 'FREE': 'free'}
    for offer in offers:
        if offer.get('country') != country.upper():
            continue
        presentation = offer.get('presentationType')
        monetization = offer.get('monetizationType')
        if monetization == 'CINEMA' or presentation in ['DVD', 'BLURAY', 'BLURAY_4K', 'CANVAS']:
            continue
        flags = list(common_flags)
        if offer.get('countryFromParentFilter'):
            flags.append('offer_country_from_parent_filter')
        quality = {'SD': 'SD', 'HD': 'HD', '_4K': '4K'}.get(presentation, 'UNKNOWN')
        if quality == 'UNKNOWN':
            flags.append('quality_ambiguous=' + text(presentation))
        monetization_type = mapping.get(monetization, 'UNKNOWN')
        if monetization_type == 'UNKNOWN':
            flags.append('monetization_ambiguous=' + text(monetization))
        currency = text(offer.get('currency'))
        price = 'UNKNOWN'
        if monetization_type in ['subscription', 'free', 'free_ads']:
            price = '0'
            currency = text(locale.get('currency'))
            if currency == 'UNKNOWN':
                flags.append('market_currency_unavailable')
        elif offer.get('retailPriceValue') is not None:
            try:
                number = Decimal(str(offer['retailPriceValue']))
                if number.is_finite():
                    price = format(number, 'f')
            except InvalidOperation:
                flags.append('price_ambiguous')
        if price == 'UNKNOWN':
            flags.append('price_unavailable')
        audio = language_value(offer.get('audioLanguages'), 'audio_languages', flags)
        subtitles = language_value(offer.get('subtitleLanguages'), 'subtitle_languages', flags)
        if audio == 'UNKNOWN' and subtitles == 'UNKNOWN':
            flags.append('languages_unavailable')
        provider = text((offer.get('package') or {}).get('clearName'))
        if provider == 'UNKNOWN':
            flags.append('provider_unavailable')
        records.append(OfferRecord(title=meta['title'], jw_id=meta['id'], object_type=meta['kind'],
                     release_year=meta['year'], country=country, country_name=text(locale.get('country')),
                     available=True, provider=provider, monetization_type=monetization_type, quality=quality,
                     price=price, currency=currency, audio_languages=audio, subtitle_languages=subtitles,
                     deeplink=text(offer.get('standardWebURL')), source_url=source_url,
                     observed_at=observed_time(), flags=flags))
    return records


def run(title: str = 'The Matrix', markets: str = 'us', reference_market: str = 'us',
        object_type: str = 'UNKNOWN', release_year: int = 0) -> OfferRecords:
    """Observe one exact JustWatch title. markets accepts ordered ISO-2 codes or ALL.

    Uses the literal /{ISO-2}/{object_type}/{US-slug} paths requested: it never
    silently replaces a 404 with a translated route. Thus a route failure is
    not proof that the title is absent from that country's streaming catalogs.
    Missing language metadata is UNKNOWN, not a claim of no supported languages.
    Completed market arrays are checkpointed to run logs before the next visit.
    """
    title = str(title).strip()
    markets = str(markets).strip()
    reference_market = str(reference_market).strip().lower()
    object_type = str(object_type).strip()
    release_year = int(float(release_year))
    if not title or '{{' in title:
        raise ValueError('Provide a real title instead of a template placeholder')
    if reference_market != 'us':
        raise ValueError('reference_market must be us; discovery is US-only')
    if object_type not in ['movie', 'tv-show', 'UNKNOWN']:
        raise ValueError('object_type must be movie, tv-show, or UNKNOWN')
    if release_year < 0:
        raise ValueError('release_year must be a positive year or zero for no year filter')
    requested = [] if markets.upper() == 'ALL' else [c.strip().lower() for c in markets.split(',')]
    if markets.upper() != 'ALL' and (not requested or any(not re.fullmatch('[a-z]{2}', c) for c in requested)):
        raise ValueError('markets must be comma-separated ISO-2 codes or ALL')
    if len(set(requested)) != len(requested):
        raise ValueError('markets contains repeated country codes; pass each market once')
    client = NotteClient()
    collected = []
    clock = [0.0]
    with client.Session(proxies=True, solve_captchas=True, idle_timeout_minutes=15, max_duration_minutes=15) as session:
        navigate(session, 'https://www.justwatch.com/us/search?q=' + quote(title, safe=''), clock)
        candidates = []
        for attempt in range(3):
            candidates = eval_value(session, SEARCH)
            if candidates:
                break
            session.execute(type='wait', time_ms=1000)
        selected, flags = choose_title(candidates, title, object_type, release_year)
        discovery_url = 'https://www.justwatch.com' + selected['path']
        reference = navigate(session, discovery_url, clock)
        if reference.get('id') != selected['id']:
            raise RuntimeError('JustWatch discovery title page did not confirm the selected title identity')
        locales = get_locales(session)
        directory = {}
        for locale in locales:
            code = str(locale.get('iso_3166_2', '')).lower()
            if re.fullmatch('[a-z]{2}', code) and code not in directory:
                directory[code] = locale
        if markets.upper() == 'ALL':
            requested = list(directory)
        content = reference.get('content') or {}
        kind = 'movie' if selected['type'] == 'MOVIE' else 'tv-show'
        slug = selected['path'].rsplit('/', 1)[-1]
        meta = {'id': selected['id'], 'title': text(content.get('title') or selected.get('title')),
                'kind': kind, 'year': content.get('originalReleaseYear') or selected.get('year') or 'UNKNOWN'}
        flags.append('discovery_slug=' + slug)
        imdb = (content.get('externalIds') or {}).get('imdbId')
        if imdb:
            flags.append('discovery_imdb_id=' + str(imdb))
        alternate_urls = {}
        for alt in reference.get('alternates') or []:
            match = re.search(r'[_-]([A-Z]{2})$', str(alt.get('locale') or alt.get('hrefLang') or ''))
            if match and alt.get('href'):
                alternate_urls[match.group(1).lower()] = 'https://www.justwatch.com' + alt['href']
        for country in requested:
            locale = directory.get(country, {})
            attempted_url = 'https://www.justwatch.com/' + country + '/' + kind + '/' + slug
            source_url = alternate_urls.get(country, attempted_url)
            rows = []
            market_flags = list(flags)
            for attempt in range(2):
                try:
                    page = navigate(session, attempted_url, clock)
                    actual_path = unquote(urlparse(page.get('url', '')).path).rstrip('/')
                    expected_path = unquote(urlparse(attempted_url).path).rstrip('/')
                    if page.get('status') == 404:
                        rows = [unavailable(meta, country, locale, source_url, market_flags + ['http_404', 'attempted_url=' + attempted_url])]
                        break
                    if actual_path != expected_path:
                        rows = [unavailable(meta, country, locale, source_url, market_flags + ['redirected', 'redirect_url=' + text(page.get('url')), 'attempted_url=' + attempted_url])]
                        break
                    if 'not available in your country' in str(page.get('body', '')).lower():
                        rows = [unavailable(meta, country, locale, source_url, market_flags + ['not_available_in_your_country'])]
                        break
                    if page.get('status', 0) >= 400 or not page.get('ready'):
                        raise RuntimeError('JustWatch market page did not load for ' + country)
                    if page.get('id') != meta['id']:
                        raise RuntimeError('JustWatch market page did not confirm the same title for ' + country)
                    canonical = page.get('canonical')
                    if canonical and urlparse(canonical).path.split('/')[1] == country:
                        source_url = canonical
                    try:
                        offers = query_offers(session, meta['id'], country, kind)
                    except (RuntimeError, ActionExecutionError, requests.RequestException):
                        fallback_js = FALLBACK.replace('TARGETKEY', json.dumps(('Movie:' if kind == 'movie' else 'Show:') + meta['id'])).replace('COUNTRYCODE', country.upper())
                        fallback = eval_value(session, fallback_js)
                        if not fallback.get('found'):
                            raise RuntimeError('JustWatch offers unavailable in both title response and embedded data for ' + country)
                        offers = fallback['offers']
                        market_flags.append('embedded_offers_fallback')
                    rows = make_records(offers, meta, country, locale, source_url, market_flags)
                    if not rows:
                        rows = [unavailable(meta, country, locale, source_url, market_flags + ['no_offers_observed'])]
                    break
                except (RuntimeError, ActionExecutionError, requests.RequestException, ValueError):
                    if attempt == 1:
                        rows = [unavailable(meta, country, locale, source_url, market_flags + ['fetch_failed'])]
            collected.extend(rows)
            print(json.dumps({'checkpoint_country': country, 'records': [row.model_dump() for row in rows]}, ensure_ascii=False), flush=True)
    def record_order(record):
        return (record.country, record.provider)

    collected.sort(key=record_order)
    if not collected:
        raise RuntimeError('JustWatch sweep completed without any market records')
    return OfferRecords(collected)
