# EncFS 通用文件系统与模拟中断扩展验收

本报告记录 2026-10-24 新增的模拟器验证。视频不在范围内；模拟器 SIGKILL 不等同于真实硬件断电。2026-10-24 首版成品和其旧报告保持原样。本报告不将尚在运行的 24 小时压力计为通过，也不以文件系统套件结果代替在线换钥专项验收。

以下 `encfs-results/` 路径以 openvela 工作区根目录为基准。每轮保留原始串口、JSON 报告、固件哈希和实际磁盘路径；重复尝试使用新目录。

## 最终专用存储固件

参与构建的跟踪源码版本：nuttx `04264dc9a71be9dba96c4321bc5d7770e79d2dca`，apps `e198d0a1a032b886dc43c6cf5e7677f5dad4a42e`。原 `goldfish-arm64-v8a-ap-encfs` 配置启用 `CONFIG_ENCFS_KEYWRAP=y` 后，在全新输出目录 `cmake_out/vela_encfs_storage_final` 编译成功。apps 中原有的未跟踪 NIST 目录未参与本配置构建；报告保留完整 Git 状态，没有将整个工作区误报为干净。

2026-10-24 14:33:26 至 14:33:55（上海时区），独立新测试盘共 6 次启动，结果 **789 通过、0 失败**：

| 阶段 | 通过 | 失败 |
| --- | ---: | ---: |
| 独立名称层，含严格 UTF-8 | 172 | 0 |
| 破损名称及元数据 | 361 | 0 |
| 加密卷写入、UTF-8、1000 次文件回归与 100 次生命周期 | 232 | 0 |
| 同盘冷启动读回，第 1 次 | 8 | 0 |
| 同盘冷启动读回，第 2 次 | 8 | 0 |
| 同盘冷启动读回，第 3 次 | 8 | 0 |

写入停止后及三次只读冷启动后的实际磁盘 SHA-256 均为：

```
ab41e933d3dedd52c1167fb72eabd2c77abdeca30b0e52f02714723f75414f70
```

证据：`encfs-results/extended-20261024/storage-final-01/report.json`、同目录 6 份原始串口及 `independent-audit.json`。此组走默认直接派生密钥提供方；启用 KEYWRAP 编译选项不表示创建了包装密钥卷。

## 最终扩展配置回归

使用同一最终 nuttx `04264dc9a7`、apps `e198d0a1a`、tests `264e7219a8d815deaf38a202cba33fa220293dcb` 和 vendor `f08e648c5b1386c70fd74c637307de70cf255533` 的全新 `cmake_out/vela_encfs_extended_release` 固件，于 2026-10-24 14:38:20 至 14:39:25（上海时区）完成以下复验：

| 项目 | 实际结果 |
| --- | --- |
| 原始 CMocka 文件系统套件 | 46 通过、0 失败、0 运行时跳过 |
| 原始 fstest，10 轮、64 文件、最大 8192 字节 | OK 20、FAILED 0 |
| 原始 Vela 追加与偏移读回 | 100 轮通过 |
| 写入中 SIGKILL、同盘重启 | 10 次通过 |
| 提交后 SIGKILL、同盘重启 | 10 次通过 |
| 最终已提交数据读回 | 3 个哨兵及 0 至 20 共 21 代文件，1,442,328 字节逐字节验证通过 |
| 同长度单字节损坏负向 oracle | 偏移 4097 篡改被准确拒绝，EILSEQ 84 |
| 文件时间探针 | now 1767323045、mtime 1767323044，误差 −1 秒 |

证据：`encfs-results/extended-20261024/acceptance-final-01/report.json`、同目录 24 份原始串口及 `independent-audit.json`。独立审计逐条比对串口用例计数、每次 SIGKILL 的信号 9、重启前后代号、最终读回字节数和负向检查，并保存每份串口的 SHA-256。不同程序的计数单位不同，不将 46 个用例、20 个内部结果和 100 次循环相加为一个“总用例数”。

最终专用存储 ELF SHA-256 为 `de42d2b9044511da72868285a6691b1c7ac533a8c172a318d1369b04449ba763`，最终扩展 ELF SHA-256 为 `c032811f7323892987c805aabab9bdcfcf3845863b610a781b8561bf9360c278`。报告同时绑定各自的 `.config`、裸固件、系统镜像与初始盘哈希，整个测试期间未变更这些输入。

此前候选固件 `a599fe0a...` 的 `acceptance-01` 和 `storage-acceptance-01` 保留为历史；本节最终结论只采用上述 release 固件的新验收。

## 验证方法

扩展配置增加 CMocka、上游 fstest、原始 Vela 追加读回程序及 `encfs_extended` 恢复夹具。保持原 GUI 配置和正在运行的长期压力固件独立。

