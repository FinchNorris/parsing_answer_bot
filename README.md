# dogma-parser

Система для парсинга, хранения и поиска квартир с сайта [dogma.ru](https://dogma.ru).

Включает три компонента:
- **`parser.py`** — парсер квартир через Firecrawl API, сохраняет данные в SQLite
- **`agent.py`** — LangChain-агент на базе Claude (Anthropic) для поиска квартир по естественному запросу
- **`bot.py`** — Telegram-бот как интерфейс к агенту с возможностью запуска парсинга прямо из чата

---

## Архитектура

```
Пользователь
    │
    ▼
Telegram Bot (bot.py)
    │
    ├── Запрос на поиск квартиры
    │       ▼
    │   LangChain Agent (agent.py)
    │       ├── Классификация запроса (Claude claude-haiku-4-5)
    │       ├── Построение SQL-фильтров
    │       ├── Запрос к SQLite
    │       └── Формирование ответа с объяснением (Claude)
    │
    └── Кнопка «Обновить данные»
            ▼
        Parser (parser.py)
            ├── Firecrawl API → dogma.ru
            ├── Извлечение pageProps из Next.js HTML
            └── Сохранение в SQLite
```

**Как работает парсер:** dogma.ru — Next.js SPA. При загрузке страницы сервер отдаёт все данные о квартирах в HTML внутри тега `<script>` в объекте `pageProps`. Парсер извлекает этот объект напрямую, без необходимости ходить на каждую страницу `/flat/ID`. Пагинация — через URL-параметр `?offcet=N`.

**Как работает агент:** принимает запрос в свободной форме → классифицирует его (поиск / уточнение / чат) → строит SQL-запрос → получает топ-3/5 квартир из БД → формирует ответ с объяснением почему каждая квартира подходит.

---

## Структура проекта

```
dogma-parser/
├── parser.py          # Парсер квартир с dogma.ru
├── agent.py           # LangChain-агент для поиска
├── bot.py             # Telegram-бот
├── config.py          # Ключи и настройки (не коммитить!)
├── requirements.txt   # Зависимости
├── apartments.db      # SQLite БД (генерируется парсером)
└── prompts/
    ├── classifier.txt # Промпт классификации запроса
    └── formatter.txt  # Промпт форматирования ответа
```

---

## Установка

### Требования

- Python 3.10+
- Аккаунт [Firecrawl](https://firecrawl.dev) (API key)
- Аккаунт [Anthropic](https://console.anthropic.com) (API key)
- Telegram-бот (токен от [@BotFather](https://t.me/BotFather))

### Зависимости

```bash
pip install -r requirements.txt
```

### Конфигурация

Скопируй и заполни `config.py`:

```python
# Telegram
TG_TOKEN = "ваш_токен_бота"

# Anthropic (Claude)
ANTHROPIC_KEY = "sk-ant-..."

# Firecrawl
FIRECRAWL_KEY = "fc-..."

# База данных
DB_PATH = "apartments.db"

# Прокси для Telegram API (если api.telegram.org недоступен напрямую)
# Форматы: "socks5://host:port", "http://user:pass@host:port"
PROXY_URL = ""

# Прокси для Firecrawl (если api.firecrawl.dev недоступен с сервера)
FIRECRAWL_PROXY = ""

# Сбросить системный прокси для Firecrawl (True если системный прокси мешает)
DISABLE_SYSTEM_PROXY_FOR_FIRECRAWL = False
```

> **Важно:** добавь `config.py` и `apartments.db` в `.gitignore`

---

## Быстрый старт

```bash
# 1. Спарсить квартиры (нужно сделать перед запуском бота)
python3 parser.py https://dogma.ru/projects/yukki

# 2. Запустить бота
python3 bot.py
```

---

## Парсер

### CLI

```bash
python3 parser.py <URL> [опции]
```

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `url` | — | URL страницы проекта (обязательный) |
| `--db` | `apartments.db` | Путь к SQLite файлу |
| `--delay` | `3.0` | Задержка между страницами, сек |
| `--pages` | `0` (все) | Максимум страниц для парсинга |
| `--retry-delay` | `10.0` | Пауза перед ретраем заблокированной страницы, сек |
| `--max-retries` | `3` | Количество попыток для заблокированных страниц |

```bash
# Все квартиры ЖК Юкки
python3 parser.py https://dogma.ru/projects/yukki

# Первые 5 страниц, задержка 4 сек
python3 parser.py https://dogma.ru/projects/yukki --pages 5 --delay 4

# Агрессивный ретрай при DDoS
python3 parser.py https://dogma.ru/projects/yukki --max-retries 5 --retry-delay 15
```

### Использование как модуль

```python
from parser import run, ParseResult

result: ParseResult = run(
    start_url   = "https://dogma.ru/projects/yukki",
    db          = "apartments.db",
    delay       = 4.0,
    max_pages   = 0,        # 0 = все страницы
    retry_delay = 10.0,
    max_retries = 3,
)

print(result.total_saved)    # сохранено квартир
print(result.blocked_pages)  # страницы не прошедшие DDoS-Guard
print(result.жк)             # название ЖК
```

### Поддерживаемые ЖК и проекты

| Город | ЖК | URL |
|---|---|---|
| Ленинградская обл. | Догма Юкки | `/projects/yukki` |
| Новороссийск | Порто-Ново | `/projects/porto-novo` |
| Реутов | ЭВО | `/projects/evo` |
| Пушкино | Публицист | `/projects/publicist` |
| Калуга | Космопарк | `/projects/kosmopark` |
| Омск | Снегири | `/projects/snegiri` |
| Краснодар | Грейд | `/projects/grade` |
| Краснодар | Ридз | `/projects/reeds` |
| Краснодар | Рекорд 2 | `/projects/record2` |
| Краснодар | САМОЛЁТ 7 | `/projects/samolet7` |
| Краснодар | МКР Самолёт | `/projects/samolet` |
| Краснодар | DOGMA ПАРК | `/projects/dogma-park` |
| Краснодар | Парк Победы | `/projects/park-pobedy` |

---

## Telegram-бот

### Команды

| Команда | Действие |
|---|---|
| `/start` | Запуск, приветствие, сброс диалога |
| `/summary` | Саммари текущего диалога |
| `/reset` | Очистить память диалога |

### Кнопки

| Кнопка | Действие |
|---|---|
| 🔍 Найти студию до 6 млн | Быстрый поиск |
| 📊 Самые дешёвые квартиры | Быстрый поиск |
| 📋 Саммари диалога | Показать саммари |
| 🔄 Начать заново | Сбросить диалог |
| 🔄 Обновить данные | Выбрать город и запустить парсинг |

### Примеры запросов

```
Найди студию до 6 млн от 25 м²
Хочу 1-комнатную на высоком этаже в Юкках
Покажи самые большие квартиры в Краснодаре
Есть ли что-то дешевле 5 млн?
Найди 2-комнатную с отделкой до 10 млн
```

### Обновление данных из бота

Нажми **🔄 Обновить данные** → выбери город → (для Краснодара выбери ЖК) → парсинг запустится с выводом логов прямо в чат.

---

## База данных

### Таблица `apartments`

| Поле | Тип | Описание |
|---|---|---|
| `id` | INTEGER PK | ID квартиры на сайте |
| `url` | TEXT | Ссылка на лот |
| `жк` | TEXT | Название ЖК |
| `тип` | TEXT | Студия / 1-комн. / 2-комн. / 3-комн. |
| `комнат` | INTEGER | Количество комнат (0 = студия) |
| `площадь` | REAL | Площадь, м² |
| `цена` | INTEGER | Цена, ₽ |
| `цена_скидка` | INTEGER | Цена до скидки, ₽ |
| `этаж` | INTEGER | Этаж |
| `этаж_макс` | INTEGER | Максимальный этаж в доме |
| `корпус` | TEXT | Номер корпуса |
| `срок_сдачи` | TEXT | Дата сдачи (YYYY-MM-DD) |
| `статус` | INTEGER | Статус лота (2 = в продаже) |
| `отделка` | TEXT | Тип отделки |
| `адрес` | TEXT | Адрес объекта |
| `parsed_at` | TEXT | Дата парсинга (ISO 8601) |

### Примеры SQL-запросов

```sql
-- Студии дешевле 5 млн
SELECT url, площадь, цена FROM apartments
WHERE тип = 'Студия' AND цена < 5000000
ORDER BY цена;

-- Статистика по типам
SELECT тип, COUNT(*), MIN(цена), MAX(цена), ROUND(AVG(площадь), 1)
FROM apartments GROUP BY тип ORDER BY тип;

-- Квартиры со скидкой
SELECT url, тип, цена, цена_скидка, (цена_скидка - цена) AS экономия
FROM apartments WHERE цена_скидка IS NOT NULL
ORDER BY экономия DESC;

-- История парсинга
SELECT page_num, scraped, status, ts FROM parse_runs ORDER BY ts DESC;
```

---

## DDoS-Guard

Сайт защищён DDoS-Guard, который блокирует параллельные и слишком частые запросы. Парсер обходит это так:

1. **Основной проход** — последовательные запросы с задержкой `--delay`. Заблокированные страницы записываются в очередь.
2. **Ретрай** — после основного прохода повторяет заблокированные страницы с увеличенной паузой `--retry-delay`. До `--max-retries` попыток.

При запуске на сервере с системным прокси может возникать конфликт между прокси Telegram и прокси Firecrawl. Настраивается через `PROXY_URL`, `FIRECRAWL_PROXY` и `DISABLE_SYSTEM_PROXY_FOR_FIRECRAWL` в `config.py`.

---
