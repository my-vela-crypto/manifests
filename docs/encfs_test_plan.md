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
python3 nuttx/tools/tests/encfs_crypto/test_blkcrypt.py
python3 nuttx/tools/tests/encfs_crypto/test_vfs_lifecycle.py
python3 nuttx/tools/tests/encfs_crypto/test_directory_rewind.py
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

扩展验收默认包含六次实际开机。`--reboot-count` 设置同一加密盘的冷启动次数，默认 3，范围 1–20；这些阶段均禁止重新 provision。流程如下：

| 阶段 | Guest 操作 | Host 预期 |
| --- | --- | --- |
| `standalone` | 新实例运行 `encfs_test standalone`；直接在专用 `/dev/virtblk1` 格式化普通 exFAT，再挂名称层；包括错误 path_key 的元数据认证拒绝和正常文件操作 | 完整测试内容在普通下层镜像中可找到；`secret.txt` 的 ASCII/UTF-16LE 不出现；解析 exFAT 根的 `.p`，其可见目录和文件名全部以 `E1_` 开头 |
| `corruption` | 独立新实例运行 `encfs_test corruption`；分别注入 10 类卷头/物理根错误及 6 类真实下层目录项损坏，检查拒绝、恢复、重挂和资源释放 | 此模式只反复格式化自己的新盘；保存日志和盘哈希，不把损坏夹具作为正常加密镜像验收 |
| `encrypted-write` | 另一个新实例执行 `encfs provision --erase`，再运行 `encfs_test write`；覆盖真实三层、生命周期和异常测试，最后 stop | 实际加密盘找不到完整测试内容及指定逻辑名称的 ASCII/UTF-16LE；首扇区不暴露普通 exFAT 头 |
| `encrypted-reboot` 及后续编号阶段 | 逐次保留并重启上一步同一实例、同一路径数据盘，运行 `encfs_test verify`；此阶段不 provision | 读回内容及逻辑目录一致；重启前镜像哈希与上一步停机后相同；再次检查加密镜像 |

每次 Guest 命令完成必须回到 `encfs> `。测试程序必须输出对应模式的一条 `ENCFS_TEST_RESULT mode=<模式> pass=<数量> fail=0`，且通过数大于 0。缺少总结行、超时、Guest 退出、任一失败断言或镜像检查失败都会令 runner 非零退出，不会据部分日志宣告通过。

每阶段测试后执行 `encfs stop`，要求状态 STOPPED，再对本次独立模拟器进程组发 SIGTERM，等待 EOF 和进程结束后才读镜像。需要 SIGKILL 的阶段不计作干净停机通过。检查对象是 `avd/<实例>.vvd/vela_data.bin`，不是构建目录中的初始空白盘。

## NSH 测试密钥、完整 GUI 与定时压力回归

完整 GUI 配置 `goldfish-arm64-v8a-ap-encfs-gui` 从原 `goldfish-arm64-v8a-ap` 配置扩展，保留 LVGL、Binder servicemanager、KVDB、ADB、音频、网络及原调试功能，同时启用 EncFS、NSH 测试主密钥入口、真实帧缓冲截图 `fbcapture` 和触控输入工具 `input`。GUI demo 的图像等资源编入固件；系统 ROMFS 仍按原板级规则打包，空白数据盘不预装明文文件。

从工作区根目录构建并运行完整回归：

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-gui/ --cmake -j4
python3 .repo/manifests/tools/test_encfs_gui.py \
  --source-root "$PWD" \
  --build-dir "$PWD/cmake_out/vela_goldfish-arm64-v8a-ap-encfs-gui" \
  --results-dir "$PWD/encfs-results/gui-stress-$(date +%Y%m%d-%H%M%S)" \
  --stress-seconds 3600
