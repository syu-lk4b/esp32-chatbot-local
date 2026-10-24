import json

import httpx
import pytest

from voice_server.config import Settings
from voice_server.engines import MAX_TOOL_ROUNDS, STALL_NUDGE, Llm
from voice_server.tools import WEB_SEARCH_SPEC, WebSearch, format_results, query_terms, rank_results


BACKENDS = ["openai", "ollama"]


def llm_client(backend: str, replies: list[dict], seen: list[dict]) -> httpx.Client:
    """Fake LLM server returning the scripted assistant messages in the backend's envelope."""
    it = iter(replies)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        msg = next(it)
        if backend == "openai":
            assert req.url.path.endswith("/chat/completions")
            return httpx.Response(200, json={"choices": [{"message": msg}]})
        assert req.url.path == "/api/chat"
        return httpx.Response(200, json={"message": msg})

    return httpx.Client(base_url="http://llm", transport=httpx.MockTransport(handler))


def make_llm(backend, replies, seen, tools):
    return Llm(Settings(llm_backend=backend), tools=tools, client=llm_client(backend, replies, seen))


@pytest.mark.parametrize("backend", BACKENDS)
def test_tool_call_then_answer(backend):
    seen: list[dict] = []
    replies = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": {"query": "上海天气"}}}]},
        {"role": "assistant", "content": "上海今天多云，二十一度。"},
    ]
    queries: list[str] = []
    tools = {"web_search": (WEB_SEARCH_SPEC, lambda query: queries.append(query) or "1. 上海 多云 21℃")}
    llm = make_llm(backend, replies, seen, tools)

    reply, used = llm.chat([], "上海天气怎么样")

    assert reply == "上海今天多云，二十一度。"
    assert used == ["web_search"] and queries == ["上海天气"]
    assert "tools" in seen[0]
    tool_msg = seen[1]["messages"][-1]
    if backend == "openai":
        assert tool_msg == {"role": "tool", "tool_call_id": "call_1", "content": "1. 上海 多云 21℃"}
        assert "max_tokens" in seen[0] and "options" not in seen[0]
    else:
        assert tool_msg == {"role": "tool", "tool_name": "web_search", "content": "1. 上海 多云 21℃"}
        assert "options" in seen[0]


@pytest.mark.parametrize("backend", BACKENDS)
def test_tool_rounds_are_capped_and_last_round_has_no_tools(backend):
    seen: list[dict] = []
    loop_call = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query": "x"}'}}]}
    replies = [loop_call] * MAX_TOOL_ROUNDS + [{"role": "assistant", "content": "搜不到"}]
    tools = {"web_search": (WEB_SEARCH_SPEC, lambda query: "没有搜到相关结果")}
    llm = make_llm(backend, replies, seen, tools)

    reply, used = llm.chat([], "?")

    assert reply == "搜不到" and len(used) == MAX_TOOL_ROUNDS
    assert "tools" not in seen[-1]


@pytest.mark.parametrize("backend", BACKENDS)
def test_unknown_tool_and_bad_args_are_reported_to_model(backend):
    seen: list[dict] = []
    replies = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "nope", "arguments": {}}},
            {"function": {"name": "web_search", "arguments": {"q": "wrong key"}}},
        ]},
        {"role": "assistant", "content": "好的"},
    ]
    tools = {"web_search": (WEB_SEARCH_SPEC, lambda query: "ok")}
    make_llm(backend, replies, seen, tools).chat([], "?")
    tool_msgs = [m["content"] for m in seen[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0].startswith("未知工具") and tool_msgs[1].startswith("工具参数错误")


def test_web_search_formats_and_handles_errors():
    def ok(req: httpx.Request) -> httpx.Response:
        assert req.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [
            {"title": "q 标题", "content": "摘要  内容", "url": "https://example.com/a", "publishedDate": "2026-10-22T08:00:00"},
        ]})

    search = WebSearch("http://s", client=httpx.Client(base_url="http://s", transport=httpx.MockTransport(ok)))
    assert search("q") == "1. q 标题（example.com 2026-10-22）摘要 内容"
    assert search("  ") == "搜索失败：关键词为空"

    down = WebSearch("http://s", client=httpx.Client(base_url="http://s", transport=httpx.MockTransport(lambda r: httpx.Response(502))))
    assert down("q").startswith("搜索失败")
    assert format_results([]) == "没有搜到相关结果"


def test_query_terms_mix_cjk_and_ascii():
    assert query_terms("NVDA 股价") == {"nvda", "股价"}
    assert query_terms("雨") == {"雨"}


def test_rank_results_drops_unrelated_pages():
    results = [
        {"title": "2026 - Wikipedia", "content": "2026 is the current year"},
        {"title": "2026年诺贝尔物理学奖授予弗朗西斯·哈尔岑", "content": "中微子天文台"},
        {"title": "2026 Calendar - United States", "content": "holidays"},
        {"title": "诺贝尔奖历史", "content": "物理学奖 获奖者 名单"},
    ]
    ranked = rank_results("2026年诺贝尔物理学奖 获奖者", results)
    titles = [r["title"] for r in ranked]
    assert "2026 - Wikipedia" not in titles and "2026 Calendar - United States" not in titles
    assert titles[0].startswith("2026年诺贝尔物理学奖")


