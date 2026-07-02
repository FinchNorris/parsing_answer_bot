#!/usr/bin/env python3
"""
Парсер квартир с dogma.ru через Firecrawl API.

Использование как CLI:
  python3 parser.py https://dogma.ru/projects/yukki
  python3 parser.py https://dogma.ru/projects/yukki --pages 5 --delay 4

Использование как модуль из другого скрипта:
  from parser import run, ParseResult
  result = run("https://dogma.ru/projects/yukki", db="apts.db", delay=4)
  print(result.total_saved, result.blocked_pages)

  https://dogma.ru/projects/park-pobedy, https://dogma.ru/projects/samolet, https://dogma.ru/projects/reeds, https://dogma.ru/projects/publicist - False
"""

import os
import sys
import re
import json
import time
import math
import sqlite3
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from firecrawl import FirecrawlApp

try:
    import config
    API_KEY = getattr(config, "FIRECRAWL_KEY", "")
except ImportError:
    API_KEY = ""

# Приоритет: переменная окружения (устанавливается bot_crypt.py при расшифровке)
API_KEY = os.environ.get("FIRECRAWL_KEY") or API_KEY

# Сервер может использовать системный прокси — исключаем Firecrawl из него,
# чтобы запросы к api.firecrawl.dev шли напрямую
_FIRECRAWL_HOST = "api.firecrawl.dev"
_no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
if _FIRECRAWL_HOST not in _no_proxy:
    os.environ["NO_PROXY"] = f"{_no_proxy},{_FIRECRAWL_HOST}".strip(",")
    os.environ["no_proxy"] = os.environ["NO_PROXY"]

try:
    import config as _cfg
    if getattr(_cfg, "DISABLE_SYSTEM_PROXY_FOR_FIRECRAWL", False):
        for _var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(_var, None)
    _firecrawl_proxy = getattr(_cfg, "FIRECRAWL_PROXY", "")
    if _firecrawl_proxy:
        os.environ["HTTP_PROXY"]  = _firecrawl_proxy
        os.environ["HTTPS_PROXY"] = _firecrawl_proxy
        os.environ["http_proxy"]  = _firecrawl_proxy
        os.environ["https_proxy"] = _firecrawl_proxy
except ImportError:
    pass


# ── Логирование ───────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    GRAY   = "\033[90m"

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    palette = {"INFO": C.CYAN, "OK": C.GREEN, "WARN": C.YELLOW, "ERR": C.RED, "DATA": C.GRAY}
    color = palette.get(level, C.RESET)
    print(f"{C.GRAY}[{ts}]{C.RESET} {color}{level:<4}{C.RESET}  {msg}", flush=True)


# ── Результат парсинга (для использования как модуль) ─────────────────────────
@dataclass
class ParseResult:
    total_saved:   int = 0
    total_pages:   int = 0
    blocked_pages: list = field(default_factory=list)   # номера заблокированных страниц
    retried_pages: list = field(default_factory=list)   # страницы успешно повторены
    db_path:       str  = ""
    жк:            str  = ""


# ── SQLite ────────────────────────────────────────────────────────────────────
def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apartments (
            id           INTEGER PRIMARY KEY,
            url          TEXT UNIQUE,
            жк           TEXT,
            тип          TEXT,
            комнат       INTEGER,
            площадь      REAL,
            цена         INTEGER,
            цена_скидка  INTEGER,
            этаж         INTEGER,
            этаж_макс    INTEGER,
            корпус       TEXT,
            срок_сдачи   TEXT,
            статус       INTEGER,
            отделка      TEXT,
            адрес        TEXT,
            parsed_at    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parse_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            page_num    INTEGER,
            total_pages INTEGER,
            scraped     INTEGER,
            status      TEXT,
            ts          TEXT
        )
    """)
    conn.commit()
    return conn


def save_apartments(conn: sqlite3.Connection, objects: list, жк: str) -> int:
    now = datetime.now().isoformat()
    saved = 0
    for o in objects:
        rooms = o.get("room", 0)
        тип = "Студия" if rooms == 0 else f"{rooms}-комн."
        try:
            conn.execute("""
                INSERT OR REPLACE INTO apartments
                (id, url, жк, тип, комнат, площадь, цена, цена_скидка,
                 этаж, этаж_макс, корпус, срок_сдачи, статус, отделка, адрес, parsed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                o.get("id"),
                f"https://dogma.ru/flat/{o['id']}",
                жк, тип, rooms,
                o.get("area"),
                o.get("cost"),
                o.get("cost_sale") or None,
                o.get("floor"),
                o.get("floor_max"),
                o.get("letter_name"),
                o.get("rv_pd_deadline"),
                o.get("status"),
                o.get("finish_types"),
                o.get("address"),
                now,
            ))
            saved += 1
        except Exception as e:
            log(f"Ошибка сохранения id={o.get('id')}: {e}", "WARN")
    conn.commit()
    return saved


