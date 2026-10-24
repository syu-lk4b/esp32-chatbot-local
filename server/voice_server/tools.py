"""Tools the LLM can call. Currently: web search through a local SearXNG."""
from __future__ import annotations

import calendar
import threading
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

SEARCH_TIMEOUT_SECONDS = 8.0
SEARCH_MAX_RESULTS = 6
SNIPPET_CHARS = 200
# Results sharing less than this fraction of the query's terms are dropped; Bing in
# particular pads Chinese queries with unrelated English pages (calendars, year articles).
MIN_RELEVANCE = 0.3

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9]+")

WEB_SEARCH_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "联网搜索网页。用于其他工具覆盖不到的实时或不确定的问题，例如比赛结果、最近发生的事、具体事实核查、"
                       "价格和地点信息。不要用它查天气、股价/指数、科技新闻：这些必须用 get_weather、get_stock_quote、get_tech_news。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "简洁的搜索关键词，中文问题用中文搜索"}},
            "required": ["query"],
        },
    },
}


class WebSearch:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=SEARCH_TIMEOUT_SECONDS)

    def __call__(self, query: str) -> str:
        """Returns a compact plain-text digest for the model; errors are reported as text."""
        query = query.strip()
        if not query:
            return "搜索失败：关键词为空"
        try:
            resp = self._client.get("/search", params={"q": query, "format": "json"})
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("web_search %r failed: %s", query, e)
            return f"搜索失败：{e}"
        return format_results(rank_results(query, results))


def query_terms(query: str) -> set[str]:
    """CJK character bigrams (single chars for 1-char runs) plus lowercase ASCII words."""
    terms: set[str] = set()
    for run in _CJK_RUN.findall(query):
        terms |= {run[i : i + 2] for i in range(len(run) - 1)} or {run}
    terms |= set(_WORD.findall(query.lower()))
    return terms


def rank_results(query: str, results: list[dict]) -> list[dict]:
    """Drops results unrelated to the query and orders the rest by term overlap."""
    terms = query_terms(query)
    if not terms:
        return results
    scored = []
    for i, r in enumerate(results):
        text = f"{r.get('title') or ''} {r.get('content') or ''}".lower()
        score = sum(t in text for t in terms) / len(terms)
        if score >= MIN_RELEVANCE:
            scored.append((-score, i, r))  # i keeps engine order among ties
    return [r for _, _, r in sorted(scored)]


def format_results(results: list[dict]) -> str:
    lines = []
    for r in results[:SEARCH_MAX_RESULTS]:
        title = (r.get("title") or "").strip()
        snippet = " ".join((r.get("content") or "").split())[:SNIPPET_CHARS]
        site = urlparse(r.get("url") or "").netloc
        date = (r.get("publishedDate") or "")[:10]
        meta = " ".join(x for x in (site, date) if x)
        lines.append(f"{len(lines) + 1}. {title}（{meta}）{snippet}")
    return "\n".join(lines) if lines else "没有搜到相关结果"


# ---------------------------------------------------------------- topic tools (free, no key)
# Dedicated sources answer the commonest real-time questions with exact numbers in one call,
# instead of stitching figures out of search snippets (see search-eval/RESULTS.md).

class _TtlCache:
    def __init__(self, ttl_seconds: float, clock=time.monotonic) -> None:
        self._ttl, self._clock, self._items = ttl_seconds, clock, {}

    def get(self, key):
        hit = self._items.get(key)
        return hit[1] if hit and self._clock() - hit[0] < self._ttl else None

    def put(self, key, value):
        self._items[key] = (self._clock(), value)
        return value


# WMO weather interpretation codes (https://open-meteo.com/en/docs).
_WMO = {0: "晴", 1: "少云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇", 51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
        56: "冻毛毛雨", 57: "冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨", 71: "小雪",
        73: "中雪", 75: "大雪", 77: "雪粒", 80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
        95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹"}

WEATHER_SPEC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询天气：当前天气和未来几天的预报（气温、降水概率、风）。问天气、要不要带伞、冷不冷时优先用它。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，不填则为用户所在城市"},
                "days": {"type": "integer", "description": "预报天数 1-7，默认 3"},
            },
        },
    },
}


