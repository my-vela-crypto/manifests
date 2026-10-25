#!/usr/bin/env python3
"""Package only a fully accepted EncFS extension release.

Read-only --check-only refuses running/failed/missing evidence and never
creates a deliverable directory. Normal execution validates everything
before staging, does not modify repositories, and never overwrites an old
package. No status is prefilled as passed. Test master files are included
only in the explicit private area of the local archive, never GitHub data.
"""

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = next((p for p in Path(__file__).resolve().parents
             if (p / "nuttx").is_dir() and (p / "run-emulator.sh").is_file()),
            Path.cwd())
ARTIFACTS = ("nuttx", "nuttx.bin", ".config", "vela_system.bin", "vela_data.bin")
MANIFEST_BASE = "8817cd523602eaecdfe2c3cfc338b85a608b59b1"
SUFFIXES = {".json", ".log", ".png", ".bmp", ".csv", ".svg", ".pdf", ".md",
            ".txt", ".py", ".sh", ".html", ".toml", ".sha256"}
EXCLUDED = {"avd", "__pycache__", ".git", "node_modules", "frozen-inputs"}
PRIVATE_NAME = re.compile(r"(?:master-[0-9a-fA-F]{8}\.bin|volume-random\.bin|encfs\.keyslot(?:\.tmp)?)$")
PUBLICATION = {"output_created": False}


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def run(argv, cwd=None, stdout=None):
    result = subprocess.run(list(map(str, argv)), cwd=cwd,
                            stdout=stdout if stdout is not None else subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0,
            "Command failed: " + " ".join(map(str, argv)) + "\n" +
            result.stderr.decode(errors="replace"))
    return result.stdout


def git(path, *args):
    return run(["git", "-C", path, *args])


def verified(path, record, label):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"{label}: missing regular file {path}")
    if "size" in record:
        require(path.stat().st_size == record["size"], f"{label}: changed size {path}")
    require(sha(path) == record["sha256"], f"{label}: changed SHA256 {path}")


def check_report(directory, label, flexible_phases=False):
    path = directory / "report.json"
    require(path.is_file(), f"{label}: report missing: {path}")
    report = load(path)
    require(report.get("status") == "passed",
            f"{label}: report status={report.get('status')!r}; completion required")
    require(report.get("finished_at") and report.get("phases"), f"{label}: incomplete report")
    if not flexible_phases:
        require(all(p.get("status") == "passed" for p in report["phases"]),
                f"{label}: a phase is not passed")
    for phase in report["phases"]:
        interrupted = phase.get("intentional_interruption", {})
        require(phase.get("finished_at") or
                (phase.get("status") == "passed" and
                 interrupted.get("signal") == "SIGKILL" and
                 interrupted.get("clean_shutdown") is False),
                f"{label}/{phase['name']}: no completion/interruption evidence")
        if phase.get("guest_result"):
            require(phase["guest_result"].get("fail") == 0,
                    f"{label}/{phase['name']}: guest failure")
        serial = phase.get("serial_log")
        require(serial and Path(serial).is_file(), f"{label}: original serial missing")
    return {"label": label, "directory": directory, "path": path,
            "report": report, "sha256": sha(path)}


def stress(item, seconds):
    report = item["report"]
    require(report.get("requested_stress_seconds") == seconds,
            f"{item['label']}: exact {seconds}s request required")
    phases = [p for p in report["phases"] if "long_stress_result" in p]
    require(len(phases) == 1, f"{item['label']}: need one unique stress result")
    phase = phases[0]
    result = phase["long_stress_result"]
    require(result.get("requested_seconds") == seconds and
            result.get("completed") == 1 and result.get("errors") == 0 and
            result.get("workers") == 2 and
            all(result.get(k, 0) >= seconds * 1000 for k in
                ("elapsed_ms", "worker0_elapsed_ms", "worker1_elapsed_ms")) and
            all(result.get(k, 0) > 0 for k in
                ("worker0_iterations", "worker1_iterations", "written_bytes", "verified_bytes")) and
            phase.get("stress_host_elapsed_seconds", 0) >= seconds - 2,
            f"{item['label']}: actual duration/worker/counter gate failed")
    raw = Path(phase["serial_log"]).read_text(errors="replace")
    matches = re.findall(r"ENCFS_LONG_STRESS_RESULT ([^\r\n]+)", raw)
    require(len(matches) == 1, f"{item['label']}: unique raw result missing")
    parsed = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", matches[0])}
    require(parsed == result, f"{item['label']}: raw result differs from JSON")
    return phase


