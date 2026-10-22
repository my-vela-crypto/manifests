# EncFS 编译与验收步骤

本计划区分宿主检查、真实 Guest 文件系统检查和停机后的实际镜像检查。宿主替身验证不会计作模拟器通过，字符串扫描也不能单独证明文件系统功能正确。本文不预填 Guest PASS；以本次运行的 `report.json`、串口日志及镜像哈希为验收证据。

## 准备与构建

从 openvela 工作区根目录执行：

```bash
repo sync -c -j8
python3 .repo/manifests/tools/apply_encfs.py
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs/ --cmake -j$(nproc)
```

首次 Python 应用成功后才创建根目录 `apply-encfs.sh`，之后可使用该快捷入口幂等重试。脚本从 manifest 安装 `patches/nuttx/encfs.patch`、`patches/vendor_openvela/encfs.patch` 及 `overlays/apps/`，因此不要求本次内核修改已经推送远端。补丁基线和校验值见 `encfs-sources.json`。

应用前完成两份补丁与全部覆盖文件的校验/冲突预检。遇到不兼容基线或冲突时应先处理冲突，不能覆盖其他成员修改。源码树是 repo 管理的多个 Git 仓库，不能按一个根 Git 仓库提交全部改动。

构建目录应包含 `.config`、`nuttx`、`nuttx.bin`、`vela_system.bin` 和 `vela_data.bin`。专用配置启用 blkcrypt、encpathfs、管理器、NSH 命令和测试 API；`NAME_MAX=255`、`PATH_MAX=1024`、板级主密钥长度 32，提示符为 `encfs> `。加密卷不启用路径缓存。构建生成的数据盘是新的 256 MiB 空白盘；第一次开机普通 start 失败是预期的，仍应进入 NSH，不能因此退回明文 `/data`。

Runner 要求 Python 3.9+ 和 `pexpect`；全部宿主脚本建议统一使用 Ubuntu 22.04 的 Python 3.10+。可用 `sudo apt install python3-pexpect` 安装 runner 依赖。

## 宿主检查

```bash
python3 nuttx/tools/tests/encfs_crypto/test_crypto.py
python3 nuttx/tools/tests/encpathfs/test_encpathfs.py
nuttx/tools/tests/encfs_manager/run_host_tests.sh
```

| 检查 | 使用的真实源码 | 检查范围与限制 |
| --- | --- | --- |
| 密码算法 | AES-SIV、HKDF、AES/CMAC/HMAC/SHA 等实现 | RFC 5297、RFC 5869、边界、认证失败、可用时与 OpenSSL 差分；不测试 VFS 挂载 |
| 名称和路径 | `encpathfs_codec.c`、`encpathfs_path.c` 及密码实现 | 1–128 字节名称、规范 Base32hex、篡改、错误密钥、缓冲区、点路径、根逃逸和编码膨胀；inode/stat 边界由替身提供 |
| 管理器 | `encfs_manager.c`、`encfs_key.c`、`encfs_block_port.c` | 获取/释放顺序、无钥匙、四阶段一次性故障、忙时卸载、清理失败保留资源、预留 upper、元数据关闭恢复、provision 与 start 分离；board、HKDF 和 VFS 等外部依赖由可观测替身提供 |

具体编译器和 sanitizer 参数见各测试目录 README。管理器脚本默认开启 ASan+UBSan；容器 ptrace 环境默认关闭 LeakSanitizer。宿主测试的断言数量可能随用例增长，执行输出和退出码才是本次结果。

## 自动化真实模拟器验收

```bash
python3 .repo/manifests/tools/test_encfs.py \
  --source-root "$PWD" \
  --build-dir "$PWD/cmake_out/vela_goldfish-arm64-v8a-ap-encfs" \
  --results-dir "$PWD/encfs-results/acceptance-$(date +%Y%m%d-%H%M%S)"
```

也可以不传参数，脚本从自身所在目录向上定位源码根，兼容其位于 `.repo/manifests/tools/` 的布局；默认结果目录为 `encfs-results/<时间戳和随机后缀>/`。显式指定的结果目录必须不存在或为空。启动及命令较慢时可调整 `--boot-timeout`、`--command-timeout`，单位均为秒。

脚本先检查固件、镜像、配置和 SHA-256，然后使用：

```text
./run-emulator.sh goldfish-arm64-v8a-ap-encfs -keep <唯一实例> \
  -no-window -no-audio -gpu off -accel off
```

每次验收新建一个普通名称层实例和一个完整加密实例。`ANDROID_AVD_HOME`、`CUSTOM_AVD_SPACE` 都指向本次结果目录的 `avd/`。当前预编译模拟器还需要 `$HOME/.vela/<实例>.ini` 注册信息，板级启动脚本仅为本次唯一名称写入注册文件；数据盘本身仍位于结果目录。运行环境必须允许模拟器访问正常用户配置和 IPC。不会删除其他实例，也不会拿日常使用的数据盘做破坏性测试。

三阶段流程：

