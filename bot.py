"""
Telegram-бот для поиска квартир от застройщика ДОГМА.
Запуск: python3 bot.py
"""

import os
import asyncio
import logging
import threading
from queue import Queue, Empty
from typing import Optional

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from agent import ApartmentAgent
import parser as dogma_parser

# ── Конфигурация ──────────────────────────────────────────────────────────────
try:
    import config
    TG_TOKEN      = os.getenv("TG_TOKEN",      config.TG_TOKEN)
    ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY", config.ANTHROPIC_KEY)
    DB_PATH       = os.getenv("DB_PATH",       config.DB_PATH)
    PROXY_URL     = os.getenv("PROXY_URL",     getattr(config, "PROXY_URL", ""))
except ImportError:
    TG_TOKEN      = os.getenv("TG_TOKEN",      "")
    ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY", "")
    DB_PATH       = os.getenv("DB_PATH",       "apartments.db")
    PROXY_URL     = os.getenv("PROXY_URL",     "")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Карта городов и проектов ──────────────────────────────────────────────────
CITIES = {
    "🏙 Новороссийск":       ["https://dogma.ru/projects/porto-novo"],
    "🏙 Реутов":             ["https://dogma.ru/projects/evo"],
    "🏙 Пушкино":            ["https://dogma.ru/projects/publicist"],
    "🏙 Калуга":             ["https://dogma.ru/projects/kosmopark"],
    "🏙 Омск":               ["https://dogma.ru/projects/snegiri"],
    "🏙 Ленинградская обл.": ["https://dogma.ru/projects/yukki"],
    "🏙 Краснодар":          None,  # None = показываем субменю ЖК
}

KRASNODAR_PROJECTS = {
    "Грейд":       "https://dogma.ru/projects/grade",
    "Ридз":        "https://dogma.ru/projects/reeds",
    "Рекорд 2":    "https://dogma.ru/projects/record2",
    "САМОЛЁТ 7":   "https://dogma.ru/projects/samolet7",
    "МКР Самолёт": "https://dogma.ru/projects/samolet",
    "DOGMA ПАРК":  "https://dogma.ru/projects/dogma-park",
    "Парк Победы": "https://dogma.ru/projects/park-pobedy",
}

LEVEL_ICON = {"OK": "✅", "ERR": "❌", "WARN": "⚠️", "DATA": "📦", "INFO": "ℹ️"}

# ── Клавиатуры ────────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup([
    ["📋 Саммари диалога",  "🔄 Начать заново"],
    ["🔄 Обновить данные",  "📍 Список ЖК"],
], resize_keyboard=True)

STOP_KB = ReplyKeyboardMarkup([
    ["⛔ Остановить парсинг"],
], resize_keyboard=True)

CITIES_KB = ReplyKeyboardMarkup(
    [[city] for city in CITIES.keys()] + [["← Назад"]],
    resize_keyboard=True,
)

KRASNODAR_KB = ReplyKeyboardMarkup(
    [[name] for name in KRASNODAR_PROJECTS.keys()] + [["← Назад в города"]],
    resize_keyboard=True,
)

# ── Агенты (по одному на пользователя) ───────────────────────────────────────
agents: dict[int, ApartmentAgent] = {}

def get_agent(user_id: int) -> ApartmentAgent:
    if user_id not in agents:
        agents[user_id] = ApartmentAgent(api_key=ANTHROPIC_KEY, db_path=DB_PATH)
    return agents[user_id]


# ── Форматирование одной квартиры для вывода в чат ───────────────────────────
def format_apartment(o: dict) -> str:
    rooms = o.get("room", 0)
    тип = "Студия" if rooms == 0 else f"{rooms}-комн."
    цена = f"{o.get('cost', 0):,}".replace(",", " ")
    скидка = o.get("cost_sale")
    цена_str = f"{цена} ₽"
    if скидка and скидка > 0:
        цена_str += f" (скидка с {скидка:,} ₽)".replace(",", " ")
    return (
        f"🏠 {тип} | {o.get('area', '?')} м² | {цена_str}\n"
        f"Этаж {o.get('floor', '?')} | Корп. {o.get('letter_name', '?')} | "
        f"Сдача {(o.get('rv_pd_deadline') or '')[:7]}\n"
        f"🔗 https://dogma.ru/flat/{o.get('id', '')}"
    )


