#!/usr/bin/env python3
"""Run real Vela EncFS acceptance tests in newly created emulator data disks.

Requires Python 3.9+ and pexpect. No test instance or image is reused from a
previous invocation. Plaintext corruption fixtures have their own disk. The
encrypted instance is reused for cold-boot verification, which never
provisions. Serial logs, actual image paths, hashes, and assertions are
retained on both success and failure.
"""

import argparse
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

try:
    import pexpect
except ImportError:
    pexpect = None


CONFIG = "goldfish-arm64-v8a-ap-encfs"
PROMPT = "encfs> "
CONTENT = (
    b"ENCFS_COURSE_2026_SECRET_PAYLOAD_7d64f98ac0e34251_"
    b"persistent-content-and-transparent-path-encryption"
)
SHANGHAI = timezone(timedelta(hours=8))


class TestFailure(RuntimeError):
    """A required observation was missing or contradicted the expected result."""


def now():
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_default():
    script = Path(__file__).resolve()
    for parent in script.parents:
        if (parent / "nuttx").is_dir() and (parent / "run-emulator.sh").is_file():
            return parent
    return script.parents[2]


def require(condition, message):
    if not condition:
        raise TestFailure(message)


def git_revision(path):
    try:
        revision = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.splitlines()
        return {"revision": revision, "dirty": bool(dirty), "changes": dirty}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"unavailable": str(exc)}


def validate_build(source, build):
    require((source / "run-emulator.sh").is_file(), "run-emulator.sh is missing")
    artifacts = {}
    for name in ("nuttx", "nuttx.bin", "vela_data.bin", "vela_system.bin", ".config"):
        path = build / name
        require(path.is_file() and path.stat().st_size > 0,
                f"Missing or empty build artifact: {path}")
        artifacts[name] = {
            "path": str(path), "size": path.stat().st_size, "sha256": sha256(path),
        }
    config = (build / ".config").read_text()
    for option in ("CONFIG_ENCFSMGR", "CONFIG_ENCFS_TESTING", "CONFIG_BLKCRYPT",
                   "CONFIG_FS_ENCPATHFS", "CONFIG_EXAMPLES_ENCFS_TEST",
                   "CONFIG_SYSTEM_ENCFS", "CONFIG_SCHED_WAITPID"):
        require(re.search(rf"^{option}=y$", config, re.M),
                f"Build configuration must enable {option}")
    require('CONFIG_NSH_PROMPT_STRING="encfs> "' in config,
            "Build must use CONFIG_NSH_PROMPT_STRING=\"encfs> \"")
    require("CONFIG_BOARDCTL_UNIQUEKEY_SIZE=32" in config,
            "Build must use a 32-byte board key")
    return artifacts


