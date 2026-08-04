from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import re
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import urlparse

INJECTION_PATTERNS = (
    re.compile(
        r"ignore\s+(?:all\s+)?(?:previous|prior|above|system)\s+instructions?", re.I
    ),
    re.compile(r"(?:reveal|print|show|repeat)\s+(?:the\s+)?system\s+prompt", re.I),
    re.compile(r"(?:developer|system)\s*(?:message|prompt)\s*:", re.I),
    re.compile(
        r"(?:override|bypass|disregard)\s+(?:the\s+)?(?:rules|policy|guardrails)", re.I
    ),
    re.compile(r"pretend\s+(?:that\s+)?(?:you|he)\s+(?:are|has|worked)", re.I),
    re.compile(r"say\s+(?:that\s+)?(?:you|he)\s+(?:have|has|worked)", re.I),
    re.compile(r"do\s+not\s+cite|without\s+(?:a\s+)?source", re.I),
    re.compile(r"<\/?(?:system|assistant|developer|tool)[^>]*>", re.I),
)

SENSITIVE_TERMS = re.compile(
    r"\b(?:race|ethnicity|religion|religious|health|diagnosis|sexuality|"
    r"sexual orientation|"
    r"political affiliation|political party|date of birth|age)\b",
    re.I,
)

TAG_RE = re.compile(r"<[^>]+>")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPACE_RE = re.compile(r"\s+")
# Whitespace that is not a line break, so answer text can keep its paragraphs.
HORIZONTAL_SPACE_RE = re.compile(r"[^\S\n]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
NAME_RE = re.compile(r"[^\w\s'.-]", re.UNICODE)


def contains_prompt_injection(value: str) -> bool:
    return any(pattern.search(value) for pattern in INJECTION_PATTERNS)


def contains_sensitive_traits(value: str) -> bool:
    return bool(SENSITIVE_TERMS.search(value or ""))


def sanitize_external_text(value: str, *, max_length: int = 240) -> str:
    """Make fetched content inert, or reject it when it carries commands."""
    clean = html.unescape(CONTROL_RE.sub(" ", TAG_RE.sub(" ", value or "")))
    clean = SPACE_RE.sub(" ", clean).strip()
    if contains_prompt_injection(clean) or contains_sensitive_traits(clean):
        return ""
    return clean[:max_length].rstrip()


def sanitize_answer_text(value: str, *, max_length: int = 240) -> str:
    """Make the twin's own drafted reply inert without flattening its shape.

    `sanitize_external_text` collapses every run of whitespace, which is right
    for a fetched page but wrong for the one piece of model output a visitor
    reads as prose: paragraphs and lists arrived as a single unbroken slab.
    Line structure survives here; tags, control characters, injected
    instructions and sensitive categories are treated exactly as before.
    """
    clean = html.unescape(CONTROL_RE.sub(" ", TAG_RE.sub(" ", value or "")))
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    clean = HORIZONTAL_SPACE_RE.sub(" ", clean)
    clean = "\n".join(line.strip() for line in clean.split("\n"))
    clean = BLANK_LINES_RE.sub("\n\n", clean).strip()
    if contains_prompt_injection(clean) or contains_sensitive_traits(clean):
        return ""
    return clean[:max_length].rstrip()


def normalize_name(value: str) -> str:
    value = html.unescape(value or "")
    value = NAME_RE.sub("", value)
    return SPACE_RE.sub(" ", value).strip()[:80]


def normalized_cache_key(value: str) -> str:
    return normalize_name(value).casefold()


def hash_ip(ip: str, secret: str) -> str:
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def is_public_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname.casefold().rstrip(".")
        if host == "localhost" or host.endswith(".local"):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        )
    except ValueError:
        return False


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        threshold = timestamp - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= threshold:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(timestamp)
            return True


def approximate_tokens(value: str) -> int:
    # A conservative byte-aware estimate caps cost even for languages without spaces.
    return max(1, (len(value.encode("utf-8")) + 2) // 3)
