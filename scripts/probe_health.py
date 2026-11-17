import json, os, random, threading, time, urllib.parse
from collections import Counter, defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

GEO = r'C:\Users\Janus\OneDrive\Documentos\Proyecto predeterminado\ASCENSION-source\public\cameras.geojson'
OUT = r'C:\Users\Janus\OneDrive\Documentos\Proyecto predeterminado\ASCENSION-source\scripts\data'
PROXY = 'http://127.0.0.1:8787/api/proxy?url='
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
N_UNIFORM = int(os.getenv('NEXUS_PROBE_N', '6000'))
PER_SOURCE = int(os.getenv('NEXUS_PROBE_PER_SOURCE', '40'))
TOP_SOURCES = 30

os.makedirs(OUT, exist_ok=True)
t0 = time.time()

d = json.load(open(GEO, encoding='utf-8'))
fs = d['features']

def is_de(p):
    return str(p.get('directEligible')).strip().lower() in ('true', '1')

cams = []
for f in fs:
    p = f['properties']
    live = bool((p.get('streamUrl') or '').strip())
    if not (is_de(p) or live):
        continue
    stream = (p.get('streamUrl') or '').strip()
    feed = (p.get('feedUrl') or '').strip()
    target = stream or feed
    if not target or not target.lower().startswith(('http://', 'https://')):
        continue
    c = f.get('geometry', {}).get('coordinates') or []
    cams.append({
        'id': p.get('id'),
        'source': p.get('source') or '',
        'ft': p.get('feedType') or '',
        'feed': feed,
        'stream': stream,
        'url': target,
        'lat': c[1] if len(c) > 1 else None,
        'lon': c[0] if len(c) > 0 else None,
        'cc': p.get('country') or '',
    })
print('probe-eligible cameras', len(cams), flush=True)

rng = random.Random(20261116)
uniform = rng.sample(cams, min(N_UNIFORM, len(cams)))
uniform_ids = {c['id'] for c in uniform}

by_src = defaultdict(list)
for c in cams:
    by_src[c['source']].append(c)
extra = []
for src, _ in sorted(((s, len(v)) for s, v in by_src.items()), key=lambda kv: -kv[1])[:TOP_SOURCES]:
    v = by_src[src]
    extra.extend(rng.sample(v, min(PER_SOURCE, len(v))))

seen = set()
sample = []
for c in uniform + extra:
    if c['id'] in seen:
        continue
    seen.add(c['id'])
    sample.append(c)
print('sample size', len(sample), '(uniform', len(uniform), '+ source top-up)', flush=True)

IMAGE_MAGIC = (
    b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a', b'BM',
)

def looks_image(b):
    if any(b.startswith(m) for m in IMAGE_MAGIC):
        return True
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return True
    return False

host_sem = defaultdict(lambda: threading.BoundedSemaphore(6))
host_lock = threading.Lock()
proxy_sem = threading.BoundedSemaphore(36)

def host_of(url):
    try:
        return (urllib.parse.urlparse(url).hostname or '').lower()
    except Exception:
        return ''

def fetch(session, url, timeout, stream=True):
    r = session.get(url, timeout=(min(6, timeout), timeout), stream=stream,
                    allow_redirects=True, headers={'User-Agent': UA})
    return r

def validate(cam, resp, body):
    ct = (resp.headers.get('content-type') or '').lower()
    url = resp.url or cam['url']
    lower = url.lower().split('?')[0]
    if cam['ft'] == 'iframe':
        xfo = (resp.headers.get('x-frame-options') or '').lower()
        csp = (resp.headers.get('content-security-policy') or '').lower()
        if 'deny' in xfo or 'sameorigin' in xfo:
            return False, 'iframe_blocked_xfo'
        if 'frame-ancestors' in csp and '*' not in csp.split('frame-ancestors', 1)[1][:80]:
            return False, 'iframe_blocked_csp'
        if resp.status_code == 200 and ('text/html' in ct or not ct):
            return True, 'iframe_ok'
        return False, f'iframe_bad_ct_{ct[:20]}'
    if 'text/html' in ct:
        return False, 'html_error_page'
    if cam['ft'] == 'txdot-json' or lower.endswith('.json'):
        try:
            obj = json.loads(body.decode('utf-8', 'replace'))
        except Exception:
            return False, 'json_parse'
        if isinstance(obj, dict):
            snippet = obj.get('snippet')
            if isinstance(snippet, str) and len(snippet) > 100:
                return True, 'txdot_ok'
            if any(k in obj for k in ('image', 'imageUrl', 'url', 'data')):
                return True, 'json_ok'
        return False, 'json_no_image'
    if cam['ft'] in ('m3u8', 'mp4') or 'mpegurl' in ct or lower.endswith('.m3u8'):
        if b'#EXTM3U' in body:
            return True, 'hls_ok'
        if 'video/' in ct and len(body) > 1024:
            return True, 'video_ok'
        return False, 'hls_bad'
    if looks_image(body):
        if len(body) < 200:
            return False, 'image_too_small'
        return True, 'image_ok'
    if 'image/' in ct and len(body) > 200:
        return True, 'image_ct_ok'
    if ct.startswith('video/'):
        return True, 'video_ct_ok'
    return False, f'bad_content_{ct[:20] or "none"}'