def test_inline_think_block_is_stripped():
    seen: list[dict] = []
    replies = [{"role": "assistant", "content": "<think>想一想</think>\n[happy]你好呀"}]
    reply, _ = make_llm("openai", replies, seen, {}).chat([], "hi")
    assert reply == "[happy]你好呀"


def test_reasoning_effort_sent_only_when_configured():
    seen: list[dict] = []
    reply = [{"role": "assistant", "content": "[calm]好"}]
    Llm(Settings(llm_backend="openai", llm_reasoning_effort="none"), client=llm_client("openai", reply, seen)).chat([], "?")
    assert seen[0]["reasoning_effort"] == "none"
    seen.clear()
    Llm(Settings(llm_backend="openai", llm_reasoning_effort=""), client=llm_client("openai", list(reply), seen)).chat([], "?")
    assert "reasoning_effort" not in seen[0]


def test_final_round_nudges_and_strips_text_tool_calls():
    seen: list[dict] = []
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "x"}'}}]}
    leaked = {"role": "assistant", "content": "<tool_call>\n<function=web_search>\n<parameter=query>NVDA"}
    tools = {"web_search": (WEB_SEARCH_SPEC, lambda query: "1. 结果")}
    reply, used = make_llm("openai", [call] * MAX_TOOL_ROUNDS + [leaked], seen, tools).chat([], "?")
    assert "tools" not in seen[-1]
    assert seen[-1]["messages"][-1]["role"] == "system" and "不要再调用工具" in seen[-1]["messages"][-1]["content"]
    assert "<tool_call>" not in reply and reply.endswith("我没查到确切的信息。")


@pytest.mark.parametrize("stall", ["我查一下", "[calm]我帮你搜一下。", "稍等，我看看", "[happy]好，我来查查。"])
def test_stalling_reply_is_nudged_into_a_tool_call(stall):
    seen: list[dict] = []
    replies = [
        {"role": "assistant", "content": stall},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "上海 天气"}'}}]},
        {"role": "assistant", "content": "[calm]明天有小雨。"},
    ]
    tools = {"web_search": (WEB_SEARCH_SPEC, lambda query: "1. 小雨")}
    reply, used = make_llm("openai", replies, seen, tools).chat([], "明天下雨吗")
    assert reply == "[calm]明天有小雨。" and used == ["web_search"]
    assert any(m.get("content") == STALL_NUDGE for m in seen[1]["messages"])


def test_normal_short_answer_is_not_treated_as_stall():
    seen: list[dict] = []
    reply, used = make_llm("openai", [{"role": "assistant", "content": "[happy]一加一等于二"}], seen,
                           {"web_search": (WEB_SEARCH_SPEC, lambda query: "")}).chat([], "1+1")
    assert reply == "[happy]一加一等于二" and len(seen) == 1


def test_routed_question_answered_from_memory_is_nudged():
    seen: list[dict] = []
    replies = [
        {"role": "assistant", "content": "[happy]北京今天挺舒服的"},  # confident but unverified
        {"role": "assistant", "content": "", "tool_calls": [{"id": "w", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}}]},
        {"role": "assistant", "content": "[calm]北京今天 8 度，挺冷的。"},
    ]
    tools = {"get_weather": ({"type": "function", "function": {"name": "get_weather"}}, lambda city="", days=3: "北京 8°C")}
    reply, used = make_llm("openai", replies, seen, tools).chat([], "北京今天冷不冷？")
    assert used == ["get_weather"] and reply == "[calm]北京今天 8 度，挺冷的。"
    assert any("get_weather" in (m.get("content") or "") for m in seen[0]["messages"] if m["role"] == "system")


def test_prompt_prefix_is_stable_and_time_goes_last():
    seen: list[dict] = []
    replies = [{"role": "assistant", "content": "[calm]好"}, {"role": "assistant", "content": "[calm]好"}]
    llm = make_llm("openai", replies, seen, {"web_search": (WEB_SEARCH_SPEC, lambda query: "")})
    llm.chat([], "你好")
    llm.chat([], "在吗")
    first, second = seen[0]["messages"], seen[1]["messages"]
    assert first[0] == second[0] and "现在是" not in first[0]["content"]  # cacheable prefix
    assert first[-2]["role"] == "system" and first[-2]["content"].startswith("现在是")
    assert seen[0]["tools"] == seen[1]["tools"]


def test_bare_emotion_tag_counts_as_no_answer():
    seen: list[dict] = []
    replies = [{"role": "assistant", "content": "[calm]"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "s", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "诺贝尔"}'}}]},
               {"role": "assistant", "content": "[calm]哈尔岑。"}]
    reply, used = make_llm("openai", replies, seen, {"web_search": (WEB_SEARCH_SPEC, lambda query: "1. 哈尔岑")}).chat([], "今年诺贝尔物理学奖颁给了谁")
    assert reply == "[calm]哈尔岑。" and used == ["web_search"]
