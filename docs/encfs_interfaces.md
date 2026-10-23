# EncFS 实际接口与磁盘格式

本文描述当前第二层、第三层源码的接口。源码路径均相对于 `repo sync` 后的 openvela 工作区根目录。交付同时保存 `patches/nuttx/encfs.patch`、`patches/vendor_openvela/encfs.patch` 和 `overlays/apps/`，分别重现内核、板级及新增应用变化；不依赖本次内核修改已成功推送远端。`encfs-sources.json` 记录两个补丁的原始基线与全部补丁/应用文件校验值。

首次使用执行 `python3 .repo/manifests/tools/apply_encfs.py`。脚本先预检所有补丁和覆盖文件，成功应用后才创建根目录 `apply-encfs.sh`；后续可使用该快捷入口幂等重试。不要把首次尚不存在的快捷链接作为唯一安装入口。

## 三层关系与固定卷

```text
应用 / NSH
    │ 逻辑文件名、明文内容
    ▼
/data                 encpathfs：AES-SIV 认证文件名
    │ 保留的 lower inode，直接转发下层文件操作
    ▼
隐藏的 /datap         FatFs 管理的 exFAT 卷
    │ 明文扇区
    ▼
/dev/encdata0          blkcrypt：XTS-AES-128 扇区加密
    │ 密文扇区
    ▼
/dev/virtblk1          模拟器实际 vela_data.bin
```

第三层只管理这一个卷、一个名称密钥槽 `slot=0`。上层挂载成功后，`/datap` 从公共 inode 树移除，名称层持有并接管原 FatFs inode。其文件操作使用该 inode 及操作表，不重新查找 `/datap/...`。这避免通过普通下层挂载路径绕过名称层；它不构成对特权代码或直接原始设备访问的安全隔离。

当前面向 flat 构建，内置应用直接调用内核服务。protected/kernel 构建的系统调用通路不在本版范围内。

## 密钥来源与派生

`nuttx/fs/encfs/encfs_key.c` 获取并派生密钥：

```text
master   = board_uniquekey() 的 32 字节输出
blk_key  = HKDF-SHA256(master, salt=ASCII("blkcrypt"),  info=空, L=32)
path_key = HKDF-SHA256(master, salt=ASCII("encpathfs"), info=空, L=32)
```

两个 salt 分别为 8、9 字节，均不含结尾 NUL。没有从短口令直接构造主密钥的接口。`CONFIG_BOARDCTL_UNIQUEKEY_SIZE` 必须为 32。临时 master、派生输出和密码中间缓冲区在相应使用结束后擦除；运行实例只保留操作所需上下文。密钥不进入挂载选项、状态结构或程序主动打印的状态输出。测试 CLI 的参数仍可能被终端回显或保存到历史，详见下文。

QEMU 板级 `vendor/openvela/boards/vela/src/qemu_vela.c` 返回固定的 32 字节伪密钥。它用于课程演示和可复现测试，不是设备硬件秘密或生产密钥管理方案。测试配置还打开了可注入主密钥的 `CONFIG_ENCFS_TESTING`，生产构建应关闭该选项，并另行提供可信密钥来源。

密码接口以以下头文件为准，均返回 `0` 或负 errno：

| 头文件 | 接口与约束 |
| --- | --- |
| `nuttx/include/nuttx/crypto/hkdf.h` | `hkdf_sha256(ikm, ikmlen, salt, saltlen, info, infolen, out, outlen)`；一般输出长度上限 `255 * 32` 字节 |
| `nuttx/include/nuttx/crypto/aes_siv.h` | `aes_siv_encrypt(key, ad, adlen, plain, plainlen, sealed)`、`aes_siv_decrypt(...)`；32 字节密钥、16 字节 SIV、一个关联数据字符串；认证失败返回 `-EBADMSG` 并清输出明文 |

第一层使用 32 字节 XTS 总密钥，即两把 AES-128 密钥，不是单把 AES-256。文件名层的 32 字节 AES-SIV 密钥也由两把 AES-128 密钥组成。

## 第一层适配

真实接口位于 `nuttx/include/nuttx/crypto/blkcrypt.h`：

```c
int blkcrypt_register(const char *encdev, const char *parent);
int blkcrypt_set_key(const char *encdev, const uint8_t *key, size_t keylen);
int blkcrypt_unregister(const char *encdev);
```

第三层 `encfs_block_port.c` 总是显式传入 `/dev/encdata0`，不能按旧草案省略 `set_key` 的设备参数。密钥长度固定为 32，两半相同的 XTS 密钥被拒绝。没有安装密钥时，设备读写被拒绝；文件系统挂载前先装入块密钥。卸载文件系统并释放设备引用后才能注销；忙时返回 `-EBUSY`。运行中的密钥替换被管理器拒绝，第一层也检查已打开设备的换钥情况。