# ── Парсинг с трансляцией логов и квартир в чат ───────────────────────────────
async def run_parser_to_chat(url: str, chat_id: int, bot, stop_event, db_path: str = ""):
    """
    Запускает parser.run() в потоке (макс. 5 страниц).
    Логи батчами шлёт в чат, найденные квартиры — отдельными сообщениями.
    """
    db = db_path or DB_PATH
    log_queue: Queue = Queue()

    def log_callback(level: str, msg: str):
        log_queue.put(("LOG", level, msg))

    def on_apartment(objects: list):
        log_queue.put(("APTS", objects))

    _saved_env = {k: os.environ.get(k) for k in
                  ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}

    def parser_thread():
        try:
            dogma_parser.run(
                start_url=url, db=db, delay=3.0, max_pages=5,
                log_callback=log_callback,
                on_apartment=on_apartment,
                stop_event=stop_event,
            )
        except Exception as e:
            log_queue.put(("LOG", "ERR", f"Критическая ошибка: {e}"))
        finally:
            log_queue.put(None)  # sentinel

    threading.Thread(target=parser_thread, daemon=True).start()

    log_buffer = []

    async def flush_logs():
        if not log_buffer:
            return
        text = "\n".join(log_buffer)
        log_buffer.clear()
        try:
            await bot.send_message(chat_id, f"```\n{text}\n```", parse_mode="Markdown")
        except Exception:
            pass

    loop = asyncio.get_event_loop()

    while True:
        deadline = loop.time() + 1.5
        while loop.time() < deadline:
            try:
                item = log_queue.get_nowait()
            except Empty:
                await asyncio.sleep(0.3)
                continue

            if item is None:    # парсинг завершён
                await flush_logs()
                for k, v in _saved_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                return

            if item[0] == "APTS":
                await flush_logs()
                cards = "\n\n".join(format_apartment(o) for o in item[1])
                try:
                    await bot.send_message(chat_id, cards, disable_web_page_preview=True)
                except Exception:
                    pass
            else:
                _, level, msg = item
                icon = LEVEL_ICON.get(level, "ℹ️")
                log_buffer.append(f"{icon} {msg}")
                if len(log_buffer) >= 6:
                    await flush_logs()

        await flush_logs()


# ── Команды ───────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in agents:
        del agents[user.id]
    context.user_data.clear()
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу подобрать квартиру от застройщика ДОГМА.\n\n"
        "Примеры запросов:\n"
        "• _Найди студию до 6 млн от 25 м²_\n"
        "• _Хочу 1-комн. на высоком этаже в Юкках_\n"
        "• _Самые дешёвые квартиры_\n\n"
        "Кнопка «🔄 Обновить данные» — спарсить свежие данные с сайта.",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = get_agent(update.effective_user.id)
    summary = agent.get_summary()
    await update.message.reply_text(
        f"📋 *Саммари диалога:*\n\n{summary}",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in agents:
        del agents[uid]
    context.user_data.clear()
    await update.message.reply_text("🔄 Диалог сброшен. Что ищете?", reply_markup=MAIN_KB)


# ── Список ЖК ────────────────────────────────────────────────────────────────
def build_jk_list() -> str:
    city_map = {
        "Ленинградская обл.": ["Догма Юкки"],
        "Новороссийск":       ["Порто-Ново"],
        "Реутов":             ["ЭВО"],
        "Пушкино":            ["Публицист"],
        "Калуга":             ["Космопарк"],
        "Омск":               ["Снегири"],
        "Краснодар":          list(KRASNODAR_PROJECTS.keys()),
    }
    lines = ["📍 *Доступные ЖК:*\n"]
    for city, projects in city_map.items():
        lines.append(f"🏙 *{city}:*")
        for p in projects:
            lines.append(f"  • {p}")
    return "\n".join(lines)


# ── Запуск парсинга ───────────────────────────────────────────────────────────
async def start_parsing(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        urls: list, label: str):
    chat_id = update.effective_chat.id
    bot = context.bot

    stop_event = threading.Event()
    context.user_data["stop_event"] = stop_event
    context.user_data["parsing"] = True

    await bot.send_message(
        chat_id,
        f"🚀 Запускаю парсинг: *{label}*\n"
        f"Парсим до 5 страниц. Квартиры будут появляться по мере загрузки.",
        parse_mode="Markdown",
        reply_markup=STOP_KB,
    )

    for url in urls:
        if stop_event.is_set():
            break
        await bot.send_message(chat_id, f"📡 Парсим: `{url}`", parse_mode="Markdown")
        await run_parser_to_chat(url, chat_id, bot, stop_event=stop_event)

    context.user_data["parsing"] = False
    context.user_data.pop("stop_event", None)

    if stop_event.is_set():
        msg = f"⛔ *Парсинг остановлен.*"
    else:
        msg = f"✅ *Готово: {label}*\nМожете задавать вопросы по квартирам!"

    await bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=MAIN_KB)


