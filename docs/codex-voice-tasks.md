# Codex 语音任务与完成播报

此功能把机器人明确收到的“让 Codex……”请求交给本机专用工作会话。它不会自动接续当前 Codex 桌面聊天，也不会监听所有普通闲聊。主程序不在 ESP32 上运行，电脑必须开机、Codex 已安装并登录，PC relay 连接正常。

## 配置

参考 `examples/pc-assistant.codex.example.json`，使用本机实际 Codex `.exe` 和项目路径，保留现有端点与独立令牌。`codex_start`、`codex_status`、`codex_cancel` 是额外的显式允许动作；没有配置时旧操作不受影响。默认示例仍为 `dry_run: true`，模拟不会启动 Codex，也不会发出完成通知。

`codex.sandbox` 仅接受 `read-only` 或 `workspace-write`，默认只读。任务只能选择 `projects` 中的项目 ID，不能通过语音提供命令行、可执行文件、额外目录或任意路径。工作进程使用官方 `codex exec --json`、参数数组和标准输入，忽略用户全局配置，强制 `approval_policy=never`，不绕过沙箱。自动推送、部署、发布、购买、发送消息和破坏性操作不属于这条通道的授权，worker 指令要求准备可审阅结果后说明后续所需授权。

这是受约束的编程助手，`workspace-write` 可以修改项目文件；提示词不是完整的权限边界，沙箱和明确的项目配置才是文件写入边界。请先用只读任务验收。不要把配置、任务记录和通知去重文件放到被执行的项目目录或公开仓库。

Windows 上应配置完整安装目录中的 Codex CLI，保留同目录的 `code-mode-host` 等配套程序；只复制一个 `codex.exe` 可能导致模型工具无法运行。可选 `codex.windows_sandbox` 只接受 `mxc`、`elevated`、`unelevated`，用于选择本机已经准备好的 Windows 沙箱后端，不更改只读或项目写入权限，也不会跳过沙箱。未填写时使用 CLI 的平台默认值。部署前应对实际后端执行一次无害的只读沙箱检查；需要首次系统安装或授权时在本机完成，不自动修改全局 Codex 配置。

如果桌面版和命令行使用不同的登录目录，可以在 `codex` 配置中显式增加 `home`，填入桌面版实际使用的 Codex 数据目录。目录必须已经存在，且位于所有允许项目之外；助手只通过子进程环境变量 `CODEX_HOME` 传递目录，不复制或发送登录文件，也不把它放到命令行、状态或云端能力中。未设置时沿用启动助手时的环境与 CLI 默认目录。`--ignore-user-config` 仍保留，确保选择正确登录数据不等于继承全局危险权限配置。不要把此目录加入项目允许列表。

每次提交快速返回 `accepted` 与 `task_id`；它只表示接收，不表示完成。最长任务时间为配置的十秒到两小时，默认三十分钟。一次只有一个后台任务，忙时返回原任务 ID，不排队悄悄重放。请求正文最多四千字，结果摘要最多三千字。状态、取消使用任务 ID，也可用 `latest` 查询最近任务。

状态包括 `accepted`、`running`、`completed`、`failed`、`cancelled`、`timed_out`、`interrupted`。只有 CLI 正常退出、收到 `turn.completed`，并且最终结构化结果明确为 `outcome: completed` 才判定完成。固定结果 schema 保存在私有任务记录目录，通过 `--output-schema` 传给 CLI；结果必须只包含 `outcome` 与 `summary`。工具或权限阻止任务时必须返回 `blocked`，执行失败返回 `failed`，二者都映射为失败任务，不播放成功通知。普通文本、格式不符、缺少最终结果也不能判定完成。任务结果是 Codex 报告的结果，仍应核对其测试与限制，不代表用户已验收。取消不会自动撤销已经修改的文件。

