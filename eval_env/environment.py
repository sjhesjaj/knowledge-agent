"""Versioned, immutable eval environments.

An environment pins every *input* of an eval run that is data rather than code:

- the frozen dataset (referenced by path + sha256 - it is already immutable);
- a snapshot of the diagnostic label overlay;
- snapshots of the corpora: the document(s), the Wiki (a pages file or a
  published-build snapshot) and the System fixture;
- the embedding model, by Ollama digest.

Everything is copied under `eval/environments/<env_id>/` once, by `make`, and
never rewritten. `verify` checks every hash before a run and refuses on any
mismatch. `activate` points the unchanged agent code at the snapshots for the
duration of a run - the live `data/wiki`, the shared embedding cache and the
repository's sample files are never read.

Retriever parameters are experiment configuration (code), not environment;
runs record them but environments do not pin them.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from . import common

SCHEMA_VERSION = 1
ENVIRONMENTS_DIR = common.ROOT / "eval" / "environments"
CACHE_ROOT = common.ROOT / "eval" / "artifacts" / "env-cache"
WIKI_PAGES_FILE, WIKI_PUBLISHED_BUILD = "pages_file", "published_build"


class EnvironmentRefused(RuntimeError):
    """The environment cannot be trusted for this run; nothing was executed."""


def _refuse_blind(path: Path) -> None:
    if "blind" in Path(path).name.lower():
        raise EnvironmentRefused(f"refusing to use blind dataset {Path(path).name}")


# --------------------------------------------------------------------------
# make
# --------------------------------------------------------------------------


def make_environment(env_id: str, *, dataset: Path, labels: Path, documents: list[Path],
                     system_fixture: Path, wiki_pages: Path | None = None, wiki_build: Path | None = None,
                     embedding_model: str = "nomic-embed-text", embedding_digest: str | None = None,
                     description: str = "", environments_dir: Path = ENVIRONMENTS_DIR) -> Path:
    """Snapshot the inputs into a new environment directory. Never overwrites."""
    target = Path(environments_dir) / env_id
    if target.exists():
        raise EnvironmentRefused(f"environment {env_id} already exists; environments are immutable - make a new version")
    if (wiki_pages is None) == (wiki_build is None):
        raise EnvironmentRefused("give exactly one of wiki_pages or wiki_build")
    _refuse_blind(dataset)
    if len(documents) != 1:
        raise EnvironmentRefused("the answerability evaluator indexes exactly one document")
    if embedding_digest is None:
        embedding_digest = common.model_digest(common.ollama_tags(), embedding_model)
        if embedding_digest is None:
            raise EnvironmentRefused(f"embedding model {embedding_model} is not installed in Ollama")

    labels_data = json.loads(Path(labels).read_text(encoding="utf-8"))
    if labels_data.get("dataset_sha256") != common.sha256_file(dataset):
        raise EnvironmentRefused("the label overlay was written for a different dataset version")

    Path(environments_dir).mkdir(parents=True, exist_ok=True)
    # Staged next to its final place so the closing rename is atomic and same-volume.
    staging = Path(tempfile.mkdtemp(prefix=f".staging-{env_id}-", dir=environments_dir))
    try:
        def copy(source: Path, relative: str) -> dict:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return {"path": relative, "sha256": common.sha256_file(destination),
                    "bytes": destination.stat().st_size, "copied_from": common.rel(source)}

        docs = [{**copy(doc, f"corpus/documents/{Path(doc).name}"), "source_name": Path(doc).name}
                for doc in documents]
        if wiki_pages is not None:
            from orchestration import load_wiki_pages

            wiki = {"kind": WIKI_PAGES_FILE, "corpus_id": "committed_sample",
                    **copy(wiki_pages, f"corpus/wiki/{Path(wiki_pages).name}"),
                    "page_count": len(load_wiki_pages(wiki_pages))}
        else:
            build = json.loads(Path(wiki_build).read_text(encoding="utf-8"))
            wiki = {"kind": WIKI_PUBLISHED_BUILD, "build_id": build["build_id"],
                    "corpus_id": f"published_build:{build['build_id']}",
                    **copy(wiki_build, f"corpus/wiki/{build['build_id']}.json"),
                    "page_count": len(build["pages"])}
        if labels_data.get("wiki_corpus") != wiki["corpus_id"]:
            raise EnvironmentRefused(f"label overlay describes wiki {labels_data.get('wiki_corpus')!r}, "
                                     f"but this environment's wiki is {wiki['corpus_id']!r}")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "env_id": env_id,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "created_from": {k: v for k, v in common.git_state().items() if k != "changes"},
            "dataset": {"path": common.rel(dataset), "sha256": common.sha256_file(dataset)},
            "diagnostic_labels": copy(labels, f"labels/{Path(labels).name}"),
            "corpus": {"documents": docs, "wiki": wiki,
                       "system_fixture": copy(system_fixture, f"corpus/system/{Path(system_fixture).name}")},
            "embedding": {"model": embedding_model, "ollama_digest": embedding_digest},
            "not_pinned": {"retriever": "experiment configuration - recorded per run, not part of the environment",
                           "chat_model": "the system under test - recorded per run"},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------


@dataclass
class VerifiedEnvironment:
    root: Path
    manifest: dict
    manifest_sha256: str
    git: dict
    exploratory: bool
    checks: list[dict] = field(default_factory=list)

    @property
    def env_id(self) -> str:
        return self.manifest["env_id"]

    def path(self, relative: str) -> Path:
        return self.root / relative

    @property
    def dataset_path(self) -> Path:
        return common.ROOT / self.manifest["dataset"]["path"]

    @property
    def labels_path(self) -> Path:
        return self.path(self.manifest["diagnostic_labels"]["path"])

    @property
    def document(self) -> dict:
        return self.manifest["corpus"]["documents"][0]

    @property
    def wiki(self) -> dict:
        return self.manifest["corpus"]["wiki"]

    def metadata(self) -> dict:
        corpus = self.manifest["corpus"]
        return {
            "env_id": self.env_id,
            "manifest_path": common.rel(self.root / "manifest.json"),
            "manifest_sha256": self.manifest_sha256,
            "dataset": self.manifest["dataset"],
            "diagnostic_labels": {k: self.manifest["diagnostic_labels"][k] for k in ("path", "sha256")},
            "corpus": {
                "documents": [{k: d[k] for k in ("path", "source_name", "sha256")} for d in corpus["documents"]],
                "wiki": {k: v for k, v in corpus["wiki"].items() if k in
                         ("kind", "corpus_id", "build_id", "path", "sha256", "page_count")},
                "system_fixture": {k: corpus["system_fixture"][k] for k in ("path", "sha256")},
            },
            "embedding": self.manifest["embedding"],
            "verification": self.checks,
            "run_kind": "exploratory" if self.exploratory else "reference",
            "baseline_eligible": not self.exploratory,
        }


def verify_environment(env_dir: Path, *, allow_dirty: bool = False) -> VerifiedEnvironment:
    """Check every pinned input; raise EnvironmentRefused on the first mismatch."""
    root = Path(env_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise EnvironmentRefused(f"{root} has no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks: list[dict] = []

    def require(ok: bool, name: str, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok)})
        if not ok:
            raise EnvironmentRefused(f"{manifest.get('env_id', root.name)}: {name}: {detail}")

    require(manifest.get("schema_version") == SCHEMA_VERSION, "schema_version", "unsupported manifest schema")
    require(manifest.get("env_id") == root.name, "env_id", "manifest env_id does not match its directory")

    dataset = common.ROOT / manifest["dataset"]["path"]
    _refuse_blind(dataset)
    require(dataset.exists(), "dataset_present", f"{dataset} is missing")
    require(common.sha256_file(dataset) == manifest["dataset"]["sha256"], "dataset_sha256",
            "the frozen dataset changed")

    def pinned(entry: dict, name: str) -> Path:
        path = root / entry["path"]
        require(path.exists(), f"{name}_present", f"{entry['path']} is missing")
        require(common.sha256_file(path) == entry["sha256"], f"{name}_sha256", f"{entry['path']} was modified")
        return path

    labels_path = pinned(manifest["diagnostic_labels"], "diagnostic_labels")
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    require(labels.get("dataset_sha256") == manifest["dataset"]["sha256"], "labels_dataset",
            "the label snapshot targets another dataset version")
    documents = manifest["corpus"]["documents"]
    require(len(documents) == 1, "single_document", "the answerability evaluator indexes exactly one document")
    for index, entry in enumerate(documents):
        pinned(entry, f"document[{index}]")
    wiki = manifest["corpus"]["wiki"]
    wiki_path = pinned(wiki, "wiki")
    require(labels.get("wiki_corpus") == wiki["corpus_id"], "labels_wiki_corpus",
            f"labels describe wiki {labels.get('wiki_corpus')!r}, environment has {wiki['corpus_id']!r}")
    if wiki["kind"] == WIKI_PAGES_FILE:
        from orchestration import load_wiki_pages

        require(len(load_wiki_pages(wiki_path)) == wiki["page_count"], "wiki_page_count", "page count changed")
    elif wiki["kind"] == WIKI_PUBLISHED_BUILD:
        build = json.loads(wiki_path.read_text(encoding="utf-8"))
        require(build.get("build_id") == wiki["build_id"], "wiki_build_id",
                f"snapshot declares {build.get('build_id')!r}, manifest pins {wiki['build_id']!r}")
        require(len(build.get("pages") or []) == wiki["page_count"], "wiki_page_count", "page count changed")
    else:
        require(False, "wiki_kind", f"unknown wiki kind {wiki['kind']!r}")
    pinned(manifest["corpus"]["system_fixture"], "system_fixture")

    embedding = manifest["embedding"]
    try:
        digest = common.model_digest(common.ollama_tags(), embedding["model"])
    except requests.RequestException as exc:
        require(False, "embedding_digest", f"Ollama unreachable, cannot verify {embedding['model']}: {exc}")
    require(digest == embedding["ollama_digest"], "embedding_digest",
            f"{embedding['model']} digest {digest} != pinned {embedding['ollama_digest']}")

    git = common.git_state()
    if git["dirty"] and not allow_dirty:
        require(False, "clean_tree", "the working tree has changes; commit them, or pass --allow-dirty "
                                     "for an exploratory run that can never become a baseline")
    checks.append({"check": "clean_tree", "ok": not git["dirty"], "allowed_dirty": bool(git["dirty"])})
    return VerifiedEnvironment(root, manifest, common.sha256_file(manifest_path), git, bool(git["dirty"]), checks)


# --------------------------------------------------------------------------
# embedding cache bound to env / digest / chunking
# --------------------------------------------------------------------------


def chunk_fingerprint(chunks) -> str:
    return common.sha256_text(json.dumps([[c.source, c.index, c.text] for c in chunks], ensure_ascii=False))


def embedding_cache_path(env: VerifiedEnvironment, chunks, embed_model: str) -> tuple[Path, dict]:
    """A cache file that can only ever hold vectors for this env, model digest and chunking."""
    binding = {"env_id": env.env_id, "embedding_model": embed_model,
               "embedding_digest": env.manifest["embedding"]["ollama_digest"],
               "chunk_fingerprint": chunk_fingerprint(chunks)}
    key = common.sha256_text(json.dumps(binding, sort_keys=True))[:16]
    path = CACHE_ROOT / env.env_id / f"embeddings-{key}.json"
    meta = path.with_suffix(".meta.json")
    if path.exists() and (not meta.exists() or json.loads(meta.read_text(encoding="utf-8")) != binding):
        path.unlink()  # never reuse vectors whose provenance does not match exactly
    path.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(binding, sort_keys=True), encoding="utf-8")
    return path, binding


def index_fingerprint(chunks) -> str:
    """Identity of the built index: chunk text plus the exact vectors used."""
    return common.sha256_text(json.dumps([[c.source, c.index, c.text, c.embedding] for c in chunks]))


# --------------------------------------------------------------------------
# activate
# --------------------------------------------------------------------------


@contextmanager
def activate(env: VerifiedEnvironment, scratch: Path):
    """Point the unchanged agent code at the environment's snapshots.

    Yields a dict that is filled with index facts once the evaluator builds its
    chunks. Every patch is undone on exit.
    """
    import chat_orchestration
    import evaluate_answerability
    import rag
    import wiki_runtime
    from orchestration import load_wiki_pages

    scratch = Path(scratch)
    facts: dict = {}
    document = env.path(env.document["path"])
    if document.name != env.document["source_name"]:
        raise EnvironmentRefused("document snapshot must keep its source file name")
    wiki = env.wiki
    wiki_file = env.path(wiki["path"])
    patches = [
        patch.object(evaluate_answerability, "DOCUMENT_PATH", document),
        patch.object(chat_orchestration, "SAMPLE_FIXTURE_PATH", env.path(env.manifest["corpus"]["system_fixture"]["path"])),
    ]
    if wiki["kind"] == WIKI_PAGES_FILE:
        # No published build may shadow the pinned pages: the runtime gets an empty root.
        patches += [patch.object(chat_orchestration, "WIKI_PAGES", load_wiki_pages(wiki_file)),
                    patch.object(wiki_runtime, "RUNTIME", wiki_runtime.WikiRuntime(root=scratch / "wiki-empty"))]
    else:
        build_root = scratch / "wiki-build"
        (build_root / "builds").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wiki_file, build_root / "builds" / f"{wiki['build_id']}.json")
        (build_root / "current.json").write_text(json.dumps({
            "schema_version": "1.0", "build_id": wiki["build_id"],
            "published_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}), encoding="utf-8")
        patches += [patch.object(chat_orchestration, "WIKI_PAGES", ()),
                    patch.object(wiki_runtime, "RUNTIME", wiki_runtime.WikiRuntime(root=build_root))]

    original_build_chunks = evaluate_answerability.build_chunks

    def build_chunks():
        text = rag.read_file(document.name, document.read_bytes())
        chunks = rag.split_text(text, document.name)
        cache_path, binding = embedding_cache_path(env, chunks, rag.EMBED_MODEL)
        reused = cache_path.exists()
        with patch.object(rag, "CACHE_FILE", cache_path):
            indexed = rag.build_index(chunks)
        facts.update({"chunk_count": len(indexed), "chunk_fingerprint": binding["chunk_fingerprint"],
                      "index_fingerprint": index_fingerprint(indexed), "embedding_cache": common.rel(cache_path),
                      "embedding_cache_reused": reused})
        return indexed

    patches.append(patch.object(evaluate_answerability, "build_chunks", build_chunks))
    patches.append(patch.object(rag, "CACHE_FILE", scratch / "no-shared-cache.json"))
    for item in patches:
        item.start()
    try:
        yield facts
    finally:
        for item in reversed(patches):
            item.stop()
        assert evaluate_answerability.build_chunks is original_build_chunks
