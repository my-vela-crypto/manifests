#!/usr/bin/env python3
"""Run upstream FS regression and deliberate emulator SIGKILL recovery.

Uses dedicated new encrypted disks. Killing a software emulator does not model
all physical media/controller power-loss behaviours. Fsynced, committed files
are checked byte for byte; an interrupted temporary file is explicitly allowed
to be partial. Raw upstream failures are retained and never relabelled passes.
"""

import argparse
import json
from pathlib import Path
import re
import signal
import time
import traceback
import uuid

import test_encfs as base


def save(args, report):
    (args.results_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def phase(report, name):
    item = {"name": name}
    report["phases"].append(item)
    return item


def parse_cmocka(output):
    started = re.findall(r"\[\s*RUN\s*\]\s+(\w+)", output)
    passed = re.findall(r"\[\s*OK\s*\]\s+(\w+)", output)
    failed = re.findall(r"\[\s*FAILED\s*\]\s+(\w+)", output)
    failed = list(dict.fromkeys(name for name in failed if name in started))
    skipped = re.findall(r"\[\s*SKIPPED\s*\]\s+(\w+)", output)
    base.require(started, "Upstream cmocka did not run any cases")
    base.require(set(started) == set(passed) | set(failed) | set(skipped),
                 "Upstream cmocka did not finish every case")
    return {"started": started, "passed": passed, "failed": failed,
            "skipped": skipped, "counts": {"run": len(started),
            "pass": len(passed), "fail": len(failed), "skip": len(skipped)}}


def required_command(emu, command, expected):
    output = emu.command(command)
    base.require(expected in output, f"Missing required result: {expected}")
    return output


def set_clock(emu):
    # The storage target has no RTC: its initial epoch is 1970, outside the
    # FAT timestamp range. Initialize the test clock, without changing FatFs.
    output = emu.command('date -u -s "Jan 02 03:04:05 2026"')
    base.require("failed" not in output.lower() and "not found" not in output.lower(),
                 "Guest clock initialization failed")
    emu.phase["guest_clock"] = {"source": "NSH explicit test clock initialization",
                                 "value": "Jan 02 03:04:05 2026", "hardware_rtc": False}


def regression(args, report, instance):
    item = phase(report, "upstream-fs-regression")
    with base.Emulator(args, instance, item) as emu:
        set_clock(emu)
        emu.encfs("provision --erase")
        emu.encfs("start")
        clock = emu.command("encfs_extended clock")
        report["time_probe"] = clock
        matches = re.findall(r"ENCFS_TIME_RESULT now=(\d+) mtime=(-?\d+) delta=(-?\d+)", clock)
        base.require(len(matches) == 1 and int(matches[0][0]) >= 315532800
                     and abs(int(matches[0][2])) <= 2,
                     "FAT timestamp probe does not agree with initialized guest clock")
        output = emu.command("cmocka_fs_test", timeout=600)
        report["upstream_cmocka"] = parse_cmocka(output)
        save(args, report)
        required_command(emu, "mkdir /data/fstest", "")
        output = emu.command("fstest -m /data/fstest -n 10 -o 64 -s 8192",
                             timeout=600)
        results = re.findall(r"File system tests done\.\.\. OK: (\d+), FAILED: (\d+)", output)
        base.require(len(results) == 1, "fstest did not complete")
        passed, failed = map(int, results[0])
        report["upstream_fstest"] = {"loops": 10, "max_open": 64,
                                      "max_file_size": 8192,
                                      "pass": passed, "fail": failed}
        base.require(passed > 0 and failed == 0, "fstest reported failure")
        output = emu.command("vela_fs_rwloops /data 100", timeout=300)
        base.require("TEST PASSED !" in output and "TEST FAILED !" not in output,
                     "Original Vela append/read loop failed")
        report["vela_fs_rwloops"] = {"loops": 100, "status": "passed",
            "source": "tests/testcases/vela_fs_test/fs/stress/read_and_write_loops_test.c"}
        emu.encfs("stop")
    item["status"] = "completed"
    save(args, report)


def kill_emulator(emu, record):
    """Intentionally bypass graceful Emulator.shutdown for this one guest."""
    child = emu.child
    record.update({"kill_requested_at": base.now(), "signal": "SIGKILL",
                   "process_group": emu.group,
                   "process_id": child.pid})
    emu.signal_group(signal.SIGKILL)
    child.expect(base.pexpect.EOF, timeout=15)
    child.close(force=False)
    deadline = time.monotonic() + 15
    while emu.group_running() and time.monotonic() < deadline:
        time.sleep(0.05)
    base.require(not emu.group_running(), "Emulator descendants survived SIGKILL")
    record.update({"process_group_stopped": True,
                   "exit_status": child.exitstatus,
                   "exit_signal": child.signalstatus,
                   "killed_at": base.now()})
    emu.phase.update({"intentional_sigkill": True,
                      "finished_at": base.now()})
    emu.log.flush()
    emu.log.close()
    emu.log = None
    emu.child = None


def recovery(args, report, instance):
    item = phase(report, "power-prepare")
    with base.Emulator(args, instance, item) as emu:
        set_clock(emu)
        emu.encfs("provision --erase")
        emu.encfs("start")
        required_command(emu, "encfs_extended prepare",
                         "ENCFS_POWER_PREPARED sentinels=3 committed_generation=0")
        required_command(emu, "encfs_extended verify 0",
                         "ENCFS_POWER_VERIFIED sentinels=3 committed_generation=0")
        emu.encfs("stop")

    committed = 0
    report["powercuts"] = []
    for index in range(args.powercuts):
        item = phase(report, f"powercut-{index + 1:02d}")
        record = {"index": index + 1,
                  "boundary": "mid-temporary-write" if index % 2 == 0 else "after-commit"}
        report["powercuts"].append(record)
        with base.Emulator(args, instance, item) as emu:
            set_clock(emu)
            emu.encfs("start")
            required_command(emu, f"encfs_extended verify {committed}",
                             f"ENCFS_POWER_VERIFIED sentinels=3 committed_generation={committed}")
            emu.child.sendline(f"encfs_extended churn {committed + 1}")
            emu.child.expect(re.compile(
                rf"ENCFS_POWER_COMMITTED generation={committed + 1}\r*\n"), timeout=30)
            committed += 1
            if index % 2 == 0:
                emu.child.expect(re.compile(
                    rf"ENCFS_POWER_INFLIGHT generation={committed + 1} offset=4096\r*\n"),
                    timeout=30)
            record.update({"expected_committed_generation": committed,
                           "temporary_generation": committed + 1,
                           "temporary_expected_complete": False})
            kill_emulator(emu, record)
        image = args.results_dir / "avd" / (instance + ".vvd") / "vela_data.bin"
        record["image_after_kill"] = {"path": str(image), "sha256": base.sha256(image)}
        save(args, report)

    item = phase(report, "power-final-coldboot")
    with base.Emulator(args, instance, item) as emu:
        set_clock(emu)
        emu.encfs("start")
        output = required_command(emu, f"encfs_extended verify {committed}",
                                  f"ENCFS_POWER_VERIFIED sentinels=3 committed_generation={committed}")
        record["final_verify_output"] = output
        emu.encfs("stop")
    report["power_recovery"] = {"status": "passed", "cuts": args.powercuts,
        "final_committed_generation": committed, "sentinels_per_boot": 3,
        "verified_bytes_final_boot": 514 + 65537 * (committed + 2),
        "verified_generations_final_boot": committed + 1,
        "scope": "Dedicated emulator process SIGKILL; not real hardware power loss"}
    save(args, report)


def negative_oracle(args, report, instance):
    item = phase(report, "single-byte-corruption-oracle")
    with base.Emulator(args, instance, item) as emu:
        set_clock(emu)
        emu.encfs("provision --erase")
        emu.encfs("start")
        required_command(emu, "encfs_extended prepare",
                         "ENCFS_POWER_PREPARED sentinels=3 committed_generation=0")
        required_command(emu, "encfs_extended verify 0",
                         "ENCFS_POWER_VERIFIED sentinels=3 committed_generation=0")
        emu.command("cd /data/power-fixture")
        # Byte 4097 of sentinel2 is nonzero by the deterministic oracle.
        # Keep the file length unchanged so this tests content, not size.
        emu.command("dd if=/dev/zero of=sentinel2 bs=1 count=1 seek=4097 conv=notrunc")
        emu.command("cd /")
        listing = emu.command("ls -l /data/power-fixture")
        base.require(re.search(r"\b65537\b[^\n]*\bsentinel2\b", listing),
                     "Negative fixture did not retain its original file length")
        output = emu.command("encfs_extended verify 0")
        base.require("ENCFS_EXTENDED_RESULT action=verify result=-1 errno=84" in output,
                     "The recovery oracle accepted a single corrupted byte")
        base.require("ENCFS_POWER_VERIFIED" not in output,
                     "Corrupted fixture emitted a false success")
        report["negative_oracle"] = {"status": "passed", "offset": 4097,
            "mutation": "one byte overwritten with zero; length unchanged",
            "expected_error": "EILSEQ (84)", "output": output,
            "directory_listing": listing}
        emu.encfs("stop")
    item["status"] = "passed"
    save(args, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=base.source_default())
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--config", default="goldfish-arm64-v8a-ap-encfs-extended")
    parser.add_argument("--powercuts", type=int, default=10)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--regression-only", action="store_true")
    selection.add_argument("--recovery-only", action="store_true")
    parser.add_argument("--gpu", default="off")
    parser.add_argument("--boot-timeout", type=int, default=120)
    parser.add_argument("--command-timeout", type=int, default=300)
    parser.add_argument("--shutdown-timeout", type=int, default=15)
    args = parser.parse_args()
    args.source_root = args.source_root.resolve()
    args.build_dir = (args.build_dir or args.source_root / "cmake_out" /
                      ("vela_" + args.config)).resolve()
    args.results_dir = args.results_dir.resolve()
    base.require(not args.results_dir.exists(), "Choose a new results directory")
    base.require(1 <= args.powercuts <= 100, "powercuts must be between 1 and 100")
    args.results_dir.mkdir(parents=True)
    (args.results_dir / "avd").mkdir()
    report = {"schema": 1, "started_at": base.now(), "status": "running",
              "source_root": str(args.source_root), "build_dir": str(args.build_dir),
              "config": args.config, "phases": [], "scope": [
                  "Unmodified upstream cmocka filesystem suite on the encrypted /data mount",
                  "Upstream fstest 10 loops with 64 files of up to 8192 bytes",
                  "Original Vela append/read loop, 100 repetitions",
                  "Dedicated encrypted disk, SIGKILL during writes and after commits"]}
    try:
        report["artifacts"] = base.validate_build(args.source_root, args.build_dir)
        report["source_revisions"] = {name: base.git_revision(args.source_root / name)
                                      for name in ("nuttx", "apps", "tests", "vendor/openvela")}
        sources = ["apps/testing/fs/encfs_extended/encfs_extended_main.c",
                   "apps/testing/testsuites/kernel/fs/cmocka_fs_test.c",
                   "apps/testing/fs/fstest/fstest_main.c",
                   "tests/testcases/vela_fs_test/fs/stress/read_and_write_loops_test.c",
                   "nuttx/fs/encpathfs/encpathfs_codec.c",
                   "nuttx/fs/encpathfs/encpathfs_mount.c",
                   "nuttx/fs/encfs/encfs_manager.c"]
        report["source_fingerprints"] = {name: base.sha256(args.source_root / name)
                                         for name in sources}
        stamp = uuid.uuid4().hex[:12]
        if not args.recovery_only:
            regression(args, report, "encfs-regression-" + stamp)
        if not args.regression_only:
            recovery(args, report, "encfs-power-" + stamp)
            negative_oracle(args, report, "encfs-negative-" + stamp)
        for name, artifact in report["artifacts"].items():
            base.require(base.sha256(Path(artifact["path"])) == artifact["sha256"],
                         f"Build artifact changed during test: {name}")
        report["status"] = ("passed" if not report.get("upstream_cmocka", {}).get("failed")
                            else "completed-with-upstream-failures")
    except BaseException as exc:
        report.update({"status": "failed", "error": str(exc),
                       "traceback": traceback.format_exc()})
    report["finished_at"] = base.now()
    save(args, report)
    print(json.dumps({"status": report["status"], "report": str(args.results_dir / "report.json")},
                     ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