| 阶段 | Guest 操作 | Host 预期 |
| --- | --- | --- |
| `standalone` | 新实例运行 `encfs_test standalone`；直接在专用 `/dev/virtblk1` 格式化普通 exFAT，再挂名称层；包括错误 path_key 的元数据认证拒绝和正常文件操作 | 完整测试内容在普通下层镜像中可找到；`secret.txt` 的 ASCII/UTF-16LE 不出现；解析 exFAT 根的 `.p`，其可见目录和文件名全部以 `E1_` 开头 |
| `encrypted-write` | 另一个新实例执行 `encfs provision --erase`，再运行 `encfs_test write`；覆盖真实三层、生命周期和异常测试，最后 stop | 实际加密盘找不到完整测试内容及指定逻辑名称的 ASCII/UTF-16LE；首扇区不暴露普通 exFAT 头 |
| `encrypted-reboot` | 保留并重启上一步同一实例、同一路径数据盘，运行 `encfs_test verify`；此阶段不 provision | 读回内容及逻辑目录一致；重启前镜像哈希与上一步停机后相同；再次检查加密镜像 |

每次 Guest 命令完成必须回到 `encfs> `。测试程序必须输出对应模式的一条 `ENCFS_TEST_RESULT mode=<模式> pass=<数量> fail=0`，且通过数大于 0。缺少总结行、超时、Guest 退出、任一失败断言或镜像检查失败都会令 runner 非零退出，不会据部分日志宣告通过。

每阶段测试后执行 `encfs stop`，要求状态 STOPPED，再对本次独立模拟器进程组发 SIGTERM，等待 EOF 和进程结束后才读镜像。需要 SIGKILL 的阶段不计作干净停机通过。检查对象是 `avd/<实例>.vvd/vela_data.bin`，不是构建目录中的初始空白盘。

## Guest 用例覆盖

`encfs_test crypto` 只运行密码与组件名检查。`standalone` 包含算法检查及独立名称层文件测试，会格式化它所在实例的原始数据盘，不能在持久化样本上执行。`write`/`all` 需要已 provision 的完整卷；`verify` 只读取已有样本。

| 组别 | 通过条件 |
| --- | --- |
| 算法/名称 | RFC 向量一致；名称往返及确定性正确；错误密钥、篡改、非法 Base32 被拒绝；128 字节名称成功、129 字节失败 |
| 基本文件操作 | mkdir、open、write、fsync、fstat、随机定位、dup、读回、stat/statfs、truncate 正确 |
| 目录/变更 | readdir 恢复逻辑名，rewind 正确；目录整体 rename 后子项可访问；跨目录移动、unlink/rmdir 正确；已有目标 rename 返回 `EEXIST` |
| 回归 | 最长逻辑名可创建/删除；缺失父目录失败；多轮创建、写入、校验和删除无异常 |
| 卷头认证 | 重复 initialize 不覆盖已有卷；普通 FatFs 上错误 path_key 挂名称层失败，下层仍可用；正确 key 可继续挂载 |
| 忙时卸载 | 打开文件或目录时 stop 返回 `-EBUSY`，仍为 READY；关闭后可停止；运行时换测试主钥返回 `-EBUSY` |
| 异常启动 | 缺失或错误 master 不留下可用加密挂载/块节点；register/key/lower/upper 故障逐一回滚为无残留资源，再 start 可成功 |
| 重启 | 同次运行多轮 stop/start 内容一致；两次真实开机之间不重新 provision，逻辑路径和内容仍可恢复 |

更多低层 I/O 清理失败由宿主生命周期测试验证。当前四个 Guest failpoint 都在对应操作前触发，不能据此声称已测试任意物理设备错误或掉电场景。

## 结果与失败定位

每次结果目录保留：

```text
report.json
standalone.serial.log
encrypted-write.serial.log
encrypted-reboot.serial.log
avd/<独立实例>.vvd/vela_data.bin
```

失败发生得早时，后续阶段日志可能尚未创建。`report.json` 记录固件与配置哈希、各模块 Git revision 和未提交修改、实际启动参数、Guest 命令及总结、每个阶段状态、镜像绝对路径、大小及 SHA-256、目录解析结果、原始失败及异常追踪。整体 `status` 必须为 `passed`，并有三个通过阶段，才可填写本轮完整验收通过。需要强制结束进程等异常时，报告会标记；失败后取得的哈希只作为保留样本记录，不能替代成功断言。

若 start 失败，先运行 `encfs status` 查看原始阶段、错误与回滚错误。第一次空白盘应显式 provision；错误密钥、损坏元数据或已有卷不能通过自动格式化“修复”。若 stop 忙，关闭文件/目录后重试；有残留资源时保留日志并重试 stop，不直接换钥或注销下层设备。

镜像未检出指定完整字符串，仅证明本次已知样本的可见性检查通过。结合独立名称层目录解析、Guest 读写、忙时卸载和同盘重启，才形成该版本的验收证据；它不证明任意内容认证、断电原子性、中文名称支持或生产安全。
