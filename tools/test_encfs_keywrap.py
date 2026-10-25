#!/usr/bin/env python3
"""Test real online key wrapping in a new, independent emulator volume.

The external files model a simulator provider, not a TEE. Each invocation
generates new disposable material using host os.urandom. Existing user data
and the previous delivery are never reused or reformatted.
"""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import traceback
import uuid

from test_encfs import (Emulator, git_revision, inspect_image, now, require,
                        sha256, source_default, validate_build)
from test_encfs_gui import (capture, expect_encfs, long_stress, save_report,
                            start_demo, widgets_interactions)
import pexpect


MARKER = "ENCFS_WRAP_UTF8_20261024_7df925ca"
LOGICAL = "/data/验证/密钥🔐.txt"


def slot_status(emulator, key_id, generation):
    output = emulator.command("encfs status")
    require(re.search(rf"wrapped=1 master_id={key_id} generation={generation}\b",
                      output), "Unexpected wrapped-volume identity")


def read_marker(emulator):
    require(MARKER in emulator.command("cat " + LOGICAL),
            "Chinese/emoji marker did not survive key rotation")


def slot_record(directory):
    path = directory / "encfs.keyslot"
    record = path.read_bytes()
    require(len(record) == 112 and record[:8] == b"ENCKEY01",
            "Unexpected external keyslot format")
    return record


