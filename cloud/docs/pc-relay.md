# 电脑中继协议

本功能默认关闭，仅接入兼容语音管线（DeepSeek 等）。启用前在云端私有环境中配置：

```dotenv
STACKCHAN_PC_TOKEN=
STACKCHAN_PC_ID=desktop
STACKCHAN_PC_DEVICE_IDS=
```

Token 必须包含三十二到四千零九十六个非空白字符，请在本机生成随机值，勿提交到
仓库。设备白名单使用机器人建立语音 WebSocket 时的 `Device-Id`，多个值用逗号隔开。
电脑 ID 和 Token 应与 Windows 助手私有配置一致。缺少有效 Token、设备白名单或
电脑 ID 时中继返回服务不可用；未授权机器人不会获得电脑工具或能力清单。

电脑主动连接 `wss://你的域名/dania/pc/ws`，使用 `Authorization: Bearer <token>`。
云端只允许每个已配置电脑 ID 一个连接，拒绝新连接抢占。HTTP 层必须使用有效 TLS，
Token 不放 URL 中。现有 `tools/render-gateway.py` 的私有输入配置可增加
`"pc_relay_enabled": true` 来生成这条可选反向代理路由；生成文件不会部署或重启服务。
独立 Compose 已透传上述三项环境变量。服务直接运行时环境变量在进程启动时读取。

首条消息（能力列表只包含本机允许的项目，不携带路径或可执行文件参数）：

```json
{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[{"id":"browser","name":"浏览器"}],"projects":[],"actions":["open_app","set_volume","media","search_web","lock"]}}
```

服务回复 `{"type":"ready","version":1}` 后才允许调用。握手五秒超时，空闲连接使用
Ping/Pong 心跳，四十五秒未收到 Pong 即断开。消息仅限 UTF-8 JSON 文本，单条最大
十六 KiB，不允许重复字段、未知字段或无效能力。同组 ID、动作名不得重复，应用和
项目列表各最多六十四项，ID 为一到六十四位字母、数字、下划线或连字符，名称最多
一百个字符。

服务发送：

```json
{"type":"call","id":"随机请求标识","action":"set_volume","args":{"value":35},"deadline_ms":1893456000000}
```

`deadline_ms` 是 Unix 毫秒时间戳，距离发送时间最多十五秒。最多四个待处理请求，
忙碌或离线时直接报错。电脑校验截止时间、会话是否仍有效和本机白名单后才执行。
不存在离线队列、自动重试或重连后重放。

电脑回复：

```json
{"type":"result","id":"原请求标识","ok":true,"message":"Computer volume changed","data":{"value":35}}
```

模拟运行必须返回 `data.simulated: true`，表示没有操作桌面。模型接收完整结果，
不得把模拟或“已发送请求”当成已经完成桌面动作。失败回执保留 `ok: false`。超时、
中断或断线只能说明执行结果未知，服务不会自动补发。迟到结果不会被当作成功：
如果对应请求仍在等待，服务返回超时错误并保持连接；如果已没有匹配的待处理请求，
则与非本连接标识、重复或未请求的结果一样关闭连接，使其他待处理请求失败。
格式错误的结果也会关闭连接。

模型工具只从当前连接的动作和 ID 生成，调用时再次校验设备授权及实际能力：

| 工具 | 参数 |
| --- | --- |
| `pc_open_app` | `id`，必须在已连接电脑的应用白名单内 |
| `pc_open_project` | `id`，必须在已连接电脑的项目白名单内 |
| `pc_set_volume` | 整数 `value`，零到一百 |
| `pc_media` | `command`：`play_pause`、`next`、`previous` |
| `pc_search_web` | 可打印 `query`，去首尾空白后最多三百个字符；只打开搜索页 |
| `pc_lock` | 空对象 |

每轮对话最多四个不同电脑调用；完全相同的动作与参数复用本轮回执，不重复发送。
尤其是媒体暂停/继续属于切换操作，不应为了确认状态再次调用。机器人表情/姿态
仍独立最多三次，不影响电脑预算。电脑工具的参数、搜索词及回执不写入工具日志；
已有语音转写和对话记录功能按其原有配置管理。

当前代码提供协议与自动化验证，真实电脑控制需完成私有部署及实机验收。没有新增
任意命令、Shell、远程路径、文件删除、消息发送、关机或屏幕读取功能。
