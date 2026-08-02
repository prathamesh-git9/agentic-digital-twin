from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TOKEN_RE = re.compile(r"[a-z0-9+#.]+", re.I)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "did",
    "do",
    "does",
    "for",
    "has",
    "have",
    "he",
    "his",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "prathamesh",
    "kalamkar",
    "s",
    "tell",
    "that",
    "the",
    "to",
    "what",
    "with",
    "you",
}


def tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(value)} - STOP_WORDS


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source: str
    text: str
    url: str | None = None


class ProfileCorpus:
    def __init__(self, path: Path, *, show_phone: bool = False) -> None:
        with path.open(encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("profile corpus must be a mapping")
        self.data: dict[str, Any] = loaded
        self.show_phone = show_phone
        self.evidence = self._flatten()

    def _flatten(self) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        person = self.data["person"]
        items.append(
            EvidenceItem(
                "CV › Contact",
                f"Prathamesh Kalamkar is based in {person['location']} and can be "
                f"contacted at {person['email']}.",
            )
        )
        if self.show_phone:
            items.append(EvidenceItem("CV › Contact › Phone", str(person["phone"])))
        for value in self.data["summary"]:
            items.append(EvidenceItem("CV › Summary", value))
        for role in self.data["experience"]:
            source = f"CV › Experience › {role['organisation']} › {role['title']}"
            dates = f"{role['start']} to {role['end']}"
            items.append(
                EvidenceItem(
                    source,
                    f"{role['organisation']} | {role['title']} | {dates}.",
                )
            )
            items.extend(EvidenceItem(source, bullet) for bullet in role["bullets"])
        for project in self.data["projects"]:
            source = f"CV › Projects › {project['name']}"
            technologies = ", ".join(project["technologies"])
            items.append(
                EvidenceItem(
                    source,
                    f"{project['name']} ({project['year']}): {technologies}.",
                )
            )
            items.extend(EvidenceItem(source, bullet) for bullet in project["bullets"])
        for category, values in self.data["skills"].items():
            source = f"CV › Skills › {category}"
            items.append(EvidenceItem(source, ", ".join(values) + "."))
        for education in self.data["education"]:
            source = f"CV › Education › {education['institution']}"
            items.append(
                EvidenceItem(
                    source,
                    f"{education['degree']}, {education['result']}, "
                    f"{education['start']} to {education['end']}. Coursework: "
                    f"{', '.join(education['coursework'])}.",
                )
            )
        for certification in self.data["certifications"]:
            items.append(EvidenceItem("CV › Certifications", certification))
        items.extend(
            [
                EvidenceItem(
                    "Policy › Grounding boundary",
                    "The twin only makes claims supported by the CV or allow-listed "
                    "GitHub metadata and refuses unsupported claims.",
                ),
                EvidenceItem(
                    "Policy › Representation boundary",
                    "The twin cannot negotiate salary, accept offers, agree to start "
                    "dates, or make contractual commitments for Prathamesh.",
                ),
            ]
        )
        return items

    def retrieve(self, query: str, limit: int = 8) -> list[EvidenceItem]:
        query_tokens = tokens(query)
        scored: list[tuple[float, int, EvidenceItem]] = []
        for index, item in enumerate(self.evidence):
            haystack = tokens(f"{item.source} {item.text}")
            overlap = query_tokens & haystack
            score = len(overlap) * 2.0
            if query_tokens and query_tokens <= haystack:
                score += 2.0
            if overlap and "summary" in item.source.casefold():
                score += 0.15
            scored.append((score, -index, item))
        scored.sort(reverse=True, key=lambda row: (row[0], row[1]))
        return [item for score, _, item in scored if score > 0][:limit]

    @property
    def skills(self) -> dict[str, list[str]]:
        return self.data["skills"]

    @property
    def email(self) -> str:
        return str(self.data["person"]["email"])