def run(args, report):
    stamp = uuid.uuid4().hex[:12]
    (args.results_dir / "avd").mkdir()
    legacy = "encfs-legacy-" + stamp
    wrapped = "encfs-wrapped-" + stamp

    phase = {"name": "legacy-rekey-rejection", "status": "running"}
    report["phases"].append(phase)
    save_report(args, report)
    with Emulator(args, legacy, phase) as emulator:
        emulator.encfs("provision --erase")
        emulator.encfs("start")
        # NuttX defines ENOTSUP as 138; Linux aliases it to 95.
        expect_encfs(emulator, "rekey 1", state="READY", result=-138)
        emulator.encfs("stop")
    phase["status"] = "passed"
    save_report(args, report)

    phase = {"name": "gui-wrapped-key-stress", "status": "running"}
    report["phases"].append(phase)
    save_report(args, report)
    directory = args.results_dir / "avd" / (wrapped + ".vvd") / "share" / "keys"
    image = directory.parents[1] / "vela_data.bin"
    masters = {i: os.urandom(32) for i in range(4)}

    def master_path(key_id):
        return directory / f"master-{key_id:08x}.bin"

    with Emulator(args, wrapped, phase) as emulator:
        directory.mkdir(mode=0o700)
        for key_id, material in masters.items():
            master_path(key_id).write_bytes(material)
            master_path(key_id).chmod(0o600)
        random_path = directory / "volume-random.bin"
        random_path.write_bytes(os.urandom(64))
        random_path.chmod(0o600)
        expect_encfs(emulator, "stop", state="STOPPED")
        expect_encfs(emulator, "keyfiles /share/keys", state="STOPPED")
        expect_encfs(emulator, "start", state="STOPPED", result=-126)
        emulator.encfs("provision --erase")
        require(not random_path.exists(), "One-use random fixture was not consumed")
        emulator.encfs("start")
        slot_status(emulator, 0, 1)
        emulator.command("mkdir /data/验证")
        emulator.command("echo " + MARKER + " > " + LOGICAL)
        read_marker(emulator)
        initial = slot_record(directory)
        phase["initial_slot_sha256"] = sha256(directory / "encfs.keyslot")

        expect_encfs(emulator, "rekey 99", state="READY", result=-126)
        require(slot_record(directory) == initial,
                "Missing replacement key changed the committed keyslot")
        expect_encfs(emulator, "keyslot-failcommit on", state="READY")
        expect_encfs(emulator, "rekey 1", state="READY", result=-5)
        require(slot_record(directory) == initial,
                "Failed commit replaced the committed keyslot")
        slot_status(emulator, 0, 1)
        read_marker(emulator)

        before_rekey = sha256(image)
        expect_encfs(emulator, "rekey 1", state="READY")
        slot_status(emulator, 1, 2)
        require(slot_record(directory) != initial,
                "Online rekey did not update the wrapping record")
        require(sha256(image) == before_rekey,
                "Rewrapping modified the underlying data disk")
        read_marker(emulator)
        master_path(0).unlink()
        phase["idle_rekey_data_disk_unchanged"] = True
        phase["failed_commit_preserved_slot"] = True

        start_demo(args, emulator, phase, "widgets")
        widgets_interactions(args, emulator, phase)
        rotation_done = False
        rotation_counts = None

        def rotate_during_io(current, workers, checkpoint):
            nonlocal rotation_done, rotation_counts
            if rotation_done:
                return
            require(all(value > 0 for value in workers),
                    "Both I/O workers must advance before online rotation")
            expect_encfs(current, "rekey 2", state="READY")
            slot_status(current, 2, 3)
            read_marker(current)
            master_path(1).unlink()
            rotation_done = True
            rotation_counts = tuple(workers)
            checkpoint["online_rotation"] = {
                "master_id": 2, "generation": 3,
                "worker_iterations_before_rotation": list(workers),
                "old_master_files_removed": True,
            }

        long_stress(args, emulator, phase, report,
                    checkpoint_hook=rotate_during_io)
        require(rotation_done, "No rotation was completed during concurrent I/O")
        final = phase["long_stress_result"]
        require(all(final[f"worker{i}_iterations"] > rotation_counts[i]
                    for i in range(2)),
                "Both workers must perform further I/O after online rotation")
        emulator.log.flush()
        serial = Path(phase["serial_log"]).read_text(errors="replace")
        start_index = serial.index("ENCFS_LONG_STRESS_START ")
        rotation_index = serial.index("encfs rekey 2", start_index)
        result_index = serial.index("ENCFS_LONG_STRESS_RESULT ", start_index)
        require(start_index < rotation_index < result_index,
                "Rotation command was not between workload start and completion")
        acknowledgment_index = serial.index(
            "wrapped=1 master_id=2 generation=3", rotation_index)
        after_rotation = []
        for line in re.findall(r"ENCFS_LONG_STRESS_PROGRESS ([^\r\n]+)",
                               serial[acknowledgment_index:result_index]):
            after_rotation.append({key: int(value) for key, value in
                                   re.findall(r"(\w+)=(\d+)", line)})
        require(len(after_rotation) >= 2,
                "Need two independent progress samples after rotation acknowledgment")
        require(all(after_rotation[1][f"worker{i}_iterations"] >
                    after_rotation[0][f"worker{i}_iterations"] for i in range(2)),
                "Both workers must advance between two post-rotation samples")
        phase["online_rotation_proof"] = {
            "command_between_workload_start_and_result": True,
            "both_workers_advanced_between_post_acknowledgment_samples": True,
            "post_acknowledgment_progress_samples": after_rotation[:2],
            "iterations_before_rotation": list(rotation_counts),
            "iterations_at_completion": [final[f"worker{i}_iterations"]
                                          for i in range(2)],
        }
        read_marker(emulator)
        capture(args, emulator, phase, "after-wrapped-stress")
        emulator.encfs("stop")
    phase["image"] = {"path": str(image), "sha256": sha256(image),
                      "size": image.stat().st_size}
    phase["final_slot_sha256"] = sha256(directory / "encfs.keyslot")
    phase["status"] = "passed"
    reference_hash = sha256(image)
    save_report(args, report)

    phase = {"name": "wrapped-coldboot-and-negative-keys", "status": "running"}
    report["phases"].append(phase)
    save_report(args, report)
    slot_path = directory / "encfs.keyslot"
    committed = slot_record(directory)
    with Emulator(args, wrapped, phase) as emulator:
        expect_encfs(emulator, "stop", state="STOPPED")
        expect_encfs(emulator, "keyfiles /share/keys", state="STOPPED")
        emulator.encfs("start")
        slot_status(emulator, 2, 3)
        read_marker(emulator)
        emulator.encfs("stop")

        slot_path.unlink()
        expect_encfs(emulator, "start", state="STOPPED", result=-126)
        require(not slot_path.exists(), "Ordinary start recreated a missing keyslot")
        slot_path.write_bytes(committed)

        damaged = bytearray(committed)
        damaged[-1] ^= 1
        slot_path.write_bytes(damaged)
        expect_encfs(emulator, "start", state="STOPPED", result=-74)
        require(slot_path.read_bytes() == damaged,
                "Ordinary start repaired a damaged keyslot")
        slot_path.write_bytes(committed)

        master_path(2).write_bytes(masters[0])
        expect_encfs(emulator, "start", state="STOPPED", result=-74)
        master_path(2).unlink()
        expect_encfs(emulator, "start", state="STOPPED", result=-126)
        master_path(2).write_bytes(masters[2][:-1])
        expect_encfs(emulator, "start", state="STOPPED", result=-74)
        master_path(2).write_bytes(masters[2])
        emulator.encfs("start")
        slot_status(emulator, 2, 3)
        read_marker(emulator)
        emulator.encfs("stop")
    require(sha256(image) == reference_hash,
            "Cold boot or negative-key testing modified the data disk")
    require(slot_record(directory) == committed,
            "Negative-key testing modified the valid committed keyslot")
    inspect_image(image, encrypted=True, result=phase.setdefault("image", {}))
    require(MARKER.encode() not in image.read_bytes(),
            "Encrypted disk exposes the unique wrapped-volume marker")
    phase.update(status="passed", coldboot_data_disk_unchanged=True,
                 missing_wrong_truncated_master_rejected=True,
                 missing_damaged_keyslot_rejected=True)
    save_report(args, report)

    # Stop the guest at the exact durable-temp / atomic-rename boundary.
    # This is an intentional emulator-process cut, not clean shutdown and
    # not a physical storage power-loss claim.
    phase = {"name": "keyslot-cut-before-commit", "status": "running"}
    report["phases"].append(phase)
    save_report(args, report)
    temporary = directory / "encfs.keyslot.tmp"
    require(not temporary.exists(), "Unexplained temporary keyslot before cut test")
    with Emulator(args, wrapped, phase) as emulator:
        emulator.encfs("stop")
        expect_encfs(emulator, "keyfiles /share/keys", state="STOPPED")
        emulator.encfs("start")
        read_marker(emulator)
        expect_encfs(emulator, "keyslot-pausecommit 10000", state="READY")
        marker = "ENCFS_KEYSLOT_PAUSED before_atomic_commit ms=10000"
        output = emulator.command("encfs rekey 3 &")
        if marker not in output:
            emulator.child.expect_exact(marker, timeout=8)
        phase["observed_pause_marker"] = marker
        emulator.log.flush()
        require(marker in Path(phase["serial_log"]).read_text(errors="replace"),
                "The exact post-fsync/readback pause marker is absent from serial")
        require(temporary.is_file() and temporary.stat().st_size == 112,
                "No complete staged keyslot was observed before the cut")
        require(slot_record(directory) == committed,
                "The committed keyslot changed before the pause/cut")
        staged_hash = sha256(temporary)
        emulator.signal_group(signal.SIGKILL)
        emulator.child.expect(pexpect.EOF, timeout=10)
        emulator.child.close(force=False)
        deadline = time.monotonic() + 10
        while emulator.group_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        require(not emulator.group_running(), "Cut emulator descendants remained alive")
        phase["intentional_interruption"] = {
            "signal": "SIGKILL", "clean_shutdown": False,
            "staged_slot_sha256": staged_hash, "staged_slot_bytes": 112,
            "committed_slot_unchanged": slot_record(directory) == committed,
        }
        emulator.log.flush()
        emulator.child = None
    require(slot_record(directory) == committed and temporary.is_file(),
            "Cut did not preserve the old commit plus staged temporary")
    phase["status"] = "passed"
    save_report(args, report)

    phase = {"name": "keyslot-cut-recovery", "status": "running"}
    report["phases"].append(phase)
    save_report(args, report)
    with Emulator(args, wrapped, phase) as emulator:
        emulator.encfs("stop")
        expect_encfs(emulator, "keyfiles /share/keys", state="STOPPED")
        emulator.encfs("start")
        slot_status(emulator, 2, 3)
        read_marker(emulator)
        output = emulator.command("encfs_test rekey 3")
        matches = re.findall(r"ENCFS_REKEY_RESULT pass=(\d+) fail=(\d+) "
                             r"next_master_id=3", output)
        require(len(matches) == 1 and int(matches[0][0]) > 0 and
                int(matches[0][1]) == 0,
                "Live open/dup/DIR rekey helper failed during stale-temp recovery")
        main_result = re.findall(r"ENCFS_TEST_RESULT mode=rekey pass=(\d+) fail=(\d+)",
                                 output)
        require(len(main_result) == 1 and int(main_result[0][0]) > 0 and
                int(main_result[0][1]) == 0,
                "Registered rekey test did not report complete success")
        for label in (
                "rotate master while file and DIR remain open",
                "same descriptor reads unchanged data after rekey",
                "same open DIR returns logical names after rekey",
                "same DIR rewinds and reads after rekey",
                "append through duplicate after rekey",
                "read all data after wrapper restart"):
            require("[PASS] " + label in output,
                    "Missing live-handle observation: " + label)
        phase["live_handle_guest_result"] = {
            "pass": int(matches[0][0]), "fail": int(matches[0][1])}
        slot_status(emulator, 3, 4)
        require(not temporary.exists(), "Retry did not recover its stale staged keyslot")
        read_marker(emulator)
        emulator.encfs("stop")
    # The helper intentionally writes and removes its own live-handle file;
    # therefore this phase records a new hash rather than requiring equality
    # with the earlier read-only boot.
    phase["image"] = {"path": str(image), "sha256": sha256(image),
                      "size": image.stat().st_size}
    phase["final_slot_sha256"] = sha256(slot_path)
    phase["status"] = "passed"
    save_report(args, report)

    master_path(2).unlink()
    reference_hash = sha256(image)
    phase = {"name": "recovered-wrapper-coldboot", "status": "running"}
    report["phases"].append(phase)
    save_report(args, report)
    with Emulator(args, wrapped, phase) as emulator:
        emulator.encfs("stop")
        expect_encfs(emulator, "keyfiles /share/keys", state="STOPPED")
        emulator.encfs("start")
        slot_status(emulator, 3, 4)
        read_marker(emulator)
        emulator.encfs("stop")
    require(sha256(image) == reference_hash,
            "Read-only recovered-wrapper cold boot changed the data disk")
    require(not any(master_path(i).exists() for i in range(3)),
            "Old provider masters unexpectedly remain available")
    phase.update(status="passed", final_master_id=3, final_generation=4,
                 only_current_master_available=True,
                 coldboot_data_disk_unchanged=True,
                 image={"path": str(image), "sha256": sha256(image),
                        "size": image.stat().st_size})
    save_report(args, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=source_default())
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--stress-seconds", type=int, default=180)
    args = parser.parse_args()
    if not 150 <= args.stress_seconds <= 86400:
        parser.error("stress duration must be between 150 and 86400 seconds")
    args.source_root = args.source_root.resolve()
    args.results_dir = args.results_dir.resolve()
    args.config = "goldfish-arm64-v8a-ap-encfs-gui"
    args.gpu = "swiftshader_indirect"
    args.emulator_args = []
    args.boot_timeout = 300
    args.command_timeout = 300
    args.shutdown_timeout = 30
    args.build_dir = (args.build_dir or args.source_root / "cmake_out" /
                      ("vela_" + args.config)).resolve()
    if args.results_dir.exists() and any(args.results_dir.iterdir()):
        parser.error("Results directory must be empty")
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report = {"started_at": now(), "status": "running", "phases": [],
              "requested_stress_seconds": args.stress_seconds,
              "config": args.config, "build_dir": str(args.build_dir),
              "limitations": [
                  "Disposable host-file provider models a keystore, not a TEE.",
                  "No real power-loss or malicious-host rollback guarantee.",
                  "Legacy directly-derived volumes reject online rekey.",
                  "Captured old keyslots together with old masters remain decryptable."]}
    result = 1
    try:
        report["build_artifacts"] = validate_build(args.source_root, args.build_dir)
        config = (args.build_dir / ".config").read_text()
        for option in ("ENCFS_KEYWRAP", "GRAPHICS_LVGL", "EXAMPLES_LVGLDEMO",
                       "FBCAPTURE", "GRAPHICS_INPUT_TOOL"):
            require(f"CONFIG_{option}=y" in config, f"Missing {option}")
        report["source_revisions"] = {
            name: git_revision(args.source_root / relative)
            for name, relative in (("nuttx", "nuttx"), ("apps", "apps"),
                                   ("vendor_openvela", "vendor/openvela"),
                                   ("manifests", ".repo/manifests"))}
        run(args, report)
        report["status"] = "passed"
        result = 0
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        if report["phases"] and report["phases"][-1]["status"] == "running":
            report["phases"][-1]["status"] = "failed"
        print(f"Keywrap regression failed: {exc}", file=sys.stderr, flush=True)
    finally:
        report["finished_at"] = now()
        save_report(args, report)
        print(f"Report: {args.results_dir / 'report.json'}", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
