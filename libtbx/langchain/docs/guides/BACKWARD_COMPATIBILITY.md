# Backward Compatibility: Server Updates vs. Older Clients

## The Problem

Users run the PHENIX AI Agent client (GUI/CLI) on their local machines. The agent's "brain" runs on a remote REST server. When you update the server, some users will still be running older client versions. A server change that assumes a new field in `session_info` will silently break for every old client that doesn't send it.

This document describes the backward compatibility system that is now **implemented and enforced by automated tests**.

## Architecture

```
CLIENT (user's PHENIX)                  SERVER (your REST endpoint)
─────────────────────                   ──────────────────────────
programs/ai_agent.py                    agent/graph_nodes.py
  └── agent/session.py                    ├── perceive()    ← normalization + version gate
  └── agent/best_files_tracker.py         ├── plan()        ← stop guard
                                          ├── build()       ← stop guard
                                          │   └── agent/command_builder.py
                                          │   └── agent/workflow_state.py
                                          │   └── agent/workflow_engine.py
                                          ├── validate()    ← stop guard
                                          ├── fallback()    ← stop guard
                                          └── output_node() ← response field guarantees

  ── session_info, files, history ──→
  ←── history_record (command, etc) ──
```

The `agent/` directory is **shared code** — it ships with both the client (in PHENIX) and the server. When you update the server, the `agent/` code on the server is newer than the `agent/` code on the client. This is the primary source of compatibility risk.

## The Contract (`agent/contract.py`)

The contract registry is the **single source of truth** for the client↔server interface. It defines every field, its default value, and the protocol version it was introduced in.

### Client → Server (Request)

The client calls `decide_next_step()` with these arguments:

| Argument | Type | Description |
|----------|------|-------------|
| `log_content` | str | Log text from the last program run |
| `history` | list[dict] | Cycle records with `program`, `command`, `result`, `output_files`, `analysis` |
| `files` | list[str] | Available file paths (validated on client) |
| `guidelines` | str | User advice / project instructions |
| `session_resolution` | float or None | Resolution in Å |
| `session_info` | dict | **The main extensibility surface** — see below |
| `abort_on_red_flags` | bool | Whether to abort on sanity check failures |
| `abort_on_warnings` | bool | Whether to abort on warnings |

**`session_info` fields** (registered in `agent/contract.py :: SESSION_INFO_FIELDS`):

| Field | Default | Version | Description |
|-------|---------|---------|-------------|
| `experiment_type` | `""` | v1 | `"xray"` or `"cryoem"` |
| `best_files` | `{}` | v1 | `{category: path}` from BestFilesTracker |
| `rfree_mtz` | `None` | v1 | Locked R-free MTZ path |
| `directives` | `{}` | v1 | Parsed user directives |
| `rfree_resolution` | `None` | v2 | Resolution limit of R-free flags |
| `force_retry_program` | `None` | v2 | One-shot forced program for error recovery |
| `recovery_strategies` | `{}` | v2 | Error recovery hints |
| `explicit_program` | `None` | v2 | User-requested program from advice |
| `advice_changed` | `False` | v2 | Whether advice changed on resume |
| `bad_inject_params` | `{}` | v2 | Parameter injection blacklist |
| `unplaced_model_cell` | `None` | v3 | Pre-extracted CRYST1 cell `[a,b,c,α,β,γ]` |
| `model_hetatm_residues` | `None` | v3 | Pre-extracted HETATM data `[[chain,resseq,resname],...]` |
| `client_protocol_version` | `1` | v3 | Protocol version of the sending client |

### Server → Client (Response)

The server returns a `history_record` dict. `output_node()` guarantees these fields always exist with safe defaults:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `next_move` | dict | — | `{command, program, explanation, process_log}` |
| `debug_log` | list[str] | `[]` | Diagnostic messages from all nodes |
| `events` | list[dict] | `[]` | Structured events for display |
| `experiment_type` | str | — | Detected experiment type |
| `stop_reason` | str or None | `None` | Why the agent stopped |
| `abort_message` | str or None | `None` | Red flag abort explanation |
| `red_flag_issues` | list | `[]` | Issues that triggered abort |
| `warnings` | list[str] | `[]` | Deprecation/advisory messages |

### The `warnings` Deprecation Channel

The `warnings` field provides a server→client communication channel for non-fatal advisories. Currently it's used for protocol version deprecation:

