# MCP Web Tools

MCP сервер с двумя инструментами: поиск в интернете и загрузка содержимого страниц.

## Инструменты

- **web_search** — поиск по DuckDuckGo (до 5 результатов)
- **fetch_url** — загрузка содержимого веб-страницы (markdown или HTML)

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python main.py
```

## Подключение через MCP-клиент

```json
{
  "mcpServers": {
    "web-tools": {
      "command": "python",
      "args": ["D:/pets/mcp_search_test/main.py"]
    }
  }
}
```

## Примеры использования

### Поиск

```json
{
  "name": "web_search",
  "arguments": {
    "query": "Python programming",
    "max_results": 5
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

## Безопасность

- Блокировка `file://`, `ftp://` схем
- Блокировка localhost и частных IP-адресов
- Лимит вывода 10 000 символов для fetch_url
- Логирование в stderr
