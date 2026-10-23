# 第二、三层实现与测试报告

最新验收：2026 年 10 月 23 日，Asia/Shanghai。源码实现、ARM64 编译和下述扩展模拟器验收通过。该轮验收完全使用本地源码。随后已将内核和可复现集成发布到团队 nuttx/dev、manifests/dev；演示视频仍待完成。10 月 22 日的首次验收记录保留为历史证据。

## 2026-10-23 扩展验收（当前版本）

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

主机密码 16,889、名称路径 6,073、第一层 10,000 次并发操作与 2,000 次注销竞争，以及 VFS/FatFs 事务测试均通过。最新隔离补丁交付验证 **193 项通过**，覆盖 13 个应用文件、内容与权限一致、二次应用完全不改写、冲突预检零写入。

普通名称层盘通过实际 exFAT 目录解析确认物理名称为 E1_ 编码且测试内容仍为明文。完整加密盘未检出指定完整内容、逻辑名称或明文 exFAT 头。完整加密盘在三次冷启动前后均保持同一 SHA-256：

```text
f3bc43bc66888fc801aaebecf69ffdc9a067fbaf59d07e1108074772813d155e
```

失败的首次扩展尝试保留在 `encfs-results/acceptance-03-attempt1/`，其中 corruption 的失败就是上述 rewind 问题。最终通过结果仅来自 `acceptance-03`，不混用两次尝试。

## 实现范围

按《第二层与第三层实现框架》及第三层 PPT 实现，实际对接第一层 `blkcrypt_set_key(encdev, key, 32)`。存储链为应用 `/data` → encpathfs → 隐藏的 FatFs/exFAT → `/dev/encdata0` → 第一层 XTS-AES-128 → `/dev/virtblk1` → 实际模拟器数据盘。

| 模块 | 已完成内容 |
| --- | --- |
| 第二层 encpathfs | AES-SIV 认证名称加密、大写无填充 Base32hex、逐分量转换、卷头认证；文件与目录操作、seek、truncate、dup、rename、syncfs；下层接管、忙时卸载及失败关闭资源恢复 |
| 第三层管理 | 32 字节板级主密钥，按规划的 salt 派生两组子密钥；独立 provision、start、stop、status；失败回滚、真实资源状态、临时密钥清理；编译开关控制的测试密钥和四阶段故障注入 |
| 应用与启动 | NSH `encfs`、`encfs_test`；独立 `goldfish-arm64-v8a-ap-encfs` 配置；加密启动失败不回退到明文 FAT/tmpfs；新数据盘为空白，Guest 明确建盘 |
| 集成修复 | 第一层忙时换钥/注销及引用管理；VFS 重命名前不预删 EncFS 目标、挂载目标引用与发布竞争；FatFs 句柄跟踪、同步及忙时卸载 |
| 可复现交付 | nuttx/vendor 补丁、apps 覆盖文件、SHA-256 清单、幂等应用脚本、真实模拟器 runner、接口和测试文档 |

第一层的扇区 XTS 算法未改写。为对接生命周期和防止在线破坏，修复了其正在使用时允许换钥及注销引用的边界问题。

## 编译与模拟器

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

## 补丁交付验证

最新使用原始 nuttx、apps、vendor/openvela 基线在 `/tmp/encfs-apply-validation3-r2eph4d0` 独立建立临时副本，交付验证共 **193 项检查通过**。首次安装两份补丁和 13 个应用文件成功；应用结果逐文件内容及权限与已验收源码一致；二次执行不改变文件集合、内容、mtime、权限或 Git 状态；预先放入冲突应用文件时，脚本在任何补丁或快捷入口写入前拒绝执行并保留原文件。原 10 月 22 日的 198 项记录属于旧版校验脚本，不能直接比较计数大小。

## 本地版本与远端状态

| 本地项目 | encfs-layer23 提交 |
| --- | --- |
| nuttx | `7934920bb876195f2f45fc76b863235d5129c6e3` |
| apps | `9f095e7003a14598af7e7771a6bbf08c215a1534` |
| vendor/openvela | `a5ebb9c9c0dcb53fb8f23acd007c02abb1b0fa22` |

2026 年 10 月 23 日，组织 Owner 将基础权限调整为 Write 后，两个目标仓库均返回有效的 `push: true`。使用 AC1Liu 的既有 SSH 身份正常快进推送，未强推：内核 [nuttx/dev](https://github.com/my-vela-crypto/nuttx/tree/dev) 包含实现与修复提交 [`7934920bb876195f2f45fc76b863235d5129c6e3`](https://github.com/my-vela-crypto/nuttx/commit/7934920bb876195f2f45fc76b863235d5129c6e3)，本报告及完整补丁、13 个应用文件、板级补丁和验收工具发布于 [manifests/dev](https://github.com/my-vela-crypto/manifests/tree/dev)。apps 与 vendor/openvela 继续通过经过隔离验证的覆盖文件和补丁交付，未向官方上游写入。此前的 403 和 `push: false` 是调整基础权限之前的历史诊断；连接器集成访问错误本身不能证明用户账号权限。成功推送与远端提交核对日志保存在工作区 `encfs-results/publication-20261023/`，并随更新后的交付包保存。

## 已知边界与尚待交付

- QEMU 的板级主密钥是固定伪密钥，只用于课程联调。测试构建启用了故障注入和测试钥匙，生产用途需关闭并替换可信密钥来源。
- 逻辑名首版支持可打印 ASCII，最多 128 字节，编码名最多 234 字节；中文名、软硬链接、mmap 不在本版支持范围。
- 确定性文件名会泄露重复名称、长度和目录结构；第一层 XTS 不提供文件内容认证。不能据此宣称防回滚或抵御任意磁盘篡改。
- 初始空白盘未写过的扇区仍可能为零，不宣称整盘每个扇区都已经加密写入。
- 若 FatFs 的卷缓存因底层 I/O 失败而残留脏状态，包装层返回 `-EIO` 并保留资源，不虚报同步成功；持续硬件故障恢复及断电原子性未做真实故障验证。
- 本轮未运行原始完整 GUI 配置或全部 Vela 通用测试集，验收范围为本报告的加密专用固件和列出的用例。

- 本轮稳定性范围为两组各 1000 轮文件回归、100 次启停和 3 次独立冷启动，不等于长期压力、真实掉电或任意硬件 I/O 故障验证。
- 演示视频尚未制作；PPT 中的手工 NSH 输入主密钥入口目前仅有测试 API，未增加面向用户的命令。它在原规划中属于可选联调入口。
