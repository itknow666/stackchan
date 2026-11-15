# 构建与部署

本仓库提供源码快照。请先核对硬件与分区；这里的定制目标是十六兆闪存、八兆 Quad PSRAM 的 ESP32-S3 StackChan。现用设备已经保存 Wi-Fi 和 WebSocket 接入配置，开源源码不包含这些配置。

## 固件

使用 ESP-IDF 六点一，在 Linux 或 WSL 环境构建。示例命令从仓库根目录开始：

```sh
cd firmware
python3 fetch_repos.py
idf.py reconfigure
python3 ../tools/apply-managed-patches.py
idf.py build
```

先加载 ESP-IDF 的 `export.sh`。`fetch_repos.py` 会固定 xiaozhi 提交并应用包含全部定制的补丁；不要另外重复应用旧的 StackChan 补丁。相机驱动补丁在托管依赖下载后执行，重复运行会验证补丁已存在。

默认启用 Quad PSRAM、单条 BLE 外设连接、UART 主控制台与 USB 辅助控制台。已有的 `sdkconfig` 优先于 defaults；从旧配置迁移时应逐项核对设置。不要按容量推断为 Octal PSRAM。

个人服务地址放入被忽略的 `firmware/sdkconfig.defaults.local`。`CONFIG_STACKCHAN_SERVER_URL` 是原 StackChan HAL 后端，`CONFIG_OTA_URL` 是启动接入地址，二者不能当作同一个接口。当前 Agent 跳过启动 OTA 检查，**实际语音连接使用 NVS 的 `websocket/url`、token、version**；仅修改 OTA URL 不会自动重新发现服务。初次接入需先完成原固件的配置/接入流程，或由理解本机 NVS 布局的人写入正确的 WebSocket 配置，再使用本固件。

刷写前备份本机完整闪存和分区表。公开仓库不提供个人备份；不要把别人的 NVS 写入自己的设备。上游 `cloud/flash_nvs.py` 会替换整块 NVS，不适合保留现有 Wi-Fi 和设备身份的直接升级。本次整理不执行任何刷写。

## 云端服务

云端有上游 Home Assistant add-on 和独立 Docker 两种运行方式；完整说明在 `cloud/README.zh.md`。定制语音无需电脑 Home Assistant 常驻。

在 `cloud/stackchan-server` 将 `.env.standalone.example` 复制为 `.env`，在本机填写 Key，选择 `openai_compatible`，配置 DeepSeek 基础地址及当前账号可用的模型。当前适配实现对 `api.deepseek.com` 使用 `/v1/tools/asr` 和 `/v1/tools/tts`，对话走兼容接口；这描述本次快照的实现，不保证未来供应商接口和账号权限不变。实际使用过的配置为对话 `deepseek-flash`、识别 `deepseek-asr`、合成 `deepseek-tts`、声音 `stella`。

```sh
docker compose -f docker-compose.standalone.yml up --build -d
```

启用角色工具时传入 `STACKCHAN_ROBOT_TOOLS=1`，自定义声音设置 `STACKCHAN_VOICE_PACK_DIR=/data/voice-pack`，包放入挂载的 data 目录。Compose 已包含这两个变量的透传。通过本地设置页把 `examples/persona-zh.txt` 保存为 system_prompt。当前两轴版本提示词以文件中的修订为准。

设置页面只绑定本机回环地址。公网使用需要独立 HTTPS 网关，把设备流量转发到回环监听的语音服务，避免把管理端口直接开放。可把 `examples/gateway-config.example.json` 复制为被忽略的 `gateway-config.json`，填写自己的域名和设备标识，然后运行：

```sh
python tools/render-gateway.py gateway-config.json
```

生成的 `private/gateway/Caddyfile` 和 `device-access.json` 是本机配置，不应提交。生成器不会启动服务或开放端口。安装 Caddy 后先 validate，再使用自己的服务管理方式部署；使用 Docker 时将语音端口映射也限制为 `127.0.0.1`。设备标识过滤不是完整认证，随机 URL 应视为凭据，TLS 必须保持有效。

## Windows Home Assistant 控制台

安装 Python 与 `home-assistant/requirements.txt`。把 `examples/local-config.example.json` 复制为 `home-assistant/local-config.json`，填写本机 MQTT 账号；如已有 MQTT/Home Assistant，可直接接入，无需安装专用容器。

```powershell
python home-assistant/build-dashboard.py
python home-assistant/bridge.py
```

将生成的仪表盘作为 Home Assistant YAML 仪表盘加载，并开启 MQTT discovery。机器人保持 AI Agent，桥接会扫描唯一一台 StackChan 并连接；多台设备同时广播时会拒绝猜测目标。蓝牙控制和云端聊天可同时工作。

`cloud.enabled` 默认 false，基本遥控不依赖 SSH。要启用云端录音开关，填写自己的主机、身份文件和已核验的 known_hosts，配置具备相应权限的受限管理账号；`StrictHostKeyChecking` 保持开启。

若希望复现本项目专用的 WSL Ubuntu-22.04 安装，可检查并手动运行 `setup-local.py`、`install-specialization.py` 和 `build-dashboard.py --install`，最后运行 `launcher.py`。这些脚本针对专用环境，会配置回环 MQTT、创建名为 stackchan-homeassistant 的容器和 `/opt/stackchan-homeassistant` 目录，不应直接套用到已有复杂的 Home Assistant 部署。
