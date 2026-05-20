from yandex_search import search

# Test with Russian query
print("=== Russian query ===")
results = list(search('главные новости мира сегодня', lang='ru', num=5))
print(f"Results: {len(results)}")
for r in results:
    print(f"  URL: {r.url}")
    print(f"  Title: {r.title}")
    print(f"  Desc: {r.description}")
    print()

# Test with English query
print("=== English query ===")
results2 = list(search('python programming language', lang='en', num=5))
print(f"Results: {len(results2)}")
for r in results2:
    print(f"  URL: {r.url}")
    print(f"  Title: {r.title}")
    print(f"  Desc: {r.description}")
    print()
