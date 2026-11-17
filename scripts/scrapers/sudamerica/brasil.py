import requests
from scrapers.utils import log, build_feature, HEADERS

def fetch(config) -> list[dict]:
    url = "https://www.der.sp.gov.br/api/cameras"
    features = []
    
    log("Fetching DER-SP Traffic Cameras (no key)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.get("TIMEOUT", 10))
        resp.raise_for_status()
        cams = resp.json()
    except Exception as e:
        log(f"  DER-SP fetch failed: {e}", "WARN")
        return []

    skipped = 0
    for cam in cams:
        try:
            lat = float(cam.get("latitude", 0) or cam.get("lat", 0))
            lon = float(cam.get("longitude", 0) or cam.get("lon", 0))
            if lat == 0 and lon == 0:
                skipped += 1
                continue
            
            cam_id = str(cam.get("id", "unknown"))
            image_url = cam.get("imageUrl", "") or cam.get("image_url", "") or cam.get("url", "")
            if not image_url:
                skipped += 1
                continue
                
            features.append(build_feature(
                cam_id=cam_id, 
                name=cam.get("name", f"DER-SP Camera {cam_id}"),
                lat=lat, 
                lon=lon, 
                feed_url=image_url,
                cam_type="traffic", 
                city=cam.get("city", "São Paulo"), 
                country="BR",
                source="brasil_der-sp"
            ))
        except Exception:
            skipped += 1
            continue
            
    log(f"DER-SP: {len(features)} cameras loaded ({skipped} skipped)", "OK")
    return features