# my-vela-crypto manifests

本仓库是 [openvela](https://github.com/open-vela) 的定制 manifest，在官方源码基础上：

- **nuttx** 指向 `my-vela-crypto/nuttx`（内核个性化修改）
- 其余子项目默认从 openvela 官方仓库同步
- 通过 linkfile 提供 **`run-emulator.sh`**，解决 CMake 离树编译后官方 `emulator.sh` 无法直接启动的问题

## 环境要求

课程扩展当前进度见[扩展状态](docs/encfs_extension_status.md)：UTF-8文件名、在线包装密钥轮换、789项存储回归和通用文件系统/模拟中断验证已集成；两轮24小时任务仍在运行，不能当作已通过。复现在线换钥见[说明](docs/encfs_keywrap.md)，通用回归见[验收说明](docs/encfs_extended_validation.md)。原10月24日首版报告继续保留历史范围。

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

团队[nuttx/dev](https://github.com/my-vela-crypto/nuttx/tree/dev)保存两层实现与验收修复。当前锁定版本见`encfs-tested.xml`。本manifest同时保存完整内核补丁、`patches/vendor_openvela/encfs.patch`板级补丁及`overlays/apps/`的25个受控文件，从记录的官方基线重现。应用器识别团队内核已有的相同修改并跳过，仍需安装板级与应用改动。基线和全部文件校验值由`encfs-sources.json`记录；首版报告见`docs/encfs_test_report.md`，最新扩展见`docs/encfs_extension_status.md`。

首次同步后，根目录的快捷链接可能尚不存在，**先执行 Python 入口**：

课程验收推荐在新的工作区选择 `encfs-tested.xml`：它固定了本次编译的 232 个项目版本，apps 和 vendor/openvela 使用已验证补丁的官方基线，再由应用器安装本项目改动。日常开发的默认 manifest 仍跟随 dev。新工作区的完整入口为：

```bash
repo init -u https://github.com/my-vela-crypto/manifests.git -b dev -m encfs-tested.xml
repo sync -c -j8
python3 .repo/manifests/tools/apply_encfs.py
```

已有工作区按当前 manifest 同步并应用：

```bash
repo sync -c -j8
python3 .repo/manifests/tools/apply_encfs.py
```

脚本先完成两个仓库补丁及全部 apps 覆盖文件的校验和/冲突预检，再应用变化；只有成功后才创建根目录 `apply-encfs.sh`。以后可用该快捷入口重复应用，已安装的相同内容不会再次改写：

```bash
./apply-encfs.sh
```

脚本遇到基线或本地修改冲突时，先处理冲突，不要强行覆盖成员改动。升级同步后也应重新应用并检查结果。后续若改用已包含这些变化的团队仓库，应同时调整补丁与覆盖文件，避免重复维护。

2026-10-24首版历史验收为**665/0（六次开机）**，完整GUI与实际一小时压力通过，见`docs/encfs_test_report.md`。10月25日扩展已有**789/0**、完整GUI正式复验、180秒在线换钥、46项通用文件系统及20次模拟器切断验证；两轮24小时仍待完成，见[扩展报告](docs/encfs_extension_report.md)。视频不制作；本地编译和测试不需要GitHub写权限。

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

### NSH 手工输入测试主密钥

仅 `CONFIG_ENCFS_TESTING=y` 的测试配置提供 `encfs testkey <64hex>|board|none`：恰好 64 个十六进制字符选择 32 字节测试 master，`board` 恢复板级来源，`none` 模拟缺钥。先 stop，确认 STOPPED 且无残留资源；运行时换来源返回 `-EBUSY`，非法输入返回 `-EINVAL` 并保持旧来源。

例如选择公开测试夹具 A：

```text
encfs stop
encfs testkey 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
```

新空白专用盘随后按上一节首次 provision；已用 A 初始化的盘直接 `encfs start`，不要再次 provision。错误或缺失密钥应拒绝挂载，恢复原来源后应仍能读回已有数据。测试来源只在 RAM 中保留，冷启动后须重新输入 A。参数可能被终端回显、串口日志或历史保存，仅使用公开测试材料，不输入真实秘密。

### 完整 GUI 配置与手工检查

独立配置保留原 GUI、音频、网络和服务，加入加密及真实截图/触控工具：

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-gui/ --cmake -j4
ENCFS_GUI_DIR="$PWD/encfs-gui-avd"
ENCFS_GUI_INSTANCE="encfs-gui-demo-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ENCFS_GUI_DIR"
ANDROID_AVD_HOME="$ENCFS_GUI_DIR" CUSTOM_AVD_SPACE="$ENCFS_GUI_DIR" \
  ./run-emulator.sh goldfish-arm64-v8a-ap-encfs-gui \
  -keep "$ENCFS_GUI_INSTANCE" -no-window -no-audio \
  -gpu swiftshader_indirect -accel off
```

首次新盘才执行 `encfs provision --erase`。之后在 NSH 中：

```text
encfs start
lvgldemo widgets &
fbcapture -d /dev/fb0 -o /share/profile.bmp
input tap 950 35
fbcapture -d /dev/fb0 -o /share/analytics.bmp
input tap 1160 35
fbcapture -d /dev/fb0 -o /share/shop.bmp
```

本机真实帧缓冲为 1280×800 横屏；Profile/Analytics/Shop 点击点约为 `(740,35)`、`(950,35)`、`(1160,35)`。原始 BMP 导出到 `$ENCFS_GUI_DIR/$ENCFS_GUI_INSTANCE.vvd/share/`。本配置使用 RGB565，原截图 BMP 头存在 RGB555 解释歧义，自动 runner 会保存原 BMP 并按实际格式导出 PNG；画面复核使用其 `frames/*.png`。关闭前先 `encfs stop`，同盘重启不再次 provision。

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

完整 GUI 与定时双线程压力 runner 另需 Pillow：

```bash
sudo apt install python3-pexpect python3-pil
python3 .repo/manifests/tools/test_encfs_gui.py \
  --source-root "$PWD" \
  --build-dir "$PWD/cmake_out/vela_goldfish-arm64-v8a-ap-encfs-gui" \
  --results-dir "$PWD/encfs-results/gui-stress-$(date +%Y%m%d-%H%M%S)" \
  --stress-seconds 3600
```

该流程验证手工测试钥、四个LVGL demo、触控与真实截图、GUI并行存储回归、定时双线程读写和同盘冷启动。每30秒检查进度、NSH、挂载和GUI，保存统计、内存、画面与镜像哈希。只有实际测满请求时长且整体报告为`passed`才可记通过；首版665和扩展789分别有其正式证据，不把运行中的24小时计入已通过。更改已有defconfig后，先用同一构建入口执行`--cmake resetconfig`再编译。

本文不预填模拟器 PASS；请以实际运行报告为准。详细接口、卷格式和测试判据见 [接口说明](docs/encfs_interfaces.md) 与 [验收计划](docs/encfs_test_plan.md)。

### 当前边界

- QEMU 的 `board_uniquekey()` 返回固定伪密钥；本配置还开启 `CONFIG_ENCFS_TESTING`。它用于课程演示，不是生产密钥保护方案。
- 默认legacy模式以32字节主密钥经HKDF-SHA256派生块/路径子密钥。新wrapped模式使用独立随机数据密钥，由卷外provider包装，支持在线更换包装主密钥；旧直派生卷拒绝伪换钥迁移。模拟器文件provider不是TEE。
- 逻辑名支持1–128字节严格UTF-8（中文/emoji），不归一化或折叠大小写；128字节编码后为234字节，旧ASCII格式保持兼容。软/硬链接和mmap不在支持范围内。
- 下层实际格式是 FatFs 管理的 **exFAT**，挂载类型仍为 `fatfs`。已有目标 rename 返回 `EEXIST`，不承诺替换语义或断电原子性。
- XTS 不提供文件内容或任意扇区认证；SIV 认证文件名及卷元数据。未检出已知字符串不是完整安全证明，空白未写区域也不应声称已加密或已安全擦除。
