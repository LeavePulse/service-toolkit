"""Problems as first-class entities, with history.

A problem says what is wrong with one subject (a host, an edge, a service),
since when, on what evidence, and what the system is doing about it. A service
declares its ``ProblemKind`` values in one ``ProblemRegistry``. The
``ProblemStore`` keeps the problems and an append-only transition log.
"""

from __future__ import annotations

from .model import (
    GENERATION_FIELD,
    Action,
    NeedsOperator,
    Nothing,
    Problem,
    ProblemDetails,
    ProblemKind,
    ProblemRegistry,
    ProblemState,
    ProblemTransition,
    Remedy,
    Severity,
    Waiting,
)
from .store import MemoryProblemStore, ProblemEvent, ProblemStore

__all__ = [
    "GENERATION_FIELD",
    "Action",
    "MemoryProblemStore",
    "NeedsOperator",
    "Nothing",
    "Problem",
    "ProblemDetails",
    "ProblemEvent",
    "ProblemKind",
    "ProblemRegistry",
    "ProblemState",
    "ProblemStore",
    "ProblemTransition",
    "Remedy",
    "Severity",
    "Waiting",
]
