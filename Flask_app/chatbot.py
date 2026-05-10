"""
LINA - robot-aware chatbot brain.

Optional packages:
    pip install groq tavily-python ddgs

Environment variables:
    GROQ_API_KEY or GROQ
    TAVILY_API_KEY or TAVILY
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

try:
    from groq import Groq
except Exception:  # pragma: no cover - optional runtime dependency
    Groq = None

try:
    from tavily import TavilyClient
except Exception:  # pragma: no cover - optional runtime dependency
    TavilyClient = None

try:
    from ddgs import DDGS
except Exception:  # pragma: no cover - optional runtime dependency
    try:
        from duckduckgo_search import DDGS
    except Exception:
        DDGS = None


def _load_local_env() -> None:
    """Load simple KEY=VALUE pairs from Flask_app/.env without requiring dotenv."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_local_env()


# ----------------------------- CONFIG ---------------------------------

MODEL = os.getenv("LINA_GROQ_MODEL", "llama-3.3-70b-versatile")
ROUTER_MODEL = os.getenv("LINA_ROUTER_MODEL", "llama-3.1-8b-instant")
MAX_TOKENS = int(os.getenv("LINA_MAX_TOKENS", "420"))
TEMPERATURE = float(os.getenv("LINA_TEMPERATURE", "0.45"))
MAX_HISTORY = int(os.getenv("LINA_MAX_HISTORY", "10"))
TIMEZONE = os.getenv("LINA_TIMEZONE", "Asia/Kolkata")
LOCATION = os.getenv("LINA_LOCATION", "Purnia, Bihar, India")
DEFAULT_NEWS_COUNT = int(os.getenv("LINA_DEFAULT_NEWS_COUNT", "5"))
MAX_SEARCH_RESULTS = int(os.getenv("LINA_MAX_SEARCH_RESULTS", "10"))
NEWS_FRESH_DAYS = int(os.getenv("LINA_NEWS_FRESH_DAYS", "7"))
LIVE_FRESH_DAYS = int(os.getenv("LINA_LIVE_FRESH_DAYS", "2"))

VALID_SEARCH_TYPES = {"none", "ddg", "tavily", "hybrid"}
LIVE_KEYWORDS = {
    "today",
    "latest",
    "current",
    "recent",
    "now",
    "live",
    "breaking",
    "this week",
    "this month",
    "2026",
}
FRESH_SEARCH_INTENTS = {"news", "weather", "price", "sports", "recent", "current_role"}
CURRENT_ROLE_KEYWORDS = {
    "minister",
    "chief minister",
    "cm",
    "deputy chief minister",
    "governor",
    "president",
    "prime minister",
    "mayor",
    "mp",
    "mla",
    "ceo",
    "chairman",
    "secretary",
    "portfolio",
}

history: list[dict[str, str]] = []
_decision_cache: dict[str, dict[str, Any]] = {}


GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROQ")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY")

groq_client = Groq(api_key=GROQ_API_KEY) if Groq and GROQ_API_KEY else None
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TavilyClient and TAVILY_API_KEY else None


# ----------------------------- DATA -----------------------------------

@dataclass
class PromptProfile:
    user_text: str
    intent: str = "chat"
    needs_search: bool = False
    search_type: str = "none"
    query: str = ""
    requested_count: int = 1
    confidence: float = 0.75
    robot_action: str | None = None
    answer_style: str = "short"
    reason: str = ""

    def to_route_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("user_text", None)
        return data


@dataclass
class SearchItem:
    title: str
    snippet: str = ""
    url: str = ""
    published: str = ""
    source: str = ""


# -------------------------- PROMPT ANALYSIS ----------------------------

