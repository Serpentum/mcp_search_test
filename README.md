# MCP Web Tools

MCP-сервер с инструментами для поиска в интернете и загрузки содержимого веб-страниц. Использует concurrent multi-engine поиск через Playwright (Bing + Yandex + Baidu одновременно) и извлекает текст страниц в markdown-формате.

## Инструменты

| Инструмент | Описание | Аргументы |
|------------|----------|-----------|
| `web_search` | Поиск в интернете через Bing, Yandex, Baidu (все одновременно, до 15 результатов: заголовок, URL, сниппет) | `query` (str) — текст запроса |
| `fetch_url` | Загрузка содержимого веб-страницы, извлечение текста в markdown или HTML | `url` (str) — URL страницы, `format` (str) — `markdown` (по умолчанию) или `html` |
| `reset_limits` | Сброс всех счётчиков вызовов для начала новой задачи | нет |

## Лимиты вызовов

### Мягкие лимиты (advisory)

Модель получает рекомендацию остановиться, но технически может продолжить:

| Инструмент | Мягкий лимит | Сообщение |
|------------|-------------|-----------|
| `web_search` | 5 вызовов | "Рекомендую прочитать найденные страницы и сформировать ответ" |
| `fetch_url`  | 8 вызовов | "Пожалуйста, завершите чтение и сформируйте ответ" |

### Жёсткие лимиты (hard)

Блокируют дальнейшее использование инструмента:

| Инструмент | Жёсткий лимит | Сообщение |
|------------|--------------|-----------|
| `web_search` | 7 вызовов | "Лимит поисков исчерпан" |
| `fetch_url`  | 10 вызовов | "Лимит чтений исчерпан" |
| **Всего**   | **30 вызовов** | Общий лимит на все инструменты |

### Кэширование

- Результаты поиска кэшируются по точному совпадению query (TTL: 5 минут)
- Содержимое страниц кэшируется по URL + формату (TTL: 5 минут)
- Кэш-хиты **не тратят** счётчик вызовов
- Максимум 100 записей в кэше (LRU eviction)

### Прогресс в ответе

Каждый ответ содержит баннер прогресса:

```
---
📊 Прогресс: поиски 2/7, чтения 3/10, всего 5/30
Осталось: 5 поисков, 7 чтений, 25 всего
```

## Установка

### Требования

- Python 3.10+

### Установка зависимостей

```bash
pip install -r requirements.txt
python -m playwright install firefox
```

## Конфигурация

### Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `MCP_MAX_SEARCH_SOFT` | `5` | Мягкий лимит поисков (advisory warning) |
| `MCP_MAX_SEARCH_HARD` | `7` | Жёсткий лимит поисков (блокирует дальнейший поиск) |
| `MCP_MAX_FETCH_SOFT` | `8` | Мягкий лимит чтений (advisory warning) |
| `MCP_MAX_FETCH_HARD` | `10` | Жёсткий лимит чтений (блокирует дальнейшие чтения) |
| `MCP_MAX_TOTAL` | `30` | Общий жёсткий лимит вызовов всех инструментов |
| `MCP_CACHE_TTL` | `300` | Время жизни кэша в секундах (5 минут) |
| `MCP_CACHE_MAX_SIZE` | `100` | Максимальное количество записей в кэше (LRU eviction) |
| `MCP_UNSAFE_MODE` | `false` | Отключает все проверки безопасности URL (localhost, частные IP). Используйте только в изолированных средах |
| `MCP_MIN_RELEVANCE` | `0.0` | Минимальный порог релевантности результата (float 0.0–1.0). Результаты ниже порога отбрасываются |
| `MCP_LANG_MISMATCH` | `0.3` | Включает фильтрацию результатов на другом языке (>0.0 = включено, 0.0 = отключено). Работает для пар ru↔zh, пропускает en |

### Запуск

Базовый запуск:

```bash
python server.py
```

С кастомной конфигурацией:

```bash
MCP_MAX_SEARCH_SOFT=3 MCP_MAX_TOTAL=20 python server.py
```

## Подключение к MCP-клиенту

Сервер использует протокол MCP (JSON-RPC через stdin/stdout).

### macOS (Homebrew Python)

На macOS с Homebrew Python требуется использование виртуального окружения:

```json
{
  "mcpServers": {
    "web-tools": {
      "command": "/path/to/mcp_search_test/.venv/bin/python",
      "args": ["/path/to/mcp_search_test/server.py"],
      "env": {
        "MCP_UNSAFE_MODE": "true"
      }
    }
  }
}
```

### Claude Desktop

Путь к конфигу: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "web-tools": {
      "command": "/path/to/mcp_search_test/.venv/bin/python",
      "args": ["/path/to/mcp_search_test/server.py"],
      "env": {
        "MCP_UNSAFE_MODE": "true"
      }
    }
  }
}
```

### Cursor / VS Code / Chatbox / другие MCP-клиенты

Аналогичная JSON-конфигурация:

```json
{
  "mcpServers": {
    "web-tools": {
      "command": "/path/to/mcp_search_test/.venv/bin/python",
      "args": ["/path/to/mcp_search_test/server.py"],
      "env": {
        "MCP_UNSAFE_MODE": "true"
      }
    }
  }
}
```

> **Важно:** Замените `/path/to/mcp_search_test` на реальный путь к проекту.
> `MCP_UNSAFE_MODE=true` разрешает доступ к localhost и частным IP-адресам — нужно для локальных сервисов.

## Примеры вызовов

### Поиск

```json
{
  "name": "web_search",
  "arguments": {
    "query": "Python programming"
  }
}
```

### Загрузка страницы

```json
{
  "name": "fetch_url",
  "arguments": {
    "url": "https://example.com",
    "format": "markdown"
  }
}
```

### Сброс лимитов

```json
{
  "name": "reset_limits",
  "arguments": {}
}
```

## Безопасность

- Блокировка схем `file://` и `ftp://`
- Блокировка localhost и частных IP-адресов (10.x, 172.16–31.x, 192.168.x, 127.x, link-local, IPv6 private/loopback)
- Лимит вывода 10 000 символов для `fetch_url`
- Логирование ошибок в stderr
- `MCP_UNSAFE_MODE=true` разрешает доступ к localhost и частным IP-адресам

## Зависимости

| Пакет | Назначение |
|-------|-----------|
| `mcp>=1.0.0` | Официальный MCP SDK (FastMCP) |
| `httpx>=0.27.0` | Асинхронный HTTP-клиент |
| `beautifulsoup4>=4.12.0` | Парсинг HTML (fallback) |
| `trafilatura>=1.6.0` | Извлечение текста страниц в markdown |
| `playwright>=1.40.0` | Playwright для браузерной автоматизации (multi-engine поиск) |
