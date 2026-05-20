from requests import get
from bs4 import BeautifulSoup

resp = get(
    'https://www.google.com/search',
    params={'q': 'test query', 'num': 5, 'hl': 'ru', 'start': 0, 'safe': 'active'},
    timeout=10,
    headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*'
    },
    cookies={'CONSENT': 'PENDING+987', 'SOCS': 'CAESHAgBEhIaAB'},
    verify=False
)

print(f'Status: {resp.status_code}')
print(f'Length: {len(resp.text)}')

soup = BeautifulSoup(resp.text, 'html.parser')

# Check for the classes the library uses
ezO2md = soup.find_all('div', class_='ezO2md')
print(f'ezO2md divs: {len(ezO2md)}')

# Check for other common result classes
result_links = soup.find_all('a', href=True)
print(f'Total links: {len(result_links)}')

# Look for links that look like search results (contain /url?q=)
search_links = [a for a in result_links if '/url?q=' in a.get('href', '')]
print(f'Search result links (/url?q=): {len(search_links)}')
for l in search_links[:5]:
    href = l.get('href', '')
    print(f'  {href[:100]}...')

# Check for common result container classes
all_divs = soup.find_all('div')
common_classes = {}
for d in all_divs:
    for c in d.get('class', []):
        common_classes[c] = common_classes.get(c, 0) + 1

print(f'\nTop 20 most common div classes:')
for cls, count in sorted(common_classes.items(), key=lambda x: -x[1])[:20]:
    print(f'  {cls}: {count}')
