# MCP Web Tools

MCP-сервер с двумя инструментами: поиск в интернете и загрузка содержимого страниц.

## Инструменты

- **web_search** — поиск через Google (до 5 результатов)
- **fetch_url** — загрузка содержимого веб-страницы (markdown или HTML)

## Лимиты

- Поиск: максимум 2 вызова за сессию
- Чтение страниц: максимум 5 вызовов за сессию
- После исчерпания лимита инструментарий блокируется

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python server.py
```

## Подключение через MCP-клиент

```json
{
  "mcpServers": {
    "web-tools": {
      "command": "python",
      "args": ["D:/pets/mcp_search_test/server.py"]
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

## Безопасность

- Блокировка `file://`, `ftp://` схем
- Блокировка localhost и частных IP-адресов
- Лимит вывода 10 000 символов для fetch_url
- Логирование в stderr
