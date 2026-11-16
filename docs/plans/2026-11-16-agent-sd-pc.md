# Dania Agent, SD Audio and PC Assistant Implementation Plan

> Execution: subagent-driven-development in this session; specification review precedes quality review for each task. The user's instruction already authorizes implementation and deployment; no execution-choice question is needed.

**Goal:** Make Dania boot directly into Agent, load private voice resources from SD with fallback, and execute bounded voice-requested Windows actions with real execution results.

**Architecture:** Preserve the current firmware/private runtime as rollback. The ESP32 handles touch, expression, SD audio and BLE. A Windows client opens an authenticated outbound WSS connection to the Go server; the server exposes only client-advertised, configured actions to the authorized robot session and returns actual results to the LLM. Existing conversation and BLE must remain functional when the PC is offline or SD is absent.

**Tech Stack:** ESP-IDF 6.1/C++23, Go 1.26, Python 3.12+, Windows native APIs, Home Assistant MQTT, Ogg Opus on FAT32 SD.

## Shared PC protocol, version one

Endpoint: `/dania/pc/ws`; Authorization: `Bearer <STACKCHAN_PC_TOKEN>`; TLS terminates at the existing Caddy gateway on a separate authenticated route. Require at least 32 token characters. One live connection per configured PC; reject takeover rather than silently replacing an active executor. No offline queue or automatic command replay.

Client sends `{"type":"hello","version":1,"pc_id":"desktop","capabilities":{"apps":[{"id":"browser","name":"浏览器"}],"projects":[{"id":"stackchan","name":"机器人项目"}],"actions":["open_app","open_project","set_volume","media","search_web","lock"]}}`.

Server replies `{"type":"ready","version":1}`. Calls are `{"type":"call","id":"random-id","action":"open_app","args":{"id":"browser"},"deadline_ms":<unix milliseconds>}`. Results are `{"type":"result","id":"same-id","ok":true,"message":"...","data":{}}`. Every result must match an outstanding ID on the same connection; failures/timeouts are explicit. A call deadline expires in at most 15 seconds. Client must reject expired requests and repeat IDs without replaying physical actions. Frame limit 16 KiB; bounded concurrent/pending calls. Unknown actions and extra arguments fail closed.

Action argument schemas: open_app/open_project `{id: configured ID}`; set_volume `{value: integer 0..100}`; media `{command: play_pause|next|previous}`; search_web `{query: nonempty bounded string}`; lock `{}`. PC applications and folders are configured locally, never arbitrary model-supplied paths or shell commands. Desktop-initiated notifications and complex automation are later extensions, not silently enabled.

## Task 1: Windows PC executor and client

Create `pc-assistant/assistant.py`, `actions.py`, requirements and isolated unit tests, example private config and installation/startup instructions. Implement authenticated reconnecting client, schema validation, bounded execution and duplicate suppression. Native volume/media/lock; allowlisted executable/folder IDs; encoded web search. Dry-run explicitly reports simulation. Separate process startup from existing BLE bridge. Test with fake executor/WebSocket and real loopback protocol; do not lock the sleeping user's PC or open arbitrary apps during tests. Verify strict deadline/duplicate/error behavior and that credentials never enter public logs/configs. Commit only scoped files.

## Task 2: Go relay and voice tools

Implement connection authentication/lifecycle, bounded requests and deadline/cancellation behavior under AI package; integrate with compatible chat tool dispatch and robot device authorization. Require explicit device IDs env and map authorized robot to one configured PC. Only advertise tools whose actual connected-client capabilities permit them; offline computer is an ordinary explicit failure. Add API/transport lifecycle/security tests and include Compose defaults disabled. Tests run in isolated server copy with existing Go/cache/libopus, no production overwrite until checks and reviews pass.

## Task 3: Agent-only firmware and SD resources

Compile out standalone Dance, Avatar UI/video, ESP-NOW controller, EZData, App Center, templates and desktop launcher. Retain Agent rendering, touch/servo/BLE, small maintenance UI with provisioning, calibration, volume/brightness and diagnostics, manual update path and recovery entry. Boot directly into Agent by default, deliberately enter maintenance on persisted request; no firmware/asset check on Agent entry. Investigate actual SD pinout/shared LCD bus electrically before editing driver pin assignments. Never format on mount failure. Streaming/bounded file read, missing/corrupt SD fallback to built-in clips. Add getStorageInfo BLE response with request ID and validate voice manifest IDs/files. Preserve known-good Wi-Fi/audio/console/PSRAM behavior. Host tests plus full firmware build; backup and validate current flash/partition map before device update, preserve NVS/assets and prior slot.

## Task 4: Local clip protocol and console integration

Negotiate manifest/version/local-audio capability before server requests clip ID. Include playback completion/failure acknowledgments and timeout; fall back to cloud audio on unavailable/mismatch before playback, never silently lose speaking lifecycle. Keep free-response TTS streaming. Add console SD and PC state, configured actions and private voice import helpers; retain current HA route and existing volume controls. Test mismatched/missing/corrupt resource and offline PC cases, retain user recordings only in private runtime/SD.

## Integration and release

Run CTest, Windows/client tests, Go full tests/build, YAML and shell syntax checks, private-content scan and git diff check. Review spec then quality per task, final integration review. Stage hardware update in inactive slot with rollback backup, observe stable Agent auto-entry/BLE and SD mount. Save human-only voice/touch/screen observations as pending when user is asleep. Install current-user startup for PC client with secrets in ignored private config; do not open inbound PC port. Update docs with actual verified results; commit/push a reviewable feature branch and only promote stable tested changes to main. Keep pending features and verification explicit.

## Execution record

Tasks one through four and final integration review passed specification and quality checks. Firmware, cloud relay, PC companion and console were installed in the private runtime on 2026-11-16, preserving rollback and private configuration. Automated and muted hardware evidence is recorded in [publication checks](../publication-checks.md). Human listening, maintenance touch and physical cold boot remain pending; arbitrary desktop/file/coding automation is outside this iteration.
