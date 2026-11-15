# 自定义语音

仅使用自己拥有使用权的录音。仓库内不分发个人录音，也不提供含个人声音的刷机包。需要本机安装 FFmpeg；所有生成声音和实际清单保留在被忽略的运行目录。

## 云端整句触发

```sh
python tools/import-audio.py cloud praise.wav --output home-assistant/voice-pack
```

将 `examples/voice-pack.example.json` 复制为该目录的 `manifest.json`，使 `file` 对应生成的文件名，填写 `reply` 和 `triggers`，完成后把 `enabled` 改为 true。将整个包放到服务读取的目录，设置 `STACKCHAN_VOICE_PACK_DIR`。

格式为二万四千赫兹、单声道、十六位小端无头 PCM，每段最多十五秒。匹配会忽略大小写、空白和标点，允许指定角色称呼前缀，使用整句匹配，不用包含某个词的任意长句误触发。未匹配时正常调用模型。

电脑试听使用同一格式与清单；试听按钮播放电脑扬声器，不会远程播放到机器人。

## 摸头十句

```sh
python tools/import-audio.py headpet clip01.wav clip02.wav clip03.wav clip04.wav clip05.wav clip06.wav clip07.wav clip08.wav clip09.wav clip10.wav --output firmware/main/assets/sfx
```

按照传入顺序生成 `dania_cute_01.ogg` 至 `dania_cute_10.ogg`，重新构建固件即可。必须十个文件齐全才启用可选语音模块；没有私人文件时仍保留摸头表情，不会引用不存在的嵌入符号。

触发条件为 AI Agent 待机且没有正在播放的声音，冷却六秒，不连续选中同一条。当前不支持从 SD 卡加载，修改这十句仍需重新构建和刷写。
