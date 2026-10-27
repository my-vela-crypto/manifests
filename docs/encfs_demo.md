# 模拟器启动与测试流程

在另一台 Ubuntu 电脑上，打开自己的 openvela 项目根目录并在此打开两个终端。以下命令均从项目根目录执行，不依赖用户名、桌面位置或原电脑的安装路径。

演示入口会自动定位项目、固件、数据盘和测试密钥，保存本轮实例名。首次启动和冷启动使用同一条命令，终端 B 自动读取终端 A 创建的实例配置。

## 1 准备运行环境和固件

新电脑安装基础运行及测试依赖：

```bash
sudo apt update
sudo apt install python3 python3-pexpect python3-pil file libc++abi-dev adb build-essential git git-lfs curl
```

将 `跨电脑演示工具包.zip` 解压并合并到另一台电脑的完整 openvela 项目根目录，即可使用随包提供的 GUI 固件，直接进入第 2 步。GUI 固件目录为 `cmake_out/vela_goldfish-arm64-v8a-ap-encfs-gui/`，内含 `nuttx`、`nuttx.bin`、`vela_system.bin`、`vela_data.bin`。

如果新电脑只有同步后的源码，先应用项目补丁，再生成本机固件：

```bash
python3 .repo/manifests/tools/apply_encfs.py
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-gui/ --cmake -j4
```

从原电脑复制二进制运行时保留上述四个文件即可；重新编译时使用新电脑生成的构建目录。可在启动前查看自动识别的路径：

```bash
python3 .repo/manifests/tools/encfs_demo.py start --dry-run
```

## 2 启动模拟器

宿主终端 A 执行：

```bash
python3 .repo/manifests/tools/encfs_demo.py start
```

模拟器窗口打开，终端出现 `encfs>` 后进入下一步。后续 NSH 命令直接输入终端 A，不需要输入提示符本身。

默认演示目录为项目内的 `encfs-results/course-demo/`。它只记录实例名，不保存原电脑的项目绝对路径。

如需使用放在其他目录的现成固件，可指定相对项目根目录的路径：

```bash
python3 .repo/manifests/tools/encfs_demo.py start --build-dir firmware/gui
```

如需重新做一轮全新演示，给 `start` 和 `keys` 同时添加 `--session encfs-results/course-demo-02`。冷启动继续使用原来的演示目录。

## 3 准备测试密钥并初始化新盘

保持模拟器运行，宿主终端 B 执行：

```bash
python3 .repo/manifests/tools/encfs_demo.py keys
```

入口自动找到当前实例的共享目录，生成四把测试主密钥和一次建卷随机材料。重复执行会保留已有的完整测试材料。

回到终端 A，在 `encfs>` 下逐行输入：

```text
encfs stop
encfs keyfiles /share/keys
encfs provision --erase
encfs start
encfs status
```

`provision --erase` 会格式化本实例的数据盘，仅首次建卷执行。后续重启直接挂载原盘。

正常状态包含：

```text
state=READY result=0
wrapped=1 master_id=0 generation=1
```

## 4 测试中文文件读写

在 NSH 输入：

```text
mkdir /data/验证
echo ENCFS_DEMO_OK > /data/验证/密钥.txt
ls /data/验证
cat /data/验证/密钥.txt
```

目录显示中文文件名 `密钥.txt`，读取输出 `ENCFS_DEMO_OK`。

展示要点：应用使用普通文件命令和中文逻辑路径，名称与内容加密由底层透明完成。

## 5 展示 GUI 交互

在 NSH 输入：

```text
lvgldemo widgets &
```

界面出现后，可直接使用鼠标，也可逐条执行下面的触控命令，观察每次画面变化：

```text
input tap 950 35
input tap 1160 35
input tap 683 357
input tap 740 35
input swipe 685 402 1050 402 400
```

对应操作依次为 Analytics 页签、Shop 页签、复选框、Profile 页签和滑块。

## 6 测试在线主密钥轮换

保持 GUI 运行，在 NSH 输入：

```text
encfs_test rekey 1
encfs status
cat /data/验证/密钥.txt
```

程序保持文件描述符、复制描述符和目录句柄打开，换钥后继续读取、追加与目录遍历。输出包含：

```text
ENCFS_REKEY_RESULT pass=27 fail=0 next_master_id=1
ENCFS_TEST_RESULT mode=rekey pass=28 fail=0
```

随后状态为 `wrapped=1 master_id=1 generation=2`，原中文文件仍输出 `ENCFS_DEMO_OK`。

展示要点：主密钥已经更换，文件系统持续挂载，原有句柄和文件数据继续可用。

## 7 运行现场双线程压力测试

在 NSH 输入：

```text
encfs_test stress 60 &
```

