"""
LangChain агент для поиска квартир по SQLite базе.
Используется как модуль из bot.py.
"""

import json
import sqlite3
import re
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

ANTHROPIC_API_KEY = "your-anthropic-api-key"  # заменить или передать при инициализации
DB_PATH = "apartments.db"


def load_prompt(filename: str) -> str:
    with open(f"prompts/{filename}", "r", encoding="utf-8") as f:
        return f.read().strip()


# ── Инициализация LLM ─────────────────────────────────────────────────────────
def make_llm(api_key: str) -> ChatAnthropic:
    return ChatAnthropic(
        model="claude-haiku-4-5",
        temperature=0.5,
        anthropic_api_key=api_key,
        base_url="https://api.proxyapi.ru/anthropic",
        max_tokens=2000,
    )


# ── Построение SQL из фильтров ────────────────────────────────────────────────
def build_sql(filters: dict) -> tuple[str, list]:
    conditions = []
    params = []

    if filters.get("тип"):
        conditions.append("тип = ?")
        params.append(filters["тип"])

    if filters.get("цена_min") is not None:
        conditions.append("цена >= ?")
        params.append(filters["цена_min"])

    if filters.get("цена_max") is not None:
        conditions.append("цена <= ?")
        params.append(filters["цена_max"])

    if filters.get("площадь_min") is not None:
        conditions.append("площадь >= ?")
        params.append(filters["площадь_min"])

    if filters.get("площадь_max") is not None:
        conditions.append("площадь <= ?")
        params.append(filters["площадь_max"])

    if filters.get("жк"):
        conditions.append("жк LIKE ?")
        params.append(f"%{filters['жк']}%")

    if filters.get("этаж_min") is not None:
        conditions.append("этаж >= ?")
        params.append(filters["этаж_min"])

    if filters.get("этаж_max") is not None:
        conditions.append("этаж <= ?")
        params.append(filters["этаж_max"])

    if filters.get("корпус"):
        conditions.append("корпус = ?")
        params.append(filters["корпус"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    order_by = filters.get("order_by") or "цена"
    order = filters.get("order") or "asc"
    if order not in ("asc", "desc"):
        order = "asc"
    if order_by not in ("цена", "площадь", "этаж"):
        order_by = "цена"

    limit = int(filters.get("limit") or 5)
    if limit not in (3, 5):
        limit = 5

    sql = f"""
        SELECT id, url, жк, тип, площадь, цена, цена_скидка,
               этаж, корпус, срок_сдачи, отделка
        FROM apartments
        {where}
        ORDER BY {order_by} {order}
        LIMIT {limit}
    """
    return sql.strip(), params


# ── Запрос к БД ───────────────────────────────────────────────────────────────
def query_db(filters: dict, db_path: str) -> list[dict]:
    sql, params = build_sql(filters)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_stats(db_path: str) -> dict:
    """Краткая статистика по БД для контекста."""
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM apartments").fetchone()[0]
    жк_list = [r[0] for r in conn.execute("SELECT DISTINCT жк FROM apartments").fetchall()]
    types = [r[0] for r in conn.execute("SELECT DISTINCT тип FROM apartments").fetchall()]
    prices = conn.execute("SELECT MIN(цена), MAX(цена) FROM apartments").fetchone()
    conn.close()
    return {
        "total": total,
        "жк": жк_list,
        "types": types,
        "цена_min": prices[0],
        "цена_max": prices[1],
    }


# ── Парсинг JSON из ответа LLM ────────────────────────────────────────────────
def parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    # Убираем markdown-блоки
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Пытаемся найти JSON внутри текста
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


# ── Основной агент ────────────────────────────────────────────────────────────
class ApartmentAgent:
    def __init__(self, api_key: str, db_path: str = DB_PATH):
        self.llm = make_llm(api_key)
        self.db_path = db_path
        self.classifier_prompt = load_prompt("classifier.txt")
        self.formatter_prompt = load_prompt("formatter.txt")
        self.stats = db_stats(db_path)

        # История: список (role, text) — последние MAX_HISTORY сообщений
        self.history: list[tuple[str, str]] = []
        self.MAX_HISTORY = 20

    def _add_to_history(self, role: str, text: str):
        self.history.append((role, text))
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY:]

    def _history_text(self, last_n: int = 6) -> str:
        if not self.history:
            return ""
        recent = self.history[-last_n:]
        return "\n".join(f"{'Пользователь' if r == 'human' else 'Ассистент'}: {t}" for r, t in recent)

    def get_summary(self) -> str:
        """Генерирует саммари диалога через LLM."""
        if not self.history:
            return "Диалог ещё не начат."

        dialog = "\n".join(
            f"{'Пользователь' if r == 'human' else 'Ассистент'}: {t}"
            for r, t in self.history
        )
        messages = [
            SystemMessage(content="Сделай краткое саммари диалога на русском языке. 3–5 предложений: что искал пользователь, какие фильтры применялись, что было найдено."),
            HumanMessage(content=dialog),
        ]
        response = self.llm.invoke(messages)
        return response.content

    # ── Шаг 1: классификация ─────────────────────────────────────────────────
    def classify(self, query: str) -> dict:
        stats_ctx = (
            f"В базе {self.stats['total']} квартир. "
            f"ЖК: {', '.join(self.stats['жк'])}. "
            f"Типы: {', '.join(self.stats['types'])}. "
            f"Цены: от {self.stats['цена_min']:,} до {self.stats['цена_max']:,} ₽."
        )

        history_text = ""
        if self.history:
            history_text = "\n\nИстория диалога:\n" + self._history_text()

        messages = [
            SystemMessage(content=self.classifier_prompt + f"\n\n{stats_ctx}"),
            HumanMessage(content=f"{history_text}\n\nНовый запрос: {query}"),
        ]

        response = self.llm.invoke(messages)
        result = parse_json(response.content)
        if not result:
            return {"type": "chat", "message": None}
        return result

    # ── Шаг 2: поиск в БД ────────────────────────────────────────────────────
    def search(self, filters: dict) -> list[dict]:
        return query_db(filters, self.db_path)

    # ── Шаг 3: формирование ответа ────────────────────────────────────────────
    def format_results(self, query: str, filters: dict, apartments: list[dict]) -> str:
        payload = {
            "запрос_пользователя": query,
            "фильтры": filters,
            "найдено": len(apartments),
            "квартиры": apartments,
        }

        messages = [
            SystemMessage(content=self.formatter_prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ]

        response = self.llm.invoke(messages)
        return response.content

    # ── Шаг 4: чат-ответ (приветствие, общие вопросы) ────────────────────────
    def chat_response(self, query: str) -> str:
        history_text = self._history_text()

        stats_ctx = (
            f"Ты — ассистент по подбору квартир застройщика ДОГМА. "
            f"В базе {self.stats['total']} квартир в ЖК: {', '.join(self.stats['жк'])}. "
            f"Помогай пользователю найти подходящую квартиру. "
            f"Если пользователь хочет искать — попроси уточнить параметры (тип, бюджет, площадь)."
        )

        messages = [
            SystemMessage(content=stats_ctx),
            HumanMessage(content=f"{history_text}\n\nПользователь: {query}"),
        ]

        response = self.llm.invoke(messages)
        return response.content

    # ── Главный метод ─────────────────────────────────────────────────────────
    def process(self, query: str) -> str:
        self._add_to_history("human", query)

        # Классифицируем
        classification = self.classify(query)
        intent = classification.get("type")

        if intent == "clarify":
            response = classification.get("question", "Уточните, пожалуйста, параметры поиска.")

        elif intent == "search":
            filters = classification.get("filters", {})
            apartments = self.search(filters)

            if not apartments:
                response = (
                    "По вашему запросу ничего не найдено. "
                    "Попробуйте расширить фильтры — увеличить бюджет, уменьшить площадь "
                    "или убрать ограничение по ЖК."
                )
            else:
                response = self.format_results(query, filters, apartments)

        else:
            # chat / unknown
            response = self.chat_response(query)

        self._add_to_history("ai", response)
        return response