- **Server side** (`perceive()` via `get_deprecation_warnings()`): Automatically appends a warning when `client_protocol_version < CURRENT_PROTOCOL_VERSION`.
- **Client side** (`ai_agent.py`): After receiving the response, prints each warning via `self.vlog.quiet("[AI Server Warning] ...")`.
- **Old clients** that don't check `warnings` simply ignore the field — they already use `.get()` / `getattr()` for all response fields.

### Protocol Version Constants

Defined in `agent/contract.py`:

| Constant | Value | Purpose |
|----------|-------|---------|
| `CURRENT_PROTOCOL_VERSION` | `3` | What the latest client sends |
| `MIN_SUPPORTED_PROTOCOL_VERSION` | `1` | Oldest client the server accepts |

The client imports `CURRENT_PROTOCOL_VERSION` from contract.py via `_get_protocol_version()` in `ai_agent.py`, so bumping the constant updates both server and client automatically.

## The Rules

### RULE 1: New `session_info` fields MUST have safe defaults

Every server-side `.get()` on a `session_info` field MUST supply an explicit default that matches the contract:

```python
# CORRECT
hetatm = session_info.get("model_hetatm_residues", None)
strategies = session_info.get("recovery_strategies", {})
changed = session_info.get("advice_changed", False)

# WRONG — crashes if old client doesn't send it
hetatm = session_info["model_hetatm_residues"]

# FRAGILE — returns None instead of contract default {}
strategies = session_info.get("recovery_strategies")
```

**Enforced by**: `tst_contract_compliance.py :: test_no_bare_session_info_bracket_reads` and `test_get_without_default_on_non_none_fields`. Both are hard assertions — the test suite fails if either is violated.

### RULE 2: Never REMOVE a response field the client reads

Old clients read specific fields from the response. If the server stops sending a field, old clients break. Fields can be added freely.

**Enforced by**: `output_node()` in `graph_nodes.py` guarantees `warnings`, `debug_log`, `events`, `stop_reason`, `abort_message`, and `red_flag_issues` always exist in the final state.

### RULE 3: Never change the SEMANTICS of existing fields

If `best_files.model` means "best positioned model path", don't repurpose it. Add a new field instead.

### RULE 4: New programs.yaml entries are safe; removing entries is not

Adding a new program to `programs.yaml` is safe. Removing a program that old clients might request via `explicit_program` or directives could cause confusing behavior.

### RULE 5: File list is the client's responsibility

The server must never assume files exist on its filesystem. All file discovery, validation, and content reading must happen on the client, with results passed through `session_info`.

### RULE 6: Hard rejection for unsupported clients

When a client is too old to serve correctly, the server returns a clean STOP with a human-readable error message instead of silently producing wrong results.

**Implemented in**: `perceive()` calls `check_client_version()` from `contract.py`. If `client_protocol_version < MIN_SUPPORTED_PROTOCOL_VERSION`, the pipeline returns immediately with `stop=True`, `stop_reason="unsupported_client"`, and an `abort_message` telling the user to update.

Today `MIN_SUPPORTED_PROTOCOL_VERSION = 1` (accept everything). When you bump it, the `client_protocol_version` logging data tells you how many users would be affected.

### RULE 7: The `agent/` shared code trap

Code in `agent/` runs on both client and server. If the server adds a new function to `agent/workflow_engine.py` that old clients don't have, any code path that calls it on the client will crash.

Mitigations:
- Guard new utility functions with `try/except ImportError` when callable from client paths
- Prefer adding server-only logic in `graph_nodes.py` rather than modifying shared files
- Verify new dependencies exist in the PHENIX distribution

**Enforced by**: `tst_shared_code_imports.py :: test_no_forbidden_imports_in_shared` (blocks LLM SDK imports in shared code) and `test_agent_imports_reference_known_modules` (blocks imports of unknown modules).

## What's Implemented

### Runtime Protection (server side)

All in `agent/graph_nodes.py`:

1. **`perceive()` — Normalization and version gate**: At the top of the pipeline, before any `session_info` access:
   - Calls `normalize_session_info()` — fills in safe defaults for every registered field (mutable defaults are copied to avoid cross-request contamination)
   - Calls `check_client_version()` — returns a clean STOP if client is below `MIN_SUPPORTED`
   - Calls `get_deprecation_warnings()` — appends advisory messages to `state["warnings"]`
   - Wrapped in `try/except ImportError` — graceful no-op if contract.py is somehow missing

