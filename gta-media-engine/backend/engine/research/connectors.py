"""Research source connectors.

Only official APIs and syndication feeds are used: YouTube Data API v3, Reddit's
OAuth API, RSS/Atom feeds (robots.txt honoured), and a trends provider (SerpApi's
Google Trends endpoint). No HTML scraping and no private account data.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib import robotparser
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import costs
from ..config import get_settings
from ..logs import get_logger
from ..models import CostEntry
from ..providers.http import NotConfigured, ProviderError, request
from .text import domain_of

log = get_logger("research.connectors")

YOUTUBE_DAILY_QUOTA = 10_000  # default project quota; raise via Google if granted more


@dataclass
class RawItem:
    connector: str
    external_id: str
    url: str
    title: str
    summary: str = ""
    author: str = ""
    published_at: datetime | None = None
    engagement: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return domain_of(self.url)


class Connector:
    name = "base"

    def configured(self) -> bool:
        return False

    def missing(self) -> list[str]:
        return []

    def fetch(self, session: Session, query: str) -> list[RawItem]:
        raise NotImplementedError


class YouTubeConnector(Connector):
    name = "youtube"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key if api_key is not None else get_settings().youtube_api_key
        self.client = client

    def configured(self) -> bool:
        return bool(self.api_key)

    def missing(self) -> list[str]:
        return [] if self.api_key else ["YOUTUBE_API_KEY"]

    def _quota_used_today(self, session: Session) -> float:
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return float(
            session.scalar(
                select(func.coalesce(func.sum(CostEntry.units), 0)).where(
                    CostEntry.provider == "youtube_data_api", CostEntry.created_at >= day
                )
            )
            or 0
        )

    def _spend_quota(self, session: Session, units: int, note: str) -> None:
        if self._quota_used_today(session) + units > YOUTUBE_DAILY_QUOTA * 0.8:
            # keep 20% headroom for uploads (videos.insert costs 1,600 units) and analytics
            raise ProviderError("YouTube research quota budget for today reached", transient=True)
        costs.record(
            session, category="api_quota", provider="youtube_data_api", units=units,
            unit="quota_units", estimated_usd=0.0, actual_usd=0.0, note=note,
        )

    def fetch(self, session: Session, query: str = "GTA 6") -> list[RawItem]:
        if not self.api_key:
            raise NotConfigured("YOUTUBE_API_KEY is not set")
        after = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._spend_quota(session, 100, "search.list")
        search = request(
            "GET",
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet", "q": query, "type": "video", "order": "viewCount",
                "publishedAfter": after, "maxResults": 25, "key": self.api_key,
                "relevanceLanguage": "en",
            },
            client=self.client,
        ).json()
        ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            return []
        self._spend_quota(session, 1, "videos.list")
        videos = request(
            "GET",
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,statistics", "id": ",".join(ids), "key": self.api_key},
            client=self.client,
        ).json()
        items = []
        for v in videos.get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            items.append(
                RawItem(
                    connector=self.name,
                    external_id=v["id"],
                    url=f"https://www.youtube.com/watch?v={v['id']}",
                    title=sn.get("title", ""),
                    summary=(sn.get("description") or "")[:1500],
                    author=sn.get("channelTitle", ""),
                    published_at=_parse_dt(sn.get("publishedAt")),
                    engagement={
                        "views": _int(st.get("viewCount")),
                        "likes": _int(st.get("likeCount")),
                        "comments": _int(st.get("commentCount")),
                    },
                    raw={"channel_id": sn.get("channelId"), "tags": sn.get("tags", [])[:20]},
                )
            )
        return items

    def count_recent_videos(self, session: Session, query: str) -> int | None:
        """Saturation signal: how many videos on this topic in the last 7 days (capped sample)."""
        if not self.api_key:
            return None
        after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._spend_quota(session, 100, "search.list saturation")
        body = request(
            "GET",
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "id", "q": query, "type": "video", "publishedAfter": after,
                    "maxResults": 50, "key": self.api_key},
            client=self.client,
        ).json()
        return int(body.get("pageInfo", {}).get("totalResults", 0))


class RedditConnector(Connector):
    name = "reddit"
    _token: tuple[str, float] | None = None

    def __init__(self, client: httpx.Client | None = None):
        s = get_settings()
        self.client_id, self.client_secret = s.reddit_client_id, s.reddit_client_secret
        self.user_agent, self.subreddits = s.reddit_user_agent, s.reddit_subreddits
        self.client = client

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def missing(self) -> list[str]:
        return [k for k, v in (("REDDIT_CLIENT_ID", self.client_id),
                               ("REDDIT_CLIENT_SECRET", self.client_secret)) if not v]

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        body = request(
            "POST",
            "https://www.reddit.com/api/v1/access_token",
            headers={"Authorization": f"Basic {basic}", "User-Agent": self.user_agent},
            data={"grant_type": "client_credentials"},
            client=self.client,
        ).json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._token[0]

    def fetch(self, session: Session, query: str = "GTA 6") -> list[RawItem]:
        if not self.configured():
            raise NotConfigured("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not set")
        token = self._access_token()
        items: list[RawItem] = []
        for sub in self.subreddits:
            body = request(
                "GET",
                f"https://oauth.reddit.com/r/{sub}/hot",
                params={"limit": 25, "raw_json": 1},
                headers={"Authorization": f"Bearer {token}", "User-Agent": self.user_agent},
                client=self.client,
            ).json()
            for child in body.get("data", {}).get("children", []):
                d = child.get("data", {})
                if d.get("stickied") or d.get("over_18"):
                    continue
                items.append(
                    RawItem(
                        connector=self.name,
                        external_id=d.get("name") or d.get("id", ""),
                        url="https://www.reddit.com" + d.get("permalink", ""),
                        title=d.get("title", ""),
                        summary=(d.get("selftext") or "")[:1500],
                        author=f"r/{sub}",
                        published_at=datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc),
                        engagement={
                            "score": _int(d.get("score")),
                            "comments": _int(d.get("num_comments")),
                            "upvote_ratio": d.get("upvote_ratio"),
                        },
                        raw={"flair": d.get("link_flair_text"), "linked_url": d.get("url")},
                    )
                )
            time.sleep(0.7)  # stay far below Reddit's 100 QPM OAuth limit
        return items


class RSSConnector(Connector):
    name = "rss"

    def __init__(self, feeds: list[str] | None = None, client: httpx.Client | None = None):
        self.feeds = feeds if feeds is not None else get_settings().rss_feeds
        self.client = client
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    def configured(self) -> bool:
        return bool(self.feeds)

    def missing(self) -> list[str]:
        return [] if self.feeds else ["RSS_FEEDS"]

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = request("GET", base + "/robots.txt", retries=0, timeout=10, client=self.client)
                rp.parse(resp.text.splitlines())
                self._robots[base] = rp
            except ProviderError:
                self._robots[base] = None  # no robots.txt -> allowed
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(get_settings().reddit_user_agent, url)

    def fetch(self, session: Session, query: str = "") -> list[RawItem]:
        import feedparser

        if not self.feeds:
            raise NotConfigured("RSS_FEEDS is empty")
        items: list[RawItem] = []
        for feed_url in self.feeds:
            if not self._allowed(feed_url):
                log.warning("robots.txt disallows %s; skipped", feed_url)
                continue
            try:
                resp = request("GET", feed_url, timeout=20, client=self.client)
            except ProviderError as exc:
                log.warning("feed %s failed: %s", feed_url, exc)
                continue
            parsed = feedparser.parse(resp.content)
            for e in parsed.entries[:40]:
                link = e.get("link", "")
                published = None
                if e.get("published_parsed"):
                    published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                items.append(
                    RawItem(
                        connector=self.name,
                        external_id=e.get("id") or link,
                        url=link,
                        title=e.get("title", ""),
                        summary=_strip_html(e.get("summary", ""))[:1500],
                        author=parsed.feed.get("title", domain_of(feed_url)),
                        published_at=published,
                    )
                )
        return items


class TrendsProvider:
    """Search-demand signal. Implementation: SerpApi Google Trends (paid, credentials required)."""

    name = "serpapi_google_trends"

    def __init__(self, client: httpx.Client | None = None):
        self.api_key = get_settings().serpapi_api_key
        self.client = client

    def configured(self) -> bool:
        return bool(self.api_key)

    def missing(self) -> list[str]:
        return [] if self.api_key else ["SERPAPI_API_KEY"]

    def interest(self, session: Session, query: str) -> float | None:
        """Mean interest (0-100) over the last 7 days, or None when unavailable."""
        if not self.api_key:
            return None
        est = get_settings().price_serpapi_per_call
        costs.check_budget(session, est)
        body = request(
            "GET",
            "https://serpapi.com/search.json",
            params={"engine": "google_trends", "q": query[:100], "date": "now 7-d",
                    "data_type": "TIMESERIES", "api_key": self.api_key},
            client=self.client,
        ).json()
        costs.record(session, category="api", provider=self.name, units=1, unit="call",
                     estimated_usd=est, actual_usd=est, note="trends")
        values = []
        for point in body.get("interest_over_time", {}).get("timeline_data", []):
            for v in point.get("values", []):
                if v.get("extracted_value") is not None:
                    values.append(float(v["extracted_value"]))
        return sum(values) / len(values) if values else None


def all_connectors() -> list[Connector]:
    return [YouTubeConnector(), RedditConnector(), RSSConnector()]


def connector_status() -> list[dict]:
    rows = [{"name": c.name, "configured": c.configured(), "missing": c.missing()} for c in all_connectors()]
    t = TrendsProvider()
    rows.append({"name": t.name, "configured": t.configured(), "missing": t.missing()})
    return rows


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _strip_html(text: str) -> str:
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()
