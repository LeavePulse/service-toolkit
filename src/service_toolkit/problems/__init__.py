"""Problems as first-class entities, with history.

A problem says what is wrong with one subject (a host, an edge, a service),
since when, on what evidence, and what the system is doing about it. A service
declares its ``ProblemKind`` values in one ``ProblemRegistry``. The
``ProblemStore`` keeps the problems and an append-only transition log.
``SqlProblemStore`` and ``problem_tables`` need the ``sqlalchemy`` extra and
are imported on first use.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

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
    "ProblemTables",
    "ProblemTransition",
    "Remedy",
    "Severity",
    "SqlProblemStore",
    "Waiting",
    "problem_tables",
]

# SQLAlchemy is an optional extra; a service with the memory store only must
# be able to import this package without it.
_LAZY = {"ProblemTables": ".sql", "SqlProblemStore": ".sql", "problem_tables": ".sql"}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(importlib.import_module(module_name, __name__), name)
    setattr(sys.modules[__name__], name, value)
    return value
