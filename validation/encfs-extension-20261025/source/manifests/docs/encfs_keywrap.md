# 模拟器主密钥在线轮换验收

本扩展针对新建的 wrapped 卷：独立生成稳定的数据块密钥和路径密钥，由卷外 provider 的主密钥包装。在线轮换只替换包装记录，文件系统保持挂载，已打开的文件、dup 描述符和目录句柄继续可用。它不会在运行中直接替换 XTS 密钥，也不会重写已有用户数据。

本说明提供模拟器复现步骤，不把 host 文件模拟成真实 TEE。实现契约和字节格式见 [`nuttx/fs/encfs/README.md`](https://github.com/my-vela-crypto/nuttx/blob/dev/fs/encfs/README.md) 与 [`encfs.h`](https://github.com/my-vela-crypto/nuttx/blob/dev/include/nuttx/fs/encfs.h)。验收结果以本次实际 runner 的 `report.json`、原始串口、截图和哈希为准；本说明本身不是通过证明。

## 配置与编译

需要 `CONFIG_ENCFSMGR=y`、`CONFIG_ENCFS_KEYWRAP=y`；模拟器文件 provider、故障钩子和手工验收另需 `CONFIG_ENCFS_TESTING=y`、`CONFIG_SYSTEM_ENCFS=y`、`CONFIG_EXAMPLES_ENCFS_TEST=y`，并启用 hostfs 或 9p 的独立 `/share` 挂载。当前完整 GUI 目标保留 LVGL 和原有服务，并提供真实帧缓冲截图、触控命令。

在 openvela 工作区根目录构建当前配置：

```bash
./build.sh vendor/openvela/boards/vela/configs/goldfish-arm64-v8a-ap-encfs-gui/ --cmake -j4
```

修改过 defconfig 时，先用同一构建入口执行 `--cmake resetconfig`，再编译。不要将已有数据盘复制到本次新建的破坏性验收实例。

## 一次自动复现

```bash
python3 .repo/manifests/tools/test_encfs_keywrap.py \
  --source-root "$PWD" \
  --results-dir "$PWD/encfs-results/keywrap-fresh-01" \
  --stress-seconds 180
```

结果目录必须为空。runner 创建独立实例和新的随机测试材料，只对新建测试盘执行显式 provision；后续阶段复用该盘和它的卷外 keyslot，冷启动不再格式化。默认 180 秒、最低 150 秒，要求从换钥成功确认之后的原始串口取得至少两个新进度样本，且两个工作线程都继续增长；不能用换钥前的旧计数与最终计数比较冒充该证明。这是换钥专项验证，不替代另行运行的 24 小时压力验收。

自动验证包括：legacy 卷拒绝 rekey、缺失 keyslot 拒绝挂载、失败提交保持原槽、空闲时轮换不改变数据盘、GUI 与双线程实际 I/O 期间轮换、同盘冷启动、错误/缺失/截断主密钥、被破坏/缺失 keyslot，以及在提交边界强制终止模拟器后的恢复。它必须检查原始串口最终统计和各项断言，不以启动了命令作为通过。

## 手工新建实例

先在宿主终端启动一个全新的、专用于本轮验证的实例。以下示例使用固定名称以便展示路径，若该实例已经存在，应换一个新的名称；不要对已有实例重新执行 destructive provision。

```bash
ENCFS_WRAP_AVD="$PWD/encfs-results/manual-keywrap-avd"
ENCFS_WRAP_INSTANCE="encfs-keywrap-manual-01"
mkdir -p "$ENCFS_WRAP_AVD"
ANDROID_AVD_HOME="$ENCFS_WRAP_AVD" CUSTOM_AVD_SPACE="$ENCFS_WRAP_AVD" \
  ./run-emulator.sh goldfish-arm64-v8a-ap-encfs-gui \
  -keep "$ENCFS_WRAP_INSTANCE" -no-window -no-audio \
  -gpu swiftshader_indirect -accel off
```

等到 `encfs>` 提示符后，再在另一个宿主终端进入同一 openvela 根目录，生成公开测试用途的一次性材料：

```bash
python3 - <<'PY'
from pathlib import Path
import os

share = (Path.cwd() / "encfs-results/manual-keywrap-avd" /
         "encfs-keywrap-manual-01.vvd/share")
assert share.is_dir(), "先启动指定的新模拟器实例，确认 share 已创建"
directory = share / "keys"
directory.mkdir(mode=0o700)  # 已存在则拒绝，避免覆盖已有卷的密钥
for key_id in range(4):
    path = directory / f"master-{key_id:08x}.bin"
    path.write_bytes(os.urandom(32))
    path.chmod(0o600)
random_path = directory / "volume-random.bin"
random_path.write_bytes(os.urandom(64))
random_path.chmod(0o600)
print("已生成独立模拟器测试材料；未输出任何密钥字节：", directory)
PY
```

`master-00000000.bin` 到 `master-00000003.bin` 各为 32 个原始二进制字节，不是十六进制文本。`volume-random.bin` 是本次 provision 使用的 64 个新随机字节；provider 消费它一次，绝不悄悄回退到可能未充分播种的 guest PRNG。重复 destructive provision 必须另供新随机材料。

上述文件用于测试模型，不能代表硬件密钥不可导出。目录必须是该 provider 专用的私有目录，宿主不能并发改写 keyslot 或保留名 `encfs.keyslot.tmp`。

## NSH 首次初始化与真实在线换钥

仅在刚创建、可擦除的独立测试盘上执行：

```text
encfs stop
encfs keyfiles /share/keys
encfs start
encfs provision --erase
encfs start
encfs status
mkdir /data/验证
echo ENCFS_WRAPPED_MANUAL_PAYLOAD > /data/验证/密钥.txt
cat /data/验证/密钥.txt
encfs_test rekey 1
encfs status
cat /data/验证/密钥.txt
```

第一次 `start` 应为 `STOPPED result=-126`（缺 keyslot），且不会创建元数据或格式化。随后显式 `provision --erase` 才会格式化并初始化新 wrapped 卷。首次成功挂载时应显示 `wrapped=1 master_id=0 generation=1`。

`encfs_test rekey 1` 是实际在线验收。它持有一个已 fsync 的文件、它的 dup 描述符和一个目录句柄，先检查 busy stop、同 ID 拒绝以及一次失败提交；随后在这些句柄仍打开时执行真实 rekey，使用原描述符读取旧内容、通过 dup 追加到 8193 字节，并用原目录句柄读取逻辑名和 rewind。关闭句柄后，再 stop/start 并读回全量内容。

成功应有唯一 `ENCFS_REKEY_RESULT pass=N fail=0 next_master_id=1`，且状态为 `wrapped=1 master_id=1 generation=2`。主密钥标识不是秘密；CLI 不打印主密钥或数据子密钥。helper 只创建并删除自己的专属测试文件，用户写入的示例文件应保留。

需要验证空闲轮换时，可在没有其他数据写入的时段，分别在宿主记录实际 `vela_data.bin` 的 SHA256，在 NSH 执行 `encfs rekey 2`，再次记录。只有 keyslot 应变化，数据盘应不变；若同时运行存储压力任务，数据盘变化是正常写入，不能用整体盘哈希判断换钥是否重写了数据。

## 错误与失败提交

在 READY 状态执行：

```text
encfs rekey 99
encfs keyslot-failcommit on
encfs rekey 2
encfs status
cat /data/验证/密钥.txt
encfs rekey 2
encfs status
```

若没有 id 99 的主密钥文件，第一条返回 `-126`。一次 failcommit 使下一次替换在写入、fsync、读回临时槽之后、正式 rename 之前返回 `-5`；旧的 `encfs.keyslot` 字节及当前 id/generation 不变，已打开句柄和数据读写继续正常。之后普通重试才成功。重复使用当前 master id 返回 `-114`（`EALREADY`），不把未换主密钥的操作算作轮换。

切换 provider 必须完全 STOPPED。`encfs keyfiles /share/keys` 在 READY 时返回 `-16`。原有 `encfs testkey board|none|<64-hex>` 仍是测试接口；它在完全停止时显式退出 file-provider 模式并选择 legacy 来源，不能用它冒充在线换钥。

## 冷启动与材料保存

先执行 `encfs stop`，退出模拟器；再次以相同 AVD 根目录、相同 `-keep` 名称启动。NSH 只执行：

```text
encfs stop
encfs keyfiles /share/keys
encfs start
encfs status
cat /data/验证/密钥.txt
```

不得再次 provision。成功后确认 id/generation 与上次轮换一致，示例内容仍在。数据盘和卷外 keyslot 必须成套保存：

```text
<实例>.vvd/vela_data.bin
<实例>.vvd/share/keys/encfs.keyslot
<实例>.vvd/share/keys/master-<当前id的8位十六进制>.bin
```

缺失当前主密钥或 keyslot 会拒绝挂载；损坏认证记录或使用错误/截断主密钥也会拒绝。普通 start 不重建 slot，不修复错误密钥，不格式化磁盘。备份时先停止 guest 写入并同步记录盘和 slot 的哈希。保留旧主密钥文件并不是挂载新 id 的必要条件；测试可删除旧 id 文件后，再以当前 id 做冷启动读回。

## 提交边界中断和临时槽恢复

故障专项优先使用自动 runner，确保只终止本次独立模拟器进程组。其步骤是设置 `encfs keyslot-pausecommit 10000`，后台执行一次 rekey，等待串口唯一标记：

```text
ENCFS_KEYSLOT_PAUSED before_atomic_commit ms=10000
```

此标记在临时文件 fsync、关闭、完整读回检查成功之后打印并 flush，再进入暂停。仅看到 `.tmp` 已有 112 字节不能证明 fsync 已完成，不能代替该标记。runner 此时记录旧 committed slot 和临时槽哈希，执行 SIGKILL，而非干净关机。

新进程启动首先仍用旧 committed slot 正常挂载并读回原数据。再次 rekey 时，provider 先比较已提交槽是否仍匹配预期，再自动处理保留名临时文件。完整 112 字节残留必须 SIV 认证通过，且解出的稳定数据密钥必须与当前卷相同；0–111 字节残留仅接受合法 magic/version 前缀及长度上限。截断残片本身不具有认证，允许删除它依赖专用私有目录和单 writer 前提。异常前缀、超长、完整异卷记录或认证失败的文件会保留并报错，不会默默删除。正式槽从不在恢复时先被 unlink。

## 兼容与安全边界

legacy 卷保持原来的直接派生格式和正常读取路径，但 `encfs rekey` 返回 `-138`（本 NuttX 目标的 `ENOTSUP`，宿主 Linux 对应值可能不同）。把旧 master 派生出的数据密钥包装起来，旧 master 仍然能重新派生它们，因此不算撤销旧钥。已有 legacy 数据需要先保留原卷，再复制到独立随机数据密钥的新 wrapped 卷；当前实现不提供静默原地迁移。

包装使用已有 RFC 5297 AES-SIV 和 RFC 5869 HKDF-SHA256。112 字节记录认证了版本、provider id、非零 generation 和保留字段。generation 是认证字段，但不是可信单调计数器：攻击者若同时保留旧 slot 和旧 master，仍可恢复不变的数据密钥。新主密钥不能撤回攻击者已经取得的数据密钥或历史材料。可信防回滚需要额外受保护单调状态。

模拟器后端通过 hostfs/9p 原生 rename 替换正式槽，绕开 generic VFS 先删旧目标的行为，不更改全局 hostfs 语义。单 writer 下的文件比较与 rename 用于模拟器进程中断模型；不承诺跨宿主进程原子 CAS、恶意宿主防护或真实介质断电持久性。真实 provider 必须自行满足原子、持久、失败保旧的 compare-and-replace 契约，且不得把 bootstrap 资料放到 `/data` 或隐藏下层 `/datap` 内。

## 宿主独立检查

```bash
python3 nuttx/fs/encfs/tests/test_keywrap.py
python3 nuttx/fs/encfs/tests/test_keywrap_adversarial.py
python3 apps/system/encfs/tests/test_rekey_cli.py
python3 apps/system/encfs/tests/test_testkey.py
bash nuttx/tools/tests/encfs_manager/run_host_tests.sh
```

这些测试编译实际 C 源码，验证 AES-SIV/HKDF 独立 OpenSSL 参照、每个 slot bit 的篡改拒绝、失败输出清理、generation 溢出、legacy 拒绝、临时槽边界和命令参数。宿主测试不能替代真实模拟器 open/dup/DIR 和并发 I/O 验收。