def analysis(item):
    path = item["directory"] / "analysis/analysis.json"
    require(path.is_file(), f"{item['label']}: independent analysis missing")
    value = load(path)
    require(value.get("status") == "passed" and
            value.get("input_report", {}).get("sha256") == item["sha256"],
            f"{item['label']}: stale or failed analysis")
    source = value["input_serial"]
    verified(Path(source["path"]), source, "analysis raw serial")
    for key in ("statistics_match_independently_parsed_serial",
                "each_worker_met_requested_duration",
                "both_worker_counts_advance_in_every_progress_interval",
                "payload_counters_match_deterministic_completed_cycle_pattern"):
        require(value["validation"].get(key) is True, f"analysis gate missing: {key}")
    for frame in value.get("verified_framebuffers", []):
        verified(Path(frame["path"]), frame, "analysis framebuffer")
    for plot in value.get("plots", []):
        verified(Path(plot["path"]), plot, "analysis plot")
    return path


def audit_report(item, path, strict=False, auditor=None):
    require(path.is_file(), f"{item['label']}: independent audit missing: {path}")
    record = load(path)
    digest = record.get("input_report_sha256", record.get("report_sha256"))
    if digest is None:
        digest = next((x["sha256"] for x in record.get("checked_artifacts", [])
                       if Path(x["path"]).resolve() == item["path"].resolve()), None)
    require(record.get("status") == "passed" and digest == item["sha256"],
            f"{item['label']}: failed/stale independent audit")
    if strict:
        require(record.get("strict_both_workers_continue_after_rotation") is True,
                "wrapped 24h audit must independently prove post-ACK I/O")
        require(len(record.get("post_rekey_acknowledgment_progress_samples", [])) >= 2,
                "wrapped audit lacks two original post-ACK progress samples")
        require(record.get("checks") and
                all(x.get("passed") is True for x in record["checks"]) and
                record.get("checks_passed") == len(record["checks"]),
                "wrapped audit has failed or incomplete checks")
        require(auditor is not None and auditor.is_file() and
                sha(auditor) == record.get("auditor_script_sha256"),
                "wrapped audit does not bind the actual reusable auditor version")
    for artifact in record.get("checked_artifacts", []):
        verified(Path(artifact["path"]), artifact, "independently audited input")
    for name, digest in record.get("serial_sha256", {}).items():
        verified(item["directory"] / name, {"sha256": digest}, "audited serial")
    return path


def frozen_and_exit(base, item, require_supervisor):
    exitpath = base / "runner-exit.json"
    if exitpath.is_file():
        exited = load(exitpath)
        require(exited.get("returncode") == 0 and exited.get("finished_at"),
                f"{item['label']}: observed runner exit was not 0")
        require(Path(exited["report"]).resolve() == item["path"].resolve(),
                f"{item['label']}: exit receipt points to another report")
        exit_observation = {"actual_exit_code": 0, "source": str(exitpath)}
    else:
        require(not require_supervisor,
                f"{item['label']}: supervisor runner-exit.json missing")
        launcher = base / "launcher.log"
        require(launcher.is_file() and "Report: " + str(item["path"]) in
                launcher.read_text(errors="replace"),
                "baseline functional acceptance requires its final flushed Report log")
        exit_observation = {
            "actual_exit_code": None,
            "source": "Original baseline was direct Popen without retained wait result",
            "functional_acceptance_basis": "complete report, raw serial, strict analysis, final coldboot, completion log",
        }
    receipt = load(base / "input-receipt.json")
    for relative, record in receipt["files"].items():
        verified(base / relative, record, "immutable endurance input")
    for name in ARTIFACTS:
        record = receipt["files"]["frozen-inputs/firmware/" + name]
        require(record["sha256"] == item["report"]["build_artifacts"][name]["sha256"],
                f"{item['label']}: tested {name} differs from frozen firmware")
    return exit_observation


