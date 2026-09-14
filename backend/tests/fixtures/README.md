# Recorded stream-json fixtures

All four were produced against the **pinned 2.1.236 CLI running in the
Factor-ia container**, which is the build whose output shape the adapter parses.

| File | Origin |
|---|---|
| `initialize_handshake.json` | **Captured.** The real `control_response` to one `initialize` control request. Free (0 tokens) and it answers *while signed out*, which is what lets the "model default comes from the CLI's own handshake" rule be tested without a session. |
| `unauthenticated_run.jsonl` | **Captured.** A real run attempted with no credential: `system/init`, `system/status`, the replayed user turn, a `<synthetic>` assistant frame carrying `error: "authentication_failed"`, and a `result` with `is_error: true` — which the CLI nonetheless labels `subtype: "success"`. The process exits **1**. This is the whole non-zero-exit / auth-failure path, recorded rather than imagined. |
| `turn_success.jsonl` | **Constructed**, on the captured `system/init` frame and the documented streaming shapes (`content_block_start` / `content_block_delta` / `content_block_stop`, `tool_use`, `tool_result`, `result`). A successful turn cannot be captured without a signed-in session and spent tokens; the operator's manual verification is what confirms the real thing matches. |
| `permission_request.jsonl` | **Constructed.** The spawn passes no `--permission-prompt-tool`, so the CLI has nobody to ask and never emits this today. Kept because the event is in the adopted union. |

`turn_success.jsonl` also carries two frames the normalizer must **drop**:
`system/status` and `rate_limit_event`. They are in the fixture precisely so a
future change that starts mapping them fails a test.