class Exfat:
    """Read allocated exFAT directory entries; never write or mount an image."""

    def __init__(self, data):
        self.data = data
        require(data[3:11] == b"EXFAT   ", "Plain test disk is not superfloppy exFAT")
        self.sector_size = 1 << data[108]
        self.cluster_size = self.sector_size * (1 << data[109])
        require(512 <= self.sector_size <= 4096, "Invalid exFAT sector size")
        require(self.cluster_size <= 32 * 1024 * 1024, "Invalid exFAT cluster size")
        self.fat_offset = self.u32(80) * self.sector_size
        self.heap_offset = self.u32(88) * self.sector_size
        self.cluster_count = self.u32(92)
        self.root_cluster = self.u32(96)
        self.cluster_limit = self.cluster_count + 2
        require(self.heap_offset + self.cluster_count * self.cluster_size <= len(data),
                "exFAT cluster heap extends beyond the actual image")

    def u32(self, offset):
        return struct.unpack_from("<I", self.data, offset)[0]

    def cluster(self, number):
        require(2 <= number < self.cluster_limit, f"Invalid exFAT cluster {number}")
        offset = self.heap_offset + (number - 2) * self.cluster_size
        return self.data[offset:offset + self.cluster_size]

    def directory_bytes(self, cluster, size=0, contiguous=False):
        if contiguous:
            count = math.ceil(size / self.cluster_size)
            require(0 < count <= self.cluster_count, "Invalid contiguous directory size")
            return b"".join(self.cluster(c) for c in range(cluster, cluster + count))
        chunks = []
        seen = set()
        while cluster < 0xfffffff8:
            require(cluster not in seen, "Cycle in exFAT directory FAT chain")
            seen.add(cluster)
            chunks.append(self.cluster(cluster))
            cluster = self.u32(self.fat_offset + cluster * 4)
            require(cluster >= 2, "Unallocated cluster in exFAT directory chain")
            require(len(chunks) <= 8192, "Unexpectedly large exFAT directory")
        return b"".join(chunks)

    def entries(self, cluster, size=0, contiguous=False):
        raw = self.directory_bytes(cluster, size, contiguous)
        entries = []
        offset = 0
        while offset + 32 <= len(raw):
            kind = raw[offset]
            if kind == 0:
                break
            if kind != 0x85:
                offset += 32
                continue
            secondary = raw[offset + 1]
            end = offset + (secondary + 1) * 32
            require(secondary >= 2 and end <= len(raw), "Invalid exFAT entry set")
            stream = raw[offset + 32:offset + 64]
            require(stream[0] == 0xc0, "Missing exFAT stream extension")
            name_length = stream[3]
            name = bytearray()
            for part in range(offset + 64, end, 32):
                if raw[part] == 0xc1:
                    name.extend(raw[part + 2:part + 32])
            require(len(name) >= name_length * 2, "Truncated exFAT filename")
            try:
                decoded = bytes(name[:name_length * 2]).decode("utf-16-le")
            except UnicodeDecodeError as exc:
                raise TestFailure("Invalid UTF-16 exFAT filename") from exc
            entries.append({
                "name": decoded,
                "directory": bool(struct.unpack_from("<H", raw, offset + 4)[0] & 0x10),
                "cluster": struct.unpack_from("<I", stream, 20)[0],
                "size": struct.unpack_from("<Q", stream, 24)[0],
                "contiguous": bool(stream[1] & 2),
            })
            offset = end
        return entries

    def path_entries(self):
        roots = self.entries(self.root_cluster)
        physical = next((entry for entry in roots if entry["name"] == ".p"), None)
        require(physical and physical["directory"], "Plain volume has no .p directory")
        paths = []

        def walk(entry, parent, depth):
            require(depth <= 16, "Unexpectedly deep physical directory tree")
            children = self.entries(entry["cluster"], entry["size"], entry["contiguous"])
            for child in children:
                require(child["name"].startswith("E1_"),
                        f"Unexpected plaintext or invalid physical name: {child['name']!r}")
                path = parent + "/" + child["name"]
                paths.append({"path": path, "directory": child["directory"]})
                if child["directory"]:
                    walk(child, path, depth + 1)

        walk(physical, ".p", 0)
        require(any(entry["directory"] for entry in paths),
                "No encoded physical directory was found")
        require(any(not entry["directory"] for entry in paths),
                "No encoded physical file was found")
        return {"root_names": [entry["name"] for entry in roots], "physical_paths": paths}


def inspect_image(path, encrypted, result=None):
    result = {} if result is None else result
    result.update({"path": str(path.resolve()), "encrypted": encrypted})
    require(path.is_file(), f"Actual emulator image is missing: {path}")
    result.update({"size": path.stat().st_size, "sha256": sha256(path)})
    require(result["size"] > 0, "Actual emulator image is empty")
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        observations = {
            "content_marker": data.find(CONTENT) >= 0,
            "secret_name_ascii": data.find(b"secret.txt") >= 0,
            "secret_name_utf16le": data.find("secret.txt".encode("utf-16-le")) >= 0,
            "encoded_prefix_utf16le": data.find("E1_".encode("utf-16-le")) >= 0,
            "plaintext_exfat_header": data[3:11] == b"EXFAT   ",
        }
        result["observations"] = observations
        if encrypted:
            require(not observations["content_marker"], "Encrypted image contains full plaintext marker")
            require(not observations["secret_name_ascii"] and not observations["secret_name_utf16le"],
                    "Encrypted image contains the specified plaintext filename")
            require(not observations["plaintext_exfat_header"],
                    "Encrypted image still exposes a plaintext exFAT header")
        else:
            require(observations["content_marker"], "Standalone image is missing known plaintext content")
            require(not observations["secret_name_ascii"] and not observations["secret_name_utf16le"],
                    "Standalone path layer exposed the logical filename")
            require(observations["encoded_prefix_utf16le"], "Standalone image has no encoded name prefix")
            result["exfat"] = Exfat(data).path_entries()
    return result


