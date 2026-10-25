# 第二、三层扩展实现与测试报告

更新日期：2026-10-25，Asia/Shanghai。本文是本轮扩展的当前验收稿：UTF-8 文件名、包装卷在线主密钥轮换、通用文件系统回归、可控模拟器中断及完整 GUI 复验已有实际通过证据；**两轮 24 小时压力仍在运行，不能宣称整轮完工**。视频按要求不制作。本轮使用模拟器。

2026-10-24 首版报告及 `encfs-layer23-final-20261024` 成品包保留，原包内部不修改。本文中的 `encfs-results/`、`cmake_out/` 等路径均相对于 `/home/tourist/Desktop/openvela`；各 JSON 与原始串口是具体运行的依据，不把不同程序的断言、循环和审计检查数相加成一个“总用例数”。

## 当前结果

| 项目 | 实际结果与证据 |
| --- | --- |
| 最终专用存储固件 | **789 通过、0 失败**，六次开机；`encfs-results/extended-20261024/storage-final-01/report.json` 与同目录 `independent-audit.json` |
| 最终通用文件系统配置 | 原始 CMocka **46/0、0 skip**；fstest 十轮 **20/0**；原 Vela 追加读回 **100 轮**；另有 **20 次 SIGKILL** 及同盘重启；`encfs-results/extended-20261024/acceptance-final-01/` |
| UTF-8 宿主独立验证 | **1,237,942 项通过**，含 224 个 ASCII 名称与旧实现密文逐字节相同；`encfs-results/extension-20261024/utf8/host-report.json` |
| 在线换钥正式专项 | 180 秒、**1,396 次事务、0 错误**；持有句柄 helper **27/0**、主命令 **28/0**，独立审计 **216/216**；`encfs-results/keywrap-180-20261025-02/` |
| 源码隔离交付 | **249/249**，25 个 apps overlays，首次安装、重复应用不改写、冲突零写入；`encfs-results/extension-20261024/delivery/` |
| 新固件完整 GUI | 正式 `gui-extension-20261025-02` 七阶段通过，父 wrapper 观察真实退出码 **0**；232/0 存储回归、8/0 冷启动、60 秒 **452 次事务、0 错误**；独立分析核验 28 张 PNG 与 4 个所选页签头 |
| 首版 24 小时压力 | **running**；11:06 快照为 75,038.344 秒、525,125 次事务、0 错误，不是完成结果 |
| 包装卷 24 小时压力 | **running**；实际于 2026-10-25 11:03 启动，压力主体约 11:03:30 开始；至少到 **2026-10-26 11:04 后**才可能取得完整结果 |

## 源码和构建

| 项目 | 本轮最终跟踪源码提交 |
| --- | --- |
| nuttx | `04264dc9a71be9dba96c4321bc5d7770e79d2dca` |
| apps | `e198d0a1a032b886dc43c6cf5e7677f5dad4a42e` |
| vendor/openvela | `f08e648c5b1386c70fd74c637307de70cf255533` |
| 通用测试所用 tests | `264e7219a8d815deaf38a202cba33fa220293dcb` |

已实际构建专用存储、完整 GUI 和 extended 三种 ARM64 goldfish 配置，分别使用 `cmake_out/vela_encfs_storage_final`、`cmake_out/vela_goldfish-arm64-v8a-ap-encfs-gui` 和 `cmake_out/vela_encfs_extended_release`。专用存储 ELF 的 SHA-256 为 `de42d2b9044511da72868285a6691b1c7ac533a8c172a318d1369b04449ba763`；extended ELF 为 `c032811f7323892987c805aabab9bdcfcf3845863b610a781b8561bf9360c278`；GUI ELF 为 `794119b446b764f4ada8c175467a29b4758001925d63c224273d3e2c1cc10615`。每轮报告还绑定 `.config`、裸固件、系统镜像及初始空白盘。

