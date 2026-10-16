"""Fetch a few Malayalam Wikipedia articles as plain-text ground truth (CC BY-SA)."""
import json, time, urllib.parse, urllib.request

TITLES = ["കേരളം", "തൃശ്ശൂർ ജില്ല", "ചാലക്കുടി", "മലയാളം", "പെരിയാർ"]
UA = {"User-Agent": "unshape-fetch-text/0.1"}

for t in TITLES:
    q = urllib.parse.urlencode({"action": "query", "prop": "extracts", "explaintext": 1,
                                "format": "json", "titles": t})
    req = urllib.request.Request("https://ml.wikipedia.org/w/api.php?" + q, headers=UA)
    d = json.load(urllib.request.urlopen(req, timeout=30))
    p = next(iter(d["query"]["pages"].values()))
    text = p.get("extract", "")
    with open(f"wiki_{p.get('pageid')}.txt", "w", encoding="utf-8") as f:
        f.write(text)
    print(p.get("pageid"), len(text))
    time.sleep(2)
