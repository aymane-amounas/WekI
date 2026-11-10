"""Interface for the future AI/RAG phase. Nothing is implemented yet."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Doc:
    path: str
    title: str
    text: str
    score: float = 0.0


def retrieve(query: str) -> list[Doc]:
    raise NotImplementedError("AI retrieval is out of scope for v1")