class Emulator:
    def __init__(self, args, instance, phase):
        self.args = args
        self.instance = instance
        self.phase = phase
        self.child = None
        self.log = None
        self.group = None

    def __enter__(self):
        args = self.args
        command = [str(args.source_root / "run-emulator.sh"), CONFIG,
                   "-keep", self.instance, "-no-window", "-no-audio",
                   "-gpu", "off", "-accel", "off"]
        env = os.environ.copy()
        env.update({"ANDROID_AVD_HOME": str(args.results_dir / "avd"),
                    "CUSTOM_AVD_SPACE": str(args.results_dir / "avd"),
                    "VELA_BUILD_DIR": str(args.build_dir)})
        self.phase.update({"instance": self.instance, "command": command,
                           "started_at": now(), "commands": []})
        self.log = (args.results_dir / f"{self.phase['name']}.serial.log").open("w", encoding="utf-8")
        self.phase["serial_log"] = str(Path(self.log.name).resolve())
        try:
            self.child = pexpect.spawn(command[0], command[1:], cwd=str(args.source_root),
                                       env=env, encoding="utf-8", codec_errors="replace",
                                       timeout=args.command_timeout, echo=False)
            self.child.logfile_read = self.log
            group = os.getpgid(self.child.pid)
            require(group != os.getpgrp(), "Emulator did not get an isolated process group")
            self.group = group
            self.child.expect_exact(PROMPT, timeout=args.boot_timeout)
            for name in ("nuttx", "nuttx.bin", "vela_data.bin", "vela_system.bin"):
                staged = args.source_root / "nuttx" / name
                require(staged.resolve() == (args.build_dir / name).resolve(),
                        f"Staged {name} does not use requested build directory")
            return self
        except BaseException:
            try:
                self.shutdown()
            except Exception as cleanup_error:
                self.phase["shutdown_error"] = str(cleanup_error)
            raise

    def command(self, command):
        record = {"command": command, "started_at": now()}
        self.phase["commands"].append(record)
        self.child.sendline(command)
        try:
            self.child.expect_exact(PROMPT, timeout=self.args.command_timeout)
        except (pexpect.EOF, pexpect.TIMEOUT) as exc:
            record["output_tail"] = self.child.before[-4000:]
            record["error"] = type(exc).__name__
            raise TestFailure(f"Guest did not finish {command!r}; see {self.log.name}") from exc
        output = self.child.before
        record["finished_at"] = now()
        record["output"] = output
        return output

    def test(self, mode):
        output = self.command(f"encfs_test {mode}")
        matches = re.findall(r"ENCFS_TEST_RESULT mode=(\w+) pass=(\d+) fail=(\d+)", output)
        require(len(matches) == 1 and matches[0][0] == mode,
                f"Guest did not emit the expected result for {mode}")
        passed, failed = map(int, matches[0][1:])
        self.phase["guest_result"] = {"mode": mode, "pass": passed, "fail": failed}
        stress = re.findall(r"ENCFS_STRESS_RESULT requested=(\d+) completed=(\d+) "
                            r"bytes=(\d+) elapsed_ns=(\d+)", output)
        if mode in ("standalone", "write"):
            require(len(stress) == 1, "Guest did not emit one stress result")
            requested, completed, count, elapsed = map(int, stress[0])
            self.phase["stress_result"] = {
                "requested": requested, "completed": completed,
                "bytes": count, "elapsed_ns": elapsed,
            }
            require(requested == 1000 and completed == requested and count > 0,
                    "Guest did not complete the required file regression")
        if mode == "write":
            restarts = re.findall(r"ENCFS_RESTART_RESULT requested=(\d+) completed=(\d+)", output)
            require(len(restarts) == 1, "Guest did not emit one lifecycle result")
            requested, completed = map(int, restarts[0])
            self.phase["restart_result"] = {"requested": requested, "completed": completed}
            require(requested == 100 and completed == requested,
                    "Guest did not complete the required lifecycle regression")
        require(passed > 0 and failed == 0, f"Guest {mode} failed: pass={passed}, fail={failed}")

    def encfs(self, action):
        output = self.command("encfs " + action)
        matches = re.findall(r"encfs: state=(\w+) result=(-?\d+)", output)
        require(len(matches) == 1 and int(matches[0][1]) == 0,
                f"Guest encfs {action} did not succeed")
        if action in ("stop", "provision --erase"):
            require(matches[0][0] == "STOPPED", f"encfs {action} did not leave STOPPED")

    def group_running(self):
        if self.group is None:
            return False
        proc = Path("/proc")
        if proc.is_dir():
            for entry in proc.iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                    if int(fields[2]) == self.group and fields[0] != "Z":
                        return True
                except (OSError, ValueError, IndexError):
                    continue
            return False
        try:
            os.killpg(self.group, 0)
            return True
        except ProcessLookupError:
            return False

    def signal_group(self, sig):
        if self.group is None:
            if self.child is not None:
                self.child.kill(sig)
            return
        try:
            os.killpg(self.group, sig)
        except ProcessLookupError:
            pass

    def shutdown(self):
        if self.child is None:
            if self.log:
                self.log.close()
            return
        child = self.child
        try:
            if self.group is None:
                child.terminate(force=True)
            else:
                self.signal_group(signal.SIGTERM)
            if child.isalive():
                try:
                    child.expect(pexpect.EOF, timeout=self.args.shutdown_timeout)
                except pexpect.TIMEOUT:
                    self.phase["forced_kill"] = True
                    self.signal_group(signal.SIGKILL)
                    child.expect(pexpect.EOF, timeout=10)
            child.close(force=False)
            deadline = time.monotonic() + self.args.shutdown_timeout
            while self.group_running() and time.monotonic() < deadline:
                time.sleep(0.1)
            if self.group_running():
                self.phase["forced_kill"] = True
                self.signal_group(signal.SIGKILL)
                raise TestFailure("Emulator descendants remained alive after EOF")
            self.phase["exit_status"] = child.exitstatus
            self.phase["exit_signal"] = child.signalstatus
            self.phase["finished_at"] = now()
        finally:
            if self.log:
                self.log.flush()
                self.log.close()
            self.child = None
        require(not self.phase.get("forced_kill"),
                "Emulator required SIGKILL; image checks cannot claim clean shutdown")

    def __exit__(self, exc_type, exc_value, exc_traceback):
        try:
            self.shutdown()
        except Exception as cleanup_error:
            self.phase["shutdown_error"] = str(cleanup_error)
            if exc_type is None:
                raise


