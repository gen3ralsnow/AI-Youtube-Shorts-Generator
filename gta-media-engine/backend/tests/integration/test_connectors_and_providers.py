"""Research connectors and AI providers against mocked HTTP (no live credentials)."""

import base64

import httpx
import pytest

from engine.agents.base import AgentContext
from engine.agents.research import ResearchAgent, TrendAnalystAgent
from engine.models import CostEntry, SourceItem, Topic
from engine.providers import llm as llm_mod
from engine.providers.http import NotConfigured, ProviderError
from engine.providers.tts import ElevenLabsTTS, OpenAITTS, words_from_char_alignment
from engine.research.connectors import RedditConnector, RSSConnector, YouTubeConnector
from tests.conftest import mock_http

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>IGN</title>
<item><title>GTA 6 trailer 2 shows Vice City beach in Leonida</title><link>https://www.ign.com/articles/gta-6-beach</link>
<guid>ign-1</guid><description>The new GTA 6 trailer shows Vice City beach crowds and Lucia driving.</description>
<pubDate>Mon, 10 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Minecraft snapshot adds frogs</title><link>https://www.ign.com/articles/mc</link><guid>ign-2</guid></item>
</channel></rss>"""


def test_rss_connector_respects_robots_and_parses(session):
    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        if req.url.path == "/private/feed":
            raise AssertionError("must not fetch a robots-disallowed URL")
        return httpx.Response(200, content=RSS)

    conn = RSSConnector(["https://feeds.example.com/feed", "https://feeds.example.com/private/feed"],
                        client=mock_http(handler))
    out = ResearchAgent([conn]).run(AgentContext(session), {})
    assert out.fetched == 2 and out.new_items == 1 and out.irrelevant_skipped == 1
    src = session.query(SourceItem).one()
    assert src.credibility == "credible" and src.domain == "ign.com"
    # second run updates, does not duplicate
    out2 = ResearchAgent([conn]).run(AgentContext(session), {})
    assert out2.new_items == 0 and session.query(SourceItem).count() == 1


def test_youtube_connector_tracks_quota(session, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    from engine.config import reset_settings_cache

    reset_settings_cache()

    def handler(req: httpx.Request):
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "abc"}}]})
        return httpx.Response(200, json={"items": [{"id": "abc", "snippet": {
            "title": "GTA 6 map details everyone missed", "description": "Leonida map breakdown",
            "channelTitle": "Some Creator", "channelId": "UC1", "publishedAt": "2026-11-09T10:00:00Z"},
            "statistics": {"viewCount": "120000", "likeCount": "5000", "commentCount": "800"}}]})

    items = YouTubeConnector(client=mock_http(handler)).fetch(session, "GTA 6")
    assert items[0].engagement == {"views": 120000, "likes": 5000, "comments": 800}
    used = sum(c.units for c in session.query(CostEntry).filter_by(provider="youtube_data_api"))
    assert used == 101


def test_reddit_connector_oauth_and_filtering(session, monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_SUBREDDITS", "GTA6")
    from engine.config import reset_settings_cache

    reset_settings_cache()
    monkeypatch.setattr("engine.research.connectors.time.sleep", lambda *_: None)

    def handler(req: httpx.Request):
        if "access_token" in req.url.path:
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer t"
        return httpx.Response(200, json={"data": {"children": [
            {"data": {"name": "t3_1", "title": "Theory: Lucia is undercover in GTA 6", "permalink": "/r/GTA6/1",
                      "score": 4200, "num_comments": 900, "created_utc": 1762768800}},
            {"data": {"name": "t3_2", "title": "Mod post", "stickied": True, "permalink": "/r/GTA6/2"}},
        ]}})

    conn = RedditConnector(client=mock_http(handler))
    conn._token = None
    items = conn.fetch(session)
    assert [i.external_id for i in items] == ["t3_1"]


def test_unconfigured_connectors_are_reported_not_faked(session):
    out = ResearchAgent().run(AgentContext(session), {})
    assert set(out.unconfigured) == {"youtube", "reddit", "rss"} and out.fetched == 0
    with pytest.raises(NotConfigured):
        YouTubeConnector(api_key="").fetch(session)


def test_trend_analyst_creates_scored_topics_and_flags_duplicates(session):
    for i, title in enumerate(["GTA 6 trailer shows Vice City beach", "Vice City beach spotted in GTA 6 trailer",
                               "GTA 6 Lucia theory explained"]):
        session.add(SourceItem(connector="rss", external_id=str(i), url=f"https://www.ign.com/{i}", domain="ign.com",
                               title=title, summary=title + " in Leonida.", credibility="credible",
                               engagement={"views": 10000 * (i + 1)}))
    session.flush()
    out = TrendAnalystAgent().run(AgentContext(session), {})
    assert out.clusters == 2 and out.topics_created == 2
    beach = next(t for t in session.query(Topic) if "beach" in t.title.lower())
    assert len(beach.sources) == 2 and 0 <= beach.score <= 100
    assert "search_demand" in beach.score_breakdown["missing_signals"]
    # A near-identical new story becomes a duplicate, not a new topic.
    session.add(SourceItem(connector="rss", external_id="9", url="https://www.ign.com/9", domain="ign.com",
                           title="GTA 6 trailer Vice City beach", summary="", credibility="credible"))
    session.flush()
    out2 = TrendAnalystAgent().run(AgentContext(session), {})
    assert out2.duplicates == 1 and out2.topics_created == 0


# ------------------------------------------------------------------ AI providers


def test_anthropic_and_openai_parsing(monkeypatch, session):
    calls = []

    class Resp:
        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    def fake_request(method, url, **kw):
        calls.append((url, kw["json"]))
        if "anthropic" in url:
            return Resp({"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": '```json\n{"ok": 1}\n```'}],
                         "usage": {"input_tokens": 100, "output_tokens": 20}})
        return Resp({"model": "gpt", "choices": [{"message": {"content": '{"ok": 2}'}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    monkeypatch.setattr(llm_mod, "request", fake_request)
    a = llm_mod.AnthropicLLM("k", "claude-sonnet-5-5").complete_json("sys", "prompt")
    assert a.data == {"ok": 1} and a.input_tokens == 100
    o = llm_mod.OpenAILLM("k", "gpt").complete_json("sys", "prompt")
    assert o.data == {"ok": 2} and calls[1][1]["response_format"] == {"type": "json_object"}
    res = llm_mod.metered_complete(session, llm_mod.AnthropicLLM("k", "m"), "s", "p", max_tokens=100, job_id=None)
    entry = session.query(CostEntry).filter_by(category="language_model").one()
    assert entry.estimated_usd > 0 and entry.actual_usd is not None and res.data == {"ok": 1}


def test_llm_json_errors_are_provider_errors():
    with pytest.raises(ProviderError):
        llm_mod.parse_json("no json here")
    assert llm_mod.parse_json('Sure! {"a": [1, 2]} hope that helps') == {"a": [1, 2]}


def test_tts_providers_request_shape(monkeypatch, tmp_path):
    seen = {}

    class Resp:
        content = b"ID3fake"

        def json(self):
            return {"audio_base64": base64.b64encode(b"ID3fake").decode(),
                    "alignment": {"characters": list("Hi you"), "character_start_times_seconds": [0, .1, .2, .3, .4, .5],
                                  "character_end_times_seconds": [.1, .2, .3, .4, .5, .6]}}

    def fake_request(method, url, **kw):
        seen[url] = kw
        return Resp()

    monkeypatch.setattr("engine.providers.tts.request", fake_request)
    with pytest.raises(NotConfigured):
        ElevenLabsTTS("key", "m").synthesize("x", {}, tmp_path / "a.mp3")  # needs a voice id
    r = ElevenLabsTTS("key", "m").synthesize("Hi you", {"provider_voice_id": "v1", "speed": 1.1}, tmp_path / "a.mp3")
    assert [w["word"] for w in r.word_timings] == ["Hi", "you"] and not r.placeholder
    body = next(v for k, v in seen.items() if "elevenlabs" in k)["json"]
    assert body["voice_settings"]["speed"] == 1.1
    OpenAITTS("key", "gpt-4o-mini-tts").synthesize("Hello", {"speed": 1.0}, tmp_path / "b.mp3")
    assert (tmp_path / "b.mp3").read_bytes() == b"ID3fake"
    assert words_from_char_alignment({}) == []
