"""
Search SERP — raw web skill (Serper.dev backend)

POST https://google.serper.dev/search
Headers:
    X-API-KEY: <SERP_DEV_KEY>
    Content-Type: application/json
Body:
    q, num, hl, gl

Input:  query (str), timeout (int), num (int), start (int)
Output: {"status": <int>, "output": <list[dict]>}
"""

import os
import re
import requests

SERP_API_URL = os.getenv("SERP_API_URL", "https://google.serper.dev/search")
SERP_DEV_KEY = os.getenv("SERP_DEV_KEY", "YOUR_API_KEY")


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
    """Search Google via Serper.dev API and return extracted results."""
    hl, gl = _detect_language(query)
    headers = {
        "X-API-KEY": SERP_DEV_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "num": min(max(num, 1), 10),
        "hl": hl,
        "gl": gl,
    }
    if start > 1:
        payload["page"] = start
    try:
        proxies = {}
        proxy_url = os.environ.get("https_proxy") or os.environ.get("http_proxy")
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.post(SERP_API_URL, json=payload, headers=headers, timeout=timeout, proxies=proxies)
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

    result = search_serp("Python web scraping", num=3)
    print(f"status={result['status']}  count={len(result['output'])}")
    print(json.dumps(result["output"], indent=2, ensure_ascii=False)[:1000])
