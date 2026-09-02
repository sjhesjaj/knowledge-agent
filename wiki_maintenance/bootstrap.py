"""Seed an empty repository with the committed sample Wiki as `build-0001`.

This is the bridge from the hand-authored collection in `wiki_pages/` to the
build lifecycle. It reads that file and never writes to it: the committed
sample stays the reviewed source, and the repository holds copies of it under
version control of its own.

A repository that already has builds is refused rather than reseeded. Bootstrap
is a first-run action, and silently laying a second `build-0001`-shaped history
over an existing one would destroy the very provenance the builds exist to keep.

Nothing here runs on import; the caller chooses the repository and the moment.
"""

from __future__ import annotations

from pathlib import Path

from orchestration.wiki_adapter import DEFAULT_WIKI_PATH, load_wiki_pages

from .models import WikiBuild
from .repository import WikiRepository, WikiRepositoryError


def bootstrap_from_wiki_file(
    repository: WikiRepository,
    *,
    path: str | Path = DEFAULT_WIKI_PATH,
    publish: bool = True,
) -> WikiBuild:
    """Create the repository's first build from a Wiki collection file.

    Returns the created build. With `publish` left on, it is also the live
    build, so a fresh repository goes from empty to answerable in one call.
    """
    if not isinstance(repository, WikiRepository):
        raise ValueError(
            f"repository must be a WikiRepository, got {type(repository).__name__}"
        )
    existing = repository.list_builds()
    if existing:
        raise WikiRepositoryError(
            f"repository at {repository.root} already has "
            f"{len(existing)} build(s), starting with {existing[0].build_id!r}; "
            "bootstrap only seeds an empty repository"
        )

    pages = load_wiki_pages(path)
    build = repository.create_build(pages)
    if publish:
        repository.publish(build.build_id)
    return build
