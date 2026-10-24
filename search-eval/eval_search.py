"""Web-search back-end comparison for 小奇's web_search tool.

Compares every SearXNG engine one at a time (via a throwaway all-engines instance on
:8889, see settings.eval.yml), the ddgs library, and topic-specific free APIs
(Open-Meteo weather, Tencent quotes, tech-news RSS) on the same question set.

usage: ../server/.venv/bin/python eval_search.py   -> results.json + a printed summary
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from voice_server.tools import rank_results  # noqa: E402  (same relevance filter the bot uses)

SEARXNG = "http://127.0.0.1:8889"
OUT = Path(__file__).resolve().parent / "results.json"
TIMEOUT = 10.0
PAUSE_S = 1.0  # between requests to one engine, to stay under rate limits

QUERIES = {
    "weather": "上海明天天气预报",
    "stock": "英伟达 股价 今天",
    "news": "今日科技新闻",
    "fact": "2026年诺贝尔物理学奖 获奖者",
    "product": "iPhone 17 Pro 价格",
    "local": "上海迪士尼门票价格",
}
ENGINES = ["bing", "bing news", "360search", "quark", "sogou", "baidu", "chinaso news", "duckduckgo",
           "duckduckgo news", "google", "google news", "brave", "startpage", "mojeek", "wikipedia", "yahoo", "yandex"]


@dataclass
class Run:
    method: str
    topic: str
    ms: float
    raw: int = 0
    relevant: int = 0
    titles: list[str] = field(default_factory=list)
    error: str = ""


def searx(client: httpx.Client, engine: str, topic: str, q: str) -> Run:
    t = time.perf_counter()
    try:
        r = client.get(f"{SEARXNG}/search", params={"q": q, "format": "json", "engines": engine})
        r.raise_for_status()
        d = r.json()
        res = d.get("results", [])
        bad = ", ".join(f"{e}:{why}" for e, why in d.get("unresponsive_engines", []))
        ranked = rank_results(q, res)
        return Run(f"searxng:{engine}", topic, (time.perf_counter() - t) * 1000, len(res), len(ranked),
                   [x.get("title", "")[:40] for x in (ranked or res)[:2]], bad)
    except (httpx.HTTPError, ValueError) as e:
        return Run(f"searxng:{engine}", topic, (time.perf_counter() - t) * 1000, error=str(e)[:80])


def ddgs_run(kind: str, topic: str, q: str) -> Run:
    from ddgs import DDGS

    t = time.perf_counter()
    try:
        with DDGS(timeout=int(TIMEOUT)) as d:
            items = list((d.text if kind == "text" else d.news)(q, region="cn-zh", max_results=8))
        res = [{"title": i.get("title", ""), "content": i.get("body", "")} for i in items]
        ranked = rank_results(q, res)
        return Run(f"ddgs:{kind}", topic, (time.perf_counter() - t) * 1000, len(res), len(ranked),
                   [x["title"][:40] for x in (ranked or res)[:2]])
    except Exception as e:  # ddgs raises its own exception hierarchy
        return Run(f"ddgs:{kind}", topic, (time.perf_counter() - t) * 1000, error=f"{type(e).__name__}: {str(e)[:60]}")


def open_meteo(client: httpx.Client) -> Run:
    t = time.perf_counter()
    try:
        r = client.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": 31.2304, "longitude": 121.4737, "timezone": "Asia/Shanghai", "forecast_days": 2,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
        r.raise_for_status()
        dly = r.json()["daily"]
        line = (f"{dly['time'][1]} {dly['temperature_2m_min'][1]}~{dly['temperature_2m_max'][1]}°C "
                f"降水概率{dly['precipitation_probability_max'][1]}% code={dly['weather_code'][1]}")
        return Run("api:open-meteo", "weather", (time.perf_counter() - t) * 1000, 1, 1, [line])
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return Run("api:open-meteo", "weather", (time.perf_counter() - t) * 1000, error=str(e)[:80])


def tencent_quote(client: httpx.Client) -> Run:
    t = time.perf_counter()
    try:
        r = client.get("http://qt.gtimg.cn/q=usNVDA")
        r.raise_for_status()
        f = r.content.decode("gbk", errors="replace").split("~")
        line = f"{f[1]} {f[3]} USD {f[32]}% ({f[30]})"
        return Run("api:tencent-quote", "stock", (time.perf_counter() - t) * 1000, 1, 1, [line])
    except (httpx.HTTPError, IndexError) as e:
        return Run("api:tencent-quote", "stock", (time.perf_counter() - t) * 1000, error=str(e)[:80])


def rss(client: httpx.Client, name: str, url: str) -> Run:
    import feedparser

    t = time.perf_counter()
    try:
        r = client.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        titles = [e.get("title", "")[:40] for e in feed.entries]
        return Run(f"rss:{name}", "news", (time.perf_counter() - t) * 1000, len(titles), len(titles), titles[:2])
    except httpx.HTTPError as e:
        return Run(f"rss:{name}", "news", (time.perf_counter() - t) * 1000, error=str(e)[:80])


def main() -> None:
    runs: list[Run] = []
    with httpx.Client(timeout=TIMEOUT) as client:
        for engine in ENGINES:
            for topic, q in QUERIES.items():
                runs.append(searx(client, engine, topic, q))
                time.sleep(PAUSE_S)
            print(f"done {engine}", flush=True)
        for kind in ("text", "news"):
            for topic, q in QUERIES.items():
                runs.append(ddgs_run(kind, topic, q))
                time.sleep(PAUSE_S)
        runs.append(open_meteo(client))
        runs.append(tencent_quote(client))
        runs.append(rss(client, "ithome", "https://www.ithome.com/rss/"))
        runs.append(rss(client, "36kr", "https://36kr.com/feed"))
        runs.append(rss(client, "sspai", "https://sspai.com/feed"))
    OUT.write_text(json.dumps([asdict(r) for r in runs], ensure_ascii=False, indent=1))
    summarize(runs)


def summarize(runs: list[Run]) -> None:
    by_method: dict[str, list[Run]] = {}
    for r in runs:
        by_method.setdefault(r.method, []).append(r)
    print(f"\n{'method':22} {'ok':>5} {'hit':>5} {'med ms':>7}  topics with relevant results")
    rows = []
    for m, rs in by_method.items():
        ok = [r for r in rs if not r.error or r.raw]
        hits = [r for r in rs if r.relevant]
        med = statistics.median(r.ms for r in rs)
        rows.append((len(hits) / len(rs), -med, m, len(ok), len(hits), len(rs), med, ",".join(r.topic for r in hits)))
    for _, _, m, ok, hits, n, med, topics in sorted(rows, reverse=True):
        print(f"{m:22} {ok:>2}/{n:<2} {hits:>2}/{n:<2} {med:7.0f}  {topics}")


if __name__ == "__main__":
    main()
