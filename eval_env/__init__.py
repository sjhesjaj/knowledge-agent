"""Versioned eval environments (Stage 2.1): pinned corpora, labels and embeddings.

See `environment.py` for what an environment pins and `__main__.py` for the
make / verify / run / diff commands. Agent code is never modified; a run only
redirects its inputs to the environment's immutable snapshots.
"""

from .environment import (  # noqa: F401
    EnvironmentRefused,
    VerifiedEnvironment,
    activate,
    make_environment,
    verify_environment,
)
