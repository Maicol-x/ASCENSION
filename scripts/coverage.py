import json, math, time, os
import numpy as np
import global_land_mask as glm

PATH = r'C:\Users\Janus\OneDrive\Documentos\Proyecto predeterminado\ASCENSION-source\public\cameras.geojson'
OUT = r'C:\Users\Janus\OneDrive\Documentos\Proyecto predeterminado\ASCENSION-source\scripts\data\coverage.json'

R_EARTH = 6371.0088
DEG = 0.1
H = int(180 / DEG)          # 1800 rows
W = int(360 / DEG)          # 3600 cols
lat = -90 + (np.arange(H) + 0.5) * DEG
lon = -180 + (np.arange(W) + 0.5) * DEG
coslat = np.cos(np.radians(lat))

t0 = time.time()
d = json.load(open(PATH, encoding='utf-8'))
fs = d['features']
print('features', len(fs))

def is_de(p):
    return str(p.get('directEligible')).strip().lower() in ('true', '1')

cams = []
countries = set()
for f in fs:
    p = f['properties']
    if not (is_de(p) or (p.get('streamUrl') or '').strip()):
        continue
    c = f.get('geometry', {}).get('coordinates') or []
    if len(c) < 2:
        continue
    lo, la = c[0], c[1]
    if not (isinstance(lo, (int, float)) and isinstance(la, (int, float))):
        continue
    if not (-180 <= lo <= 180 and -90 <= la <= 90):
        continue
    cams.append((lo, la))
    if p.get('country'):
        countries.add(p['country'])
print('displayable with coords', len(cams), 'countries', len(countries))

# unique occupied cells
cells = set()
for lo, la in cams:
    i = min(H - 1, max(0, int((la + 90) / DEG)))
    j = int((lo + 180) / DEG) % W
    cells.add((i, j))
cells = sorted(cells)
print('unique occupied cells', len(cells))

lat_r = np.radians(lat)

def covered_for_radius(radius_km):
    covered = np.zeros((H, W), dtype=bool)
    max_dlat = radius_km / (111.132 * DEG)
    k0 = int(math.ceil(max_dlat))
    for ii, jj in cells:
        la1 = lat_r[ii]
        lon1 = math.radians(lon[jj])
        for di in range(-k0, k0 + 1):
            r2 = ii + di
            if r2 < 0 or r2 >= H:
                continue
            d_ns = abs(di) * 111.132 * DEG
            if d_ns > radius_km:
                continue
            la2 = lat_r[r2]
            rem = radius_km ** 2 - d_ns ** 2
            if rem <= 0:
                continue
            cos_a = max(1e-6, math.cos(la1))
            dlon_deg = math.sqrt(rem) / (111.132 * cos_a)
            kdj = int(math.ceil(dlon_deg / DEG))
            if kdj > W // 2:
                kdj = W // 2
            idx = (jj + np.arange(-kdj, kdj + 1)) % W
            lon2 = np.radians(lon[idx])
            dlat = la2 - la1
            dlon = lon2 - lon1
            a = (np.sin(dlat / 2) ** 2
                 + math.cos(la1) * math.cos(la2) * np.sin(dlon / 2) ** 2)
            dist = 2 * R_EARTH * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
            hit = idx[dist <= radius_km]
            if hit.size:
                covered[r2, hit] = True
    return covered

def weighted_stats(covered):
    total_land = covered_land = total_all = covered_all = 0.0
    step = 100
    for r0 in range(0, H, step):
        r1 = min(H, r0 + step)
        land = glm.is_land(lat[r0:r1][:, None], lon[None, :])
        w = coslat[r0:r1][:, None]
        total_land += float((land * w).sum())
        covered_land += float(((land & covered[r0:r1]) * w).sum())
        total_all += float(w.sum() * W)
        covered_all += float((covered[r0:r1] * w).sum())
    return {
        'land_pct': round(100 * covered_land / total_land, 2),
        'surface_pct': round(100 * covered_all / total_all, 2),
    }

res = []
for r in (10, 25, 50):
    t = time.time()
    cov = covered_for_radius(r)
    st = weighted_stats(cov)
    st['radius_km'] = r
    st['seconds'] = round(time.time() - t, 1)
    res.append(st)
    print(st, flush=True)

result = {
    'displayable_cameras': len(cams),
    'occupied_cells_0.1deg': len(cells),
    'radii': res,
    'countries_with_cameras': len(countries),
    'countries': sorted(countries),
    'computed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(result, open(OUT, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
print('wrote', OUT, 'in', round(time.time() - t0, 1), 's')