def check_endurance(base, label, wrapped, auditor):
    item = check_report(base / "run", label)
    stress(item, 86400)
    names = {p["name"]: p for p in item["report"]["phases"]}
    if wrapped:
        required = {"legacy-rekey-rejection", "gui-wrapped-key-stress",
                    "wrapped-coldboot-and-negative-keys", "keyslot-cut-before-commit",
                    "keyslot-cut-recovery", "recovered-wrapper-coldboot"}
        require(required <= set(names), "wrapped 24h phases missing")
        cold = names["recovered-wrapper-coldboot"]
        require(cold.get("final_master_id") == 3 and cold.get("final_generation") == 4 and
                cold.get("only_current_master_available") is True and
                cold.get("coldboot_data_disk_unchanged") is True,
                "wrapped 24h final independent coldboot is incomplete")
        audit_report(item, base / "run/audit/keywrap-independent-audit.json", True, auditor)
    else:
        require("gui-persistence-coldboot" in names,
                "baseline 24h final coldboot missing")
        cold = names["gui-persistence-coldboot"]
        require(cold["image"]["sha256"] == names["gui-widgets-storage"]["image"]["sha256"],
                "baseline 24h read-only coldboot modified its data image")
    analysis(item)
    item["exit_observation"] = frozen_and_exit(base, item, wrapped)
    return item


def artifacts(item):
    records = item["report"].get("build_artifacts", item["report"].get("artifacts", {}))
    require(set(ARTIFACTS) <= set(records), f"{item['label']}: tested artifacts missing")
    for name in ARTIFACTS:
        verified(Path(records[name]["path"]), records[name], "tested firmware")
    return records


def source_state(root, expected):
    require(expected and re.fullmatch(r"[0-9a-f]{40}", expected),
            "supply the full final committed --expected-manifest-commit")
    manifest = root / ".repo/manifests"
    metadata = load(manifest / "encfs-sources.json")
    bases = {name: record["base"] for name, record in metadata["patches"].items()}
    bases.update({"apps": metadata["apps_base"], ".repo/manifests": MANIFEST_BASE})
    result = {}
    for name in ("nuttx", "apps", "vendor/openvela", ".repo/manifests"):
        repository = root / name
        commit = git(repository, "rev-parse", "HEAD").decode().strip()
        require(not git(repository, "status", "--porcelain=v1", "-uno").strip(),
                f"{name}: tracked files must be committed first")
        git(repository, "merge-base", "--is-ancestor", bases[name], commit)
        if name == ".repo/manifests":
            require(commit == expected, "manifest HEAD differs from selected final commit")
            require(not git(repository, "status", "--porcelain=v1", "--untracked-files=all").strip(),
                    "manifest has uncommitted/untracked delivery files")
            files = git(repository, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
            require(not any(PRIVATE_NAME.search(Path(f).name) for f in files),
                    "raw test master/keyslot material must never enter GitHub manifest")
            changed_paths = git(repository, "log", "--format=", "--name-only",
                                bases[name] + ".." + commit).decode().splitlines()
            require(not any(PRIVATE_NAME.search(Path(f).name) for f in changed_paths if f),
                    "raw provider material exists in committed manifest history")
            require(not any(line.startswith("120000 ") for line in
                            git(repository, "ls-tree", "-r", "HEAD").decode().splitlines()),
                    "committed manifest snapshot must not contain symlinks")
        history = git(repository, "log", "--format=%H%x09%aI%x09%s", "--reverse",
                      bases[name] + ".." + commit).decode().splitlines()
        result[name] = {"base": bases[name], "commit": commit,
                        "history": [dict(zip(("sha", "authored_at", "subject"), x.split("\t", 2)))
                                    for x in history]}
    return result


def last_images(item):
    found = {}
    def walk(value, trail=()):
        if isinstance(value, dict):
            path = value.get("path")
            if path and "sha256" in value and Path(path).name == "vela_data.bin" and \
                    "avd" in Path(path).parts:
                found[path] = {**value, "source_location": ".".join(trail)}
            for key, child in value.items():
                walk(child, trail + (key,))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, trail + (str(i),))
    walk(item["report"])
    require(found, f"{item['label']}: no measured runtime images")
    for path, record in found.items():
        verified(Path(path), record, "last measured runtime data image")
        record["size"] = Path(path).stat().st_size
    return found


