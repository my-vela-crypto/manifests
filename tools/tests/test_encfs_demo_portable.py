#!/usr/bin/env python3
"""Run the public demo CLI in relocated workspaces, without an emulator."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "encfs_demo.py"
CONFIG = "goldfish-arm64-v8a-ap-encfs-gui"


class PortableDemo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="encfs demo ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "第一台电脑 source"
        self.tools = self.root / ".repo/manifests/tools"
        self.tools.mkdir(parents=True)
        (self.root / "nuttx").mkdir()
        shutil.copyfile(SOURCE, self.tools / "encfs_demo.py")
        output = self.root / ("cmake_out/vela_" + CONFIG)
        output.mkdir(parents=True)
        for name in ("nuttx", "nuttx.bin", "vela_data.bin", "vela_system.bin"):
            (output / name).write_text(name)
        launcher = self.root / "run-emulator.sh"
        launcher.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
avd = Path(os.environ['CUSTOM_AVD_SPACE'])
instance = sys.argv[sys.argv.index('-keep') + 1]
(avd / (instance + '.vvd') / 'share').mkdir(parents=True, exist_ok=True)
(avd / 'capture.json').write_text(json.dumps({
    'args': sys.argv[1:], 'build': os.environ['VELA_BUILD_DIR'], 'cwd': os.getcwd()}))
if os.environ.get('HOLD_LAUNCH'):
    import time
    time.sleep(60)
''')
        launcher.chmod(0o755)
        self.outside = self.base / "unrelated cwd"
        self.outside.mkdir()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / ".repo/manifests/tools/encfs_demo.py"), *args],
                              cwd=self.outside, capture_output=True, text=True)

    def test_relocation_reuses_identity_and_keys_with_new_paths(self):
        first = self.run_cli("start")
        self.assertEqual(first.returncode, 0, first.stderr)
        state = self.root / "encfs-results/course-demo"
        original_state = (state / "session.json").read_bytes()
        identity = json.loads(original_state)["instance"]
        self.assertNotIn(str(self.root), original_state.decode())
        keys = self.run_cli("keys")
        self.assertEqual(keys.returncode, 0, keys.stderr)
        keydir = state / "avd" / (identity + ".vvd") / "share/keys"
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in keydir.iterdir()}
        moved = self.base / "第二台电脑 moved checkout"
        self.root.rename(moved)
        self.root = moved
        again = self.run_cli("start", "--headless")
        self.assertEqual(again.returncode, 0, again.stderr)
        state = self.root / "encfs-results/course-demo"
        self.assertEqual((state / "session.json").read_bytes(), original_state)
        capture = json.loads((state / "avd/capture.json").read_text())
        self.assertEqual(capture["cwd"], str(self.root))
        self.assertEqual(capture["build"], str(self.root / ("cmake_out/vela_" + CONFIG)))
        self.assertEqual(capture["args"][-1], "-no-window")
        kept = self.run_cli("keys")
        self.assertEqual(kept.returncode, 0, kept.stderr)
        keydir = state / "avd" / (identity + ".vvd") / "share/keys"
        self.assertEqual(hashes, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in keydir.iterdir()})

    def test_dry_run_never_creates_state_or_runs_launcher(self):
        result = self.run_cli("start", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "encfs-results").exists())

    def test_missing_firmware_does_not_create_session(self):
        (self.root / ("cmake_out/vela_" + CONFIG) / "nuttx.bin").unlink()
        result = self.run_cli("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nuttx.bin", result.stderr)
        self.assertFalse((self.root / "encfs-results").exists())

    def test_custom_session_and_build_are_project_relative(self):
        shutil.move(self.root / ("cmake_out/vela_" + CONFIG), self.root / "firmware folder")
        result = self.run_cli("start", "--session", "demo session", "--instance", "another-demo",
                              "--build-dir", "firmware folder")
        self.assertEqual(result.returncode, 0, result.stderr)
        capture = json.loads((self.root / "demo session/avd/capture.json").read_text())
        self.assertEqual(capture["build"], str(self.root / "firmware folder"))
        self.assertIn("another-demo", capture["args"])
        mismatch = self.run_cli("start", "--session", "demo session", "--instance", "different",
                               "--build-dir", "firmware folder")
        self.assertNotEqual(mismatch.returncode, 0)

    def test_results_read_portable_summary_without_recorded_host_paths(self):
        docs = self.root / ".repo/manifests/docs/course-demo"
        docs.mkdir(parents=True)
        payload = {"status": "passed", "framebuffer_count": 578, "workload": {
            "guest_elapsed_ms": 86400640, "completed_iterations": 707001,
            "worker0_iterations": 353379, "worker1_iterations": 353622,
            "errors": 0, "logical_written_bytes": 6783168387,
            "logical_verified_read_bytes": 13492123166}}
        (docs / "24h-summary.json").write_text(json.dumps(payload))
        result = self.run_cli("results")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("707,001", result.stdout)
        self.assertIn("578", result.stdout)
        self.assertNotIn(str(self.root), result.stdout)

    def test_partial_existing_keys_are_never_overwritten(self):
        self.assertEqual(self.run_cli("start").returncode, 0)
        self.assertEqual(self.run_cli("keys").returncode, 0)
        state = self.root / "encfs-results/course-demo"
        identity = json.loads((state / "session.json").read_text())["instance"]
        key = state / "avd" / (identity + ".vvd") / "share/keys/master-00000000.bin"
        key.write_bytes(b"broken")
        result = self.run_cli("keys")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(key.read_bytes(), b"broken")

    def test_stop_targets_only_the_saved_live_process(self):
        child = subprocess.Popen(
            [sys.executable, str(self.tools / "encfs_demo.py"), "start"],
            cwd=self.outside, env=dict(os.environ, HOLD_LAUNCH="1"),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / "encfs-results/course-demo/avd/capture.json").exists():
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            duplicate = self.run_cli("start")
            self.assertNotEqual(duplicate.returncode, 0)
            result = self.run_cli("stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(child.wait(timeout=5), -15)
            stopped = self.run_cli("stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            child.stderr.close()


if __name__ == "__main__":
    unittest.main()
