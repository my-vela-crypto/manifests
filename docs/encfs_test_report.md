# 第二、三层实现与测试报告

<!-- encfs-final-current:begin -->
最新正式验收：**2026-10-24（Asia/Shanghai）**。NSH 手工测试密钥入口、完整 GUI 配置回归和实际一小时双线程压力已完成。视频按用户要求暂不制作。下方保留 10 月 22 日及 10 月 23 日上午验收/发布历史；这些历史缺项不代表本轮状态。

远端归档见[本轮验收证据](https://github.com/my-vela-crypto/manifests/tree/e6db47d77e1622aaf3cf8fc79cdce4ba573f4575/validation/encfs-final-20261024)。本地成品包另含已测固件、五张实际运行数据盘、全部原始截图及校验清单。

## 2026-10-24 NSH、完整 GUI 与一小时压力验收（当前）

| 正式证据 | 实际结果 |
| --- | --- |
| `encfs-results/acceptance-04/report.json` | 六次开机，**665 通过、0 失败**：116/361/176/4/4/4；两组各 1000 轮文件操作、100 次启停、同盘三次冷启动 |
| `encfs-results/gui-long-01/report.json` | 七次开机，手工测试钥及冷启动、四个已启用 LVGL demo 渲染、GUI 并行存储回归、实际一小时双线程压力与同盘冷启动全部完成 |
| `encfs-results/gui-keypad-controls-02/report.json` | 额外一次开机，使用相同 GUI 固件独立补验 Keypad 弹窗关闭、复选框、滑块和文本页签，四项均通过 |
| `encfs-results/gui-long-01/analysis/analysis.json` | 独立复核原始串口计数、各工作线程时长、每窗口双线程进度、帧哈希和压力期间所选页签；生成累计工作量及全局堆曲线 |
| 隔离交付与 CLI | **195/195** 交付检查，15 个 apps 文件；测试配置 CLI **405/0**，生产配置 CLI **5/0** |

正式 GUI 运行区间为 `2026-10-23T23:29:39+08:00` 至 `2026-10-24T00:31:58+08:00`。四个 demo 是 `music`、`keypad_encoder`、`stress`、`widgets`。Music 已核对播放界面与动画；本轮使用无音频宿主参数，不将画面状态视作声音输出或完整音频链路验收。Widgets 的 Profile/Analytics/Shop 页签、复选框与滑块有实际像素变化证据，存储写入期间 GUI 与 NSH 保持可用。

GUI 同时运行原服务时，`encfs_test write` **176/0**，1000 轮多扇区文件回归、100 次 stop/start 完成。四种 demo 的渲染不等于所有控件均已穷举：Keypad 主流程最早的两张截图相同，只证明渲染，**不能用它们证明触控成功**；真实触控以 `gui-keypad-controls-02` 为准，关闭弹窗/复选框/滑块/文本页签分别变化 **1,012,668/225/3,170/283,880** 像素（复选框、滑块采用控件局部，另两项采用全帧）。

### 实际一小时压力与内存观测

两个工作线程连续执行创建、变长分块写入、跨扇区偏移覆盖、fsync、完整字节校验、rename、重开校验、逻辑目录遍历和删除。每线程最多一个 65537 字节临时文件，固定种子 `0x6e637273` 可独立复算预期内容。

| 统计 | 实测 |
| --- | --- |
| 请求时长 | 3,600 秒 |
| Guest 总时长 / 宿主经过时间 | 3,600,477 ms / 3,602.993 秒 |
| worker 0 / worker 1 各自运行时长 | 3,600,017 / 3,600,217 ms |
| 总完成轮数（worker 0 / worker 1） | **31,336**（15,651 / 15,685） |
| 逻辑写入 / 完整读回校验 | **300,596,674 / 597,903,932 字节** |
| 错误 / 完成标记 | **0 / completed=1** |
| 原始进度样本 / 宿主存活检查点 | 120 / 120 |
| 独立核验 PNG / 压力期间所选页签头 | 47 / 26；所选页签头与基线逐像素相同 |

上述字节仅统计测试逻辑载荷，**不是物理设备 I/O 量或吞吐率**，不包括元数据、对齐和其他任务流量。原始进度每约 30 秒记录，两线程在每个窗口持续推进；全部进度和最终结果从原始串口再次解析、与 JSON 对照。

堆统计来自全局分配器，包含 GUI 及其他任务。排除前 300 秒预热及末尾释放工作缓冲后的清理样本，稳态 110 个样本的已用堆范围为 **5,651,184–5,659,472 字节**；首末差 **+4,192 字节**，OLS 趋势 **+173.054 字节/小时**（R²=0.000809）。这是本次有界运行的观测，不能据此宣称零泄漏，也不能将全局变化全部归因于 EncFS。曲线及数值见 analysis 目录。

### 密钥、同盘恢复及可核对的画面

NSH 已提供 `encfs testkey <64hex>|board|none`，仅在 `CONFIG_ENCFS_TESTING=y` 时存在。换来源须先 stop 并释放资源；READY 时拒绝换钥，非法参数不替换旧来源。公开夹具 A/B、缺钥、错误钥拒挂、恢复 A 后读回与同盘冷启动均已验证，错误钥路径从不格式化。输入参数可能留在终端回显和历史，只用于公开测试材料。

手工 A 密钥卷的正常读回冷启动前后 SHA-256 相同：

```text
fdbf338010a728177332b7aa3c7727ba98157f26b7534cdfdeadf0fae9e79993
```

GUI 一小时压力完成并干净停止后，与随后只读冷启动同一路径数据盘的 SHA-256 相同：

```text
85fc4a1a641fac415c33a8fef67d4c97e659cdd131bd9e963d70941f3f17a528
```

实际帧缓冲为 1280×800 横屏；原 BMP 与按真实 RGB565 解码的 PNG 均保留，不能按模拟器配置中的 720×1280 推断画面尺寸。`fbcapture` 原 BMP 的 BI_RGB 头可能被通用读取器解释为 RGB555；复核使用保存的 PNG。正式报告记录 47 张 PNG，另有 Keypad 补验 5 张，均核对哈希。压力期间 Analytics/Shop 所选页签头以 `(680,0,1280,70)` 区域与各自基线精确相等为准。

### 源码、发布与历史尝试

| 项目 | 本轮实际受测提交 |
| --- | --- |
| nuttx | `7934920bb876195f2f45fc76b863235d5129c6e3` |
| apps | `b3b26aab77d67c8e15e626da07f6c9470b0a599f` |
| vendor/openvela | `eb539d2fa01e427e1013f839986c6f1025bb373a` |
| 已实际推送的验证集成 | [`e6db47d77e1622aaf3cf8fc79cdce4ba573f4575`](https://github.com/my-vela-crypto/manifests/commit/e6db47d77e1622aaf3cf8fc79cdce4ba573f4575) |

apps 与 vendor 定制仍通过 manifest 内校验补丁/15 个 overlay 文件交付，未向官方上游写入。原始报告如实保留运行时 manifest 提交和未提交集成改动的记录，不把最终发布 SHA 回填成当时已受测 SHA；上述 apps/vendor/nuttx 提交与固件哈希才是受测实现的依据。195 项隔离检查验证基线安装、幂等重试和冲突零写入。

失败 pilot `gui-stress-pilot*`、`gui-keypad-controls-01` 均保留历史，**不计入通过次数**。联调修正了宿主测试的实际帧尺寸/坐标、短截图文件名，以及控件需要完整按下再释放的注入方式；没有为这些脚本修正改 Guest 驱动或存储实现。早期 Keypad 相同截图保留为绘制证据，控件结果以独立补验的四项明确变化为准。

本轮“完整 GUI”指保留原完整 GUI 配置及服务的加密集成回归，覆盖上述四个 demo 和列出的交互，**不等于已运行全 Vela 所有通用测试集**。真实掉电、物理硬件 fault、无限期耐久性仍未验证；文件内容不受 XTS 认证、ASCII 名称等接口边界继续有效。视频按用户要求暂不制作，不属于本轮欠交项。
<!-- encfs-final-current:end -->

## 历史归档说明

以下保存 10 月 22 日、10 月 23 日上午及初版发布证据；历史轮的 193 项/13 文件及未测事项均有当时语境，最新结果以顶部为准。

历史状态摘要（2026-10-23 上午验收及随后初版发布）：当时验收：2026 年 10 月 23 日，Asia/Shanghai。源码实现、ARM64 编译和下述扩展模拟器验收通过。该轮验收完全使用本地源码。随后已将内核和可复现集成发布到团队 nuttx/dev、manifests/dev；演示视频当时待完成；后续按用户要求暂不制作。10 月 22 日的首次验收记录保留为历史证据。

## 2026-10-23 上午扩展验收（历史）

实际运行时间：2026-10-23T10:00:45+08:00 至 10:01:11+08:00。原始结果为 `encfs-results/acceptance-03/report.json`，六个阶段均通过，没有强制杀进程的阶段。

| 实际阶段 | 通过 | 失败 | 额外执行量 |
| --- | --- | --- | --- |
| 普通 exFAT 上独立名称层 | 116 | 0 | 1000 轮变长、多扇区、偏移写、fsync、完整读回、重开、rename、删除 |
| 独立损坏夹具 | 361 | 0 | 10 类卷头/物理根损坏、6 类真实坏目录项及恢复重挂 |
| 三层完整集成 | 176 | 0 | 1000 轮文件回归、100 次 stop/start、外部卸载与同路径其他挂载保护 |
| 同盘冷启动 1 | 4 | 0 | 不重新 provision |
| 同盘冷启动 2 | 4 | 0 | 不重新 provision |
| 同盘冷启动 3 | 4 | 0 | 不重新 provision |
| **合计** | **665** | **0** | 六次实际开机 |

每组 1000 轮回归成功写入 2,619,734 字节。独立名称层用时 5.682 秒，完整链 11.806 秒；这些是本机软件模拟的固定工作负载耗时，不是顺序吞吐基准或真实硬件性能结论。

新增 Guest 验证覆盖 chmod/fchmod、chown/fchown、utimensat/futimens、FIOC_FILEPATH、拒绝未允许 ioctl、大小写逻辑名称、点路径及编码膨胀边界。权限测试遵循当前 FatFs 的实际能力：接受普通 mode 位但不持久化，stat 报告 0777；mtime 以两秒精度保存，不宣称完整 POSIX 权限控制。

本轮新增测试发现并修复两处实际问题，断言没有为通过而放宽：

1. 外部 `umount /data` 后管理器状态仍为 READY。现按 inode 序号、操作表和私有上下文核对挂载身份；状态可降为 BLOCK_KEYED，stop 清理后能重新启动，不误卸载同路径的其他文件系统。旧源码宿主回归失败；修后 ASan/UBSan 管理器共 **652 项断言通过**，真实 Guest 也覆盖恢复路径。
2. 首次坏目录项已推进下层游标、但认证失败未增加逻辑位置时，`rewinddir` 未真正重置下层。现 `seek_mountptdir` 在目标为 0 时总调用下层 rewind，并传播下层错误。旧生产函数回归 **16 通过、7 失败**，修后 **23 通过、0 失败**；六类坏目录项的真实 Guest 重新读取全部正确拒绝。

主机密码 16,889、名称路径 6,073、第一层 10,000 次并发操作与 2,000 次注销竞争，以及 VFS/FatFs 事务测试均通过。该历史轮隔离补丁交付验证 **193 项通过**，覆盖 13 个应用文件、内容与权限一致、二次应用完全不改写、冲突预检零写入。

普通名称层盘通过实际 exFAT 目录解析确认物理名称为 E1_ 编码且测试内容仍为明文。完整加密盘未检出指定完整内容、逻辑名称或明文 exFAT 头。完整加密盘在三次冷启动前后均保持同一 SHA-256：

```text
f3bc43bc66888fc801aaebecf69ffdc9a067fbaf59d07e1108074772813d155e
```

失败的首次扩展尝试保留在 `encfs-results/acceptance-03-attempt1/`，其中 corruption 的失败就是上述 rewind 问题。最终通过结果仅来自 `acceptance-03`，不混用两次尝试。

## 初版实现范围（历史说明，新增入口见顶部）

按《第二层与第三层实现框架》及第三层 PPT 实现，实际对接第一层 `blkcrypt_set_key(encdev, key, 32)`。存储链为应用 `/data` → encpathfs → 隐藏的 FatFs/exFAT → `/dev/encdata0` → 第一层 XTS-AES-128 → `/dev/virtblk1` → 实际模拟器数据盘。

| 模块 | 已完成内容 |
| --- | --- |
| 第二层 encpathfs | AES-SIV 认证名称加密、大写无填充 Base32hex、逐分量转换、卷头认证；文件与目录操作、seek、truncate、dup、rename、syncfs；下层接管、忙时卸载及失败关闭资源恢复 |
| 第三层管理 | 32 字节板级主密钥，按规划的 salt 派生两组子密钥；独立 provision、start、stop、status；失败回滚、真实资源状态、临时密钥清理；编译开关控制的测试密钥和四阶段故障注入 |
| 应用与启动 | NSH `encfs`、`encfs_test`；独立 `goldfish-arm64-v8a-ap-encfs` 配置；加密启动失败不回退到明文 FAT/tmpfs；新数据盘为空白，Guest 明确建盘 |
| 集成修复 | 第一层忙时换钥/注销及引用管理；VFS 重命名前不预删 EncFS 目标、挂载目标引用与发布竞争；FatFs 句柄跟踪、同步及忙时卸载 |
| 可复现交付 | nuttx/vendor 补丁、apps 覆盖文件、SHA-256 清单、幂等应用脚本、真实模拟器 runner、接口和测试文档 |

第一层的扇区 XTS 算法未改写。为对接生命周期和防止在线破坏，修复了其正在使用时允许换钥及注销引用的边界问题。

## 初版编译与模拟器（历史）

- 主机为 Linux x86_64，使用仓库预编译 GCC 13.4.0，目标为 ARM64 goldfish AP 的 flat 构建。
- 实际执行 `CCACHE_DIR=/tmp/openvela-ccache ./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs/ --cmake -j4`，成功生成 `nuttx`、`nuttx.bin`、`vela_system.bin`、空白 `vela_data.bin`。
- 实际使用仓库的 Vela emulator 32.1.15.0、无窗口软件模拟运行。测试实例使用独立目录，未使用原有用户数据盘。
- 修复该版模拟器的 VVD 登记路径兼容性；数据盘仍在工作区测试目录，登记文件位于模拟器固定使用的 `~/.vela/`。

## 2026-10-22 首次验收结果（历史）

| 验证 | 结果 |
| --- | --- |
| AES-SIV、HKDF 主机测试 | 16,889 条断言通过；RFC 向量、OpenSSL 独立对照、边界、篡改、错误钥匙和清理检查 |
| 名称与路径主机测试 | 6,073 条断言通过；512 组随机往返、长度与编码规范、路径边界 |
| 第三层生命周期主机测试 | 403 条断言通过；注入失败、回滚、清理重试、忙时状态 |
| 第一层生命周期主机测试 | 10,000 次并发打开/读/关闭与 2,000 次注销尝试，ASan/UBSan 无报错，资源计数恢复 |
| VFS/FatFs 主机测试 | 正式函数源码在 ASan/UBSan 下验证发布竞争、引用回滚、脏状态、忙句柄与失败重试 |
| Guest 独立路径层 | **60 通过、0 失败**；普通 exFAT 上只叠加路径层，包含错路径密钥认证拒绝 |
| Guest 三层集成 | **105 通过、0 失败**；文件/目录操作、忙时换钥与卸载、无钥读写拒绝、错误主密钥、四阶段失败与重试 |
| Guest 同盘重启 | **4 通过、0 失败**；没有再次 provision，原文件内容及逻辑目录名恢复 |

Guest 合计 **169 个检查通过、0 失败**。前期失败日志保留在旧测试目录；上述结果只取最终成功运行 `acceptance-02`，不以主机替身测试代替真实模拟器结果。

## 2026-10-22 首次镜像证据（历史）

最终验收时间：2026-10-22T22:57:52+08:00 至 2026-10-22T22:57:56+08:00。

独立路径层数据盘：`/home/tourist/Desktop/openvela/encfs-results/acceptance-02/avd/encfs-plain-20261022-225752-72fb805f.vvd/vela_data.bin`。

Host 直接解析 exFAT 的已分配目录项，根目录包含 `.p` 与 `.encfs.meta`，`.p` 下实际名称为 `E1_...` 编码串，测试内容仍以明文存在。这单独证明了第二层的名称转换，避免只用块加密后的不可见性代替第二层验证。

完整加密数据盘：`/home/tourist/Desktop/openvela/encfs-results/acceptance-02/avd/encfs-crypt-20261022-225752-72fb805f.vvd/vela_data.bin`。

Host 对停止后的实际运行镜像检查：完整测试内容、`secret.txt` 的 ASCII/UTF-16LE 名称、编码名称前缀以及明文 exFAT 头均不可见。两次启动之间确认同一数据盘没有被替换，重启读回后仍保持同一 SHA-256：

```text
491af7a4580e58b59055a436d72bf6dd64a72de5ff6c66df9551b028dbb7d7ae
```

原始 JSON：`encfs-results/acceptance-02/report.json`。逐阶段串口日志在同一目录。JSON 还保存了镜像绝对路径、构建产物校验值、运行参数和当时的源码状态。

## 复现

首次应用（完整补丁随 manifest 保存，可在记录基线上离线重现；团队内核已有的相同修改会被识别并跳过）：

```sh
python3 .repo/manifests/tools/apply_encfs.py
CCACHE_DIR="$PWD/.build-cache/ccache" ./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs/ --cmake -j4
python3 .repo/manifests/tools/test_encfs.py --source-root "$PWD"
```

重新修改板级 defconfig 后，先用相同 build 入口执行 `--cmake resetconfig`，再构建。Python runner 需要 `pexpect`。它仅对本次新建测试盘执行一次明确的 `encfs provision --erase`，后续三次冷启动复用同一加密盘并禁止重新建盘。已有数据盘的普通启动只执行 `encfs start`。

接口和边界见 `.repo/manifests/docs/encfs_interfaces.md`；完整命令见 `.repo/manifests/README.md`；测试定义见 `.repo/manifests/docs/encfs_test_plan.md`。

## 初版补丁交付验证（历史）

该历史轮使用原始 nuttx、apps、vendor/openvela 基线在 `/tmp/encfs-apply-validation3-r2eph4d0` 独立建立临时副本，交付验证共 **193 项检查通过**。首次安装两份补丁和 13 个应用文件成功；应用结果逐文件内容及权限与已验收源码一致；二次执行不改变文件集合、内容、mtime、权限或 Git 状态；预先放入冲突应用文件时，脚本在任何补丁或快捷入口写入前拒绝执行并保留原文件。原 10 月 22 日的 198 项记录属于旧版校验脚本，不能直接比较计数大小。

## 2026-10-23 初版发布版本与远端状态（历史）

| 本地项目 | encfs-layer23 提交 |
| --- | --- |
| nuttx | `7934920bb876195f2f45fc76b863235d5129c6e3` |
| apps | `9f095e7003a14598af7e7771a6bbf08c215a1534` |
| vendor/openvela | `a5ebb9c9c0dcb53fb8f23acd007c02abb1b0fa22` |

2026 年 10 月 23 日，组织 Owner 将基础权限调整为 Write 后，两个目标仓库均返回有效的 `push: true`。使用 AC1Liu 的既有 SSH 身份正常快进推送，未强推：内核 [nuttx/dev](https://github.com/my-vela-crypto/nuttx/tree/dev) 包含实现与修复提交 [`7934920bb876195f2f45fc76b863235d5129c6e3`](https://github.com/my-vela-crypto/nuttx/commit/7934920bb876195f2f45fc76b863235d5129c6e3)，本报告及完整补丁、13 个应用文件、板级补丁和验收工具发布于 [manifests/dev](https://github.com/my-vela-crypto/manifests/tree/dev)。apps 与 vendor/openvela 继续通过经过隔离验证的覆盖文件和补丁交付，未向官方上游写入。此前的 403 和 `push: false` 是调整基础权限之前的历史诊断；连接器集成访问错误本身不能证明用户账号权限。成功推送与远端提交核对日志保存在工作区 `encfs-results/publication-20261023/`，并随更新后的交付包保存。

## 2026-10-23 上午验收范围及当时缺项（历史）

- QEMU 的板级主密钥是固定伪密钥，只用于课程联调。测试构建启用了故障注入和测试钥匙，生产用途需关闭并替换可信密钥来源。
- 逻辑名首版支持可打印 ASCII，最多 128 字节，编码名最多 234 字节；中文名、软硬链接、mmap 不在本版支持范围。
- 确定性文件名会泄露重复名称、长度和目录结构；第一层 XTS 不提供文件内容认证。不能据此宣称防回滚或抵御任意磁盘篡改。
- 初始空白盘未写过的扇区仍可能为零，不宣称整盘每个扇区都已经加密写入。
- 若 FatFs 的卷缓存因底层 I/O 失败而残留脏状态，包装层返回 `-EIO` 并保留资源，不虚报同步成功；持续硬件故障恢复及断电原子性未做真实故障验证。
- 当时未运行完整 GUI 或全部 Vela 通用测试集；本轮完整 GUI 配置回归已完成，范围见顶部，全部通用测试集仍不在验收范围。

- 当时稳定性仅覆盖两组各 1000 轮、100 次启停和 3 次独立冷启动；本轮一小时双线程压力见顶部，真实掉电与任意硬件 I/O 故障仍未验证。
- 当时仅有测试主密钥 API；本轮已补 NSH testkey 命令并实测。视频按用户要求暂不制作，不再列作待交付。