def run_suite(args, report):
    suffix = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    plain_instance = "encfs-plain-" + suffix
    corruption_instance = "encfs-corrupt-" + suffix
    encrypted_instance = "encfs-crypt-" + suffix
    avd = args.results_dir / "avd"
    avd.mkdir()
    plain_image = avd / (plain_instance + ".vvd") / "vela_data.bin"
    corruption_image = avd / (corruption_instance + ".vvd") / "vela_data.bin"
    encrypted_image = avd / (encrypted_instance + ".vvd") / "vela_data.bin"
    require(not any(image.parent.exists() for image in
                    (plain_image, corruption_image, encrypted_image)),
            "Dedicated test instances must be new")
    phases = [("standalone", plain_instance, "standalone", plain_image),
              ("corruption", corruption_instance, "corruption", corruption_image),
              ("encrypted-write", encrypted_instance, "write", encrypted_image)]
    for index in range(args.reboot_count):
        name = "encrypted-reboot" if index == 0 else f"encrypted-reboot-{index + 1:02d}"
        phases.append((name, encrypted_instance, "verify", encrypted_image))
    for name, instance, mode, image in phases:
        phase = {"name": name, "status": "running", "image": {"path": str(image)},
                 "registry_path": str(Path.home() / ".vela" / (instance + ".ini"))}
        report["phases"].append(phase)
        print(f"[{now()}] {name}: booting {instance}", flush=True)
        if mode == "verify":
            require(encrypted_image.is_file(), "Persistence image disappeared before reboot")
            phase["before_reboot_sha256"] = sha256(encrypted_image)
            require(phase["before_reboot_sha256"] == report["phases"][-2]["image"]["sha256"],
                    "Persistence image changed between the two stopped emulator runs")
        with Emulator(args, instance, phase) as emulator:
            if mode == "write":
                emulator.encfs("provision --erase")
            emulator.test(mode)
            emulator.encfs("stop")
        if mode == "corruption":
            phase["image"].update({"path": str(image.resolve()),
                                   "encrypted": False, "size": image.stat().st_size,
                                   "sha256": sha256(image),
                                   "purpose": "isolated malformed-metadata fixtures"})
        else:
            inspect_image(image, encrypted=mode != "standalone", result=phase["image"])
        phase["status"] = "passed"
        print(f"[{now()}] {name}: passed; image {phase['image']['sha256']}", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=source_default())
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--boot-timeout", type=float, default=240)
    parser.add_argument("--command-timeout", type=float, default=240)
    parser.add_argument("--shutdown-timeout", type=float, default=30)
    parser.add_argument("--reboot-count", type=int, default=3,
                        help="independent cold boots of the same encrypted image (1..20)")
    args = parser.parse_args(argv)
    if not 1 <= args.reboot_count <= 20:
        parser.error("--reboot-count must be between 1 and 20")
    args.source_root = args.source_root.resolve()
    args.build_dir = (args.build_dir or args.source_root / "cmake_out" / ("vela_" + CONFIG)).resolve()
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    args.results_dir = (args.results_dir or args.source_root / "encfs-results" / stamp).resolve()
    for name in ("boot_timeout", "command_timeout", "shutdown_timeout"):
        parser_value = getattr(args, name)
        if parser_value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.results_dir.exists() and (not args.results_dir.is_dir() or
                                      any(args.results_dir.iterdir())):
        print(f"Results directory must be empty: {args.results_dir}", file=sys.stderr)
        return 2
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report = {"started_at": now(), "status": "running", "source_root": str(args.source_root),
              "build_dir": str(args.build_dir), "results_dir": str(args.results_dir),
              "phases": [], "limitations": [
                  "Raw-image searches check specified complete markers and names only.",
                  "XTS does not authenticate arbitrary sector contents.",
                  "Unused blank sectors are not claimed to have been encrypted.",
                  "The board key in the QEMU demonstration is deterministic test material.",
              ]}
    exit_code = 1
    try:
        require(pexpect is not None, "Python dependency pexpect is missing")
        report["build_artifacts"] = validate_build(args.source_root, args.build_dir)
        report["source_revisions"] = {
            name: git_revision(args.source_root / relative) for name, relative in (
                ("nuttx", "nuttx"), ("apps", "apps"),
                ("vendor_openvela", "vendor/openvela"), ("manifests", ".repo/manifests"),
            )
        }
        run_suite(args, report)
        report["status"] = "passed"
        exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        if report["phases"] and report["phases"][-1]["status"] == "running":
            report["phases"][-1]["status"] = "failed"
        print(f"EncFS acceptance failed: {exc}", file=sys.stderr, flush=True)
        if isinstance(exc, KeyboardInterrupt):
            exit_code = 130
    finally:
        for phase in report["phases"]:
            image = phase.get("image", {})
            image_path = Path(image.get("path", ""))
            if image_path.is_file() and "sha256" not in image:
                try:
                    image.update({"size": image_path.stat().st_size,
                                  "sha256": sha256(image_path)})
                    if phase.get("shutdown_error"):
                        image["hash_may_be_unstable"] = True
                except OSError as image_error:
                    image["snapshot_error"] = str(image_error)
        report["finished_at"] = now()
        path = args.results_dir / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Report: {path}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
