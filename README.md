# my-vela-crypto manifests

本仓库是 [openvela](https://github.com/open-vela) 的定制 manifest，在官方源码基础上：

- **nuttx** 指向 `my-vela-crypto/nuttx`（内核个性化修改）
- 其余子项目默认从 openvela 官方仓库同步
- 通过 linkfile 提供 **`run-emulator.sh`**，解决 CMake 离树编译后官方 `emulator.sh` 无法直接启动的问题

## 环境要求

- Ubuntu 22.04（x86_64 / arm64）
- 至少 40 GB 磁盘、16 GB 内存
- 已安装：git、git-lfs、curl、cmake、python3、build-essential

```bash
sudo apt update
sudo apt install git curl cmake python3 libc++abi-dev build-essential

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

在 openvela 根目录：

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap/ --cmake -j$(nproc)
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

### 模拟器报 `No initial vela_system image`

说明 `cmake_out/` 产物未 staging 到 `nuttx/`，请使用 `./run-emulator.sh` 而非直接 `./emulator.sh`。
