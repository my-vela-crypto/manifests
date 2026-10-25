#!/usr/bin/env python3
"""Independently audit immutable keywrap guest evidence; never start a guest."""
import argparse
import ctypes as C
import ctypes.util
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import re
import struct
import subprocess

from PIL import Image, ImageChops


ROOT = next((parent for parent in Path(__file__).resolve().parents
             if (parent / "nuttx").is_dir() and
             (parent / "run-emulator.sh").is_file()), Path.cwd())
MARKER = "ENCFS_WRAP_UTF8_20261024_7df925ca"
FILE_LENGTHS = (1, 511, 512, 513, 1023, 4095, 4096, 4097, 8191, 16385, 65537)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fields(line):
    return {name: int(value) for name, value in re.findall(r"(\w+)=(\d+)", line)}


def payload(counts):
    written = verified = 0
    for worker, iterations in enumerate(counts):
        # The independent 11-length / 3-offset schedules repeat every 33
        # transactions. Bound the audit cost even for 2,880 24h samples.
        cycle = []
        for i in range(33):
            length = FILE_LENGTHS[(i + worker) % len(FILE_LENGTHS)]
            offset = (0, 495, 4095)[i % 3]
            if offset >= length:
                offset = 0
            cycle.append((length + min(length - offset, 67), length * 2))
        full, remainder = divmod(iterations, len(cycle))
        written += full * sum(x[0] for x in cycle) + sum(x[0] for x in cycle[:remainder])
        verified += full * sum(x[1] for x in cycle) + sum(x[1] for x in cycle[:remainder])
    return written, verified


def authenticate_openssl(slot, master):
    """Independent system OpenSSL; returns only the plaintext length."""
    lib = C.CDLL(ctypes.util.find_library("crypto"))
    ptr = C.c_void_p
    integer = C.c_int
    for name, args, result in [
        ("EVP_CIPHER_fetch", [ptr, C.c_char_p, C.c_char_p], ptr),
        ("EVP_CIPHER_free", [ptr], None),
        ("EVP_CIPHER_CTX_new", [], ptr),
        ("EVP_CIPHER_CTX_free", [ptr], None),
        ("EVP_DecryptInit_ex2", [ptr, ptr, ptr, ptr, ptr], integer),
        ("EVP_DecryptUpdate", [ptr, ptr, C.POINTER(integer), ptr, integer], integer),
        ("EVP_DecryptFinal_ex", [ptr, ptr, C.POINTER(integer)], integer),
        ("EVP_CIPHER_CTX_ctrl", [ptr, integer, integer, ptr], integer),
    ]:
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    header = slot[:32]
    extract = hmac.digest(b"encfs-keyslot-v1", master, hashlib.sha256)
    wrapping = hmac.digest(extract, header + b"\1", hashlib.sha256)
    cipher = lib.EVP_CIPHER_fetch(None, b"AES-128-SIV", None)
    context = lib.EVP_CIPHER_CTX_new()
    assert cipher and context
    key = C.create_string_buffer(wrapping)
    tag = C.create_string_buffer(slot[32:48])
    ad = C.create_string_buffer(header)
    ciphertext = C.create_string_buffer(slot[48:])
    out = C.create_string_buffer(80)
    count = integer()
    try:
        assert lib.EVP_DecryptInit_ex2(context, cipher, key, None, None) == 1
        assert lib.EVP_CIPHER_CTX_ctrl(context, 0x11, 16, tag) == 1
        assert lib.EVP_DecryptUpdate(context, None, C.byref(count), ad, 32) == 1
        assert lib.EVP_DecryptUpdate(context, out, C.byref(count), ciphertext, 64) == 1
        size = count.value
        assert lib.EVP_DecryptFinal_ex(context, C.byref(out, size), C.byref(count)) == 1
        return size + count.value
    finally:
        C.memset(C.addressof(out), 0, C.sizeof(out))
        C.memset(C.addressof(key), 0, C.sizeof(key))
        lib.EVP_CIPHER_CTX_free(context)
        lib.EVP_CIPHER_free(cipher)


def changed(left, right, crop=None):
    with Image.open(left) as a, Image.open(right) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        if crop:
            a, b = a.crop(crop), b.crop(crop)
        return sum(pixel != (0, 0, 0) for pixel in ImageChops.difference(a, b).getdata())


