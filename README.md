# StackChan 达妮娅

基于 M5Stack StackChan 的中文具身陪伴机器人项目：DeepSeek 语音对话、傲娇角色、表情和摆头、自定义录音，以及可以和 AI Agent 同时使用的电脑蓝牙控制台。

这是个人定制版本，保留固件、云端服务、Windows 电脑助手与本机 Home Assistant 桥接源码。旧独立供电、触摸与开机键问题已由用户确认修复。新功能的自动检查与人工验收分别记录，详见[验证记录](docs/verification.md)。

十一月十六日更新：关闭记事本后连续两次用相同口令重新打开，已获用户确认。聊天音频候选版也已确认连续流畅，但仍有一次未导致重启的 AFE 看门狗告警，保留在[草稿 PR #1](https://github.com/itknow666/stackchan/pull/1)，尚未合入主分支。下一步设计见[智能化方案](docs/intelligence-plan.md)。

## 已实现

- AI Agent 内同时运行 Wi-Fi 对话和 BLE 控制，无须切换到 DANCE。
- 开机直接进入 Agent；独立娱乐应用不编入固件，底部上滑进入维护页，保留配网、音量、电源和返回 Agent。
- SD 存放录音与校验清单：摸头 SD 优先，固定场景在资源包匹配时本地播放，资源不可用时保留嵌入或云端回退。
- 语音调用电脑的指定软件、项目文件夹、音量、媒体、网页搜索和锁屏；电脑助手主动加密连接，按允许清单执行并返回结果。
- DeepSeek 对话、语音识别和语音合成适配；特定整句优先播放录音，其他问题继续交给模型。
- 角色提示词与真实的 `set_emotion`、`set_pose` 工具联动，支持八种情绪；当前硬件只有两个舵机，roll 必须为零。
- 待机摸头随机播放十条本地录音，六秒冷却且不连续重复；私人录音需要自行导入。
- 进入 Agent 时跳过自动固件和资源检查，使用已保存的 WebSocket 配置。
- Home Assistant 控制音量、转头、脸部和灯光；手动控制短暂优先，音量支持静音、设备确认和重启保存。
- 调整音频解码优先级和网络预缓冲，改善蓝牙共存时的卡音。

## 源码结构

| 目录 | 内容 |
| --- | --- |
| `firmware/` | ESP32-S3 固件、固定版本依赖补丁、主机单元测试 |
| `cloud/` | 基于 stackchan-ai-server 的完整服务源码和上游说明 |
| `home-assistant/` | Windows BLE/MQTT 桥接、仪表盘和专用 WSL 部署脚本 |
| `pc-assistant/` | Windows 反向语音控制执行器、状态和登录启动项 |
| `examples/` | 角色提示词、录音清单与连接配置示例 |
| `tools/` | 音频转换、依赖修补和网关配置生成工具 |
| `docs/` | 架构、部署、验证范围和后续计划 |

先阅读[部署说明](docs/setup.md)和[架构说明](docs/architecture.md)。原有运行目录与本仓库分离，整理源码不会改变现用服务或机器人配置。

## 快速验证

```sh
python3 firmware/tests/fetch_host_dependency.py /tmp/stackchan-host-arduinojson
cmake -S firmware/tests -B build-host-tests -DARDUINOJSON_INCLUDE_DIR=/tmp/stackchan-host-arduinojson/src
cmake --build build-host-tests
ctest --test-dir build-host-tests --output-on-failure
```

Windows 本机控制台的独立测试：

```powershell
python -m pip install -r home-assistant/requirements.txt
python home-assistant/test-bridge.py
```

云端服务在 Go 一点二六及安装 libopus 开发依赖后，在 `cloud/stackchan-server/server` 目录运行 `go test ./...` 和 `go build ./...`。CI 分别验证固件逻辑、桥接契约和 Go 服务。

## 分享与许可证

仓库不包含 API Key、SSH 私钥、设备地址和 NVS 备份，也不包含私人达妮娅录音或含这些录音的固件二进制。请使用自己拥有使用权的声音，按[音频导入说明](docs/audio.md)制作语音包。

云端源自 AGPL-3.0 项目，固件主体保留 MIT 许可，第三方文件保留原许可证；新增桥接和工具以 AGPL-3.0-or-later 提供。不要将整个仓库统一理解为 MIT，详见 [NOTICE](NOTICE.md) 与[上游来源](docs/upstream.md)。
