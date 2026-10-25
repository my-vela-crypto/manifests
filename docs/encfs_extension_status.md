# 第二、三层扩展工作（进行中）

用户于 2026-10-24 要求在已交付首版上继续，视频仍排除，并明确只使用模拟器。原 `encfs-final-20261024` 是已验收首版，不能将下面未完成事项写成其既有测试结果。

## 当前状态（2026-10-25）

- 中文及严格 UTF-8 文件名已实现并通过完整模拟器存储回归，保持 ASCII 磁盘编码兼容及 128 字节逻辑名上限。
- 包装卷密钥及在线主密钥轮换已实现。新卷使用独立随机数据密钥；旧版直接派生卷可读，拒绝用包装旧派生密钥冒充撤销旧主密钥。后端明确为卷外测试文件，不等同于真实 TEE。
- 专用 storage 最终固件：`encfs-results/extended-20261024/storage-final-01/report.json`，789/0，六次开机；三次冷启动含 UTF-8 文件读回。
- 最终 extended 固件：`encfs-results/extended-20261024/acceptance-final-01/report.json`，原始 CMocka 46/0/0skip、fstest 10 轮（20/0）、原 Vela 100 轮、20 次 SIGKILL 中断及重启读回、单字节同长度损坏拒绝。无 RTC 时明确设置有效 FAT 测试时钟，不将模拟器切断称为真实硬件断电。
- 源码交付：249/249 隔离安装、幂等与冲突零写入验证，25 个 apps overlays；锁定 manifest 仅更新 nuttx，其他231个项目保持不变。
- GUI正式复验`encfs-results/gui-extension-20261025-02/`于11:12:04–11:15:39完成：独立父wrapper观察Python返回0，七次启动、四demo、Keypad真实触控、GUI下write232/0、verify8/0及60秒452事务/0错误均通过，独立分析校验28PNG/4页签头。`extension-20261025/gui-wrapper-rc.json`保存真正wait结果。-01保留为历史：原断言及分析通过，但工具外层session55112返回143，不能把它说成Python返回0。
- 在线换钥 60 秒探索专项 `keywrap-60-20261024-02` 的持有 open/dup/DIR 27/0、提交前 SIGKILL、恢复及最终 generation4 独立冷启动已通过；但其换钥后的后续双线程进度证据不充分，保留为限定证据。正式180秒专项`keywrap-180-20261025-02`于10/25 11:05:56–11:09:22完成，1396事务、0错误，真实runner退出0，独立216检查通过。成功ACK后90.623秒样本[329,371]到120.684秒[439,491]两worker都增长，证明换钥后继续真实I/O；helper27/0、main28/0、精确提交前切断/恢复、仅currentmaster可用的gen4新冷启动都通过。旧结果不覆盖。
- 两轮24小时 GUI 并发压力均仍在进行，分别记录首版与新版，均需测满86,400秒并完成后续冷启动和独立分析。

## 长期任务入口

工作区：`/home/tourist/Desktop/openvela`。

`encfs-results/endurance-24h-baseline-20261024/` 保存冻结的首版 GUI 固件和脚本、输入哈希、启动进程及日志。`run/report.json` 是实时报告，`launcher.log` 是宿主日志。此轮仅证明首版固件的长期行为，不证明尚未编译的 UTF-8 / 包装密钥扩展。

首版源码与成品包已提交，不覆盖旧证据。扩展须实际编译、模拟器回归及同盘恢复后另行发布。测试失败必须保留原始结果，新尝试使用新目录。

`encfs-results/endurance-24h-wrapped-20261024/` 是冻结的最终扩展固件（UTF-8/keywrap）及新版工具。此前三次启动审批服务超时，**没有执行**；直到2026-10-25 11:03才成功启动，压力主体约11:03:30开始，预计10月26日11:04后才结束。`process.json`、`run/report.json` 与 `launcher.log` 是实际入口，`supervise.py` 会在结束时写 `runner-exit.json`。该脚本在工具会话50796运行，不要重复启动。必须用独立审计确认换钥成功ack后两个进度样本间双worker继续增长；冻结脚本旧判据不单独作为充分证明。冻结输入不能修改。

用户已明确授权每小时自动跟进，应用内 heartbeat `encfs-24` 已创建并启用；全部扩展和长期验收收尾后应停止。正常推进无实质变化时不重复通知。长期压力约在 2026-10-24 14:15:16 +08 进入主体，至少持续到次日同一时间，仍须通过随后冷启动与独立分析。确切起点以实时 report/原始串口为准。