# ── Главный обработчик сообщений ──────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip()
    uid   = update.effective_user.id
    state = context.user_data.get("state", "main")

    # Кнопка остановки парсинга
    if text == "⛔ Остановить парсинг":
        stop_event = context.user_data.get("stop_event")
        if stop_event:
            stop_event.set()
            await update.message.reply_text("⛔ Останавливаю парсинг...", reply_markup=MAIN_KB)
        else:
            await update.message.reply_text("Парсинг не запущен.", reply_markup=MAIN_KB)
        return

    # Если идёт парсинг — игнорируем другие сообщения
    if context.user_data.get("parsing"):
        await update.message.reply_text(
            "⏳ Идёт парсинг. Нажмите «⛔ Остановить парсинг» чтобы прервать.",
            reply_markup=STOP_KB,
        )
        return

    # Быстрые кнопки
    if text == "📋 Саммари диалога":
        await cmd_summary(update, context); return

    if text == "🔄 Начать заново":
        await cmd_reset(update, context); return

    if text == "📍 Список ЖК":
        await update.message.reply_text(build_jk_list(), parse_mode="Markdown", reply_markup=MAIN_KB)
        return

    if text == "🔄 Обновить данные":
        context.user_data["state"] = "choose_city"
        await update.message.reply_text("Выберите город:", reply_markup=CITIES_KB)
        return

    # Выбор города
    if state == "choose_city":
        if text == "← Назад":
            context.user_data["state"] = "main"
            await update.message.reply_text("Главное меню:", reply_markup=MAIN_KB)
            return

        if text == "🏙 Краснодар":
            context.user_data["state"] = "choose_krasnodar"
            await update.message.reply_text("Выберите ЖК в Краснодаре:", reply_markup=KRASNODAR_KB)
            return

        if text in CITIES:
            urls = CITIES[text]
            context.user_data["state"] = "main"
            await start_parsing(update, context, urls, text)
            return

        await update.message.reply_text("Выберите город из списка.", reply_markup=CITIES_KB)
        return

    # Выбор ЖК Краснодара
    if state == "choose_krasnodar":
        if text == "← Назад в города":
            context.user_data["state"] = "choose_city"
            await update.message.reply_text("Выберите город:", reply_markup=CITIES_KB)
            return

        if text in KRASNODAR_PROJECTS:
            url = KRASNODAR_PROJECTS[text]
            context.user_data["state"] = "main"
            await start_parsing(update, context, [url], f"Краснодар / {text}")
            return

        await update.message.reply_text("Выберите ЖК из списка.", reply_markup=KRASNODAR_KB)
        return

    # Основной режим — запрос к агенту
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    agent = get_agent(uid)
    try:
        response = agent.process(text)
    except Exception as e:
        logger.error(f"Ошибка агента uid={uid}: {e}", exc_info=True)
        response = "Произошла ошибка. Попробуйте ещё раз."

    for chunk in [response[i:i+4096] for i in range(0, len(response), 4096)]:
        await update.message.reply_text(chunk, reply_markup=MAIN_KB)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)


# ── Запуск ────────────────────────────────────────────────────────────────────
def main():
    if not TG_TOKEN or TG_TOKEN == "your-telegram-bot-token":
        raise RuntimeError("Укажите TG_TOKEN в config.py")
    if not ANTHROPIC_KEY or ANTHROPIC_KEY == "your-anthropic-api-key":
        raise RuntimeError("Укажите ANTHROPIC_KEY в config.py")

    logger.info(f"Запуск бота. DB: {DB_PATH}")

    builder = Application.builder().token(TG_TOKEN)
    if PROXY_URL:
        logger.info(f"Используем прокси: {PROXY_URL}")
        builder = builder.proxy_url(PROXY_URL)
    app = builder.build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("reset",   cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(handle_error)
    logger.info("Бот запущен, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
