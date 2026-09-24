"""Stage 2.5 comparison tooling: pricing, peak hours, transitions. No network."""

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("model_comparison", ROOT / "eval" / "model_comparison.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


def at(*args):
    return datetime(*args, tzinfo=timezone.utc)


class PricingTests(unittest.TestCase):
    def test_peak_hours_are_weekday_utc_windows(self):
        self.assertTrue(mc.is_peak(at(2026, 9, 24, 1, 0)))      # Thursday 01:00
        self.assertFalse(mc.is_peak(at(2026, 9, 24, 4, 0)))     # window end is exclusive
        self.assertTrue(mc.is_peak(at(2026, 9, 24, 9, 59)))
        self.assertFalse(mc.is_peak(at(2026, 9, 24, 12, 0)))
        self.assertFalse(mc.is_peak(at(2026, 9, 26, 7, 0)))     # Saturday

    def test_cost_uses_cache_split_and_time_of_day(self):
        usage = {"prompt_cache_hit_tokens": 1_000_000, "prompt_cache_miss_tokens": 1_000_000,
                 "completion_tokens": 1_000_000}
        off = mc.call_cost({"kind": "deepseek", "peak": False, "usage": usage})
        peak = mc.call_cost({"kind": "deepseek", "peak": True, "usage": usage})
        self.assertAlmostEqual(off, 0.003 + 0.15 + 0.6)
        self.assertAlmostEqual(peak, 2 * off)
        self.assertIsNone(mc.call_cost({"kind": "ollama", "peak": False, "usage": {"prompt_tokens": 5}}))
        # Without the cache split the cost is unknown, never guessed.
        self.assertIsNone(mc.call_cost({"kind": "deepseek", "peak": False, "usage": {"prompt_tokens": 5}}))


class TransitionTests(unittest.TestCase):
    def record(self, counts):
        return {"runs": 3, "cases": [{"id": cid, "category": "x", "pass_count": n,
                                      "runs": [{"actual_behavior": "answer"}]} for cid, n in counts.items()]}

    def test_the_four_transitions_and_unstable(self):
        qwen = self.record({"a": 3, "b": 0, "c": 3, "d": 0, "e": 2})
        deepseek = self.record({"a": 3, "b": 3, "c": 0, "d": 0, "e": 3})
        result = mc.transitions(qwen, deepseek)
        self.assertEqual(result["counts"], {"stable_pass": 1, "fixed": 1, "newly_failed": 1,
                                            "unchanged_failure": 1, "unstable": 1})
        self.assertEqual({r["id"]: r["transition"] for r in result["cases"]},
                         {"b": "fixed", "c": "newly_failed", "d": "unchanged_failure", "e": "unstable"})


class SeriesTests(unittest.TestCase):
    def test_series_use_separate_labels_and_directories(self):
        old, new = mc.arms_for("stage25"), mc.arms_for("pmi")
        self.assertEqual([a["label"] for a in old], ["stage25_qwen_env_v1", "stage25_deepseek_env_v1"])
        self.assertEqual([a["label"] for a in new], ["pmi_qwen_env_v1", "pmi_deepseek_env_v1"])
        self.assertNotEqual(mc.SERIES["stage25"]["out_dir"], mc.SERIES["pmi"]["out_dir"])
        self.assertEqual(mc.SERIES["pmi"]["kind"], "post-main-integration baseline")

    def test_existing_reports_are_never_rewritten(self):
        from eval_env.environment import EnvironmentRefused

        with self.assertRaisesRegex(EnvironmentRefused, "never overwritten"):
            mc.build_report("stage25")


if __name__ == "__main__":
    unittest.main()
