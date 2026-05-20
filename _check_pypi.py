import urllib.request
import json

# Check PyPI API for yandex search packages
url = 'https://pypi.org/simple/'
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode('utf-8')
        for line in html.split('\n'):
            if 'yandex' in line.lower():
                print(line)
except Exception as e:
    print(f'Error: {e}')
