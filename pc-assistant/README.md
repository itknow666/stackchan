# 达妮娅电脑助手

电脑主动建立经过认证的加密连接，接收云端发来的有限操作，返回实际执行结果。没有电脑入站端口，没有远程命令行。此目录可以独立测试；云端还需要提供对应的 `/dania/pc/ws` 接口。

## 安装和配置

支持 Windows 桌面会话、Python 三点一二及以上。已在 Windows 的 Python 三点一四安装并验证 `websockets 16.1.1`、`pycaw 20260927`、`comtypes 1.5.0`。操作需要当前用户登录，电脑休眠、离线或关机时不会执行。

在仓库根目录打开 PowerShell：

```powershell
python -m venv pc-assistant/.venv
pc-assistant/.venv/Scripts/python.exe -m pip install -r pc-assistant/requirements.txt
Copy-Item examples/pc-assistant.example.json pc-assistant/local-config.json
```

编辑 `pc-assistant/local-config.json`：

- `endpoint`：实际云端地址，必须是 `wss://域名/dania/pc/ws`。不接受明文连接、URL 密码、查询参数或跳转。
- `token`：为电脑连接单独生成的随机密钥，至少三十二个字符，与云端一致。不要使用模型 API Key。示例的空值会拒绝启动。
- `pc_id`：电脑标识，默认 `desktop`，只接受字母、数字、下划线和连字符。
- `dry_run`：默认 `true`。结果明确标注模拟，所有桌面操作都不执行。验证链路之后，手动改为 `false` 才会执行。
- `actions`：明确开放的操作名。可以删除任意不需要的操作；关闭的操作不会被执行。
- `apps`：本机允许打开的软件，使用 `id`、`name`、`executable`、可选的固定 `args`。可执行文件必须为绝对路径的 `.exe`，不要配置命令解释器、脚本运行器或管理员工具。
- `projects`：本机允许打开的文件夹，使用 `id`、`name`、`path`，路径必须为绝对路径。此版用文件管理器打开；要使用编辑器，可另配置固定参数的软件快捷项。
- `state_file`：默认 `private/status.json`，相对于配置文件所在目录。保存在线状态、待处理数量、模拟状态、最后成功与否及 `capabilities` 中的软件与项目 ID/名称和允许动作，不记录密钥、搜索词、软件路径和操作参数。控制台仅读取此安全状态，十五秒未更新则显示离线。

例如将下面内容分别填入 `apps` 和 `projects` 数组，路径替换为本机实际路径：

```json
{"id":"editor","name":"编辑器","executable":"C:/Program Files/Editor/editor.exe","args":[]}
```

```json
{"id":"robot","name":"机器人项目","path":"C:/Projects/stackchan"}
```

配置文件 `local-config.json` 和 `private/` 已被仓库忽略。复制或分享文件前仍需检查内容。不要把密钥放进命令参数、日志、截图或公开仓库。密钥在进程内存和本机配置文件中使用，配置文件应仅供当前用户读取。

## 运行与随登录启动

```powershell
pc-assistant/.venv/Scripts/python.exe pc-assistant/run.py
```

前台运行时按 Ctrl 加 C 退出。退出会取消未完成的请求，丢弃待处理操作。没有离线任务队列，重连不补做操作。

确认配置正确后，可以安装当前用户的登录启动项，不需要管理员权限：

```powershell
pc-assistant/.venv/Scripts/python.exe pc-assistant/startup.py install
```

这个命令只创建当前用户 Startup 文件夹下的 `DaniaPcAssistant.vbs`，下次登录以隐藏窗口运行当前虚拟环境的 `pythonw.exe`；不会立即启动，不会安装系统服务，不会提升权限。路径包含中文和空格可用，启动命令只包含程序和配置文件路径。若系统禁用 Windows Script Host，需要手动运行前台命令。

```powershell
pc-assistant/.venv/Scripts/python.exe pc-assistant/startup.py remove
```

移除只影响下次登录。已运行的进程需要在任务管理器按命令行找到对应 `pc-assistant/run.py` 后结束；前台实例用 Ctrl 加 C。不要结束其他 Python 程序。移动仓库或虚拟环境后需重新安装启动项。目前一次只启动一个实例；云端应限制同一 `pc_id` 的连接归属。

