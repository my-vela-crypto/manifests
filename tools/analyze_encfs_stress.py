#!/usr/bin/env python3
"""Cross-check a completed GUI/stress report against its preserved evidence.

Only a report already marked passed can be analyzed.  The report's stress
statistics are independently parsed again from the original guest serial log.
Every recorded framebuffer PNG is checked, and every stress-time Analytics or
Shop image must show the same selected-tab header as its respective baseline.

Matplotlib produces standalone PNG plots when installed.  Otherwise a small
stdlib SVG renderer produces standalone vector plots, without downloading any
dependencies.  Neither backend changes the original report, logs, or images.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops


HEADER_BOX = (680, 0, 1280, 70)
FRAME_SIZE = (1280, 800)
STEADY_AFTER_MS = 300000
MAX_PROGRESS_GAP_MS = 95000
PROGRESS_FIELDS = {
    "elapsed_ms", "iterations", "written_bytes", "verified_bytes",
    "worker0_iterations", "worker1_iterations", "errors", "heap_used",
    "heap_free",
}
RESULT_FIELDS = {
    "requested_seconds", "elapsed_ms", "workers", "iterations",
    "written_bytes", "verified_bytes", "worker0_iterations",
    "worker1_iterations", "worker0_elapsed_ms", "worker1_elapsed_ms",
    "errors", "completed", "heap_before", "heap_after",
}
COLORS = ("#2364aa", "#d66b19")
FILE_LENGTHS = (1, 511, 512, 513, 1023, 4095, 4096, 4097, 8191, 16385, 65537)
PATCH_OFFSETS = (0, 495, 4095)


class EvidenceError(RuntimeError):
    """Evidence is incomplete, inconsistent, or fails a required check."""


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def marker_records(serial, name):
    # NSH can append background output directly after an ANSI ESC[K prompt;
    # the terminal-control sequence's final K is not a marker word boundary.
    return list(re.finditer(re.escape(name) + r" ([^\r\n]+)", serial))


def integer_fields(text, required, label):
    fields = {}
    for key, value in re.findall(r"\b([A-Za-z_]\w*)=(-?\d+)(?=\s|$)", text):
        require(key not in fields, f"Duplicate field {key} in {label}")
        fields[key] = int(value)
    require(required <= fields.keys(),
            f"Missing fields in {label}: {sorted(required - fields.keys())}")
    require(all(value >= 0 for value in fields.values()),
            f"Negative counter in {label}")
    return fields


def expected_payload_totals(worker0_iterations, worker1_iterations):
    """Recompute logical byte totals from the deterministic 33-cycle pattern."""
    written = 0
    verified = 0
    period = len(FILE_LENGTHS) * len(PATCH_OFFSETS)
    for worker, count in enumerate((worker0_iterations, worker1_iterations)):
        pattern = []
        for index in range(period):
            length = FILE_LENGTHS[(index + worker) % len(FILE_LENGTHS)]
            offset = PATCH_OFFSETS[index % len(PATCH_OFFSETS)]
            if offset >= length:
                offset = 0
            pattern.append((length + min(67, length - offset), length * 2))
        repetitions, remainder = divmod(count, period)
        written += repetitions * sum(item[0] for item in pattern)
        written += sum(item[0] for item in pattern[:remainder])
        verified += repetitions * sum(item[1] for item in pattern)
        verified += sum(item[1] for item in pattern[:remainder])
    return written, verified


def require_payload_totals(values, label):
    expected = expected_payload_totals(values["worker0_iterations"],
                                       values["worker1_iterations"])
    require((values["written_bytes"], values["verified_bytes"]) == expected,
            f"Logical byte counters disagree with completed cycle pattern: {label}")


def parse_stress(report, phase, serial):
    starts = marker_records(serial, "ENCFS_LONG_STRESS_START")
    results = marker_records(serial, "ENCFS_LONG_STRESS_RESULT")
    samples = marker_records(serial, "ENCFS_LONG_STRESS_PROGRESS")
    application_matches = list(re.finditer(
        r"ENCFS_TEST_RESULT mode=stress pass=(\d+) fail=(\d+)", serial))
    applications = [match.groups() for match in application_matches]
    require(len(starts) == 1 and len(results) == 1,
            "Expected exactly one start and one result in the original serial")
    require(applications == [("1", "0")],
            "Original serial does not contain one successful stress application")
    require("ENCFS_LONG_STRESS_ERROR" not in serial,
            "Original serial contains an I/O worker error")
    require(len(samples) >= 2, "Too few original progress samples")
    require(starts[0].start() < samples[0].start() and
            samples[-1].end() < results[0].start() and
            results[0].end() < application_matches[0].start(),
            "Stress serial marker order is inconsistent")

    start = integer_fields(starts[0].group(1),
                           {"requested_seconds", "workers",
                            "max_live_file_bytes"}, "start")
    result = integer_fields(results[0].group(1), RESULT_FIELDS, "result")
    progress = [integer_fields(sample.group(1), PROGRESS_FIELDS,
                               f"progress {index}")
                for index, sample in enumerate(samples)]
    requested = report.get("requested_stress_seconds")
    require(isinstance(requested, int) and not isinstance(requested, bool) and
            60 <= requested <= 86400, "Invalid requested duration in report")
    require(start["requested_seconds"] == requested ==
            result["requested_seconds"], "Requested duration disagrees")
    require(start["workers"] == result["workers"] == 2 and
            start["max_live_file_bytes"] == 131074,
            "Unexpected worker count or bounded-file configuration")
    require(re.search(r"\bseed=6e637273(?=\s|$)", starts[0].group(1)),
            "Unexpected deterministic workload seed")
    require(result["completed"] == 1 and result["errors"] == 0,
            "Guest stress result is not a successful completion")
    required_ms = requested * 1000
    for field in ("elapsed_ms", "worker0_elapsed_ms", "worker1_elapsed_ms"):
        require(result[field] >= required_ms,
                f"Guest {field} did not reach the requested duration")
    require(max(result["worker0_elapsed_ms"], result["worker1_elapsed_ms"]) <=
            result["elapsed_ms"], "Worker duration exceeds total guest duration")
    require(result["iterations"] == result["worker0_iterations"] +
            result["worker1_iterations"] and
            min(result["worker0_iterations"], result["worker1_iterations"]) > 0,
            "Final worker iteration counts are inconsistent")
    require(result["written_bytes"] > 0 and result["verified_bytes"] > 0,
            "Final logical data counters are empty")
    require_payload_totals(result, "result")
    require(phase.get("long_stress_result") == result,
            "Report's final statistics disagree with the original serial")
    require(phase.get("long_stress_progress") == progress,
            "Report's progress samples disagree with the original serial")
    host_elapsed = phase.get("stress_host_elapsed_seconds")
    require(isinstance(host_elapsed, (int, float)) and
            math.isfinite(host_elapsed) and host_elapsed >= requested - 2,
            "Host duration did not reach the independent duration threshold")

    increasing_fields = (
        "elapsed_ms", "iterations", "worker0_iterations", "worker1_iterations",
        "written_bytes", "verified_bytes")
    previous = {field: 0 for field in increasing_fields}
    for index, sample in enumerate(progress):
        require(sample["errors"] == 0,
                f"Original progress {index} contains errors")
        require(sample["iterations"] == sample["worker0_iterations"] +
                sample["worker1_iterations"],
                f"Worker totals disagree in progress {index}")
        require_payload_totals(sample, f"progress {index}")
        for field in increasing_fields:
            require(sample[field] > previous[field],
                    f"{field} stopped advancing at progress {index}")
        require(sample["elapsed_ms"] - previous["elapsed_ms"] <=
                MAX_PROGRESS_GAP_MS,
                f"Progress evidence has a gap exceeding 95 seconds at {index}")
        require(sample["elapsed_ms"] <= result["elapsed_ms"],
                "Progress timestamp exceeds final elapsed time")
        previous = sample
    for field in ("iterations", "written_bytes", "verified_bytes",
                  "worker0_iterations", "worker1_iterations", "errors"):
        require(progress[-1][field] == result[field],
                f"Final cleanup sample does not match final {field}")
    require(result["elapsed_ms"] - progress[-1]["elapsed_ms"] <=
            MAX_PROGRESS_GAP_MS, "Final result is detached from progress")
    return result, progress


def frame_evidence(results_dir, report, stress_phase):
    verified = []
    paths = set()
    stress_frames = {}
    baseline = {}
    frames_dir = results_dir / "frames"
    require(frames_dir.is_dir(), "Framebuffer directory is missing")
    for phase in report["phases"]:
        name = phase["name"]
        require(isinstance(name, str) and re.fullmatch(r"[\w-]+", name),
                "Invalid phase name in report")
        for record in phase.get("frames", []):
            label = record.get("label", "")
            require(isinstance(label, str) and re.fullmatch(r"[\w-]+", label),
                    "Invalid framebuffer label")
            basename = name + "-" + label + ".png"
            require(Path(record.get("path", "")).name == basename,
                    "Recorded framebuffer path does not match its phase/label")
            path = frames_dir / basename
            require(path.is_file() and not path.is_symlink(),
                    f"Missing or symlinked framebuffer: {path}")
            require(path not in paths, f"Duplicate framebuffer record: {path}")
            paths.add(path)
            digest = sha256(path)
            require(record.get("sha256") == digest,
                    f"Framebuffer SHA256 disagrees: {path}")
            with Image.open(path) as image:
                image.load()
                require(image.format == "PNG" and image.size == FRAME_SIZE and
                        (record.get("width"), record.get("height")) == FRAME_SIZE,
                        f"Framebuffer format/dimensions disagree: {path}")
                if phase is stress_phase:
                    header = image.convert("RGB").crop(HEADER_BOX)
                    if label in ("analytics", "shop"):
                        baseline[label] = (header, path)
                    elif re.fullmatch(r"stress-\d+-(analytics|shop)", label):
                        index, kind = re.fullmatch(
                            r"stress-(\d+)-(analytics|shop)", label).groups()
                        pair = stress_frames.setdefault(int(index), {})
                        require(kind not in pair,
                                "Duplicate stress frame index and page")
                        pair[kind] = (header, path)
            verified.append({"phase": name, "label": label,
                             "path": str(path), "recorded_path": record["path"],
                             "sha256": digest, "width": FRAME_SIZE[0],
                             "height": FRAME_SIZE[1]})
    require(paths == set(frames_dir.rglob("*.png")),
            "Unrecorded framebuffer PNGs are present in the frames directory")
    require(baseline.keys() == {"analytics", "shop"} and stress_frames,
            "Expected page baselines or stress-time captures are missing")
    require(baseline["analytics"][0].tobytes() != baseline["shop"][0].tobytes(),
            "Analytics and Shop baselines do not show distinct selected tabs")
    checkpoints = stress_phase.get("checkpoints", [])
    expected_indexes = {index for index, checkpoint in enumerate(checkpoints, 1)
                        if "touch_changed_pixels" in checkpoint}
    require(set(stress_frames) == expected_indexes,
            "Stress captures do not match the recorded touch checkpoints")
    require(len(stress_frames) >= 2,
            "Missing early and final stress-time touch evidence")
    comparisons = []
    for index, pair in sorted(stress_frames.items()):
        require(pair.keys() == {"analytics", "shop"},
                f"Incomplete stress capture pair at checkpoint {index}")
        require(checkpoints[index - 1]["touch_changed_pixels"] > 1000,
                "Recorded touch checkpoint did not pass its panel-change check")
        for kind, (header, path) in pair.items():
            expected, expected_path = baseline[kind]
            difference = ImageChops.difference(header, expected)
            changed = sum(pixel != (0, 0, 0)
                          for pixel in difference.getdata())
            require(changed == 0,
                    f"Selected-tab header differs from {kind} baseline: {path} "
                    f"({changed} pixels)")
            comparisons.append({"checkpoint_index": index, "page": kind,
                                "frame_path": str(path),
                                "baseline_path": str(expected_path),
                                "crop": list(HEADER_BOX),
                                "different_pixels": changed,
                                "match_policy": "exact RGB pixel equality"})
    return verified, comparisons


def heap_summary(progress):
    # Production stress emits its final progress line after joining threads,
    # freeing both worker buffers, and removing its directories.  Including
    # that cleanup drop in active-workload OLS would bias the slope downward.
    active = progress[:-1]
    steady = [sample for sample in active
              if sample["elapsed_ms"] >= STEADY_AFTER_MS]
    result = {
        "scope": "Global allocator usage including GUI and other tasks",
        "unit": "bytes", "steady_after_seconds": STEADY_AFTER_MS / 1000,
        "all_progress_sample_count": len(progress),
        "active_sample_count": len(active), "steady_sample_count": len(steady),
        "final_cleanup_sample_excluded_from_steady_OLS": progress[-1],
        "interpretation": (
            "A bounded run describes observed global heap behavior. It does "
            "not establish absence of leaks or attribute changes to EncFS."),
    }
    if len(steady) < 2:
        result.update({"steady_available": False,
                       "reason": "Fewer than two active samples at/after 300s"})
        return result
    x = [sample["elapsed_ms"] / 1000 for sample in steady]
    y = [sample["heap_used"] for sample in steady]
    average_x = statistics.mean(x)
    average_y = statistics.mean(y)
    denominator = sum((value - average_x) ** 2 for value in x)
    slope = sum((a - average_x) * (b - average_y)
                for a, b in zip(x, y)) / denominator
    intercept = average_y - slope * average_x
    residual = sum((b - (intercept + slope * a)) ** 2 for a, b in zip(x, y))
    variation = sum((value - average_y) ** 2 for value in y)
    result.update({
        "steady_available": True, "minimum_used_bytes": min(y),
        "maximum_used_bytes": max(y), "first_used_bytes": y[0],
        "last_used_bytes": y[-1], "first_elapsed_seconds": x[0],
        "last_elapsed_seconds": x[-1], "last_minus_first_bytes": y[-1] - y[0],
        "mean_used_bytes": average_y,
        "minimum_free_bytes": min(sample["heap_free"] for sample in steady),
        "maximum_free_bytes": max(sample["heap_free"] for sample in steady),
        "OLS": {"slope_bytes_per_second": slope,
                "slope_bytes_per_hour": slope * 3600,
                "intercept_bytes": intercept,
                "r_squared": 1 - residual / variation if variation else 1.0},
    })
    return result


def svg_plot(path, title, ylabel, series, *, note, warmup=False):
    """Write a standalone, labeled vector line plot using SVG primitives."""
    width, height = 1100, 650
    left, right, top, bottom = 110, 35, 80, 120
    chart_width = width - left - right
    chart_height = height - top - bottom
    xs = [x for _name, values in series for x, _y in values]
    ys = [y for _name, values in series for _x, y in values]
    xmax = max(xs) if max(xs) > 0 else 1
    ymin, ymax = min(ys), max(ys)
    if "iterations" in ylabel:
        ymin = 0
    padding = max((ymax - ymin) * 0.06, abs(ymax) * 0.0001, 1e-6)
    ymax += padding
    if ymin != 0:
        ymin -= padding
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)

    def element(parent, tag, attributes=None, content=None):
        child = ET.SubElement(parent, "{" + ns + "}" + tag, attributes or {})
        if content is not None:
            child.text = content
        return child

    root = ET.Element("{" + ns + "}svg", {
        "width": str(width), "height": str(height),
        "viewBox": f"0 0 {width} {height}", "role": "img"})
    element(root, "title", content=title)
    element(root, "desc", content=note)
    element(root, "rect", {"width": "100%", "height": "100%", "fill": "white"})
    group = element(root, "g", {"font-family": "sans-serif", "fill": "#222"})

    def text(x, y, content, **attributes):
        attrs = {"x": str(x), "y": str(y), "font-size": "16"}
        attrs.update(attributes)
        return element(group, "text", attrs, content)

    def px(value):
        return left + value / xmax * chart_width

    def py(value):
        return top + (ymax - value) / (ymax - ymin) * chart_height

    text(left, 35, title, **{"font-size": "23", "font-weight": "600"})
    if warmup:
        element(group, "rect", {"x": str(left), "y": str(top),
                                "width": str(px(min(5, xmax)) - left),
                                "height": str(chart_height), "fill": "#f1f1f1"})
    for index in range(6):
        x = xmax * index / 5
        y = ymin + (ymax - ymin) * index / 5
        element(group, "line", {"x1": str(left), "x2": str(width - right),
                                "y1": str(py(y)), "y2": str(py(y)),
                                "stroke": "#dedede"})
        text(left - 12, py(y) + 5, f"{y:,.3f}" if ymax < 100 else f"{y:,.0f}",
             **{"text-anchor": "end"})
        text(px(x), top + chart_height + 27, f"{x:.1f}",
             **{"text-anchor": "middle"})
    element(group, "path", {"d": f"M {left} {top} V {top + chart_height} "
                           f"H {width - right}", "fill": "none",
                           "stroke": "#333", "stroke-width": "1.5"})
    for index, (name, values) in enumerate(series):
        color = COLORS[index % len(COLORS)]
        points = " ".join(f"{px(x):.2f},{py(y):.2f}" for x, y in values)
        element(group, "polyline", {"points": points, "fill": "none",
                                    "stroke": color, "stroke-width": "2"})
        legend_x = left + index * 250
        element(group, "line", {"x1": str(legend_x), "x2": str(legend_x + 30),
                                "y1": "58", "y2": "58", "stroke": color,
                                "stroke-width": "3"})
        text(legend_x + 40, 63, name)
    text(left + chart_width / 2, height - 60, "Guest monotonic elapsed (minutes)",
         **{"text-anchor": "middle"})
    text(24, top + chart_height / 2, ylabel,
         transform=f"rotate(-90 24 {top + chart_height / 2})",
         **{"text-anchor": "middle"})
    text(left, height - 22, note, **{"font-size": "13", "fill": "#555"})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def plots(output_dir, progress):
    iterations = [(f"Worker {worker}",
                   [(sample["elapsed_ms"] / 60000,
                     sample[f"worker{worker}_iterations"])
                    for sample in progress]) for worker in (0, 1)]
    active = progress[:-1]
    heap = [("Global heap used", [(sample["elapsed_ms"] / 60000,
                                   sample["heap_used"] / 1048576)
                                  for sample in active])]
    descriptions = [
        ("worker_iterations", "Verified file cycles per worker",
         "Completed iterations", iterations,
         "Each cycle includes writes, fsync, two full reads, rename and delete.", False),
        ("global_heap", "Global heap during the active workload",
         "Global heap used (MiB)", heap,
         "Includes GUI and other tasks. Shaded: first 5 min. Cleanup sample excluded.", True),
    ]
    os.environ.setdefault("MPLCONFIGDIR", str(output_dir / "matplotlib-cache"))
    try:
        import matplotlib
    except ImportError:
        matplotlib = None
    artifacts = []
    if matplotlib is None:
        for stem, title, ylabel, series, note, warmup in descriptions:
            path = output_dir / (stem + ".svg")
            svg_plot(path, title, ylabel, series, note=note, warmup=warmup)
            artifacts.append({"path": str(path), "sha256": sha256(path),
                              "format": "SVG"})
        return "stdlib SVG (matplotlib unavailable)", artifacts
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for stem, title, ylabel, series, note, warmup in descriptions:
        figure, axis = plt.subplots(figsize=(11, 6.5))
        for index, (name, values) in enumerate(series):
            axis.plot([x for x, _y in values], [y for _x, y in values],
                      label=name, color=COLORS[index], linewidth=1.7)
        if warmup:
            axis.axvspan(0, min(5, max(x for _name, values in series
                                      for x, _y in values)),
                         color="0.94", zorder=0)
        else:
            axis.set_ylim(bottom=0)
        axis.set(title=title, xlabel="Guest monotonic elapsed (minutes)",
                 ylabel=ylabel)
        axis.grid(alpha=0.25)
        axis.legend()
        figure.text(0.5, 0.02, note, ha="center", fontsize=9)
        figure.tight_layout(rect=(0, 0.06, 1, 1))
        path = output_dir / (stem + ".png")
        figure.savefig(path, dpi=180)
        plt.close(figure)
        artifacts.append({"path": str(path), "sha256": sha256(path),
                          "format": "PNG"})
    return "matplotlib Agg", artifacts


def analyze(results_dir, output_dir):
    report_path = results_dir / "report.json"
    require(report_path.is_file(), "Report is missing")
    report = json.loads(report_path.read_text())
    require(report.get("status") == "passed",
            f"Refusing incomplete/failed report (status={report.get('status')!r})")
    phases = report.get("phases", [])
    require(phases and all(phase.get("status") == "passed" for phase in phases),
            "Not all reported phases passed")
    matches = [phase for phase in phases
               if phase.get("name") == "gui-widgets-storage"]
    require(len(matches) == 1, "Missing unique GUI/storage stress phase")
    phase = matches[0]
    serial_path = results_dir / "gui-widgets-storage.serial.log"
    require(Path(phase.get("serial_log", "")).name == serial_path.name and
            serial_path.is_file() and not serial_path.is_symlink(),
            "Original stress serial path is missing or inconsistent")
    serial = serial_path.read_text(errors="replace")
    result, progress = parse_stress(report, phase, serial)
    frames, comparisons = frame_evidence(results_dir, report, phase)
    heap = heap_summary(progress)
    if result["requested_seconds"] >= 600:
        require(heap["steady_available"], "Long run has no steady heap evidence")
    # All validation precedes creation of output files. In-progress evidence
    # cannot produce an analysis marked passed or even a partial plot set.
    output_dir.mkdir(parents=True, exist_ok=True)
    backend, charts = plots(output_dir, progress)
    analysis = {
        "status": "passed",
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results_directory": str(results_dir),
        "input_report": {"path": str(report_path), "sha256": sha256(report_path)},
        "input_serial": {"path": str(serial_path), "sha256": sha256(serial_path)},
        "validation": {
            "report_and_all_phase_statuses_passed": True,
            "statistics_match_independently_parsed_serial": True,
            "each_worker_met_requested_duration": True,
            "both_worker_counts_advance_in_every_progress_interval": True,
            "payload_counters_match_deterministic_completed_cycle_pattern": True,
            "maximum_allowed_progress_gap_seconds": MAX_PROGRESS_GAP_MS / 1000,
            "verified_framebuffer_png_count": len(frames),
            "verified_stress_header_count": len(comparisons),
            "selected_tab_header_policy": "exact RGB equality at (680,0,1280,70)",
        },
        "workload": {
            "requested_seconds": result["requested_seconds"],
            "guest_elapsed_ms": result["elapsed_ms"],
            "host_elapsed_seconds": phase["stress_host_elapsed_seconds"],
            "completed_iterations": result["iterations"],
            "worker0_iterations": result["worker0_iterations"],
            "worker1_iterations": result["worker1_iterations"],
            "worker0_elapsed_ms": result["worker0_elapsed_ms"],
            "worker1_elapsed_ms": result["worker1_elapsed_ms"],
            "logical_written_bytes": result["written_bytes"],
            "logical_verified_read_bytes": result["verified_bytes"],
            "errors": result["errors"],
            "counter_scope": "Logical test payload only; not physical device I/O",
            "progress_sample_count": len(progress),
        },
        "global_heap": heap,
        "progress_samples": progress,
        "verified_framebuffers": frames,
        "stress_selected_tab_comparisons": comparisons,
        "plot_backend": backend, "plots": charts,
        "limitations": [
            "This bounded run is not an endurance guarantee or proof of zero leaks.",
            "Global heap samples include GUI and other tasks; they do not isolate EncFS.",
            "Payload byte counters exclude metadata, alignment and other physical I/O.",
            "Framebuffer hashes establish integrity; header checks specifically verify "
            "stress-time page selection, not every possible GUI interaction.",
            "Power-loss and hardware fault recovery are not tested by this analysis.",
        ],
    }
    path = output_dir / "analysis.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(analysis, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n")
    temporary.replace(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = analyze(args.results_dir.resolve(), args.output_dir.resolve())
    except (EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Stress evidence analysis refused: {exc}", file=sys.stderr)
        return 1
    print(f"Verified stress analysis: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
