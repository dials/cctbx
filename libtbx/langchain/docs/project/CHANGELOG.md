# PHENIX AI Agent - Changelog

## Version 112.52 (S2L — Probe Crash Interpreted + Client Cell Transport)

Fixes the apoferritin AIAgent_165 scenario where the placement probe
(`phenix.map_correlations`) crashed with "model is entirely outside map",
ran a second time with the same result, then caused the agent to quit —
never routing to `dock_in_map`.

### Root cause analysis — three independent failures

**Failure 1 — Tier 1 (cell mismatch) is non-functional in production.**
`_check_cell_mismatch` calls `check_cryoem_cell_mismatch(pdb_path, map_path)`
which immediately calls `os.path.exists(pdb_path)`. On the server, `pdb_path`
is `/Users/terwill/.../1aew_A.pdb` — a client-side path. `os.path.exists`
returns `False`. Function returns `False` (fail-safe). The 4× unit cell
mismatch (184 Å F432 crystal vs 32.5 × 39.65 × 36.4 Å P1 sub-box) is never
detected. **Tier 1 has been silently broken for all RemoteAgent users.**

**Failure 2 — The crash itself carries the answer, but the code discards it.**
The probe runs correctly. `map_correlations` raises:

```
Sorry: Stopping as model is entirely outside map and wrapping=False
```

This is a stronger signal than a low CC — it's categorical proof the model
is not placed. But `_analyze_history` has this at the top of the probe loop:

```python
if _is_failed_result(_result):
    continue   # Ignore failed cycles for probe detection
```

The entire entry is discarded. `placement_probed` stays `False`.

**Failure 3 — The loop.**
With `placement_probed=False`, `placement_uncertain` is still `True` on the
next cycle. The agent runs `map_correlations` again → same crash → same discard
→ eventually the LLM consecutive-failure counter trips and the run stops with
no docking ever attempted.

### Fix 1 — Probe crash detection in `_analyze_history` (workflow_state.py)

Before the `continue`, inspect failed `map_correlations` entries that occurred
before any refine/dock cycle:

```python
if _is_failed_result(_result):
    if "map_correlations" in _ecomb and not _seen_refine_or_dock:
        _rl = (_result or "").lower()
        _outside_signals = [
            "entirely outside map", "outside map", "model is outside",
            "model entirely outside", "stopping as model",
        ]
        if any(s in _rl for s in _outside_signals):
            # Hard evidence: model is not in the map at all
            info["placement_probed"] = True
            info["placement_probe_result"] = "needs_dock"
        elif not info.get("placement_probed"):
            # Unknown failure — prevent infinite probe retry
            info["placement_probed"] = True
            # Leave placement_probe_result as None (inconclusive)
    continue
```

Three outcomes:
- **"outside map" crash** → `placement_probed=True, result="needs_dock"` → routes to `dock_model`
- **Other crash** → `placement_probed=True, result=None` → inconclusive, falls through to `obtain_model`
- **Second failed probe** → `placement_probed` already set, no overwrite; guard on `not info.get("placement_probed")` prevents the inconclusive case from clobbering an earlier definitive result

### Fix 2 — Client-side model cell transport (S2L-b)

The client reads the model's CRYST1 cell (which it has access to) and transmits
it in `session_state["unplaced_model_cell"]`. The server uses this pre-read cell
in `_check_cell_mismatch` instead of trying to open the file:

**Client side** (`programs/ai_agent.py`): Before assembling `session_info`,
read the CRYST1 cell from the first unplaced PDB in `active_files` and add it
to `session_info["unplaced_model_cell"]`. Only populated when placement hasn't
been confirmed by history (no `dock_done`, no `refine_done`).

**Transport** (`agent/api_client.py`): `build_session_state` passes
`unplaced_model_cell` through to `session_state`.

**Server receipt** (`phenix_ai/run_ai_agent.py`): Maps `unplaced_model_cell`
from `session_state` into `session_info`.

**Server use** (`agent/workflow_engine.py`): `build_context` passes
`session_info` down to `_check_cell_mismatch(files, model_cell=...)`.
`_check_cell_mismatch` uses the pre-read cell for comparison against the map
(which the server **can** read — it was just created by `resolve_cryo_em`
on the server). Tier 1 now fires correctly in production:

```
model cell  = (184, 184, 184, 90, 90, 90)   # F432 crystal
map cell    = (32.5, 39.65, 36.4, 90, 90, 90)  # P1 sub-box
→ mismatch > 5% on all three axes → cell_mismatch=True → dock_model
```

### Also fixed: `optimized_full_map` not checked by `_check_cell_mismatch`

After `resolve_cryo_em`, the output `denmod_map.ccp4` is categorized as
`optimized_full_map` (not `full_map`). The original code only checked
`files.get("full_map") or files.get("map")`. Added `optimized_full_map`
as a priority-2 fallback so the freshly-generated density-modified map is
always found.

### Files changed

| File | Change |
|------|--------|
| `agent/workflow_state.py` | `_analyze_history`: probe-crash handler before `continue`; `detect_workflow_state` gains `session_info` parameter |
| `agent/workflow_engine.py` | `_check_cell_mismatch` rewritten with `model_cell` parameter and S2L fast-path; `optimized_full_map` added to map search; `build_context` accepts `session_info`; `get_workflow_state` accepts `session_info` |
| `agent/api_client.py` | `build_session_state` passes `unplaced_model_cell` through |
| `phenix_ai/run_ai_agent.py` | Maps `unplaced_model_cell` from `session_state` → `session_info` |
| `programs/ai_agent.py` | Reads CRYST1 cell client-side, adds to `session_info["unplaced_model_cell"]` |
| `agent/graph_nodes.py` | Passes `session_info=state.get("session_info",{})` to `detect_workflow_state` |
| `tests/tst_audit_fixes.py` | 8 new S2L tests (131 total) |

### Corrected cycle trace for AIAgent_165

| Cycle | Program | Why (after fix) |
|-------|---------|-----------------|
| 1 | `phenix.mtriage` | Map analysis |
| 2 | `phenix.resolve_cryo_em` | Half-maps → denmod_map |
| **3** | **`phenix.dock_in_map`** | **Tier 1: cell_mismatch=True (client cell + server map) → dock_model** |
| 4 | `phenix.real_space_refine` | Model docked, refine |

Without the fix, cycle 3 ran `map_correlations` twice (probe crash not interpreted), then stopped.

### Note on test coverage

Tests 5 and 6 (api_client and workflow_engine) are marked SKIP in non-PHENIX
environments because they require the production libtbx import path. They pass
fully in a PHENIX installation. Tests 1–4, 7–8 pass in all environments.

---

## Version 112.48 (S2k — _inject_user_params Empty Program Guard)

Fixes a subtle Python truth-value bug introduced in v112.47 where the
program-scope filter silently passed all dotted keys through when
`prog_base` was an empty string.

### Problem

```python
# Python: "refinement".startswith("") is True — every string starts with ""
scope_matches = leading_scope.startswith(prog_base) or prog_base.startswith(leading_scope)
```

When `program_name` was not passed to `_inject_user_params`, `prog_base`
defaulted to `""`, so `leading_scope.startswith("")` was always `True` and
every dotted key bypassed the filter — exactly the bug the fix was meant to prevent.

### Fix

Added three guards to the scope-matching expression:

```python
scope_matches = (
    bool(prog_base) and len(prog_base) >= 4 and
    (leading_scope == prog_base or
     (leading_scope.startswith(prog_base) and len(prog_base) >= 4) or
     (prog_base.startswith(leading_scope) and len(leading_scope) >= 4))
)
```

- `bool(prog_base)` — empty string immediately fails; no keys injected
- `len(prog_base) >= 4` — prevents one- or two-character accidental prefix matches
- Prefix matching handles `refine` ↔ `refinement` in both directions

**Decision table:**

| Program | Key | Result |
|---|---|---|
| `phenix.refine` | `refinement.main.number_of_macro_cycles` | **INJECT** (`refine` ⊂ `refinement`) |
| `phenix.ligandfit` | `refinement.main.number_of_macro_cycles` | **SKIP** (no overlap) |
| `phenix.autosol` | `autosol.atom_type` | **INJECT** (exact match) |
| any | `general.nproc` | **INJECT** (universal scope) |
| (empty string) | `refinement.*` | **SKIP** (`bool("")` fails immediately) |

### Files changed

| File | Change |
|------|--------|
| `programs/ai_agent.py` | `_inject_user_params`: three-part guard on scope-matching expression |
| `tests/tst_audit_fixes.py` | 1 new S2k guard test (123 total) |

---

## Version 112.47 (S2k — _inject_user_params Program-Scoped Filtering)

Extends `_inject_user_params` to accept a `program_name` parameter and filter
dotted PHIL keys to only inject parameters that belong to the target program's
scope.

### Problem

After the server correctly built `phenix.ligandfit ... general.nproc=4`, the
client's `_inject_user_params` scanned the guidelines string, found
`refinement.main.number_of_macro_cycles=2` (from an earlier user directive),
and appended it unconditionally:

```
[inject_user_params] appended: refinement.main.number_of_macro_cycles=2
Final command: phenix.ligandfit ... general.nproc=4 refinement.main.number_of_macro_cycles=2
```

The command was clean when it left the server; the client contaminated it.

### Fix

`_inject_user_params(self, command, guidelines, program_name='')` now
classifies each extracted dotted key before injecting:

```python
_UNIVERSAL_SCOPES = {'general', 'output', 'job', 'data_manager', 'nproc'}
prog_base = program_name.replace('phenix.', '').lower()

for key in extracted_keys:
    if '.' in key:
        leading_scope = key.split('.')[0].lower()
        if leading_scope not in _UNIVERSAL_SCOPES and not scope_matches(leading_scope, prog_base):
            skipped.append(key)
            continue
    inject(key)
```

Universal scopes (`general`, `output`, etc.) are always injected. All other
dotted keys are only injected when their leading scope matches the program name.

**Note:** v112.47 contained the empty-`prog_base` bug fixed in v112.48.

### Files changed

| File | Change |
|------|--------|
| `programs/ai_agent.py` | `_inject_user_params` rewritten with `program_name` parameter and scope filter; call site updated to pass `program_name` |
| `tests/tst_audit_fixes.py` | 1 new S2k test (122 total) |

---

## Version 112.46 (S2k — _inject_user_params STOP Guard)

Prevents `_inject_user_params` from running at all when the command is `STOP`.

### Problem

The `_inject_user_params` call site had no guard for STOP commands. A user
directive containing `refinement.main.number_of_macro_cycles=2` would cause
the function to emit `STOP refinement.main.number_of_macro_cycles=2`, which
then failed command validation.

### Fix

```python
if command and command != 'No command generated.' and \
        command.strip().split()[0] != 'STOP':
    command = self._inject_user_params(command, guidelines, program_name)
```

STOP commands bypass `_inject_user_params` entirely.

### Files changed

| File | Change |
|------|--------|
| `programs/ai_agent.py` | Guard added at `_inject_user_params` call site |

---

## Version 112.45 (S2j — program_registry Passthrough Removal)

Removes the dotted-key passthrough in `program_registry.py` that allowed
LLM strategy parameters to leak across program boundaries.

### Problem

`_build_command_from_registry` had a catchall passthrough: any key in the
LLM strategy dict that contained a dot was appended to the command verbatim.
This meant `refinement.main.number_of_macro_cycles=2` from the LLM's strategy
for one program was silently included in the command for any program.

### Fix

Replaced the unconditional passthrough with an allowlist. A strategy key
is now accepted only when it:

1. Appears in `strategy_flags` for this specific program, or
2. Is in `KNOWN_PHIL_SHORT_NAMES` (`nproc`, `twin_law`, `unit_cell`, etc.), or
3. Contains a literal `=` sign (already a complete PHIL assignment)

Dotted-path keys that pass none of these tests are logged as `DROPPED` and
discarded. User-supplied dotted overrides reach the command through
`_inject_user_params` instead (subject to the program-scope filter added in
v112.47/v112.48).

### Files changed

| File | Change |
|------|--------|
| `agent/program_registry.py` | Passthrough logic replaced with allowlist; dropped keys logged |
| `tests/tst_audit_fixes.py` | 1 new S2j code test (121 total) |

---

## Version 112.44 (S2j — Cross-Program Strategy Contamination: Prompt Fix)

Fixes the LLM prompt so the model only emits strategy parameters that apply to
the program it is currently selecting.

### Root cause

Three places in the prompt instructed the LLM to extract user parameters into
the `strategy` field with no program-specificity qualifier. The LLM obediently
included refinement parameters (`refinement.main.number_of_macro_cycles=2`) in
the strategy for every program it selected — including `phenix.ligandfit` and
even `STOP`.

### Fixes (knowledge/prompts_hybrid.py)