def log_run(conn, page_num, total_pages, scraped, status):
    conn.execute(
        "INSERT INTO parse_runs (page_num, total_pages, scraped, status, ts) VALUES (?,?,?,?,?)",
        (page_num, total_pages, scraped, status, datetime.now().isoformat())
    )
    conn.commit()


# ── Парсинг HTML ──────────────────────────────────────────────────────────────
def extract_pageprops(html: str) -> Optional[dict]:
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for s in scripts:
        if "pageProps" not in s or "getFromFilter" not in s:
            continue
        # SDK отдаёт UTF-8, MCP — с unicode_escape. Пробуем оба варианта.
        for candidate in [s, bytes(s, "utf-8").decode("unicode_escape")]:
            try:
                return json.loads(candidate).get("props", {}).get("pageProps", {})
            except Exception:
                continue
    return None


def decode_str(s: str) -> str:
    try:
        return s.encode("latin1").decode("utf-8")
    except Exception:
        return s


def is_blocked(html: str) -> bool:
    return "DDOS-GUARD" in html[:500].upper()


def print_apt(o: dict):
    rooms = o.get("room", 0)
    тип = "Студия" if rooms == 0 else f"{rooms}-комн."
    цена = o.get("cost", 0)
    скидка = o.get("cost_sale")
    цена_str = f"{цена:>12,} ₽"
    if скидка and скидка > 0:
        цена_str += f"  {C.YELLOW}(было {скидка:,}){C.RESET}"
    print(
        f"   {C.GRAY}├{C.RESET} "
        f"id={C.BOLD}{o['id']}{C.RESET}  "
        f"{тип:<10}  "
        f"{o.get('area','?')} м²  "
        f"{цена_str}  "
        f"эт.{o.get('floor','?')}  "
        f"корп.{o.get('letter_name','?')}  "
        f"{(o.get('rv_pd_deadline') or '')[:7]}",
        flush=True
    )


# ── Firecrawl: скрап одной страницы ──────────────────────────────────────────
def scrape_page(app: FirecrawlApp, base_url: str, page_num: int) -> Optional[str]:
    url = base_url if page_num == 1 else f"{base_url}?offcet={page_num}"
    try:
        result = app.scrape_url(url, formats=["rawHtml"], wait_for=5000, proxy="stealth")
        return result.raw_html if hasattr(result, "raw_html") else (result.get("rawHtml") or "")
    except Exception as e:
        log(f"Firecrawl ошибка на стр.{page_num}: {e}", "ERR")
        return None


# ── Обработка одной страницы ──────────────────────────────────────────────────
def process_page(app, conn, base_url, page_num, total_pages, жк, delay_on_block=0, _log=None, on_apartment=None) -> Optional[int]:
    """
    Возвращает кол-во сохранённых квартир или None если страница заблокирована/пустая.
    delay_on_block — доп. пауза перед запросом (для ретраев).
    """
    _log = _log or log
    if delay_on_block:
        _log(f"Пауза {delay_on_block}с перед ретраем стр.{page_num}...")
        time.sleep(delay_on_block)

    html = scrape_page(app, base_url, page_num)

    if not html:
        log_run(conn, page_num, total_pages, 0, "error")
        return None

    if is_blocked(html):
        _log(f"Стр.{page_num} заблокирована DDoS-Guard", "WARN")
        log_run(conn, page_num, total_pages, 0, "blocked")
        return None

    props = extract_pageprops(html)
    if not props:
        _log(f"pageProps не найден на стр.{page_num}", "WARN")
        log_run(conn, page_num, total_pages, 0, "no_data")
        return None

    objects = props.get("getFromFilter", {}).get("objects", [])
    saved = save_apartments(conn, objects, жк)
    log_run(conn, page_num, total_pages, saved, "ok")

    _log(f"Стр.{page_num}: {saved} кв. сохранено", "DATA")
    for o in objects:
        print_apt(o)

    if on_apartment and objects:
        try:
            on_apartment(objects)
        except Exception:
            pass

    return saved


