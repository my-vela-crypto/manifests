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
