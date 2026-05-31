"""
Search SERP — raw web skill (Serper.dev backend)

POST https://google.serper.dev/search
Headers:
    X-API-KEY: <key>
    Content-Type: application/json
Body:
    q, num, hl, gl

Input:  query (str), timeout (int), num (int), start (int)
Output: {"status": <int>, "output": <list[dict]>}

Keys are read from SERP_API_KEYS (comma-separated) with fallback to SERP_DEV_KEY.
On 403/429 the current key is marked exhausted and the next key is tried automatically.
"""

import os
import re
import threading
import requests

SERP_API_URL = os.getenv("SERP_API_URL", "https://google.serper.dev/search")

# Build key pool from SERP_API_KEYS (comma-separated), fall back to SERP_DEV_KEY
_raw_keys = os.getenv("SERP_API_KEYS", "")
if _raw_keys:
    _KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
else:
    _dev_key = os.getenv("SERP_DEV_KEY", "")
    _KEYS = [_dev_key] if _dev_key else []

_exhausted: set[str] = set()
_lock = threading.Lock()


def _next_key() -> str | None:
    """Return the first non-exhausted key, or None if all are exhausted."""
    with _lock:
        for k in _KEYS:
            if k not in _exhausted:
                return k
    return None


def _mark_exhausted(key: str) -> None:
    with _lock:
        _exhausted.add(key)


def _detect_language(query: str) -> tuple[str, str]:
    if re.search(r"[一-鿿]", query):
        return "zh", "cn"
    return "en", "us"


def search_serp(
    query: str,
    timeout: int = 20,
    num: int = 10,
    start: int = 1,
    raw_save_path: str | None = None,
) -> dict:
    """Search Google via Serper.dev API with automatic key rotation on quota errors."""
    hl, gl = _detect_language(query)
    payload = {
        "q": query,
        "num": min(max(num, 1), 10),
        "hl": hl,
        "gl": gl,
    }
    if start > 1:
        payload["page"] = start

    last_status = -1
    while True:
        key = _next_key()
        if key is None:
            return {"status": last_status, "output": []}

        headers = {
            "X-API-KEY": key,
            "Content-Type": "application/json",
        }
        try:
            proxies = {}
            proxy_url = os.environ.get("https_proxy") or os.environ.get("http_proxy")
            if proxy_url:
                proxies = {"http": proxy_url, "https": proxy_url}
            resp = requests.post(SERP_API_URL, json=payload, headers=headers, timeout=timeout, proxies=proxies)

            if resp.status_code in (403, 429):
                _mark_exhausted(key)
                last_status = resp.status_code
                continue

            if raw_save_path and resp.status_code == 200:
                os.makedirs(os.path.dirname(raw_save_path) or ".", exist_ok=True)
                with open(raw_save_path, "w", encoding="utf-8") as f:
                    f.write(resp.text)

            if resp.status_code != 200:
                return {"status": resp.status_code, "output": []}

            data = resp.json()
            results = [
                {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "date": item.get("date", ""),
                    "query": query,
                }
                for item in data.get("organic", [])
            ]
            return {"status": resp.status_code, "output": results}
        except Exception as e:
            return {"status": -1, "output": []}


if __name__ == "__main__":
    import json

    result = search_serp("Python web searching", num=3)
    print(f"status={result['status']}  count={len(result['output'])}")
    print(json.dumps(result["output"], indent=2, ensure_ascii=False)[:1000])
