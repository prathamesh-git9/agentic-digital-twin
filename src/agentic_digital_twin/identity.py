from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

NICKNAME_GROUPS: tuple[tuple[str, ...], ...] = (
    ("alexander", "alex", "sasha"),
    ("andrew", "andy", "drew"),
    ("anthony", "tony"),
    ("benjamin", "ben", "benny"),
    ("catherine", "cathy", "kate", "katie"),
    ("charles", "charlie", "chuck"),
    ("christopher", "chris"),
    ("daniel", "dan", "danny"),
    ("david", "dave"),
    ("elizabeth", "beth", "betsy", "liz", "lizzy"),
    ("james", "jim", "jimmy"),
    ("jennifer", "jen", "jenny"),
    ("joseph", "joe", "joey"),
    ("margaret", "maggie", "meg", "peggy"),
    ("matthew", "matt"),
    ("michael", "mike", "mikey"),
    ("nicholas", "nick", "nicky"),
    ("patricia", "pat", "trish"),
    ("rebecca", "becky", "becca"),
    ("richard", "rich", "rick", "dick"),
    ("robert", "rob", "bob", "bobby"),
    ("samuel", "sam", "sammy"),
    ("stephen", "steven", "steve"),
    ("thomas", "tom", "tommy"),
    ("william", "will", "bill", "billy"),
)

_NICKNAMES = {nickname: group for group in NICKNAME_GROUPS for nickname in group}
_NAME_WORD = re.compile(r"[^\W\d_]+(?:[\-'’][^\W\d_]+)*\.?", re.UNICODE)
_TITLE_BREAK = re.compile(r"\s+(?:[-–—|·•])\s+", re.UNICODE)
_CONTEXT_BREAK = re.compile(
    r"\s+\b(?:at|from|conference|speaker|speaks|talk|keynote|podcast|profile|"
    r"linkedin|github|cfp)\b.*$",
    re.IGNORECASE,
)
_PARTICLES = {
    "al",
    "bin",
    "da",
    "de",
    "del",
    "della",
    "der",
    "di",
    "dos",
    "du",
    "la",
    "le",
    "st",
    "van",
    "von",
}


@dataclass(frozen=True, slots=True)
class PublicNameEvidence:
    name: str
    source_url: str
    source_kind: str = "search_result"
    confidence: int = 70


@dataclass(frozen=True, slots=True)
class ResolvedPublicName:
    display_name: str
    surname_resolved: bool
    source_url: str | None
    source_kind: str
    why: str
    variants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NameParts:
    display_name: str
    first: str
    middle: tuple[str, ...]
    last: str
    surname_variants: tuple[str, ...]
    family_first: bool

    @property
    def surname_resolved(self) -> bool:
        return bool(self.last)


def local_token(value: str) -> str:
    """Return an ASCII local-part token while leaving display names untouched."""

    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]", "", ascii_value.casefold())


def nickname_variants(value: str) -> tuple[str, ...]:
    normalized = local_token(value)
    group = _NICKNAMES.get(normalized, (normalized,))
    return tuple(dict.fromkeys((normalized, *group))) if normalized else ()


def token_equivalent(left: str, right: str) -> bool:
    left_value, right_value = local_token(left), local_token(right)
    if not left_value or not right_value:
        return False
    if left_value == right_value:
        return True
    if len(left_value) == 1 or len(right_value) == 1:
        return left_value[0] == right_value[0]
    if right_value in _NICKNAMES.get(left_value, ()):
        return True
    return SequenceMatcher(None, left_value, right_value).ratio() >= 0.78


def name_tokens(value: str) -> tuple[str, ...]:
    return tuple(match.group(0).rstrip(".") for match in _NAME_WORD.finditer(value))


def names_compatible(submitted: str, observed: str) -> bool:
    submitted_tokens = name_tokens(submitted)
    observed_tokens = name_tokens(observed)
    if not submitted_tokens or not observed_tokens:
        return False
    matched = sum(
        any(token_equivalent(token, candidate) for candidate in observed_tokens)
        for token in submitted_tokens
    )
    required = 1 if len(submitted_tokens) == 1 else max(1, len(submitted_tokens) - 1)
    return matched >= required


