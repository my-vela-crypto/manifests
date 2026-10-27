#!/usr/bin/env python3
"""Start the course demo and prepare its keys without machine-specific paths."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import shlex
import sys
import time
import uuid


CONFIG = "goldfish-arm64-v8a-ap-encfs-gui"
ARTIFACTS = ("nuttx", "nuttx.bin", "vela_system.bin", "vela_data.bin")


def source_root(explicit=None):
    if explicit is not None:
        candidates = [explicit.resolve()]
    else:
        candidates = Path(__file__).resolve().parents
    for root in candidates:
        if (root / "nuttx").is_dir() and (root / "run-emulator.sh").is_file():
            return root
    raise ValueError("请从 openvela 项目运行，或使用 --source-root 指定项目目录")


def rooted(root, value):
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def session(args, root, create=False):
    directory = rooted(root, args.session)
    path = directory / "session.json"
    if path.exists():
        data = json.loads(path.read_text())
        if data.get("schema") != 1:
            raise ValueError("演示实例配置格式不符")
        instance = data["instance"]
        if args.instance and args.instance != instance:
            raise ValueError("此演示目录已有实例；新一轮请换用 --session 目录")
    elif create:
        instance = args.instance or "encfs-course-" + uuid.uuid4().hex[:12]
        validate_instance(instance)
        directory.mkdir(parents=True, exist_ok=True)
        # Store identity only. All paths are recalculated after relocation.
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"schema": 1, "instance": instance}, stream)
            stream.write("\n")
    elif getattr(args, "dry_run", False):
        instance = args.instance or "encfs-course-preview"
    else:
        raise ValueError("请先运行 start 创建本轮演示实例")
    validate_instance(instance)
    avd = directory / "avd"
    return directory, instance, avd


def validate_instance(instance):
    if not isinstance(instance, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", instance):
        raise ValueError("实例名须由英文字母、数字、下划线、点或短横线组成")


def process_identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": int(fields[19]), "state": fields[0],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def live_process(saved):
    try:
        current = process_identity(saved["pid"])
    except (OSError, KeyError, ValueError):
        return False
    return (current["state"] != "Z" and all(current.get(key) == saved.get(key)
            for key in ("pid", "start_ticks", "boot_id")))


def start(args, root):
    build = rooted(root, args.build_dir or "cmake_out/vela_" + CONFIG)
    missing = [name for name in ARTIFACTS if not (build / name).is_file()]
    if missing:
        raise ValueError(
            f"固件目录缺少：{', '.join(missing)}\n"
            f"固件目录：{build}\n"
            "在项目根目录编译：\n"
            f"./build.sh vendor/openvela/boards/vela/configs/{CONFIG}/ --cmake -j4\n"
            "已有固件也可通过 --build-dir 指定目录"
        )
    directory, instance, avd = session(args, root, create=not args.dry_run)
    receipt = directory / "process.json"
    if receipt.exists() and live_process(json.loads(receipt.read_text())):
        raise ValueError("本轮模拟器已在运行；冷启动前先执行 stop")
    argv = [str(root / "run-emulator.sh"), CONFIG, "-keep", instance,
            "-no-audio", "-gpu", "swiftshader_indirect", "-accel", "off"]
    if args.headless:
        argv.append("-no-window")
    environment = os.environ.copy()
    environment.update(ANDROID_AVD_HOME=str(avd), CUSTOM_AVD_SPACE=str(avd),
                       VELA_BUILD_DIR=str(build))
    print(f"演示实例：{instance}\n数据目录：{avd}\n固件目录：{build}", flush=True)
    if args.dry_run:
        print(shlex.join(argv))
        return 0
    avd.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(process_identity(os.getpid())) + "\n")
    os.chdir(root)
    os.execve(argv[0], argv, environment)


def stop(args, root):
    directory, _, _ = session(args, root)
    receipt = directory / "process.json"
    if not receipt.exists():
        print("本轮模拟器未运行")
        return 0
    saved = json.loads(receipt.read_text())
    if not live_process(saved):
        print("本轮模拟器未运行")
        return 0
    try:
        os.kill(saved["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 30
    while live_process(saved):
        if time.monotonic() >= deadline:
            raise ValueError("本轮模拟器仍在退出，请稍后重试 stop")
        time.sleep(0.1)
    print("本轮模拟器已退出，演示数据盘与密钥已保留")
    return 0


def prepare_keys(args, root):
    _, instance, avd = session(args, root)
    share = avd / (instance + ".vvd") / "share"
    if not share.is_dir():
        raise ValueError("先启动本轮模拟器，等待 encfs> 提示符，再准备密钥")
    directory = share / "keys"
    if directory.exists():
        masters = [directory / f"master-{i:08x}.bin" for i in range(4)]
        valid_masters = all(p.is_file() and p.stat().st_size == 32 for p in masters)
        material = directory / "volume-random.bin"
        slot = directory / "encfs.keyslot"
        valid_volume = ((material.is_file() and material.stat().st_size == 64)
                        or (slot.is_file() and slot.stat().st_size == 112))
        if not valid_masters or not valid_volume:
            raise ValueError("已有密钥目录内容不完整，已保留原文件；新一轮请使用新的 --session")
        print("本轮测试密钥已存在，继续使用原文件；冷启动直接在 NSH 选择 /share/keys")
        return 0
    directory.mkdir(mode=0o700)
    for key_id in range(4):
        path = directory / f"master-{key_id:08x}.bin"
        with path.open("xb") as stream:
            stream.write(os.urandom(32))
        path.chmod(0o600)
    path = directory / "volume-random.bin"
    with path.open("xb") as stream:
        stream.write(os.urandom(64))
    path.chmod(0o600)
    print("测试密钥准备完成；回到 NSH 执行 encfs keyfiles /share/keys")
    return 0


def show_results(args, root):
    if args.report:
        path = rooted(root, args.report)
    else:
        path = Path(__file__).resolve().parents[1] / "docs/course-demo/24h-summary.json"
    report = json.loads(path.read_text())
    work = report["workload"]
    count = report.get("framebuffer_count")
    if count is None:
        count = len(report["verified_framebuffers"])
    print("24 小时双线程压力测试")
    print("结果：", report["status"])
    print(f"运行时间：{work['guest_elapsed_ms'] / 1000:,.3f} 秒")
    print(f"完成事务：{work['completed_iterations']:,} 次")
    print(f"线程 0：{work['worker0_iterations']:,} 次")
    print(f"线程 1：{work['worker1_iterations']:,} 次")
    print(f"错误数：{work['errors']}")
    print(f"逻辑写入：{work['logical_written_bytes']:,} 字节")
    print(f"读回校验：{work['logical_verified_read_bytes']:,} 字节")
    print(f"运行画面：{count} 张")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "stop", "keys", "results"):
        command = commands.add_parser(name)
        command.add_argument("--source-root", type=Path)
        if name != "results":
            command.add_argument("--session", default="encfs-results/course-demo",
                                 help="演示目录，相对项目根目录；冷启动使用同一目录")
            command.add_argument("--instance", help="首次创建时指定实例名")
        if name == "start":
            command.add_argument("--build-dir", type=Path)
            command.add_argument("--headless", action="store_true")
            command.add_argument("--dry-run", action="store_true")
        if name == "results":
            command.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        root = source_root(args.source_root)
        return {"start": start, "stop": stop, "keys": prepare_keys,
                "results": show_results}[args.command](args, root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"演示操作失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