def probe(cam):
    session = requests.Session()
    session.trust_env = False
    host = host_of(cam['url'])
    sem = host_sem[host]
    started = time.time()
    result = {'id': cam['id'], 'source': cam['source'], 'ft': cam['ft'], 'cc': cam['cc'],
              'url': cam['url'][:300], 'ok': False, 'via': '', 'reason': '', 'ms': 0,
              'uniform': cam['id'] in uniform_ids}

    # 1) direct
    should_proxy = True
    try:
        with sem:
            r = fetch(session, cam['url'], 5)
        body = r.raw.read(8192, decode_content=True) or b''
        status = r.status_code
        r.close()
        if status == 200:
            ok, reason = validate(cam, r, body)
            if ok:
                result.update(ok=True, via='direct', reason=reason)
                result['ms'] = int((time.time() - started) * 1000)
                return result
            result['reason'] = reason
        elif status in (401, 403, 429):
            result['reason'] = f'http_{status}'
        elif status in (404, 410):
            result.update(reason=f'http_{status}', ms=int((time.time() - started) * 1000))
            return result
        else:
            result['reason'] = f'http_{status}'
    except requests.exceptions.Timeout:
        result['reason'] = 'Timeout'
    except requests.exceptions.SSLError:
        result['reason'] = 'SSLError'
        should_proxy = False
    except requests.exceptions.ConnectionError:
        result['reason'] = 'ConnectionError'
        # Same resolver/network as the proxy for DNS or refused sockets, so a
        # fallback would just burn another timeout. HTTP-level blocks still do.
        should_proxy = False
    except Exception as e:
        result['reason'] = type(e).__name__

    # 2) local proxy fallback (same path the app uses)
    if not should_proxy:
        result['ms'] = int((time.time() - started) * 1000)
        return result
    try:
        purl = PROXY + urllib.parse.quote(cam['url'], safe='')
        with proxy_sem:
            r = fetch(session, purl, 14)
        body = r.raw.read(8192, decode_content=True) or b''
        status = r.status_code
        r.close()
        if status == 200:
            ok, reason = validate(cam, r, body)
            if ok:
                result.update(ok=True, via='proxy', reason=reason)
                result['ms'] = int((time.time() - started) * 1000)
                return result
            result['reason'] = reason
        else:
            try:
                err = json.loads(body.decode('utf-8', 'replace')).get('error', '')
            except Exception:
                err = ''
            result['reason'] = f'proxy_http_{status}' + (f':{err[:40]}' if err else '')
    except Exception as e:
        result['reason'] = (result['reason'] + '/proxy_' if result['reason'] else '') + type(e).__name__

    result['ms'] = int((time.time() - started) * 1000)
    return result

jsonl_path = os.path.join(OUT, 'health_probe.jsonl')
done = [0]
lock = threading.Lock()

# Resume support: the probe writes one JSON line per camera as it finishes, so a
# crash or restart should not throw away hours of work. Re-read what is already
# on disk, keep it for the summary, and only probe the missing ids.
prior = []
if os.path.exists(jsonl_path):
    with open(jsonl_path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                prior.append(json.loads(line))
            except Exception:
                continue
already = {r.get('id') for r in prior if r.get('id')}
if already:
    print(f'resuming: {len(already)} cameras already probed', flush=True)
sample = [c for c in sample if c['id'] not in already]
print(f'remaining: {len(sample)}', flush=True)

fout = open(jsonl_path, 'a', encoding='utf-8')

def work(cam):
    res = probe(cam)
    with lock:
        fout.write(json.dumps(res, ensure_ascii=False) + '\n')
        fout.flush()
        done[0] += 1
        if done[0] % 250 == 0:
            print(f'  {done[0]}/{len(sample)}  {time.time()-t0:6.0f}s', flush=True)
    return res

results = list(prior)
with ThreadPoolExecutor(max_workers=96) as ex:
    futs = [ex.submit(work, c) for c in sample]
    for fu in as_completed(futs):
        results.append(fu.result())
fout.close()

def rate(rows):
    n = len(rows)
    ok = sum(1 for r in rows if r['ok'])
    p = ok / n if n else 0
    se = (p * (1 - p) / n) ** 0.5 if n else 0
    return {'n': n, 'ok': ok, 'rate_pct': round(100 * p, 2),
            'ci95': [round(100 * max(0, p - 1.96 * se), 2), round(100 * min(1, p + 1.96 * se), 2)]}

overall = rate([r for r in results if r['uniform']])
by_source = {}
for src in sorted({r['source'] for r in results}):
    rows = [r for r in results if r['source'] == src]
    if len(rows) >= 15:
        by_source[src] = rate(rows)
reasons = Counter(r['reason'].split('/')[0] for r in results if not r['ok'])
via = Counter(r['via'] for r in results if r['ok'])
by_ft = {}
for ft in sorted({r['ft'] for r in results}):
    rows = [r for r in results if r['ft'] == ft]
    if rows:
        by_ft[ft or '(none)'] = rate(rows)

summary = {
    'sample_size': len(results),
    'uniform_size': len([r for r in results if r['uniform']]),
    'overall_uniform': overall,
    'by_feed_type': by_ft,
    'by_source': by_source,
    'success_via': dict(via),
    'failure_reasons': dict(reasons.most_common(20)),
    'elapsed_seconds': round(time.time() - t0, 1),
    'probed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
json.dump(summary, open(os.path.join(OUT, 'health_summary.json'), 'w', encoding='utf-8'),
          indent=2, ensure_ascii=False)
print(json.dumps(summary, indent=2, ensure_ascii=False))