def now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _contains_any(text: str, words: set[str] | tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _extract_requested_count(text: str, default: int = 1) -> int:
    lowered = text.lower()
    word_numbers = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    patterns = [
        r"\b(?:top|latest|first|give me|show me|tell me|list)\s+(\d{1,2})\b",
        r"\b(\d{1,2})\s+(?:news|headlines|updates|points|results|items|articles)\b",
        r"\b(?:top|latest|first|give me|show me|tell me|list)\s+("
        + "|".join(word_numbers)
        + r")\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            raw = match.group(1)
            value = int(raw) if raw.isdigit() else word_numbers.get(raw, default)
            return max(1, min(value, MAX_SEARCH_RESULTS))
    return default


def _is_math_request(text: str) -> bool:
    cleaned = text.lower()
    cleaned = cleaned.replace("what is", "").replace("calculate", "").replace("solve", "")
    return bool(re.fullmatch(r"[\d\s\.\+\-\*\/\%\(\)\^]+", cleaned.strip()))


def _safe_calculate(text: str) -> str | None:
    expression = text.lower()
    expression = expression.replace("what is", "")
    expression = expression.replace("calculate", "")
    expression = expression.replace("solve", "")
    expression = expression.replace("^", "**").strip()
    if not expression or not re.fullmatch(r"[\d\s\.\+\-\*\/\%\(\)\*]+", expression):
        return None

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
    )
    try:
        tree = ast.parse(expression, mode="eval")
        if not all(isinstance(node, allowed_nodes) for node in ast.walk(tree)):
            return None
        result = eval(compile(tree, "<lina-math>", "eval"), {"__builtins__": {}}, {})
    except Exception:
        return None

    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"{expression} = {result}"


def _robot_action(text: str) -> str | None:
    lowered = text.lower()
    actions = {
        "stop": ("stop", "halt", "freeze", "brake"),
        "forward": ("forward", "go ahead", "move ahead", "move forward", "front"),
        "backward": ("backward", "reverse", "back up", "move back"),
        "left": ("left", "turn left", "rotate left"),
        "right": ("right", "turn right", "rotate right"),
        "scan": ("scan", "look around", "auto scan"),
    }
    for action, phrases in actions.items():
        if any(phrase in lowered for phrase in phrases):
            movement_words = ("move", "go", "turn", "rotate", "stop", "halt", "scan", "reverse")
            if action == "stop" or _contains_any(lowered, movement_words):
                return action
    return None


def _with_missing_terms(base: str, *terms: str, include_date: bool = True) -> str:
    value = _norm(base)
    lowered = value.lower()
    additions = []
    for term in terms:
        if term and term.lower() not in lowered:
            additions.append(term)
    if include_date:
        today = now_local().strftime("%d %B %Y")
        if today.lower() not in lowered:
            additions.append(today)
    return _norm(" ".join([value] + additions))


def _build_search_query(text: str, intent: str, count: int) -> str:
    lowered = text.lower()
    now = now_local()
    current_year = now.year
    current_date = now.strftime("%d %B %Y")

    cleaned = _norm(text)
    cleaned = re.sub(r"^(please\s+)?(hey|hi|hello)\s+lina[:,]?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^lina[:,]?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bplease\b", "", cleaned, flags=re.I).strip()

    if intent == "news":
        topic = cleaned
        topic = re.sub(r"\b(give me|show me|tell me|list|top|latest|news|headlines|updates)\b", "", topic, flags=re.I)
        topic = re.sub(r"\b\d{1,2}\b", "", topic).strip(" ,.-")
        if not topic:
            topic = "India"
        if "near me" in lowered or "local" in lowered or "here" in lowered:
            topic = LOCATION
        return f"latest {count} news headlines {topic} today {current_date}"

    if intent == "weather":
        if "here" in lowered or "my location" in lowered or "near me" in lowered:
            return _with_missing_terms(f"weather {LOCATION}", "current", "today")
        return _with_missing_terms(cleaned, "current", "today")

    if intent == "price":
        return _with_missing_terms(cleaned, "live", "price", "now", "today")

    if intent == "sports":
        return _with_missing_terms(cleaned, "live", "latest", "today")

    if intent == "recent":
        return _with_missing_terms(cleaned, "latest", "current", "today")

    if intent == "current_role":
        return _with_missing_terms(cleaned, "official", "current", "today")

    return cleaned


def analyze_prompt(user_text: str) -> PromptProfile:
    text = _norm(user_text)
    lowered = text.lower()
    profile = PromptProfile(user_text=text, query=text)

    if not text:
        profile.intent = "empty"
        profile.confidence = 1.0
        profile.reason = "blank prompt"
        return profile

    count = _extract_requested_count(text, DEFAULT_NEWS_COUNT if "news" in lowered else 1)
    profile.requested_count = count

    greetings = {"hi", "hello", "hey", "namaste", "good morning", "good evening", "good night"}
    if lowered in greetings or re.fullmatch(r"(hi|hello|hey)\s+lina[!.]?", lowered):
        profile.intent = "greeting"
        profile.confidence = 1.0
        profile.reason = "simple greeting"
        return profile

    action = _robot_action(text)
    if action:
        profile.intent = "robot_command"
        profile.robot_action = action
        profile.confidence = 0.95
        profile.reason = "robot movement/control phrase"
        return profile

    if any(phrase in lowered for phrase in ("your name", "who are you", "what can you do", "about yourself")):
        profile.intent = "identity"
        profile.confidence = 0.95
        profile.reason = "robot identity"
        return profile

    if any(phrase in lowered for phrase in ("what time", "current time", "time now")):
        profile.intent = "time"
        profile.confidence = 1.0
        profile.reason = "local clock"
        return profile

    if any(phrase in lowered for phrase in ("what date", "today's date", "todays date", "what day", "which day")):
        profile.intent = "date"
        profile.confidence = 1.0
        profile.reason = "local calendar"
        return profile

    if _is_math_request(text):
        profile.intent = "math"
        profile.confidence = 0.95
        profile.reason = "safe local calculation"
        return profile

    if any(word in lowered for word in ("news", "headline", "headlines", "breaking", "current affairs")):
        profile.intent = "news"
        profile.needs_search = True
        profile.search_type = "tavily"
        profile.requested_count = max(count, DEFAULT_NEWS_COUNT if count == 1 else count)
        profile.query = _build_search_query(text, profile.intent, profile.requested_count)
        profile.answer_style = "bullets"
        profile.confidence = 0.98
        profile.reason = "fresh news needs live search"
        return profile

    if any(word in lowered for word in ("weather", "temperature", "rain", "forecast", "humidity", "wind")):
        profile.intent = "weather"
        profile.needs_search = True
        profile.search_type = "tavily"
        profile.query = _build_search_query(text, profile.intent, count)
        profile.confidence = 0.96
        profile.reason = "weather changes frequently"
        return profile

    if any(word in lowered for word in ("price", "stock", "share", "bitcoin", "crypto", "gold rate", "silver rate")):
        profile.intent = "price"
        profile.needs_search = True
        profile.search_type = "tavily"
        profile.query = _build_search_query(text, profile.intent, count)
        profile.confidence = 0.93
        profile.reason = "price/live market data"
        return profile

    if any(word in lowered for word in ("score", "match", "ipl", "cricket", "football", "fixture", "schedule")):
        profile.intent = "sports"
        profile.needs_search = True
        profile.search_type = "tavily"
        profile.query = _build_search_query(text, profile.intent, count)
        profile.confidence = 0.9
        profile.reason = "sports schedules and scores can change"
        return profile

    if _contains_any(lowered, CURRENT_ROLE_KEYWORDS):
        profile.intent = "current_role"
        profile.needs_search = True
        profile.search_type = "tavily"
        profile.query = _build_search_query(text, profile.intent, count)
        profile.confidence = 0.9
        profile.reason = "current public/company role can change"
        return profile

    if _contains_any(lowered, LIVE_KEYWORDS):
        profile.intent = "recent"
        profile.needs_search = True
        profile.search_type = "tavily"
        profile.query = _build_search_query(text, profile.intent, count)
        profile.confidence = 0.82
        profile.reason = "prompt asks for recent/current info"
        return profile

    if lowered.startswith(("what is ", "who is ", "where is ", "when was ", "explain ", "define ", "how does ")):
        profile.intent = "knowledge"
        profile.needs_search = True
        profile.search_type = "ddg"
        profile.query = _build_search_query(text, profile.intent, count)
        profile.confidence = 0.8
        profile.reason = "fact lookup benefits from cheap web context"
        return profile

    if any(word in lowered for word in ("best", "compare", "review", "which is better", "recommend")):
        profile.intent = "research"
        profile.needs_search = True
        profile.search_type = "hybrid"
        profile.query = _build_search_query(text, profile.intent, min(max(count, 3), 6))
        profile.requested_count = min(max(count, 3), 6)
        profile.confidence = 0.7
        profile.reason = "recommendation/research query"
        return profile

    profile.intent = "chat"
    profile.confidence = 0.62
    profile.reason = "general conversation"
    return profile


ROUTER_PROMPT = """
You route messages for LINA, a small physical robot assistant.
Return only JSON with these keys:
needs_search, search_type, query, intent, requested_count, confidence, reason.

search_type must be one of: none, ddg, tavily, hybrid.
Use none for greetings, robot commands, time/date, math, identity, jokes, and normal chat.
Use ddg for stable facts and explanations.
Use tavily for news, weather, prices, sports, releases, current office holders/current roles, and anything live/recent.
Use hybrid for recommendations or comparison questions where multiple sources help.
Keep requested_count between 1 and 10.
"""


def _ai_route(user_text: str, baseline: PromptProfile) -> PromptProfile:
    if not groq_client:
        return baseline
    try:
        response = groq_client.chat.completions.create(
            model=ROUTER_MODEL,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": user_text},
            ],
            max_tokens=140,
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        data = json.loads(raw)
    except Exception as exc:
        print(f"[LINA route] Groq router fallback: {exc}")
        return baseline

    search_type = str(data.get("search_type", baseline.search_type)).lower()
    if search_type not in VALID_SEARCH_TYPES:
        search_type = baseline.search_type
    try:
        requested_count = int(data.get("requested_count", baseline.requested_count))
    except Exception:
        requested_count = baseline.requested_count

    baseline.needs_search = bool(data.get("needs_search", baseline.needs_search))
    baseline.search_type = search_type
    baseline.intent = str(data.get("intent", baseline.intent)) or baseline.intent
    baseline.requested_count = max(1, min(requested_count, MAX_SEARCH_RESULTS))
    baseline.query = _norm(str(data.get("query", baseline.query))) or baseline.query
    baseline.confidence = float(data.get("confidence", baseline.confidence) or baseline.confidence)
    baseline.reason = str(data.get("reason", baseline.reason)) or baseline.reason
    return baseline


def route(user_text: str) -> dict[str, Any]:
    """Decide whether a prompt needs search, without spending Groq tokens unless useful."""
    profile = analyze_prompt(user_text)
    if profile.intent != "chat" and 0.45 <= profile.confidence < 0.68:
        profile = _ai_route(user_text, profile)

    route_data = profile.to_route_dict()
    _decision_cache[_norm(user_text)] = route_data
    return route_data


# ----------------------------- SEARCH ---------------------------------

def _unique_items(items: list[SearchItem], limit: int) -> list[SearchItem]:
    seen: set[str] = set()
    unique: list[SearchItem] = []
    for item in items:
        key = (item.url or item.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _freshness_days(intent: str) -> int:
    if intent == "news":
        return NEWS_FRESH_DAYS
    if intent == "current_role":
        return 30
    if intent in {"weather", "price", "sports", "recent"}:
        return LIVE_FRESH_DAYS
    return 30


def _freshness_label(intent: str) -> str:
    days = _freshness_days(intent)
    if days <= 1:
        return "today only"
    return f"last {days} days"


def _fresh_search_query(query: str, intent: str) -> str:
    if intent not in FRESH_SEARCH_INTENTS:
        return query

    today = now_local().strftime("%d %B %Y")
    lowered = query.lower()
    if today.lower() in lowered:
        return query
    if intent == "news":
        return f"{query} latest today {today}"
    if intent == "current_role":
        return f"{query} official current today {today}"
    if intent in {"weather", "price", "sports"}:
        return f"{query} current live today {today}"
    return f"{query} latest current today {today}"


def _ddg_timelimit(intent: str) -> str | None:
    if intent in {"weather", "price", "sports"}:
        return "d"
    if intent in {"news", "recent"}:
        return "w"
    return None


def _tavily_time_range(intent: str) -> str | None:
    days = _freshness_days(intent)
    if intent not in FRESH_SEARCH_INTENTS:
        return None
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 30:
        return "month"
    return "year"


def _parse_published(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None

    relative = re.search(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", text, re.I)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        if unit == "minute":
            return now_local() - timedelta(minutes=amount)
        if unit == "hour":
            return now_local() - timedelta(hours=amount)
        if unit == "day":
            return now_local() - timedelta(days=amount)
        if unit == "week":
            return now_local() - timedelta(weeks=amount)
        if unit == "month":
            return now_local() - timedelta(days=amount * 30)

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo:
            return parsed.astimezone(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
        return parsed
    except Exception:
        pass

    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text[:30], fmt)
        except Exception:
            continue
    return None


def _filter_stale_items(items: list[SearchItem], intent: str) -> list[SearchItem]:
    if intent not in FRESH_SEARCH_INTENTS:
        return items

    cutoff = now_local().replace(tzinfo=None) - timedelta(days=_freshness_days(intent))
    fresh: list[SearchItem] = []
    dated = 0
    for item in items:
        published = _parse_published(item.published)
        if published:
            dated += 1
            if published < cutoff:
                continue
        fresh.append(item)

    if dated and not fresh:
        return []
    return fresh


def _official_score(item: SearchItem) -> int:
    host = urlparse(item.url or "").netloc.lower()
    title = item.title.lower()
    snippet = item.snippet.lower()
    score = 0

    if host.endswith(".gov") or ".gov." in host or host.endswith(".nic.in") or "state.bihar.gov.in" in host:
        score += 50
    if any(word in host for word in ("gov", "nic", "official")):
        score += 20
    if any(word in title for word in ("official", "department", "government", "minister")):
        score += 10
    if any(word in snippet for word in ("honourable minister", "current", "assumed charge", "portfolio")):
        score += 6
    if item.published:
        score += 2
    return score


def _rank_items(items: list[SearchItem], intent: str) -> list[SearchItem]:
    if intent != "current_role":
        return items
    return sorted(items, key=_official_score, reverse=True)


def _format_search_context(
    query: str,
    source: str,
    items: list[SearchItem],
    answer: str = "",
    intent: str = "search",
    requested_count: int = 3,
) -> str:
    freshness = (
        _freshness_label(intent)
        if intent in FRESH_SEARCH_INTENTS
        else "relevant current source context"
    )
    lines = [
        "[SearchContext]",
        f"source: {source}",
        f"intent: {intent}",
        f"requested_count: {requested_count}",
        f"current_date: {now_local().strftime('%A, %d %B %Y')}",
        f"freshness_policy: use only {freshness}",
        f"query: {query}",
    ]
    if answer:
        lines.append(f"quick_answer: {answer[:700]}")
    lines.append("results:")
    for idx, item in enumerate(items[:requested_count], start=1):
        lines.append(f"{idx}. title: {item.title[:180]}")
        if item.published:
            lines.append(f"   published: {item.published[:80]}")
        if item.url:
            lines.append(f"   url: {item.url[:220]}")
        if item.snippet:
            lines.append(f"   snippet: {item.snippet[:420]}")
    return "\n".join(lines)


def _parse_search_meta(query: str) -> tuple[str, int]:
    lowered = query.lower()
    count = _extract_requested_count(query, DEFAULT_NEWS_COUNT if "news" in lowered else 3)
    if any(word in lowered for word in ("news", "headline", "breaking")):
        return "news", max(count, DEFAULT_NEWS_COUNT if count == 1 else count)
    if any(word in lowered for word in ("weather", "rain", "forecast", "temperature")):
        return "weather", min(count, 4)
    if any(word in lowered for word in ("price", "stock", "bitcoin", "gold", "crypto")):
        return "price", min(count, 4)
    if any(word in lowered for word in ("score", "match", "ipl", "cricket", "football", "fixture", "schedule")):
        return "sports", min(count, 5)
    if _contains_any(lowered, CURRENT_ROLE_KEYWORDS):
        return "current_role", min(max(count, 3), 6)
    if _contains_any(lowered, LIVE_KEYWORDS):
        return "recent", min(max(count, 3), MAX_SEARCH_RESULTS)
    return "search", min(max(count, 3), MAX_SEARCH_RESULTS)


def search_ddg(query: str, max_results: int | None = None, intent: str | None = None) -> str:
    """Cheap/free search path for stable facts and fallback retrieval."""
    intent = intent or _parse_search_meta(query)[0]
    max_results = max_results or _parse_search_meta(query)[1]
    search_query = _fresh_search_query(query, intent)
    timelimit = _ddg_timelimit(intent)

    if not DDGS:
        print("[LINA search] DDG package is not installed.")
        return ""

    print(f"[LINA search] DDG -> {search_query}")
    items: list[SearchItem] = []
    try:
        with DDGS() as ddgs:
            if intent == "news" and hasattr(ddgs, "news"):
                kwargs = {"region": "in-en", "max_results": max_results}
                if timelimit:
                    kwargs["timelimit"] = timelimit
                raw_results = list(ddgs.news(search_query, **kwargs))
                for result in raw_results:
                    items.append(
                        SearchItem(
                            title=result.get("title", ""),
                            snippet=result.get("body", ""),
                            url=result.get("url", ""),
                            published=result.get("date", ""),
                            source="ddg-news",
                        )
                    )
            else:
                kwargs = {"region": "in-en", "max_results": max_results}
                if timelimit:
                    kwargs["timelimit"] = timelimit
                raw_results = list(ddgs.text(search_query, **kwargs))
                for result in raw_results:
                    items.append(
                        SearchItem(
                            title=result.get("title", ""),
                            snippet=result.get("body", ""),
                            url=result.get("href", ""),
                            source="ddg",
                        )
                    )
    except TypeError:
        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(search_query, max_results=max_results))
            for result in raw_results:
                items.append(
                    SearchItem(
                        title=result.get("title", ""),
                        snippet=result.get("body", ""),
                        url=result.get("href", ""),
                        source="ddg",
                    )
                )
        except Exception as exc:
            print(f"[LINA search] DDG failed: {exc}")
            return ""
    except Exception as exc:
        print(f"[LINA search] DDG failed: {exc}")
        return ""

    items = _unique_items(items, max_results)
    items = _filter_stale_items(items, intent)
    items = _rank_items(items, intent)
    if not items:
        return ""
    return _format_search_context(search_query, "duckduckgo", items, intent=intent, requested_count=max_results)


def search_tavily(query: str, max_results: int | None = None, intent: str | None = None) -> str:
    """Accurate/live search path, used only when freshness matters."""
    intent = intent or _parse_search_meta(query)[0]
    max_results = max_results or _parse_search_meta(query)[1]
    search_query = _fresh_search_query(query, intent)

    if not tavily_client:
        print("[LINA search] Tavily is not configured.")
        return ""

    search_depth = "advanced" if intent in {"research"} else "basic"
    include_answer = intent not in {"news"} or max_results <= 4
    time_range = _tavily_time_range(intent)

    payload: dict[str, Any] = {
        "query": search_query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": include_answer,
        "include_raw_content": False,
    }
    if time_range:
        payload["time_range"] = time_range
    if intent == "news":
        payload["topic"] = "news"
        payload["days"] = _freshness_days(intent)

    print(f"[LINA search] Tavily -> {search_query}")
    try:
        while True:
            try:
                result = tavily_client.search(**payload)
                break
            except TypeError:
                for optional_key in ("days", "topic", "time_range", "include_raw_content"):
                    if optional_key in payload:
                        payload.pop(optional_key)
                        break
                else:
                    raise
    except Exception as exc:
        print(f"[LINA search] Tavily failed: {exc}")
        return ""

    answer = result.get("answer", "") or ""
    items: list[SearchItem] = []
    for raw in result.get("results", [])[:max_results]:
        items.append(
            SearchItem(
                title=raw.get("title", ""),
                snippet=raw.get("content", ""),
                url=raw.get("url", ""),
                published=raw.get("published_date", "") or raw.get("date", ""),
                source="tavily",
            )
        )

    items = _unique_items(items, max_results)
    items = _filter_stale_items(items, intent)
    items = _rank_items(items, intent)
    if intent in FRESH_SEARCH_INTENTS and not items:
        return ""
    if not items and not answer:
        return ""
    return _format_search_context(search_query, "tavily", items, answer, intent=intent, requested_count=max_results)


def smart_search(search_type: str, query: str) -> str:
    """Run the cheapest useful search pipeline and fall back gracefully."""
    intent, count = _parse_search_meta(query)
    search_type = (search_type or "none").lower()
    if search_type not in VALID_SEARCH_TYPES:
        search_type = "ddg"

    if search_type == "none":
        return ""

    if search_type == "ddg":
        result = search_ddg(query, count, intent)
        if not result and intent in {"news", "weather", "price", "sports", "recent"}:
            result = search_tavily(query, count, intent)
        return result

    if search_type == "tavily":
        result = search_tavily(query, count, intent)
        if not result:
            result = search_ddg(query, count, intent)
        return result

    if search_type == "hybrid":
        primary = search_tavily(query, count, "research")
        secondary = search_ddg(query, count, "research")
        if primary and secondary:
            return primary + "\n\n" + secondary
        return primary or secondary

    return ""


# --------------------------- ANSWERING --------------------------------

def build_system_prompt() -> str:
    now = now_local()
    return f"""
You are LINA, the user's personal voice assistant inside a friendly physical robot.
You live in a robot body with motors, a rotating head sonar, IR sensors, and a dashboard cockpit.

Current date/time: {now.strftime("%A, %d %B %Y | %I:%M %p")} ({TIMEZONE})
User location: {LOCATION}

Behavior:
- Sound like a calm, capable personal assistant, not a generic chatbot.
- Speak in short natural sentences that work well aloud.
- Be warm, practical, and quietly proactive.
- Use "I" naturally as Lina. Do not over-explain your system.
- Be concise by default, but obey requested counts and bullet formats when the user asks.
- Never invent live facts. If search context is provided, answer from it.
- For news, weather, prices, sports, current roles, and "latest" questions, use only current/recent search context and reject stale results.
- If sources disagree, say what is most likely and mention uncertainty.
- For robot movement commands, acknowledge clearly without pretending the motors moved unless a control pipeline executed it.
- Never say you are an AI language model.
"""


def trim_history() -> None:
    global history
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]


def _remember(role: str, content: str) -> None:
    history.append({"role": role, "content": content})
    trim_history()


def _local_response(profile: PromptProfile) -> str | None:
    now = now_local()
    if profile.intent == "greeting":
        return "Hi, I am Lina. I am awake, listening, and ready to help."

    if profile.intent == "identity":
        return "I am Lina, your robot assistant. I can chat, search the web, explain things, and help you control or monitor the robot cockpit."

    if profile.intent == "time":
        return f"It is {now.strftime('%I:%M %p')} in {LOCATION}."

    if profile.intent == "date":
        return f"Today is {now.strftime('%A, %d %B %Y')}."

    if profile.intent == "math":
        return _safe_calculate(profile.user_text)

    if profile.intent == "robot_command":
        action = profile.robot_action or "command"
        if action == "stop":
            return "Stop command understood. I will keep the drive pipeline cautious and ready to halt."
        if action == "scan":
            return "Scan command understood. I can use my rotating head sonar to check the area."
        return f"{action.capitalize()} command understood. I will treat nearby obstacles and sensor safety as priority."

    return None


def _build_answer_instruction(profile: PromptProfile, search_result: str | None) -> str:
    if profile.intent == "news":
        return (
            f"Give exactly {profile.requested_count} news bullet points if the search context has enough unique items. "
            "Each bullet should be one line: headline - short useful summary. "
            f"Use only current/latest items from {_freshness_label('news')}. "
            "If fewer unique current items are available, give only what is available and say how many were found."
        )

    if profile.intent in {"weather", "price", "sports"}:
        return (
            f"Answer only with current/live information from {_freshness_label(profile.intent)}. "
            "If the context looks old or unclear, say you cannot confirm the current value. "
            "Keep it under 5 short bullets or sentences."
        )

    if profile.intent == "current_role":
        return (
            "Answer the current office holder/role directly. Prefer official government/company pages first, "
            f"then very recent reports from {_freshness_label('current_role')}. "
            "Ignore older office-holder names unless explaining that they are former holders. "
            "If the sources conflict, say the official/current source is preferred."
        )

    if profile.intent in {"knowledge", "research", "recent"} and search_result:
        if profile.intent == "recent":
            return (
                f"Use only current/latest information from {_freshness_label('recent')}. "
                "Do not include old background unless the user asks for history."
            )
        return "Use the search context for accuracy. Give the best answer first, then one short caveat if needed."

    return "Answer naturally and briefly."


def _fallback_from_search(profile: PromptProfile, search_result: str) -> str:
    titles = re.findall(r"^\d+\.\s+title:\s*(.+)$", search_result, flags=re.M)
    snippets = re.findall(r"^\s+snippet:\s*(.+)$", search_result, flags=re.M)
    if not titles:
        return "I found search context, but I could not shape it into a clean answer."

    if profile.intent == "news":
        bullets = []
        for idx, title in enumerate(titles[: profile.requested_count]):
            snippet = snippets[idx] if idx < len(snippets) else ""
            summary = f" - {snippet[:160]}" if snippet else ""
            bullets.append(f"- {title}{summary}")
        return "\n".join(bullets)

    first = titles[0]
    detail = snippets[0] if snippets else ""
    return f"{first}. {detail[:260]}".strip()


def _max_tokens_for(profile: PromptProfile) -> int:
    if profile.intent == "news":
        return min(950, max(MAX_TOKENS, 90 * profile.requested_count))
    if profile.intent in {"research", "recent"}:
        return min(800, max(MAX_TOKENS, 560))
    return MAX_TOKENS


def chat(user_text: str, search_result: str | None = None) -> str:
    """Generate the final reply. Uses local answers first, Groq only when it adds value."""
    clean_text = _norm(user_text)
    cached = _decision_cache.get(clean_text)
    profile = analyze_prompt(clean_text)
    if cached:
        for key, value in cached.items():
            if hasattr(profile, key):
                setattr(profile, key, value)

    local = _local_response(profile)
    if local and not search_result:
        _remember("user", clean_text)
        _remember("assistant", local)
        return local

    live_intents = {"news", "weather", "price", "sports", "recent", "current_role"}
    if profile.intent in live_intents and not search_result:
        reply = "I could not reach live search right now, so I do not want to guess. Please check the connection or API keys and ask me again."
        _remember("user", clean_text)
        _remember("assistant", reply)
        return reply

    if search_result and not groq_client:
        reply = _fallback_from_search(profile, search_result)
        _remember("user", clean_text)
        _remember("assistant", reply)
        return reply

    if not groq_client:
        reply = "My Groq brain is not configured yet, but my local robot systems are online."
        _remember("user", clean_text)
        _remember("assistant", reply)
        return reply

    instruction = _build_answer_instruction(profile, search_result)
    current_content = clean_text
    if search_result:
        current_content = (
            f"{clean_text}\n\n"
            f"[Answer instruction]\n{instruction}\n\n"
            f"[Web search context]\n{search_result}"
        )
    else:
        current_content = f"{clean_text}\n\n[Answer instruction]\n{instruction}"

    messages = [{"role": "system", "content": build_system_prompt()}]
    messages.extend(history[-MAX_HISTORY:])
    messages.append({"role": "user", "content": current_content})

    try:
        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=_max_tokens_for(profile),
            temperature=TEMPERATURE,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[LINA chat] Groq failed: {exc}")
        if search_result:
            reply = _fallback_from_search(profile, search_result)
        else:
            reply = "My online brain had a hiccup, but I am still listening from the robot cockpit."

    _remember("user", clean_text)
    _remember("assistant", reply)
    return reply


# -------------------------- LOCAL COMMANDS -----------------------------

def handle_command(text: str) -> bool:
    """Handle CLI-only commands. Returns True if handled."""
    cmd = text.lower().strip()
    if cmd == "reset":
        global history
        history = []
        _decision_cache.clear()
        print("[LINA] Memory cleared.")
        return True
    if cmd == "history":
        print(f"[LINA] History: {len(history)} messages")
        return True
    if cmd in ("quit", "exit", "bye"):
        print("LINA: Goodbye.")
        raise SystemExit(0)
    return False


def get_user_input() -> str:
    return input("You: ").strip()


def speak_response(text: str) -> None:
    print(f"\nLINA: {text}\n")


def main() -> None:
    now = now_local()
    print("=" * 58)
    print("  LINA Chatbot | robot-aware search + Groq synthesis")
    print("=" * 58)
    print(f"  Time      : {now.strftime('%I:%M %p')} ({TIMEZONE})")
    print(f"  Location  : {LOCATION}")
    print(f"  Groq      : {'ready' if groq_client else 'not configured'}")
    print(f"  Tavily    : {'ready' if tavily_client else 'not configured'}")
    print(f"  DDG       : {'ready' if DDGS else 'not installed'}")
    print("  Commands  : reset | history | quit\n")

    while True:
        try:
            user_text = get_user_input()
            if not user_text:
                continue
            if handle_command(user_text):
                continue

            decision = route(user_text)
            stype = decision.get("search_type", "none")
            query = decision.get("query", user_text)
            print(f"[LINA route] {decision.get('intent')} -> {stype} ({decision.get('reason')})")

            search_result = ""
            if decision.get("needs_search") and stype != "none":
                search_result = smart_search(stype, query)

            reply = chat(user_text, search_result)
            speak_response(reply)

        except KeyboardInterrupt:
            print("\nLINA: Goodbye.")
            break


if __name__ == "__main__":
    main()