2. **Stop guards on all 5 pipeline nodes**: Every node after `perceive()` begins with:
   ```python
   if state.get("stop"):
       return state
   ```
   This ensures a stop from any upstream node (version rejection, red flag, etc.) flows cleanly through without any node accidentally overwriting it or trying to build a command.

3. **`output_node()` — Response field guarantees**: Before returning the final state, ensures `warnings`, `debug_log`, `events`, `stop_reason`, `abort_message`, and `red_flag_issues` all exist with safe defaults.

### Client-Side Changes (`programs/ai_agent.py`)

1. **`client_protocol_version`**: Sent in `session_info`, imported from `contract.py` via `_get_protocol_version()` helper (falls back to `3` if import fails).

2. **`warnings` handling**: After receiving the server response, checks `history_record.get('warnings')` (with fallback to `result_record.warnings`) and prints each as `[AI Server Warning] ...`.

### Contract Registry (`agent/contract.py`)

Single file containing:
- `CURRENT_PROTOCOL_VERSION` and `MIN_SUPPORTED_PROTOCOL_VERSION` constants
- `SESSION_INFO_FIELDS` — every field with its default, version, and description
- `RESPONSE_FIELDS` and `NEXT_MOVE_FIELDS` — response shape documentation
- `HISTORY_ENTRY_FIELDS` — history list entry shape
- `STATE_PROMOTED_FIELDS` — fields copied from session_info to top-level state
- `normalize_session_info()` — runtime helper
- `check_client_version()` — version gate
- `get_deprecation_warnings()` — advisory message generator

Zero external dependencies — safe to import on both client and server.

## Test Suite

### `tests/tst_contract_compliance.py` — 10 tests

Static analysis and contract verification:

| Test | What it catches |
|------|----------------|
| `test_no_bare_session_info_bracket_reads` | `session_info["X"]` without `.get()` — would KeyError on old clients |
| `test_all_accessed_fields_in_contract` | Fields used but not registered in contract |
| `test_contract_defaults_consistency` | `.get("X", wrong_default)` — default doesn't match contract |
| `test_get_without_default_on_non_none_fields` | `.get("X")` where contract default is `{}`, `False`, `""` — fragile without normalization |
| `test_protocol_version_consistency` | Client sends a version that doesn't match `CURRENT_PROTOCOL_VERSION` |
| `test_version_bounds` | `MIN_SUPPORTED > CURRENT` (impossible state) |
| `test_warnings_in_response_contract` | `warnings` missing from `RESPONSE_FIELDS` |
| `test_client_handles_warnings` | Client code doesn't check for server warnings |
| `test_normalize_covers_all_fields` | `normalize_session_info()` misses a registered field |
| `test_normalize_mutable_isolation` | Mutable defaults leak between calls |

### `tests/tst_old_client_compat.py` — 6 tests

Frozen fixture tests against real old-client payloads:

| Test | What it catches |
|------|----------------|
| `test_fixtures_valid_structure` | Fixture JSON is well-formed |
| `test_normalize_fills_missing_fields` | Old client payloads get all fields filled in |
| `test_junk_fields_preserved` | Unknown fields aren't stripped (forward compat) |
| `test_version_check_accepts_all` | Current fixtures aren't rejected |
| `test_deprecation_warnings_for_old_clients` | Old clients get warnings, current clients don't |
| `test_full_pipeline_if_available` | Full perceive→output pipeline with old payloads (in PHENIX env) |

**Fixtures** (in `tests/fixtures/`, NEVER updated after creation):

| Fixture | Protocol | Fields | Scenario |
|---------|----------|--------|----------|
| `client_v1/xray_ligandfit.json` | v1 | 5 (missing 8) + junk field | X-ray after 2 refines, needs ligandfit |
| `client_v1/cryoem_initial.json` | v1 | 4 (missing 9) | Cryo-EM first cycle with half-maps |
| `client_v3/xray_ligandfit.json` | v3 | 13 (complete) | Same X-ray scenario, full fields |

### `tests/tst_shared_code_imports.py` — 4 tests

Import safety for shared `agent/` modules:

