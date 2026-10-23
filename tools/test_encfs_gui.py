#!/usr/bin/env python3
"""Exercise test-key CLI, all four enabled LVGL demos, and timed EncFS stress.

Uses the full GUI configuration and real guest framebuffer/input tools. Every
image is a new dedicated emulator instance. The manual-key and board-key data
disks are separate. No existing development disk is formatted or reused.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time
import traceback
import uuid

from PIL import Image, ImageChops, ImageStat

from test_encfs import (Emulator, TestFailure, git_revision, inspect_image,
                        now, require, sha256, source_default, validate_build)


KEY_A = bytes(range(32)).hex()  # Public, deliberately non-secret test fixture.
KEY_B = bytes(range(32, 64)).hex()
KEY_MARKER = "ENCFS_MANUAL_TEST_KEY_CONTENT_20261023_683d72c1"


def save_report(args, report):
    report["updated_at"] = now()
    path = args.results_dir / "report.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def expect_encfs(emulator, action, *, state=None, result=0):
    output = emulator.command("encfs " + action)
    matches = re.findall(r"encfs: state=(\w+) result=(-?\d+)", output)
    require(len(matches) == 1, f"Missing unique encfs result for {action}")
    actual_state, actual_result = matches[0][0], int(matches[0][1])
    if result is None:
        require(actual_result < 0, f"Expected rejection: encfs {action}")
    else:
        require(actual_result == result, f"Wrong result for encfs {action}: {actual_result}")
    if state:
        require(actual_state == state, f"Wrong state for encfs {action}: {actual_state}")
    return actual_result


def manual_keys(emulator, cold_boot=False):
    expect_encfs(emulator, "stop", state="STOPPED")
    if not cold_boot:
        expect_encfs(emulator, "testkey none", state="STOPPED")
        expect_encfs(emulator, "start", state="STOPPED", result=None)
        expect_encfs(emulator, "testkey 1234", state="STOPPED", result=-22)
        expect_encfs(emulator, "testkey " + "z" * 64, state="STOPPED", result=-22)
    expect_encfs(emulator, "testkey " + KEY_A, state="STOPPED")
    if not cold_boot:
        expect_encfs(emulator, "provision --erase", state="STOPPED")
    expect_encfs(emulator, "start", state="READY")
    if not cold_boot:
        emulator.command("echo " + KEY_MARKER + " > /data/manual-key.txt")
    require(KEY_MARKER in emulator.command("cat /data/manual-key.txt"),
            "Manually selected test key did not recover the expected content")
    if not cold_boot:
        for key in (KEY_B, "board", "none"):
            expect_encfs(emulator, "testkey " + key, state="READY", result=-16)
        expect_encfs(emulator, "stop", state="STOPPED")
        expect_encfs(emulator, "testkey " + KEY_B, state="STOPPED")
        expect_encfs(emulator, "start", state="STOPPED", result=None)
        # Invalid input must not replace the current A source.
        expect_encfs(emulator, "testkey " + KEY_A, state="STOPPED")
        expect_encfs(emulator, "testkey " + "0" * 63, state="STOPPED", result=-22)
        expect_encfs(emulator, "start", state="READY")
        require(KEY_MARKER in emulator.command("cat /data/manual-key.txt"),
                "Invalid key input changed the previously selected key")
        expect_encfs(emulator, "stop", state="STOPPED")
        expect_encfs(emulator, "testkey board", state="STOPPED")
        expect_encfs(emulator, "start", state="STOPPED", result=None)
        expect_encfs(emulator, "testkey none", state="STOPPED")
        expect_encfs(emulator, "start", state="STOPPED", result=None)
        expect_encfs(emulator, "testkey " + KEY_A, state="STOPPED")
        expect_encfs(emulator, "start", state="READY")
        require(KEY_MARKER in emulator.command("cat /data/manual-key.txt"),
                "Wrong/missing keys changed the original encrypted content")
    expect_encfs(emulator, "stop", state="STOPPED")


def capture(args, emulator, phase, label):
    # Keep guest commands below the upstream NSH 80-character line limit.
    basename = hashlib.sha256(phase["name"].encode()).hexdigest()[:6] + "-" + label
    path = args.results_dir / "avd" / (emulator.instance + ".vvd") / "share" / (basename + ".bmp")
    require(not path.exists(), f"Capture already exists: {path}")
    output = emulator.command(f"fbcapture -d /dev/fb0 -o /share/{basename}.bmp")
    require("Success" in output and path.is_file(), f"Framebuffer capture failed: {label}")
    with Image.open(path) as frame:
        frame.load()
        require(frame.size == (1280, 800), f"Unexpected framebuffer size: {frame.size}")
        # The bundled fbcapture writes a BI_RGB header even for RGB565.
        # Decode the preserved pixel bytes using the configured real format;
        # a generic BMP reader would incorrectly interpret them as RGB555.
        if frame.mode == "RGB" and frame.info.get("compression", 0) == 0:
            raw = path.read_bytes()
            bits = struct.unpack_from("<H", raw, 28)[0]
            offset = struct.unpack_from("<I", raw, 10)[0]
            if bits == 16:
                stride = ((frame.width * 2 + 3) // 4) * 4
                rgb = Image.frombytes("RGB", frame.size, raw[offset:], "raw", "BGR;16", stride, -1)
            else:
                rgb = frame.convert("RGB")
        else:
            rgb = frame.convert("RGB")
        extrema = rgb.getextrema()
        require(any(high - low > 50 for low, high in extrema),
                f"Framebuffer is blank or nearly uniform: {label}")
        png = args.results_dir / "frames" / (phase["name"] + "-" + label + ".png")
        png.parent.mkdir(exist_ok=True)
        rgb.save(png)
        record = {"label": label, "path": str(png), "source_bmp": str(path),
                  "width": 1280, "height": 800, "sha256": sha256(png),
                  "mean_rgb": ImageStat.Stat(rgb).mean, "captured_at": now()}
        record["pixel_format"] = "RGB565 (decoded explicitly from guest capture)"
    phase.setdefault("frames", []).append(record)
    return png


def assert_frame_changed(before, after, label, crop=None, minimum=1000):
    with Image.open(before) as left, Image.open(after) as right:
        if crop:
            left, right = left.crop(crop), right.crop(crop)
        difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        # A real panel update must alter a material number of pixels.
        changed = sum(value != (0, 0, 0) for value in difference.getdata())
        require(changed > minimum, f"Touch did not change the rendered panel: {label} ({changed})")
        return changed


def start_demo(args, emulator, phase, demo):
    emulator.command("lvgldemo " + demo + " &")
    time.sleep(3)
    ps = emulator.command("ps")
    require("lvgldemo" in ps, f"GUI task missing for {demo}")
    return capture(args, emulator, phase, "initial")


def widgets_interactions(args, emulator, phase):
    emulator.command("input tap 740 35")
    time.sleep(1)
    profile = capture(args, emulator, phase, "profile")
    emulator.command("input tap 950 35")
    time.sleep(1)
    analytics = capture(args, emulator, phase, "analytics")
    emulator.command("input tap 1160 35")
    time.sleep(1)
    shop = capture(args, emulator, phase, "shop")
    phase["touch_changed_pixels"] = {
        "profile_to_analytics": assert_frame_changed(profile, analytics, "Analytics tab"),
        "analytics_to_shop": assert_frame_changed(analytics, shop, "Shop tab"),
    }
    emulator.command("input tap 683 357")
    time.sleep(1)
    toggled = capture(args, emulator, phase, "checkbox-toggled")
    phase["touch_changed_pixels"]["shop_checkbox"] = assert_frame_changed(
        shop, toggled, "Shop checkbox", crop=(668, 340, 705, 375), minimum=100)
    emulator.command("input tap 740 35")
    time.sleep(1)
    before = capture(args, emulator, phase, "slider-before")
    emulator.command("input swipe 685 402 1050 402 400")
    time.sleep(1)
    after = capture(args, emulator, phase, "slider-after")
    phase["touch_changed_pixels"]["profile_slider"] = assert_frame_changed(
        before, after, "Profile slider", crop=(670, 380, 1240, 425), minimum=1000)


def keypad_interactions(args, emulator, phase, initial):
    # Dismiss the actual welcome modal before exercising underlying controls.
    emulator.command("input tap 640 445")
    time.sleep(1)
    dismissed = capture(args, emulator, phase, "modal-dismissed")
    checks = {"dismiss_modal": assert_frame_changed(initial, dismissed, "Keypad welcome modal")}
    emulator.command("input tap 597 707")
    time.sleep(1)
    checked = capture(args, emulator, phase, "checkbox")
    checks["checkbox"] = assert_frame_changed(dismissed, checked, "Keypad checkbox",
                                               crop=(580, 690, 620, 725), minimum=100)
    emulator.command("input swipe 510 743 730 743 400")
    time.sleep(1)
    slider = capture(args, emulator, phase, "slider")
    checks["slider"] = assert_frame_changed(checked, slider, "Keypad slider",
                                             crop=(500, 725, 780, 765), minimum=1000)
    emulator.command("input tap 960 32")
    time.sleep(0.3)
    emulator.command("input drag 960 32 960 32 200")
    time.sleep(1)
    text = capture(args, emulator, phase, "text-tab")
    checks["text_tab"] = assert_frame_changed(slider, text, "Keypad text-input tab")
    phase["touch_changed_pixels"] = checks


def long_stress(args, emulator, phase, report):
    start = time.monotonic()
    emulator.command(f"encfs_test stress {args.stress_seconds} &")
    phase["stress_host_started_at"] = now()
    phase["checkpoints"] = []
    save_report(args, report)
    deadline = start + args.stress_seconds + 120
    index = 0
    last_progress_count = 0
    last_progress_host = start
    previous_workers = None
    logpath = Path(emulator.log.name)
    while time.monotonic() < deadline:
        # Keep the NSH responsive and exercise the GUI while both I/O workers
        # are active. Serial is continuously drained by these commands.
        time.sleep(min(30, max(1, args.stress_seconds - (time.monotonic() - start))))
        index += 1
        output = emulator.command("encfs status", timeout=30)
        require("state=READY result=0" in output, "Encrypted mount lost READY during stress")
        tasks = emulator.command("ps", timeout=30)
        require("lvgldemo" in tasks, "GUI task exited during stress")
        free = emulator.command("free", timeout=30)
        emulator.log.flush()
        serial = logpath.read_text(errors="replace")
        matches = re.findall(r"ENCFS_LONG_STRESS_RESULT ([^\r\n]+)", serial)
        progress_lines = re.findall(r"ENCFS_LONG_STRESS_PROGRESS ([^\r\n]+)", serial)
        if len(progress_lines) > last_progress_count:
            for line in progress_lines[last_progress_count:]:
                values = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", line)}
                current = (values.get("worker0_iterations", 0), values.get("worker1_iterations", 0))
                if previous_workers is not None and not matches:
                    require(all(a > b for a, b in zip(current, previous_workers)),
                            "An I/O worker stopped advancing between progress windows")
                previous_workers = current
            last_progress_count = len(progress_lines)
            last_progress_host = time.monotonic()
        require(time.monotonic() - last_progress_host < 95 or matches,
                "No fresh I/O progress for 95 seconds")
        checkpoint = {"elapsed_host_seconds": round(time.monotonic() - start, 3),
                      "checked_at": now(), "memory_output": free}
        if index == 1 or index % 10 == 0 or matches:
            # Alternate known tabs so each capture also verifies a live input
            # path, rather than merely checking that a GUI task still exists.
            emulator.command("input tap 950 35")
            time.sleep(0.4)
            one = capture(args, emulator, phase, f"stress-{index:03d}-analytics")
            emulator.command("input tap 1160 35")
            time.sleep(0.4)
            two = capture(args, emulator, phase, f"stress-{index:03d}-shop")
            checkpoint["touch_changed_pixels"] = assert_frame_changed(one, two, "stress tabs")
        phase["checkpoints"].append(checkpoint)
        save_report(args, report)
        if matches:
            require(len(matches) == 1, "Duplicate timed stress results")
            values = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", matches[0])}
            phase["long_stress_result"] = values
            phase["long_stress_progress"] = [
                {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", line)}
                for line in progress_lines]
            require(values.get("requested_seconds") == args.stress_seconds and
                    values.get("elapsed_ms", 0) >= args.stress_seconds * 1000 and
                    values.get("worker0_elapsed_ms", 0) >= args.stress_seconds * 1000 and
                    values.get("worker1_elapsed_ms", 0) >= args.stress_seconds * 1000 and
                    time.monotonic() - start >= args.stress_seconds - 2 and
                    values.get("workers") == 2 and values.get("errors") == 0 and
                    values.get("completed") == 1 and
                    values.get("worker0_iterations", 0) > 0 and
                    values.get("worker1_iterations", 0) > 0 and
                    values.get("written_bytes", 0) > 0 and
                    values.get("verified_bytes", 0) > 0,
                    f"Timed stress did not satisfy duration/data checks: {values}")
            require("ENCFS_TEST_RESULT mode=stress pass=1 fail=0" in serial,
                    "Timed stress application did not complete successfully")
            phase["stress_host_elapsed_seconds"] = round(time.monotonic() - start, 3)
            return
        require("ENCFS_LONG_STRESS_ERROR" not in serial, "Timed I/O worker reported an error")
        print(f"[{now()}] GUI + encrypted stress: {int(time.monotonic() - start)} / "
              f"{args.stress_seconds} seconds", flush=True)
    raise TestFailure("Timed stress result did not arrive before the deadline")


def run(args, report):
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    manual = "encfs-manual-" + stamp
    gui = "encfs-gui-" + stamp
    (args.results_dir / "avd").mkdir()
    phases = [("manual-key", manual, None), ("manual-key-coldboot", manual, None),
              ("gui-music", gui, "music"), ("gui-keypad", gui, "keypad_encoder"),
              ("gui-stress-demo", gui, "stress"), ("gui-widgets-storage", gui, "widgets"),
              ("gui-persistence-coldboot", gui, "widgets")]
    if args.keypad_only:
        phases = [("gui-keypad-controls", gui, "keypad_encoder")]
    previous_manual_hash = None
    previous_gui_hash = None
    for name, instance, demo in phases:
        phase = {"name": name, "status": "running"}
        report["phases"].append(phase)
        save_report(args, report)
        print(f"[{now()}] {name}: booting {instance}", flush=True)
        image = args.results_dir / "avd" / (instance + ".vvd") / "vela_data.bin"
        with Emulator(args, instance, phase) as emulator:
            if demo is None:
                manual_keys(emulator, cold_boot=name.endswith("coldboot"))
            else:
                if name == "gui-music" or args.keypad_only:
                    emulator.encfs("provision --erase")
                emulator.encfs("start")
                initial = start_demo(args, emulator, phase, demo)
                if demo == "widgets":
                    widgets_interactions(args, emulator, phase)
                elif demo == "keypad_encoder":
                    keypad_interactions(args, emulator, phase, initial)
                else:
                    if demo == "music":
                        emulator.command("input tap 640 608")
                    time.sleep(3)
                    later = capture(args, emulator, phase, "animated")
                    phase["animation_changed_pixels"] = assert_frame_changed(initial, later, demo)
                if name == "gui-widgets-storage":
                    emulator.test("write")
                    emulator.encfs("start")
                    capture(args, emulator, phase, "after-storage-regression")
                    long_stress(args, emulator, phase, report)
                    require("persistent-content-and-transparent-path-encryption" in
                            emulator.command("cat /data/test/secret.txt"),
                            "Persistent marker was lost during long stress")
                elif name == "gui-persistence-coldboot":
                    emulator.test("verify")
                emulator.encfs("stop")
        phase["image"] = {"path": str(image), "sha256": sha256(image), "size": image.stat().st_size}
        if demo is None:
            raw = image.read_bytes()
            require(KEY_MARKER.encode() not in raw and b"manual-key.txt" not in raw,
                    "Manual test-key disk exposed the specified plaintext")
            if previous_manual_hash:
                require(phase["image"]["sha256"] == previous_manual_hash,
                        "Manual-key read-only cold boot changed the disk")
            previous_manual_hash = phase["image"]["sha256"]
        elif name in ("gui-widgets-storage", "gui-persistence-coldboot"):
            inspect_image(image, encrypted=True, result=phase["image"])
            if name == "gui-persistence-coldboot":
                require(phase["image"]["sha256"] == previous_gui_hash,
                        "Read-only GUI persistence boot changed the encrypted disk")
            previous_gui_hash = phase["image"]["sha256"]
        phase["status"] = "passed"
        save_report(args, report)
        print(f"[{now()}] {name}: passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=source_default())
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--stress-seconds", type=int, default=3600)
    parser.add_argument("--keypad-only", action="store_true",
                        help="run only an isolated keypad control regression (no timed stress)")
    parser.add_argument("--boot-timeout", type=float, default=300)
    parser.add_argument("--command-timeout", type=float, default=300)
    parser.add_argument("--shutdown-timeout", type=float, default=30)
    args = parser.parse_args()
    if not 60 <= args.stress_seconds <= 86400:
        parser.error("--stress-seconds must be between 60 and 86400")
    args.source_root = args.source_root.resolve()
    args.results_dir = args.results_dir.resolve()
    args.config = "goldfish-arm64-v8a-ap-encfs-gui"
    args.build_dir = (args.build_dir or args.source_root / "cmake_out" / ("vela_" + args.config)).resolve()
    args.gpu = "swiftshader_indirect"
    args.emulator_args = []
    if args.results_dir.exists() and any(args.results_dir.iterdir()):
        parser.error("Results directory must be empty")
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report = {"started_at": now(), "status": "running", "phases": [],
              "requested_stress_seconds": None if args.keypad_only else args.stress_seconds,
              "scope": "keypad controls only" if args.keypad_only else "full GUI and timed stress",
              "config": args.config, "build_dir": str(args.build_dir),
              "limitations": ["Test keys are public fixtures, never production secrets.",
                              "One-hour stress is a bounded run, not an endurance guarantee.",
                              "This does not test power-loss or hardware fault recovery."]}
    result = 1
    try:
        report["build_artifacts"] = validate_build(args.source_root, args.build_dir)
        config = (args.build_dir / ".config").read_text()
        for option in ("GRAPHICS_LVGL", "EXAMPLES_LVGLDEMO", "FBCAPTURE", "GRAPHICS_INPUT_TOOL"):
            require(f"CONFIG_{option}=y" in config, f"GUI build missing {option}")
        report["source_revisions"] = {name: git_revision(args.source_root / path)
                                      for name, path in (("nuttx", "nuttx"), ("apps", "apps"),
                                                         ("vendor_openvela", "vendor/openvela"),
                                                         ("manifests", ".repo/manifests"))}
        run(args, report)
        report["status"] = "passed"
        result = 0
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        if report["phases"] and report["phases"][-1]["status"] == "running":
            report["phases"][-1]["status"] = "failed"
        print(f"GUI/stress regression failed: {exc}", file=sys.stderr, flush=True)
    finally:
        report["finished_at"] = now()
        save_report(args, report)
        print(f"Report: {args.results_dir / 'report.json'}", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
