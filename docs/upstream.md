# 上游与快照

首次公开快照整理于二零二六年十一月十五日，使用独立的初始提交；不包含原运行目录的 Git 历史、备份和密钥。

| 部分 | 上游 | 基线提交 |
| --- | --- | --- |
| 固件 | https://github.com/m5stack/StackChan | `28b9f0c8bd6acd81cb3c70f1a93c3e373b39d400` |
| AI 服务 | https://github.com/rudyll/stackchan-ai-server | `195eed0f41a039762998fc58315efc1077447cb5` |
| xiaozhi 依赖 | https://github.com/78/xiaozhi-esp32 | `49b4380b3faa0ceadd28a3e78aaf0f311c46c57e` |

固件定制直接保存在 `firmware/main`；依赖定制汇总在 `firmware/patches/xiaozhi-esp32.patch`，其中既包含上游 StackChan 的适配，也包含达妮娅的修改。不要再叠加一遍原来的上游补丁。`firmware/repos.json` 固定 xiaozhi 提交，其余组件版本保持上游发布标签或提交。

云端保留上游目录和说明用于追溯；原 README 中的作者自述、发行下载和旧仓库地址属于上游，不代表本仓库已经发布了相同的成品。当前项目入口以根 README 和本目录的说明为准。
