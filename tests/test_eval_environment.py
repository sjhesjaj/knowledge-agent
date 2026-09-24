"""Stage 2.1 eval environment tests. Ollama and git are mocked; nothing reaches the network."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import chat_orchestration
import evaluate_answerability
import rag
import wiki_runtime
from diagnostic_eval.report import eval_context
from eval_env import common
from eval_env import environment as envmod
from eval_env.__main__ import environment_diff, run
from eval_env.environment import EnvironmentRefused, activate, make_environment, verify_environment
from orchestration import load_wiki_pages
from wiki_maintenance.repository import WikiRepository

ROOT = Path(__file__).resolve().parent.parent
DIGEST = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
TAGS = [{"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest", "digest": DIGEST}]
CLEAN = {"commit": "abc123", "describe": "abc123", "branch": "b", "dirty": False, "changes": []}
DIRTY = {**CLEAN, "dirty": True, "changes": [" M rag.py"]}


class EnvTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.envs = self.tmp / "environments"
        # Sources are copies, so a test can tamper with them freely.
        self.sources = self.tmp / "src"
        self.sources.mkdir()
        for name in ("eval_answerability_validation_v1.json", "sample_company_rules.md"):
            shutil.copyfile(ROOT / name, self.sources / name)
        shutil.copyfile(ROOT / "wiki_pages" / "sample_company_wiki.json", self.sources / "sample_company_wiki.json")
        shutil.copyfile(ROOT / "system_fixtures" / "sample_business_system.sql", self.sources / "fixture.sql")
        shutil.copyfile(ROOT / "eval" / "diagnostic_labels" / "validation_v1.labels.json", self.sources / "labels.json")
        self.tags = patch.object(common, "ollama_tags", return_value=TAGS).start()
        self.git = patch.object(common, "git_state", return_value=CLEAN).start()
        self.addCleanup(patch.stopall)

    def make(self, env_id="env-test", **overrides):
        options = dict(dataset=self.sources / "eval_answerability_validation_v1.json",
                       labels=self.sources / "labels.json", documents=[self.sources / "sample_company_rules.md"],
                       system_fixture=self.sources / "fixture.sql",
                       wiki_pages=self.sources / "sample_company_wiki.json", environments_dir=self.envs)
        options.update(overrides)
        return make_environment(env_id, **options)

    def manifest(self, root):
        return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


class MakeTests(EnvTestCase):
    def test_make_snapshots_every_input_and_verifies(self):
        root = self.make()
        manifest = self.manifest(root)
        self.assertEqual(manifest["env_id"], "env-test")
        self.assertEqual(manifest["embedding"], {"model": "nomic-embed-text", "ollama_digest": DIGEST})
        wiki = manifest["corpus"]["wiki"]
        self.assertEqual((wiki["kind"], wiki["corpus_id"], wiki["page_count"]), ("pages_file", "committed_sample", 4))
        for entry in (manifest["corpus"]["documents"][0], wiki, manifest["corpus"]["system_fixture"],
                      manifest["diagnostic_labels"]):
            self.assertTrue((root / entry["path"]).exists())
            self.assertEqual(common.sha256_file(root / entry["path"]), entry["sha256"])
        self.assertNotIn("retriever", manifest)  # experiment configuration is never pinned
        env = verify_environment(root)
        self.assertFalse(env.exploratory)
        self.assertTrue(all(check["ok"] for check in env.checks))

    def test_environments_are_immutable(self):
        self.make()
        with self.assertRaisesRegex(EnvironmentRefused, "immutable"):
            self.make()

    def test_labels_for_another_dataset_are_refused(self):
        labels = json.loads((self.sources / "labels.json").read_text(encoding="utf-8"))
        labels["dataset_sha256"] = "0" * 64
        (self.sources / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
        with self.assertRaisesRegex(EnvironmentRefused, "different dataset"):
            self.make()
        # Refused before anything was written: not even a staging directory.
        self.assertFalse(self.envs.exists() and any(self.envs.iterdir()))

    def test_blind_datasets_are_refused(self):
        with self.assertRaisesRegex(EnvironmentRefused, "blind"):
            self.make(dataset=self.sources / "eval_answerability_blind_v2.json")


class VerifyTests(EnvTestCase):
    def test_tampered_snapshot_is_refused(self):
        root = self.make()
        document = root / self.manifest(root)["corpus"]["documents"][0]["path"]
        document.write_text(document.read_text(encoding="utf-8") + "\n## 新增制度\n", encoding="utf-8")
        with self.assertRaisesRegex(EnvironmentRefused, "document\\[0\\]_sha256"):
            verify_environment(root)

    def test_tampered_label_snapshot_is_refused(self):
        root = self.make()
        labels = root / self.manifest(root)["diagnostic_labels"]["path"]
        labels.write_text(labels.read_text(encoding="utf-8").replace("工作时间", "考勤"), encoding="utf-8")
        with self.assertRaisesRegex(EnvironmentRefused, "diagnostic_labels_sha256"):
            verify_environment(root)

    def test_changed_dataset_is_refused(self):
        root = self.make()
        dataset = self.sources / "eval_answerability_validation_v1.json"
        dataset.write_text(dataset.read_text(encoding="utf-8").replace("绩效", "考核"), encoding="utf-8")
        with self.assertRaisesRegex(EnvironmentRefused, "dataset_sha256"):
            verify_environment(root)

    def test_embedding_digest_mismatch_is_refused(self):
        root = self.make()
        self.tags.return_value = [{**TAGS[0], "digest": "f" * 64}]
        with self.assertRaisesRegex(EnvironmentRefused, "embedding_digest"):
            verify_environment(root)

    def test_unreachable_ollama_is_refused(self):
        root = self.make()
        self.tags.side_effect = requests.ConnectionError("down")
        with self.assertRaisesRegex(EnvironmentRefused, "Ollama unreachable"):
            verify_environment(root)

    def test_dirty_tree_is_refused_unless_explicitly_exploratory(self):
        root = self.make()
        self.git.return_value = DIRTY
        with self.assertRaisesRegex(EnvironmentRefused, "clean_tree"):
            verify_environment(root)
        env = verify_environment(root, allow_dirty=True)
        metadata = env.metadata()
        self.assertEqual((metadata["run_kind"], metadata["baseline_eligible"]), ("exploratory", False))

    def test_exploratory_run_cannot_be_labelled_a_baseline(self):
        with self.assertRaisesRegex(EnvironmentRefused, "baseline"):
            run("env-test", "env_x_baseline", 1, allow_dirty=True)

    def test_existing_results_are_never_overwritten(self):
        with self.assertRaisesRegex(EnvironmentRefused, "never overwritten"):
            run("eval-env-v1", "stage1_trace_qwen", 1)

    def test_renamed_environment_directory_is_refused(self):
        root = self.make()
        moved = root.with_name("other-name")
        root.rename(moved)
        with self.assertRaisesRegex(EnvironmentRefused, "env_id"):
            verify_environment(moved)


class PublishedBuildTests(EnvTestCase):
    def build_file(self, pages):
        repository = WikiRepository(self.tmp / "wiki-src")
        build = repository.create_build(pages)
        return repository.build_path(build.build_id)

    def test_build_snapshot_is_pinned_and_served(self):
        pages = load_wiki_pages(self.sources / "sample_company_wiki.json")[:2]
        labels = json.loads((self.sources / "labels.json").read_text(encoding="utf-8"))
        labels["wiki_corpus"] = "published_build:build-0001"
        (self.sources / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        root = self.make(wiki_pages=None, wiki_build=self.build_file(pages))
        wiki = self.manifest(root)["corpus"]["wiki"]
        self.assertEqual((wiki["kind"], wiki["build_id"], wiki["page_count"]), ("published_build", "build-0001", 2))
        env = verify_environment(root)
        with activate(env, self.tmp / "scratch"):
            served = chat_orchestration.current_wiki_pages()
        self.assertEqual([p.page_id for p in served], [p.page_id for p in pages])

    def test_build_with_another_id_is_refused(self):
        labels = json.loads((self.sources / "labels.json").read_text(encoding="utf-8"))
        labels["wiki_corpus"] = "published_build:build-0001"
        (self.sources / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        root = self.make(wiki_pages=None, wiki_build=self.build_file(load_wiki_pages()))
        manifest = self.manifest(root)
        manifest["corpus"]["wiki"]["build_id"] = "build-0002"
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(EnvironmentRefused, "wiki_build_id"):
            verify_environment(root)

    def test_labels_for_another_wiki_are_refused(self):
        # The overlay says committed_sample; a published-build environment cannot use it.
        with self.assertRaisesRegex(EnvironmentRefused, "label overlay describes wiki"):
            self.make(wiki_pages=None, wiki_build=self.build_file(load_wiki_pages()))


class ActivationTests(EnvTestCase):
    def test_every_input_points_at_the_snapshots_and_is_restored(self):
        root = self.make()
        env = verify_environment(root)
        # A live published build that would normally shadow the committed Wiki.
        live = WikiRepository(self.tmp / "live-wiki")
        live.publish(live.create_build(load_wiki_pages()[:1]).build_id)
        before = (evaluate_answerability.DOCUMENT_PATH, chat_orchestration.SAMPLE_FIXTURE_PATH, rag.CACHE_FILE,
                  chat_orchestration.WIKI_PAGES, evaluate_answerability.build_chunks)
        with patch.object(wiki_runtime, "RUNTIME", wiki_runtime.WikiRuntime(root=self.tmp / "live-wiki")):
            self.assertEqual(len(chat_orchestration.current_wiki_pages()), 1)  # the live build wins outside
            with activate(env, self.tmp / "scratch"):
                self.assertEqual(len(chat_orchestration.current_wiki_pages()), 4)
                self.assertEqual(evaluate_answerability.DOCUMENT_PATH.parent, root / "corpus" / "documents")
                self.assertEqual(chat_orchestration.SAMPLE_FIXTURE_PATH.parent, root / "corpus" / "system")
                self.assertNotEqual(rag.CACHE_FILE, Path(".cache/embeddings.json"))
            self.assertEqual(len(chat_orchestration.current_wiki_pages()), 1)
        after = (evaluate_answerability.DOCUMENT_PATH, chat_orchestration.SAMPLE_FIXTURE_PATH, rag.CACHE_FILE,
                 chat_orchestration.WIKI_PAGES, evaluate_answerability.build_chunks)
        self.assertEqual(before, after)

    def test_embedding_cache_is_bound_to_env_digest_and_chunking(self):
        root = self.make()
        env = verify_environment(root)
        cache_root = self.tmp / "cache"
        vectors = {"n": 0}

        def fake_embed_many(texts):
            vectors["n"] += len(texts)
            return [[0.5, float(len(t))] for t in texts]

        with patch.object(envmod, "CACHE_ROOT", cache_root), patch.object(rag, "embed_many", side_effect=fake_embed_many):
            for _ in range(2):
                with activate(env, self.tmp / "scratch") as facts:
                    chunks = evaluate_answerability.build_chunks()
            self.assertTrue(facts["embedding_cache_reused"])
            self.assertEqual(vectors["n"], len(chunks))  # second run reused every vector
            first_cache = facts["embedding_cache"]
            # A different embedding digest must never read those vectors.
            other = verify_environment(root)
            other.manifest["embedding"]["ollama_digest"] = "e" * 64
            with activate(other, self.tmp / "scratch2") as other_facts:
                evaluate_answerability.build_chunks()
        self.assertNotEqual(other_facts["embedding_cache"], first_cache)
        self.assertFalse(other_facts["embedding_cache_reused"])
        self.assertEqual(vectors["n"], 2 * len(chunks))
        self.assertEqual(facts["chunk_fingerprint"], other_facts["chunk_fingerprint"])
        self.assertNotEqual(facts["index_fingerprint"], "")

    def test_cache_with_mismatched_provenance_is_rebuilt(self):
        root = self.make()
        env = verify_environment(root)
        with patch.object(envmod, "CACHE_ROOT", self.tmp / "cache"):
            chunks = rag.split_text("## 甲\n内容", "sample_company_rules.md")
            path, binding = envmod.embedding_cache_path(env, chunks, "nomic-embed-text")
            path.write_text("{}", encoding="utf-8")
            path.with_suffix(".meta.json").write_text(json.dumps({**binding, "env_id": "forged"}), encoding="utf-8")
            envmod.embedding_cache_path(env, chunks, "nomic-embed-text")
        self.assertFalse(path.exists())


class DiffAndDiagnosticTests(EnvTestCase):
    def test_diagnostic_reads_the_pinned_wiki_corpus(self):
        record = {"environment": {"eval_environment": {"corpus": {"wiki": {"corpus_id": "committed_sample"}}},
                                  "wiki": {"published_build_id": "build-0001"}}}
        self.assertEqual(eval_context(record).wiki_corpus, "committed_sample")
        legacy = {"environment": {"wiki": {"published_build_id": "build-0001"}}}
        self.assertEqual(eval_context(legacy).wiki_corpus, "published_build:build-0001")

    def test_environment_diff_attributes_every_change_to_the_environment(self):
        def result(pass_count, behaviours, eval_env=None):
            runs = [{"passed": p, "actual_behavior": b, "actual_route": "wiki_only", "source_types": ["wiki"],
                     "answer": b} for p, b in zip([True, True, True][:pass_count] + [False] * (3 - pass_count), behaviours)]
            environment = {"eval_environment": eval_env, "git": {"commit": "new"}} if eval_env else \
                {"wiki": {"published_build_id": "build-0001"}, "git_commit": "old"}
            return {"environment": environment, "per_run_summary": [{"passed": pass_count}],
                    "cases": [{"id": "c", "category": "wiki", "pass_count": pass_count, "runs": runs},
                              {"id": "same", "category": "x", "pass_count": 3, "runs": runs}]}

        old = self.tmp / "old.json"
        new = self.tmp / "new.json"
        old.write_text(json.dumps(result(3, ["answer"] * 3)), encoding="utf-8")
        new_record = result(1, ["answer", "generation_refuse", "generation_refuse"],
                            {"env_id": "eval-env-v1", "corpus": {"wiki": {"corpus_id": "committed_sample"}}})
        new_record["cases"][1] = json.loads(old.read_text(encoding="utf-8"))["cases"][1]
        new.write_text(json.dumps(new_record), encoding="utf-8")
        diff = environment_diff(old, new)
        changed = {row["id"]: row for row in diff["cases"]}
        self.assertEqual(changed["c"]["change"], "pass_count_changed")
        self.assertEqual(changed["c"]["attribution"], "environment_change")
        self.assertEqual(changed["same"]["change"], "unchanged")
        self.assertIn("unpinned", diff["environments"]["old"]["wiki"])
        self.assertEqual(diff["environments"]["new"]["wiki"], "committed_sample")


if __name__ == "__main__":
    unittest.main()
