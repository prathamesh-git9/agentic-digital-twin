"""Evidence ranking for the twin's chat corpus.

Two problems with counting overlapping tokens, which is what this replaces.

**Every token counted the same.** "engineer" appears in most of the CV and
"Kafka" appears once, but a query matching either scored identically. BM25's
inverse document frequency fixes that: a rare term that matches is strong
evidence about what the visitor is asking for, a common one is close to noise.

**Recruiters and CVs use different words.** Someone asks about "message queues"
and the CV says "Kafka" and "SQS"; someone asks about "containers" and the CV
says "Docker". With no shared token the corpus returned nothing, and no
evidence means the twin refuses -- so it looked *less* qualified than it is.
That is the worst failure this system has, because it is invisible: the visitor
sees a polite refusal and never learns the answer was in the CV.

The alias map below closes that gap, and its shape is deliberate. It is
hand-written data, reviewable in a diff, not an embedding whose behavior nobody
can inspect. Expansions are discounted, so a direct term always outranks a
synonym. And it only affects *which evidence is retrieved*: the grounding
verifier is untouched, so widening retrieval can never widen what the twin is
allowed to claim. Asking about Kubernetes still surfaces nothing that supports
a Kubernetes claim, because there is nothing in the CV to find.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

TOKEN_RE = re.compile(r"[a-z0-9+#.]+", re.I)

# BM25 parameters. b is below the usual 0.75 because these documents are single
# CV bullets: length differences carry less meaning than in prose, and heavy
# normalisation would over-reward the terse skill lines.
K1 = 1.2
B = 0.6

# A matched synonym is real but weaker evidence of intent than the visitor's
# own word, so it scores lower and can never outrank a direct hit.
ALIAS_WEIGHT = 0.55

# Recruiter vocabulary mapped onto the words this CV actually uses. Every
# right-hand term must appear in data/profile.yaml; tests enforce that, so this
# map cannot drift into suggesting experience the corpus does not contain.
ALIASES: dict[str, tuple[str, ...]] = {
    # Messaging and asynchronous work
    "queue": ("kafka", "sqs", "event", "pipelines"),
    "queues": ("kafka", "sqs", "event", "pipelines"),
    "messaging": ("kafka", "sqs", "event", "driven"),
    "broker": ("kafka", "sqs"),
    "brokers": ("kafka", "sqs"),
    "pubsub": ("kafka", "sqs", "event"),
    "streaming": ("kafka", "event", "driven"),
    "asynchronous": ("event", "driven", "pipelines", "retries"),
    "async": ("event", "driven", "pipelines"),
    "eventing": ("event", "driven", "pipelines"),
    # Containers and delivery
    "container": ("docker",),
    "containers": ("docker",),
    "containerisation": ("docker",),
    "containerization": ("docker",),
    "pipeline": ("ci", "cd", "actions"),
    "pipelines": ("ci", "cd", "actions", "event", "driven"),
    "deployment": ("ci", "cd", "docker"),
    "devops": ("ci", "cd", "docker", "actions"),
    # Data and storage
    "database": ("sql", "databases", "modeling"),
    "databases": ("sql", "modeling"),
    "storage": ("sql", "vector", "store"),
    "persistence": ("sql", "databases", "state"),
    "vectordb": ("vector", "store", "embeddings"),
    "retrieval": ("rag", "embeddings", "vector", "context"),
    "semantic": ("embeddings", "vector", "rag"),
    # AI and agents
    "llm": ("llm", "openai", "claude", "integrations"),
    "llms": ("llm", "openai", "claude"),
    "genai": ("llm", "openai", "claude", "agentic"),
    "agent": ("agentic", "agent", "tool", "orchestration"),
    "agents": ("agentic", "agent", "orchestration"),
    "agentic": ("agentic", "orchestration", "workflows"),
    "toolcalling": ("function", "calling", "tool", "orchestration"),
    "chatbot": ("llm", "agentic", "prompt"),
    "finetuning": ("model", "serving", "patterns"),
    "guardrail": ("guardrails", "validation", "safety"),
    "guardrails": ("guardrails", "validation", "safety"),
    "evaluation": ("evals", "evaluation", "harness", "scoring"),
    "evals": ("evals", "evaluation", "harness"),
    "hallucination": ("guardrails", "validation", "grounding"),
    # Reliability and operations
    "monitoring": ("logs", "metrics", "traces", "observability", "dashboards"),
    "observability": ("logs", "metrics", "traces", "dashboards", "opentelemetry"),
    "telemetry": ("metrics", "traces", "opentelemetry", "logs"),
    "alerting": ("metrics", "dashboards", "diagnostics"),
    "oncall": ("production", "support", "diagnostics", "debugging"),
    "sre": ("reliability", "observability", "production", "diagnostics"),
    "resilience": ("retries", "reliability", "guardrails", "health"),
    "reliability": ("retries", "reliability", "health", "diagnostics"),
    "scalability": ("microservices", "performance", "tuning"),
    "performance": ("performance", "tuning", "diagnostics"),
    # Backend and platform
    "backend": ("back", "end", "spring", "boot", "rest", "apis"),
    "api": ("rest", "apis", "graphql", "contracts"),
    "apis": ("rest", "apis", "graphql", "contracts"),
    "microservice": ("microservices", "service", "contracts"),
    "jvm": ("java", "spring", "boot", "jvm"),
    "java": ("java", "spring", "boot"),
    "webhooks": ("apis", "rest", "integrations"),
    # Security
    "authentication": ("authentication", "flows", "access", "control"),
    "authorisation": ("authentication", "access", "control", "privilege"),
    "authorization": ("authentication", "access", "control", "privilege"),
    "secrets": ("secret", "management", "configuration"),
    "vulnerability": ("vulnerabilities", "security", "analysis"),
    "appsec": ("security", "vulnerabilities", "validation"),
    "cybersecurity": ("cybersecurity", "security"),
    "injection": ("injection", "prompt", "defenses", "validation"),
    # Cloud
    "cloud": ("aws", "docker", "ci", "cd"),
    "aws": ("aws",),
    "serverless": ("aws", "sqs"),
    "infrastructure": ("docker", "ci", "cd", "configuration"),
    # Testing and process
    "testing": ("testing", "integration", "regression", "checks"),
    "qa": ("qa", "testing", "regression", "reviews"),
    "tdd": ("testing", "regression", "checks"),
    "codereview": ("code", "reviews", "pull", "request"),
    "mentoring": ("collaborate", "collaborating", "teams", "reviews"),
    "leadership": ("ownership", "collaborating", "teams"),
    # Education and background
    "degree": ("msc", "bsc", "honours", "university", "dublin"),
    "education": ("msc", "bsc", "university", "coursework"),
    "masters": ("msc", "cybersecurity", "dublin"),
    "university": ("university", "dublin", "business", "school"),
    "certification": ("course", "certifications"),
    "certifications": ("course", "certifications"),
    # Logistics
    "visa": ("dublin", "ireland", "based"),
    "relocation": ("dublin", "ireland", "based"),
    "remote": ("dublin", "ireland", "based"),
    "located": ("dublin", "ireland", "based"),
    "salary": ("negotiate", "representation", "boundary"),
    "compensation": ("negotiate", "representation", "boundary"),
    "notice": ("present",),
    "availability": ("present", "freelance"),
    # "Present" is how the CV encodes "the job I have now", so the words a
    # visitor uses for the current moment have to reach it.
    "now": ("present", "freelance"),
    "current": ("present", "freelance"),
    "currently": ("present", "freelance"),
    "today": ("present", "freelance"),
    "contact": ("contact", "contacted"),
    "experience": ("experience",),
    "years": ("experience", "start"),
}

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


def token_list(value: str) -> list[str]:
    """Tokenise keeping repeats, because term frequency is part of the score.

    The pattern keeps dots so "node.js", "gmail.com", and "3.11" survive as one
    token, which also glues sentence-ending punctuation on: "Jan 2025 to
    Present." yielded `present.`, a token no query could ever match. Trailing
    and leading dots are stripped, interior ones kept.
    """

    result: list[str] = []
    for raw in TOKEN_RE.findall(value):
        token = raw.casefold().strip(".")
        if token and token not in STOP_WORDS:
            result.append(token)
    return result


def expand_query(query: str) -> dict[str, float]:
    """Return query terms with weights, folding in discounted aliases.

    A term the visitor actually typed keeps full weight even when it is also an
    alias target, so expansion can add reach but never demote a direct match.
    """

    weights: dict[str, float] = {}
    for term in token_list(query):
        weights[term] = 1.0
    for term in list(weights):
        for alias in ALIASES.get(term, ()):
            if alias not in weights:
                weights[alias] = ALIAS_WEIGHT
    return weights


# Words that point at something said earlier instead of naming it. A question
# built from these has no retrievable content of its own.
ANAPHORA = frozenset(
    {
        "that",
        "there",
        "those",
        "them",
        "they",
        "these",
        "this",
        "then",
        "its",
        "same",
        "one",
        "ones",
        "above",
        "else",
        "more",
        "other",
    }
)

# A question with at most this many content words names nothing on its own --
# "why?", "how long?", "and?". The bar is deliberately low: stop words strip
# most of a sentence, so "What databases have you worked with?" reduces to two
# terms while being perfectly self-contained. Carrying context into a question
# that did not need it dilutes a good query, so anaphora is the primary signal
# and length is only the fallback for questions with almost nothing left.
SELF_CONTAINED_TERMS = 1


def conversational_query(
    question: str,
    history: list[dict[str, str]],
    *,
    max_carried_terms: int = 6,
) -> str:
    """Return the text to retrieve with, carrying context into follow-ups.

    Retrieval saw only the current message, so the second half of every real
    conversation searched on words that name nothing. "Tell me about your
    back-end work" retrieves correctly; "what did you use there?" retrieves on
    "use", and the twin answers a question nobody asked -- or refuses.

    Terms are carried from the visitor's own earlier turns, not from the twin's
    answers: an answer's vocabulary is what the model chose to say, and feeding
    it back would let one loose reply steer every later retrieval. Expansion is
    for retrieval only; the model still receives the question as written.
    """

    current = token_list(question)
    leans_on_context = bool(set(current) & ANAPHORA)
    if len(current) > SELF_CONTAINED_TERMS and not leans_on_context:
        return question

    seen = set(current)
    carried: list[str] = []
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        for term in token_list(message.get("content", "")):
            if term in seen or term in ANAPHORA:
                continue
            seen.add(term)
            carried.append(term)
        if len(carried) >= max_carried_terms:
            break
    if not carried:
        return question
    return " ".join([question, *carried[:max_carried_terms]])


@dataclass(frozen=True, slots=True)
class _Document:
    index: int
    counts: Counter
    length: int


class BM25Index:
    """Okapi BM25 over the flattened profile corpus."""

    def __init__(self, documents: list[str]) -> None:
        self._documents = [
            _Document(index, Counter(terms := token_list(text)), len(terms))
            for index, text in enumerate(documents)
        ]
        self._average_length = (
            sum(document.length for document in self._documents) / len(self._documents)
            if self._documents
            else 0.0
        )
        frequencies: Counter = Counter()
        for document in self._documents:
            frequencies.update(document.counts.keys())
        total = max(1, len(self._documents))
        self._idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in frequencies.items()
        }

    def score(self, query: str) -> list[tuple[float, int]]:
        """Return (score, document index) for every document that matched."""

        weights = expand_query(query)
        if not weights:
            return []
        scored: list[tuple[float, int]] = []
        for document in self._documents:
            total = 0.0
            for term, weight in weights.items():
                frequency = document.counts.get(term, 0)
                if not frequency:
                    continue
                idf = self._idf.get(term, 0.0)
                normalised = (
                    frequency
                    * (K1 + 1)
                    / (
                        frequency
                        + K1
                        * (1 - B + B * document.length / (self._average_length or 1.0))
                    )
                )
                total += weight * idf * normalised
            if total > 0:
                scored.append((total, document.index))
        # Ties resolve to corpus order so results stay stable between runs.
        scored.sort(key=lambda row: (-row[0], row[1]))
        return scored