| 项目 | 实际执行范围 | 判定方法 |
| --- | --- | --- |
| CMocka | `apps/testing/testsuites/kernel/fs/cmocka_fs_test.c` 在当前 ARM64 flat 配置登记的 46 项 | 逐条解析 RUN、OK、FAILED、SKIPPED；主程序固定返回 0，不能只看退出码 |
| fstest | 上游 `apps/testing/fs/fstest`，10 轮、64 文件、最大文件 8192 字节 | 原程序随机内容 CRC 读回、删除、最终计数；禁止省略 FAILED 字段 |
| 原始 Vela | `read_and_write_loops_test.c`，命令 `vela_fs_rwloops /data 100` | 原代码执行 100 次追加及指定偏移读回，检查成功和失败标记 |
| 模拟中断 | 专盘 3 个固定哨兵文件与不可变 `committed-N` 文件 | 每次重启只启动已有卷，不重新 provision；逐字节检查所有已确认提交的代号、长度和 EOF |
| 负向完整性 oracle | 独立专盘的 65537 字节哨兵文件 | 保持长度不变，仅将偏移 4097 的一个非零字节覆盖为零，必须以 EILSEQ 拒绝 |

恢复夹具的哨兵长度为 1、513、65537 字节；每代提交文件为 65537 字节。写入暂存文件后 fsync、关闭，再重命名为该代唯一名称并同步。主机交替在暂存文件写完 4096 字节时及提交确认后，向该模拟器独立进程组发送 SIGKILL，确认全部进程退出后使用同一盘重新启动。

未提交暂存文件可以不存在、为空或部分写入，不能把它当作已持久化承诺。此实验不证明控制器缓存掉电、扇区撕裂、任意故障时刻下的日志恢复或硬件耐久性。

## 实际发现与处理

- **时间戳前置条件**：无 RTC 的模拟器初始 `time(NULL)=0`。FAT 时间戳下界是 1980 年，原 CMocka `stat01` 因此实际失败。扩展配置明确启用 NSH `date`；runner 每次启动设置固定 UTC 测试时钟 `2026-01-02 03:04:05`，再用独立探针检查新文件 mtime 误差不超过 2 秒。未修改 FatFs 来掩盖原失败。
- **日志证据**：原专用存储配置仅将 syslog 写入 1 KiB RAMLOG，不能收集完整上游用例输出。新扩展配置使用控制台 syslog，保留每项原始结果。
- **CMocka 依赖**：ARM64 需要启用 `ARCH_SETJMP_H`。最终配置从干净输出目录完整解析 Kconfig，避免复用旧缓存中的残缺依赖。
- **rename 语义**：恢复夹具最初试图覆盖已有 `current`，实际返回 EEXIST。随后改用不可变提交名称；这不证明覆盖已有目标的 rename 或原子替换语义受到支持。
- **历史结果不覆盖**：配置、日志、时间戳及初版夹具问题的失败记录仍保存在 `encfs-results/extended-20261024/exploratory-01` 至 `exploratory-05`；原因索引为同级 `history.json`。

## 覆盖边界

46 项计数指当前 CMocka 程序实际登记并执行的用例，不包含原源文件中被注释或条件编译排除的代码。两个软链接用例在伪根 `/file_link` 创建链接，不能据其成功宣称 EncPathFS 卷内支持软链接。

本轮未宣称全部 Vela 测试集通过。旧 `tests/testcases/vela_fs_test` 有多个压力、稳定、手工断电和只读镜像程序；本轮只将上述原始追加读回程序计入已执行集合。例如：最大文件名程序按全局 `CONFIG_NAME_MAX` 取长度，必须先匹配加密卷的实际逻辑名上限；随机读写程序未核对读出的完整内容；`power_off_test01.c` 的检查函数同时移动指针并重新计算 strlen，会漏查字节，不能用作完整性 oracle；只读镜像 MD5 程序还需要其预期镜像与校验清单。其他未执行程序不计通过，也不以通用的“全部通过”代替逐项证据。

## 复现

```
CCACHE_DISABLE=1 ./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs --cmake -b cmake_out/vela_encfs_storage_final -j8
python3 .repo/manifests/tools/test_encfs.py --config goldfish-arm64-v8a-ap-encfs --build-dir cmake_out/vela_encfs_storage_final --results-dir encfs-results/storage-new

CCACHE_DISABLE=1 ./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-extended --cmake -b cmake_out/vela_encfs_extended_release -j8
python3 .repo/manifests/tools/test_encfs_extended.py --build-dir cmake_out/vela_encfs_extended_release --results-dir encfs-results/extended-new --powercuts 20
```

测试会创建自己的新模拟器实例和数据盘。结果目录必须使用新名称；`provision --erase` 只用于该轮新测试盘的初次准备。恢复阶段禁止重新格式化。
