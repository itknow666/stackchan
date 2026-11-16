# 自定义语音

仅使用自己拥有使用权的录音。仓库内不分发个人录音，也不提供含个人声音的刷机包。需要本机安装 FFmpeg；所有生成声音和实际清单保留在被忽略的运行目录。

## 云端整句触发

```sh
python tools/import-audio.py cloud praise.wav --output home-assistant/voice-pack
```

将 `examples/voice-pack.example.json` 复制为该目录的 `manifest.json`，使 `file` 对应生成的文件名，填写 `reply` 和 `triggers`，完成后把 `enabled` 改为 true。将整个包放到服务读取的目录，设置 `STACKCHAN_VOICE_PACK_DIR`。

格式为二万四千赫兹、单声道、十六位小端无头 PCM，每段最多十五秒。匹配会忽略大小写、空白和标点，允许指定角色称呼前缀，使用整句匹配，不用包含某个词的任意长句误触发。未匹配时正常调用模型。

电脑试听使用同一格式与清单，在电脑扬声器播放；控制台另有机器人 SD 试听按钮，只在 Agent 待机且资源可用时播放。

## 摸头十句

```sh
python tools/import-audio.py headpet clip01.wav clip02.wav clip03.wav clip04.wav clip05.wav clip06.wav clip07.wav clip08.wav clip09.wav clip10.wav --output firmware/main/assets/sfx
```

按照传入顺序生成 `dania_cute_01.ogg` 至 `dania_cute_10.ogg`，重新构建固件即可。必须十个文件齐全才启用可选语音模块；没有私人文件时仍保留摸头表情，不会引用不存在的嵌入符号。

触发条件为 AI Agent 待机且没有正在播放的声音，冷却六秒，不连续选中同一条。现在优先使用 SD 清单中的十个 headpet ID，没有有效 SD 包时保留上述私有嵌入录音回退。

## SD 语音包

使用硬件支持的 FAT32 卡。固件不自动格式化，也不覆盖未知已有清单。工具仅在电脑私有输出目录生成文件，不上传或刷机。

```sh
python tools/sd-voice-pack.py hello.wav praise.wav --ids hello praise --pack-id my-pack --output private/my-pack
```

把输出内容复制到 SD 根目录的 `dania/voice-pack` 后重启。输入支持普通录音和二万四千赫兹单声道 PCM16 `.pcm`。输出为单声道 Opus Ogg，每段最多十五秒含编解码尾包、二百五十六 KiB，最多六十四段；清单含大小、CRC32 和 SHA256。非法或重复 ID、缺文件、符号链接和非空输出目录会拒绝，不覆盖其他资源。

用 `--headpet cute_01 cute_02 cute_03 cute_04 cute_05 cute_06 cute_07 cute_08 cute_09 cute_10` 指定十个已导入 ID 的摸头选择顺序。更新 SD 声音无需重刷固件，但须重新生成清单并重启。SD 不能增加运行内存。

在云端 PCM 清单设置相同 `pack_id`，为对应 rule 设置与 SD 相同 `id`，保留原 `file` 和 PCM 文件作为回退。设备包和 ID 匹配才走本地播放，否则继续网络录音。详见[协议说明](local-audio-protocol.md)。
