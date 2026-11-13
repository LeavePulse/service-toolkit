"""Problems as first-class entities, with history.

A problem says what is wrong with one subject (a host, an edge, a service),
since when, on what evidence, and what the system is doing about it. A service
declares its ``ProblemKind`` values in one ``ProblemRegistry``; ``install``
turns each into reactive rules, so a problem opens when its requirement starts
to hold (after ``hold``), updates while it stays, and resolves when it stops.
The ``ProblemStore`` keeps the problems and an append-only transition log.

See ``docs/problems.md`` for the model and how a reconcile pass feeds it.
``SqlProblemStore`` and ``problem_tables`` need the ``sqlalchemy`` extra and
are imported on first use.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

from .feed import SnapshotFeed
from .model import (
    ACTION_TYPES,
    GENERATION_FIELD,
    PROBLEM_STATES,
    SEVERITIES,
    Action,
    ActionType,
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
from .wiring import ReconcileReport, install, reconcile, uninstall

__all__ = [
    "ACTION_TYPES",
    "GENERATION_FIELD",
    "PROBLEM_STATES",
    "SEVERITIES",
    "Action",
    "ActionType",
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
    "ReconcileReport",
    "Remedy",
    "Severity",
    "SnapshotFeed",
    "SqlProblemStore",
    "Waiting",
    "install",
    "problem_tables",
    "reconcile",
    "uninstall",
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