压力任务在后台执行，可继续查看状态和操作 GUI：

```text
encfs status
ps
free
```

约每 30 秒打印一次双线程进度。最终 `ENCFS_LONG_STRESS_RESULT` 汇总应包含 `errors=0 completed=1`，随后打印：

```text
ENCFS_TEST_RESULT mode=stress pass=1 fail=0
```

## 8 冷启动后读回原盘

等压力测试结束，先在 NSH 输入：

```text
encfs stop
```

确认 `state=STOPPED result=0` 后，在宿主终端 B 执行停止命令，保留本轮数据盘：

```bash
python3 .repo/manifests/tools/encfs_demo.py stop
```

终端 A 返回宿主命令行后，执行同一条启动命令：

```bash
python3 .repo/manifests/tools/encfs_demo.py start
```

出现 `encfs>` 后输入：

```text
encfs stop
encfs keyfiles /share/keys
encfs start
encfs status
cat /data/验证/密钥.txt
```

状态保持 `master_id=1 generation=2`，文件输出仍为 `ENCFS_DEMO_OK`。

展示要点：新进程使用轮换后的主密钥，成功挂载同一数据盘并读回中文路径文件。

## 9 展示已经完成的 24 小时测试

宿主终端 B 执行：

```bash
python3 .repo/manifests/tools/encfs_demo.py results
```

入口读取随源码提供的相对路径结果摘要，显示：**24 小时、707,001 次事务、0 错误、578 张运行画面**。无需访问原电脑上的日志位置。

配套图片和曲线位于本流程旁的 `course-demo/` 文件夹：

- [GUI 运行画面](course-demo/GUI运行画面.png)
- [双线程进度曲线](course-demo/双线程进度.svg)
- [系统堆曲线](course-demo/系统堆曲线.svg)
- [24 小时结果摘要](course-demo/24h-summary.json)

## 10 手工输入测试主密钥

该项使用独立演示目录。先按第 8 步停止并退出当前模拟器，再在宿主终端 A 执行：

```bash
python3 .repo/manifests/tools/encfs_demo.py start --session encfs-results/course-testkey
```

首次新盘在 NSH 输入下面这把公开测试密钥：

```text
encfs stop
encfs testkey 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
encfs provision --erase
encfs start
echo TEST_KEY_OK > /data/key-demo.txt
cat /data/key-demo.txt
encfs stop
```

随后演示缺钥拒绝与恢复：

```text
encfs testkey none
encfs start
encfs testkey 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
encfs start
cat /data/key-demo.txt
```

缺钥时启动被拒绝；重新输入原密钥后，文件读回 `TEST_KEY_OK`。在线换钥使用第 3–8 步的包装卷实例。退出手工密钥实例时，先在 NSH 执行 `encfs stop`，再在宿主终端 B 执行：

```bash
python3 .repo/manifests/tools/encfs_demo.py stop --session encfs-results/course-testkey
```

## 11 自动回归测试

退出手工模拟器后，在项目根目录执行所需测试。每个命令会创建独立测试实例，并保存串口日志和报告。新电脑准备另外两套回归固件：

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs/ --cmake -j4
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-extended/ --cmake -j4
```

每次回归先生成结果目录标签：

```bash
ENCFS_RUN_TAG="$(date +%Y%m%d-%H%M%S)"
```

**专用存储回归与三次冷启动：**

```bash
python3 .repo/manifests/tools/test_encfs.py \
  --results-dir "encfs-results/course-storage-$ENCFS_RUN_TAG"
```

**完整 GUI 回归与 60 秒压力：**

```bash
python3 .repo/manifests/tools/test_encfs_gui.py \
  --results-dir "encfs-results/course-gui-$ENCFS_RUN_TAG" \
  --stress-seconds 60
```

**在线换钥专项与 180 秒并发压力：**

```bash
python3 .repo/manifests/tools/test_encfs_keywrap.py \
  --results-dir "encfs-results/course-keywrap-$ENCFS_RUN_TAG" \
  --stress-seconds 180
```

**通用文件系统回归与 20 次模拟器中断恢复：**

```bash
python3 .repo/manifests/tools/test_encfs_extended.py \
  --results-dir "encfs-results/course-extended-$ENCFS_RUN_TAG" \
  --powercuts 20
```

脚本根据自身位置定位项目，默认选择对应配置的 `cmake_out/vela_<配置名>/` 固件。使用自定义固件目录时追加 `--build-dir` 即可。

各结果目录中的 `report.json` 保存总结果，`*.serial.log` 保存命令输出，GUI 测试的 `frames/` 保存运行画面。

现场汇报顺序：**中文文件读写 → GUI 交互 → 在线换钥 → 双线程压力 → 同盘冷启动 → 24 小时统计**。