后台任务不随正常云端断线重派或重启。恢复连接可继续查询原任务。任务记录保存在独立本地文件，最近一百项；会话 ID 按项目、真实路径、沙箱和有效登录目录隔离，后续任务使用明确会话 ID 继续，而不是 `--last`。助手重启把未完成任务标记为 `interrupted`，不会重试。Windows Job Object 在助手退出时关闭 worker 及后代；取消、超时也会关闭该作业，必要时按本次进程 PID 停止进程树。无法确认停止时禁止继续启动新任务，需在电脑上检查。

## 自动由机器人播报

在 Home Assistant 桥接本地配置中增加下面的部分。路径必须换成真实的本地绝对路径，状态文件应与 PC 助手 `state_file` 指向同一文件；去重文件应独立保存，不能与配置、状态或任务文件相同。

```json
{
  "pc_assistant": {"status_file": "C:/StackChan/private/status.json"},
  "codex_notifications": {
    "enabled": true,
    "ledger_file": "C:/StackChan/private/codex-notifications.json",
    "max_age_seconds": 120,
    "clips": {
      "completed": "done",
      "failed": "confused",
      "cancelled": "see_you",
      "timed_out": "confused",
      "interrupted": "confused"
    }
  }
}
```

`clips` 是已经存在于机器人 SD 资源包的录音 ID，不是电脑文件名。配置前试听，确保成功、失败和取消的文案符合实际含义。此处只是映射示例；可换成专门生成的 `job_done`、`job_failed`、`job_cancelled` 等录音，但须先导入有效 SD 包。没有配置某种终态就不播报该类事件。不会用成功录音报告失败。

连接检测使用两个事实：PC 助手最近十五秒更新的真实在线状态，以及电脑当前 BLE 已连接机器人。USB 数据线不是播报通道，充电连接也不等于 BLE 在线。离线或正在讲话时不会打断；收到固件明确的“尚未开始播放，不可用”结果后，最多在完成后两分钟内等下次待机重试。超过有效期的通知不在重连后突然补播。

通知在发送物理播放命令前，先把任务 ID 写入持久去重文件。已接受、播放完成、失败、连接中断或结果未知都不重播；只有明确的开始前不可用才允许重试。崩溃发生在写入去重和发命令之间可能漏掉通知，这是为避免重复播放作出的取舍。此版本播通用完成录音，不直接朗读可能包含路径和私密信息的完整工作摘要；对机器人说“查询 Codex 最新任务”获取结果。

状态投影只含任务 ID、项目 ID、状态和时间，不含提示词、摘要、路径或凭据。详细结果在本地任务文件，并通过已认证云端工具在用户查询时返回。

## 验收

先在模拟模式确认没有进程与通知，再使用已通过本机预检的沙箱配置，请求“让 Codex 检查机器人项目，只读文件，列出三个可以优化的地方”。应立即确认接收，稍后才能报告完成。接着查询状态、提交一个可取消任务并取消，确认三种状态和各自录音。让机器人回答普通问题时任务完成，通知应等待待机；断开 BLE 时完成不播放，恢复时仅在短有效期内播一次。重启桥接不重放已通知任务。

本机实际验证使用完整安装目录中的 CLI、桌面版当前登录目录、`windows_sandbox: elevated` 和 `sandbox: workspace-write`。任务本身明确要求只读 README，成功返回正确标题，项目文件没有变化。这里的“只读任务”描述任务要求，不表示启用了 `read-only` 沙箱：本机 `read-only` 模式下模型读文件工具受到执行策略阻止，尚未通过同样的端到端验证。生产配置采用只允许指定项目写入的 `workspace-write`，不能把这次验证宣传为只读沙箱已经可用。

单元测试不消耗模型额度，不启动真实 Codex：

```powershell
# 从仓库根目录，使用已安装相应依赖的 Python
$env:PYTHONPATH='pc-assistant'
python -m unittest discover -s pc-assistant/tests
python home-assistant/test-bridge.py
python home-assistant/test-codex-notifications.py
```

真实 Codex 任务会使用当前登录账号的正常模型额度。本次接入不创建额度监控或自动兑换逻辑。