def extract_public_name(submitted_name: str, title: str) -> str | None:
    """Extract a plausible display name from a public-result/page title."""

    value = re.sub(r"<[^>]+>", " ", title)
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n|·•–—-")
    if not value:
        return None
    value = _TITLE_BREAK.split(value, maxsplit=1)[0].strip()
    value = re.sub(
        r"^(?:meet|about|profile\s+(?:of|for)|speaker)\s*[:\-]?\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = _CONTEXT_BREAK.sub("", value).strip(" ,:;|·•–—-")
    value = re.sub(r"\s*\([^)]{1,40}\)\s*$", "", value).strip()
    tokens = name_tokens(value)
    if not 1 <= len(tokens) <= 6 or not names_compatible(submitted_name, value):
        return None
    # Preserve punctuation, accents, and public name order in the display value.
    return value[:100]


def parse_name_parts(display_name: str) -> NameParts:
    cleaned = re.sub(r"\s+", " ", display_name).strip()
    family_first = "," in cleaned
    if family_first:
        family_text, given_text = (part.strip() for part in cleaned.split(",", 1))
        family_tokens = name_tokens(family_text)
        given_tokens = name_tokens(given_text)
        first = local_token(given_tokens[0]) if given_tokens else ""
        middle = tuple(local_token(value) for value in given_tokens[1:])
        last_values = tuple(local_token(value) for value in family_tokens)
    else:
        tokens = name_tokens(cleaned)
        first = local_token(tokens[0]) if tokens else ""
        middle = tuple(local_token(value) for value in tokens[1:-1])
        last_values = (local_token(tokens[-1]),) if len(tokens) > 1 else ()

    last_values = tuple(value for value in last_values if value)
    last = "".join(last_values)
    surname_variants: list[str] = []
    if last:
        surname_variants.append(last)
    if not family_first:
        tokens = [local_token(value) for value in name_tokens(cleaned)]
        tokens = [value for value in tokens if value]
        if len(tokens) >= 3:
            penultimate = tokens[-2]
            compound = f"{penultimate}{tokens[-1]}"
            # A particle is almost certainly part of the surname. A second surname is
            # still useful as a lower-ranked alternative for double-surname cultures.
            if penultimate in _PARTICLES:
                surname_variants.insert(0, compound)
                last = compound
                middle = tuple(tokens[1:-2])
            elif compound not in surname_variants:
                surname_variants.append(compound)
    return NameParts(
        display_name=display_name,
        first=first,
        middle=tuple(value for value in middle if value),
        last=last,
        surname_variants=tuple(dict.fromkeys(surname_variants)),
        family_first=family_first,
    )


def display_name_variants(display_name: str) -> tuple[str, ...]:
    parts = parse_name_parts(display_name)
    raw_tokens = name_tokens(display_name)
    if not raw_tokens:
        return ()
    if parts.family_first:
        family, given = (part.strip() for part in display_name.split(",", 1))
        given_tokens = name_tokens(given)
        first_display = given_tokens[0] if given_tokens else ""
        suffix = " ".join(given_tokens[1:])
        values = [display_name]
        for nickname in nickname_variants(first_display):
            given_value = " ".join(value for value in (nickname.title(), suffix) if value)
            values.append(f"{family}, {given_value}")
            values.append(f"{given_value} {family}")
        return tuple(dict.fromkeys(values))

    first_display, rest = raw_tokens[0], " ".join(raw_tokens[1:])
    values = [display_name]
    for nickname in nickname_variants(first_display):
        values.append(" ".join(value for value in (nickname.title(), rest) if value))
    if len(raw_tokens) > 1:
        values.append(f"{raw_tokens[-1]}, {' '.join(raw_tokens[:-1])}")
    return tuple(dict.fromkeys(values))


def resolve_public_name(
    submitted_name: str, evidence: list[PublicNameEvidence]
) -> ResolvedPublicName:
    candidates: list[tuple[int, int, PublicNameEvidence, str]] = []
    source_bonus = {
        "github_profile": 24,
        "company_team": 22,
        "company_leadership": 22,
        "conference_speaker": 20,
        "public_profile": 18,
        "page_title": 14,
        "search_result": 10,
    }
    for index, item in enumerate(evidence):
        name = extract_public_name(submitted_name, item.name)
        if name is None:
            continue
        parts = parse_name_parts(name)
        score = item.confidence + source_bonus.get(item.source_kind, 8)
        if parts.surname_resolved:
            score += 30
        score += min(8, len(name_tokens(name)) * 2)
        candidates.append((score, -index, item, name))
    if candidates:
        _, _, selected, display_name = max(candidates, key=lambda value: value[:2])
        parts = parse_name_parts(display_name)
        return ResolvedPublicName(
            display_name=display_name,
            surname_resolved=parts.surname_resolved,
            source_url=selected.source_url,
            source_kind=selected.source_kind,
            why=f"full name resolved from {selected.source_kind.replace('_', ' ')}",
            variants=display_name_variants(display_name),
        )

    fallback = re.sub(r"\s+", " ", submitted_name).strip()
    parts = parse_name_parts(fallback)
    return ResolvedPublicName(
        display_name=fallback,
        surname_resolved=parts.surname_resolved,
        source_url=None,
        source_kind="submitted_name",
        why=(
            "no public source established a surname"
            if not parts.surname_resolved
            else "no stronger public display name was available"
        ),
        variants=display_name_variants(fallback),
    )