def audit(directory, output, require_strict, input_receipt=None, frozen_root=None):
    checks = []
    artifacts = {}

    def check(name, condition, details=None):
        checks.append({"name": name, "passed": bool(condition), "details": details})
        if not condition:
            raise AssertionError(name)

    def artifact(path):
        path = Path(path)
        data = {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": sha(path)}
        artifacts[str(path.resolve())] = data
        return data

    report_path = directory / "report.json"
    report = json.loads(report_path.read_text())
    artifact(report_path)
    check("report completed and every phase passed", report["status"] == "passed" and
          all(p["status"] == "passed" for p in report["phases"]))
    phases = {p["name"]: p for p in report["phases"]}
    serials = {}
    for name, phase in phases.items():
        source = Path(phase["serial_log"])
        artifact(source)
        serials[name] = normalized(source.read_bytes().decode("utf-8"))
        for command in phase["commands"]:
            check("raw serial supports command: " + name + ": " + command["command"],
                  normalized(command.get("output", "")) in serials[name])
        check("no guest test failures: " + name,
              "[FAIL]" not in serials[name] and
              "ENCFS_LONG_STRESS_ERROR" not in serials[name])
    check("legacy direct-derived volume rejects rotation",
          "state=READY result=-138" in serials["legacy-rekey-rejection"])

    stress = phases["gui-wrapped-key-stress"]
    serial = serials[stress["name"]]
    finals = re.findall(r"ENCFS_LONG_STRESS_RESULT ([^\n]+)", serial)
    check("one final stress result", len(finals) == 1)
    final = fields(finals[0])
    progress = [fields(x) for x in re.findall(r"ENCFS_LONG_STRESS_PROGRESS ([^\n]+)", serial)]
    check("report final statistics equal independently parsed serial",
          final == stress["long_stress_result"])
    check("report progress equals independently parsed serial",
          progress == stress["long_stress_progress"])
    check("both workers and guest met requested real duration",
          final["requested_seconds"] == report["requested_stress_seconds"] and
          all(final[k] >= final["requested_seconds"] * 1000 for k in
              ("elapsed_ms", "worker0_elapsed_ms", "worker1_elapsed_ms")) and
          final["workers"] == 2 and final["completed"] == 1 and final["errors"] == 0)
    for i, sample in enumerate(progress + [final]):
        counts = sample["worker0_iterations"], sample["worker1_iterations"]
        expected = payload(counts)
        check(f"independent payload oracle sample {i}",
              sample["iterations"] == sum(counts) and
              (sample["written_bytes"], sample["verified_bytes"]) == expected and
              sample["errors"] == 0)

    start = serial.index("ENCFS_LONG_STRESS_START ")
    rotate = serial.index("encfs rekey 2", start)
    ack = serial.index("wrapped=1 master_id=2 generation=3", rotate)
    complete = serial.index("ENCFS_LONG_STRESS_RESULT ", ack)
    check("online rotation command and acknowledgment lie inside workload lifecycle",
          start < rotate < ack < complete)
    after = [fields(x) for x in re.findall(
        r"ENCFS_LONG_STRESS_PROGRESS ([^\n]+)", serial[ack:complete])]
    strict = len(after) >= 2 and all(
        after[1][f"worker{i}_iterations"] > after[0][f"worker{i}_iterations"]
        for i in range(2))
    if require_strict:
        check("two post-ack progress samples show both workers continue", strict, after)

    images = {frame["label"]: frame for frame in stress["frames"]}
    for label, frame in images.items():
        png = Path(frame["path"])
        check("PNG SHA and dimensions: " + label,
              artifact(png)["sha256"] == frame["sha256"] and
              Image.open(png).size == (1280, 800))
        raw = Path(frame["source_bmp"]).read_bytes()
        artifact(frame["source_bmp"])
        offset = struct.unpack_from("<I", raw, 10)[0]
        width, height = struct.unpack_from("<ii", raw, 18)
        bits = struct.unpack_from("<H", raw, 28)[0]
        check("real framebuffer BMP dimensions and RGB565: " + label,
              width == 1280 and abs(height) == 800 and bits == 16)
        decoded = Image.frombytes("RGB", (width, abs(height)), raw[offset:],
                                  "raw", "BGR;16", ((width * 2 + 3) // 4) * 4,
                                  -1 if height > 0 else 1)
        with Image.open(png) as rendered:
            check("PNG exactly represents raw guest framebuffer: " + label,
                  ImageChops.difference(decoded, rendered.convert("RGB")).getbbox() is None)
        if label.startswith("stress-"):
            page = "analytics" if label.endswith("analytics") else "shop"
            check("stress selected-tab header exact match: " + label,
                  changed(png, images[page]["path"], (680, 0, 1280, 70)) == 0)
    for name, left, right, crop in [
        ("profile_to_analytics", "profile", "analytics", None),
        ("analytics_to_shop", "analytics", "shop", None),
        ("shop_checkbox", "shop", "checkbox-toggled", (668, 340, 705, 375)),
        ("profile_slider", "slider-before", "slider-after", (670, 380, 1240, 425)),
    ]:
        check("touch pixel count independently recomputed: " + name,
              changed(images[left]["path"], images[right]["path"], crop) ==
              stress["touch_changed_pixels"][name])

    cut = phases["keyslot-cut-before-commit"]
    cutserial = serials[cut["name"]]
    pause = "ENCFS_KEYSLOT_PAUSED before_atomic_commit ms=10000"
    check("exact post-fsync/readback pre-rename pause appears once in raw serial",
          cutserial.count(pause) == 1 and pause == cut["observed_pause_marker"])
    check("intentional interruption was SIGKILL with old committed slot preserved",
          cut["intentional_interruption"]["signal"] == "SIGKILL" and
          cut["intentional_interruption"]["clean_shutdown"] is False and
          cut["intentional_interruption"]["committed_slot_unchanged"] and
          cut["intentional_interruption"]["staged_slot_bytes"] == 112)
    check("cut serial ends at pause before successful replacement acknowledgment",
          "generation=4" not in cutserial[cutserial.index(pause):])
    recovery = phases["keyslot-cut-recovery"]
    recovery_serial = serials[recovery["name"]]
    labels = re.findall(r"\[PASS\] ([^\n]+)", recovery_serial)
    helper = re.findall(r"ENCFS_REKEY_RESULT pass=(\d+) fail=(\d+) next_master_id=(\d+)", recovery_serial)
    check("live open/dup/DIR helper completed exact 27/0 plus main 28/0",
          helper == [("27", "0", "3")] and len(labels) == 28 and
          "ENCFS_TEST_RESULT mode=rekey pass=28 fail=0" in recovery_serial)
    for text in ("same descriptor reads unchanged data after rekey",
                 "same open DIR returns logical names after rekey",
                 "same DIR rewinds and reads after rekey",
                 "append through duplicate after rekey",
                 "restart using new provider master"):
        check("live handle evidence: " + text, text in labels)
    cold = phases["recovered-wrapper-coldboot"]
    coldserial = serials[cold["name"]]
    check("separate coldboot starts id 3 generation 4 and reads Chinese/emoji path",
          "NuttShell (NSH)" in coldserial and
          "wrapped=1 master_id=3 generation=4" in coldserial and
          MARKER in coldserial and cold["only_current_master_available"] and
          cold["instance"] == recovery["instance"])
    check("later boots never provision existing data", all(
        not any(c["command"] == "encfs provision --erase" for c in phase["commands"])
        for name, phase in phases.items()
        if name not in ("legacy-rekey-rejection", "gui-wrapped-key-stress")))

    image = Path(cold["image"]["path"])
    check("final disk equals both recovery and final readonly coldboot hashes",
          artifact(image)["sha256"] == cold["image"]["sha256"] == recovery["image"]["sha256"])
    keydir = image.parent / "share/keys"
    slotpath = keydir / "encfs.keyslot"
    slot = slotpath.read_bytes()
    check("final slot matches recovered and staged wrapper hashes",
          artifact(slotpath)["sha256"] == recovery["final_slot_sha256"] ==
          cut["intentional_interruption"]["staged_slot_sha256"])
    check("current slot format id 3 generation 4", len(slot) == 112 and
          slot[:8] == b"ENCKEY01" and int.from_bytes(slot[8:12], "little") == 1 and
          int.from_bytes(slot[12:16], "little") == 3 and
          int.from_bytes(slot[16:24], "little") == 4 and slot[24:32] == bytes(8))
    current_master = keydir / "master-00000003.bin"
    # Hash/length/path only. Never serialize raw master or decrypted DEK.
    artifact(current_master)
    check("only current master file exists and staging file recovered",
          sorted(p.name for p in keydir.iterdir()) ==
          ["encfs.keyslot", "master-00000003.bin"] and
          current_master.stat().st_size == 32)
    check("independent OpenSSL authenticates final 64-byte data-key wrapper",
          authenticate_openssl(slot, current_master.read_bytes()) == 64)

    analysispath = directory / "analysis/analysis.json"
    check("independent original-serial stress analysis exists", analysispath.is_file())
    if analysispath.is_file():
        analysis = json.loads(analysispath.read_text())
        artifact(analysispath)
        check("analysis input hashes bind this original report and serial",
              analysis["status"] == "passed" and
              analysis["input_report"]["sha256"] == sha(report_path) and
              analysis["input_serial"]["sha256"] == sha(stress["serial_log"]))
        for plot in analysis["plots"]:
            check("analysis plot hash: " + Path(plot["path"]).name,
                  artifact(plot["path"])["sha256"] == plot["sha256"])

    frozenroot = frozen_root or ROOT / "encfs-results/endurance-24h-wrapped-20261024"
    receiptpath = frozenroot / "input-receipt.json"
    receipt = json.loads(receiptpath.read_text())
    artifact(receiptpath)
    frozen_comparison = []
    for relative, metadata in receipt["files"].items():
        path = frozenroot / relative
        observed = artifact(path)
        check("frozen input unchanged: " + relative,
              observed["sha256"] == metadata["sha256"] and
              observed["size"] == metadata["size"])
        if "/firmware/" in relative:
            check("guest-tested firmware equals frozen wrapped firmware: " + path.name,
                  metadata["sha256"] == report["build_artifacts"][path.name]["sha256"])
        else:
            current = ROOT / ".repo/manifests/tools" / path.name
            frozen_comparison.append({"tool": path.name,
                                      "frozen_sha256": metadata["sha256"],
                                      "current_sha256": sha(current),
                                      "identical": sha(current) == metadata["sha256"]})
    observed_input_receipt = None
    if input_receipt is not None:
        observed = json.loads(input_receipt.read_text())
        observed_input_receipt = artifact(input_receipt)
        observed_input_receipt["captured_at"] = observed["captured_at"]
        observed_input_receipt["scope"] = observed["scope"]
        for relative, metadata in observed["files"].items():
            current = artifact(ROOT / relative)
            check("current observed input snapshot unchanged: " + relative,
                  current["sha256"] == metadata["sha256"] and
                  current["size"] == metadata["size"])
        check("input observation falls within runner execution",
              datetime.fromisoformat(report["started_at"]) <=
              datetime.fromisoformat(observed["captured_at"]) <=
              datetime.fromisoformat(report["finished_at"]))
    result = {
        "status": "passed" if strict else "passed_with_limitations",
        "audited_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "auditor_script_sha256": sha(Path(__file__)),
        "input_report": str(report_path.resolve()),
        "input_report_sha256": sha(report_path),
        "checks_passed": len(checks), "checks": checks,
        "stress": final, "post_rekey_acknowledgment_progress_samples": after,
        "strict_both_workers_continue_after_rotation": strict,
        "frozen_tools_vs_current": frozen_comparison,
        "frozen_receipt_source_revisions": receipt["source_revisions"],
        "observed_input_receipt": observed_input_receipt,
        "recorded_source_revisions": report["source_revisions"],
        "provenance_boundary": (
            "A dirty manifest HEAD recorded in a guest report does not alone identify "
            "the tested runner/tool bytes. The frozen input receipt identifies its "
            "own immutable tools and firmware by hash. A separate observed-input "
            "snapshot, when supplied, retains its actual capture timing. Current "
            "workspace tool differences are reported instead of silently equated."),
        "qualified_claims": [] if strict else [
            "The old 60s report's final counters exceed a 30s pre-rotation sample; "
            "that does not establish new I/O after the near-completion rekey. "
            "Only one post-acknowledgment cleanup sample is present."],
        "not_verified": [
            "Physical media power-loss durability or malicious-host rollback resistance",
            "Hardware or TEE key protection (host files are simulation fixtures)",
            "Global allocator samples do not prove zero leaks or isolate EncFS allocations",
            "Legacy-volume migration or data-key revocation using historical slots",
            "Exhaustive GUI controls beyond the recorded widgets interactions",
        ],
        "checked_artifacts": list(artifacts.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in
                      ("status", "checks_passed", "strict_both_workers_continue_after_rotation")},
                     ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--frozen-input-root", type=Path)
    parser.add_argument("--require-strict", "--require-post-ack-progress",
                        dest="require_strict", action="store_true")
    parser.add_argument("--input-receipt", type=Path)
    args = parser.parse_args()
    ROOT = args.workspace.resolve()
    directory = args.results_dir or args.directory
    output = ((args.output_dir / "keywrap-independent-audit.json")
              if args.output_dir else args.output)
    if directory is None or output is None:
        parser.error("provide --results-dir and --output-dir (or the two positional paths)")
    audit(directory.resolve(), output.resolve(), args.require_strict,
          args.input_receipt.resolve() if args.input_receipt else None,
          args.frozen_input_root.resolve() if args.frozen_input_root else None)
