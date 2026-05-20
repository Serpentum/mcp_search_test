import urllib.request
import json

# Check yandex-search on PyPI
for pkg in ['yandex-search', 'yandex-search-api', 'yandex-searcher']:
    url = f'https://pypi.org/pypi/{pkg}/json'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            info = data['info']
            print(f"=== {pkg} ===")
            print(f"  Version: {info['version']}")
            print(f"  Summary: {info['summary']}")
            print(f"  Requires: {info['requires_dist']}")
            print()
    except Exception as e:
        print(f"=== {pkg} === Error: {e}")
        print()
