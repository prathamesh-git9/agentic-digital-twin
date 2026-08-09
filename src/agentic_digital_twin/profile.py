from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .retrieval import BM25Index

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
    """Tokenise for overlap comparisons.

    Dots stay inside the pattern so "node.js", "gmail.com", and "3.11" survive
    as single tokens, which also glues sentence-ending punctuation on: a claim
    ending "...in Cybersecurity." produced `cybersecurity.`, which matched
    nothing, and the grounding verifier refused a true statement over a full
    stop. Surrounding dots are stripped, interior ones kept.
    """

    found = {token.casefold().strip(".") for token in TOKEN_RE.findall(value)}
    return {token for token in found if token} - STOP_WORDS


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source: str
    text: str
    url: str | None = None
    authority: str = "profile"


class ProfileCorpus:
    def __init__(self, path: Path, *, show_phone: bool = False) -> None:
        with path.open(encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("profile corpus must be a mapping")
        self.data: dict[str, Any] = loaded
        self.show_phone = show_phone
        self.evidence = self._flatten()
        self._index: BM25Index | None = None

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

    @property
    def index(self) -> BM25Index:
        # Built once per corpus: the document frequencies are what make a rare
        # term outrank a common one, so they have to span the whole corpus.
        if self._index is None:
            self._index = BM25Index(
                [f"{item.source} {item.text}" for item in self.evidence]
            )
        return self._index

    def retrieve(self, query: str, limit: int = 8) -> list[EvidenceItem]:
        return [self.evidence[position] for _, position in self.index.score(query)][
            :limit
        ]

    @property
    def skills(self) -> dict[str, list[str]]:
        return self.data["skills"]

    @property
    def email(self) -> str:
        return str(self.data["person"]["email"])
