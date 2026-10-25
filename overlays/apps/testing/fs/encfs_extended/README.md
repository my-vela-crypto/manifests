# EncFS 扩展文件系统和模拟中断验证

该应用用于专用测试卷，不能在有用户数据的 `/data` 上运行 `prepare`。

新配置 `goldfish-arm64-v8a-ap-encfs-extended` 集成三种现有回归：

* `apps/testing/testsuites/kernel/fs/cmocka_fs_test.c` 原始文件系统套件；
* `apps/testing/fs/fstest/fstest_main.c` 原始随机填充、CRC 读回、删除套件；
* `tests/testcases/vela_fs_test/fs/stress/read_and_write_loops_test.c` 原始追加与读回循环，命令名 `vela_fs_rwloops`。

原始 CMocka 套件会在伪根 `/file_link` 测试软链接，不能把它的成功解释为 EncPathFS 卷内支持软链接。主程序始终返回 0，所以验收脚本解析每个 CMocka 用例结果，不依赖退出码。

构建和运行：

```
CCACHE_DISABLE=1 ./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-extended --cmake -j8
python3 .repo/manifests/tools/test_encfs_extended.py --results-dir encfs-results/extended-new
```

独立命令 `encfs_extended prepare` 创建并 fsync 三个不再修改的基准文件（1、513、65537 字节）和第 0 代 `committed-0` 文件。`churn <起始代号>` 把 65537 字节逐块写入 `inflight`，随后 fsync、关闭、重命名为该代唯一的 `committed-N` 并同步。FatFs 的 rename 不覆盖已有目标，所以使用不可变提交文件，不假定原子覆盖语义。主机脚本交替在临时文件写入 4096 字节和提交完成后对该模拟器的独立进程组发送 SIGKILL；每次重启只执行 `encfs start`，禁止重新格式化。`verify <已提交代号>` 对三个基准文件和第 0 代至已确认提交的所有文件逐字节核验，同时核验长度与 EOF。

模拟器没有 RTC，初始系统时间为 1970，早于 FAT 时间戳的 1980 年下界。脚本每次启动后用 NSH 将测试时钟明确设为 2026 年 1 月 2 日，并记录这个操作；不修改 FatFs 时间转换实现。时间探针另行检查新文件 mtime 与当前测试时钟的误差不超过 2 秒。

探针的独立命令为 `encfs_extended clock`，输出 `now`、`mtime` 和 `delta` 的秒数；会在 `/data` 创建并删除自己的 `clock-probe` 文件。

未提交的 `inflight` 可以不存在、为空或部分写入，不计为恢复失败。该实验检验模拟器进程突然退出后的行为，不等同于真实硬件的掉电、写缓存断电、扇区撕裂或控制器故障。

其他上游用例需要按配置和实际语义选取，不能宣称全部 Vela 测试通过。例如，`maximum_file_name_test.c` 按全局 `CONFIG_NAME_MAX` 生成名称，必须先检查加密卷实际 `statfs.f_namelen`；`random_read_and_write_test.c` 的随机读取只检查返回字节数，并未核对读出内容，不能用作数据完整性证明；`power_off_test01.c` 的 `checkBuffer` 在循环中同时递增指针并重新计算 strlen，会漏查字符，不作为断电恢复的完整性判断依据。
