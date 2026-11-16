# Desktop Codex voice tasks

Goal: each explicit spoken Codex request creates a visible desktop chat in the
configured local workspace. DeepSeek improves clarity while preserving the
original request. No hidden CLI fallback.

1. Use the installed official codex-app-tools MCP server over standard stdio.
   Discover its current local pipe on reconnect; use the configured originating
   chat as caller context. Do not alter desktop permissions or model settings.
2. Add a desktop task manager, persist returned chat IDs, and monitor formal turn
   status. On companion restart resume monitoring, never resubmit a task.
3. Optimize only Codex requests using DeepSeek, include the verbatim current user
   utterance in the visible prompt, and keep ordinary conversation unchanged.
4. Configure this machine's default project to E:\测试profile. Keep paths, caller
   IDs, tokens and plugin locations in private local configuration.
5. Verify fresh chats, correct workspace, original intent, duplicate protection,
   failure handling and completion monitoring. Deploy with backups; retain the
   accepted fluent audio firmware and playback configuration.

## Validation

- Forty PC tests passed, including fresh desktop chats, unknown submissions,
  honest blocked results, observer restart without resubmission and notifications
  for older chats finishing later.
- Full Go suite, AI service race checks and production build passed.
- Synthetic Chinese speech traversed STT, DeepSeek task optimization and the
  authenticated PC relay. Two independent desktop chats were created in the
  requested workspace, retaining the original and optimized instructions.
- After replacing the executor-only wait helper with the desktop read_thread
  tool, formal completion was observed and the real robot acknowledged its
  SD completion recording finished. No firmware or playback setting changed.
- Desktop cancellation is manual because the available app tools do not expose
  interruption. Plugin upgrades may require updating the private server path.
