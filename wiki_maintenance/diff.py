"""Deterministic difference between two Wiki builds.

Identity is the `page_id` and the `claim_id`, nothing else. A page or claim
present in both builds is "updated" when its serialized content differs -
title, summary, aliases, version, and for a page its claims too. There is no
model in this comparison, no embedding, no similarity threshold: a rename is
reported as one removal and one addition, because deciding that two differently
named pages are "the same page" is a judgement, and a diff that guesses is worse
than a diff that reports plainly.

Claim identity is global across a build (`validate_collection` enforces that),
so a claim that moves from one page to another with its wording intact is not a
claim change. Both pages report as updated, which is where the move is visible.

Every list comes back sorted by id, so the same pair of builds always produces
byte-identical output.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestration.wiki_schema import WikiClaim, WikiPage

from .models import WikiBuild


@dataclass(frozen=True, kw_only=True)
class WikiDiff:
    """What changed between `base_build_id` and `target_build_id`."""

    base_build_id: str
    target_build_id: str
    added_page_ids: tuple[str, ...] = ()
    removed_page_ids: tuple[str, ...] = ()
    updated_page_ids: tuple[str, ...] = ()
    added_claim_ids: tuple[str, ...] = ()
    removed_claim_ids: tuple[str, ...] = ()
    updated_claim_ids: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.added_page_ids
            or self.removed_page_ids
            or self.updated_page_ids
            or self.added_claim_ids
            or self.removed_claim_ids
            or self.updated_claim_ids
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "base_build_id": self.base_build_id,
            "target_build_id": self.target_build_id,
            "added_page_ids": list(self.added_page_ids),
            "removed_page_ids": list(self.removed_page_ids),
            "updated_page_ids": list(self.updated_page_ids),
            "added_claim_ids": list(self.added_claim_ids),
            "removed_claim_ids": list(self.removed_claim_ids),
            "updated_claim_ids": list(self.updated_claim_ids),
        }


def _pages_by_id(build: WikiBuild) -> dict[str, WikiPage]:
    return {page.page_id: page for page in build.pages}


def _claims_by_id(build: WikiBuild) -> dict[str, WikiClaim]:
    return {
        claim.claim_id: claim for page in build.pages for claim in page.claims
    }


def diff_builds(base: WikiBuild, target: WikiBuild) -> WikiDiff:
    """Compare two builds by id, then by serialized content."""
    if not isinstance(base, WikiBuild) or not isinstance(target, WikiBuild):
        raise ValueError("diff_builds takes two WikiBuild values")

    base_pages = _pages_by_id(base)
    target_pages = _pages_by_id(target)
    base_claims = _claims_by_id(base)
    target_claims = _claims_by_id(target)

    return WikiDiff(
        base_build_id=base.build_id,
        target_build_id=target.build_id,
        added_page_ids=tuple(sorted(set(target_pages) - set(base_pages))),
        removed_page_ids=tuple(sorted(set(base_pages) - set(target_pages))),
        updated_page_ids=tuple(
            sorted(
                page_id
                for page_id in set(base_pages) & set(target_pages)
                if base_pages[page_id].to_dict() != target_pages[page_id].to_dict()
            )
        ),
        added_claim_ids=tuple(sorted(set(target_claims) - set(base_claims))),
        removed_claim_ids=tuple(sorted(set(base_claims) - set(target_claims))),
        updated_claim_ids=tuple(
            sorted(
                claim_id
                for claim_id in set(base_claims) & set(target_claims)
                if base_claims[claim_id].to_dict() != target_claims[claim_id].to_dict()
            )
        ),
    )