# ── Основная функция (используется и как CLI, и как модуль) ───────────────────
def run(
    start_url:    str   = "",
    db:           str   = "apartments.db",
    delay:        float = 3.0,
    max_pages:    int   = 5,
    log_callback  = None,   # callable(level, msg) — лог в Telegram
    on_apartment  = None,   # callable(list[dict]) — батч квартир со страницы
    stop_event    = None,   # threading.Event — установи для остановки парсинга
    api_key: str  = "",
) -> ParseResult:
    """
    Парсит квартиры с указанного URL и сохраняет в SQLite.

    Параметры:
      start_url   — URL страницы проекта (напр. https://dogma.ru/projects/yukki)
      db          — путь к SQLite файлу
      delay       — задержка между страницами в секундах
      max_pages   — максимум страниц (0 = все)
      retry_delay — задержка перед каждым ретраем заблокированных страниц
      max_retries — сколько раз пытаться повторить заблокированные страницы

    Возвращает ParseResult с итогами.
    """
    # _log пишет в консоль и вызывает внешний callback (для Telegram)
    def _log(msg: str, level: str = "INFO"):
        log(msg, level)
        if log_callback:
            try:
                log_callback(level, msg)
            except Exception:
                pass

    _log(f"Старт парсинга: {start_url}")
    _log(f"БД: {db}")

    app = FirecrawlApp(api_key=api_key or API_KEY)
    conn = init_db(db)
    _log("БД инициализирована", "OK")

    result = ParseResult(db_path=db)
    total_pages = None
    жк = "Догма"
    current = 1

    # ── Основной проход ───────────────────────────────────────────────────────
    while True:
        # Проверяем сигнал остановки
        if stop_event and stop_event.is_set():
            _log("Парсинг остановлен пользователем", "WARN")
            break

        _log(f"Страница {current}{f'/{total_pages}' if total_pages else '/?'}")

        if current == 1:
            html = scrape_page(app, start_url, 1)
            if not html or is_blocked(html):
                _log("Не удалось загрузить первую страницу", "ERR")
                break
            props = extract_pageprops(html)
            if not props:
                _log("pageProps не найден на стр.1", "ERR")
                break

            gff = props.get("getFromFilter", {})
            stf = props.get("sendToFilter", {})
            total_count = gff.get("count", 0)
            limit = stf.get("limit", 12)
            total_pages = math.ceil(total_count / limit) if limit else 1
            if max_pages:
                total_pages = min(total_pages, max_pages)

            raw_name = (gff.get("objects") or [{}])[0].get("project_name", "Догма")
            жк = decode_str(raw_name) if raw_name else "Догма"
            result.жк = жк
            result.total_pages = total_pages

            _log(f"ЖК: {жк}", "OK")
            _log(f"Квартир на сайте: {total_count} | парсим страниц: {total_pages}", "OK")

            objects = gff.get("objects", [])
            saved = save_apartments(conn, objects, жк)
            log_run(conn, 1, total_pages, saved, "ok")
            result.total_saved += saved
            _log(f"Стр.1: {saved} кв. сохранено | всего: {result.total_saved}", "DATA")
            for o in objects:
                print_apt(o)
            if on_apartment and objects:
                try:
                    on_apartment(objects)
                except Exception:
                    pass

        else:
            saved = process_page(
                app, conn, start_url, current, total_pages, жк,
                _log=_log, on_apartment=on_apartment,
            )
            if saved is None:
                result.blocked_pages.append(current)
            else:
                result.total_saved += saved

        if current >= total_pages:
            break

        current += 1
        if current <= total_pages:
            # Пауза с проверкой stop_event каждую секунду
            for _ in range(int(delay)):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)

    # ── Итог ──────────────────────────────────────────────────────────────────
    if result.blocked_pages:
        _log(f"Пропущено страниц (DDoS): {result.blocked_pages}", "WARN")
    _log(f"Готово. Квартир в БД: {result.total_saved}", "OK")

    rows = conn.execute("""
        SELECT тип, COUNT(*) cnt, MIN(цена), MAX(цена), ROUND(AVG(площадь), 1)
        FROM apartments GROUP BY тип ORDER BY тип
    """).fetchall()

    print(f"\n{C.BOLD}Итог по типам:{C.RESET}")
    print(f"  {'Тип':<12} {'Кол-во':>7} {'Мин цена':>14} {'Макс цена':>14} {'Ср.площадь':>11}")
    print("  " + "─" * 62)
    for r in rows:
        print(f"  {str(r[0]):<12} {r[1]:>7} {r[2]:>14,} {r[3]:>14,} {str(r[4]):>10} м²")

    conn.close()
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Парсер квартир dogma.ru через Firecrawl")
    cli.add_argument("url",                               help="URL страницы проекта")
    cli.add_argument("--db",    default="apartments.db",  help="SQLite файл (default: apartments.db)")
    cli.add_argument("--delay", type=float, default=3.0,  help="Задержка между страницами, сек (default: 3)")
    cli.add_argument("--pages", type=int,   default=5,    help="Макс. страниц (default: 5)")
    args = cli.parse_args()

    run(start_url=args.url, db=args.db, delay=args.delay, max_pages=args.pages)