## 操作及限制

| 操作 | 精确参数 | 结果的含义 |
|---|---|---|
| `open_app` | `{"id":"本地软件标识"}` | 固定程序启动请求已提交，不保证应用页面已加载 |
| `open_project` | `{"id":"本地项目标识"}` | 已存在的固定文件夹已请求打开 |
| `set_volume` | `{"value":0}` 到 `{"value":100}` | 调整 Windows 默认输出设备主音量，返回回读值；不调整机器人音量，也不自动取消系统静音 |
| `media` | `{"command":"play_pause"}`、`next`、`previous` | 已发送 Windows 媒体键，具体播放器是否响应取决于系统 |
| `search_web` | `{"query":"搜索内容"}` | 对搜索词进行 URL 编码后，在默认浏览器打开必应搜索，最多三百字符 |
| `lock` | `{}` | Windows 接受了锁屏请求，无法远程解锁 |

未知操作、额外参数、任意路径和任意命令均被拒绝。删除文件、发送消息和关机不在此版本能力范围内。锁屏会影响正在使用电脑的人，云端应在明确收到用户指令后才调用。

同一时刻最多四个待执行或执行中的请求，单工作协程顺序处理。到达时和真正执行前都检查有效期，最长十五秒，同时使用单调时钟防止系统校时延长排队。断线取消未执行操作；已经提交给 Windows 的动作无法撤回。不会重试物理操作。

最近一千零二十四个请求标识最多保留三十分钟，重连期间也保留。相同内容的重复请求返回已有结果，执行中的重复请求等待原结果；标识相同而内容不同明确拒绝。缓存满时拒绝新请求，不挤出仍在保护期内的标识。去重不持久化到磁盘；服务端必须使用不可复用的唯一请求标识，且不能重发过期动作或在重连后重新派发旧动作。

连接默认验证系统信任的 TLS 证书，不跟随重定向，不经过环境变量指定的代理。断线以一到三十秒指数退避并加入随机延时重连。客户端日志只报告通用连接失败，不记录请求正文和认证内容。

## 协议

握手 HTTP 请求头为 `Authorization: Bearer <token>`。每帧必须为不超过十六 KiB 的 JSON 文本。客户端首先发送：

```json
{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[],"projects":[],"actions":["lock"]}}
```

只有本机允许的操作和软件名称、标识会被公布，磁盘路径不会发送。服务端须在十秒内回应：

```json
{"type":"ready","version":1}
```

服务端请求：

```json
{"type":"call","id":"unique-id","action":"set_volume","args":{"value":40},"deadline_ms":1234567890000}
```

`deadline_ms` 为 UTC Unix 毫秒时间，必须晚于电脑当前时间且最多十五秒。真实部署应保持电脑和服务器自动校时。

客户端响应：

```json
{"type":"result","id":"unique-id","ok":true,"message":"Computer volume changed","data":{"value":40}}
```

模拟模式始终返回 `data.simulated: true`，`message` 明确包含 `Simulated`。云端应尊重这个标记，告诉用户当前是模拟结果。通信失败、超时、离线都不等同于执行成功。

## 验证

以下测试只使用模拟执行器和本机临时 WebSocket 端口，不会锁屏、调音量或打开软件：

```powershell
Set-Location pc-assistant
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

覆盖严格参数、认证选项、文本帧限制、队列上限、排队过期、执行前过期、系统时钟回退、重复标识、断线取消、握手、真实本机 WebSocket 往返和启动项生成。桌面操作需在用户清醒且确认配置后另行验收，模拟测试通过不代表实际电脑动作已验证。


## Codex 后台任务

可选的 `codex_start`、`codex_status`、`codex_cancel` 用于明确的语音编程请求，提交后快速返回，后台独立执行。配置和完成通知见 [Codex 语音任务](../docs/codex-voice-tasks.md) 及 [配置示例](../examples/pc-assistant.codex.example.json)。普通电脑动作仍使用原来的十五秒截止时间。Codex 长任务使用独立执行时限，云端断线不重新派发，助手重启不自动重试。