| Test | What it catches |
|------|----------------|
| `test_no_forbidden_imports_in_shared` | LLM SDKs (langchain, openai, anthropic) in shared code |
| `test_agent_imports_reference_known_modules` | Import of `agent.X` where X doesn't exist in PHENIX |
| `test_server_only_has_llm_imports` | Sanity check: `graph_nodes.py` should have LLM imports |
| `test_shared_imports_are_guarded` | Top-level unguarded intra-agent imports (warning-level) |

### Running All Tests

```bash
cd /path/to/agent_debug
PYTHONPATH=. python tests/tst_contract_compliance.py      # 10 tests
PYTHONPATH=. python tests/tst_old_client_compat.py        # 6 tests
PYTHONPATH=. python tests/tst_shared_code_imports.py      # 4 tests
PYTHONPATH=. python tests/tst_audit_fixes.py              # 31 tests (pre-existing)
```

## Version Lifecycle

The protocol version system provides a deprecation lifecycle:

```
 v1 shipped ──→ v2 shipped ──→ v3 shipped ──→ MIN bumped to v2
                                                │
                   soft warning ◄────────────────┘
                   for 6 months
                        │
                   hard rejection
                   after deadline
```

**To make a breaking change**:

1. Ship the new client (e.g., v4) with the new field/behavior
2. Server automatically warns clients below v4 (via `get_deprecation_warnings()`)
3. Monitor `client_protocol_version` logs to see who's still on old versions
4. Set a deadline and add it to the warning message
5. After the deadline, bump `MIN_SUPPORTED_PROTOCOL_VERSION` to v4
6. Old clients get a clear "please update" error, not silent breakage

## Checklist for Every Server Update

### Code Review

- [ ] No new `session_info["X"]` bracket reads — always `.get("X", default)`
- [ ] Every `.get("X", default)` uses the contract-registered default
- [ ] New session_info fields added to `contract.py :: SESSION_INFO_FIELDS`
- [ ] No removed response fields — old clients depend on them
- [ ] No changed field semantics — add new fields instead
- [ ] New `programs.yaml` entries are additive — no removals
- [ ] No new `os.path.exists()` guards on file paths — use `_file_is_available()`
- [ ] No new `open()` calls without fallback — server can't read client files
- [ ] New features degrade gracefully when session_info field is missing
- [ ] Shared `agent/` code: no new imports that don't exist in shipped PHENIX
- [ ] Version-gated features: if it needs a new client field, gate on `client_protocol_version`

### Tests

- [ ] `tst_contract_compliance.py` passes (10 tests)
- [ ] `tst_old_client_compat.py` passes (6 tests)
- [ ] `tst_shared_code_imports.py` passes (4 tests)
- [ ] `tst_audit_fixes.py` passes (31 tests)
- [ ] Manual test: one cycle with current PHENIX client against new server

### Deployment

1. Merge changes to server code
2. Run full test suite including compatibility tests
3. Deploy to staging server
4. Run current PHENIX release against staging (smoke test)
5. Deploy to production
6. Monitor for errors from clients (log `client_protocol_version`)

## How to Add a New session_info Field

1. Add the entry to `agent/contract.py :: SESSION_INFO_FIELDS` with the next version number and a safe default
2. In `programs/ai_agent.py`, add the field to the `session_info` dict in `_query_agent()`
3. On the server, access it **only** via `session_info.get("field", default)` where the default matches the contract
4. Run `tst_contract_compliance.py` to confirm compliance
5. Create a new frozen fixture in `tests/fixtures/client_vN/` if the change represents a meaningful new scenario

## Files

| File | Role |
|------|------|
| `agent/contract.py` | Contract registry, version constants, runtime helpers |
| `agent/graph_nodes.py` | Pipeline nodes with normalization, version gate, stop guards, response guarantees |
| `programs/ai_agent.py` | Client: sends `client_protocol_version`, handles `warnings` |
| `tests/tst_contract_compliance.py` | 10 static analysis + contract tests |
| `tests/tst_old_client_compat.py` | 6 frozen fixture tests |
| `tests/tst_shared_code_imports.py` | 4 import safety tests |
| `tests/tst_utils.py` | Test infrastructure (assert helpers, test runner) |
| `tests/fixtures/client_v1/` | 2 frozen old-client payloads |
| `tests/fixtures/client_v3/` | 1 frozen current-client payload |