## 名称层公共接口

头文件：`nuttx/include/nuttx/fs/encpathfs.h`。

```c
int encpathfs_setkey(unsigned int slot, const uint8_t *key, size_t keylen);
int encpathfs_clearkey(unsigned int slot);
int encpathfs_initialize(const char *lower, const uint8_t *key, size_t keylen);
int encpathfs_recover(void);
int encpathfs_encode_name(const uint8_t *key, const char *name,
                         char *encoded, size_t capacity);
int encpathfs_decode_name(const uint8_t *key, const char *encoded,
                         char *name, size_t capacity);
```

所有接口返回 `0` 或负 errno。`setkey` 复制 32 字节密钥至暂存槽，仅接受 `slot=0`。bind 取得密钥后消费并擦除暂存槽；挂载中、挂载有效期间或元数据恢复未完成时拒绝换钥。`capacity` 包括字符串末尾 NUL。

正常挂载固定使用：

```c
mount(NULL, "/data", "encpathfs", 0,
      "lower=/datap,root=.p,slot=0");
```

内核服务使用返回负 errno 的 `nx_mount`，应用使用 POSIX `mount`，后者失败返回 `-1` 并设置 errno。挂载选项只接受当前约定的顺序和内容；不要在其中传递密钥。

下层必须是当前 FatFs 实现的独立挂载点，支持所需文件/目录操作及 `syncfs`，文件名能力至少 234 字节。已存在下层打开句柄、子 inode 或启用路径缓存时，名称层拒绝接管。初始化只接受空下层卷，不覆盖已有文件、已有元数据或损坏的旧元数据。

`recover` 重试元数据关闭失败后保留的临时文件及初始化回滚。恢复失败保留下层 inode、句柄和忙状态，供管理器之后重试；它不替代有效上层卷的正常 unbind。

## 名称及卷格式 v1

逻辑名称限定为 1–128 字节可打印 ASCII（`0x20..0x7e`），不包含 `/`；普通分量不能为 `.` 或 `..`。不支持中文 UTF-8 名称。路径中的结构性 `.`、`..` 由路径转换器处理，拒绝逃出逻辑根；它还检查缺失/非目录中间项、尾部斜线和编码后完整路径长度。超限返回 `-ENAMETOOLONG`，没有长名称哈希回退。

```text
名称 AD：ASCII("encpathfs:name:v1")，不含 NUL
名称密文：SIV[16] || ciphertext[n]
物理名称："E1_" || BASE32HEX_UPPER_NO_PADDING(名称密文)
编码字母：0123456789ABCDEFGHIJKLMNOPQRSTUV
物理长度：3 + ceil(8 * (16 + n) / 5)
```

128 字节逻辑名得到 234 字节物理名。解码器校验前缀、字符集、规范长度、末尾未使用位和 AES-SIV 认证；损坏的物理项不会作为明文名称返回。`statfs` 的 `f_namelen` 报告逻辑限制 128。

每个路径分量独立编码，关联数据不包含父路径。因此目录整体移动无需重编码子树；名称层本身保留名称长度、目录结构和同名相等性。

下层布局：

```text
FatFs 根目录
├── .encfs.meta        51 字节认证卷记录
└── .p/                逻辑根，子项全部为 E1_ 编码名称
    └── E1_.../
        └── E1_...
```

`.encfs.meta` 使用字节序列化，不直接写 C 结构体：

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 8 | ASCII `ENCPFS01` |
| 8 | 2 | 小端版本号 1 |
| 10 | 2 | 小端编码类型 1 |
| 12 | 2 | 小端逻辑名上限 128 |
| 14 | 2 | 保留值 0 |
| 16 | 16 | AES-SIV 认证标签 |
| 32 | 19 | 固定校验明文 `encpathfs-volume-v1` 的密文 |

卷认证 AD 是不含 NUL 的 `encpathfs:volume:v1` 紧接前 16 字节卷头；密钥为 `path_key`。初始化写入、同步并重新读取验证。正常 bind 只验证已有卷头、认证记录及 `.p`，缺失或版本不匹配均拒绝挂载。元数据不保存主密钥或派生密钥。

## 第三层生命周期接口

头文件：`nuttx/include/nuttx/fs/encfs.h`。

```c
int encfs_start(void);
int encfs_stop(void);
int encfs_provision(void);
int encfs_get_status(struct encfs_status_s *status);
```