最终源：nuttx `04264dc9a7`、apps `e198d0a1a`、vendor `f08e648`。宿主严格 UTF-8 独立 oracle 1,237,942 项通过，包含与首版逐字节相同的224个ASCII密文；证据 `encfs-results/extension-20261024/utf8/host-report.json`。包装密钥/OpenSSL、独立对抗、CLI和旧manager回归证据见 `keywrap-implementation/report.json`。

nuttx扩展已正常快进推送团队dev（7934920→04264dc，未强推），凭证日志`encfs-results/publication-20261025/nuttx-push.log`。manifest源码、已完成测试的轻量证据和工具已发布团队dev：`31aef0eba7953d0185bc94b2f95f9da4fedfe31c`，日志`manifests-implementation-push.log`。当前公开证据位于`validation/encfs-extension-20261025`，不含私有master、slot或大盘；两24h仅观察元数据。仍需实际完成两24h、更新最终报告和成品包，不能把当前源码发布说成全部交付完成。

2026-10-25 11:06当前首版监测快照 `encfs-results/extension-20261025/baseline-monitor.json`：75,038.344秒、525,125事务、0错误；2498个原始进度、2451个宿主检查点、509张PNG及492个所选页签头核验正常，冻结输入未变。此处是运行中快照，绝不是24小时通过结论。宿主检查点与guest进度采样独立，不能要求两者都恰为2880。

## 后续收尾

1. 独立复核扩展代码及真正 guest 测试，记录兼容性与未通过项。
2. 更新经过校验的 manifest 补丁和应用覆盖，重新验证隔离安装、幂等与冲突保护。
3. 长期测试完成后用冻结的 `analyze_encfs_stress.py` 解析原始串口与画面，不把尚在运行等同于通过。
4. 更新报告、固定源码版本、推送并构建新版本交付包，保留首版包。

收尾工具已准备：`tools/audit_encfs_keywrap.py`独立核对新wrapped原始进度、OpenSSL认证、完整帧和gen4冷启动；`tools/package_encfs_extended.py`先验证所有完成门槛，运行中会拒绝且不创建输出父目录。最终执行前须更新并提交报告及课程镜像、保持manifest干净，传入真实完整manifest SHA。示例（日期须不早于所有实际完成日）：

```bash
python3 encfs-results/endurance-24h-baseline-20261024/frozen-inputs/tools/analyze_encfs_stress.py --results-dir encfs-results/endurance-24h-baseline-20261024/run --output-dir encfs-results/endurance-24h-baseline-20261024/run/analysis
python3 .repo/manifests/tools/analyze_encfs_stress.py --results-dir encfs-results/endurance-24h-wrapped-20261024/run --output-dir encfs-results/endurance-24h-wrapped-20261024/run/analysis --phase gui-wrapped-key-stress
python3 .repo/manifests/tools/audit_encfs_keywrap.py --results-dir encfs-results/endurance-24h-wrapped-20261024/run --output-dir encfs-results/endurance-24h-wrapped-20261024/run/audit --frozen-input-root encfs-results/endurance-24h-wrapped-20261024 --require-post-ack-progress
ENCFS_FINAL_MANIFEST=$(git -C .repo/manifests rev-parse HEAD)
python3 .repo/manifests/tools/package_encfs_extended.py --workspace "$PWD" --release-date 20261026 --expected-manifest-commit "$ENCFS_FINAL_MANIFEST" --check-only
```

首版baseline须另用其冻结analyzer作完整独立分析；其最初是直接Popen，没有真实父wait退出码记录，不得补造0。打包器明确记录未知exit=null，以完整功能报告、原串口、严格分析、同盘冷启及结束日志作该轮功能验收；新版wrapped须有`runner-exit.json`真正wait返回0。移除`--check-only`才实际打包，默认输出新目录`高级操作系统/输出/encfs-layer23-extended-<日期>`及同名tar.gz，拒绝覆盖旧包。源码/原始报告/固件/最终盘/全部文件和压缩包都要核对，GitHub证据不得含私有master或slot；本地包才成套保留专用测试盘与其当前provider夹具。

视频无需制作；用户明确模拟器目标，无需再索要硬件。
