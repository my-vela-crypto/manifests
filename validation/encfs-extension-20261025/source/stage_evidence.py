#!/usr/bin/env python3
"""Stage small, completed EncFS evidence into a NEW destination.

Default inputs are the final storage/extended runs, the completed 180-second
keywrap regression, declared source delivery, host checks, and failed histories.
Add a newly completed GUI run with --run PATH. Active endurance runs are
observed as metadata only and never copied or called complete implicitly.
Use --dry-run to validate everything without creating the destination.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


DEFAULT_RUNS = (
    "extended-20261024/storage-final-01",
    "extended-20261024/acceptance-final-01",
    "keywrap-180-20261025-02",
    "gui-extension-20261025-02",
)
HISTORY_RUNS = (
    "acceptance-01", "acceptance-03-attempt1",
    "gui-stress-pilot-20261023", "gui-stress-pilot2-20261023",
    "gui-stress-pilot3-20261023", "gui-keypad-controls-01",
    "extended-20261024/exploratory-01", "extended-20261024/exploratory-02",
    "extended-20261024/exploratory-03", "extended-20261024/exploratory-04",
    "extended-20261024/exploratory-05", "extended-20261024/acceptance-01",
    "extended-20261024/storage-acceptance-01", "keywrap-60-20261024-01",
    "keywrap-60-20261024-02", "keywrap-180-20261025-01",
    "gui-extension-20261025-01",
)
OBSERVE_RUNS = (
    "endurance-24h-baseline-20261024/run",
    "endurance-24h-wrapped-20261024/run",
)
HOST_GROUPS = (
    "extension-20261024/utf8", "extension-20261024/keywrap-implementation",
    "extension-20261024/keywrap-review", "extension-20261024/delivery",
)
SOURCE_FILES = (
    "README.md", "encfs-sources.json", "encfs-tested.xml",
    "tools/apply_encfs.py", "tools/apply-encfs.sh",
    "tools/test_encfs.py", "tools/test_encfs_gui.py",
    "tools/test_encfs_extended.py", "tools/test_encfs_keywrap.py",
    "tools/analyze_encfs_stress.py", "docs/encfs_extended_validation.md",
    "docs/encfs_keywrap.md", "docs/encfs_extension_status.md",
    "docs/encfs_interfaces.md", "docs/encfs_extension_report.md",
)
FORBIDDEN_PARTS = {"avd", "keys", ".git", "__pycache__"}
FORBIDDEN_SUFFIXES = {".bin", ".img", ".core", ".o", ".a", ".bundle",
                      ".bmp", ".tar", ".gz", ".zip", ".keyslot"}
FRAME_LABELS = {"initial", "profile", "analytics", "shop", "slider-before",
                "slider-after", "checkbox-toggled", "modal-dismissed",
                "checkbox-before", "checkbox-after", "text-tab", "checkbox", "slider"}
SHANGHAI = timezone(timedelta(hours=8))


class EvidenceError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_default():
    for parent in Path(__file__).resolve().parents:
        if (parent / "nuttx").is_dir() and (parent / "run-emulator.sh").is_file():
            return parent
    return Path.cwd()


class Plan:
    def __init__(self, root, max_file_bytes=64 * 1024 * 1024):
        self.root = root.resolve()
        self.max_file_bytes = max_file_bytes
        self.files = {}
        self.completed = []
        self.histories = []
        self.observations = []
        self.omitted_optional = []
        self.artifact_checks = {}

    def add(self, source, target, kind, expected_sha=None):
        source = Path(source)
        target = Path(target)
        require(not source.is_symlink(), f"Refusing symbolic link: {source}")
        actual = source.resolve()
        require(actual.is_relative_to(self.root), f"Source outside workspace: {source}")
        require(actual.is_file(), f"Missing evidence: {source}")
        relative = actual.relative_to(self.root)
        require(not any(part in FORBIDDEN_PARTS or part.endswith(".vvd")
                        for part in relative.parts), f"Private/runtime directory excluded: {source}")
        require(actual.suffix.lower() not in FORBIDDEN_SUFFIXES,
                f"Binary image, keyslot, or large runtime file excluded: {source}")
        require(not target.is_absolute() and ".." not in target.parts
                and not any(ch in str(target) for ch in "\r\n\t\\"),
                f"Unsafe evidence destination: {target}")
        size = actual.stat().st_size
        require(size <= self.max_file_bytes, f"Evidence exceeds file size cap: {source}")
        if actual.suffix.lower() == ".png":
            require(actual.open("rb").read(8) == b"\x89PNG\r\n\x1a\n", f"Invalid PNG: {source}")
        elif actual.suffix.lower() != ".log":
            require(b"\0" not in actual.read_bytes(), f"Unexpected binary input: {source}")
            if actual.suffix.lower() == ".json":
                read_json(actual)
        digest = sha256(actual)
        require(expected_sha is None or digest == expected_sha,
                f"Evidence fingerprint differs from its report: {source}")
        record = {"source": str(actual), "destination": target.as_posix(),
                  "kind": kind, "size": size, "sha256": digest}
        key = target.as_posix()
        require(key not in self.files or self.files[key] == record,
                f"Two different files map to {target}")
        self.files[key] = record

    def optional(self, source, target, kind):
        if Path(source).is_file():
            self.add(source, target, kind)
        else:
            self.omitted_optional.append(str(source))

    def run(self, relative_name, historical=False, max_frames=20):
        folder = (self.root / "encfs-results" / relative_name).resolve()
        require(folder.is_relative_to(self.root / "encfs-results"), "Run must be in encfs-results")
        relative_name = folder.relative_to(self.root / "encfs-results").as_posix()
        report_path = folder / "report.json"
        report = read_json(report_path)
        require(report.get("status") not in (None, "running", "pending"),
                f"Refusing an incomplete run: {relative_name}")
        if not historical:
            require(report.get("status") == "passed" and report.get("finished_at"),
                    f"Required run did not finish successfully: {relative_name}")
            require(not any(p.get("status") in ("running", "failed", "pending")
                            for p in report.get("phases", [])),
                    f"A phase is incomplete or failed: {relative_name}")
        category = "history" if historical else "reports"
        target = Path(category) / relative_name
        self.add(report_path, target / "report.json", category)
        serials = sorted(folder.glob("*.serial.log"))
        require(serials or historical, f"Completed guest run has no raw serials: {relative_name}")
        for serial in serials:
            self.add(serial, target / serial.name, "rawserial")
        for phase in report.get("phases", []):
            if phase.get("serial_log"):
                serial = Path(phase["serial_log"])
                require(serial.resolve().parent == folder,
                        f"Unexpected raw serial location: {serial}")
                require(serial in serials, f"Missing phase serial: {serial}")
        descriptor = {"run": relative_name, "status": report.get("status"),
                      "started_at": report.get("started_at"),
                      "finished_at": report.get("finished_at"),
                      "report_sha256": sha256(report_path), "raw_serial_count": len(serials)}
        if historical:
            descriptor["interpretation"] = "Historical report only; no new proof or return-code inference"
            self.histories.append(descriptor)
            return

        artifacts = report.get("build_artifacts", report.get("artifacts", {}))
        checked = {}
        for name, artifact in artifacts.items():
            original = Path(artifact["path"])
            require(original.resolve().is_relative_to(self.root),
                    f"Artifact outside the source workspace: {original}")
            require(original.is_file() and sha256(original) == artifact["sha256"],
                    f"Actual completed-run artifact no longer matches: {original}")
            checked[name] = artifact["sha256"]
            if name == ".config":
                self.add(original, Path("source/build-configs") / relative_name / "input.config",
                         "source", artifact["sha256"])
        self.artifact_checks[relative_name] = checked
        audit = folder / "independent-audit.json"
        if audit.exists():
            data = read_json(audit)
            require(data.get("status") == "passed"
                    and data.get("report_sha256") == sha256(report_path),
                    f"Stale or failed independent audit: {audit}")
            self.add(audit, target / audit.name, "reports")
        if relative_name in DEFAULT_RUNS[:2]:
            require(audit.is_file(), f"Final storage/extended audit is required: {audit}")

        analysis = folder / "analysis/analysis.json"
        stress = any(p.get("long_stress_result") for p in report.get("phases", []))
        if stress or analysis.exists():
            data = read_json(analysis)
            require(data.get("status") == "passed"
                    and data.get("input_report", {}).get("sha256") == sha256(report_path),
                    f"Stale or incomplete stress analysis: {analysis}")
            for source in sorted(analysis.parent.iterdir()):
                if source.is_file() and source.suffix in (".json", ".svg", ".png"):
                    self.add(source, target / "analysis" / source.name, "reports")

        frames = []
        for phase in report.get("phases", []):
            records = [frame for frame in phase.get("frames", [])
                       if str(frame.get("path", "")).endswith(".png")]
            if not records:
                continue
            frames.extend(records[:1] + records[-1:])
            frames.extend(frame for frame in records if frame.get("label") in FRAME_LABELS)
            for tab in ("analytics", "shop"):
                candidates = [frame for frame in records
                              if str(frame.get("label", "")).startswith("stress-")
                              and str(frame.get("label", "")).endswith("-" + tab)]
                frames.extend(candidates[:1] + candidates[-1:])
        selected = list({frame["path"]: frame for frame in frames}.values())[:max_frames]
        for frame in selected:
            image = Path(frame["path"])
            require(image.resolve().parent == folder / "frames", f"Unexpected framebuffer path: {image}")
            self.add(image, Path("selectedPNG") / relative_name / image.name,
                     "selectedPNG", frame["sha256"])
        descriptor["selected_png_count"] = len(selected)
        self.completed.append(descriptor)


def add_sources(plan):
    manifest = plan.root / ".repo/manifests"
    metadata = read_json(manifest / "encfs-sources.json")
    for name in SOURCE_FILES:
        plan.add(manifest / name, Path("source/manifests") / name, "source")
    plan.optional(manifest / "tools/audit_encfs_keywrap.py",
                  "source/manifests/tools/audit_encfs_keywrap.py", "source")
    for entry in metadata["patches"].values():
        plan.add(manifest / entry["file"], Path("source/manifests") / entry["file"],
                 "source", entry["sha256"])
    for name, digest in metadata["overlays"].items():
        plan.add(manifest / "overlays/apps" / name,
                 Path("source/manifests/overlays/apps") / name, "source", digest)
        require(sha256(plan.root / "apps" / name) == digest,
                f"Application source and declared delivery differ: {name}")
    plan.add(Path(__file__), "source/stage_evidence.py", "source")
    helper = plan.root / "encfs-results/extended-20261024/audit-results.py"
    plan.add(helper, "source/audit-results.py", "source")
    for name in ("test_stage_evidence.py", "audit_keywrap_guest.py", "run_gui_wrapper.py"):
        helper = plan.root / "encfs-results/extension-20261025" / name
        plan.add(helper, Path("source") / name, "source")


def build_plan(args):
    plan = Plan(args.source_root, args.max_file_mib * 1024 * 1024)
    add_sources(plan)
    runs = list(dict.fromkeys(DEFAULT_RUNS + tuple(args.run)))
    for name in runs:
        plan.run(name, max_frames=args.max_frames)
    for name in dict.fromkeys(HISTORY_RUNS + tuple(args.history_run)):
        if (plan.root / "encfs-results" / name / "report.json").is_file():
            plan.run(name, historical=True)
        else:
            plan.omitted_optional.append("history:" + name)
    for name in HOST_GROUPS:
        folder = plan.root / "encfs-results" / name
        require(folder.is_dir(), f"Required host evidence directory missing: {folder}")
        for source in sorted(folder.iterdir()):
            if source.is_file() and source.suffix in (".json", ".log"):
                plan.add(source, Path("hosttests") / name / source.name, "hosttests")
    for name in ("history.json", "build-storage-final.log", "build-extended-release.log"):
        plan.add(plan.root / "encfs-results/extended-20261024" / name,
                 Path("hosttests/extended-20261024") / name, "hosttests")
    support = plan.root / "encfs-results/extension-20261025"
    for name in ("stager-tests.json", "keywrap-180-independent-audit.json",
                 "keywrap-60-independent-audit.json", "keywrap-180-02-input-receipt.json",
                 "gui-01-outer-tool-observation.json"):
        plan.add(support / name, Path("hosttests/extension-20261025") / name, "hosttests")
    # Additional independent JSON receipts are small snapshots, never a
    # recursive copy of their referenced private files. Exclude this tool's
    # generated dry-run plan to avoid collecting its own redirected output.
    for source in sorted(support.glob("*.json")):
        if source.name == "evidence-dry-run.json":
            continue
        require(not re.search(r"(^|[-_.])(master|keyslot|currentmaster)([-_.]|$)", source.name),
                f"Raw key-material filename is not public evidence: {source}")
        plan.add(source, Path("hosttests/extension-20261025") / source.name, "hosttests")
    keywrap_audit = read_json(support / "keywrap-180-independent-audit.json")
    keywrap_report = plan.root / "encfs-results/keywrap-180-20261025-02/report.json"
    require(keywrap_audit.get("status") == "passed"
            and keywrap_audit.get("strict_both_workers_continue_after_rotation") is True,
            "Formal keywrap evidence lacks strict post-acknowledgment worker proof")
    fingerprints = {item["path"]: item["sha256"]
                    for item in keywrap_audit.get("checked_artifacts", [])}
    require(fingerprints.get(str(keywrap_report)) == sha256(keywrap_report),
            "Formal keywrap independent audit is stale")
    if "gui-extension-20261025-02" in runs:
        wrapper = read_json(support / "gui-wrapper-rc.json")
        require(wrapper.get("program_return_code") == 0
                and wrapper.get("report_status") == "passed"
                and wrapper.get("status") == "passed"
                and Path(wrapper["results_dir"]).resolve()
                    == plan.root / "encfs-results/gui-extension-20261025-02",
                "GUI formal rerun requires an actual successful direct-parent return-code receipt")
        for name in ("gui-wrapper-rc.json", "gui-wrapper.stdout.log", "gui-wrapper-launch.log"):
            plan.add(support / name, Path("hosttests/extension-20261025") / name, "hosttests")
        independent = read_json(support / "gui-02-independent-audit.json")
        require(independent.get("status") == "passed"
                and independent.get("program_return_code") == 0
                and independent.get("report_sha256") == sha256(
                    plan.root / "encfs-results/gui-extension-20261025-02/report.json")
                and independent.get("wrapper_receipt_sha256") == sha256(
                    support / "gui-wrapper-rc.json"), "Stale independent GUI return-code audit")
        plan.add(support / "gui-02-independent-audit.json",
                 "hosttests/extension-20261025/gui-02-independent-audit.json", "hosttests")
    # Do not copy a live report or serial. This is merely a dated observation.
    for name in OBSERVE_RUNS:
        report = plan.root / "encfs-results" / name / "report.json"
        if report.exists():
            try:
                data = read_json(report)
                plan.observations.append({"run": name, "observed_status": data.get("status"),
                    "started_at": data.get("started_at"), "updated_at": data.get("updated_at"),
                    "copied": name in runs, "claim": "No implicit completion claim"})
            except json.JSONDecodeError:
                plan.observations.append({"run": name, "observed_status": "being-updated",
                                          "copied": False, "claim": "No completion claim"})
    total = sum(record["size"] for record in plan.files.values())
    require(total <= args.max_total_mib * 1024 * 1024, "Evidence exceeds total size cap")
    return plan


def publish(plan, destination):
    require(not destination.exists(), f"Refusing existing evidence archive: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + destination.name + ".", dir=destination.parent))
    try:
        for record in plan.files.values():
            source = Path(record["source"])
            require(sha256(source) == record["sha256"], f"Input changed after preflight: {source}")
            target = staging / record["destination"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            require(sha256(target) == record["sha256"], f"Copied evidence differs: {target}")
        index = {"schema": 1, "staged_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
                 "scope": "Only listed completed tests; this is not a project-completion assertion",
                 "completed_runs": plan.completed, "historical_runs": plan.histories,
                 "endurance_observations": plan.observations,
                 "actual_build_artifacts_rechecked": plan.artifact_checks,
                 "omitted_optional": plan.omitted_optional,
                 "files": sorted(plan.files.values(), key=lambda record: record["destination"])}
        (staging / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
        (staging / ".gitattributes").write_text("* -text\n")
        (staging / "README.md").write_text(
            "# EncFS 扩展轻量验收证据\n\n"
            "仅包含 index.json 中列明的已完成测试、原始串口、源码交付、宿主检查和精选 PNG。"
            "history 单独保留最初失败及旧候选，通过结果不覆盖失败。原始报告不改写。\n\n"
            "不包含临时主密钥、外部 keyslot、AVD 目录、数据盘、固件二进制、core、BMP 或编译对象。"
            "实际固件只重查其 SHA-256，轻量归档中保留对应输入配置。\n\n"
            "进行中的长期测试不自动纳入；endurance_observations 仅是生成时的状态观察，"
            "不得把本归档解释为所有工作或全部 24 小时测试已经完成。"
            "原始报告里引用的大镜像和运行时私有路径不会被递归复制。\n\n"
            "旧 60 秒换钥实验保留为限定历史，正式在线并发证据采用新的 180 秒记录。"
            "GUI 首轮保存的报告通过，但外层工具 session 55112 返回 143，Python returncode 未独立取得；"
            "该事实保留且不猜测 rc 0。GUI 第二轮以独立父进程 wrapper 的实际 wait 结果为准。\n\n"
            "在本目录运行 `sha256sum -c SHA256SUMS` 核对全部归档文件。\n",
            encoding="utf-8")
        files = sorted(path for path in staging.rglob("*") if path.is_file())
        (staging / "SHA256SUMS").write_text("".join(
            f"{sha256(path)}  {path.relative_to(staging).as_posix()}\n" for path in files))
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=staging,
                       stdout=subprocess.DEVNULL, check=True)
        require(not destination.exists(), f"Destination appeared during staging: {destination}")
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=source_default())
    parser.add_argument("--destination", type=Path,
                        help="a NEW public evidence directory; existing paths are refused")
    parser.add_argument("--run", action="append", default=[],
                        help="additional completed directory relative to encfs-results")
    parser.add_argument("--history-run", action="append", default=[])
    parser.add_argument("--max-frames", type=int, default=20)
    parser.add_argument("--max-file-mib", type=int, default=64)
    parser.add_argument("--max-total-mib", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.source_root = args.source_root.resolve()
    require(args.max_frames > 0 and args.max_file_mib > 0 and args.max_total_mib > 0,
            "Evidence limits must be positive")
    require(args.dry_run or args.destination is not None, "Specify --destination or --dry-run")
    if args.destination:
        args.destination = args.destination.resolve()
        require(not args.destination.exists(), "Destination already exists")
    plan = build_plan(args)
    if not args.dry_run:
        publish(plan, args.destination)
    result = {"status": "validated-dry-run" if args.dry_run else "staged-completed-evidence",
              "destination": str(args.destination) if args.destination else None,
              "files": len(plan.files), "bytes": sum(r["size"] for r in plan.files.values()),
              "completed_runs": plan.completed, "historical_run_count": len(plan.histories),
              "endurance_observations": plan.observations,
              "project_completion_claim": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceError, OSError, ValueError) as error:
        print(f"Evidence staging refused: {error}", file=sys.stderr)
        raise SystemExit(1)