apps 原有的未跟踪 NIST 目录保留，没有计入这三种配置的受控交付，也没有把整个工作区误称为干净。运行报告如实记录当时 manifest 为 `b53dd710...` 且有未提交工具变更；该旧提交单独不能标识本轮 runner。在线换钥审计另记录工具实际哈希，其输入快照是在启动后不久采集，**没有冒充启动前收据**。

nuttx 已实际推送至团队 `dev`：日志 `encfs-results/publication-20261025/nuttx-push.log` 记录 `7934920bb8..04264dc9a7 HEAD -> dev`，对应[已发布内核提交](https://github.com/my-vela-crypto/nuttx/commit/04264dc9a71be9dba96c4321bc5d7770e79d2dca)。manifest扩展源码、复现工具及已完成测试的轻量证据已正常快进发布至团队dev：[`31aef0eba7953d0185bc94b2f95f9da4fedfe31c`](https://github.com/my-vela-crypto/manifests/commit/31aef0eba7953d0185bc94b2f95f9da4fedfe31c)。证据目录为`validation/encfs-extension-20261025`，不包含临时master、slot或实际盘。两轮24小时仍未完成，新成品包尚未生成，不能把当前源码发布说成全部交付完成。

## 第二层：中文与严格 UTF-8 文件名

名称验证按 [RFC 3629](https://www.rfc-editor.org/rfc/rfc3629) 接受 Unicode scalar 的规范 UTF-8 字节序列，支持中文、两字节拉丁字符和四字节补充平面字符，例如 emoji。拒绝无效首字节、孤立 continuation、截断字符、过长编码、UTF-16 surrogate 和超过 U+10FFFF 的值；ASCII 控制字节、DEL、斜线及结构分量 `.`、`..` 仍拒绝。

逻辑分量上限保持 **128 字节**，不是 128 个汉字。AES-SIV 的关联数据、`E1_` 前缀和大写无填充 Base32hex 均未改变；既有 ASCII 物理文件名无须迁移。名称不做 Unicode 归一化或大小写折叠，NFC 与 NFD 字节不同的名称可同时存在。POSIX/C 输入以第一个 NUL 终止；对认证后解密出的显式长度明文，内嵌 NUL 会被拒绝，过长 UTF-8 NUL/斜线编码也不能绕过检查。

宿主测试编译实际 codec、AES、CMAC 和 AES-SIV，以 Python 严格 UTF-8 解码器为独立 oracle：穷举 **1,112,064 个 Unicode scalar** 和 **65,536 个两字节输入**，再检查无效/混合输入、真实加解密、容量边界，以及有合法 SIV 标签的恶意名称明文。224 个旧 ASCII 样本与原 codec 的输出完全相同，UBSan 未发现问题。真实模拟器另覆盖中文目录、中文/emoji 文件、读写、stat、逻辑 FILEPATH、readdir、目录和文件改名、128/129 字节边界及三次冷启动读回。

## 第三层：稳定数据密钥与在线主密钥轮换

新增包装卷在显式 provision 时生成独立随机的 block/path 数据密钥，卷外 provider 使用主密钥派生的包装密钥保护这两组稳定密钥。112 字节 keyslot 的版本、provider id、非零 generation 和保留字段均受 AES-SIV 认证。`encfs rekey <id>` 只替换包装记录，不改变正在使用的数据密钥，因此不需要卸载或重写数据卷。

provider 必须独立于 `/data` 及隐藏下层 `/datap`，并提供主密钥、随机数、读取 slot 和原子比较替换接口。当前模拟器使用私有 hostfs/9p 文件作为 provider；这实现了可复现的板级接入边界和模拟后端，**不等同于真实硬件 TEE**。切换 provider 仍要求全部 STOPPED；在线 rekey 只允许 READY wrapped 卷。同 id 返回 EALREADY；提交失败保持旧 slot 和当前 id/generation，缺失或损坏 slot 不回退到 legacy 来源、不格式化。完全释放资源后立即擦除缓存中的明文数据密钥。

旧版直接从 master 派生数据密钥的卷继续可读，但在线 rekey 返回 ENOTSUP。把旧派生密钥再包装并不能使旧 master 失去推导能力，因此没有将这种操作计作换钥成功。需要迁移时应保留旧卷，再复制到使用独立随机数据密钥的新卷；本轮没有静默原地迁移。

宿主实现复验覆盖实际密码源代码、OpenSSL 独立参照、896 个逐 bit slot 篡改、错误 master、partial/positive provider 错误、CAS stale、generation 溢出及临时槽恢复边界。结果包括包装逻辑 **801/0**、OpenSSL **2 个 slot/0**、独立对抗 **3,248/0**、rekey CLI 生产/测试配置 **28/0、34/0**，既有 testkey CLI **405/0、5/0** 和 manager **652 项断言通过**。完整命令、工具版本和源码指纹见 `encfs-results/extension-20261024/keywrap-implementation/report.json`。宿主结果没有代替下述真实模拟器句柄验收。

## 专用存储与通用文件系统验收

专用存储运行区间为 2026-10-24 14:33:26–14:33:55（上海时区）。六次实际启动分别得到：

| 阶段 | 通过 | 失败 |
| --- | ---: | ---: |
| 独立名称层，含 UTF-8 | 172 | 0 |
| 损坏名称与元数据夹具 | 361 | 0 |
| 完整链写入、UTF-8、1000 轮文件回归及 100 次启停 | 232 | 0 |
| 同盘冷启动 1、2、3 | 各 8 | 0 |
| 合计 | **789** | **0** |

写入停止后与三次只读冷启动后的实际加密盘 SHA-256 均为 `ab41e933d3dedd52c1167fb72eabd2c77abdeca30b0e52f02714723f75414f70`。此组使用默认直接派生来源；仅启用 KEYWRAP 编译选项不等于创建了 wrapped 卷。

通用 extended 配置于 2026-10-24 14:38:20–14:39:25 完成原始 CMocka 46 项、fstest 十轮和原 Vela 追加读回 100 轮。独立审计解析 **24 份原始串口**，逐条核对执行、通过、失败和跳过字段；CMocka 主程序固定返回 0，验收未只看其退出码。无 RTC 的模拟器明确设置有效 FAT 测试时钟，mtime 探针误差为 −1 秒；没有修改 FatFs 来掩盖初始时间为零的失败。

另外使用专用新盘完成 10 次写入暂存文件中途、10 次提交确认后的 SIGKILL。每次确认该模拟器进程组退出，再以原盘启动，只读回已提交的代号和哨兵，不重新 provision。最终验证 3 个哨兵及 0–20 共 21 代文件，**1,442,328 字节逐字节相符**。独立同长度单字节篡改在偏移 4097 被内容 oracle 以 EILSEQ 拒绝；这是对测试校验器的负向验证，不能解释为 XTS 自动认证任意文件内容。

这 20 次实验验证的是明确程序边界下的模拟器进程中断模型。未提交暂存文件不作为持久化承诺，也没有据此宣称扇区撕裂、控制器缓存失电或任意故障时刻均能恢复。46 项仅指当前配置实际登记的用例；软链接用例在伪根执行，不能据此推断 EncPathFS 卷内支持软链接；其他未运行的 Vela 测试未计通过。

## 在线换钥正式模拟器专项

正式结果采用 `encfs-results/keywrap-180-20261025-02/`，运行区间为 2026-10-25 11:05:56–11:09:22。六阶段报告、原始串口、数据盘、外部 slot、截图和输入指纹经 `encfs-results/extension-20261025/keywrap-180-independent-audit.json` 复核，**216 项均通过**；压力统计与截图再由同目录 `analysis/analysis.json` 独立解析。

| 项目 | 实测 |
| --- | --- |
| 请求 / Guest 总时长 / 宿主经过时间 | 180 秒 / 180,813 ms / 183.025 秒 |
| worker 0 / worker 1 时长 | 180,365 / 180,024 ms |
| 完成事务，worker 0 / worker 1 | **1,396**，638 / 758 |
| 逻辑写入 / 完整读回校验 | 13,403,409 / 26,660,092 字节 |
| 错误 / 完成标记 | **0 / completed=1** |
| 原始进度 / 核验 PNG / 压力所选页签头 | 6 / 12 / 4 |
| 原 open/dup/DIR helper / 主命令 | **27/0 / 28/0** |

关键并发证明采用**换钥成功 acknowledgment 之后**的两个原始进度样本：90.623 秒时两线程为 **329/371**，120.684 秒时为 **439/491**，均继续增长；命令及成功 acknowledgment 位于 workload START 与 RESULT 之间。没有只用换钥之前的旧计数和最终计数作比较，也没有把“主任务未退出”当作每个线程仍在工作。

专项还验证默认 legacy 卷拒绝 rekey、缺少替换 master、失败提交保持旧 slot、空闲 rewrap 前后数据盘哈希不变、删除旧 master 后仍可冷启动，以及错误/缺失/截断 master 和缺失/损坏 slot 均拒挂。其代次依次为 `id0/gen1 → id1/gen2 → id2/gen3 → id3/gen4`。

持有句柄的真实 helper 先创建并 fsync 文件，保持原 fd、dup 和 DIR 打开，验证 busy stop、同 id 拒绝和一次失败提交，再成功换钥。随后使用原描述符读取旧内容、经 dup 追加到 8193 字节；原 DIR 能读到逻辑文件名，并在 rewind 后再次读到。关闭句柄后 stop/start 完整读回；最终另启独立模拟器，以 id3/gen4 冷启动读回，且该只读冷启动前后的数据盘哈希一致。

提交边界中断以原始串口唯一 `ENCFS_KEYSLOT_PAUSED before_atomic_commit ms=10000` 为准：它在临时槽 fsync、关闭和完整读回核对之后、正式替换之前出现。此后执行 SIGKILL，确认旧 committed slot 不变、完整临时记录保留；新进程先用旧 slot 读回原数据，重试时恢复临时槽，再完成真实换钥。仅看到临时文件长 112 字节不会被视为到达该边界。

早期 `keywrap-60-20261024-02` 虽已完成句柄、恢复和冷启动，其换钥后的并发推进证据不充分，保留为探索历史，不计作上述正式并发证明。180 秒也不是长期耐久性保证；逻辑载荷字节数不包含元数据等物理 I/O，短于 300 秒的运行不用于宣称稳态内存无泄漏。

## 完整 GUI 正式复验

正式结果采用 `encfs-results/gui-extension-20261025-02/`，实际运行区间为 **2026-10-25 11:12:04–11:15:39**。父 wrapper 用 `Popen.wait()` 观察到真实 `program_return_code=0`、报告 passed，本次外层工具也返回 0；收据为 `encfs-results/extension-20261025/gui-wrapper-rc.json`。该轮七次开机均完成，没有用 JSON 的 passed 字段代替程序返回值。

新 GUI 固件保留原完整配置和服务，覆盖 music、keypad_encoder、stress、widgets 四个 demo。Keypad 弹窗关闭、复选框、滑块、文本页签的实际像素变化分别为 **1,012,668 / 225 / 3,170 / 283,880**；Widgets 的页签、复选框和滑块亦完成真实输入与画面核对。存储回归 **232/0**，含 1000 轮文件操作和 100 次启停；最终同盘冷启动 **8/0**，包括 UTF-8 文件读回。

并行压力实际 Guest 总时长 **60,622 ms**，两个线程各 **60,064 / 60,341 ms**，完成 **452 次事务**（211 / 241），错误为 **0**。`analysis/analysis.json` 从原始串口独立解析计数与持续时长，并核验 **28 张 PNG、4 个压力所选页签头**，结果 passed。此处的 60 秒是短期功能回归，不代替后面的 24 小时运行。

较早的 `gui-extension-20261025-01/report.json` 于 11:08:31 已记录七阶段 passed，包含 441 次短期压力事务和 0 错误；但其外层工具返回 **143**，没有可靠观察到 runner 的真实退出码。原记录及 `encfs-results/extension-20261025/gui-01-outer-tool-observation.json` 保留为历史，不猜测其程序返回值，不作为本节正式成功依据。

GUI 覆盖限于保留的四个 demo 和上述交互；不等于穷举所有控件。无音频宿主参数下的 music 画面不作为声音链路验收。实际帧为 1280×800，BMP 按真实 RGB565 解码；压力页签头与各自基线作精确 RGB 比对。

## 两轮 24 小时压力：仍在运行

| 运行 | 冻结输入与实际起点 | 当前可作的结论 |
| --- | --- | --- |
| 首版基线 | `encfs-results/endurance-24h-baseline-20261024/`；压力主体 2026-10-24 14:15:16 开始 | 11:06 快照为 75,038.344 秒、525,125 次事务、0 错误；不是最终完成 |
| 新版 wrapped 卷 | `encfs-results/endurance-24h-wrapped-20261024/`；实际启动 2026-10-25 11:03:13，压力约 11:03:30 开始 | 实际 running；此前启动审批超时没有执行，不能把文件夹的 10 月 24 日日期当作运行起点 |

首版监测证据为 `encfs-results/extension-20261025/baseline-monitor.json`：2,498 个原始进度样本全部满足双线程计数递增与确定性载荷字节复算，2,451 个宿主检查点连续；509 张 PNG 哈希及 492 个所选页签头核验正常，8 个 frozen 输入哈希未变。最大 Guest 进度间隔 30.276 秒，最大宿主检查间隔 37.051 秒。监测明确写 `status=running`、`completion_claim=false`，不替代最终 RESULT。

Guest 进度与宿主 NSH 检查采用独立时钟，截图也有开销，不能要求两者恰为 2880 个。首版冻结固件只说明首版长期行为，不证明新增 UTF-8/keywrap。新版压力至少到 **10 月 26 日 11:04 后**才可能完成；随后还须做同盘冷启动、helper、提交中断恢复及独立原始串口审计。新版冻结工具不能修改，完成后仍须额外确认换钥成功 acknowledgment 后的两个样本间双 worker 均继续增长，不能单凭较早判据的布尔字段下结论。

## 交付与待收尾

`encfs-sources.json` 已刷新两个完整 binary patch：nuttx 覆盖 59 个文件，vendor 覆盖 6 个文件；apps 以官方 `7b36f234...` 为基线，受控新增文件为 25 个。`encfs-tested.xml` 的 232 个项目仅将 nuttx 更新为上述最终提交，apps/vendor 保持官方 patch 基线，其余 231 个项目未改。249 项隔离检查覆盖从基线首次安装、每个文件内容和权限、二次应用的内容/模式/mtime 与 Git 状态不变，以及冲突预检在任何改写前失败。10 月 25 日再次只读复核仍与该报告和 SHA-256 元数据相符。

当前还须补齐两轮 86,400 秒压力及后续恢复分析，再更新最终报告和公开长期证据，生成、校验新成品包并发布最终状态。首版成品继续保留；不将运行中的测试、新准备的脚本或已暂存文件写成已完成交付。

安全边界保持明确：XTS 不认证任意内容；旧 slot 与旧 master 的历史副本仍可恢复稳定数据密钥；generation 不是可信防回滚计数器。hostfs/9p 测试后端证明模拟器进程中断模型，不证明真实介质失电、恶意宿主防护、跨宿主进程原子 CAS 或硬件 TEE 保护。上述边界不改变本轮已实际取得的模拟器结果。
