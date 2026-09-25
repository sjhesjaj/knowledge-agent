"""Label-revision overlays: they must still describe the frozen datasets they correct."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("apply_label_revisions", ROOT / "eval" / "apply_label_revisions.py")
alr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alr)

OVERLAY = ROOT / "eval" / "label_revisions" / "stage3_freshness_contract.revisions.json"


class OverlayTests(unittest.TestCase):
    def test_stage3_overlay_matches_the_frozen_datasets(self):
        overlay = alr.load_revisions(OVERLAY)  # raises if a dataset or old value drifted
        self.assertEqual({(r["dataset"], r["case_id"]) for r in overlay["revisions"]}, {
            ("eval_orchestrated_routes_dev.json", "route_document_only_004"),
            ("eval_orchestrated_routes_validation_v1.json", "route_document_only_h005"),
        })
        for item in overlay["revisions"]:
            self.assertTrue(item["rationale"].strip())
            self.assertNotEqual(item["old"], item["new"])

    def test_rescore_reports_original_and_revised_side_by_side(self):
        overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
        route_eval = {"dataset": "eval_orchestrated_routes_dev.json", "cases": [
            {"id": "route_document_only_004", "expected_requires_freshness": True, "actual_requires_freshness": False},
            {"id": "other", "expected_requires_freshness": True, "actual_requires_freshness": True},
        ]}
        result = alr.rescore(route_eval, overlay)
        self.assertEqual(result["metrics"]["freshness_signal_accuracy"],
                         {"original_labels": "1/2", "with_revisions": "2/2"})
        self.assertEqual([c["id"] for c in result["revised_cases"]], ["route_document_only_004"])

    def test_overlays_never_target_blind_or_holdout_data(self):
        bad = {"revisions": [{"dataset": "eval_orchestrated_routes_blind_v2.json", "field": "expected_requires_freshness"}]}
        path = ROOT / "eval" / "artifacts" / "_bad_overlay_test.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bad), encoding="utf-8")
        self.addCleanup(path.unlink)
        with self.assertRaisesRegex(ValueError, "blind or holdout"):
            alr.load_revisions(path)


if __name__ == "__main__":
    unittest.main()
