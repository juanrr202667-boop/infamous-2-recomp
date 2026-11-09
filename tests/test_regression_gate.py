"""JUnit parsing and baseline comparison for the regression gate."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.regression_gate import compare, parse_junit


class RegressionGateTests(unittest.TestCase):
    def parse(self, xml: str) -> dict[str, str]:
        temporary = tempfile.TemporaryDirectory(prefix="i2-gate-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "junit.xml"
        path.write_text(xml, encoding="utf-8")
        return parse_junit(path, "ctest")

    def test_junit_statuses_are_distinct(self):
        results = self.parse("""
<testsuites>
  <testsuite name="suite">
    <testcase classname="" name="ok"/>
    <testcase classname="" name="bad"><failure>no</failure></testcase>
    <testcase classname="" name="err"><error>no</error></testcase>
    <testcase classname="" name="skip"><skipped/></testcase>
  </testsuite>
</testsuites>""")
        self.assertEqual(results, {
            "ok": "passed", "bad": "failed", "err": "failed", "skip": "skipped"})

    def test_baseline_comparison_separates_each_change(self):
        expected = {
            "stable": "passed",
            "now_failing": "passed",
            "now_skipped": "passed",
            "gone": "passed",
            "optional": "skipped",
        }
        actual = {
            "stable": "passed",
            "now_failing": "failed",
            "now_skipped": "skipped",
            "optional": "passed",
            "new_test": "passed",
            "new_skip": "skipped",
        }
        report = compare(expected, actual)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed"], ["now_failing"])
        self.assertEqual(report["skipped_regressions"], ["now_skipped"])
        self.assertEqual(report["missing"], ["gone"])
        self.assertEqual(report["new"], ["new_skip", "new_test"])
        self.assertEqual(report["new_skips"], ["new_skip"])
        self.assertEqual(report["improved"], ["optional"])

    def test_new_test_and_skip_alone_do_not_fail_the_gate(self):
        report = compare({"stable": "passed"}, {"stable": "passed", "new": "skipped"})
        self.assertTrue(report["ok"])
        self.assertEqual(report["new_skips"], ["new"])


if __name__ == "__main__":
    unittest.main()
