"""Small, dependency-free text utilities used for clustering, dedupe and fact matching."""

from __future__ import annotations

import re
from urllib.parse import urlparse

STOPWORDS = set(
    """a an the and or but if then so of in on at to for from by with about as is are was were be been
    being it its this that these those there here what which who whom how why when where i you he she we
    they them his her our your their my me us do does did done have has had not no yes can could would
    should will just more most very really new into out up down over under again also than too only own
    same some such any all both each few other per via vs versus get got gets one two three five six""".split()
)

# Phrases that make an item relevant to this channel.
GTA6_TERMS = [
    "gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6",
    "leonida", "vice city", "lucia", "jason", "rockstar",
]

_TOKEN = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"grand theft auto\s+(vi|6)", "gta 6", text)
    text = re.sub(r"\bgta\s*(vi|6)\b", "gta 6", text)
    text = re.sub(r"\bgta\s*(v|5)\b", "gta 5", text)
    return text


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(normalize(text)) if t not in STOPWORDS and len(t) > 1]


def keyset(text: str) -> set[str]:
    return set(tokens(text)) - {"gta", "6", "rockstar", "games", "game"}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def fingerprint(text: str) -> str:
    return " ".join(sorted(keyset(text)))


def relevance(text: str) -> float:
    norm = normalize(text)
    hits = sum(1 for term in GTA6_TERMS if term in norm)
    if "gta 6" in norm:
        return min(1.0, 0.7 + 0.1 * hits)
    return min(1.0, 0.25 * hits)


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_matches(domain: str, allowed: list[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in allowed)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def word_count(text: str) -> int:
    return len(text.split())