| 接口 | 实际行为 |
| --- | --- |
| `start` | 预留并确认 `/data` 伪目录；注册块设备、派生/安装密钥；以普通 `fatfs` 选项挂载；设置名称槽、验证并挂载 upper。绝不格式化、不补建卷头。READY 时重复调用返回 0 |
| `stop` | 先卸载 upper（其 unbind 同时释放隐藏 FatFs），再注销块设备。打开文件/目录或未完成同步时失败并保留有效资源。STOPPED 时重复调用返回 0 |
| `provision` | 破坏性操作：通过加密设备以 `forceformat` 建立 exFAT，创建并验证卷元数据，卸载并注销，成功后留下 STOPPED。只允许无残留资源时调用 |
| `get_status` | 返回串行化的状态副本，不含密钥；空指针返回 `-EINVAL` |

NSH 的破坏性入口必须写出 `encfs provision --erase`；仅 `encfs provision` 不执行格式化。普通启动脚本只调用 `encfs start`，加密配置关闭普通 FAT、TEE 和 tmpfs 的 `/data` 回退分支。

资源对应状态为 `STOPPED → BLOCK_REGISTERED → BLOCK_KEYED → LOWER_MOUNTED → READY`。`encfs_status_s` 同时提供 `block_registered`、`block_keyed`、`lower_visible`、`upper_mounted`、`path_key_staged`，判断异常状态应结合这些真实资源标记，不能只读错误码。管理器 mutex 串行化所有服务及测试密钥操作。

`stage/error` 保存原始失败，`cleanup_stage/cleanup_error` 保存随后回滚失败。逆序清理在失败处停止，保留其依赖；例如下层卸载失败时不得先注销块设备。元数据异常关闭先调用 `encpathfs_recover()`。忙时 stop 保持 READY，释放句柄后可重试；有残留资源时 start/provision/换钥返回 `-EBUSY`，先重试 stop。重复 READY start 不重跑挂载，也可能保留上一次忙时 stop 的诊断，应同时查看本次命令的 `result`。

管理器以 inode 序号、操作表和私有上下文记录挂载身份。`start`、`stop` 和状态查询核对实际挂载；外部直接卸载 `/data` 后，状态降为 `BLOCK_KEYED`，`start` 返回 `-EBUSY`，先 `encfs stop` 清理后再启动。核对及卸载受同一 inode 树锁保护；管理器不卸载同路径的替代挂载，也不长期保留会阻止普通卸载的 inode 引用。

属性操作沿用当前 FatFs 的能力：普通权限位经 `chmod/fchmod` 接受，但不持久化，`stat` 报告 `0777`；属主修改返回 `EPERM`。mtime 经路径或打开句柄修改，以 FAT 的两秒精度保存。这不等于完整 POSIX 权限控制。

错误阶段枚举依次为 `NONE(0)`、`REGISTER(1)`、`MASTER(2)`、`DERIVE(3)`、`BLOCK_KEY(4)`、`LOWER_MOUNT(5)`、`FORMAT(6)`、`PATH_KEY(7)`、`UPPER_MOUNT(8)`、`INITIALIZE(9)`、`UPPER_UMOUNT(10)`、`LOWER_UMOUNT(11)`、`UNREGISTER(12)`、`PREPARE_UPPER(13)`。`PREPARE_UPPER` 实际先执行，追加枚举值是为了保持既有值不变。

## 测试专用接口

仅 `CONFIG_ENCFS_TESTING=y` 时编译：

```c
int encfs_test_set_master(const uint8_t *key, size_t keylen);
int encfs_test_use_board_key(void);
int encfs_test_set_failpoint(enum encfs_test_failpoint_e failpoint);
```

`set_master(NULL, 0)` 模拟缺少主密钥；非空密钥长度必须为 32。恢复板级来源或更换测试主密钥都会擦除旧测试材料，运行中及有残留资源时被拒绝。

### NSH 手工输入测试主密钥

内置命令 `encfs` 在 `CONFIG_ENCFS_TESTING=y` 时增加以下入口；关闭该选项后不编译此入口，也不在帮助中列出：

```text
encfs testkey <64hex>
encfs testkey board
encfs testkey none
```

`<64hex>` 必须是恰好 64 个十六进制字符，大小写均可，不带 `0x`、空格或分隔符，解析为 32 字节 master。长度或字符错误返回 `-EINVAL`，不改变此前选择的来源。`board` 恢复 `board_uniquekey()` 来源；`none` 模拟无主密钥，随后 `encfs start` 应失败。它们都不修改磁盘密钥或重加密已有文件，不能当作在线换钥功能。

