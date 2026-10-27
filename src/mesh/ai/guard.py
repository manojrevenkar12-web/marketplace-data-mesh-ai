"""Text that reaches a model from people other than the user (dataset descriptions, log lines) is screened
for instructions aimed at the model. Flagged sentences are removed before the prompt is built and reported
to the dataset owner. Whatever slips through still cannot widen access: the query engine checks every query
against the user's own grants."""

from __future__ import annotations

import re
from dataclasses import dataclass

INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|your)\s+(instructions|rules|prompt)"
    r"|you\s+are\s+now\b|new\s+instructions?\s*:|system\s+prompt|act\s+as\s+(an?\s+)?(admin|administrator|root)"
    r"|(always|must)\s+(include|select|return|query)\s+\w+\s+(columns?|from)"
    r"|(reveal|print|output|expose)\s+(the\s+)?(hidden|restricted|secret|pii|personal)"
    r"|(grant|give)\s+(me|the\s+user|everyone)\s+access|bypass\s+(the\s+)?(permissions|access|lake\s*formation)"
    r"|\b(notes?|instructions?|message)\s+(for|to)\s+(the\s+)?(assistants?|ai|models?|llms?|copilots?|agents?)\b"
    r"|\b(assistants?|ai|llms?|copilots?|agents?)\s*:\s"
    r"|\b(also|always)\s+(return|include|select|add|show)\s+(the\s+)?[a-z_]+(\s+(column|field)s?)?\s+(so|when|for|in)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Screened:
    text: str
    flagged: list[str]


def screen(text: str) -> Screened:
    """Drop sentences that address the model; keep the rest of the description."""
    if not text:
        return Screened("", [])
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept: list[str] = []
    flagged: list[str] = []
    for s in sentences:
        (flagged if INJECTION.search(s) else kept).append(s)
    return Screened(" ".join(k for k in kept if k.strip()).strip(), [f.strip() for f in flagged if f.strip()])