```

若该构建目录已用旧版 defconfig 配置，先通过相同构建入口执行 `--cmake resetconfig`，再正常编译，核对最终 `.config` 中 `FBCAPTURE`、`GRAPHICS_INPUT_TOOL`、`ENCFS_TESTING` 均为 `y`。GUI runner 另依赖 Pillow（`PIL`），例如使用 `sudo apt install python3-pexpect python3-pil` 安装宿主依赖。`--stress-seconds` 默认 3600，可设 60–86400；60 秒只适合快速验证，不能将其结果描述为一小时压力通过。结果目录须不存在或为空。

该 runner 在独立实例中使用 `-gpu swiftshader_indirect`，通过 Guest 的 `/dev/fb0` 读取真实渲染帧，通过 `/dev/utouch` 注入触控，不复用存储 runner 的 `-gpu off`。模拟器的 `/share` 是本实例的 9p 导出目录，截图写入该路径，不影响加密卷中的持久化样本。正常用户配置、IPC 和实际数据盘隔离要求与上一节一致。

七个开机阶段按下表执行；手工主密钥实例和板级密钥 GUI 实例使用不同的新数据盘，同一实例后续启动复用其原盘：

| 阶段 | 操作与预期 |
| --- | --- |
| `manual-key` | `encfs stop` 后，通过 `encfs testkey <64hex>\|board\|none` 选择测试来源；检查缺钥、非法长度/字符拒绝、READY 时换钥拒绝；只在新盘用公开夹具 A 执行一次 provision；夹具 B 或 board 拒挂后重新选 A，原内容应仍可恢复 |
| `manual-key-coldboot` | 不再 provision；冷启动后 stop 并重新输入 A，读回相同文件，再 stop；停机盘哈希应与上阶段相同，已知明文内容和逻辑文件名不出现在原始镜像 |
| `gui-music` | GUI 新盘首次用板级来源 provision，然后启动 `lvgldemo music &`；GUI 任务应存活、初始画面有效，后续动画应改变超过 1000 个像素 |
| `gui-keypad` | 同盘冷启动，不 provision；启动 `lvgldemo keypad_encoder &`，注入点击和滑动，保存前后截图，供人工核对交互画面 |
| `gui-stress-demo` | 同盘启动 `lvgldemo stress &`；这里的 stress 是 LVGL 图形动画 demo，要求有效帧和动画变化，不将其等同文件系统定时压力 |
| `gui-widgets-storage` | 启动 widgets，点击 Profile/Analytics/Shop 并检查画面变化；GUI 与原服务存活时执行 `encfs_test write` 的完整加密回归，再启动两个文件系统线程持续请求时长，并周期检查 NSH、READY、GUI、内存及触控画面 |
| `gui-persistence-coldboot` | 定时压力结束、干净 stop 后，同盘冷启动 widgets 并再次触控；`encfs_test verify` 应读回持久化样本，原始加密盘检查通过，最终盘哈希应与压力阶段停机后相同 |

公开夹具 A 为 `000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f`，B 为 `202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f`。测试入口仅在 `CONFIG_ENCFS_TESTING=y` 时存在。换来源前须 stop 且没有残留资源；非法输入返回 `-EINVAL` 并保持旧来源，运行时返回 `-EBUSY`。错误/缺失主密钥只应拒绝挂载、回滚资源；恢复正确 A 后应仍能读取样本，期间禁止重新 provision。命令参数可能被 NSH 回显、串口日志或历史保存，因此仅输入公开测试材料，不输入真实秘密。完整语义见 `encfs_interfaces.md` 的测试专用接口节。

可在自己的独立新测试实例内手工复核 GUI；首次 provision 完成后：

```text
encfs start
lvgldemo widgets &
fbcapture -d /dev/fb0 -o /share/widgets-profile.bmp
input tap 950 35
fbcapture -d /dev/fb0 -o /share/widgets-analytics.bmp
input tap 1160 35
fbcapture -d /dev/fb0 -o /share/widgets-shop.bmp
encfs_test stress 3600 &
```

本机实际 Guest 帧缓冲为 1280×800 横屏，尽管模拟器配置仍写有 720×1280；验收以真实帧缓冲尺寸为准。widgets 顶部三个 tab 中心分别约为 `(740,35)`、`(950,35)`、`(1160,35)`。不同 demo 使用独立冷启动，不在已初始化 LVGL 后直接重新运行另一个 demo。`fbcapture` 的退出码本身不足以判断保存成功；应同时要求串口出现 `Success`、导出 BMP 实际存在、尺寸为 1280×800 且非近乎均色。切换 tab 前后超过 1000 个像素变化是自动化判据，最终仍应查看保存的画面，不能仅凭任务名断言视觉正确。

当前 `CONFIG_GOLDFISH_GPU_FB_RGB565=y`，但原 `fbcapture` 工具将 16 位 RGB565 像素写入带 `BI_RGB` 标记的 BMP；Pillow 等通用 BMP 读取器可能按 RGB555 解释，造成色彩失真。runner 保留原始 BMP 作为证据，并依据实际配置，用 Pillow 的 `BGR;16` raw 解码、真实行跨度及自底向上的顺序导出 PNG。人工复核应查看 `frames/*.png`；不要将通用读取器错误解释的 BMP 颜色当作 Guest 渲染错误。配置或帧缓冲格式变化后，应重新核对像素格式和尺寸，不能沿用该解码假设。

定时压力使用 `encfs_test stress <seconds>`：两个线程持续进行变长创建/写入、偏移覆盖、fsync、字节校验、重命名、重开、逻辑目录遍历和删除，不以睡眠凑时长。每线程最多保留一个 65537 字节文件，每轮删除自己的文件，限制磁盘占用。预期数据由固定种子生成，完整读回两次；最终同时检查 Guest 单调时钟总时长、每线程时长、宿主实际经过时间、两线程正轮数、写入/验证正字节数、`errors=0 completed=1` 和 `ENCFS_TEST_RESULT mode=stress pass=1 fail=0`。

Guest 每 30 秒报告一个进度窗口；runner 约每 30 秒检查一次 READY、NSH 响应、GUI 任务和 `free` 输出。连续窗口中两个线程的轮数都须递增；宿主连续 95 秒收不到新 I/O 进度且没有最终结果视为停滞。首个检查点、此后每 10 个检查点和结束时，重新点击 Analytics/Shop、截图并验证画面变化。内存数值进入报告，用于比较趋势；不能仅凭单次前后差值宣称没有泄漏。

这组回归不预设通过状态。一小时请求只在实际运行达到时长、全部阶段断言通过且干净卸载后成立；短跑、部分完成或仅图形 demo 运行一小时均不得代替。断电、物理硬件故障恢复和无限期耐久性不属于此步骤证明的范围。

## Guest 用例覆盖

`encfs_test crypto` 只运行密码与组件名检查。`standalone` 包含算法检查及独立名称层文件测试，会格式化它所在实例的原始数据盘，不能在持久化样本上执行。`corruption` 会在专用新盘反复建盘，禁止在持久化样本或日常数据盘执行。`write`/`all` 需要已 provision 的完整卷；`verify` 只读取已有样本。

| 组别 | 通过条件 |
| --- | --- |
| 算法/名称 | RFC 向量一致；名称往返及确定性正确；错误密钥、篡改、非法 Base32 被拒绝；128 字节名称成功、129 字节失败 |
| 基本文件操作 | mkdir、open、write、fsync、fstat、随机定位、dup、读回、stat/statfs、truncate 正确 |
| 目录/变更 | readdir 恢复逻辑名，rewind 正确；目录整体 rename 后子项可访问；跨目录移动、unlink/rmdir 正确；已有目标 rename 返回 `EEXIST` |
| 回归 | 最长逻辑名可创建/删除；缺失父目录失败；多轮创建、写入、校验和删除无异常 |
| 扩展文件回归 | 区分大小写名称、规范路径与编码膨胀边界正确；普通名称层及完整加密层各完成 1000 轮变长、多扇区、偏移覆盖、fsync、完整读回、重开、rename 和删除；JSON 记录实际次数、字节数、耗时 |
| 属性与 ioctl | chmod/fchmod、chown/fchown、utimensat/futimens 符合当前 FatFs 能力；FIOC_FILEPATH 只返回逻辑路径，其他 ioctl 拒绝；不把接受权限位等同权限控制 |
| 损坏卷与目录项 | 10 类卷头/根目录损坏均拒挂，6 类坏物理名称经真实 readdir 拒绝；rewind 后再次检测同一损坏，修复后可重新挂载 |
| 外部操作恢复 | 外部卸载 upper 后不虚报 READY，stop 可清理并允许重新 start；同路径替代挂载中的样本不被管理器卸载 |
| 卷头认证 | 重复 initialize 不覆盖已有卷；普通 FatFs 上错误 path_key 挂名称层失败，下层仍可用；正确 key 可继续挂载 |
| 忙时卸载 | 打开文件或目录时 stop 返回 `-EBUSY`，仍为 READY；关闭后可停止；运行时换测试主钥返回 `-EBUSY` |
| 异常启动 | 缺失或错误 master 不留下可用加密挂载/块节点；register/key/lower/upper 故障逐一回滚为无残留资源，再 start 可成功 |
| 重启 | 同次运行 100 次 stop/start 内容一致；默认三次独立冷启动之间不重新 provision，逻辑路径和内容仍可恢复 |

更多低层 I/O 清理失败由宿主生命周期测试验证。当前四个 Guest failpoint 都在对应操作前触发，不能据此声称已测试任意物理设备错误或掉电场景。

## 结果与失败定位

每次结果目录保留：

```text
report.json
standalone.serial.log
encrypted-write.serial.log
encrypted-reboot.serial.log
encrypted-reboot-02.serial.log
encrypted-reboot-03.serial.log
corruption.serial.log
avd/<独立实例>.vvd/vela_data.bin
```

GUI/定时回归的独立结果目录还保存 `manual-key.serial.log`、`manual-key-coldboot.serial.log`、`gui-music.serial.log`、`gui-keypad.serial.log`、`gui-stress-demo.serial.log`、`gui-widgets-storage.serial.log`、`gui-persistence-coldboot.serial.log`，以及 `frames/*.png` 和实例 `share/*.bmp`。GUI `report.json` 记录各帧路径、SHA-256、尺寸、像素差、内存检查点、Guest 进度/最终统计、宿主经过时长、所有命令、源代码与构建标识、实际数据盘路径及停机哈希。应保留原始失败尝试，再用全新结果目录复测，不覆盖失败证据。

任一 CLI 状态不符、错误主密钥意外挂载、数据不符、GUI 任务退出、空白/错误尺寸截图、tab/动画无明显变化、工作线程停滞、时长不足、缺失或重复总结行、命令/启动超时、无法干净 stop，均应非零退出并记录失败；已完成的局部阶段不能使整体 `status` 变为 `passed`。停止模拟器与镜像取证同样要求先 STOPPED、等待实例进程结束，不能对运行中的镜像作一致性结论。

失败发生得早时，后续阶段日志可能尚未创建。`report.json` 记录固件与配置哈希、各模块 Git revision 和未提交修改、实际启动参数、Guest 命令及总结、每个阶段状态、镜像绝对路径、大小及 SHA-256、目录解析结果、原始失败及异常追踪。整体 `status` 必须为 `passed`，并全部要求的阶段通过，才可填写本轮完整验收通过。需要强制结束进程等异常时，报告会标记；失败后取得的哈希只作为保留样本记录，不能替代成功断言。

若 start 失败，先运行 `encfs status` 查看原始阶段、错误与回滚错误。第一次空白盘应显式 provision；错误密钥、损坏元数据或已有卷不能通过自动格式化“修复”。若 stop 忙，关闭文件/目录后重试；有残留资源时保留日志并重试 stop，不直接换钥或注销下层设备。

镜像未检出指定完整字符串，仅证明本次已知样本的可见性检查通过。结合独立名称层目录解析、Guest 读写、忙时卸载和同盘重启，才形成该版本的验收证据；它不证明任意内容认证、断电原子性、中文名称支持或生产安全。