执行前先 `encfs stop`，确认 `state=STOPPED` 且没有残留块设备、挂载或待恢复资源；READY 或有残留资源时，三个入口均返回 `-EBUSY`，保持已有卷有效。成功时只打印来源已改变，不打印主密钥；CLI 每条返回路径都会擦除其解析缓冲。测试来源保存在 RAM，冷启动后恢复板级默认来源，手工测试卷需要重新输入原测试主密钥才能挂载。

以下是公开测试夹具 A，任何人均可知道，不提供保密性。首次初始化步骤只可用于独立的新空白测试盘：

```text
encfs stop
encfs testkey 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
encfs provision --erase
encfs start
```

可用公开夹具 B `202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f` 测试错误密钥：先 stop，选择 B，再 start，应拒绝 A 初始化的卷；随后 stop、重新选择 A、start，应仍可读回原数据。此拒绝路径绝不执行 provision。只有显式 `encfs provision --erase` 才会破坏性重建卷；不要用它消除错误密钥报错。

NSH 通过命令参数接收测试材料，终端、串口日志及命令历史可能保留整条输入。此命令只用于上述公开测试夹具，不能输入生产密钥；擦除 CLI 的局部缓冲不等于删除终端或历史中的副本。

### 定时并发文件系统压力入口

```text
encfs_test stress <seconds:60..86400>
```

此入口要求已 provision 的完整三层卷，调用 `encfs_start()` 后在两个线程中持续执行真实文件操作。它不格式化、不更换主密钥、不卸载卷，也不移除任意既有目录树。每个线程独占一个临时文件及两个工作目录，完成创建、分块写入、跨扇区偏移覆盖、fsync、打开句柄完整校验、rename、重开完整校验、逻辑目录遍历和删除。文件长度最大 65537 字节，两个线程合计最多保留 131074 字节文件内容；目录名包含 PID 和单调时钟，防止覆盖无关样本。

测试数据由固定种子 `0x6e637273`、线程编号和轮次生成，可独立复算预期字节。两个工作线程各自使用单调时钟测满请求时长，报告线程每 30 秒输出 `ENCFS_LONG_STRESS_PROGRESS`，包含两线程轮数、写入/校验字节和堆使用量。最终 `ENCFS_LONG_STRESS_RESULT` 只有在两个线程均运行足够时长、均完成操作、无错误且清理成功时给出 `completed=1 errors=0`；还须出现 `ENCFS_TEST_RESULT mode=stress pass=1 fail=0`。堆统计是观测数据，不单独证明不存在内存泄漏。

完整 GUI 回归通过 `fbcapture` 读取真实 Guest 帧缓冲；本机得到 1280×800 横屏，覆盖模拟器配置中的 720×1280 声明。当前 `CONFIG_GOLDFISH_GPU_FB_RGB565=y`，原工具保存的 BMP 使用 `BI_RGB` 标记，通用读取器可能误判为 RGB555。runner 保留原 BMP，并依据实际 RGB565 格式以 Pillow raw `BGR;16` 解码导出 PNG，供画面和色彩复核。widgets 的 Profile、Analytics、Shop 点击坐标分别约为 `(740,35)`、`(950,35)`、`(1160,35)`；复现流程及截图判据见 `encfs_test_plan.md`。

failpoint 为 `NONE`、`REGISTER`、`KEY`、`LOWER`、`UPPER`。到达对应操作之前返回一次 `-EIO`，随后自动解除；未到达该阶段不会提前消费。故障发生在真实操作之前，用来验证管理器回滚和重试，不等于覆盖全部底层 I/O 失败。宿主生命周期测试另以外部依赖替身覆盖清理失败和异常发布路径。

## 支持范围与边界

支持普通文件读写、定位、同步、truncate、dup、stat/chstat、目录遍历和 rewind、mkdir/rmdir/unlink、同卷移动及目录重命名。目标已存在的 rename 明确返回 `-EEXIST`，不承诺 POSIX 替换语义。只允许 `FIOC_FILEPATH` 查询逻辑路径，其他 ioctl 返回 `-ENOTTY`。软链接、硬链接、mmap、跨卷 rename 和强制撤销打开句柄不在支持范围内。

第一层 XTS 不认证文件内容或任意扇区，卷元数据及文件名的 SIV 认证不能代替数据完整性保护。没有声称抵抗内容篡改、回滚攻击、断电原子性、在线密钥轮换或生产级攻击。干净卸载、错误路径回滚及同盘重启是本版可复现测试目标。

板级加密配置生成新的 256 MiB 空白数据镜像，不预装明文 FAT 或预置文件。格式化不表示逐扇区安全擦除，未写入空白区也不被描述为已加密。需要迁移的数据应在新卷 provision 后通过 `/data` 写入；不要把含明文旧数据的镜像直接当作已安全加密的交付盘。
