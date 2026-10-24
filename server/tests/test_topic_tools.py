import httpx
import pytest

from voice_server.tools import StockQuoteTool, TechNewsTool, WeatherTool, route_hint, weather_icon

GEO = {"results": [{"name": "上海", "admin1": "上海市", "latitude": 31.2, "longitude": 121.5}]}
FORECAST = {
    "current": {"temperature_2m": 21.3, "apparent_temperature": 20.1, "relative_humidity_2m": 80, "weather_code": 81,
                "wind_speed_10m": 12.0},
    "daily": {"time": ["2026-10-23", "2026-10-24"], "weather_code": [3, 61], "temperature_2m_max": [22.4, 21.8],
              "temperature_2m_min": [17.2, 18.4], "precipitation_probability_max": [30, 100], "wind_speed_10m_max": [18, 25]},
}


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_weather_formats_current_and_daily_and_caches():
    calls = []

    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(200, json=GEO if "geocoding" in req.url.host else FORECAST)

    tool = WeatherTool("上海", client=client(handler))
    out = tool()
    assert "上海（上海市） 现在阵雨，21°C" in out
    assert "明天（2026-10-24）：小雨，18~22°C，降水概率100%" in out
    tool()
    assert len(calls) == 2  # second call served from cache


def test_weather_unknown_city_and_http_error():
    assert WeatherTool("x", client=client(lambda r: httpx.Response(200, json={}))) ("火星") == "没有找到城市：火星"
    assert WeatherTool("上海", client=client(lambda r: httpx.Response(500)))().startswith("天气查询失败")


QT_US = "v_usNVDA=\"200~英伟达~NVDA.OQ~212.21~221.98~" + "~".join(["0"] * 25) + "~2026-10-22 16:00:01~-9.77~-4.40~\";"


@pytest.mark.parametrize("hint,symbol,unit", [
    ('v_hint="us~nvda.oq~英伟达~ywd~GP^us~nvdy.am~x~y~GP"', "usNVDA", "美元"),
    ('v_hint="sh~600519~贵州茅台~gzmt~GP-A"', "sh600519", "元"),
    ('v_hint="sh~000001~上证指数~szzs~ZS^sh~510210~x~y~ETF"', "sh000001", "点"),
])
def test_stock_resolves_symbol_and_formats(hint, symbol, unit):
    asked = []

    def handler(req):
        if req.url.host == "smartbox.gtimg.cn":
            return httpx.Response(200, text=hint)
        asked.append(str(req.url))
        return httpx.Response(200, content=QT_US.encode("gbk"))

    out = StockQuoteTool(client=client(handler))("英伟达")
    assert asked[0] == f"https://qt.gtimg.cn/q={symbol}"
    assert f"最新价 212.21{unit}" in out and "（-4.40%）" in out and "2026-10-22 16:00:01" in out


def test_stock_rejects_malformed_symbol_from_suggest_api():
    asked = []

    def handler(req):
        if req.url.host == "smartbox.gtimg.cn":
            return httpx.Response(200, text='v_hint="sh~600519,sz000001&x=1~x~y~GP"')
        asked.append(str(req.url))
        return httpx.Response(200, content=QT_US.encode("gbk"))

    assert StockQuoteTool(client=client(handler))("茅台") == "没有找到：茅台"
    assert asked == []


def test_stock_not_found():
    tool = StockQuoteTool(client=client(lambda r: httpx.Response(200, text='v_hint="N";')))
    assert tool("不存在的公司") == "没有找到：不存在的公司"


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>旧新闻</title><pubDate>Thu, 22 Oct 2026 08:00:00 +0800</pubDate></item>
<item><title>新新闻</title><pubDate>Fri, 23 Oct 2026 09:30:00 +0800</pubDate></item>
</channel></rss>"""


def test_news_merges_feeds_newest_first_and_skips_failures():
    def handler(req):
        return httpx.Response(200, text=RSS) if "good" in req.url.host else httpx.Response(503)

    tool = TechNewsTool(client=client(handler), feeds={"好源": "https://good.example/rss", "坏源": "https://bad.example/rss"})
    lines = tool(limit=5).splitlines()
    assert lines[0].endswith("新新闻") and lines[1].endswith("旧新闻") and len(lines) == 2
    assert "[好源 " in lines[0]


def test_stock_formats_a_share_timestamp():
    qt = "v_sh600519=\"1~贵州茅台~600519~1330.23~1318.49~" + "~".join(["0"] * 25) + "~20261023135928~11.74~0.89~\";"
    def handler(req):
        if req.url.host == "smartbox.gtimg.cn":
            return httpx.Response(200, text='v_hint="sh~600519~贵州茅台~gzmt~GP-A"')
        return httpx.Response(200, content=qt.encode("gbk"))
    assert "行情时间 2026-10-23 13:59:28" in StockQuoteTool(client=client(handler))("茅台")


def test_news_times_are_local_not_utc(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    import time
    time.tzset()
    tool = TechNewsTool(client=client(lambda r: httpx.Response(200, text=RSS)), feeds={"源": "https://good.example/rss"})
    assert "[源 10-23 09:30] 新新闻" in tool(limit=1)


ALL = {"get_weather", "get_stock_quote", "get_tech_news", "web_search"}


@pytest.mark.parametrize("q,tool", [
    ("北京今天冷不冷？", "get_weather"), ("明天要带伞吗", "get_weather"),
    ("英伟达昨天收盘涨了还是跌了？", "get_stock_quote"), ("上证指数今天怎么样", "get_stock_quote"),
    ("茅台现在多少钱一股？", "get_stock_quote"), ("比特币现在多少钱", "get_stock_quote"),
    ("今天有什么科技新闻", "get_tech_news"), ("最近有什么新闻", "get_tech_news"),
    ("讲个笑话", None), ("一加一等于几", None),
])
def test_route_hint(q, tool):
    hint = route_hint(q, ALL)
    assert (hint is None) if tool is None else (tool in hint)


def test_route_hint_ignores_unavailable_tools():
    assert route_hint("明天下雨吗", {"web_search"}) is None


def test_weather_snapshot_for_clock_card():
    def handler(req):
        return httpx.Response(200, json=GEO if "geocoding" in req.url.host else {**FORECAST, "current": {**FORECAST["current"], "is_day": 1}})
    snap = WeatherTool("上海", client=client(handler)).snapshot()
    assert snap == {"city": "上海", "temp": 21, "code": 81, "text": "阵雨", "icon": "rain", "min": 17, "max": 22}


@pytest.mark.parametrize("code,day,icon", [(0, True, "sun"), (0, False, "moon"), (2, True, "partly"), (3, True, "cloud"),
                                           (45, True, "fog"), (63, True, "rain"), (73, True, "snow"), (95, True, "thunder")])
def test_weather_icon(code, day, icon):
    assert weather_icon(code, day) == icon