def copy(source, destination, sparse=False):
    require(Path(source).is_file() and not Path(source).is_symlink(),
            f"copy source is not regular: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if sparse:
        run(["cp", "--sparse=always", "--preserve=mode,timestamps", "--", source, destination])
    else:
        shutil.copy2(source, destination)


def evidence(directory, destination):
    for parent, children, files in os.walk(directory):
        children[:] = sorted(x for x in children if x not in EXCLUDED)
        for name in sorted(files):
            source = Path(parent) / name
            if source.is_symlink() or source.suffix.lower() not in SUFFIXES or \
                    PRIVATE_NAME.search(name) or "coredump" in name.lower() or name.startswith("core."):
                continue
            copy(source, destination / source.relative_to(directory))
    # Raw captures only; never recursively copy a share containing masters.
    avd = directory / "avd"
    if avd.is_dir():
        for source in sorted(avd.glob("*.vvd/share/**/*.bmp")):
            if source.is_file() and not source.is_symlink():
                copy(source, destination / "raw-screenshots" / source.relative_to(avd))


def evidence_inventory(directory):
    """Read-only snapshot of exactly the controlled files copy_evidence uses."""
    found = {}
    require(directory.is_dir(), f"evidence directory missing: {directory}")
    for parent, children, files in os.walk(directory):
        children[:] = sorted(x for x in children if x not in EXCLUDED)
        for name in sorted(files):
            source = Path(parent) / name
            if source.is_symlink() or source.suffix.lower() not in SUFFIXES or \
                    PRIVATE_NAME.search(name) or "coredump" in name.lower() or name.startswith("core."):
                continue
            found[str(source)] = {"size": source.stat().st_size, "sha256": sha(source)}
    for source in sorted((directory / "avd").glob("*.vvd/share/**/*.bmp")):
        if source.is_file() and not source.is_symlink():
            found[str(source)] = {"size": source.stat().st_size, "sha256": sha(source)}
    return found


def readme(stage, state, items, release_date):
    text = f'''# 第二、三层扩展最终交付（{release_date}）

本包只有在旧基线与新 wrapped 固件两轮实际 86400 秒功能验收、独立原始串口分析和最终冷启动通过后才生成。新 wrapped 另要求 supervisor 实际 wait 返回 0；旧 baseline 最初由直接 Popen 启动，若没有真实 wait 证据，退出码明确记为 null（未观察到），不会补造 0。旧基线以完整报告、原串口、严格分析、冷启动和结束日志作功能验收依据。具体统计见原报告及 packaging-receipt.json。视频按要求不制作。

`source-bundles/` 保存四个项目的增量 Git bundle，恢复时需要 local-commits.json 中的基线对象。`source/manifest/` 是已提交 manifest {state['.repo/manifests']['commit']} 的完整快照。历史报告中的 dirty/source revision 保留原样；实测固件由 firmware/index.json 的哈希与原报告关联，不将旧基线固件冒充新功能固件。

firmware/storage_final、firmware/gui、firmware/extended_release 是本轮三种实测固件；firmware/baseline_gui 保存扩展前旧基线的冻结固件。validation/ 收录原始报告、串口、analysis、完整 PNG 与原 BMP；selected-frames/ 另给快速查阅副本。validation/history/ 只标记历史过程，不计入本轮通过。

images/index.json 记录每张实际测试盘在报告中的最后哈希与包内路径，避免把同一路径早期旧哈希用于后来已写入的盘。images/private-test-provider/ 只属于这个本地测试交付包，含 wrapped 最终测试盘对应的 slot 和唯一 current master，绝不上传 GitHub。真实生产秘密不应放在此模拟器文件后端。

## 复现

在独立新工作区：

```bash
repo init -u https://github.com/my-vela-crypto/manifests.git -b dev -m encfs-tested.xml
repo sync -c -j8
python3 .repo/manifests/tools/apply_encfs.py --source-root "$PWD"
```

已有盘冷启动只选择原 provider 并 start，不再 provision。wrapped 盘与它的 encfs.keyslot、当前 master 文件必须成套保存；完整手工步骤见 source/manifest/docs/encfs_keywrap.md。legacy 直派生卷可读但拒绝伪换钥，TEE/真实介质断电/历史 slot 防回滚不在本模拟器保证范围。

24h wrapped 终测完成后，先分析原始串口，再执行严格独立审计：

```bash
python3 .repo/manifests/tools/analyze_encfs_stress.py --results-dir <wrapped>/run --output-dir <wrapped>/run/analysis --phase gui-wrapped-key-stress
python3 .repo/manifests/tools/audit_encfs_keywrap.py --results-dir <wrapped>/run --output-dir <wrapped>/run/audit --frozen-input-root <wrapped> --require-post-ack-progress
```

严格审计必须从成功换钥 ACK 后的原串口找到两个新进度样本，且两个 worker 都继续增长，不能依赖旧 runner 的 proof 布尔值。全局 heap 有界观测不代表零泄漏证明；统计字节为逻辑载荷，不是物理磁盘吞吐量。

在本包根目录执行 `sha256sum -c SHA256SUMS` 校验全部文件。外层 .tar.gz.sha256 校验整个稀疏压缩包。packaging-tools/ 中保留打包与审计工具的实际版本。
'''
    (stage / "README.md").write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--release-date", required=True, help="Explicit YYYYMMDD, e.g. 20261026")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--expected-manifest-commit")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--wrapped-root", type=Path)
    parser.add_argument("--storage-results", type=Path)
    parser.add_argument("--extended-results", type=Path)
    parser.add_argument("--gui-results", type=Path)
    parser.add_argument("--keywrap-results", type=Path)
    parser.add_argument("--gui-exit-receipt", type=Path)
    parser.add_argument("--final-report", type=Path)
    parser.add_argument("--extra-evidence", type=Path, action="append", default=[])
    args = parser.parse_args()
    require(re.fullmatch(r"\d{8}", args.release_date), "release date must be YYYYMMDD")
    release = datetime.strptime(args.release_date, "%Y%m%d").date()
    root = args.workspace.resolve()
    results = root / "encfs-results"
    output = (args.output_dir or root / "高级操作系统/输出" /
              ("encfs-layer23-extended-" + args.release_date)).resolve()
    archive = Path(str(output) + ".tar.gz")
    checksum = Path(str(archive) + ".sha256")
    for path in (output, archive, checksum):
        require(not os.path.lexists(path), f"refusing existing deliverable: {path}")

    # All completion gates run before mkdir/staging/archive creation.
    baseline_root = (args.baseline_root or results / "endurance-24h-baseline-20261024").resolve()
    wrapped_root = (args.wrapped_root or results / "endurance-24h-wrapped-20261024").resolve()
    auditor = root / ".repo/manifests/tools/audit_encfs_keywrap.py"
    baseline = check_endurance(baseline_root, "baseline-24h", False, auditor)
    wrapped = check_endurance(wrapped_root, "wrapped-24h", True, auditor)
    storage = check_report((args.storage_results or results / "extended-20261024/storage-final-01").resolve(), "storage_final")
    extended = check_report((args.extended_results or results / "extended-20261024/acceptance-final-01").resolve(), "extended_release", True)
    gui = check_report((args.gui_results or results / "gui-extension-20261025-02").resolve(), "gui-final")
    short = check_report((args.keywrap_results or results / "keywrap-180-20261025-02").resolve(), "keywrap-short")
    items = [baseline, wrapped, storage, extended, gui, short]
    for item in items:
        require(release >= datetime.fromisoformat(item["report"]["finished_at"]).astimezone(
            timezone(timedelta(hours=8))).date(), "release date precedes actual completion")
        artifacts(item)
    audit_report(storage, storage["directory"] / "independent-audit.json")
    audit_report(extended, extended["directory"] / "independent-audit.json")
    short_audit = results / "extension-20261025/keywrap-180-independent-audit.json"
    audit_report(short, short_audit, True, auditor)
    analysis(gui)
    gui_exit = load(args.gui_exit_receipt or results / "extension-20261025/gui-wrapper-rc.json")
    require(gui_exit.get("status") == "passed" and gui_exit.get("program_return_code") == 0 and
            Path(gui_exit["results_dir"]).resolve() == gui["directory"],
            "final full GUI direct-parent exit receipt not passed/0")
    current = source_state(root, args.expected_manifest_commit)
    manifest = root / ".repo/manifests"
    final_report = args.final_report or manifest / "docs/encfs_extension_report.md"
    require(final_report.is_file(), "final extension report missing")
    require(auditor.is_file(), "commit/copy reusable auditor into manifest tools first")
    for item in (storage, extended, gui, short, wrapped):
        sources = item["report"].get("source_revisions", {})
        for name in ("nuttx", "apps"):
            require(sources.get(name, {}).get("revision") == current[name]["commit"],
                    f"{item['label']}: packaged {name} differs from actual tested commit")
            require(not any(not x.startswith("?? ") for x in sources[name].get("changes", [])),
                    f"{item['label']}: tested {name} had tracked edits")
    image_sets = {item["label"]: last_images(item) for item in items}
    require((root / "高级操作系统/输出/第二、三层扩展实现与测试报告.md").read_bytes() ==
            final_report.read_bytes(), "course extension report differs from committed report")
    # Every wrapped final disk carries only its authenticated current fixture.
    private = []
    for item in (wrapped, short):
        cold = next(p for p in item["report"]["phases"] if p["name"] == "recovered-wrapper-coldboot")
        disk = Path(cold["image"]["path"])
        directory = disk.parent / "share/keys"
        files = sorted(x.name for x in directory.iterdir())
        require(files == ["encfs.keyslot", "master-00000003.bin"], "wrapped fixture is not current-only")
        slot = directory / "encfs.keyslot"
        master = directory / "master-00000003.bin"
        require(slot.stat().st_size == 112 and master.stat().st_size == 32,
                "wrapped fixture lengths invalid")
        private.append({"label": item["label"], "disk": str(disk),
                        "files": [{"path": str(p), "size": p.stat().st_size, "sha256": sha(p)}
                                  for p in (slot, master)]})
    for name, item in (("gui", wrapped), ("baseline_gui", baseline)):
        require(all(artifacts(item)[f]["sha256"] ==
                    load((wrapped_root if name == "gui" else baseline_root) / "input-receipt.json")["files"]["frozen-inputs/firmware/" + f]["sha256"]
                    for f in ARTIFACTS), "frozen firmware mismatch")
    supplements = [results / "extension-20261024", results / "extension-20261025",
                   *[p.resolve() for p in args.extra_evidence]]
    histories = sorted((results / "extended-20261024").glob("exploratory-*"))
    histories += [p for p in (results / "keywrap-180-20261025-01", results / "keywrap-60-20261024-02",
                              results / "gui-extension-20261025-01") if p.is_dir()]
    controlled = {str(p): evidence_inventory(p) for p in
                  [*[x["directory"] for x in items], *supplements, *histories]}
    tool_files = [Path(__file__), auditor]
    control_files = {str(p): {"size": p.stat().st_size, "sha256": sha(p)} for p in tool_files}
    receipt = {"status": "validated", "validated_at": now(), "release_date": args.release_date,
               "manifest_commit": current[".repo/manifests"]["commit"],
               "reports": {x["label"]: {"path": str(x["path"]), "sha256": x["sha256"]} for x in items},
               "completion_gate": "two real 86400s functional runs, final coldboots, original analysis, strict post-ACK audit; wrapped supervisor exit0; baseline exit observation kept explicit",
               "runner_exit_observations": {x["label"]: x["exit_observation"] for x in (baseline, wrapped)},
               "private_fixture_scope": "local archive only; never GitHub validation",
               "private_fixtures": private}
    if args.check_only:
        print(json.dumps({"status": "validated-check-only", "output_created": False,
                          "reports": receipt["reports"]}, ensure_ascii=False))
        return 0
    require(output.parent.is_dir(), "output parent must already exist")

    with tempfile.TemporaryDirectory(prefix=".encfs-package-", dir=output.parent) as temp:
        temporary = Path(temp)
        stage = temporary / output.name
        stage.mkdir(mode=0o700)
        (stage / "source-bundles").mkdir()
        for name, info in current.items():
            path = stage / "source-bundles" / (name.replace("/", "_").strip(".") + ".bundle")
            run(["git", "-C", root / name, "bundle", "create", path,
                 info["base"] + ".." + info["commit"]])
            run(["git", "-C", root / name, "bundle", "verify", path])
        manifest_tar = temporary / "manifest.tar"
        with manifest_tar.open("wb") as stream:
            git_archive = ["git", "-C", manifest, "archive", "--format=tar", "HEAD"]
            run(git_archive, stdout=stream)
        target = stage / "source/manifest"
        target.mkdir(parents=True)
        with tarfile.open(manifest_tar) as tree:
            for member in tree.getmembers():
                require(not member.name.startswith("/") and ".." not in PurePosixPath(member.name).parts,
                        "unsafe committed manifest archive path")
                require(not member.issym() and not member.islnk(), "manifest snapshot must not contain links")
            tree.extractall(target)
        (stage / "local-commits.json").write_text(json.dumps(current, indent=2) + "\n")
        firmware_index = {}
        for name, item in (("storage_final", storage), ("gui", wrapped),
                           ("extended_release", extended), ("baseline_gui", baseline)):
            firmware_index[name] = {"source_report": str(item["path"]),
                                    "report_sha256": item["sha256"], "files": {}}
            for filename, record in artifacts(item).items():
                if filename not in ARTIFACTS:
                    continue
                destination = stage / "firmware" / name / filename
                copy(Path(record["path"]), destination, filename.endswith(".bin"))
                verified(destination, record, "copied measured firmware")
                firmware_index[name]["files"][filename] = {**record, "package_path": str(destination.relative_to(stage))}
        (stage / "firmware/index.json").write_text(json.dumps(firmware_index, indent=2) + "\n")
        image_index = []
        for item in items:
            evidence(item["directory"], stage / "validation" / item["label"])
            for source, record in image_sets[item["label"]].items():
                destination = stage / "images" / item["label"] / Path(source).parent.name / "vela_data.bin"
                copy(Path(source), destination, True)
                verified(destination, record, "copied final measured image")
                image_index.append({**record, "source_path": source,
                                    "source_report": str(item["path"]), "report_sha256": item["sha256"],
                                    "package_path": str(destination.relative_to(stage)),
                                    "allocated_bytes": destination.stat().st_blocks * 512})
        for fixture in private:
            for record in fixture["files"]:
                source = Path(record["path"])
                destination = stage / "images/private-test-provider" / fixture["label"] / source.name
                copy(source, destination)
                destination.chmod(0o600)
                verified(destination, record, "paired current simulator provider material")
                record["package_path"] = str(destination.relative_to(stage))
        (stage / "images/index.json").write_text(json.dumps(image_index, indent=2) + "\n")
        (stage / "images/private-test-provider/index.json").write_text(json.dumps(private, indent=2) + "\n")
        for base in (baseline_root, wrapped_root):
            for name in ("input-receipt.json", "process.json", "runner-exit.json", "supervise.py", "launcher.log"):
                if (base / name).is_file():
                    copy(base / name, stage / "validation" / base.name / name)
            for source in (base / "frozen-inputs/tools").glob("*.py"):
                copy(source, stage / "validation" / base.name / "frozen-tools" / source.name)
        for directory in supplements:
            evidence(directory, stage / "validation/supporting" / directory.name)
        for history in histories:
            evidence(history, stage / "validation/history" / history.name)
        for item in (gui, short, wrapped):
            for phase in item["report"]["phases"]:
                for frame in phase.get("frames", []):
                    if not frame["label"].startswith("stress-"):
                        source = Path(frame["path"])
                        copy(source, stage / "selected-frames" / item["label"] / source.name)
        copy(final_report, stage / "扩展验收报告.md")
        for name in ("PROJECT_HANDOFF.md", "输出/第二层与第三层实现框架.md"):
            source = root / "高级操作系统" / name
            if source.is_file():
                copy(source, stage / "course-documents" / source.name)
        copy(Path(__file__), stage / "packaging-tools/package_extended.py")
        copy(auditor, stage / "packaging-tools/audit_encfs_keywrap.py")
        readme(stage, current, items, args.release_date)

        # Recheck live source inputs after copying, before any final name exists.
        require(source_state(root, args.expected_manifest_commit) == current,
                "source repositories changed while packaging")
        for item in items:
            require(sha(item["path"]) == item["sha256"], "report changed while packaging")
            artifacts(item)
            require(last_images(item) == image_sets[item["label"]], "runtime image changed while packaging")
        for directory, measured in controlled.items():
            require(evidence_inventory(Path(directory)) == measured,
                    "controlled evidence changed while packaging: " + directory)
        for source, measured in control_files.items():
            verified(Path(source), measured, "packaging tool input")
        for fixture in private:
            for measured in fixture["files"]:
                verified(Path(measured["path"]), measured, "private provider input")
        receipt.update(status="verified", finished_copy_at=now())
        (stage / "packaging-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        require(not any(p.is_symlink() for p in stage.rglob("*")), "packaged symlinks are not allowed")
        (stage / "SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(stage)}\n" for p in files))
        with (temporary / "checksums.log").open("wb") as stream:
            run(["sha256sum", "-c", "SHA256SUMS"], stage, stream)
        temporary_archive = temporary / archive.name
        run(["tar", "--sparse", "--format=gnu", "-czf", temporary_archive,
             "-C", temporary, output.name])
        listing = run(["tar", "-tzf", temporary_archive]).decode().splitlines()
        require(output.name + "/SHA256SUMS" in listing, "archive checksum list missing")
        require(all(name.startswith(output.name + "/") and
                    ".." not in PurePosixPath(name).parts for name in listing),
                "archive path escaped its root")
        run(["tar", "-dzf", temporary_archive, "-C", temporary])
        archive_digest = sha(temporary_archive)
        # Publish only after all checks; prior deliverables remain untouched.
        for path in (output, archive, checksum):
            require(not os.path.lexists(path), "deliverable appeared during packaging")
        run(["mv", "--no-clobber", "--no-target-directory", "--", stage, output])
        require(not stage.exists(), "publication target appeared; old output was preserved")
        PUBLICATION["output_created"] = True
        # Both paths share the output filesystem. link/x-create refuse a
        # late conflicting archive or checksum instead of overwriting it.
        os.link(temporary_archive, archive)
        with checksum.open("x") as stream:
            stream.write(f"{archive_digest}  {archive.name}\n")
        run(["sha256sum", "-c", checksum.name], archive.parent)
        print(json.dumps({"status": "passed", "archive": str(archive),
                          "archive_sha256": archive_digest, "archive_bytes": archive.stat().st_size,
                          "checked_files": len(files), "runtime_images": len(image_index)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(json.dumps({"status": "refused", "error": str(error),
                          "output_created": PUBLICATION["output_created"]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
