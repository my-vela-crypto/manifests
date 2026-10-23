# my-vela-crypto manifests

本仓库是 [openvela](https://github.com/open-vela) 的定制 manifest，在官方源码基础上：

- **nuttx** 指向 `my-vela-crypto/nuttx`（内核个性化修改）
- 其余子项目默认从 openvela 官方仓库同步
- 通过 linkfile 提供 **`run-emulator.sh`**，解决 CMake 离树编译后官方 `emulator.sh` 无法直接启动的问题

## 环境要求

- Ubuntu 22.04（**x86_64 / arm64 均可**）
- 至少 40 GB 磁盘、16 GB 内存
- 源码路径**仅使用英文字母**（不要用 `桌面` 等中文目录，否则编译/模拟器会异常）
- 已安装：git、git-lfs、curl、python3、build-essential（cmake/ninja 由 prebuilts 提供，ARM 宿主会自动选用对应架构）

```bash
sudo apt update
sudo apt install git curl cmake python3 libc++abi-dev build-essential adb

curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | sudo bash
sudo apt-get install git-lfs
git lfs install
```

安装 repo 工具：

```bash
curl -sSL "https://storage.googleapis.com/git-repo-downloads/repo" > repo
chmod +x repo
sudo mv repo /usr/local/bin
```

## 获取源码

在空目录中执行（**请直接使用 GitHub 地址，不要加 ghproxy 等代理前缀**）：

```bash
mkdir openvela && cd openvela

repo init -u https://github.com/my-vela-crypto/manifests.git \
  -b dev -m openvela.xml \
  --repo-url=https://mirrors.tuna.tsinghua.edu.cn/git/git-repo/ \
  --git-lfs

repo sync -c -j8
```

同步完成后，openvela 根目录会出现：

- `build.sh` → 来自 `nuttx/tools/build.sh`
- `run-emulator.sh` → 来自 `nuttx/tools/run-emulator.sh`

## 编译（goldfish arm64 模拟器）

在 openvela 根目录（x86_64 与 arm64 宿主使用**相同命令**；`build.sh` 会在 ARM 上自动选用 `linux-aarch64` 的 cmake/ninja）：

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap/ --cmake -j$(nproc)
```

ARM 宿主若报找不到 prebuilts，请先完整同步：

```bash
repo sync -c -j8
ls prebuilts/cmake/linux-aarch64/bin/cmake
```

编译产物位于：

```text
cmake_out/vela_goldfish-arm64-v8a-ap/
  nuttx
  nuttx.bin
  vela_data.bin
  vela_system.bin
```

## 启动模拟器

**不要使用官方 quickstart 中的 `./emulator.sh cmake_out/...`**，请使用本 manifest 提供的入口：

```bash
# 默认 goldfish-arm64-v8a-ap，带 GUI
./run-emulator.sh

# 无窗口（SSH / 终端调试）
./run-emulator.sh -no-window

# 指定 config
./run-emulator.sh goldfish-arm64-v8a-ap -no-window
```

成功启动后，终端会出现 NSH 提示符：

```text
goldfish-armv8a-ap>
```

退出模拟器：在运行 `./run-emulator.sh` 的终端按 `Ctrl+C`。

### 原理说明

官方 `emulator.sh` 从 `nuttx/` 目录读取内核与镜像，而 `--cmake` 编译产物在 `cmake_out/`。`run-emulator.sh` 会在启动前自动将产物 symlink 到 `nuttx/`，再调用 `./emulator.sh vela`。

## 仓库结构说明

| 仓库 | 远程 | 说明 |
|------|------|------|
| `manifests` | `my-vela-crypto/manifests` | 本仓库，编排所有子项目 |
| `nuttx` | `my-vela-crypto/nuttx` | 唯一 fork，用于内核定制 |
| 其他子项目 | openvela 官方 | `repo sync` 自动更新 |

## 日常更新

```bash
# 更新 manifest 本身
cd path/to/manifests
git pull origin dev

# 更新整个 openvela 树
cd path/to/openvela
repo sync -c -j8
```

## 提交改动

- **manifest 变更**（如 `openvela.xml`）：提交到 `my-vela-crypto/manifests`
- **nuttx / run-emulator.sh 变更**：提交到 `my-vela-crypto/nuttx`
- 推送 nuttx 后，在 openvela 根目录 `repo sync` 即可同步

```bash
# manifests
cd manifests && git push origin dev

# nuttx
cd openvela/nuttx && git push my-vela-crypto dev
```

## 常见问题

### `git pull` 提示「需要指定如何调和偏离的分支」

本地与远程各有独立提交时：

```bash
git pull --rebase origin dev
git push origin dev
```

### 曾使用 ghproxy 导致 push 失败

检查 remote 是否为直连 GitHub：

```bash
git remote -v
# 应为 https://github.com/my-vela-crypto/...
# 不应为 https://ghproxy.net/github.com/...
```

修正：

```bash
git remote set-url origin https://github.com/my-vela-crypto/manifests.git
```

### ARM 宿主编译报 `ld-linux-x86-64.so.2` / rosetta error

说明在 ARM 机器上调用了 x86_64 的 cmake。请确保：

1. 源码在**纯英文路径**（如 `~/openvela`）
2. 已 `repo sync` 且存在 `prebuilts/cmake/linux-aarch64/`
3. 使用本 manifest 提供的 **nuttx fork** 中的 `build.sh`（已含 ARM 自动处理；`repo sync` 后根目录 `./build.sh` 即为此版本）

若仍失败，可手动验证：

```bash
file "$(which cmake)"   # ARM 上应含 ARM aarch64，而非 x86-64
```

### 模拟器报 `No initial vela_system image`

说明 `cmake_out/` 产物未 staging 到 `nuttx/`，请使用 `./run-emulator.sh` 而非直接 `./emulator.sh`。

### UI 启动时提示找不到 ADB

用 GUI 启动模拟器时，若弹出：

```text
Could not automatically detect an ADB binary...
```

说明宿主机未安装 `adb`。openvela 预置包不含 ADB，需自行安装：

```bash
sudo apt install adb
```

安装后重启模拟器即可。若仍提示，在模拟器窗口 **Extended Controls (`...`) → Settings → General** 中，将 ADB 路径设为 `/usr/bin/adb`。

验证：

```bash
adb devices
# 模拟器运行时应显示 emulator-5554 等设备
```

此警告不影响 NSH 终端（`goldfish-armv8a-ap>`）的基本使用；仅在使用 `adb shell`、`adb push` 等调试功能时需要安装。

## EncFS 高级操作系统课程项目

第二层 `encpathfs` 负责透明、可认证的文件名加密，第三层 `encfs` 负责密钥派生、建盘、挂载、状态和失败回滚。完整链路是 `/data → encpathfs → 隐藏的 FatFs/exFAT → /dev/encdata0 → blkcrypt → /dev/virtblk1`。

团队 [nuttx/dev](https://github.com/my-vela-crypto/nuttx/tree/dev) 已包含两层实现与验收修复（`7934920bb876195f2f45fc76b863235d5129c6e3`）。本 manifest 同时保存完整内核补丁、`patches/vendor_openvela/encfs.patch` 板级补丁及 `overlays/apps/` 的 13 个应用文件，以便从记录的原始基线重现。应用器会识别已进入团队内核的相同修改并跳过，仍需安装板级与应用改动。基线和全部文件校验值由 `encfs-sources.json` 记录，发布及验收结果见 `docs/encfs_test_report.md`。

首次同步后，根目录的快捷链接可能尚不存在，**先执行 Python 入口**：

```bash
repo sync -c -j8
python3 .repo/manifests/tools/apply_encfs.py
```

脚本先完成两个仓库补丁及全部 apps 覆盖文件的校验和/冲突预检，再应用变化；只有成功后才创建根目录 `apply-encfs.sh`。以后可用该快捷入口重复应用，已安装的相同内容不会再次改写：

```bash
./apply-encfs.sh
```

脚本遇到基线或本地修改冲突时，先处理冲突，不要强行覆盖成员改动。升级同步后也应重新应用并检查结果。后续若改用已包含这些变化的团队仓库，应同时调整补丁与覆盖文件，避免重复维护。

本地扩展验收已完成：Guest 665 项检查通过，包含两组各 1000 轮文件回归、100 次启停和同盘三次冷启动；两处验收发现的问题及证据见 `docs/encfs_test_report.md`。本地编译和模拟器验收不需要 GitHub 写权限。

### 构建加密专用配置

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs/ --cmake -j$(nproc)
```

输出目录是 `cmake_out/vela_goldfish-arm64-v8a-ap-encfs/`。该配置包含测试 API 和验收命令，使用 `encfs> ` 提示符。初始 `vela_data.bin` 是新的 256 MiB 空白盘，不含明文 FAT 或预置文件；第一次开机 `encfs start` 失败并进入 NSH 属于预期。启动失败不会回退到普通 FAT 或 tmpfs `/data`。

### 首次建盘与日常使用

首次选择一个新的专用实例名并记住它。以下变量在当前宿主终端中保留，重启时使用相同值：

```bash
ENCFS_AVD_DIR="$PWD/encfs-manual-avd"
ENCFS_INSTANCE="encfs-demo-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ENCFS_AVD_DIR"
printf '%s\n' "$ENCFS_INSTANCE"
ANDROID_AVD_HOME="$ENCFS_AVD_DIR" CUSTOM_AVD_SPACE="$ENCFS_AVD_DIR" \
  ./run-emulator.sh goldfish-arm64-v8a-ap-encfs \
  -keep "$ENCFS_INSTANCE" -no-window -no-audio -gpu off -accel off
```

在 Guest 的 `encfs> ` 提示符下，只有首次明确要清空这个专用数据盘时执行：

```text
encfs provision --erase
encfs start
encfs status
mkdir /data/demo
echo course-data > /data/demo/readme.txt
cat /data/demo/readme.txt
encfs stop
```

`provision --erase` 会通过加密设备重新格式化为 exFAT，破坏卷内旧数据；普通 `start` 不格式化、不补建卷头。所有需要保留的文件应通过 `/data` 写入。先 `encfs stop` 成功，再退出模拟器。

日常重启复用相同 `-keep "$ENCFS_INSTANCE"` 和两个目录变量，**不要再次 provision**。启动脚本会尝试 `encfs start`；手动重复 start 也可：

```text
encfs start
cat /data/demo/readme.txt
encfs status
encfs stop
```

打开文件或目录时 stop 返回忙，运行状态保留；释放句柄后重试。有清理错误时保留资源及错误阶段，先处理或重试 stop，不在线换钥。

### 可复现验收

宿主检查：

```bash
python3 nuttx/tools/tests/encfs_crypto/test_crypto.py
python3 nuttx/tools/tests/encpathfs/test_encpathfs.py
nuttx/tools/tests/encfs_manager/run_host_tests.sh
```

真实模拟器 runner 需要 Python `pexpect`（Ubuntu 可安装 `python3-pexpect`）：

```bash
python3 .repo/manifests/tools/test_encfs.py \
  --source-root "$PWD" \
  --build-dir "$PWD/cmake_out/vela_goldfish-arm64-v8a-ap-encfs" \
  --results-dir "$PWD/encfs-results/acceptance-$(date +%Y%m%d-%H%M%S)"
```

Runner 新建专用实例，依次验证独立名称层、完整链路写入、同一加密实例重启读回。只有首次完整链路阶段执行显式 provision。每阶段干净卸载并结束模拟器后，它检查实际 `avd/<实例>.vvd/vela_data.bin`，保留串口日志、镜像路径/哈希与 `report.json`。独立普通 exFAT 解析应显示 `.p` 下为 `E1_` 名称；完整加密盘应检索不到指定完整内容及逻辑文件名。任一断言失败即非零退出。当前预编译模拟器会另外创建 `$HOME/.vela/<唯一实例>.ini` 注册文件，数据镜像仍保存在本次结果目录。

本文不预填模拟器 PASS；请以实际运行报告为准。详细接口、卷格式和测试判据见 [接口说明](docs/encfs_interfaces.md) 与 [验收计划](docs/encfs_test_plan.md)。

### 当前边界

- QEMU 的 `board_uniquekey()` 返回固定伪密钥；本配置还开启 `CONFIG_ENCFS_TESTING`。它用于课程演示，不是生产密钥保护方案。
- 主密钥固定 32 字节，经 HKDF-SHA256、分别以不含 NUL 的 `blkcrypt`/`encpathfs` 为 salt 派生两层 32 字节子密钥。
- 逻辑名只支持 1–128 字节可打印 ASCII；128 字节编码后为 234 字节。中文 UTF-8、软/硬链接和 mmap 不在支持范围内。
- 下层实际格式是 FatFs 管理的 **exFAT**，挂载类型仍为 `fatfs`。已有目标 rename 返回 `EEXIST`，不承诺替换语义或断电原子性。
- XTS 不提供文件内容或任意扇区认证；SIV 认证文件名及卷元数据。未检出已知字符串不是完整安全证明，空白未写区域也不应声称已加密或已安全擦除。
