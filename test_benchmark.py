import json
import sys
import tempfile
import unittest
from pathlib import Path

from bench.benchmark import measure, run, unique_output, write_results


class BenchmarkEvidenceTests(unittest.TestCase):
    def test_child_cpu_work_is_recorded_separately(self):
        code = (
            "import time\nend=time.process_time()+.03\n"
            "while time.process_time()<end: pass\n"
        )
        sample = measure(lambda: run([sys.executable, "-I", "-c", code]))
        self.assertGreaterEqual(sample["child_cpu_ms"], 20)
        self.assertGreaterEqual(sample["parent_cpu_ms"], 0)
        self.assertGreater(sample["wall_ms"], 0)

    def test_failed_process_never_becomes_successful_measurement(self):
        with self.assertRaisesRegex(RuntimeError, "exited with code 42"):
            measure(lambda: run([sys.executable, "-I", "-c", "raise SystemExit(42)"]))

    def test_evidence_files_are_unique_and_refuse_overwrite(self):
        self.assertNotEqual(unique_output("sample.json"), unique_output("sample.json"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "result.json"
            write_results(path, {"original": True})
            with self.assertRaises(FileExistsError):
                write_results(path, {"replacement": True})
            self.assertEqual(json.loads(path.read_text()), {"original": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