**User advice extraction block** (was "Extract any specific parameters from the
user advice and include them in your strategy field"):

> Extract parameters **ONLY when they apply to the program you are currently
> selecting.** Do NOT include parameters for a different program. For example,
> if selecting `phenix.ligandfit` and the user mentioned
> `refinement.main.number_of_macro_cycles`, do NOT include that parameter —
> it applies to `phenix.refine`, not `phenix.ligandfit`.

**OUTPUT FORMAT strategy field** — Added CRITICAL STRATEGY RULE block:

> Strategy keys must ONLY contain parameters valid for the selected program.
> NEVER include parameters from a different program. When stop=true, strategy
> must be empty {}.

**IMPORTANT RULES** — Added rule 6:

> Strategy is program-specific: never put parameters for program X in a
> strategy for program Y.

### Files changed

| File | Change |
|------|--------|
| `knowledge/prompts_hybrid.py` | User advice extraction, OUTPUT FORMAT strategy, IMPORTANT RULES rule 6 |
| `tests/tst_audit_fixes.py` | 1 new S2j prompt test (120 total) |

---

## Version 112.43 (S2i — STOP Command Normalization, Root Cause Fix)

Fixes the root cause of `STOP refinement.main.number_of_macro_cycles=2` being
generated as a command.

### Root cause analysis

The LLM's OUTPUT FORMAT schema has no `command` field — only `program`,
`strategy`, `files`, `stop`. When the LLM returned:

```json
{"program": "STOP", "strategy": {"refinement.main.number_of_macro_cycles": 2}, "stop": false}
```

The PLAN node normalized `program` to `"STOP"` but never set
`intent["stop"] = True`. The BUILD node then saw `stop=False`, fell through the
STOP guard, and assembled the strategy flags onto the command as normal
arguments, producing `STOP refinement.main.number_of_macro_cycles=2`, which
then failed validation as "not a recognized phenix program."

### Fix 1 — PLAN node (agent/graph_nodes.py)

When `chosen_program == "STOP"`, explicitly set `intent["stop"] = True`:

```python
if chosen_program == "STOP" or (intent.get("stop") and
        chosen_program not in valid_programs):
    chosen_program = "STOP"
    intent["program"] = "STOP"
    intent["stop"] = True        # NEW: always set stop=True when program is STOP
```

### Fix 2 — BUILD node (defence in depth)

Both `_build_with_new_builder` and the legacy `build()` function now short-circuit
when either `intent["stop"]` is True or `intent["program"]` is `"STOP"`:

```python
if intent.get("stop") or intent.get("program") == "STOP":
    state = _log(state, "BUILD: Stop requested, no command needed")
    return {**state, "command": "STOP"}
```

This means the BUILD node never sees strategy flags for STOP, regardless of
how the LLM filled the `stop` boolean.

### Files changed

| File | Change |
|------|--------|
| `agent/graph_nodes.py` | PLAN: `intent["stop"] = True` when program is STOP; BUILD: early-exit guard in both build paths |
| `tests/tst_audit_fixes.py` | 4 new S2i tests (119 total) |

---

## Version 112.42 (S2h — validation_cryoem DataManager PHIL Fix)

Resolves a PHIL argument error in `phenix.validation_cryoem` when run with
DataManager-style file arguments.

### Problem

`phenix.validation_cryoem` expected PHIL-scoped arguments
(`input.pdb.file_name=model.pdb`) but the command builder was emitting
positional DataManager style (`model.pdb map.ccp4`), causing a PHIL parse
error on launch.

### Fix

Added the correct PHIL argument template to `programs.yaml` for
`phenix.validation_cryoem`, matching the argument style the program actually
accepts. The command builder picks this up automatically through the standard
invariants pipeline.

### Files changed

| File | Change |
|------|--------|
| `knowledge/programs.yaml` | `phenix.validation_cryoem`: correct PHIL argument template |
| `tests/tst_audit_fixes.py` | 2 new S2h tests (115 total) |

---

## Version 112.41 (S2g — map_correlations Conditional Resolution)

Fixes `phenix.map_correlations` being handed a `resolution=` argument it does
not accept when running in map-vs-map mode.

### Problem

The command builder always injected `resolution=` from the session context
into `map_correlations` commands. In model-vs-map mode this is harmless; in
map-vs-map mode (used by the placement probe) the program does not accept a
resolution argument and exits with an error.

### Fix

Added a conditional resolution invariant to `map_correlations` in
`programs.yaml`: resolution is only included when a model file is present
in the command's file list.

### Files changed

| File | Change |
|------|--------|
| `knowledge/programs.yaml` | `phenix.map_correlations`: conditional resolution invariant |
| `tests/tst_audit_fixes.py` | 2 new S2g tests (113 total) |

---

## Version 112.40 (S2f — validation_cryoem Auto-Resolution)

Adds automatic resolution filling for `phenix.validation_cryoem`, which
requires an explicit `resolution=` argument and previously had to rely on the
user supplying it.

### Problem

`phenix.validation_cryoem` exited with "resolution not provided" when the
agent did not include a resolution argument. The session always has the
resolution from a prior `mtriage` run, but no invariant was wiring it through.

### Fix

Added a `requires_resolution` invariant to `phenix.validation_cryoem` in
`programs.yaml`. The command builder already handles this invariant for other
programs; the fix was purely a YAML addition.

### Files changed

| File | Change |
|------|--------|
| `knowledge/programs.yaml` | `phenix.validation_cryoem`: `requires_resolution: true` invariant |
| `tests/tst_audit_fixes.py` | 2 new S2f tests (111 total) |

---

## Version 112.36 (S2e — after_program Directive Correctly Suppresses Placement Probe)

Fixes a regression introduced by S2b (v112.33). The S2b fix made
`placement_uncertain` immune to the `model_is_placed` workflow preference
(which can be hallucinated by the LLM). However, it also inadvertently made
it immune to `after_program` directives, which are a reliable signal.

### Problem

`tst_workflow_state.py::test_model_placement_inferred_from_model_vs_data_directive`
failed with `AssertionError: Expected NOT xray_analyzed when model_vs_data requested`.

Scenario: user uploads `1aba.pdb + 1aba.mtz`, requests
`after_program=phenix.model_vs_data`. After S2b, `placement_uncertain=True`
because `has_placed_model_from_history=False` (no dock/refine in history).
This routes to `probe_placement → xray_analyzed` instead of the expected
`refine → xray_refine`.

### Root cause

S2b correctly distinguished two types of "placed" evidence:

| Source | Reliable? | Used by S2b |
|--------|-----------|-------------|
| `model_is_placed: True` (workflow_preferences) | No — LLM hallucination | Ignored by `placement_uncertain` ✓ |
| History/file evidence (dock_done, refined PDB, etc.) | Yes | `has_placed_model_from_history` ✓ |
| `after_program` in programs_requiring_placed | Yes — explicit user request | **Not distinguished — treated as unreliable** ✗ |

### Fix

Added a third placement signal: `has_placed_model_from_after_program`.

New method `_has_placed_model_from_after_program(files, directives)`:
- Returns True when `after_program` is in `programs_requiring_placed`
  (`phenix.refine`, `phenix.model_vs_data`, `phenix.polder`, etc.) AND a
  non-ligand, non-search-model PDB is present
- Returns False for all other cases (no directive, non-placement program, no PDB)

Added to `build_context()` as `context["has_placed_model_from_after_program"]`.

Added to `placement_uncertain` formula:
```python
context["placement_uncertain"] = (
    not context["has_placed_model_from_history"] and
    not context["has_placed_model_from_after_program"] and   # NEW
    not context["cell_mismatch"] and
    ...
)
```

### What is and isn't suppressed

| Directive | Suppresses probe? | Why |
|-----------|-------------------|-----|
| `workflow_preferences: {model_is_placed: True}` | No | LLM hallucination risk (S2b) |
| `stop_conditions: {after_program: phenix.model_vs_data}` | Yes | Explicit program request |
| `stop_conditions: {after_program: phenix.predict_and_build}` | No | Not in programs_requiring_placed |
| History: dock_done / refine_done | Yes | Objective evidence |

### Files changed

| File | Change |
|------|--------|
| `agent/workflow_engine.py` | New `_has_placed_model_from_after_program()` method; `"has_placed_model_from_after_program"` added to `build_context()`; added to `placement_uncertain` formula |
| `tests/tst_audit_fixes.py` | 3 new S2e tests (110 total) |

---

## Version 112.35 (S2d — skip_map_model_overlap_check for real_space_refine)

Adds `skip_map_model_overlap_check=True` as a permanent default for every
`phenix.real_space_refine` run.

### Problem

`phenix.real_space_refine` performs a box/symmetry compatibility check before
refining. When a docked model's CRYST1 record does not match the cryo-EM map
box (common for crystal-structure templates that have been docked), this check
raises a hard error and aborts the run before refinement even begins.

### Fix

Added to `knowledge/programs.yaml` under `phenix.real_space_refine`:

```yaml
defaults:
  skip_map_model_overlap_check: True
```

The `defaults` section maps directly to `key=value` arguments appended to every
command. This flag suppresses the overlap check for all RSR runs — no code
changes required.

### Files changed

| File | Change |
|------|--------|
| `knowledge/programs.yaml` | `defaults: skip_map_model_overlap_check: True` added to `phenix.real_space_refine` |
| `tests/tst_audit_fixes.py` | 1 new S2d test (107 total) |

---

## Version 112.34 (S2c — Crystal PDB to search_model Promotion for Cryo-EM Docking)

Fixes the second stage of the apoferritin AIAgent_104 crash. After S2b (v112.33)
correctly routes unplaced crystal-structure PDBs to `dock_model`, the agent was
immediately stuck because `phenix.dock_in_map` was blocked by a condition that
required AlphaFold post-processing output (`processed_model`). This version
makes the complete path from detection → docking → refinement work.

### Background

The live failure had two stages:

1. **Stage 1 (S2b):** A hallucinated `model_is_placed: True` directive suppressed
   both the cell-mismatch check and the placement-uncertainty probe, allowing
   `phenix.real_space_refine` to run directly against an unplaced model →
   symmetry/box mismatch crash. Fixed in v112.33.

2. **Stage 2 (S2c, this version):** After S2b, detection fires correctly and
   routes to `dock_model`. But `dock_in_map` requires `has_search_model=True`,
   and `1aew_A.pdb` (a plain crystal-structure PDB) is categorised as
   `unclassified_pdb → model` — never `search_model`. Three layered problems
   block docking from ever running.

### Three problems fixed

**Problem 1 — Wrong YAML condition in `dock_model` phase (`knowledge/workflows.yaml`)**

The `dock_model` phase was designed exclusively for the AlphaFold stepwise path
(`predict_and_build → process_predicted_model → dock_in_map`). Its condition
required `processed_model`, which is never set for a plain crystal-structure PDB.

```yaml
# Before:
- has: processed_model

# After:
- has_any: [search_model, processed_model]
```

`has_any` was already supported by `_check_conditions`; no engine changes needed.
The AlphaFold path is unaffected: `processed_predicted` bubbles to `search_model`,
so `has_search_model=True` still holds.

**Problem 2 — File never in `search_model` (`agent/workflow_engine.py`)**

The filename-based categoriser runs at session start with no workflow context. A
plain PDB like `1aew_A.pdb` cannot be distinguished from a positioned model by
name alone, so it lands in `unclassified_pdb → model`. At runtime, once context
confirms docking is needed, the new `_promote_unclassified_for_docking()` method
copies the file into `files["search_model"]`.

Promotion fires when **all** of these hold:
- `experiment_type == "cryoem"` (X-ray unaffected)
- `files["unclassified_pdb"]` non-empty
- `not has_placed_model_from_history` (no dock/refine in session history)
- `not has_search_model` (already populated → no-op)
- Any one of: `placement_uncertain` (Tier 3 pre-probe), `placement_probed AND
  needs_dock` (Tier 3 post-probe), or `cell_mismatch AND not from_history` (Tier 1)

The method never mutates its input dicts. `files["model"]` and
`files["unclassified_pdb"]` are left intact.

**Problem 3 — Promoted files discarded before reaching command builder (`agent/workflow_state.py`)**

`get_workflow_state()` returned the promoted `files` dict, but
`detect_workflow_state()` immediately overwrote `state["categorized_files"]`
with the original pre-promotion `files`. All downstream consumers (PLAN, BUILD,
`CommandContext`) read from `workflow_state["categorized_files"]` and would have
seen an empty `search_model` even after the fix to Problem 2.

```python
# Before (always overwrites with original):
state["categorized_files"] = files

# After (respects promoted files when present; falls back only when key absent):
state["categorized_files"] = state.get("categorized_files", files)
```

Note: `... or files` would be wrong here — Python treats `{}` as falsy and would
overwrite a legitimately empty dict. `.get(key, default)` only falls back when the
key is strictly absent from the dict.

### Data flow after fix

```
detect_workflow_state()
  files = _categorize_files()           # unclassified_pdb=[1aew_A.pdb], search_model=[]
  engine.get_workflow_state()
      build_context(files, ...)          # placement_uncertain=True OR cell_mismatch=True
      _promote_unclassified_for_docking  # files["search_model"]=[1aew_A.pdb]
      detect_phase()                     # dock_model (Tier 1 or Tier 3)
      get_valid_programs()               # dock_in_map: has_any([search_model✓]) + full_map✓
      return {"categorized_files": files, ...}   # promoted files in return dict
  state["categorized_files"] = state.get("categorized_files", files)  # kept ✓
→ PLAN reads categorized_files["search_model"] = [1aew_A.pdb]
→ BUILD / CommandContext: find_in_categories("search_model") → 1aew_A.pdb ✓
→ phenix.dock_in_map 1aew_A.pdb denmod_map.ccp4 resolution=1.9
```

### Complete cycle trace (apoferritin scenario)

| Cycle | Program | How |
|-------|---------|-----|
| 1 | `phenix.mtriage` | Map quality analysis |
| 2 | `phenix.resolve_cryo_em` | Phase 1.5: half-maps → full map |
| 3A | `phenix.dock_in_map` | Tier 1: cell mismatch (production, libtbx available); S2c 5c |
| 3B | `phenix.map_correlations` | Tier 3: no libtbx fallback, probe fires; S2c 5a |
| 4B | `phenix.dock_in_map` | Post-probe needs_dock; S2c 5b |
| 4A/5B | `phenix.real_space_refine` | Model docked, ready to refine ✓ |

### Files changed

| File | Change |
|------|--------|
| `knowledge/workflows.yaml` | `dock_model` `dock_in_map`: `has: processed_model` → `has_any: [search_model, processed_model]` |
| `agent/workflow_engine.py` | New `_promote_unclassified_for_docking()` method; call site after `build_context`; `"categorized_files"` added to return dict |
| `agent/workflow_state.py` | Line 1187: `.get("categorized_files", files)` |
| `agent/graph_nodes.py` | PERCEIVE log for S2c promotion (shows which files were promoted) |
| `tests/tst_audit_fixes.py` | 6 new S2c tests; missing `__main__` runner block restored; SKIP guards added to 32 pre-existing tests that lacked them (no behaviour change, prevents false failures in non-PHENIX environments) |

### Tests

6 new tests in `tst_audit_fixes.py` (106 total, all passing):

| Test | Verifies |
|------|----------|
| `test_s2c_promotion_fires_when_placement_uncertain` | Condition 5a: Tier 3 pre-probe path |
| `test_s2c_promotion_fires_when_probe_says_needs_dock` | Condition 5b: Tier 3 post-probe path |
| `test_s2c_promotion_fires_when_cell_mismatch` | Condition 5c: Tier 1 production path |
| `test_s2c_no_promotion_when_placed_by_history` | Guard: `has_placed_model_from_history` blocks promotion |
| `test_s2c_no_promotion_for_xray` | Guard: X-ray experiment type blocked |
| `test_s2c_categorized_files_propagates_through_get_workflow_state` | Structural fix: promoted files reach command builder |

---

## Version 112.33 (S2b — Directive-Immune Placement Uncertainty)

Fixes the first stage of the apoferritin AIAgent_104 crash. The directive
extractor hallucinated `model_is_placed: True` for "solve the structure",
which triggered a suppression cascade that bypassed both the Tier 1 cell-mismatch
check and the Tier 3 placement probe — allowing `phenix.real_space_refine` to
run directly against an unplaced crystal model in a cryo-EM map.

### Root cause

Two expressions in `workflow_engine.py` used `has_placed_model` (which respects
the `model_is_placed` directive) where they should have used
`has_placed_model_from_history` (directive-immune):

1. **S1 short-circuit** — skips the expensive `phenix.show_map_info` subprocess
   once placement is known. A wrong directive zeroed out `cell_mismatch` before
   Tier 1 could act on it.

2. **`placement_uncertain` formula** — the Tier 3 probe gate. A wrong directive
   set this to `False`, suppressing the probe entirely.

### Fix

Both expressions changed from `has_placed_model` to
`has_placed_model_from_history`. The S1 short-circuit and the probe gate are now
immune to directive mistakes. Only history/file evidence (dock/refine completed,
docked PDB present) can suppress these safety checks.

### Files changed

| File | Change |
|------|--------|
| `agent/workflow_engine.py` | S1 short-circuit: `has_placed_model` → `has_placed_model_from_history`; `placement_uncertain` formula: same change |

### Tests

Covered by existing S2 and R2/R3 tests in `tst_audit_fixes.py`.

---

## Version 112.32 (Probe-Based Model Placement Detection)

Adds a three-tier decision framework that determines whether a supplied atomic
model is already placed in the unit cell / map before choosing between
refinement and MR/docking.  Previously the agent had a blind spot for generic
PDB files with no history and no positioning metadata.

### Background

When a user supplies `model.pdb + data.mtz` (or `model.pdb + map.ccp4`) with
no session history, the agent must decide: is the model already placed (→
refine) or does it need to be placed first (→ MR / docking)?  The old
heuristics relied on file subcategory (`positioned`) or history flags —
neither of which are set for a freshly uploaded PDB.

### Three-tier framework

**Tier 1 — Unit cell comparison (free, instant)**
- Reads CRYST1 from PDB (`read_pdb_unit_cell`) and cell from MTZ/map
  (`read_mtz_unit_cell`, `read_map_unit_cells`)
- Compatible within 5% → falls through to Tier 2
- Incompatible → model cannot be placed here → immediately routes to MR / docking
- Fail-safe: any parse failure returns `False` (no mismatch declared)

**Tier 2 — Existing heuristics (`_has_placed_model`)**
- History flags, file subcategory, user directive
- Clear evidence → done; still ambiguous → Tier 3

**Tier 3 — Diagnostic probe (one program cycle)**
- X-ray: runs `phenix.model_vs_data`; R-free < 0.50 → placed
- Cryo-EM: runs `phenix.map_correlations`; CC > 0.15 → placed
- Probe never repeats: `placement_probed` flag persists in history
- Probe result is detected positionally (first occurrence before any
  refine/dock cycle) with no schema change to history entries

### New module: `agent/placement_checker.py`

Public API:

| Function | Purpose |
|---|---|
| `read_pdb_unit_cell(path)` | Parse CRYST1 line → 6-tuple or None |
| `read_mtz_unit_cell(path)` | iotbx.mtz → 6-tuple, falls back to mtzdump |
| `read_map_unit_cells(path)` | `phenix.show_map_info` → full-map and present-portion cells |
| `cells_are_compatible(a, b, tolerance=0.05)` | Fractional comparison; None → True |
| `check_xray_cell_mismatch(pdb, mtz)` | True only when both readable AND incompatible |
| `check_cryoem_cell_mismatch(pdb, map)` | True only when both readable AND model matches neither map cell |

### Changes to `agent/workflow_engine.py`

- `build_context()` gains four keys: `cell_mismatch`, `placement_probed`,
  `placement_probe_result`, `placement_uncertain`
- `placement_uncertain` is `True` when: model + data present, no history evidence,
  no directive, not predicted, no cell mismatch, probe not yet run
- When `placement_probe_result == "placed"`, `build_context` overrides
  `has_placed_model = True` so normal refine routing takes over
- `_check_cell_mismatch()` private method wires Tier 1 into context building
- `_detect_xray_phase()` / `_detect_cryoem_phase()` each gain three routing
  blocks: Tier 1 mismatch → MR/dock, Tier 3 result → MR/dock or fall-through,
  Tier 3 uncertain → `probe_placement` phase
- `probe_placement` added to both `XRAY_STATE_MAP` and `CRYOEM_STATE_MAP`

### Changes to `agent/workflow_state.py`

- `_analyze_history` initialises `placement_probed=False` and
  `placement_probe_result=None`
- Post-loop pass detects probe by position: `model_vs_data` or
  `map_correlations` found before the first refine/dock cycle
- Only successful cycles contribute (failed runs ignored)
- `cc_volume` used as fallback if `cc_mask` absent

### Changes to `knowledge/workflows.yaml`

- `probe_placement` phase added to both `xray` and `cryoem` workflows
  (positioned between `analyze` and `obtain_model`)
- X-ray probe: `phenix.model_vs_data`, transitions `if_placed → refine`,
  `if_not_placed → molecular_replacement`
- Cryo-EM probe: `phenix.map_correlations`, transitions `if_placed → refine`,
  `if_not_placed → dock_model`

### Changes to `knowledge/programs.yaml`

- `phenix.map_correlations`: added `done_tracking` (was missing entirely —
  pre-existing bug where running it in the validate phase never set
  `validation_done`)
- `phenix.model_vs_data`: added clarifying comment explaining phase-aware
  override (flag unchanged: `validation_done`)

### New tests (34 across all steps, in `tests/tst_audit_fixes.py`)

| Category | Count | What they verify |
|---|---|---|
| R1 | 11 | `placement_checker.py` unit cell parsing and comparison |
| R2 | 11 | `workflow_engine.build_context()` new keys and `placement_uncertain` logic |
| R3 | 12 | Phase routing: probe offered, Tier 1 mismatch bypass, probe results route correctly, probe not re-run |
| R3-extra | 10 | Edge cases: failed probe ignored, CC fallback fields, cryo-EM paths, `build_context` override, `placement_uncertain` clears |

### Directive override protection (S2 fixes — applied after log analysis)

**Root cause identified from runtime log:** the directive extractor LLM set
`model_is_placed: True` from the advice "solve the structure" — a case the
prompt explicitly said should NOT trigger that flag. This cascaded:
`_has_placed_model()` returned True → Tier 1 routing checked
`cell_mismatch AND NOT has_placed_model` → False (model "appeared placed") →
skipped docking → `phenix.real_space_refine` failed with
"Symmetry and/or box (unit cell) dimensions mismatch".

**S2 Fix A — Directive extractor prompt (`agent/directive_extractor.py`)**
- `model_is_placed` is now labelled HIGH-PRECISION: when in doubt, do NOT set it.
- Explicit DO NOT list expanded: "solve the structure", "refine this model",
  "run refinement", "fit a ligand", PDB + cryo-EM map without explicit placement
  confirmation, generic/ambiguous goals.
- Added prominent note: a PDB alongside cryo-EM maps always requires docking first.

**S2 Fix B — `has_placed_model_from_history` context key (`agent/workflow_engine.py`)**
- New `_has_placed_model_from_history()` method — identical logic to
  `_has_placed_model()` but intentionally ignores directives; returns True only
  from history flags (`dock_done`, `phaser_done`, `autobuild_done`,
  `predict_full_done`, `refine_done`) and positioned file subcategories.
- New `has_placed_model_from_history` key in `build_context()` output.
  `has_placed_model` remains the directive-inclusive version for program gating.

**S2 Fix C — Tier 1 routing uses `has_placed_model_from_history`**
- Both `_detect_xray_phase` (MR) and `_detect_cryoem_phase` (dock) changed from
  `not context["has_placed_model"]` to `not context.get("has_placed_model_from_history")`.
- A directive claiming the model is placed cannot override a definitive cell-dimension
  mismatch. Only concrete history evidence (dock ran, phaser ran, etc.) suppresses the check.

**S2 Fix D — S1 short-circuit also uses `has_placed_model_from_history`**
- The subprocess short-circuit (skip `phenix.show_map_info` after placement resolved)
  now checks `has_placed_model_from_history` instead of `has_placed_model`.
  A wrong directive no longer suppresses the check on subsequent cycles either.

### New tests (S2 category, 10 tests in `tests/tst_audit_fixes.py`)

| Test | What it covers |
|---|---|
| `s2_has_placed_model_from_history_method_exists` | Method present on WorkflowEngine |
| `s2_from_history_false_when_only_directive` | Directive cannot fool `_has_placed_model_from_history` |
| `s2_from_history_true_when_dock_done` | `dock_done` history → True |
| `s2_context_has_placed_from_history_key` | Key present in `build_context()` output |
| `s2_directive_model_is_placed_does_not_suppress_cell_mismatch` | Core routing fix: cell_mismatch → dock despite directive |
| `s2_history_placed_does_suppress_cell_mismatch` | `dock_done` history legitimately suppresses re-dock |
| `s2_xray_tier1_uses_from_history` | X-ray MR routing: directive cannot block Tier 1 |
| `s2_short_circuit_uses_from_history_not_directive` | Source-level: short-circuit references correct key |
| `s2_directive_prompt_stronger_do_not_set` | Prompt contains explicit DO NOT cases |
| `s2_full_cryoem_stack_routes_to_dock_not_rsr` | **Regression test for the apoferritin bug** |

### Polish fixes (applied after initial implementation review)

Four issues discovered during code review and corrected before release.

**S1 Fix 1 — YAML validator warnings (`agent/yaml_tools.py`)**
- `if_placed` and `if_not_placed` were not in `valid_transition_fields`, generating
  4 spurious "unknown transition field" warnings every time `_validate_workflows()` ran.
- Added both keys to the set.

**S1 Fix 2 — Redundant import (`agent/workflow_state.py`)**
- The probe detection block used `import re as _re2` inside the loop body.
  `re` is already imported at module level.
- Replaced `_re2.search(...)` with `re.search(...)`.

**S1 Fix 3 — Missing local import fallback (`agent/workflow_engine.py`)**
- `_check_cell_mismatch` only tried the `libtbx.langchain.agent.placement_checker`
  import path; any environment without the libtbx namespace (tests, local dev) would
  silently return `False` without trying the bare `agent.placement_checker` path.
- Added a second `except ImportError` branch matching the pattern used everywhere
  else in the codebase.

**S1 Fix 4 — Subprocess per cycle (`agent/workflow_engine.py`)**
- `_check_cell_mismatch` ran `phenix.show_map_info` as a subprocess on every
  `build_context()` call.  For cryo-EM workflows with many cycles this is wasteful:
  once placement is resolved the check can never change the outcome.
- Added a post-processing short-circuit in `build_context`: after the context dict
  is fully built, if `has_placed_model=True` **or** `placement_probed=True`,
  `cell_mismatch` is forced to `False`.  The check still runs on the first cycle
  when placement is genuinely unknown.  The routing conditions
  (`cell_mismatch AND not has_placed_model`) provide a second safety net.

### New tests (S1 category, 10 tests in `tests/tst_audit_fixes.py`)

| Test | Fix covered |
|---|---|
| `s1_yaml_validator_no_if_placed_warnings` | Fix 1: no warnings from probe_placement transitions |
| `s1_yaml_validator_if_placed_is_in_valid_set` | Fix 1: if_placed/if_not_placed in source |
| `s1_no_redundant_import_re_in_probe_block` | Fix 2: no _re2 alias, module-level re used |
| `s1_probe_re_fallback_still_works` | Fix 2: regex fallback still parses r_free from result text |
| `s1_local_import_fallback_in_check_cell_mismatch` | Fix 3: local path fallback present |
| `s1_placement_checker_importable_locally` | Fix 3: all public functions importable without libtbx |
| `s1_cell_mismatch_short_circuits_when_placed` | Fix 4: False when has_placed_model=True |
| `s1_cell_mismatch_short_circuits_when_probed` | Fix 4: False when placement_probed=True |
| `s1_cell_mismatch_not_short_circuited_first_cycle` | Fix 4: check active on first cycle |
| `s1_short_circuit_order_before_probe_override` | Fix 4: short-circuit before probe-result override |

### Files modified

- `agent/placement_checker.py` — **NEW**
- `agent/workflow_engine.py` — plus S1 fix 3 (import fallback), fix 4 (short-circuit)
- `agent/workflow_state.py` — plus S1 fix 2 (import cleanup)
- `agent/yaml_tools.py` — S1 fix 1 (transition field set)
- `knowledge/workflows.yaml`
- `knowledge/programs.yaml`
- `tests/tst_audit_fixes.py`

---

## Version 112.31 (Session Management, Resume Enhancement, Completed-Workflow Extension)

### P1: Session management keywords populate `self.result` (Fix 27)

**`display_and_stop` / `remove_last_n` left `self.result` unset — GUI calls failed**
- When the agent exited via `_handle_session_management()`, it returned without
  populating `self.result`. Any downstream call to `get_results()` or
  `get_results_as_JSON()` raised `AttributeError`.
- Fix: After session_tools operations complete, `_handle_session_management()`
  now loads the `AgentSession`, calls `_finalize_session(skip_summary=True)`,
  and builds a standard `group_args` result identical to a normal run's result.
  The GUI receives session history, cycle count, and summary with no special cases.
- `_finalize_session` gained a `skip_summary` kwarg (default `False`) that
  suppresses the `_generate_ai_summary()` LLM call — unnecessary since no new
  cycles ran during a display/remove operation.
- Files: `programs/ai_agent.py`

### P3: `get_results()` safe before `run()` (Fix 28)

**`AttributeError: 'Program' object has no attribute 'result'`**
- Any code path that called `get_results()` before `run()` completed — or on
  any early-exit path that bypassed result assignment — raised AttributeError.
- Fix: `run()` assigns `self.result = None` as its very first statement;
  `get_results()` uses `getattr(self, 'result', None)` as a defensive fallback.
- Files: `programs/ai_agent.py`

### P4: `restart_mode` auto-set on session management params (Fix 29)

**`display_and_stop` / `remove_last_n` required explicit `restart_mode=resume`**
- Both session management parameters operate on an existing session directory,
  which requires resume semantics. Forgetting `restart_mode=resume` silently
  cleared the session's log_directory instead of reusing it.
- Fix: At the start of `run()`, before `set_defaults()`, if either parameter
  is set, `restart_mode` is automatically forced to `'resume'`. The
  `display_and_stop` Phil choice default `'None'` (string) is handled
  correctly — the guard checks `!= 'None'` rather than Python truthiness.
- Files: `programs/ai_agent.py`

### Q1: Extending a completed workflow with new `project_advice` (Fix 30)

**Resuming after workflow completion with new advice was silently ignored**

**Scenario:** Agent finishes a ligand-protein complex (xtriage → phaser → refine ×3 →
ligandfit → pdbtools → molprobity). User resumes with
`project_advice="also run polder on the ligand chain B residue 100"`.
Previously the agent replied "workflow complete, nothing to do" and stopped
immediately — the new advice was never acted on.

**Root cause — two walls:**
1. **Wall 1 — AUTO-STOP in PLAN** (already fixed before this version):
   `metrics_trend.should_stop` would terminate before the LLM planned.
   The `advice_changed` flag (set by `_preprocess_user_advice` on hash
   mismatch) suppressed this for one cycle.
2. **Wall 2 — `valid_programs = ['STOP']` in PERCEIVE** (this fix):
   Once the workflow phase was `complete`, `detect_phase` returned the
   terminal phase, and `get_valid_programs` immediately returned `['STOP']`
   before Wall 1's suppression logic ran. The LLM was handed a program
   menu that only said STOP, so it couldn't choose polder even with
   Wall 1 down.

**Fix** (`agent/graph_nodes.py`, PERCEIVE node — 20 lines):
```python
# When advice_changed=True and phase='complete', step back to 'validate'
if (session_info.get("advice_changed") and
        workflow_state.get("phase_info", {}).get("phase") == "complete"):
    _new_valid = engine.get_valid_programs(exp, {"phase": "validate"}, ctx)
    workflow_state["valid_programs"] = _new_valid
    workflow_state["phase_info"] = {"phase": "validate", "reason": "advice_changed"}
```

The `validate` phase contains exactly the right program menu for
post-completion follow-up: `phenix.polder`, `phenix.molprobity`,
`phenix.model_vs_data`, `phenix.map_correlations`, plus `STOP` so the
LLM can still exit if the advice requires no action. After one successful
cycle, `advice_changed` is cleared and normal AUTO-STOP behaviour resumes.

**Complete event flow on resume with new advice:**
```
1. _preprocess_user_advice()
     new hash ≠ stored hash → session.data["advice_changed"] = True

2. PERCEIVE (Q1 fix)
     phase == 'complete' AND advice_changed
     → valid_programs = ['phenix.polder', 'phenix.molprobity',
                         'phenix.model_vs_data', 'phenix.map_correlations',
                         'STOP']
     → phase_info['phase'] set to 'validate'

3. PLAN (Wall 1 fix, pre-existing)
     metrics_trend.should_stop AND advice_changed
     → AUTO-STOP suppressed for this cycle

4. LLM
     sees new advice + validate-phase program menu → chooses phenix.polder

5. Post-cycle cleanup
     advice_changed = False
     → next cycle: normal termination logic resumes
```

**Notable behaviour:** `phenix.polder` intentionally lacks `strategy: run_once`
in `programs.yaml` — different residues and ligands may each need separate
omit maps. `polder_done=True` therefore does NOT block polder from reappearing
in `valid_programs`, allowing additional selections on subsequent resumes.

- Files: `agent/graph_nodes.py`

### Tests added (`tests/tst_audit_fixes.py`)

**P1/P3/P4 tests (20 tests)** covering session_tools functions, real method
extraction via `_build_agent_stub`, `get_results()` safety, and restart_mode
auto-set. See previous session-management context for full list.

**Q1 tests (9 tests):**
- `test_q1_complete_phase_has_only_stop`: baseline — `complete` phase → `['STOP']`
- `test_q1_validate_phase_includes_polder`: validate phase contains `phenix.polder`
- `test_q1_advice_changed_steps_back_to_validate`: core logic — step-back adds polder
- `test_q1_no_step_back_when_advice_unchanged`: unchanged advice keeps `complete` phase
- `test_q1_polder_reruns_allowed_when_already_done`: `polder_done=True` does NOT block re-run
- `test_q1_cryoem_complete_phase_steps_back`: logic is experiment-type agnostic
- `test_q1_graph_nodes_perceive_mutates_state`: end-to-end state mutation test
- `test_q1_advice_cleared_after_one_cycle`: `advice_changed` clears after one cycle
- `test_q1_step_back_does_not_apply_outside_complete`: guard only fires on `complete` phase

**Total tests: 74 (was 65 in v112_30)**

---

## Version 112.14 (Systematic Audit — Categories I, J, E, G, H)

### I1: max_refine_cycles produces bare STOP instead of controlled landing (Fix 21)

**`_apply_directives` returned `["STOP"]` when refinement limit reached**
- When `max_refine_cycles` was reached, the workflow engine stripped
  refinement programs and returned `["STOP"]`, terminating the workflow
  without validation. This left the user with no quality report.
- Fix: After removing refinement programs, `_apply_directives` now
  injects the validate-phase programs appropriate to the experiment type
  (`phenix.molprobity`, `phenix.model_vs_data`, `phenix.map_correlations`
  for X-ray; `phenix.molprobity`, `phenix.validation_cryoem`,
  `phenix.map_correlations` for cryo-EM), then appends STOP so the user
  can still exit immediately if desired.
- Also fixed: cryoem path was reading `context["refine_count"]` to check
  the limit, but cryo-EM refinement is counted in `context["rsr_count"]`.
  Now uses `rsr_count` for cryoem, `refine_count` for xray.
- Design note: `after_program` continues to produce STOP only (it is an
  explicit, unconditional stop). The validate-injection only applies to
  `max_refine_cycles` (a "limit" directive, not a "stop here" directive).
- Files: `agent/workflow_engine.py`

### J2: `_is_failed_result` false-positives on bare ERROR variants (Fix 22)

**Patterns `'ERROR '`, `': ERROR'`, `'ERROR:'` matched non-fatal log text**
- Phenix logs routinely contain strings like "Error model parameter",
  "Expected errors: 0", "No ERROR detected". All of these matched the
  broad `ERROR ` / `ERROR:` / `: ERROR` patterns, causing legitimate
  runs to be classified as failed and their done flags suppressed.
- Priority order for failure detection (per spec J2):
  1. Exit code (handled at the shell layer, before `_is_failed_result`)
  2. Output file check
  3. Log text with specific Phenix terminal phrases
- Fix: Removed the three generic `ERROR` patterns. Retained the seven
  Phenix-specific terminal failure signatures: `FAILED`, `SORRY:`,
  `SORRY `, `*** ERROR`, `FATAL:`, `TRACEBACK`, `EXCEPTION`.
  These cover all real Phenix failure modes without matching non-fatal text.
- Files: `agent/workflow_state.py`

### J5: Zombie state detection — stale done flags block re-execution (Fix 23)

**Missing output files left done flags True, preventing re-run**
- When the agent crashed mid-cycle or the user deleted output files, the
  history record retained `done_flag=True`. The phase detector saw
  `done=True` and skipped the program, but file-based flags
  (`has_full_map`, `has_placed_model`) were False because no file was
  found. The workflow became stuck.
- Fix: `_clear_zombie_done_flags(history_info, available_files)` checks
  each done flag against its expected output file pattern. If the flag
  is True but no matching file exists in `available_files`, it clears
  the flag (and associated file flags) in-memory without modifying
  history. Diagnostic messages are emitted to PERCEIVE.
- Programs covered:
  - `resolve_cryo_em_done` → `denmod_map.ccp4` → also clears `has_full_map`
  - `predict_full_done` → `*_overall_best.pdb` → also clears `has_placed_model`
  - `dock_done` → `*_docked.pdb` → also clears `has_placed_model`
  - `refine_done` → `*_refine_001.pdb` → decrements `refine_count`
  - `rsr_done` → `*_real_space_refined*.pdb` → decrements `rsr_count`
- Files: `agent/workflow_state.py`

### E1: xtriage resolution regex extracts 50.0 instead of 2.3 (Fix 24)

**Dash separator in "50.00 - 2.30" format not handled by skip group**
- The pattern `(?:[0-9.]+\s+)?` was designed to skip the low-resolution
  limit and capture the high-resolution limit. But xtriage formats the
  range as `50.00 - 2.30` (space-dash-space), not `50.00 2.30` (space
  only). With the dash present the optional skip group backtracks, and
  the capture group then matches `50.00` — the wrong value. `pick_min`
  across all lines then returned 50.0 instead of 2.3.
- Fix: Changed `(?:[0-9.]+\s+)?` to `(?:[0-9.]+\s*[-]\s*)?`. The skip
  group now explicitly handles the dash separator. The pattern still
  handles `Resolution: 1.80` (no range, skip group does not fire).
  The negative lookbehind anchors prevent "Completeness in resolution
  range: 1" from matching (J2-era fix retained).
- Files: `knowledge/programs.yaml` (`phenix.xtriage` `resolution` pattern)

### E1: real_space_refine map_cc returns first cycle instead of final (Fix 25)

**`extract: first` (default) captured the initial, worst map_cc value**
- RSR emits one `CC_mask =` line per macro-cycle. With `extract: first`
  the initial (lowest) map_cc was reported rather than the final (best).
  This caused the agent to incorrectly judge model quality as poor and
  over-refine.
- Also fixed: the pattern `CC_mask\s*[=:]\s*([0-9.]+)` was narrower
  than the standard map_cc pattern used by all other programs. Broadened
  to `(?:CC_mask|Map-CC|Model vs map CC)\s*[:=]?\s*([0-9.]+)` for
  consistency.
- Files: `knowledge/programs.yaml` (`phenix.real_space_refine` `map_cc` spec)

### G1: holton_geometry_validation defined but not in any workflow phase (Verified)

`phenix.holton_geometry_validation` is registered in `programs.yaml`
with `done_tracking` but deliberately not in any `workflows.yaml` phase.
Added an audit comment to clarify the intentional status and document
how to activate it (add to the validate phase in `workflows.yaml`).
Files: `knowledge/programs.yaml`

### H1/H3: STATE_MAP comments clarified (Documentation)

- `cryoem_has_model`: The state string assigned to `check_map` and
  `optimize_map` is a legacy misnomer — no model exists at that point.
  No behavioral code gates on this string; `phase_info["phase"]` is
  used for all internal decisions. Comment added.
- `validate` shares state string with `refine` (`xray_refined` /
  `cryoem_refined`): This is intentional for external API compatibility.
  Internal code always uses `phase_info["phase"]` to distinguish them.
  Comment added.
- Files: `agent/workflow_engine.py`

### J5d: Zombie state diagnostics silently discarded (Fix 26)

**`_clear_zombie_done_flags()` return value was ignored**
- `detect_workflow_state()` called `_clear_zombie_done_flags()` but did not
  capture the return value. The done-flag clearing worked correctly (in-memory
  modification), but the diagnostic messages explaining *why* a previously
  "done" program reappeared in `valid_programs` were silently lost.
- Without these diagnostics, users and developers had no way to know that a
  zombie state was detected and resolved — making crash/restart scenarios very
  confusing to debug.
- Fix: captured the return value as `zombie_diagnostics` and added it to the
  state dict under key `"zombie_diagnostics"` (present only when non-empty).
  The PERCEIVE node now logs each diagnostic prefixed with
  `"PERCEIVE: ZOMBIE STATE — "`.
- Files: `agent/workflow_state.py`, `agent/graph_nodes.py`

### Regression tests added: `tests/tst_audit_fixes.py`

27 tests covering all bugs found and fixed in this audit session:

- **J2** (3 tests): `_is_failed_result` true positives, false positive
  elimination, done-flag blocking on failure
- **J5** (5 tests): Zombie detection for resolve_cryo_em, refine, dock;
  preservation when output exists; rsr_count decrement
- **E1/E2** (6 tests): xtriage resolution dash-separator, completeness
  anchor, simple format, multiple ranges; RSR map_cc last-cycle and
  pattern variants
- **I1** (2 tests): X-ray controlled landing (validate + STOP injected);
  cryo-EM rsr_count used (not refine_count)
- **I2** (1 test): after_program → STOP only (no validate injection)
- **I1b** (2 tests): xray and cryoem complete phase → [STOP] after validation_done=True (clean-termination path)
- **J5 zombie surfacing** (2 tests): `zombie_diagnostics` present in state when zombie cleared; absent for clean state
- **YAML spec** (4 tests): xtriage pick_min, RSR map_cc extract:last,
  RSR clashscore extract:last, polder requires_selection invariant

Tests registered in `tests/run_all_tests.py` as "Audit Fix Regressions".



### Explicit program injection overrides multi-step directives (Fix 19)

**`_detect_explicit_program_request` hijacks multi-step workflows**
- When user says "run mtriage, resolve_cryo_em, map_symmetry", the
  directive system correctly parses ordering (start_with_program,
  after_program). But `_detect_explicit_program_request` scans the raw
  text, returns ONE program (e.g., map_symmetry), and injects
  "**IMPORTANT: run phenix.map_symmetry**" into guidelines — overriding
  the directive ordering. The LLM then picks map_symmetry instead of
  resolve_cryo_em (the correct next step).
- This cascades: map_symmetry fails (no full_map, only half maps),
  then resolve_cryo_em runs as a fallback without LLM-guided parameters.
- Fix: Skip explicit program injection when directives contain
  multi-step workflow info (`start_with_program`, `after_program`, or
  `prefer_programs` with 2+ entries). Applied to both the main
  `_query_agent_for_command` path and the retry/duplicate handler.
- Files: `programs/ai_agent.py`

### resolve_cryo_em missing input_priorities (Fix 19b)

**Category-based file selection for half_map slot**
- `phenix.resolve_cryo_em` had empty `input_priorities`, forcing the
  command builder to use extension-only matching for the `half_map`
  slot. This could select sharpened or optimized maps instead of actual
  half maps when multiple .ccp4 files are available.
- Added `input_priorities.half_map` with `categories: [half_map]` and
  `exclude_categories: [full_map, optimized_full_map]`.
- Files: `knowledge/programs.yaml`

### run_once strategy check fix (Fix 17)

**Three broken `tracking.get("run_once")` checks in workflow_engine.py**
- The YAML uses `strategy: "run_once"` but all three checks in
  `workflow_engine.py` used `tracking.get("run_once")` which always
  returned None. This meant run_once programs (map_symmetry, mtriage,
  xtriage) were never filtered from valid_programs.
- Fixed all three locations to check
  `tracking.get("strategy") == "run_once" or tracking.get("run_once")`
  for backward compatibility.
- Additionally, `_apply_directives` could re-add already-done programs
  via `start_with_program`, `program_settings`, and `after_program`
  directives. Added `_is_program_already_done()` helper that checks
  the YAML done_tracking config. All three directive paths now skip
  programs whose done flag is already set.
- Files: `agent/workflow_engine.py`

### Sharpened map mis-categorized as half_map (Fix 18)

**half_map excludes for sharpened/optimized maps**
- `emd_XXXXX_half_map_N_box_sharpened.ccp4` (output of map_sharpening)
  matched `half_map` pattern `*half*`, causing mtriage to use it as
  `half_map=` instead of `full_map=`.
- Added excludes `*sharpened*`, `*sharpen*`, `*optimized*` to the
  `half_map` category in `file_categories.yaml`. Sharpened maps now
  fall through to `full_map` via the map parent category, where
  mtriage's `input_priorities.full_map.categories: [full_map, map]`
  picks them up correctly.
- Files: `knowledge/file_categories.yaml`

### forced_program not enforced in plan node (Fix 19)

**LLM could override multi-step workflow ordering**
- `workflow_engine` sets `forced_program` from `after_program` directive
  (e.g., for "run mtriage, resolve_cryo_em, and map_symmetry"), but the
  plan node never enforced it. The LLM could freely pick any valid
  program, ignoring the directive ordering.
- Added forced_program enforcement block in plan node: when
  `forced_program` is set and valid, the LLM's choice is overridden.
- Also fixed explicit_program injection in perceive: when
  `forced_program` is set from directives, `explicit_program` (from
  `_detect_explicit_program_request` text scanning) is no longer
  injected into valid_programs, preventing conflicting LLM hints.
- Files: `agent/graph_nodes.py`

### map_symmetry offered without full map (Fix 20)

**map_symmetry should not be valid when only half maps exist**
- map_symmetry's input_priorities exclude half_map from the map slot,
  so it always fails to build when only half maps are available. But
  its workflow condition only checked `not_done: map_symmetry`, so it
  appeared in valid_programs and the LLM would pick it.
- Added `has: non_half_map` condition to map_symmetry in workflows.yaml.
- Added composite context key `has_non_half_map` in workflow_engine.py
  that checks `set(map files) - set(half_map files)`. This correctly
  becomes True after map_sharpening produces a sharpened map (which is
  in `map` but not `half_map`), even though the sharpened filename
  contains "half" and doesn't match `full_map`.
- Files: `agent/workflow_engine.py`, `knowledge/workflows.yaml`

### File discovery and filtering (Fixes 14-15)

**Companion file discovery** (`graph_nodes._discover_companion_files`)
- After `phenix.refine`: discovers map coefficients (`refine_NNN.mtz`) and
  refined model (`refine_NNN.pdb`) from `_data.mtz` prefix. Handles both
  bare (`refine_001.mtz`) and `_001` (`refine_001_001.mtz`) naming.
- After `phenix.autobuild`: discovers `overall_best.pdb` when only
  `overall_best_refine_data.mtz` is tracked by the client.
- After `phenix.pdbtools`: scans sibling `sub_*_pdbtools/` directories in
  the agent directory for `*_with_ligand.pdb` output files.
- Defense-in-depth: also added `session._find_missing_outputs()` as a
  second discovery layer for session-tracked output files.

**Intermediate file filtering** (`graph_nodes._filter_intermediate_files`)
- Filters files from ligandfit's internal `TEMP0/` directories and files
  with `EDITED_` or `TEMP_` prefixes before categorization.
- Prevents intermediate files from being selected as model inputs.
- Also added `EDITED*` and `TEMP*` exclusions to `unclassified_pdb`
  category in `file_categories.yaml`.
- Files: `agent/graph_nodes.py`, `agent/session.py`,
  `knowledge/file_categories.yaml`

### Refinement loop enforcement (Fix 13)

**At-target actively removes refine from valid programs**
- When `_is_at_target` returns True (hopeless R-free > 0.50 after 1+ cycles,
  or hard limit of 3+ cycles), `phenix.refine` and `phenix.real_space_refine`
  are now explicitly **removed** from valid programs in both `validate` and
  `refine` phases. Previously only prevented adding as supplement.
- `STOP` added to valid programs when at target.
- Exception: `needs_post_ligandfit_refine` always allows refinement (model
  changed after ligand fitting, re-refinement is scientifically required).
- Files: `agent/workflow_engine.py`

### Command validation fix (Fix 15)

**Output file arguments excluded from input validation**
- `output.file_name=X.pdb`, `output.prefix=Y`, etc. are now stripped from
  commands before extracting file references for validation. Previously
  `output.file_name=model_with_ligand.pdb` was treated as an input file
  reference and rejected as "not found in available_files".
- Files: `agent/graph_nodes.py`

### best_files excluded category check (Fix 16)

**Prevents ligand fragments from being used as refine model input**
- `best_files["model"]` is now checked against the program's
  `exclude_categories` before being applied as a model override. If the
  best model (e.g., `ligand_fit_1.pdb`) is in an excluded category
  (e.g., `ligand_fit_output` → `ligand`), it is skipped with a log message.
- Applied to both pre-population and LLM override paths in
  `command_builder.py`.
- Files: `agent/command_builder.py`

### MR-SAD phaser condition (Fix 11)

**Composite `has_model_for_mr` context key**
- Added `has_model_for_mr` in `workflow_engine.py` that checks both `model`
  and `search_model` file categories. Phaser condition in `workflows.yaml`
  changed from `has: model` to `has: model_for_mr`.
- Ensures phaser is available when user provides a dedicated search model
  (`search_model.pdb`) that categorizes as `search_model`, not `model`.
- Files: `agent/workflow_engine.py`, `knowledge/workflows.yaml`

### Cryo-EM experiment type inference (Fix 12)

**Advice preprocessor now infers experiment type from file extensions**
- Added experiment type inference rules to the advice preprocessing LLM
  prompt: `.mtz/.sca/.hkl` → X-ray, `.map/.mrc/.ccp4` → cryo-EM,
  half-maps → cryo-EM, `.pdb + .map` → cryo-EM refinement.
- Cosmetic fix only: actual workflow engine already correctly detected
  cryo-EM from file categories. This fixes the user-facing advice text.
- Files: `agent/advice_preprocessor.py`

### Input priority improvements

- `phenix.pdbtools` protein: added `autobuild_output` to
  `prefer_subcategories`, `EDITED` to `exclude_patterns`,
  `overall_best` to `priority_patterns`.
- `phenix.refine` model: `with_ligand` is first in `prefer_subcategories`,
  ensuring the combined protein+ligand model is selected for post-pdbtools
  refinement.
- Files: `knowledge/programs.yaml`

### Test coverage

- 33 new tests in `tests/tst_v112_13_fixes.py` covering companion file
  discovery (5), intermediate filtering (3), file categorization (5),
  phaser model_for_mr (3), output validation (3), program priorities (4),
  end-to-end post-pdbtools selection (2), combine_ligand phase (1),
  sharpened map categorization (2), run_once done-flag config (2),
  map_symmetry condition (1), non_half_map context key (2).
- Total: 29/35 passing (6 pre-existing libtbx import failures).

## Version 112.12 (February 2025)

### Done-tracking strategy enum (Fix 10C)

**Replaced `run_once: true` with `strategy` enum; unified detection to one system**

- Added `strategy: "set_flag" | "run_once" | "count"` to done_tracking in
  programs.yaml. `set_flag` is the default (simple done flag), `run_once`
  replaces the old boolean, `count` handles programs that need run counting.
- Added `count_field` and `exclude_markers` to history_detection schema.
  `count_field` specifies the counter name (e.g., "refine_count");
  `exclude_markers` rejects matches (checked BEFORE markers).
- Moved 4 remaining Python-only blocks to YAML: validation (4 programs
  share one flag via markers), phaser (count), refine (count + exclude),
  real_space_refine (count). Only predict_and_build cascade stays in Python.
- Added `ALLOWED_COUNT_FIELDS` whitelist validated at load time — prevents
  typos in YAML count_field from silently creating garbage attributes.
- Removed 3 dead success flags: `refine_success`, `rsr_success`,
  `phaser_success` (set but never read anywhere).
- Unified `_set_simple_done_flags()` → `_set_done_flags()` handling all
  strategies. Removed redundant `detect_programs_in_history()` calls.
- Counter fields initialized dynamically from YAML configs (single source
  of truth) instead of hardcoded in info dict.
- 36 conformance tests passing (was 33): added test_strategy_enum_values,
  test_count_field_validation, test_exclude_markers_prevent_false_matches.
- Files: `knowledge/programs.yaml`, `agent/workflow_state.py`,
  `knowledge/program_registration.py`, `tests/tst_hardcoded_cleanup.py`

## Version 112.11 (February 2025)

### Phase 3: Final hardcoded cleanup (Fixes 6, 8, 10B)

#### Stop-directive patterns → YAML (Fix 8)

**Moved 18 regex patterns from `directive_extractor.py` to `programs.yaml`**
- 9 programs now have `stop_directive_patterns` in YAML (phenix.mtriage,
  xtriage, phaser, ligandfit, refine, autobuild, map_to_model, dock_in_map,
  map_symmetry)
- `_get_stop_directive_patterns()` loads from YAML with length-based sorting
  (longest patterns match first — handles map_to_model vs dock_in_map safely)
- Density modification branching stays in Python (requires experiment-type context)
- Hardcoded fallback with DeprecationWarning if YAML unavailable
- Files: `knowledge/programs.yaml`, `agent/directive_extractor.py`

#### Rules-selector priority lists → YAML (Fix 6)

**Moved 5 priority lists from `rules_selector.py` to `workflows.yaml`**
- `shared/rules_config` section has `default_priority`, `ligand_priority`,
  and `state_aliases`
- Per-phase `rules_priority` lists in workflow phase definitions
- `_load_rules_config()` and `_get_phase_rules_priority()` load from YAML
- r_free/map_cc validation logic stays in Python (behavioral, not config)
- Files: `knowledge/workflows.yaml`, `agent/rules_selector.py`

#### Simple done-flag detection → YAML (Fix 10B)

**Moved 10 simple if/elif blocks from `_analyze_history()` to YAML**
- 10 programs now have `history_detection` in `done_tracking` with markers,
  alt_markers/alt_requires (AND logic), and optional success_flag
- Programs: process_predicted_model, autobuild, autobuild_denmod, autosol,
  ligandfit, pdbtools, dock_in_map, map_to_model, resolve_cryo_em, map_sharpening
- New `_set_simple_done_flags()` replaces ~40 lines of if/elif blocks
- Complex cases stay in Python: validation (3 programs share flag), phaser
  (count + TFZ check), predict_and_build (cascade), refine/rsr (counts)
- Fixed process_predicted_model flag name: YAML now uses `process_predicted_done`
  to match actual usage in workflow_engine.py
- Added design note in programs.yaml: `strategy: "run_once" | "count" | "success_gate"`
  is a cleaner generalization of the current `run_once: true` boolean — noted as
  future consideration, not required now since Fix 10A already nests run_once
  correctly inside done_tracking
- Files: `knowledge/programs.yaml`, `agent/workflow_state.py`

### Test coverage

- 33 conformance tests in tst_hardcoded_cleanup.py (was 30)
- New tests: test_history_detection_coverage, test_history_detection_behavioral,
  test_no_simple_done_flags_in_analyze_history

## Version 112.10 (February 2025)

### Dead code removal in planner.py (Fix 7)

**Removed ~1300 lines of dead code from `agent/planner.py`**
- Investigation confirmed `generate_next_move()`, `construct_command_mechanically()`,
  `get_required_params()`, `extract_clean_command()`, `get_relative_path()`,
  `get_program_keywords()`, and `fix_multiword_parameters()` are never called externally
- All were superseded by the YAML-driven CommandBuilder + rules_selector pipeline
- Retained only `fix_program_parameters()` (called by graph_nodes.py) and
  `extract_output_files()` (called by run_ai_analysis.py)
- Removed heavy imports: langchain_core, phenix_knowledge, validation, memory
- File: `agent/planner.py` (1436 → ~130 lines)

### GUI app_id fallback in programs.yaml (Fix 9)

**Added `gui_app_id` fields to programs.yaml as fallback for headless environments**
- 20 programs now have `gui_app_id` in YAML (3 without GUI windows excluded)
- 3 programs have `gui_app_id_cryoem` for cryo-EM variant windows:
  predict_and_build, map_correlations, map_sharpening
- `_build_program_to_app_id()` falls back to YAML when GUI PHIL is unavailable
- `get_app_id_for_program()` cryo variants now loaded from YAML (with hardcoded baseline)
- Added `gui_app_id`, `gui_app_id_cryoem` to yaml_tools valid fields
- Files: `knowledge/programs.yaml`, `programs/ai_agent.py`, `agent/yaml_tools.py`

### Unified done flag tracking in programs.yaml (Fix 10 Step A)

**Eliminated `_MANUAL_DONE_FLAGS` dict and top-level `run_once` field**
- All done flags now defined in `done_tracking` blocks in programs.yaml
- `run_once` moved from top-level field into `done_tracking.run_once`
- `get_program_done_flag_map()` reads directly from YAML — no hardcoded dict
- Added `done_tracking` to 4 previously missing programs (holton_geometry_validation,
  model_vs_data, validation_cryoem, map_correlations removed stale `run_once: false`)
- `workflow_engine.py` now reads `done_tracking.flag` instead of deriving flag names
- Updated ADDING_PROGRAMS.md, ARCHITECTURE.md, OVERVIEW.md docs
- Files: `knowledge/programs.yaml`, `knowledge/program_registration.py`,
  `agent/workflow_engine.py`, `agent/yaml_tools.py`

## Version 112.8 (February 2025)

### Prerequisite mechanism for resolution-dependent programs

**Programs that need resolution (RSR, dock_in_map, map_to_model) now auto-trigger mtriage**
- When a program's resolution invariant can't be satisfied, the command builder
  detects a `prerequisite: phenix.mtriage` declaration and automatically builds
  the mtriage command instead
- Next cycle: resolution is available from mtriage output → original program
  builds successfully
- Respects `skip_programs`: if user skipped mtriage, returns clear error instead
  of silently failing
- Files: `knowledge/programs.yaml` (prerequisite declarations),
  `agent/command_builder.py` (prerequisite tracking),
  `agent/graph_nodes.py` (prerequisite build logic)

### LLM resolution hallucination guard

**Strip unverified resolution values from LLM strategy**
- LLMs frequently hallucinate resolution values (e.g., "resolution": 3.1)
  which bypassed the prerequisite mechanism
- `_build_strategy()` now checks whether resolution came from the LLM AND
  whether `context.resolution` (verified source) is None — if so, strips it
- Log: `BUILD: Stripped LLM-hallucinated resolution=3.1 (no verified source)`
- File: `agent/command_builder.py`

### Fixed false exclude_pattern matches in file selection

**`_2` pattern in exclude_patterns matched PDB codes like `_23883`**
- Changed mtriage and RSR exclude_patterns from `[half, _1, _2, _a, _b]` to
  `[half_1, half_2, half1, half2, _half]`
- Added `input_priorities` to mtriage for category-based file selection
  (bypasses extension fallback entirely)
- File: `knowledge/programs.yaml`

### Fixed half-map misuse as full_map in mtriage

**Two half maps → one used as full_map → wrong resolution**
- Root cause: half-maps bubbled up to `map` parent category, then selected for
  `full_map` slot via category fallback. Post-selection validation then deleted
  the legitimate half_maps as "redundant"
- Fix 1: Added `exclude_categories: [half_map]` to mtriage's full_map priorities
- Fix 2: Post-selection validation now checks if the "full_map" is actually a
  categorized half-map — if so, removes the mis-selected full_map instead
- Files: `knowledge/programs.yaml`, `agent/command_builder.py`

### Fixed autosol atom_type crash from multi-atom values

**`atom_type="Se, S"` → bare `S` on command line → crash**
- LLMs put multiple atom types in `atom_type` field (e.g., "Se, S") even when
  `additional_atom_types` is correctly set separately
- `_build_strategy()` now sanitizes: splits on comma/space, keeps first atom in
  `atom_type`, moves extras to `additional_atom_types` (if not already set)
- File: `agent/command_builder.py`

### Fixed predict_and_build intermediate file tracking

**Internal `working_model_full_docked.pdb` was tracked as valid output**
- Root cause: `docked` in filename matched `valuable_output_patterns` which
  overrode all intermediate exclusions
- Removed overly broad `(\S*docked\S*\.pdb)` from log parser known_patterns
- Added exclusions for `/local_dock_and_rebuild`, `/local_rebuild` paths
- Added `intermediate_basename_prefixes` for `working_model` (always excluded,
  even if matching a "valuable" pattern)
- Added `working_model*` to `docked` category excludes in file_categories.yaml
- Files: `phenix_ai/log_parsers.py`, `phenix_ai/utilities.py`,
  `programs/ai_agent.py`, `knowledge/file_categories.yaml`

### Fixed .eff file generation for old-style programs

**phenix.refine .eff had `generate=False` despite command saying `generate=True`**
- Root cause: `master_phil.fetch()` requires exact scope paths, but agent uses
  short-form like `xray_data.r_free_flags.generate=True` (full path is
  `refinement.input.xray_data.r_free_flags.generate`)
- Fix: Use `master_phil.command_line_argument_interpreter()` to resolve short
  paths before `fetch()` — same mechanism phenix.refine's own CLI uses
- File: `programs/ai_agent.py`

### Fixed skip_programs causing workflow deadlock

**Skipping xtriage → stuck in "analyze" phase → STOP**
- Root cause: Phase detection checked `xtriage_done` before allowing progression;
  `_apply_directives` removed xtriage from programs but couldn't change the phase
- Fix: Skipped programs are treated as "done" in `build_context()` — their done
  flags are set before phase detection runs
- Done flag mapping now auto-generated via `get_program_done_flag_map()` in
  `program_registration.py` (combines run_once auto-flags with manual mappings)
- Files: `agent/workflow_engine.py`, `knowledge/program_registration.py`

### RSR GUI reload crash fix

**TypeError on `get_output_dir()` after successful RSR execution**
- Status mismatch: native execution returns "complete" but guard checked for
  "success" / "completed" — pkl_path never sent to GUI
- Added "complete" to status check, plus pkl validation before sending
- File: `programs/ai_agent.py`

### Bare command rejection

**`phenix.mtriage` with no arguments hung waiting for input**
- Added explicit bare command check after assembly: commands with fewer than
  2 parts (just the program name) are rejected
- File: `agent/command_builder.py`

## Version 112.3 (February 2025)

### Removed langchain-classic dependency

**Direct implementation replaces deprecated langchain chains/retrievers**
- Replaced `create_stuff_documents_chain` (from `langchain.chains`) with 4-line
  direct implementation: concatenate docs → format prompt → call LLM
- Replaced `ContextualCompressionRetriever` (from `langchain.retrievers`) with
  minimal `_CompressionRetriever(BaseRetriever)` class using `langchain_core`
- Zero `from langchain.` imports remain; all code uses `langchain_core`,
  `langchain_community`, and provider-specific packages only
- Files: `analysis/summarizer.py`, `rag/retriever.py`, `docs/README.md`

### Added phenix.map_correlations support

**New program in YAML registry with multi-mode input support**
- Supports 5 input modes: model+map, model+mtz, map+map, mtz+mtz, map+mtz
- Uses flag-based file assignment (`input_files.model=`, `input_files.map_in_1=`, etc.)
- `map2` and `map_coeffs_2` slots set `auto_fill: false` to prevent the command
  builder from duplicating the same file into both map slots
- Log parsing extracts: `cc_mask`, `cc_volume`, `cc_peaks`, `cc_box`, `map_map_cc`
- Added to both xray and cryoem `validate` phases in `workflows.yaml`
- Added step_metrics entry (`CC_mask: {cc_mask:.3f}`) in `metrics.yaml`
- Added quality_table row with CC_volume detail and assessment in `metrics.yaml`
- Added `cc_mask_assessment` using same thresholds as `map_cc_assessment`
- Files: `knowledge/programs.yaml`, `knowledge/workflows.yaml`,
  `knowledge/metrics.yaml`, `agent/session.py`

### Explicit program request handling

**Hard stop for unregistered `phenix.X` requests, graceful fallback for bare names**
- When user writes `phenix.some_program` explicitly and the program is not in
  the YAML registry, raise Sorry with a clear message
- When a bare name match (e.g., "anomalous signal" matching
  `phenix.anomalous_signal`) refers to an unregistered program, silently ignore
  the match — it's likely a false positive from natural language, not a deliberate
  program request. The agent proceeds with its normal workflow.
- File: `programs/ai_agent.py`

**Explicit program injection into valid_programs**
- Registered explicit programs are injected into `valid_programs` regardless of
  workflow phase, so the LLM can select them even in early phases (e.g.,
  `map_correlations` during `cryoem_initial`)
- File: `agent/graph_nodes.py`

**STOP override for unfulfilled explicit requests**
- If the LLM chooses STOP but the user's explicitly requested program hasn't
  run yet, the plan step overrides STOP and forces the explicit program
- Checks `session_info["explicit_program"]` against history of programs run
- File: `agent/graph_nodes.py`

### Transport pipeline: explicit_program passthrough

**Added `explicit_program` to three transport whitelists**
- `build_session_state()` in `agent/api_client.py`
- `build_request_v2()` normalization in `agent/api_client.py`
- `session_state → session_info` mapping in `phenix_ai/run_ai_agent.py`
- Without these, `explicit_program` was silently dropped during transport
  from client to server, preventing injection and STOP override from working

### Program name resolution fixes

**Bare name ↔ `phenix.` prefix lookup fallback**
- `get_program()` in `yaml_loader.py` now tries `phenix.` + bare_name if
  the initial lookup returns None
- `_resolve_program_patterns()` helper in `metric_patterns.py` does the same
  for metric pattern lookups (used by all 3 lookup functions)
- Root cause: `ai_agent.py` strips the prefix at line 2077
  (`command.split()[0].replace("phenix.", "")`) before passing to metric
  extraction, so lookups against YAML keys (which use full names) failed
- Files: `knowledge/yaml_loader.py`, `knowledge/metric_patterns.py`

### Metric display pattern fixes

**Regex patterns match both raw log and reformatted report formats**
- `format_metrics_report()` transforms `cc_mask` → `Cc Mask` via
  `.replace("_", " ").title()`, so result text stored in cycles uses
  the reformatted form
- Updated all CC patterns in `_extract_final_metrics()` to use
  `CC[_ ]?mask` with `re.IGNORECASE` to match both formats
- Updated `map_cc` pattern in `_extract_metrics_from_result()` similarly
- File: `agent/session.py`

### Minor fixes

**Reasoning truncation increased**
- Main reasoning: 500 → 1000 chars
- Session summary reasoning: 300 → 600 chars
- Files: `programs/ai_agent.py`, `agent/session.py`

**False input_directory warning fixed**
- When user supplies `original_files` directly, no longer warns
  "No input_directory to look for files" if files already present
- File: `programs/ai_agent.py`

**Unused import removed**
- Removed `from langchain_core.documents import Document as LCDocument`
  from `rag/retriever.py`

## Version 112.2 (February 2025)

### Cohere → FlashRank Migration

**Dependency removal - Replace Cohere API with local FlashRank reranker**
- Replaced `CohereRerank` (cloud API) with `FlashrankRerank` (local cross-encoder)
- Model: `ms-marco-MiniLM-L-12-v2` (~34MB, runs on CPU, no API key needed)
- Same `ContextualCompressionRetriever` pattern — callers unchanged
- Removed `COHERE_API_KEY` environment variable requirement
- Removed `CohereApiError` exception handling (local inference has no API errors)
- Updated privacy disclaimers (Cohere no longer contacted)
- Files: `rag/retriever.py`, `analysis/analyzer.py`, `utils/run_utils.py`,
  `programs/ai_agent.py`, `programs/ai_analysis.py`
- Install: `pip install flashrank` (replaces `cohere` + `langchain-cohere`)

**Script cleanup**
- `run_inspect_db.py`: Removed debug print and hardcoded scratch path
- `run_query_docs.py`: Replaced duplicated API key validation with shared
  `validate_api_keys()` from `utils/run_utils.py`

**Usability - Early exit when no inputs provided**
- Agent now stops with a helpful message if launched with no original_files,
  no project_advice, and no README in the input_directory
- Skipped when resuming an existing session (has previous cycles)
- File: `programs/ai_agent.py`

**Fix - Quote multi-word PHIL parameter values in commands**
- `unit_cell=114 114 32.5 90 90 90` was passed unquoted, causing shell/PHIL
  to split the values into separate arguments → crash
- Added `fix_multiword_parameters()` in `agent/planner.py` (LLM command path)
  and inline regex in `agent/program_registry.py` (registry command path)
- Now produces `unit_cell="114 114 32.5 90 90 90"` and `space_group="P 2 21 21"`
- Also handles prefixed forms like `xray_data.unit_cell=...`

**Fix - Strategy passthrough dropping known PHIL short names (HIGH IMPACT)**
- `unit_cell`, `space_group`, etc. from directives were silently dropped
  in `program_registry.py` because the passthrough required a dot in the key
- Server LLM returned `unit_cell=...` (no prefix) vs local returning
  `xray_data.unit_cell=...`, so the bug only manifested on the server
- Added `KNOWN_PHIL_SHORT_NAMES` set to the passthrough check so common
  parameters like `unit_cell`, `space_group`, `resolution`, `nproc`,
  `ncopies`, `twin_law` are accepted without their full PHIL scope prefix
- File: `agent/program_registry.py`

**Fix - Polder has no resolution keyword**
- Polder has no resolution keyword of any kind
- Removed `high_resolution` and `resolution` strategy flags and the
  `auto_fill_rfree_resolution` fix from polder's YAML
- Removed `resolution` (and `high_resolution`, `low_resolution`) from
  `KNOWN_PHIL_SHORT_NAMES` passthrough set in `program_registry.py` —
  these should only go through explicit strategy_flags, not blindly
  passed through to programs that don't support them
- Added polder entry to `parameter_fixes.json` to strip resolution/
  high_resolution/low_resolution/d_min as a safety net
- Added `has_strategy: selection` invariant to block polder if no
  selection is provided, forcing LLM retry
- Added hints telling LLM that selection is required and no resolution
  exists
- Files: `knowledge/programs.yaml`, `agent/program_registry.py`,
  `knowledge/parameter_fixes.json`

**Fix - Anomalous Resolution incorrectly reported as Resolution**
- In xtriage logs with anomalous data, "Anomalous Resolution: 9.80" was
  matched by the generic resolution regex before "Resolution: 2.50"
- Added negative lookbehind `(?<!nomalous )` to all resolution patterns:
  `agent/session.py` (hardcoded fallback), `agent/session_tools.py`,
  `knowledge/patterns.yaml` (centralized pattern)
- Updated xtriage YAML `log_parsing` to use `extract: last` so the summary
  "Resolution: 2.50" at the end of the log is preferred over earlier matches
- Added explicit `anomalous_resolution` pattern to xtriage YAML log_parsing
- Files: `agent/session.py`, `agent/session_tools.py`,
  `knowledge/programs.yaml`, `knowledge/patterns.yaml`

**Fix - AutoSol using Phaser output data instead of original data**
- After Phaser MR, `best_files['data_mtz']` was set to PHASER.1.mtz which
  has lost anomalous signal — useless for SAD phasing
- Added `input_priorities` for `data_mtz` in autosol YAML:
  `categories: [original_data_mtz, data_mtz]`,
  `exclude_categories: [phased_data_mtz]`,
  `skip_rfree_lock: true`
- AutoSol doesn't need rfree handling — added `skip_rfree_lock` support
  to `command_builder.py` PRIORITY 1 so autosol gets original data
- CRITICAL: LLM file choices were bypassing input_priorities entirely.
  Added `exclude_categories` check to the LLM hint acceptance path in
  `_select_files()` — now rejects LLM-chosen files in excluded categories
  (with both full-path and basename matching for robustness)
- Added prompt guidance telling LLM that autosol needs original data
- Files: `knowledge/programs.yaml`, `agent/command_builder.py`,
  `knowledge/prompts_hybrid.py`

**Fix - Wavelength mistakenly extracted as resolution in directives**
- Text like "data collected far from the iron edge (1.1158 Å)" was being
  extracted as `resolution=1.1158` by the directive extractor LLM
- Three fixes applied:
  1. LLM prompt: Added explicit "Do NOT confuse wavelength with resolution"
     warning with guidelines for distinguishing them
  2. Validation: Added post-extraction check that removes resolution values
     < 1.2 Å (wavelength range) or matching any extracted wavelength value
  3. Simple extractor: Removed overly broad patterns like "X Å" standalone;
     added wavelength cross-check; raised minimum from 0.5 to 1.0
- File: `agent/directive_extractor.py`

**Fix - AutoSol getting obs_labels from recovery strategy**
- Recovery strategy from xtriage was applying `autosol.input.xray_data.obs_labels=I(+)`
  to autosol commands — autosol handles labels internally
- Removed autosol from `DATA_LABEL_PARAMETERS` in command_builder.py
- Changed fallback behavior: programs NOT in `DATA_LABEL_PARAMETERS` now skip
  label recovery entirely (instead of using default `obs_labels` parameter)
- Safety net in `parameter_fixes.json` also strips obs_labels from autosol
- Files: `agent/command_builder.py`, `knowledge/parameter_fixes.json`,
  `tests/tst_command_builder.py`

**Fix - MR-SAD workflow skipping phaser and going straight to autosol**
- `after_program=phenix.autosol` directive was forcing autosol immediately,
  even before xtriage or phaser had run
- Root cause TWO-FOLD:
  1. `use_mr_sad` handling only removed autosol from `obtain_model` phase
  2. AutoSol was in YAML `obtain_model` phase with `has: anomalous` condition,
     entering valid_programs through the base path BEFORE `_apply_directives`
- Four fixes:
  1. `get_valid_programs()`: Added MR-SAD guard that removes autosol when
     has_search_model + has_anomalous + not phaser_done — runs BEFORE
     `_apply_directives` so autosol can't leak through the YAML path
  2. `_check_program_prerequisites()`: autosol requires xtriage_done, and
     for implicit MR-SAD (has_search_model + has_anomalous), also phaser_done
  3. `_apply_directives()`: use_mr_sad now removes autosol from ALL phases
     when phaser hasn't run (not just obtain_model)
  4. Directive extraction prompt: Added "CRITICAL: MR-SAD workflow" guidance
- Standalone SAD (no search model) unaffected by the guard
- Files: `agent/workflow_engine.py`, `agent/directive_extractor.py`

### Dependency Cleanup

**Fix - Directive extractor inferring use_experimental_phasing from data**
- LLM was setting `use_experimental_phasing: True` before xtriage ran,
  based on data characteristics (wavelength, atom types) rather than
  explicit user request
- This caused `predict_and_build` to be deprioritized even when the case
  needed AlphaFold to generate a model
- Added CRITICAL guidance to LLM prompt: only set `use_experimental_phasing`
  and `use_mr_sad` when user EXPLICITLY requests SAD/MAD/experimental phasing
- The system already auto-detects anomalous signal via xtriage and adjusts
  the workflow through the `has_anomalous` context flag
- File: `agent/directive_extractor.py`

**Removed langchain-classic dependency**
- In langchain 1.0+, `langchain.chains` and `langchain.retrievers` were
  removed and moved to `langchain_classic`. Rather than depending on the
  legacy package, implemented the functionality directly:
- `analysis/summarizer.py`: Replaced `create_stuff_documents_chain` with
  direct document concatenation + LLM invoke (the function just joins
  document text and passes it through a prompt template)
- `rag/retriever.py`: Replaced `ContextualCompressionRetriever` with
  `_CompressionRetriever`, a minimal `BaseRetriever` subclass that
  retrieves docs then reranks via the compressor — uses `langchain_core`
  which is still maintained
- All `from langchain.` imports eliminated — code now only uses
  `langchain_core`, `langchain_community`, and provider packages
- Removed from README dependencies table
- `langchain-classic` package can now be uninstalled

### Test Infrastructure Fixes

**Fix - Unconditional mock modules breaking PHENIX environment tests**
- Five test files unconditionally overwrote real `libtbx` modules with mocks
- Added `if 'libtbx' not in sys.modules` guards to all mock blocks
- Set `__path__` on mock modules pointing to actual local directories so
  submodule imports (e.g. `libtbx.langchain.agent.utils`) resolve automatically
- Files: `tst_state_serialization.py`, `tst_command_builder.py`,
  `tst_file_categorization.py`, `tst_session_directives.py`

**Fix - `tst_advice_preprocessing.py` import failure in PHENIX**
- `sys.path.insert` was inside `except ImportError` block, so `from tests.tst_utils`
  was unfindable when libtbx imports succeeded
- Moved to top level

**Cleanup - Stale docstring run instructions**
- Updated `Run with: python tests/test_...` → `tst_...` across 21 files

## Version 112.1 (February 2025)

### Cryo-EM State Fix & MR-SAD Workflow Support

**Fix 1 - Cryo-EM workflow stuck at cryoem_initial (HIGH IMPACT)**
- `_detect_cryoem_phase()` gated all progress behind `mtriage_done` check
- Tutorials that skip mtriage (going straight to resolve_cryo_em) got permanently stuck
- Fix: "past analysis" check now also considers `resolve_cryo_em_done`, `dock_done`, `rsr_done`
- Files: `agent/workflow_engine.py`

**Feature 1 - MR-SAD experimental phasing workflow (NEW)**
- Added `experimental_phasing` phase to X-ray workflow for MR-SAD
- Workflow: xtriage → phaser (place model) → autosol (with partpdb_file=PHASER.pdb) → autobuild
- Added `partpdb_file` optional input to autosol (auto-filled from phaser_output category)
- Added `xray_mr_sad` state mapping and detection in workflow engine
- Phase detection: triggered when `phaser_done AND (has_anomalous OR use_mr_sad)`
- Added `use_mr_sad` directive to workflow_preferences
- Directive extractor: "MR-SAD" keywords set `use_mr_sad=true`, do NOT set `after_program=autosol`
- In obtain_model phase with use_mr_sad: phaser prioritized, autosol removed
- Files: `agent/workflow_engine.py`, `knowledge/programs.yaml`, `knowledge/workflows.yaml`,
  `agent/decision_config.json`, `agent/directive_extractor.py`

**Tests: 765 total (+8 new)**
- `test_mr_sad_after_phaser_with_anomalous` - MR-SAD state detection
- `test_mr_sad_not_triggered_without_anomalous` - No false positives
- `test_mr_sad_not_triggered_when_autosol_done` - Skip if already done
- `test_mr_sad_directive_prioritizes_phaser` - Directive removes autosol from obtain_model
- `test_normal_sad_still_works` - Normal SAD pathway unaffected
- `test_autosol_has_partial_model_config` - YAML config correctness
- `test_mr_sad_directive_overrides_no_anomalous` - Directive triggers without has_anomalous
- `test_experimental_phasing_yaml_structure` - YAML phase structure validation

**Documentation updated:**
- ARCHITECTURE.md: State diagram with MR-SAD path, state table
- USER_DIRECTIVES.md: use_mr_sad field, MR-SAD example (Example 5)
- TESTING.md: Test counts (757→765)

## Version 112 (February 2025)

### Summary Quality Improvements: Steps Table Metrics & Consistency Fixes

Seven issues identified and fixed through codebase-wide audit:

**Fix 1 - Steps Performed table shows actual metrics (HIGH IMPACT)**
- `_get_key_metric_for_step()` now uses pre-parsed `cycle["metrics"]` as primary source
- YAML patterns only match raw log format (e.g., `R-free =`) but result text has reformatted
  metrics (e.g., `R Free:`) — caused all YAML pattern extraction to fail silently
- Steps table now shows "R-free: 0.258" instead of fallback text like "Analyzed" or "Built model"
- Files: `agent/session.py`

**Fix 2 - Benign warnings no longer drop metrics (MODERATE)**
- `extract_metrics_from_log()` now called for ALL success paths, not just clean success
- Previously, benign warnings (e.g., "No array of R-free flags found") triggered a code path
  that skipped metrics extraction entirely
- First refinement run (which often recreates R-free flags) could lose all R-factor metrics
- Files: `programs/ai_agent.py`

**Fix 3 - Ligandfit output typed as "ligand" not "model" (MINOR)**
- `_describe_output_file()` now returns `type: "ligand"` for `ligand_fit_*.pdb` files
- Previously typed as "model", so `best_by_type["ligand"]` was never populated in fallback path
- Files: `agent/session.py`

**Fix 4 - Removed dead `ligand_fit_output` from `categories_to_show` (COSMETIC)**
- `ligand_fit_output` is a stage under `"model"` in best_files, not a top-level category key
- Entry could never match anything; ligandfit output found via cycle-scanning (added in v111)
- Files: `agent/session.py`

**Fix 5 - `_is_intermediate_file` case-sensitive patterns fixed (MINOR)**
- `"carryOn"` and `"CarryOn"` compared against `basename_lower` could never match
- Replaced with lowercase `"carryon"`
- Files: `agent/session.py`

**Fix 6 - `detect_program` distinguishes `autobuild_denmod` (MINOR)**
- Added `autobuild_denmod` / `maps_only` detection before generic `autobuild` match
- Also added separate branch in `extract_all_metrics` to prevent autobuild_denmod from
  running autobuild's metrics extractor (which would report misleading R-factors)
- Files: `phenix_ai/log_parsers.py`

**Fix 7 - Added missing YAML `log_parsing` sections (TECH DEBT)**
- Six programs declared `outputs.metrics` but had no `log_parsing` patterns:
  autobuild, autosol, dock_in_map, map_to_model, validation_cryoem, holton_geometry_validation
- Two programs had partial coverage: predict_and_build (missing map_cc),
  real_space_refine (missing clashscore)
- All now have matching YAML patterns, same type of gap that caused v111's predict_and_build issue
- Files: `knowledge/programs.yaml`


## Version 111 (February 2025)

### Summary Output Fixes: Missing R-free Statistics & Ligandfit Files

**Problem 1 - Missing Final Quality Statistics**
- predict_and_build runs internal refinement but R-free/R-work were not extracted from its logs
- `_extract_predict_and_build_metrics()` only extracted pLDDT, map_cc, residues_built
- Added: calls `_extract_refine_metrics()` to also capture R-factors
- Added: `log_parsing` section in `programs.yaml` for predict_and_build
- Added: `autobuild_denmod` entry in `metrics.yaml` step_metrics to prevent it from
  matching generic autobuild config

**Problem 2 - Missing Ligandfit Output in Key Files**
- `ligand_fit_output` is a subcategory under parent `"model"` in best_files_tracker,
  not a top-level key — `best_files.get("ligand_fit_output")` always returned None
- Ligandfit scores 90 vs refined model's 100, so never becomes best "model" entry
- Added: cycle-scanning logic to find ligandfit output files from successful cycles

**Bonus - Fixed fallback cycle filtering**
- `cycle.get("status") != "completed"` checked a field that never exists
- Changed to `"SUCCESS" not in result` to match how success is tracked everywhere else

Files: `phenix_ai/log_parsers.py`, `knowledge/programs.yaml`, `knowledge/metrics.yaml`, `agent/session.py`


## Version 110 (January 2025)

### Major Feature: Experimental Phasing (SAD/MAD) Workflow Support

**Problem**: After xtriage detected anomalous signal, the agent would correctly run `phenix.autosol` on a straight-through workflow. But if the session was restarted (after removing autosol run), the agent would fall back to `phenix.predict_and_build` instead of re-running autosol, even though:
1. The session had `use_experimental_phasing: True` directive
2. The xtriage cycle showed `has_anomalous: True` in its metrics

**Root Cause**: A key mismatch in the transport layer:
- `session.get_history_for_agent()` returned history entries with `"analysis"` key
- `build_request_v2()` looked for `"metrics"` key, so it got empty `{}`
- After transport, history had empty metrics
- `_analyze_history()` looked for `"analysis"` key but found `"metrics"` (now empty)

Result: The anomalous flags (`has_anomalous`, `anomalous_measurability`) were lost during session restart, so autosol's condition `has: anomalous` was not met.

**Solution**:

1. **Fixed key mismatch** (`agent/api_client.py`):
   - `build_request_v2()` now checks both keys: `h.get("analysis", h.get("metrics", {}))`

2. **Fixed history analysis** (`agent/workflow_state.py`):
   - `_analyze_history()` now checks both keys: `entry.get("analysis", entry.get("metrics", {}))`

3. **Added anomalous metrics extraction** (`agent/session.py`):
   - `_extract_metrics_from_result()` now extracts anomalous info from result text
   - Patterns for: "Anomalous Measurability: 0.15", "Has Anomalous: True", etc.

### Code Consolidation: History Analysis

**Problem**: Two overlapping functions were analyzing history:
- `_extract_history_info()` in `graph_nodes.py` - used by rules selector
- `_analyze_history()` in `workflow_state.py` - used by workflow engine

This duplication led to inconsistencies and the key mismatch bug.

**Solution**: Consolidated to single source of truth:

1. **`_analyze_history()` is authoritative** - extracts all metrics from history
2. **`build_context()` includes all info** - passes to workflow_state
3. **`_extract_history_info()` delegates** - now just extracts from pre-computed context

Before (duplicated computation):
```
History → _extract_history_info() → (recompute from history)
History → _analyze_history() → (compute from history)
```

After (single computation):
```
History → _analyze_history() → history_info
                                    ↓
                            build_context() → context
                                    ↓
                            workflow_state (includes context)
                                    ↓
                            _extract_history_info() ← pulls from context
```

### New Context Fields

Added to workflow context for SAD/MAD workflows and refinement strategy:

| Field | Source | Purpose |
|-------|--------|---------|
| `has_anomalous` | xtriage analysis | Enable autosol in obtain_model phase |
| `strong_anomalous` | measurability > 0.10 | Priority boost for autosol |
| `anomalous_measurability` | xtriage | Quantify anomalous signal strength |
| `anomalous_resolution` | xtriage | Anomalous signal extent |
| `has_ncs` | map_symmetry analysis | Enable NCS constraints in refinement |

### New Tests

Added `tests/tst_history_analysis.py` with tests for:
- Transport analysis/metrics key handling
- History analysis from both key types
- NCS extraction
- Weak vs strong anomalous signal
- Session metrics extraction
- Graph nodes context extraction
- Integration: anomalous workflow enablement

### Code Consolidation: Duplicate Code Removal

**Problem**: Several functions were duplicated across the codebase:
1. Session state building - identical code in LocalAgent and RemoteAgent
2. MTZ classification - triplicated logic in workflow_state, template_builder, best_files_tracker
3. Program detection - similar functions in log_parsers and program_registry

**Solution**: Created shared utilities and delegated to canonical implementations:

**1. Session State Building** (`agent/api_client.py`):
```python
def build_session_state(session_info, session_resolution=None):
    """Build session_state dict from session_info. Used by both agents."""
```
- LocalAgent and RemoteAgent now call this shared function
- Reduced ~30 lines of duplicated code

**2. MTZ Classification** (`agent/file_utils.py` - NEW):
```python
def classify_mtz_type(filepath):
    """Classify MTZ as data_mtz or map_coeffs_mtz."""

def get_mtz_stage(filepath, category):
    """Get specific stage like refine_map_coeffs, denmod_map_coeffs."""
```
- Single source of truth for MTZ classification patterns
- workflow_state.py, template_builder.py, best_files_tracker.py now delegate to this
- Also includes is_mtz_file(), is_model_file(), is_map_file(), is_sequence_file()

**3. Program Detection** (`phenix_ai/log_parsers.py`):
- Made detect_program() the canonical implementation
- program_registry._detect_program() now delegates to it
- Added molprobity and polder detection to the canonical function

### New Test Suites

- `tests/tst_file_utils.py`: 12 tests for MTZ classification and file type detection

### Files Changed

- `agent/api_client.py`: Added build_session_state(), fixed analysis/metrics key lookup
- `agent/file_utils.py`: NEW - shared file classification utilities
- `agent/workflow_state.py`: Uses shared classify_mtz_type, fixed key lookup, added has_ncs/strong_anomalous
- `agent/workflow_engine.py`: Added has_ncs to context
- `agent/template_builder.py`: Uses shared classify_mtz_type
- `agent/best_files_tracker.py`: Uses shared classify_mtz_type
- `agent/program_registry.py`: Delegates to log_parsers.detect_program
- `agent/session.py`: Added anomalous metrics extraction
- `agent/graph_nodes.py`: Simplified _extract_history_info to use context
- `phenix_ai/local_agent.py`: Uses build_session_state
- `phenix_ai/remote_agent.py`: Uses build_session_state
- `phenix_ai/log_parsers.py`: Added molprobity and polder detection
- `tests/tst_history_analysis.py`: New test file
- `tests/tst_file_utils.py`: New test file
- `tests/run_all_tests.py`: Added History Analysis and File Utils test suites

---

### Major Feature: Dual MTZ Tracking

**Problem**: The codebase treated all MTZ files as a single category, but there are fundamentally two types:
- **Data MTZ**: Contains measured Fobs and R-free flags (for refinement)
- **Map Coefficients MTZ**: Contains calculated phases (for ligand fitting, visualization)

This caused issues with programs like `phenix.ligandfit` potentially receiving data MTZ instead of map coefficients.

**Solution**: Split MTZ tracking into two explicit categories with different update behaviors:

| Category | Update Rule | Use Case |
|----------|-------------|----------|
| `data_mtz` | First with R-free **locks forever** | Consistent R-free flags across refinement |
| `map_coeffs_mtz` | **Most recent wins** | Maps improve with refinement, use latest |

### Key Changes

**New Categories** (`knowledge/file_categories.yaml`):
- `data_mtz` (parent): original_data_mtz, phased_data_mtz
- `map_coeffs_mtz` (parent): refine_map_coeffs, denmod_map_coeffs, predict_build_map_coeffs

**Updated Programs** (`knowledge/programs.yaml`):
| Program | MTZ Input | Purpose |
|---------|-----------|---------|
| `phenix.xtriage` | `data_mtz` | Analyze Fobs |
| `phenix.phaser` | `data_mtz` | Molecular replacement |
| `phenix.refine` | `data_mtz` | Refine against Fobs |
| `phenix.polder` | `data_mtz` | Calculate omit maps |
| `phenix.ligandfit` | `map_coeffs_mtz` | Fit ligands (needs calculated phases) |

**BestFilesTracker** (`agent/best_files_tracker.py`):
- Added `_evaluate_data_mtz()`: Locks on first R-free flags
- Added `_evaluate_map_coeffs_mtz()`: Prefers most recent cycle
- Added `_classify_mtz_type()`: Auto-classifies by filename pattern

### Additional Fixes in v110

**Stepwise Mode / Automation Path**:
- Added `automation_path` to workflow state ("automated" or "stepwise")
- In stepwise mode, `predict_and_build` stops after prediction
- User then proceeds with `process_predicted_model` → `phaser` → `refine`
- Prevents duplicate predict_and_build runs

**Fallback Program Tracking**:
- Fallback node now correctly sets `program` field in state
- Fixes mismatch where PLAN showed one program but command was different
- Response builder uses `state["program"]` over `intent["program"]` when fallback used

**AutoBuild Scoring**:
- `autobuild_output` stage score increased to 100 (same as `refined`)
- AutoBuild runs internal refinement, so outputs are effectively refined models
- AutoBuild with better R-free now correctly beats earlier refine outputs

**Session Summary Best Files**:
- Removed file existence check in `_get_final_output_files()`
- Files created on client machine don't exist on server
- Now correctly shows best files in markdown summaries

### MTZ Classification Patterns

| Pattern | Category | Stage |
|---------|----------|-------|
| `refine_*_001.mtz` | map_coeffs_mtz | refine_map_coeffs |
| `*map_coeffs*.mtz` | map_coeffs_mtz | varies |
| `*denmod*.mtz` | map_coeffs_mtz | denmod_map_coeffs |
| `*_data.mtz` | data_mtz | original_data_mtz |
| Everything else | data_mtz | data_mtz |

### Files Changed

- `knowledge/file_categories.yaml`: New data_mtz and map_coeffs_mtz hierarchies
- `knowledge/programs.yaml`: All 10 programs updated
- `knowledge/metrics.yaml`: Scoring config for both MTZ types, autobuild_output score
- `knowledge/workflows.yaml`: Updated polder conditions
- `agent/best_files_tracker.py`: New evaluation methods, autobuild scoring
- `agent/workflow_state.py`: Updated parent categories, automation_path
- `agent/workflow_engine.py`: automation_path in context, stepwise mode handling
- `agent/command_builder.py`: Updated slot mappings
- `agent/graph_nodes.py`: Updated sanity context, fallback program tracking
- `agent/rules_selector.py`: Updated file selection
- `agent/session.py`: New get_best_data_mtz(), get_best_map_coeffs_mtz(), fixed best files display
- `agent/template_builder.py`: Updated category detection
- `agent/program_registry.py`: Updated phaser command
- `agent/directive_extractor.py`: Updated file preferences
- `knowledge/prompts_hybrid.py`: Updated recommended files display
- `phenix_ai/run_ai_agent.py`: Use state["program"] for response
- `tests/tst_best_files_tracker.py`: All 48 tests updated + autobuild scoring tests
- `tests/tst_workflow_state.py`: Added stepwise mode tests

---

## Version 109 (January 2025)

### Bug Fix: Empty Directives When Using Ollama Provider

**Problem**: When running with `provider=ollama`, directive extraction returned empty `{}` even when the user's advice clearly specified workflow instructions like "include ligand fitting".

**Root Cause**: Smaller local models (like llama3.2) may not follow complex JSON extraction prompts as reliably as GPT-4 or Gemini. The LLM might return:
- Empty JSON `{}`
- Malformed JSON that fails to parse
- Valid JSON but with content that gets filtered during validation

**Solution**: Multiple improvements for ollama reliability:

1. **Fallback to simple pattern extraction**: When ollama's LLM returns empty or fails, automatically fall back to `extract_directives_simple()` which uses regex patterns

2. **Added prefer_programs patterns**: New patterns to detect workflow preferences like:
   - "include ligand fitting" → `prefer_programs: [phenix.ligandfit]`
   - "fit the ligand" → `prefer_programs: [phenix.ligandfit]`
   - "with ligand fitting" → `prefer_programs: [phenix.ligandfit]`
   - "calculate polder map" → `prefer_programs: [phenix.polder]`

3. **Better logging**: Added debug logging to show:
   - What provider is being used
   - Response length from LLM
   - What sections were parsed
   - Preview of response when parsing fails

**Behavior with ollama**:
```
DIRECTIVES: Got response from ollama (500 chars)
DIRECTIVES: Parsed to empty dict - LLM may not have found actionable directives
DIRECTIVES: Trying simple pattern extraction as ollama fallback
DIRECTIVES: Simple extraction found: ['workflow_preferences']
```

### Files Changed

- `agent/directive_extractor.py`:
  - Added ollama fallback to `extract_directives()` 
  - Added `prefer_program_patterns` to `extract_directives_simple()`
  - Improved logging throughout

---

## Version 108 (January 2025)

### Multiple Summary and Advice Filtering Fixes

**Issue 1: "Predicted model" shown for predict_and_build output**

- Problem: Step metric showed "Predicted model" instead of refinement metrics for full predict_and_build runs
- Fix: Changed `metrics.yaml` step_metrics for predict_and_build to use `r_free` as primary metric with "Built model" fallback

**Issue 2: Key Output Files shows all files instead of best files**

- Problem: Summary listed many files from last cycle instead of the actual best files
- Fix: Modified `_get_final_output_files()` in `session.py` to prioritize `best_files` from session (model, mtz, map, sequence) before falling back to cycle outputs

**Issue 3: Ligandfit filtered out by user advice**

- Problem: User advice like "refinement with ligand fitting" was filtering programs to only `phenix.refine` because it contained "refine"
- Fix: Extended multi-step workflow detection in `_apply_user_advice()`:
  - Added sequencing words: "with", "including", "include", "plus", "also", "workflow", "sequence", "steps", "primary goal", "goal:"
  - Added check for multiple programs mentioned (if 2+ programs mentioned, don't filter)

**Issue 4: Empty directives when using ollama provider**

- Problem: Directive extraction returned `{}` with ollama because the server doesn't support ollama
- Root cause: Even though `run_on_server=False` was set for ollama, the code fell through to server execution when no local RAG database was found
- Fix: Added explicit check in `run_job_on_server_or_locally()` to honor `run_on_server=False` for directive_extraction mode, bypassing the database check since directive extraction only needs the LLM, not the RAG database

### Files Changed

- `knowledge/metrics.yaml` - Fixed predict_and_build step metrics
- `agent/session.py` - Modified `_get_final_output_files()` to use best_files first
- `agent/rules_selector.py` - Extended multi-step detection in `_apply_user_advice()`
- `programs/ai_analysis.py` - Added directive_extraction bypass for local-only mode

---

## Version 107 (January 2025)

### Feature: Graceful Stop on Persistent LLM Failures

**Problem**: When the LLM service was unavailable (rate limited, overloaded, API errors), the agent would silently fall back to rules-only mode without informing the user. This could lead to unexpected behavior.

**Solution**: After 3 consecutive LLM failures, the agent now stops gracefully with a helpful message:

```
The LLM service (google) is currently unavailable after 3 attempts.
Last error: 503 UNAVAILABLE - The model is overloaded

Options:
  1. Wait and try again later
  2. Run with --use_rules_only=True to continue without LLM
  3. Check your API key and network connection
```

**Behavior**:
- Failures 1-2: Fall back to rules-based planning for that cycle, continue workflow
- Failure 3+: Stop gracefully with helpful message
- On success: Reset failure counter

**Implementation**:
- Added `_handle_llm_failure()` function in `graph_nodes.py`
- Tracks `llm_consecutive_failures` in state
- Emits `STOP_DECISION` event with `llm_unavailable=True` flag

### Files Changed

- `agent/graph_nodes.py` - Added `_handle_llm_failure()`, failure tracking, graceful stop

---

## Version 106 (January 2025)

### Bug Fix: Informational Program Mentions Don't Block Workflow

**Problem**: When the LLM's processed advice mentioned programs like `phenix.elbow` or `phenix.ready_set` as suggestions (e.g., "if not provided, generate a CIF restraints file with phenix.elbow"), the directive validator treated these as explicit program requests and blocked the workflow with:

```
DIRECTIVE VALIDATION FAILED
Program 'phenix.elbow' exists in PHENIX but is not available in the AI agent workflow.
```

**Solution**: Program mentions in user advice text are now converted to warnings instead of blocking issues. Only programs explicitly requested in the directives structure (program_settings, stop_conditions.after_program, etc.) will block the workflow.

**Behavior Change**:
- Before: Any mention of unavailable program → VALIDATION FAILED
- After: Text mentions → Warning only, workflow continues

### Files Changed

- `agent/directive_validator.py` - Section 1 (user advice program check) now produces warnings not issues

---

## Version 105 (January 2025)

### Bug Fix: Failed Programs Don't Count as Done

**Problem**: Programs marked with `run_once: true` were being marked as "done" even when they failed, preventing retry attempts.

**Solution**: All `*_done` flags now only get set if the program completed successfully. Failed runs are skipped.

**Failure Detection**: Uses specific patterns to avoid false positives:
- `FAILED`, `SORRY:`, `SORRY `, `ERROR:`, `ERROR `, `: ERROR`, `TRACEBACK`, `EXCEPTION`
- Does NOT match "No ERROR detected" or similar success messages

**Programs Fixed**:

In `knowledge/program_registration.py` (auto-detected `run_once` programs):
- `detect_programs_in_history()` now checks result for failure patterns

In `agent/workflow_state.py` (manually tracked programs):
- Added `_is_failed_result()` helper function
- All `*_done` flag assignments now use this helper:
  - `validation_done`, `phaser_done`, `predict_done`, `predict_full_done`
  - `process_predicted_done`, `autobuild_done`, `autobuild_denmod_done`
  - `autosol_done`, `refine_done`, `rsr_done`, `ligandfit_done`
  - `pdbtools_done`, `dock_done`, `map_to_model_done`
  - `resolve_cryo_em_done`, `map_sharpening_done`

**Behavior Change**:
- Before: `ligandfit` fails → `ligandfit_done=True` → Cannot retry
- After: `ligandfit` fails → `ligandfit_done=False` → Can retry

### Files Changed

- `knowledge/program_registration.py` - Added specific failure patterns to `detect_programs_in_history()`
- `agent/workflow_state.py` - Added `_is_failed_result()` helper, refactored all done flag checks

---

## Version 104 (January 2025)

### Change: Remove Ligandfit Label Specification

**Rationale**: `phenix.ligandfit` can auto-detect map coefficient labels from the MTZ file. Manually specifying labels based on filename patterns was error-prone and could cause failures when the patterns didn't match the actual MTZ contents.

**Changes**:
- Removed `file_info.input_labels` from ligandfit defaults
- Removed label-switching invariants (`denmod_labels`, `predict_and_build_labels`)
- Kept commented-out code in programs.yaml for future restoration if needed

**Before**:
```
phenix.ligandfit model=... data=... ligand=... file_info.input_labels="FP PHIFP" general.nproc=4
```

**After**:
```
phenix.ligandfit model=... data=... ligand=... general.nproc=4
```

### Files Changed

- `knowledge/programs.yaml` - Removed labels from defaults, commented out invariants

---

## Version 103 (January 2025)

### Bug Fix: Intermediate File Handling

Fixed several issues where intermediate/temporary files were incorrectly used as inputs or tracked as best files.

**Issues Fixed:**

1. **Elbow ligand files used instead of fitted ligands**
   - `LIG_lig_ELBOW.*.pdb` files are geometry-optimized ligands, not fitted ligands
   - Added `*ELBOW*` and `*/TEMP*/*` exclusions to `ligand_pdb` category

2. **Superposed predicted models used as best model**
   - `*superposed_predicted_models*` files are alignment intermediates
   - Added exclusion to `predicted` category
   - Added to intermediate patterns in `best_files_tracker.py`

3. **Reference and EDITED files used as final outputs**
   - `*reference*` files are intermediate templates
   - `*EDITED*` files are intermediate edits
   - Added exclusions to `refined` category
   - Added to intermediate patterns in `best_files_tracker.py`

4. **with_ligand models now preserved**
   - Added `with_ligand` to valuable_patterns so combined protein+ligand models are tracked

### Files Changed

- `knowledge/file_categories.yaml`:
  - Added ELBOW/TEMP exclusions to `ligand_pdb`
  - Added `superposed_predicted_models` exclusion to `predicted`
  - Added patterns to `intermediate` category
  - Added exclusions to `refined` category
- `agent/best_files_tracker.py`:
  - Extended `_is_intermediate_file()` with more patterns
  - Added `with_ligand` to valuable patterns

---

## Version 102 (January 2025)

### Bug Fix: Retry on Model Overload (503 UNAVAILABLE)

**Problem**: When Gemini returns a 503 UNAVAILABLE error ("The model is overloaded"), the summarization would fail immediately without retrying.

**Solution**: 
1. Added "503" and "unavailable" to rate limit indicators in `rate_limit_handler.py`
2. Added retry logic to `summarize_log()` with exponential backoff (2s, 4s, 8s) for rate limit and overload errors

### Behavior

When model is overloaded:
```
Summarizing log file (using cheap model)...
Model overloaded/rate limited, waiting 2s before retry...
Summarization retry 2/3...
Model overloaded/rate limited, waiting 4s before retry...
Summarization retry 3/3...
[success or final error]
```

### Files Changed

- `agent/rate_limit_handler.py` - Added "503" and "unavailable" to rate limit indicators
- `phenix_ai/run_ai_analysis.py` - Added retry logic to `summarize_log()` with exponential backoff

---

## Version 101 (January 2025)

### Bug Fix: HTML Summary Table Formatting After Failed Steps

**Problem**: When a step failed, the error message in the "Key Metric" column could contain newlines or pipe characters, breaking the markdown table formatting for all subsequent rows.

Example broken output:
```
| 3 | ligandfit | ✗ | FAILED: Sorry: Sorry LigandFit failed
Please... | | 4 | pdbtools | ✓ | Completed |
```

**Solution**: Sanitize the `key_metric` field before adding to table:
- Replace newlines with spaces
- Replace pipe characters (`|`) with dashes
- Collapse multiple spaces
- Truncate to 60 characters max

### Files Changed

- `agent/session.py` - Sanitize key_metric in `_format_summary_as_markdown()`

---

## Version 100 (January 2025)

### Bug Fix: Ligand File Detection Pattern

**Problem**: `7qz0_ligand.pdb` wasn't detected as a ligand file because patterns only matched `ligand_*.pdb` (ligand at start), not `*_ligand.pdb` (ligand at end).

**Solution**: Added patterns `*_ligand.pdb` and `*_lig.pdb` to `ligand_pdb` category.

### Bug Fix: predict_and_build Map Coefficients for Ligandfit

**Problem**: After `predict_and_build`, ligandfit was using `*_refinement.mtz` which doesn't contain map coefficients. The correct file is `*_map_coeffs.mtz` with labels `FP PHIFP`.

**Solution**:
1. Added new `predict_and_build_mtz` category for `*map_coeffs*.mtz` files
2. Updated ligandfit input priorities to include `predict_and_build_mtz`
3. Added `denmod_mtz` and `predict_and_build_mtz` to `specific_subcategories` in command builder so category-based selection takes priority over `best_files`
4. Added invariant to switch labels to `"FP PHIFP"` when using map_coeffs files

### Files Changed

- `knowledge/file_categories.yaml`:
  - Added `*_ligand.pdb` and `*_lig.pdb` patterns to `ligand_pdb`
  - Added `predict_and_build_mtz` category for map coefficient files
- `knowledge/programs.yaml`:
  - Updated ligandfit mtz priorities: `[denmod_mtz, predict_and_build_mtz, refined_mtz]`
  - Added invariant for predict_and_build labels (`FP PHIFP`)
- `agent/command_builder.py`:
  - Added `denmod_mtz` and `predict_and_build_mtz` to `specific_subcategories`
- `tests/tst_file_categorization.py`:
  - Added `test_ligand_file_patterns`
  - Added `test_predict_and_build_mtz_detection`

---

## Version 99 (January 2025)

### Feature: maximum_automation=False Now Works for X-ray

**Previously**: `maximum_automation=False` (stepwise mode) only affected cryo-EM workflows, forcing `stop_after_predict=True` for `predict_and_build`.

**Now**: Stepwise mode also applies to X-ray workflows. When `maximum_automation=False`:
- `predict_and_build` will use `stop_after_predict=True` in states: `xray_initial`, `xray_placed`
- This gives users more control over the workflow with intermediate checkpoints
- User can then run `process_predicted_model` → `phaser` → `refine` separately

### Usage

```bash
# Stepwise mode - more control with intermediate checkpoints
phenix.ai_agent maximum_automation=False original_files="data.mtz sequence.fa"
```

### Workflow Comparison

**Automated (maximum_automation=True, default)**:
```
xray_initial → xtriage → predict_and_build(full) → xray_refined
```

**Stepwise (maximum_automation=False)**:
```
xray_initial → xtriage → predict_and_build(stop_after_predict)
                              ↓
              process_predicted_model → phaser → refine → xray_refined
```

### Files Changed

- `agent/graph_nodes.py` - Extended stepwise mode handling to X-ray states
- `agent/docs_tools.py` - Updated workflow documentation diagrams
- `agent/workflow_state.py` - Updated stepwise hint message
- `docs/README.md` - Added automation modes section and quick start example
- `tests/tst_integration.py` - Added `test_xray_stepwise_forces_stop_after_predict`
- `tests/tst_workflow_state.py` - Added X-ray stepwise tests

---

## Version 98 (January 2025)

### Bug Fix: predict_and_build Counts as Refinement for ligandfit

**Problem**: After running `phenix.predict_and_build`, `phenix.ligandfit` was unavailable because `refine_count=0`:
```
PERCEIVE: phenix.ligandfit unavailable: refine_count=0 does not satisfy condition '> 0'
```

But predict_and_build includes internal refinement cycles and produces the same outputs (map coefficients) that ligandfit needs.

**Solution**: When a full `predict_and_build` run completes (not stopped early), increment `refine_count` so downstream programs like `ligandfit` know there's a refined model with map coefficients.

### Files Changed

- `agent/workflow_state.py` - Increment `refine_count` for successful full predict_and_build runs

---

## Version 97 (January 2025)

### Bug Fix: PredictAndBuild Output Categorized as Model (Not Search Model)

**Problem**: Files like `PredictAndBuild_0_overall_best.pdb` were incorrectly categorized as `search_model` instead of `model`. This happened because:
1. The file contains "predict" in the name
2. Multiple categories had `excludes: ["*predict*"]`
3. The file fell through to the `predicted` category (parent: `search_model`)

This caused the sanity check to fail with:
```
PERCEIVE: RED FLAG [search_model_not_positioned]: Cannot refine: search model found but not yet positioned
```

**Solution**: Added new `predict_and_build_output` category that specifically matches `PredictAndBuild_*_overall_best*.pdb` files and categorizes them as `model` (positioned, ready for refinement).

Also added exclusions to the `predicted` category to ensure these files don't get double-categorized.

### Files Changed

- `knowledge/file_categories.yaml` - Added `predict_and_build_output` category, added exclusions to `predicted`
- `tests/tst_file_categorization.py` - Added test for PredictAndBuild output categorization

---

## Version 96 (January 2025)

### Bug Fix: LLM Slot Alias Mapping for MTZ Files

**Problem**: When the LLM requested an MTZ file using the slot name `data` (e.g., `data=PredictAndBuild_0_overall_best_refinement.mtz`), but the program defined the input slot as `mtz`, the LLM's file choice was ignored and the wrong file was auto-selected.

**Example Debug Output (Before)**:
```
BUILD: LLM requested files: {model=..., data=PredictAndBuild_0_overall_best_refinement.mtz}
BUILD: Skipping best_files for mtz (program needs specific subcategory)
BUILD: Auto-filled mtz=PredictAndBuild_0_refinement_cycle_2.extended_r_free.mtz  # WRONG!
```

**Solution**: Added `SLOT_ALIASES` mapping that translates common LLM slot names to canonical program input names:
- `data` → `mtz`
- `pdb` → `model`
- `seq_file` → `sequence`
- etc.

**Example Debug Output (After)**:
```
BUILD: LLM requested files: {model=..., data=PredictAndBuild_0_overall_best_refinement.mtz}
BUILD: Mapped LLM slot 'data' to 'mtz'
BUILD: Using LLM-selected file for mtz
```

### Files Changed

- `agent/command_builder.py` - Added `SLOT_ALIASES` dict and updated LLM file processing to use aliases
- `tests/tst_command_builder.py` - Added tests for slot alias mapping

---

## Version 94 (January 2025)

### New Feature: Explain Why Programs Are Unavailable

When a program like `phenix.ligandfit` is not available, the debug output now explains WHY:

```
PERCEIVE: Valid programs: phenix.refine, phenix.polder, STOP
PERCEIVE: phenix.ligandfit unavailable: missing required file: ligand_file
```

This helps diagnose issues when expected programs aren't offered.

### Possible Explanations

- `missing required file: ligand_file` - No ligand file (.pdb/.cif) detected
- `missing required file: sequence` - No sequence file (.fa/.seq) detected
- `already completed: ligandfit` - Program already ran (not_done condition failed)
- `r_free=0.40 does not satisfy condition '< 0.35'` - Metric threshold not met
- `refine_count=0 does not satisfy condition '> 0'` - Needs refinement first
- `run_once program already executed` - Program like xtriage already ran

### Files Changed

- `agent/workflow_engine.py` - Added `explain_unavailable_program()` method, added `unavailable_explanations` to workflow state
- `agent/graph_nodes.py` - Added debug logging for unavailable programs

---

## Version 93 (January 2025)

### New Feature: Density Modification Workflow for X-ray

- **Added `phenix.autobuild_denmod` to X-ray workflow**: Before ligand fitting, the agent can now run density modification using `phenix.autobuild maps_only=True`. This creates improved map coefficients (`overall_best_denmod_map_coeffs.mtz` with FWT/PHFWT labels) for better ligand fitting.

### Workflow Changes

The X-ray refine phase now includes:
1. `phenix.refine` (preferred) - standard refinement
2. `phenix.autobuild` - when R-free stuck above threshold
3. **`phenix.autobuild_denmod`** (NEW) - density modification before ligandfit
4. `phenix.ligandfit` - fit ligand when model is good enough
5. `phenix.polder` - omit map calculation

### Technical Changes

- **Prompt clarification**: Added warning that `predict_and_build` is NOT for density modification
- **New file category**: `denmod_mtz` for density-modified MTZ files
- **Ligandfit label switching**: Automatically uses `FWT PHFWT` labels when denmod MTZ is selected
- **Done flag tracking**: Added `autobuild_denmod_done` flag

### Documentation Updates

- **docs/OVERVIEW.md**: Updated workflow example with autobuild_denmod, updated done flags table
- **docs/guides/ADDING_PROGRAMS.md**: Added autobuild_denmod_done to flags table, added note about refine_count/rsr_count
- **docs/guides/TESTING.md**: Updated test table with counts and added "Key Tests for Recent Fixes" section
- **tests/run_all_tests.py**: Updated docstring with current test list and key tests

### Files Changed

- `knowledge/workflows.yaml` - Added autobuild_denmod to refine phase
- `knowledge/programs.yaml` - Added denmod_labels invariant to ligandfit
- `knowledge/file_categories.yaml` - Added denmod_mtz category
- `knowledge/prompts_hybrid.py` - Added autobuild_denmod description, warned about predict_and_build
- `agent/workflow_state.py` - Added autobuild_denmod_done flag and detection
- `agent/command_builder.py` - Added file_matches invariant handling
- `docs/OVERVIEW.md` - Updated workflow examples and done flags
- `docs/guides/ADDING_PROGRAMS.md` - Updated done flags table
- `docs/guides/TESTING.md` - Updated test descriptions
- `tests/run_all_tests.py` - Updated docstring

---

## Version 72 (January 2025)

### Bug Fixes

- **Fixed cycle count to exclude STOP cycles (v92)**: The session summary was reporting "Cycles: 5 (4 successful)" when 4 programs ran and cycle 5 was just STOP. Now STOP cycles are excluded from the count, so it correctly reports "Cycles: 4 (4 successful)".

### Tests Added

- `test_stop_cycle_excluded_from_count` - Verifies STOP cycles are excluded from total_cycles and successful_cycles
- `test_cryoem_done_flags` - Verifies done flags are set for cryo-EM programs (resolve_cryo_em_done, map_sharpening_done, map_to_model_done, dock_done)

### Documentation Updated

- `docs/OVERVIEW.md` - Added "Program Execution Controls" section documenting `not_done` conditions and `run_once` flags with complete table of done flags
- `docs/guides/ADDING_PROGRAMS.md` - Added "Available done flags" table showing all manually-tracked done flags
- `docs/guides/TESTING.md` - Updated test count table with new test files

### Files Changed

- `agent/session.py` - Exclude STOP/None/unknown from total_cycles and successful_cycles
- `agent/session_tools.py` - Exclude STOP cycles from print_session_summary()
- `tests/tst_session_summary.py` - Added test_stop_cycle_excluded_from_count
- `tests/tst_workflow_state.py` - Added test_cryoem_done_flags

---

## Version 71 (January 2025)

### Bug Fixes

- **Fixed predict_and_build running without resolution for X-ray (v91)**: When user provides `program_settings` for predict_and_build (e.g., `rebuilding_strategy=Quick`), the program was being added to valid_programs even before xtriage ran. This caused predict_and_build to run without resolution, forcing `stop_after_predict=True` (prediction only). Now `_check_program_prerequisites` requires xtriage_done (X-ray) or mtriage_done (cryo-EM) before adding predict_and_build from program_settings.

- **Fixed resolution requirement for predict_and_build full workflow**: The command builder now correctly requires resolution for BOTH X-ray and cryo-EM when `stop_after_predict=False`. If resolution is not available, it forces `stop_after_predict=True` with a message suggesting to run xtriage/mtriage first.

### Root Cause

The workflow engine's `_apply_directives` was adding `predict_and_build` to valid_programs whenever the user had `program_settings` for it:

```python
# Before: Always allowed predict_and_build
if program == "phenix.predict_and_build":
    return True  # Always allow - worst case it does prediction-only
```

This bypassed the normal workflow phase ordering (xtriage → obtain_model).

### The Fix

```python
# After: Require xtriage/mtriage to be done first
if program == "phenix.predict_and_build":
    if phase_name in ("obtain_model", "molecular_replacement", "dock_model"):
        return True  # Let the phase conditions handle it
    if context.get("xtriage_done") or context.get("mtriage_done"):
        return True
    return False  # Don't add to early phases
```

### Correct Workflow Now

1. xtriage runs first → extracts resolution
2. predict_and_build runs with resolution → full workflow (prediction + MR + building)

### Files Changed

- `agent/workflow_engine.py` - Fixed `_check_program_prerequisites` to require xtriage/mtriage
- `agent/command_builder.py` - Fixed `_apply_invariants` to require resolution for full workflow

---

## Version 70 (January 2025)

### Bug Fixes

- **Fixed programs running repeatedly without stopping (v90)**: Multiple programs were missing `not_done` conditions in their workflow definitions, allowing the LLM to choose them repeatedly even after successful completion. Added protection to all one-time-run programs.

### Programs Now Protected from Re-runs

**X-ray workflow:**
- `phenix.predict_and_build` - `not_done: predict_full`
- `phenix.phaser` - `not_done: phaser` (in both obtain_model and molecular_replacement phases)

**Cryo-EM workflow:**
- `phenix.predict_and_build` - `not_done: predict`
- `phenix.dock_in_map` - `not_done: dock`
- `phenix.map_to_model` - `not_done: map_to_model`
- `phenix.resolve_cryo_em` - `not_done: resolve_cryo_em` (in all 4 phases where it appears)
- `phenix.map_sharpening` - `not_done: map_sharpening` (in all 4 phases where it appears)

**Previously protected (unchanged):**
- `phenix.autobuild`, `phenix.autosol`, `phenix.ligandfit`, `phenix.map_symmetry`, `phenix.process_predicted_model`

### Programs Intentionally Without Protection (run multiple times)

- `phenix.refine` / `phenix.real_space_refine` - Iterative refinement
- `phenix.molprobity` / `phenix.model_vs_data` / `phenix.validation_cryoem` - Validation after each cycle
- `phenix.polder` - May run for different sites
- `phenix.pdbtools` - May add multiple ligands
- `phenix.xtriage` / `phenix.mtriage` - Already have `run_once: true` in programs.yaml

### Files Changed

- `knowledge/workflows.yaml` - Added `not_done` conditions to 11 program entries
- `agent/workflow_state.py` - Added done flags for `map_to_model`, `resolve_cryo_em`, `map_sharpening`

---

## Version 69 (January 2025)

### Bug Fixes

- **Fixed predict_and_build forcing `stop_after_predict=True` for X-ray (v89)**: The command builder was forcing `stop_after_predict=True` whenever resolution wasn't in the context, even for X-ray where predict_and_build can read resolution directly from the MTZ file. This caused predict_and_build to run in prediction-only mode repeatedly (3 retry cycles due to duplicate detection). Now `stop_after_predict=True` is only forced for cryo-EM without resolution.

### Root Cause

The command builder had this logic:
```python
if program == "phenix.predict_and_build":
    if not context.resolution and "stop_after_predict" not in strategy:
        strategy["stop_after_predict"] = True
```

This applied to BOTH X-ray and cryo-EM, but:
- For X-ray: predict_and_build can determine resolution from the MTZ automatically
- For cryo-EM: mtriage should run first to get resolution

The fix restricts this to cryo-EM only:
```python
if context.experiment_type == "cryoem":
    if not context.resolution and "stop_after_predict" not in strategy:
        strategy["stop_after_predict"] = True
```

### Why 3 LLM Calls Were Happening

1. LLM chose predict_and_build
2. Command builder forced `stop_after_predict=True` (same as previous run)
3. Validate detected duplicate command → `validation_error`
4. Graph looped back to Plan (retry 1)
5. Same result → retry 2
6. Same result → retry 3 → fallback

### Files Changed

- `agent/command_builder.py` - Only force `stop_after_predict=True` for cryo-EM

---

## Version 68 (January 2025)

### Bug Fixes

- **Fixed ligandfit using input MTZ instead of refined MTZ (v88)**: When ligandfit requires `refined_mtz` (an MTZ with map coefficients), and no refined MTZ exists (because refinement failed), the code was falling back to extension-based matching and selecting the input MTZ. Now when a program requires a specific subcategory (like `refined_mtz`), extension-based fallback is disabled.

- **Fixed refine_count incrementing for failed refinements (v88)**: Previously, `refine_count` was incremented regardless of whether refinement succeeded or failed. This caused workflow conditions like `refine_count > 0` (used by ligandfit) to pass even when no successful refinement had occurred. Now only successful refinements increment the count.

- **Fixed rsr_count incrementing for failed RSR (v88)**: Same fix applied to `rsr_count` for `phenix.real_space_refine`.

### Root Cause

The session showed:
```
Categorized files: model=1, search_model=15, mtz=1, sequence=10, ...
```
No `refined_mtz` category because refinement failed. But `refine_count` was 2 (from failed attempts), so ligandfit was allowed. Then command_builder fell back to extension matching and selected the input MTZ.

### Files Changed

- `agent/command_builder.py` - Added check to prevent extension-based fallback when specific subcategory required
- `agent/workflow_state.py` - Added success checking for refine_count and rsr_count in `_analyze_history()`
- `tests/tst_workflow_state.py` - Added `test_failed_refine_not_counted`

---

## Version 67 (January 2025)

### Bug Fixes

- **Fixed stop_after_predict=True being suggested for X-ray (v87)**: The prompt was incorrectly telling the LLM to use `stop_after_predict=True` for ANY stepwise workflow, but this should only apply to cryo-EM stepwise workflows. For X-ray, `predict_and_build` should run the full workflow (prediction → MR → building). Added experiment_type check so the guidance only appears for cryo-EM.

- **Clarified predict_and_build documentation in prompts**: Added explicit notes explaining that by default `predict_and_build` runs the FULL workflow, and `stop_after_predict=True` should only be used for cryo-EM stepwise.

### Root Cause

When user says "stop after PredictAndBuild", the LLM was confusing:
- "Stop the agent workflow after predict_and_build completes" (correct interpretation)
- "Set stop_after_predict=True" (incorrect - this only runs prediction, skipping MR and building)

The prompt was adding `NOTE: Use predict_and_build with strategy: {"stop_after_predict": true}` for stepwise workflows without checking if it's cryo-EM or X-ray.

### Files Changed

- `knowledge/prompts_hybrid.py` - Added experiment_type check for stop_after_predict guidance; clarified predict_and_build documentation

---

## Version 66 (January 2025)

### Critical Bug Fixes

- **Fixed predicted model incorrectly becoming best model after refinement (v85)**: When `phenix.refine` runs, the directory scan picks up ALL files (including pre-existing ones like `PredictAndBuild_0_predicted_model_processed.pdb`). Previously, `record_result()` blindly applied `stage="refined"` to ALL PDB files in `output_files`, causing the predicted model to get a higher score than the actual refined model. Now `record_result()` only applies the program stage to files whose basename matches expected output patterns (e.g., only files containing "refine" get `stage="refined"`).

- **Fixed PHASER models getting inflated scores from refinement metrics**: Similar to above - `PHASER.1.pdb` in refinement's `output_files` was getting `stage="refined"` and metrics from phenix.refine, inflating its score from 70 to 132. Now handled by the same pattern matching fix.

- **Fixed STOP not available after user's workflow completes (v84)**: When user directives specify `after_program` (e.g., "stop after refinement"), STOP is now added to valid_programs after that program completes. Previously STOP was only available after validation, forcing unwanted extra cycles.

### Consistency Fixes (v86)

- **Synchronized pattern matching across all three locations**:
  - `session.py:_rebuild_best_files_from_cycles()` - Rebuild from saved session
  - `session.py:record_result()` - Real-time recording
  - `session_tools.py:rebuild_best_files()` - Manual rebuild tool

- **Added missing program-to-stage mappings**:
  - `session.py:_infer_stage_from_program()` - Added `predict_and_build`, `ligandfit`, `pdbtools`
  - `session_tools.py:infer_stage()` - Added `process_predicted_model`

- **Added missing filename patterns**:
  - `session_tools.py` - Added `processed_predicted` and `autobuild_output` patterns

### Bug Details

**The predicted model bug (v85)**:
- Root cause: `output_files` from directory scanning includes ALL files in the working directory, not just files created by the program
- `record_result()` was giving ALL PDB files the program's stage (e.g., "refined" for phenix.refine)
- This caused `PredictAndBuild_0_predicted_model_processed.pdb` to get `stage="refined"` with the refinement metrics, giving it score 133 vs PHASER.1.pdb's score 132
- Fix: Pattern matching in `record_result()` now matches `_rebuild_best_files_from_cycles()` - only files with matching basenames get the program stage

### Testing

- Added test `test_predicted_model_not_promoted_by_refine` to verify predicted models don't get wrongly promoted when they appear in refine's output_files
- Added test `test_phaser_model_not_promoted_by_refine_metrics` to verify PHASER models don't get refinement metrics
- Added test `test_stop_added_after_after_program_completes` to verify STOP is available after after_program completes

### Files Changed

- `agent/session.py` - Fixed `record_result()` to pattern-match PDB filenames before applying stage; added missing programs to `_infer_stage_from_program()`
- `agent/session_tools.py` - Added `process_predicted_model` to `infer_stage()`; added `processed_predicted` and `autobuild_output` filename patterns
- `agent/workflow_engine.py` - Added after_program completion check in `_apply_directives()`
- `tests/tst_best_files_tracker.py` - Added predicted model and PHASER model promotion tests
- `tests/tst_workflow_state.py` - Added STOP after after_program test

---

## Version 65 (January 2025)

### Major Bug Fixes

- **Fixed map files getting wrong stage in best_files (v79)**: All map files from resolve_cryo_em were incorrectly getting `stage=optimized_full_map` instead of being classified by filename patterns. This caused `initial_map.ccp4` to win over `denmod_map.ccp4`. Now map files are classified based on their basename: `initial*` → intermediate_map, `denmod*` → optimized_full_map, `sharp*` → sharpened, `half*` → half_map.

- **Fixed rebuild function missing MTZ/PDB stage patterns (v80)**: The `_rebuild_best_files_from_cycles()` function was missing:
  - Phased MTZ detection (`*phased*`, `*phases*`, `*solve*` → `phased_mtz`)
  - Generic MTZ fallback (→ `mtz` stage)
  - `processed_predicted` pattern for process_predicted_model outputs
  - `autobuild_output` pattern for autobuild outputs

- **Fixed autobuild_done set even on failure (v77)**: Previously `autobuild_done=True` was set just because autobuild appeared in history, regardless of success/failure. Now checks for "FAIL", "SORRY", "ERROR" in result before marking done, allowing the agent to try alternatives when autobuild fails.

- **Fixed LLM file suggestions with wrong extension (v77)**: Added extension validation when LLM suggests files for input slots. Now rejects files with wrong extension (e.g., `.ccp4` for model slot which expects `.pdb/.cif`).

- **Allow programs from directives even when workflow state is "past" that phase (v81)**: When user has `program_settings` for a program (e.g., `phenix.predict_and_build`), that program is now added to `valid_programs` even if the workflow state thinks we're past that phase. This allows users to explicitly request earlier-phase programs.

### New Features

- **Programs from program_settings added to valid_programs**: If user directives include settings for a specific program, that program is automatically added to the list of valid programs (subject to prerequisite checks). This respects user intent when they've configured a program they want to run.

- **_check_program_prerequisites() helper**: New method in WorkflowEngine that centralizes prerequisite checking for programs being added via directives. Checks:
  - Refinement programs need a model to refine
  - Ligandfit needs prior refinement (for map coefficients)
  - predict_and_build is always allowed (worst case: prediction-only)

### Testing

- Added tests for directive-based program addition in tst_workflow_state.py:
  - `test_program_settings_adds_program_to_valid`
  - `test_program_settings_prioritizes_program`
  - `test_program_settings_respects_prerequisites`
  - `test_default_program_settings_ignored`

### Files Changed

- `agent/workflow_engine.py` - Added `_check_program_prerequisites()`, enhanced `_apply_directives()`
- `agent/workflow_state.py` - Fixed autobuild_done to check for success
- `agent/session.py` - Fixed map file stage assignment, complete rebuild function
- `agent/command_builder.py` - Added LLM file extension validation
- `tests/tst_workflow_state.py` - Added directive program tests

---

## Version 64 (January 2025)

### Major Bug Fixes

- **Fixed best_files rebuild from cycle history (v69)**: Previously, `best_files` was persisted in session.json and could get stale when cycles were removed. Now `Session.load()` always rebuilds `best_files` from the cycle history, ensuring consistency. Removed redundant `best_files.evaluate_file()` call from `_track_output_files()` that was overwriting good evaluations with `metrics=None`.

- **Fixed smart stage assignment in rebuild (v71)**: When rebuilding best_files, stage was blindly applied to all files in a cycle's output_files. Now only applies program-specific stage (e.g., "refined") to files whose basename matches expected patterns (e.g., contains "refine"). This prevents PHASER.1.pdb from incorrectly getting `stage=refined` when it appears in a refinement cycle's output_files.

- **Fixed after_cycle directive for ligand workflows (v72)**: When user says "stop after second refinement" with a ligand workflow, LLM incorrectly extracted `after_cycle: 2`. Extended `_fix_ligand_workflow_conflict()` to also clear `after_cycle <= 4` when ligand constraints are present, since ligand workflows need ~8 cycles minimum.

- **Fixed ligandfit using wrong MTZ file (v73)**: phenix.ligandfit needs an MTZ with map coefficients (2FOFCWT, PH2FOFCWT) from refinement, but was getting the original data MTZ. Added logic to skip generic `best_files["mtz"]` when `input_priorities` specifies a specific subcategory like `refined_mtz`.

- **Fixed cryo-EM dock_in_map using initial_map instead of denmod_map (v74)**: After resolve_cryo_em runs, dock_in_map was selecting `initial_map.ccp4` (intermediate) instead of `denmod_map.ccp4` (density-modified output). Added `optimized_full_map` category with score 100, `intermediate_map` with score 5, and proper pattern matching for `denmod_map`, `density_modified`, etc.

- **Fixed pdbtools output naming**: Added `fixes.output_name` to pdbtools configuration to generate output filenames like `{protein_base}_with_ligand.pdb`, ensuring the combined model is properly categorized for downstream programs.

### New Categories & Scoring

**Map Categories (v74):**
| Stage | Score | Description |
|-------|-------|-------------|
| optimized_full_map | 100 | denmod_map, density_modified, sharpened |
| sharpened | 90 | Sharpened maps |
| full_map | 50 | Regular full reconstructions |
| half_map | 10 | Half-maps for FSC |
| intermediate_map | 5 | initial_map (resolve_cryo_em intermediate) |

### Testing

- Added tests for optimized_full_map scoring and classification
- Added tests for intermediate_map low priority
- Added tests for ligand workflow after_cycle clearing
- Added tests for docked model bubbling to model category

### Files Changed

- `agent/session.py` - Added `_rebuild_best_files_from_cycles()`, always rebuild on load
- `agent/session_tools.py` - Updated `rebuild_best_files()` with smart stage assignment
- `agent/command_builder.py` - Skip best_files for specific subcategories
- `agent/directive_extractor.py` - Extended ligand workflow fix for after_cycle
- `agent/best_files_tracker.py` - Added `intermediate_map` stage and scoring
- `knowledge/file_categories.yaml` - Added `optimized_full_map`, `intermediate_map` categories
- `knowledge/programs.yaml` - Updated resolve_cryo_em outputs, pdbtools output naming
- `programs/ai_agent.py` - Added `placed_model`/`docked` to valuable_output_patterns
- `tests/tst_best_files_tracker.py` - New map scoring tests
- `tests/tst_directive_extractor.py` - New ligand workflow tests
- `tests/tst_file_categorization.py` - New cryo-EM map categorization tests

---

## Version 63 (January 2025)

### Major Bug Fixes

- **Fixed predict_and_build output tracking**: Output files in `CarryOn/` directories were being incorrectly excluded as "intermediate files". Added `valuable_output_patterns` list that overrides intermediate exclusions for important outputs like `*_predicted_model*.pdb`, `*overall_best*.pdb`, `*_processed*.pdb`.

- **Fixed workflow state detection for predicted models**: The `_has_placed_model()` function was incorrectly returning `True` when user directives mentioned `phenix.refine` AND any PDB file existed - even if that PDB was a search_model (unpositioned). Now properly checks that PDB files are not in `search_model`, `predicted`, or `processed_predicted` categories before considering the model "placed".

- **Fixed pdbtools file selection**: Added `input_priorities` to `phenix.pdbtools` in programs.yaml to properly select refined model + ligand instead of predicted model. Added support for `priority_patterns` and `prefer_patterns` in command_builder.py.

- **Fixed ligand workflow directive conflict**: When user wants "refine, fit ligand, refine again", the directive extractor was incorrectly setting `after_program: phenix.refine` which stopped at the first refinement. Added `_fix_ligand_workflow_conflict()` post-processing that clears `after_program` when ligand-related constraints are present.

### New Features

- **Automatic Safety Documentation Generator** (`agent/generate_safety_docs.py`): Script that scans the codebase and generates a comprehensive table of all safety checks, validations, and post-processing corrections. Run with `python agent/generate_safety_docs.py > docs/SAFETY_CHECKS.md`.

- **Simplified Verbosity Levels**: Reduced from 4 levels to 3 levels (quiet/normal/verbose). The `debug` level has been removed; `verbose` now includes all detailed output including file selection, LLM traces, and internal state. For backwards compatibility, `debug` is accepted as input but treated as `verbose`.

- **File existence retry mechanism**: Added retry logic (3 attempts, 0.1s delay) in `resolve_file_path()` to handle race conditions where log mentions a file before it's fully written to disk.

- **Session file fsync**: Added explicit `fsync()` call when saving session files to ensure data is written to disk immediately.

### Documentation

- **New SAFETY_CHECKS.md**: Auto-generated documentation of all 70+ safety checks categorized by type:
  - Sanity Checks (Pre-Execution): 20
  - Directive Validation (Post-LLM): 7
  - File Validation: 4
  - Workflow State Validation: 8
  - Command Building Validation: 3
  - Input Validation: 29
  - Post-Processing Corrections: 4

- **New PROGRAM_CONFIG_ROBUSTNESS.md**: Implementation plan for making program configuration more robust with sensible defaults, validation warnings, and dry_run_file_selection mode.

### Files Changed

- `programs/ai_agent.py` - CarryOn fix, simplified verbosity, fsync
- `agent/workflow_engine.py` - Fixed `_has_placed_model()` to exclude search_models
- `agent/command_builder.py` - Added priority_patterns/prefer_patterns support
- `agent/best_files_tracker.py` - CarryOn fix with valuable_output_patterns
- `agent/directive_extractor.py` - Enhanced LLM prompt, added `_fix_ligand_workflow_conflict()`
- `agent/session.py` - Added fsync to save()
- `phenix_ai/log_parsers.py` - Added file existence retry in resolve_file_path()
- `knowledge/programs.yaml` - Added input_priorities for pdbtools
- `agent/generate_safety_docs.py` - Safety documentation generator
- `docs/SAFETY_CHECKS.md` - New auto-generated safety documentation
- `docs/implementation/PROGRAM_CONFIG_ROBUSTNESS.md` - New implementation plan

---

## Version 42 (January 2025)

### Testing Infrastructure

- **Converted all tests to cctbx-style**: Migrated 8 test files (300+ tests) from `unittest.TestCase` to plain functions with fail-fast behavior
  - Matches PHENIX/cctbx testing conventions
  - First assertion failure stops with full traceback
  - Simpler syntax without class wrappers

- **New test utilities module** (`tests/tst_utils.py`):
  - 20+ assert helper functions (`assert_equal`, `assert_in`, `assert_true`, etc.)
  - `run_tests_with_fail_fast()` for cctbx-style test execution
  - Supports both plain functions and TestCase classes for gradual migration

- **New testing documentation** (`docs/guides/TESTING.md`):
  - Complete guide to writing and running tests
  - Migration guide from unittest to cctbx style
  - Best practices and conventions

### Bug Fixes

- **`sanitize_for_transport()` now handles all types**: Previously converted dicts/lists to string representation. Now recursively sanitizes nested structures while preserving their types.
  - Dicts → sanitized dicts
  - Lists → sanitized lists
  - None/int/float/bool → passed through unchanged
  - Strings → sanitized (control chars, tabs, markers removed)

- **`encode_for_rest()` handles non-string input**: Added type checking to JSON-encode dicts/lists before REST encoding, preventing AttributeError on dict input.

- **`validate_directives()` preserves `file_preferences`**: Added support for boolean preferences (`prefer_anomalous`, `prefer_unmerged`, `prefer_merged`) which were previously dropped.

- **`_fix_program_name()` expanded aliases**: Added mappings for:
  - `sharpen_map`, `auto_sharpen` → `phenix.map_sharpening`
  - `build_model`, `buildmodel` → `phenix.map_to_model`

### New Directive Patterns

- **Atom type extraction**: "use selenium", "Se-SAD", "sulfur SAD" → sets `atom_type` in autosol settings
- **File preferences**: "use anomalous data", "prefer unmerged data" → sets `file_preferences`
- **Workflow preferences**: "skip autobuild", "avoid ligandfit" → adds to `skip_programs` list
- **Stop after refinement**: "stop after refinement" now works (previously required "stop after THE FIRST refinement")

### Files Changed

- `agent/transport.py` - Fixed `sanitize_for_transport()` and `encode_for_rest()`
- `agent/directive_extractor.py` - Added new patterns, fixed `validate_directives()`, expanded `_fix_program_name()`
- `tests/tst_utils.py` - New assert helpers and test runner
- `tests/tst_*.py` - Converted to cctbx-style (8 files)
- `docs/guides/TESTING.md` - New testing documentation
- `docs/README.md` - Updated testing section

---

## Version 41 (January 2025)

### Enhancements
- **Enhanced session summary**: Improved AI-generated summary to include:
  - **Key Output Files**: Now shows file name, type, and descriptive text explaining what each file contains (e.g., "Refined atomic model (X-ray)", "Structure factors with R-free flags and map coefficients")
  - **Key Metrics**: Enhanced prompt requests specific metric values and names; added extraction for Ramachandran outliers, rotamer outliers, and MolProbity score
  - Output files table now formatted with File/Type/Description columns
  - LLM prompt explicitly requests formatted metrics list with values

- **Multi-step workflow support**: Added `start_with_program` directive for handling sequences like "run polder then refine"
  - When user specifies "calculate polder map and then run refinement", the system extracts `start_with_program: phenix.polder`
  - This tells the workflow "run this program first, then continue with normal workflow"
  - Different from `after_program` which means "run this and stop"
  - Cleaner semantics than a `required_programs` list

- **Fixed directive override behavior**: Safer attempt-based strategy
  - First attempt (attempt_number=0): Honor user's directive value (respect explicit request)
  - Retry attempts (attempt_number>0): Trust LLM's interpretation (it may be correcting syntax)
  - This is safer than always trusting one or the other:
    - User's explicit request gets a fair chance first
    - If it fails, LLM can try to fix potential syntax issues
  - Example: User says `selection=solvent molecule MES 88` (invalid syntax)
    - Attempt 0: Uses user's value → fails
    - Attempt 1: LLM interprets as `selection=resname MES and resseq 88` → succeeds

- **Fixed fallback program selection**: Fallback now respects `start_with_program` directive
  - Previously, if LLM failed 3 times, fallback would pick the first valid program (often xtriage)
  - Now fallback prioritizes `start_with_program` if set by directive

### Bug Fixes
- **phenix.polder workflow integration**: Fixed issue where LLM incorrectly assumed polder needs map coefficients from prior refinement
  - Added polder to PROGRAM REFERENCE in LLM system prompt with clear documentation that it works with standard MTZ data (Fobs + R-free flags)
  - Added polder to both `refine` and `validate` phases in workflows.yaml
  - Explicit clarification: "does NOT need pre-calculated map coefficients or phases"

- **Generic PDB file categorization**: Fixed critical bug where unclassified PDB files (e.g., `1aba.pdb`) were being categorized as `search_model` instead of `model`
  - Changed `unclassified_pdb.parent_category` from `search_model` to `model` in file_categories.yaml
  - Added `*search*`, `*sculptor*`, `*chainsaw*` to excludes list to prevent search model files from being miscategorized
  - This ensures generic PDB files are treated as positioned models ready for refinement
  - Files explicitly named as search models (e.g., `search_model.pdb`, `template.pdb`) correctly go to `search_model` category
  - Previously, providing a simple PDB file would cause the workflow to think Phaser/MR was needed
  - Now the workflow correctly recognizes the model is already placed and allows refinement/validation programs

### New Tests
- Added workflow configuration tests for polder (TestPolderWorkflowConfig)
- Added LLM prompt tests for polder (TestPolderLLMPrompt)
- Added file categorization tests (TestUnclassifiedPDBCategorization)
- Tests verify:
  - Polder is in correct workflow phases
  - Prompt clarifies polder doesn't need phases
  - Generic PDB files categorize as `model` not `search_model`

### Files Changed
- `knowledge/prompts_hybrid.py` - Added phenix.polder to VALIDATION PROGRAMS section
- `knowledge/workflows.yaml` - Added phenix.polder to xray refine and validate phases
- `knowledge/file_categories.yaml` - Changed unclassified_pdb parent_category to 'model', added excludes
- `agent/session.py` - Enhanced `_get_final_output_files()` with descriptions, added `_describe_output_file()`, enhanced `_extract_final_metrics()` with more metrics, updated LLM summary prompt
- `agent/directive_extractor.py` - Added `start_with_program` extraction for multi-step workflows
- `agent/directive_validator.py` - Added attempt-based override behavior, `validate_intent()` now accepts `attempt_number`
- `agent/workflow_engine.py` - Added `start_with_program` handling in `_apply_directives()`
- `agent/graph_nodes.py` - Pass `attempt_number` to `validate_intent()`, fallback respects `start_with_program`
- `tests/tst_new_programs.py` - Added TestPolderWorkflowConfig, TestPolderLLMPrompt, TestUnclassifiedPDBCategorization
- `tests/tst_workflow_state.py` - Fixed test_dock_in_map_option to use clear search model filename
- `docs/guides/USER_DIRECTIVES.md` - Added `start_with_program` docs, attempt-based override docs, multi-step example

---

## Version 40 (January 2025)

### New Features
- **USER_REQUEST_INVALID event**: When user requests a program that's not available (e.g., "run phenix.xxx"), the agent now displays a prominent warning explaining why the request can't be fulfilled and what will run instead
- Warning is shown at QUIET verbosity level (always visible)
- Distinguishes between "unknown program" and "wrong workflow state"

### Files Changed
- `agent/event_log.py` - Added USER_REQUEST_INVALID event type
- `agent/event_formatter.py` - Added formatter for prominent warning display
- `agent/graph_nodes.py` - Emit event when user request detected as invalid

---

## Version 39 (January 2025)

### Bug Fixes
- **Event transport plumbing**: Fixed events not flowing through in two edge cases:
  1. Single-shot mode via `run_job_on_server` - events now decoded from server response
  2. API result retrieval via `get_results_as_JSON()` - events now serialized in output_files

### Files Changed
- `programs/ai_agent.py` - Added events serialization in `_build_output_files_from_history`
- `programs/ai_agent.py` - Added events decoding in `run_job_on_server`

---

## Version 38 (January 2025)

### Event System Phase 4: Display Integration
- Added `verbosity` parameter to `phenix.ai_agent` command
- Integrated EventFormatter for consistent output formatting
- Added `_display_cycle_events()` method for event rendering
- Legacy fallback when events not available

### Files Changed
- `programs/ai_agent.py` - Verbosity parameter, EventFormatter integration

---

## Version 37 (January 2025)

### Event System Phase 3: Transport Integration
- Events included in v2 API response schema
- LocalAgent and RemoteAgent parse events from responses
- Events stored in history_record for persistence

### Files Changed
- `phenix_ai/run_ai_agent.py` - Include events in response
- `phenix_ai/local_agent.py` - Parse events from response
- `agent/api_schema.py` - Updated response schema

---

## Version 36 (January 2025)

### Event System Phase 2: Decision Point Instrumentation
- All graph nodes now emit structured events
- Full LLM reasoning captured without truncation
- File selection reasons tracked

### Files Changed
- `agent/graph_nodes.py` - Event emission in perceive, plan, build nodes

---

## Version 34 (January 2025)

### Event System Phase 1: Core Infrastructure
- Created EventLog class for structured logging
- Created EventFormatter for human-readable output
- Defined 17 event types with verbosity levels
- LangGraph state compatibility (list of dicts)

### New Files
- `agent/event_log.py` - EventLog class, EventType constants
- `agent/event_formatter.py` - EventFormatter class

---

## Version 33 (January 2025)

### Cleanup and Production Hardening
- Removed deprecated state.md files
- Removed redundant backup files
- Fixed program registration after import changes
- Updated test suites for new structure

---

## Version 32 (January 2025)

### Pattern Centralization
- Moved all regex patterns to `knowledge/patterns.yaml`
- Created PatternManager for centralized access
- Updated log_parsers.py to use PatternManager

### New Files
- `knowledge/patterns.yaml` - Centralized regex patterns
- `agent/pattern_manager.py` - Pattern loading and compilation

---

## Version 31 (January 2025)

### Unified Command Builder
- Single CommandBuilder class for all programs
- Reads program definitions from YAML
- Consistent file selection across all programs
- Strategy flags and defaults from YAML

### Files Changed
- `agent/command_builder.py` - Complete rewrite

---

## Version 30 (January 2025)

### File Categorization Consolidation
- Centralized file categorization in `file_categorization.py`
- Semantic categories: model vs search_model distinction
- Categories defined in `file_categories.yaml`

### New Files
- `knowledge/file_categories.yaml`
- `agent/file_categorization.py` - Centralized categorization

---

## Version 29 (January 2025)

### BestFilesTracker
- New class to track best file of each type across cycles
- Scores based on metrics (R-free, resolution)
- R-free flag locking after first refinement

### New Files
- `agent/best_files_tracker.py`

---

## Version 28 (January 2025)

### YAML Configuration System
- Programs defined in `programs.yaml`
- Workflows defined in `workflows.yaml`
- Metrics defined in `metrics.yaml`
- Transport rules defined in `transport.yaml`

### New Files
- `knowledge/programs.yaml`
- `knowledge/workflows.yaml`
- `knowledge/metrics.yaml`
- `knowledge/transport.yaml`
- `knowledge/yaml_loader.py`

---

## Version 25-27 (December 2024)

### User Directives System
- Natural language directive parsing
- Stop conditions: "stop after X", "stop when metric < Y"
- Workflow preferences: "skip program", "prefer program"
- Four-layer stop condition checking

### New Files
- `agent/directive_extractor.py`
- `agent/directive_validator.py`
- `docs/guides/USER_DIRECTIVES.md`

---

## Earlier Versions

### Initial Development (2024)
- LangGraph pipeline architecture
- LLM integration (Claude, Gemini)
- Rules-only fallback mode
- Local and remote execution modes
- Session tracking and history
- Sanity checking system
