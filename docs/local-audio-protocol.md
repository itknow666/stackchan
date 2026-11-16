# SD 场景录音协议第一版

使用已有机器人 WSS 连接，不开放新端口。协议只接受已验证清单的 ID，不接受文件路径。

有效 SD 包才在设备 Hello 增加顶层 `local_audio`：

```json
{"local_audio":{"version":1,"pack_id":"my-pack","clips":["01_hello","cute_01"]}}
```

最多六十四个唯一 ID。ID 和 pack_id 均为一至六十四个 ASCII 字母、数字、下划线或连字符。服务端只接受首次 Hello 的能力声明。云端原 PCM 清单增加 `pack_id` 和每条 rule 的 `id`；旧清单没有元数据时保留网络播放。

命中场景且包和 ID 匹配后，服务端先发送 `tts:start`，再发送：

```json
{"type":"local_audio","request_id":"random-unique-id","pack_id":"my-pack","clip_id":"01_hello","timeout_ms":20000}
```

设备复制请求并调度到应用主线程，额外等待一次主循环，让 Speaking 的解码重置和网络播放初始化先完成。文件大小、CRC、Ogg、时长预检及独占音频租约成功后回 `accepted`；解码队列和实际输出排空后才回 `completed`：

```json
{"type":"local_audio","request_id":"random-unique-id","state":"accepted"}
{"type":"local_audio","request_id":"random-unique-id","state":"completed"}
```

同一连接最多一个请求，截止二十秒。只有当前连接当前 request_id 的有效状态转换可完成请求。设备缓存每连接最多六十四个请求标识，满后明确拒绝本地播放，继续云端回退；重连清空。旧连接的延迟任务和工作回调不能发到新会话。

只有 `unavailable` 表示明确尚未开始，允许发送原 PCM。接收后失败为 `failed`，取消为 `cancelled`。发出请求后超时、断线或发送结果不确定都按中断收尾，不整段重播，避免回执丢失造成重复或混音。取消使用 `{"type":"local_audio_cancel","request_id":"..."}`，不能取消不同 ID 的新请求。原 `tts:stop` 和监听恢复仍完整执行，用户中断由原中断流程收尾。自由回答保持原云端 TTS。

## BLE 控制台试听

仍使用配置特征 UUID 后缀三，与音量、存储查询共用交换锁：

```json
{"cmd":"playLocalClip","id":"01_hello","request_id":101}
{"cmd":"getLocalClipStatus","request_id":101}
{"cmd":"cancelLocalClip","request_id":101}
```

回执 `{"cmd":"local_clip","request_id":101,"state":"pending"}`，状态可为 `pending`、`accepted`、`completed`、`unavailable`、`failed`、`cancelled`。状态查询按 ID 返回缓存结果，避免音量轮询覆盖通知。仅 Agent 待机且音频空闲时开始，不打断聊天；新网络回复可取消试听。桥接后台任务持有配置锁轮询，停止按钮设置取消信号，由该任务发送取消并等待实际终态。

`getStorageInfo` 返回 `cmd:storage`、`request_id`、`mounted`、`valid`、`pack_id`、`clips`、`total_mb`、`free_mb`、`error`，仅使用缓存，不重复扫描卡。旧固件无响应显示未知或不支持，蓝牙断开清除可用状态。MQTT 物理操作拒绝 retained 消息，启动和重连不自动试听。
