"""Stage 2 diagnostic eval: stage-level root causes from persisted traces.

Reads an eval/<label>.json (with trace_run_ids), its trace store, the frozen
dataset and an optional hand-written label overlay. Writes nothing but its own
report; the agent, the trace layer and the official scorer are untouched.
"""

from .rules import (  # noqa: F401
    ERROR_CATEGORIES,
    STAGES,
    UNATTRIBUTED_KINDS,
    DisabledJudge,
    JudgeVerdict,
    diagnose,
)
from .labels import CaseLabels, LabelError, derive_labels, load_labels  # noqa: F401
from .report import aggregate, diagnose_eval, markdown  # noqa: F401