class WeatherTool:
    def __init__(self, default_city: str, client: httpx.Client | None = None) -> None:
        self._city = default_city
        self._client = client or httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS)
        self._geo = _TtlCache(24 * 3600)
        self._forecast = _TtlCache(600)

    def _locate(self, city: str) -> dict | None:
        if (hit := self._geo.get(city)) is not None:
            return hit
        r = self._client.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": city, "count": 1, "language": "zh"})
        r.raise_for_status()
        results = r.json().get("results") or []
        return self._geo.put(city, results[0] if results else None)

    def _fetch(self, city: str, days: int) -> tuple[dict | None, dict | None]:
        """(place, forecast) for a city, cached; place is None if the city is unknown."""
        place = self._locate(city)
        if not place:
            return None, None
        key = (place["latitude"], place["longitude"], days)
        data = self._forecast.get(key)
        if data is None:
            r = self._client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
                "forecast_days": days,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"})
            r.raise_for_status()
            data = self._forecast.put(key, r.json())
        return place, data

    def __call__(self, city: str = "", days: int = 3) -> str:
        city = (city or self._city).strip()
        days = max(1, min(int(days or 3), 7))
        try:
            place, data = self._fetch(city, days)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("get_weather %r failed: %s", city, e)
            return f"天气查询失败：{e}"
        if not place:
            return f"没有找到城市：{city}"
        return format_weather(place, data)

    def snapshot(self) -> dict | None:
        """Compact current conditions for the board's clock card; None if unavailable."""
        try:
            place, data = self._fetch(self._city, 3)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("weather snapshot failed: %s", e)
            return None
        if not place:
            return None
        cur, d = data["current"], data["daily"]
        code = int(cur["weather_code"])
        return {"city": place.get("name", self._city), "temp": round(cur["temperature_2m"]), "code": code,
                "text": _WMO.get(code, "未知"), "icon": weather_icon(code, bool(cur.get("is_day", 1))),
                "min": round(d["temperature_2m_min"][0]), "max": round(d["temperature_2m_max"][0])}


def weather_icon(code: int, is_day: bool) -> str:
    """Icon family the firmware knows how to draw."""
    if code in (0, 1):
        return "sun" if is_day else "moon"
    if code == 2:
        return "partly" if is_day else "moon"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code >= 95:
        return "thunder"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    return "rain"


def format_weather(place: dict, data: dict) -> str:
    cur, d = data["current"], data["daily"]
    where = place.get("name", "")
    if place.get("admin1") and place["admin1"] != where:
        where += f"（{place['admin1']}）"
    lines = [f"{where} 现在{_WMO.get(cur['weather_code'], '未知')}，{cur['temperature_2m']:.0f}°C，"
             f"体感{cur['apparent_temperature']:.0f}°C，湿度{cur['relative_humidity_2m']}%，风速{cur['wind_speed_10m']:.0f}km/h。"]
    labels = ["今天", "明天", "后天"]
    for i, day in enumerate(d["time"]):
        label = labels[i] if i < len(labels) else day[5:]
        lines.append(f"{label}（{day}）：{_WMO.get(d['weather_code'][i], '未知')}，{d['temperature_2m_min'][i]:.0f}~"
                     f"{d['temperature_2m_max'][i]:.0f}°C，降水概率{d['precipitation_probability_max'][i]}%，"
                     f"最大风速{d['wind_speed_10m_max'][i]:.0f}km/h")
    return "\n".join(lines)


STOCK_SPEC = {
    "type": "function",
    "function": {
        "name": "get_stock_quote",
        "description": "查询股票、指数、ETF 的实时行情（A股、港股、美股）。问股价、涨跌、指数点位时优先用它。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "名称或代码，如 英伟达、NVDA、贵州茅台、600519、腾讯控股、上证指数"}},
            "required": ["query"],
        },
    },
}
_CURRENCY = {"us": "美元", "hk": "港元", "sh": "元", "sz": "元", "bj": "元"}
# Market prefix + code, e.g. sh600519 / usNVDA. The symbol comes from a remote API and goes into a URL.
_SYMBOL = re.compile(r"[a-z]{2}[A-Za-z0-9]{1,12}")


class StockQuoteTool:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS)
        self._symbols = _TtlCache(24 * 3600)

    def _symbol(self, query: str) -> tuple[str, str, str] | None:
        """Resolves a name or code to (market, qt.gtimg.cn symbol, type) via Tencent's suggest API.
        type is e.g. GP (stock), ZS (index), ETF."""
        if (hit := self._symbols.get(query)) is not None:
            return hit
        r = self._client.get("https://smartbox.gtimg.cn/s3/", params={"v": 2, "q": query, "t": "all", "c": 1})
        r.raise_for_status()
        body = r.text.encode().decode("unicode_escape")
        m = re.search(r'v_hint="([^"]*)"', body)
        if not m or not m.group(1) or m.group(1) == "N":
            return self._symbols.put(query, None)
        parts = m.group(1).split("^")[0].split("~")
        market, code, kind = parts[0], parts[1], (parts[4] if len(parts) > 4 else "")
        symbol = f"us{code.split('.')[0].upper()}" if market == "us" else f"{market}{code}"
        if not _SYMBOL.fullmatch(symbol):
            log.warning("get_stock_quote: unexpected symbol %r for %r", symbol, query)
            return self._symbols.put(query, None)
        return self._symbols.put(query, (market, symbol, kind))

    def __call__(self, query: str) -> str:
        query = query.strip()
        if not query:
            return "行情查询失败：名称为空"
        try:
            found = self._symbol(query)
            if not found:
                return f"没有找到：{query}"
            market, symbol, kind = found
            r = self._client.get(f"https://qt.gtimg.cn/q={symbol}")
            r.raise_for_status()
            f = r.content.decode("gbk", errors="replace").split("~")
            name, price, prev, chg, pct, ts = f[1], float(f[3]), float(f[4]), float(f[31]), float(f[32]), f[30]
        except (httpx.HTTPError, ValueError, IndexError) as e:
            log.warning("get_stock_quote %r failed: %s", query, e)
            return f"行情查询失败：{e}"
        unit = "点" if kind.startswith("ZS") else _CURRENCY.get(market, "")
        if re.fullmatch(r"\d{14}", ts):  # A-share style 20261023135928
            ts = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:]}"
        return (f"{name}（{symbol}）最新价 {price:g}{unit}，涨跌 {chg:+g}（{pct:+.2f}%），昨收 {prev:g}{unit}，"
                f"行情时间 {ts}。非交易时段显示的是最近一次收盘。")


NEWS_SPEC = {
    "type": "function",
    "function": {
        "name": "get_tech_news",
        "description": "获取最新的科技新闻标题（IT之家、少数派）。问今天/最近有什么科技、数码、AI 新闻时用它。",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "description": "条数，默认 8"}}},
    },
}
NEWS_FEEDS = {"IT之家": "https://www.ithome.com/rss/", "少数派": "https://sspai.com/feed"}
NEWS_FEED_TIMEOUT_SECONDS = 4.0
NEWS_REFRESH_SECONDS = 300


class TechNewsTool:
    def __init__(self, client: httpx.Client | None = None, feeds: dict[str, str] | None = None) -> None:
        # A slow feed must not stall a spoken answer: cap each one and skip it on timeout.
        self._client = client or httpx.Client(timeout=NEWS_FEED_TIMEOUT_SECONDS, follow_redirects=True)
        self._feeds = feeds or NEWS_FEEDS
        self._cache = _TtlCache(NEWS_REFRESH_SECONDS)
        self._lock = threading.Lock()

    def refresh(self) -> int:
        """Fetches all feeds now (also used by the background warmer); returns item count."""
        with ThreadPoolExecutor(max_workers=len(self._feeds)) as pool:
            batches = pool.map(lambda kv: self._fetch(*kv), self._feeds.items())
        items = sorted((i for b in batches for i in b), reverse=True)
        if items:
            with self._lock:
                self._cache.put("all", items)
        return len(items)

    def start_background_refresh(self) -> None:
        """Keeps the cache warm so a spoken "今天有什么新闻" never waits on the feeds."""
        def loop():
            while True:
                try:
                    self.refresh()
                except Exception as e:  # never let the warmer thread die
                    log.warning("news refresh failed: %s", e)
                time.sleep(NEWS_REFRESH_SECONDS - 30)
        threading.Thread(target=loop, name="news-refresh", daemon=True).start()

    def _fetch(self, source: str, url: str) -> list[tuple[float, str, str]]:
        import feedparser  # small, only needed here

        try:
            r = self._client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("news feed %s failed: %s", source, e)
            return []
        out = []
        for e in feedparser.parse(r.content).entries:
            ts = e.get("published_parsed") or e.get("updated_parsed")  # struct_time in UTC
            out.append((calendar.timegm(ts) if ts else 0, source, e.get("title", "").strip()))
        return out

    def __call__(self, limit: int = 8) -> str:
        limit = max(1, min(int(limit or 8), 15))
        with self._lock:
            items = self._cache.get("all")
        if items is None:
            self.refresh()
            with self._lock:
                items = self._cache.get("all")
        if not items:
            return "新闻获取失败"
        lines = []
        for i, (ts, source, title) in enumerate(items[:limit], 1):
            when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else ""
            lines.append(f"{i}. [{source} {when}] {title}")
        return "\n".join(lines)



# ---------------------------------------------------------------- tool routing hints
# Without "thinking", models sometimes answer weather/stock questions from memory instead of
# calling a tool. A cheap keyword pass names the tool the question needs; the LLM still
# decides the arguments. Order matters: first match wins.
_ROUTES = (
    ("get_weather", re.compile(r"天气|气温|下雨|下雪|带伞|冷不冷|热不热|多少度|温度|刮风|台风|雾霾|weather", re.I)),
    ("get_stock_quote", re.compile(r"股价|股票|一股|涨了|跌了|涨跌|收盘|开盘|行情|指数|市值|大盘|A股|港股|美股|"
                                   r"基金|ETF|比特币|币价|stock|NASDAQ|纳斯达克|道琼斯|恒生", re.I)),
    ("get_tech_news", re.compile(r"(科技|数码|AI|人工智能|互联网|手机|苹果|芯片).{0,6}(新闻|消息|资讯|动态)|新闻", re.I)),
)


def route_hint(user: str, available: set[str]) -> str | None:
    """Returns a system hint naming the tool this question needs, or None."""
    for tool, pattern in _ROUTES:
        if tool in available and pattern.search(user):
            return f"这个问题必须先调用 {tool} 获取实时数据再回答：不要用 web_search 代替，也不要凭记忆回答。"
    return None
