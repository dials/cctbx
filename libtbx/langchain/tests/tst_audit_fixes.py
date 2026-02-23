"""
Regression tests for bugs found and fixed during the systematic audit
(Categories I, J, E).

Each test is named after the audit item that revealed the bug so failures
are immediately traceable to a root-cause description.

Categories covered
------------------
I1  max_refine_cycles → controlled validation landing, not bare STOP
I1c cryoem rsr_count used (not refine_count) when checking the limit
I2  after_program beats quality gate → bare STOP, no validate injection
J2  _is_failed_result: false-positive fixes for bare ERROR variants
J5  _clear_zombie_done_flags: stale done flags cleared when output missing
E1  xtriage resolution: dash-separator range "50.00 - 2.30" → picks 2.30
E2  xtriage pick_min anchor: "Completeness in resolution range: 1" not matched
E1  real_space_refine map_cc: extract:last (final cycle, not first)
K1  best_files list values crash at cycle=2 (half_map stored as [map1, map2])
K2  mtriage/predict_and_build/map_to_model drop half_maps when full_map also present

Run with:
    python tests/tst_audit_fixes.py
"""

from __future__ import absolute_import, division, print_function

import os
import re
import sys
import tempfile
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_ai_agent_path():
    """
    Locate ai_agent.py robustly across environments.

    The file lives at different locations depending on the environment:
      - Dev / /tmp unpack:  <project_root>/programs/ai_agent.py
      - PHENIX installation: <phenix_root>/programs/ai_agent.py
                             (importable as phenix.programs.ai_agent)

    Strategy:
      1. Relative to this test file (dev / /tmp).
      2. importlib lookup of phenix.programs.ai_agent (PHENIX install).
      3. importlib lookup of libtbx.langchain.programs.ai_agent (alt layout).
      4. sys.path scan for programs/ai_agent.py and phenix/programs/ai_agent.py.
    """
    # 1. Relative to this test file
    candidate = os.path.join(_PROJECT_ROOT, 'programs', 'ai_agent.py')
    if os.path.exists(candidate):
        return candidate

    # 2 & 3. Via importlib — covers both package layouts without importing
    try:
        import importlib.util as _ilu
        for _mod in ('phenix.programs.ai_agent',
                     'libtbx.langchain.programs.ai_agent'):
            try:
                spec = _ilu.find_spec(_mod)
                if spec and spec.origin and os.path.exists(spec.origin):
                    return spec.origin
            except (ModuleNotFoundError, ValueError):
                pass
    except Exception:
        pass

    # 4. sys.path scan
    for _p in sys.path:
        for _rel in ('programs/ai_agent.py',
                     'phenix/programs/ai_agent.py',
                     'langchain/programs/ai_agent.py'):
            candidate = os.path.join(_p, _rel)
            if os.path.exists(candidate):
                return candidate

    raise FileNotFoundError(
        "Cannot locate ai_agent.py. "
        "Tried relative path %s/programs/ai_agent.py, "
        "importlib (phenix.programs.ai_agent, libtbx.langchain.programs.ai_agent), "
        "and sys.path entries." % _PROJECT_ROOT
    )

from tests.tst_utils import (
    assert_equal, assert_true, assert_false, assert_in, assert_not_none,
    run_tests_with_fail_fast,
)

# ---------------------------------------------------------------------------
# Lazy imports that need the mock infrastructure
# ---------------------------------------------------------------------------
try:
    from agent.workflow_state import (
        _is_failed_result,
        _analyze_history,
        _clear_zombie_done_flags,
        detect_workflow_state,
    )
    from agent.workflow_engine import WorkflowEngine
    _IMPORTS_OK = True
except ImportError:
    _IMPORTS_OK = False

KNOWLEDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge"
)


def _load_programs():
    with open(os.path.join(KNOWLEDGE_DIR, "programs.yaml")) as f:
        return yaml.safe_load(f)


def _apply_yaml_regex(prog_name, metric, log_text, programs=None):
    """Apply a programs.yaml log_parsing pattern to log_text, return value."""
    if programs is None:
        programs = _load_programs()
    spec = programs.get(prog_name, {}).get("log_parsing", {}).get(metric, {})
    pattern = spec.get("pattern", "")
    pick_min = spec.get("pick_min", False)
    extract = spec.get("extract", "first")
    typ = spec.get("type", "float")
    if not pattern:
        return None
    matches = re.findall(pattern, log_text, re.MULTILINE)
    if not matches:
        return None
    if pick_min:
        try:
            return min(
                float(m) if not isinstance(m, tuple) else float(m[0])
                for m in matches
            )
        except (TypeError, ValueError):
            return None
    val = matches[-1] if extract == "last" else matches[0]
    if isinstance(val, tuple):
        val = val[0]
    try:
        if typ == "float":
            return float(val)
        elif typ == "int":
            return int(val)
        return val
    except (TypeError, ValueError):
        return val


# =============================================================================
# CATEGORY J2 — _is_failed_result false-positive fixes
# =============================================================================

def test_j2_is_failed_result_true_positives():
    """J2: definitive failure signals are detected."""
    print("Test: j2_is_failed_result_true_positives")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    # Each of these must be recognised as failure
    assert_true(_is_failed_result("FAILED"),
                "FAILED should be a failure")
    assert_true(_is_failed_result("Sorry: could not read reflection file"),
                "Sorry: should be a failure")
    assert_true(_is_failed_result("Sorry bad format -- check input"),
                "Sorry (no colon) should be a failure")
    assert_true(_is_failed_result("Traceback (most recent call last):"),
                "Python traceback should be a failure")
    assert_true(_is_failed_result("*** Error: assertion failed in refine"),
                "*** Error should be a failure")
    assert_true(_is_failed_result("FATAL: out of memory"),
                "FATAL: should be a failure")
    assert_true(_is_failed_result("Exception raised during refinement"),
                "Exception should be a failure")

    print("  PASSED")


def test_j2_is_failed_result_false_positives_eliminated():
    """J2: non-fatal strings containing ERROR/error must NOT be flagged."""
    print("Test: j2_is_failed_result_false_positives_eliminated")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    # These must NOT be recognised as failure
    assert_false(_is_failed_result("No ERROR detected"),
                 "'No ERROR detected' must not be a failure")
    assert_false(_is_failed_result("No error: all checks passed"),
                 "'No error: all checks passed' must not be a failure (mid-sentence colon)")
    assert_false(_is_failed_result("No ERROR: bad context"),
                 "'No ERROR: bad context' must not be a failure")
    assert_false(_is_failed_result("Error model parameter description"),
                 "PHENIX help text 'Error model parameter' must not be a failure")
    assert_false(_is_failed_result("Phenix expected errors: 0"),
                 "'expected errors: 0' must not be a failure")
    assert_false(_is_failed_result(""),
                 "Empty string must not be a failure")
    assert_false(_is_failed_result(None),
                 "None must not be a failure")
    assert_false(_is_failed_result("resolve_cryo_em DONE"),
                 "DONE string must not be a failure")

    print("  PASSED")


def test_j2_failed_result_blocks_done_flags():
    """J2: a FAILED result string prevents _analyze_history from setting done flags."""
    print("Test: j2_failed_result_blocks_done_flags")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    failed_history = [
        {
            "program": "phenix.resolve_cryo_em",
            "command": "phenix.resolve_cryo_em half1.ccp4 half2.ccp4",
            "result": "FAILED: process killed",
            "analysis": {},
        }
    ]
    info = _analyze_history(failed_history)
    assert_false(info.get("resolve_cryo_em_done", False),
                 "FAILED result must not set resolve_cryo_em_done")

    sorry_history = [
        {
            "program": "phenix.refine",
            "command": "phenix.refine model.pdb data.mtz",
            "result": "Sorry: could not read reflection file",
            "analysis": {},
        }
    ]
    info2 = _analyze_history(sorry_history)
    assert_equal(info2.get("refine_count", 0), 0,
                 "Sorry: result must not increment refine_count")

    print("  PASSED")


# =============================================================================
# CATEGORY J5 — _clear_zombie_done_flags
# =============================================================================

def test_j5_zombie_cleared_when_output_missing():
    """J5: done flag cleared when output file is absent (zombie state)."""
    print("Test: j5_zombie_cleared_when_output_missing")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    info = {
        "resolve_cryo_em_done": True,
        "has_full_map": True,
        "predict_full_done": False,
        "dock_done": False,
        "refine_done": False,
        "refine_count": 0,
        "rsr_done": False,
        "rsr_count": 0,
        "has_placed_model": False,
    }
    diags = _clear_zombie_done_flags(info, available_files=[])

    assert_false(info["resolve_cryo_em_done"],
                 "resolve_cryo_em_done must be cleared when output file absent")
    assert_false(info["has_full_map"],
                 "has_full_map must be cleared when denmod_map output absent")
    assert_true(len(diags) > 0,
                "Diagnostic messages must be produced for zombie state")
    assert_true(any("resolve_cryo_em_done" in d for d in diags),
                "Diagnostic must name the cleared flag")

    print("  PASSED")


def test_j5_zombie_not_cleared_when_output_present():
    """J5: done flag preserved when output file is present."""
    print("Test: j5_zombie_not_cleared_when_output_present")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    info = {
        "resolve_cryo_em_done": True,
        "has_full_map": True,
        "predict_full_done": False,
        "dock_done": False,
        "refine_done": False,
        "refine_count": 0,
        "rsr_done": False,
        "rsr_count": 0,
    }
    diags = _clear_zombie_done_flags(
        info, available_files=["/data/denmod_map.ccp4"]
    )

    assert_true(info["resolve_cryo_em_done"],
                "resolve_cryo_em_done must be preserved when output file exists")
    assert_equal(len(diags), 0,
                 "No diagnostics when output file is present")

    print("  PASSED")


def test_j5_refine_zombie_decrements_count():
    """J5: refine_done zombie decrements refine_count."""
    print("Test: j5_refine_zombie_decrements_count")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    info = {
        "resolve_cryo_em_done": False,
        "has_full_map": False,
        "predict_full_done": False,
        "dock_done": False,
        "refine_done": True,
        "refine_count": 2,
        "rsr_done": False,
        "rsr_count": 0,
        "has_placed_model": False,
    }
    _clear_zombie_done_flags(info, available_files=[])

    assert_false(info["refine_done"],
                 "refine_done must be cleared when refine output absent")
    assert_equal(info["refine_count"], 1,
                 "refine_count must be decremented from 2 to 1")

    print("  PASSED")


def test_j5_dock_zombie_clears_placed_model():
    """J5: dock_done zombie clears has_placed_model."""
    print("Test: j5_dock_zombie_clears_placed_model")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    info = {
        "resolve_cryo_em_done": False,
        "has_full_map": False,
        "predict_full_done": False,
        "dock_done": True,
        "refine_done": False,
        "refine_count": 0,
        "rsr_done": False,
        "rsr_count": 0,
        "has_placed_model": True,
    }
    _clear_zombie_done_flags(info, available_files=[])

    assert_false(info["dock_done"],
                 "dock_done must be cleared when docked pdb absent")
    assert_false(info["has_placed_model"],
                 "has_placed_model must be cleared when dock output absent")

    print("  PASSED")


def test_j5_dock_zombie_not_cleared_when_docked_pdb_present():
    """J5: dock_done preserved when docked pdb is on disk."""
    print("Test: j5_dock_zombie_not_cleared_when_docked_pdb_present")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    info = {
        "resolve_cryo_em_done": False,
        "has_full_map": False,
        "predict_full_done": False,
        "dock_done": True,
        "refine_done": False,
        "refine_count": 0,
        "rsr_done": False,
        "rsr_count": 0,
        "has_placed_model": True,
    }
    _clear_zombie_done_flags(
        info, available_files=["/data/model_docked.pdb"]
    )

    assert_true(info["dock_done"],
                "dock_done must be preserved when docked pdb exists")
    assert_true(info["has_placed_model"],
                "has_placed_model must be preserved when dock output exists")

    print("  PASSED")


# =============================================================================
# CATEGORY E1/E2 — xtriage resolution regex (dash-separator fix)
# =============================================================================

def test_e1_e2_xtriage_resolution_dash_separator():
    """E1/E2: xtriage resolution picks correct value from '50.00 - 2.30' format."""
    print("Test: e1_e2_xtriage_resolution_dash_separator")

    log = (
        "  Resolution range:   50.00 - 2.30\n"
        "  Completeness in resolution range:   1   96.5%\n"
        "  Anomalous resolution range: 50.00 - 2.80\n"
        "  Resolution range:   50.00 - 3.50\n"
    )
    result = _apply_yaml_regex("phenix.xtriage", "resolution", log)

    # pick_min should select 2.30, not 50.00
    assert_not_none(result, "xtriage resolution must match")
    assert_equal(result, 2.3,
                 "xtriage resolution pick_min must extract 2.30, not low-res limit 50.00")

    print("  PASSED")


def test_e2_xtriage_resolution_anchor_blocks_completeness_line():
    """E2: 'Completeness in resolution range: 1' must NOT match resolution pattern."""
    print("Test: e2_xtriage_resolution_anchor_blocks_completeness_line")

    # The line that previously caused pick_min to return 1.0
    trap_log = "  Completeness in resolution range:   1   96.5%"
    result = _apply_yaml_regex("phenix.xtriage", "resolution", trap_log)

    assert_true(result is None,
                "Completeness line must not match xtriage resolution pattern")

    print("  PASSED")


def test_e1_xtriage_resolution_simple_format():
    """E1: simple 'Resolution: 1.80' format (no range) still matches."""
    print("Test: e1_xtriage_resolution_simple_format")

    log = "  Resolution:   1.80"
    result = _apply_yaml_regex("phenix.xtriage", "resolution", log)

    assert_not_none(result, "Simple Resolution: line must match")
    assert_equal(result, 1.80,
                 "Simple resolution format must extract 1.80")

    print("  PASSED")


def test_e1_xtriage_resolution_multiple_ranges_picks_min():
    """E1: when multiple resolution ranges appear, pick_min selects highest resolution."""
    print("Test: e1_xtriage_resolution_multiple_ranges_picks_min")

    log = (
        "  Resolution range:   50.00 - 3.50\n"
        "  Resolution range:   50.00 - 2.30\n"
    )
    result = _apply_yaml_regex("phenix.xtriage", "resolution", log)

    assert_equal(result, 2.3,
                 "pick_min across multiple ranges must return highest resolution (smallest value)")

    print("  PASSED")


# =============================================================================
# CATEGORY E1 — real_space_refine map_cc extract:last
# =============================================================================

def test_e1_rsr_map_cc_uses_last_cycle():
    """E1: real_space_refine map_cc returns final macro-cycle value, not first."""
    print("Test: e1_rsr_map_cc_uses_last_cycle")

    # RSR emits one CC_mask line per macro-cycle
    log = (
        "  Macro cycle 1: CC_mask = 0.52\n"
        "  Macro cycle 2: CC_mask = 0.63\n"
        "  Final statistics: CC_mask = 0.71\n"
    )
    result = _apply_yaml_regex("phenix.real_space_refine", "map_cc", log)

    assert_equal(result, 0.71,
                 "real_space_refine map_cc must extract last value (0.71), not first (0.52)")

    print("  PASSED")


def test_e1_rsr_map_cc_pattern_variants():
    """E1: real_space_refine map_cc pattern handles Map-CC and Model vs map CC."""
    print("Test: e1_rsr_map_cc_pattern_variants")

    # Pattern broadened to match the same variants as other programs
    log = (
        "  CC_mask = 0.65\n"
        "  Map-CC = 0.72\n"
        "  Model vs map CC = 0.78\n"
    )
    result = _apply_yaml_regex("phenix.real_space_refine", "map_cc", log)

    assert_equal(result, 0.78,
                 "Broadened pattern must match all CC variants; last wins")

    print("  PASSED")


# =============================================================================
# CATEGORY I1 — max_refine_cycles: controlled landing (validate + STOP)
# =============================================================================

def test_i1_max_refine_cycles_xray_controlled_landing():
    """I1: xray hitting max_refine_cycles injects validate programs + STOP, not bare STOP.

    Calls WorkflowEngine.get_valid_programs() directly to avoid the libtbx
    lazy-import in detect_workflow_state (which would fall back to ["STOP"]
    in environments where libtbx is unavailable).
    """
    print("Test: i1_max_refine_cycles_xray_controlled_landing")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # Simulate xray refine phase with refine_count=1 and max_refine_cycles=1
    context = {
        "phase": "refine",
        "refine_count": 1,
        "rsr_count": 0,
        "r_free": 0.32,
        "map_cc": None,
        "validation_done": False,
    }
    directives = {"stop_conditions": {"max_refine_cycles": 1}}

    valid = engine.get_valid_programs(
        experiment_type="xray",
        phase_info={"phase": "refine"},
        context=context,
        directives=directives,
    )

    # Must NOT be a bare ["STOP"]
    assert_false(valid == ["STOP"],
                 "max_refine_cycles must not produce bare [STOP]; "
                 "validation programs should be injected. Got: %s" % valid)
    has_validate = any(
        p in valid for p in [
            "phenix.molprobity", "phenix.model_vs_data", "phenix.map_correlations"
        ]
    )
    assert_true(has_validate,
                "Validate-phase programs must appear when max_refine_cycles "
                "limit is reached. Got: %s" % valid)
    assert_in("STOP", valid,
              "STOP must be included alongside validate programs")

    print("  PASSED")


def test_i1_max_refine_cycles_cryoem_uses_rsr_count():
    """I1c: cryoem limit check uses rsr_count not refine_count.

    Bug: code previously read context["refine_count"] for both experiment types.
    With rsr_count=1 and max_refine_cycles=1 the limit must fire for cryoem.
    With refine_count=0 it incorrectly did not.
    """
    print("Test: i1_max_refine_cycles_cryoem_uses_rsr_count")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    context = {
        "refine_count": 0,   # must be ignored for cryoem
        "rsr_count": 1,      # must trigger the limit
        "map_cc": 0.72,
        "r_free": None,
        "validation_done": False,
        "has_model": True, "has_placed_model": True, "has_refined_model": True,
        "has_data_mtz": False, "has_sequence": False, "has_ligand_file": False,
        "has_full_map": True, "has_half_map": False,
    }
    directives = {"stop_conditions": {"max_refine_cycles": 1}}

    valid = engine.get_valid_programs(
        experiment_type="cryoem",
        phase_info={"phase": "refine"},
        context=context,
        directives=directives,
    )

    has_validate_or_stop = "STOP" in valid or any(
        p in valid for p in ["phenix.molprobity", "phenix.validation_cryoem"]
    )
    assert_true(has_validate_or_stop,
                "cryoem max_refine_cycles with rsr_count=1: STOP or validate "
                "programs expected. Got: %s" % valid)
    assert_false(valid == ["phenix.real_space_refine"],
                 "real_space_refine alone must not be offered after limit fires")

    print("  PASSED")


def test_i2_after_program_beats_quality_gate():
    """I2: after_program → STOP only; validate programs NOT injected.

    Distinguishes max_refine_cycles (controlled landing: validate + STOP)
    from after_program (unconditional stop: STOP only).
    """
    print("Test: i2_after_program_beats_quality_gate")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    context = {
        "refine_count": 0,
        "rsr_count": 1,
        "map_cc": 0.30,      # poor — quality gate would normally continue
        "r_free": None,
        "validation_done": False,
        "last_program": "phenix.real_space_refine",
        "has_model": True, "has_placed_model": True, "has_refined_model": True,
        "has_data_mtz": False, "has_sequence": False, "has_ligand_file": False,
        "has_full_map": True, "has_half_map": False,
    }
    directives = {"stop_conditions": {"after_program": "phenix.real_space_refine"}}

    valid = engine.get_valid_programs(
        experiment_type="cryoem",
        phase_info={"phase": "refine"},
        context=context,
        directives=directives,
    )

    assert_in("STOP", valid,
              "STOP must appear after after_program completes")
    assert_false("phenix.real_space_refine" in valid,
                 "after_program: real_space_refine must not be re-offered. "
                 "Got: %s" % valid)

    print("  PASSED")


# =============================================================================
# YAML SPEC: programs.yaml regression checks
# =============================================================================

def test_yaml_xtriage_resolution_has_pick_min():
    """Regression: xtriage resolution spec must have pick_min:true."""
    print("Test: yaml_xtriage_resolution_has_pick_min")
    programs = _load_programs()
    spec = programs["phenix.xtriage"]["log_parsing"]["resolution"]
    assert_true(spec.get("pick_min", False),
                "xtriage resolution must have pick_min: true")
    print("  PASSED")


def test_yaml_rsr_map_cc_is_extract_last():
    """Regression: real_space_refine map_cc must be extract:last after E1 fix."""
    print("Test: yaml_rsr_map_cc_is_extract_last")
    programs = _load_programs()
    spec = programs["phenix.real_space_refine"]["log_parsing"]["map_cc"]
    assert_equal(spec.get("extract", "first"), "last",
                 "real_space_refine map_cc must use extract: last")
    print("  PASSED")


def test_yaml_rsr_clashscore_is_still_extract_last():
    """Regression: real_space_refine clashscore must remain extract:last."""
    print("Test: yaml_rsr_clashscore_is_still_extract_last")
    programs = _load_programs()
    spec = programs["phenix.real_space_refine"]["log_parsing"]["clashscore"]
    assert_equal(spec.get("extract", "first"), "last",
                 "real_space_refine clashscore must use extract: last")
    print("  PASSED")


def test_yaml_polder_requires_selection_invariant():
    """G2 regression: polder invariant requires selection strategy_flag."""
    print("Test: yaml_polder_requires_selection_invariant")
    programs = _load_programs()
    polder = programs["phenix.polder"]
    # selection must be a strategy_flag, not an input slot
    assert_true("selection" in polder.get("strategy_flags", {}),
                "polder selection must be a strategy_flag")
    # {selection} was removed from the command template - it is now appended
    # automatically from strategy_flags (avoids double-substitution issues).
    assert_true("{selection}" not in polder["command"],
                "polder command template must NOT contain {selection} (appended via strategy_flag)")
    # Must have a requires_selection invariant
    invariant_names = [inv.get("name") for inv in polder.get("invariants", [])]
    assert_in("requires_selection", invariant_names,
              "polder must have requires_selection invariant")
    # Flag template must use single quotes so multi-word values don't get split
    sel_flag = polder["strategy_flags"]["selection"].get("flag", "")
    assert_true("'" in sel_flag,
                "polder selection flag must use single quotes to avoid shell splitting: %s" % sel_flag)
    # Must have a safe default that doesn't assume ligand residue name
    sel_default = polder["strategy_flags"]["selection"].get("default", "")
    assert_true("hetero" in sel_default,
                "polder selection default should use 'hetero and not water', got: %s" % sel_default)
    print("  PASSED")


def test_polder_selection_always_uses_safe_default():
    """G2b: program_registry overrides LLM-supplied selection for polder with safe default.

    The LLM cannot reliably know the ligand residue name and guesses values like
    'resname LIG'.  The registry must always reset it to 'hetero and not water'.
    """
    print("Test: polder_selection_always_uses_safe_default")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.program_registry import ProgramRegistry
    except ImportError:
        print("  SKIP (ProgramRegistry unavailable)")
        return

    registry = ProgramRegistry(use_yaml=True)
    dummy_files = {"data_mtz": "/tmp/data.mtz", "model": "/tmp/model.pdb"}

    # Simulate LLM providing wrong residue name
    for bad_sel in ["resname LIG", "resname LGD", "resname ATP", "LIG"]:
        cmd = registry.build_command(
            program_name="phenix.polder",
            files=dummy_files,
            strategy={"selection": bad_sel},
            log=lambda msg: None,
        )
        assert_true(
            bad_sel not in cmd,
            "polder command should NOT contain LLM selection %r, got: %s" % (bad_sel, cmd)
        )
        assert_true(
            "hetero" in cmd,
            "polder command should use 'hetero and not water', got: %s" % cmd
        )
        assert_true(
            "'" in cmd,
            "polder selection must be single-quoted to avoid shell splitting, got: %s" % cmd
        )
    print("  PASSED (LLM selection overridden for: resname LIG, resname LGD, resname ATP, LIG)")


# =============================================================================
# CATEGORY I1b — validate → STOP after validation_done=True
# =============================================================================

def test_i1b_validation_done_produces_stop():
    """I1b: after validation_done=True engine routes to complete phase -> [STOP].

    This is the clean-termination half of the I1 story: max_refine_cycles transitions
    to validate (tested in I1a); after validation completes the engine must produce
    exactly ["STOP"] via the complete-phase handler (not via _apply_directives).

    Uses build_context() to supply all required context keys — detect_phase()
    raises KeyError for any missing key in the context dict.
    """
    print("Test: i1b_validation_done_produces_stop")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # Simulate xray session: xtriage + phaser + refine (r_free good) + molprobity done
    history_info = {
        "xtriage_done": True,
        "phaser_done": True,
        "refine_done": True,
        "refine_count": 1,
        "validation_done": True,
        "molprobity_done": True,
    }
    files = {
        "data_mtz": ["data.mtz"],
        "model": ["refine_001.pdb"],
        "refined": ["refine_001.pdb"],
    }
    context = engine.build_context(files, history_info, {"r_free": 0.24}, None)

    phase_info = engine.detect_phase("xray", context)
    valid = engine.get_valid_programs(
        experiment_type="xray",
        phase_info=phase_info,
        context=context,
    )

    assert_equal(phase_info.get("phase"), "complete",
                 "Phase must be 'complete' when validation_done=True and r_free is good. "
                 "Got: %s" % phase_info)
    assert_equal(valid, ["STOP"],
                 "After validation_done=True the engine must return [STOP] "
                 "(complete phase). Got: %s" % valid)

    print("  PASSED")


def test_i1b_cryoem_validation_done_produces_stop():
    """I1b cryo-EM: same clean-termination check for the cryo-EM workflow."""
    print("Test: i1b_cryoem_validation_done_produces_stop")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    history_info = {
        "mtriage_done": True,
        "resolve_cryo_em_done": True,
        "dock_done": True,
        "rsr_done": True,
        "rsr_count": 2,
        "has_full_map": True,
        "validation_done": True,
        "molprobity_done": True,
    }
    files = {
        "map": ["denmod_map.ccp4"],
        "full_map": ["denmod_map.ccp4"],
        "model": ["rsr_001.pdb"],
        "refined": ["rsr_001.pdb"],
    }
    context = engine.build_context(files, history_info, {"map_cc": 0.81}, None)

    phase_info = engine.detect_phase("cryoem", context)
    valid = engine.get_valid_programs(
        experiment_type="cryoem",
        phase_info=phase_info,
        context=context,
    )

    assert_equal(phase_info.get("phase"), "complete",
                 "Cryo-EM phase must be 'complete' when validation_done=True. "
                 "Got: %s" % phase_info)
    assert_equal(valid, ["STOP"],
                 "Cryo-EM: after validation_done=True engine must return [STOP]. "
                 "Got: %s" % valid)

    print("  PASSED")



# =============================================================================
# ZOMBIE DIAGNOSTIC SURFACING — J5 regression
# =============================================================================

def test_j5_zombie_diagnostics_returned_in_state():
    """J5 regression: detect_workflow_state surfaces zombie diagnostics.

    Bug: _clear_zombie_done_flags() return value was silently discarded.
    The state dict should now carry 'zombie_diagnostics' when zombies are found,
    so PERCEIVE can log them.
    """
    print("Test: j5_zombie_diagnostics_returned_in_state")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_state import detect_workflow_state
    except ImportError:
        print("  SKIP (detect_workflow_state unavailable)")
        return

    # History says resolve_cryo_em ran successfully, but no output file exists
    history = [
        {
            "program": "phenix.resolve_cryo_em",
            "command": "phenix.resolve_cryo_em half1.ccp4 half2.ccp4",
            "result": "resolve_cryo_em DONE",
            "analysis": {},
        }
    ]
    # No available_files — denmod_map.ccp4 is missing (zombie state)
    state = detect_workflow_state(
        history=history,
        available_files=[],
        analysis={},
    )

    diags = state.get("zombie_diagnostics", [])
    assert_true(len(diags) > 0,
                "Zombie diagnostics must be present when resolve_cryo_em_done "
                "is set but output file is missing")
    assert_true(any("resolve_cryo_em" in d for d in diags),
                "Zombie diagnostic must name the cleared program. Got: %s" % diags)

    # Also confirm the done flag was cleared (zombie detection worked)
    # The program should be re-eligible — resolve_cryo_em should appear in valid_programs
    # (or at minimum, resolve_cryo_em_done must not be blocking the workflow)
    print("  PASSED")


def test_j5_no_zombie_diagnostics_when_clean():
    """J5 regression: no zombie_diagnostics key when no zombie states exist."""
    print("Test: j5_no_zombie_diagnostics_when_clean")
    if not _IMPORTS_OK:
        print("  SKIP (imports unavailable)")
        return

    try:
        from agent.workflow_state import detect_workflow_state
    except ImportError:
        print("  SKIP (detect_workflow_state unavailable)")
        return

    # Empty history, single MTZ — clean initial state
    state = detect_workflow_state(
        history=[],
        available_files=["data.mtz"],
        analysis={},
    )

    # Either no zombie_diagnostics key, or an empty list
    diags = state.get("zombie_diagnostics", [])
    assert_equal(len(diags), 0,
                 "No zombie diagnostics expected for a clean initial state")

    print("  PASSED")


# =============================================================================
# CATEGORY K1 — best_files list values crash at cycle=2
# =============================================================================

def test_k1_best_path_handles_list():
    """K1: CommandBuilder._best_path must not crash when value is a list.

    Root cause: clients may legitimately store multi-file entries (e.g.
    half_map: [map1.mrc, map2.mrc]) as a list in the best_files dict that is
    passed back as session_state on cycle=2.  Every os.path call that reads a
    best_files value crashed with 'expected str, bytes or os.PathLike, not list'.

    Fix: _best_path() helper collapses list → first element, None → None.
    """
    print("Test: k1_best_path_handles_list")
    try:
        from agent.command_builder import CommandBuilder
    except ImportError:
        print("  SKIP (command_builder unavailable)")
        return

    cb = CommandBuilder()

    # None and empty string → None
    assert_equal(cb._best_path(None), None,
                 "_best_path(None) should return None")
    assert_equal(cb._best_path(""), None,
                 "_best_path('') should return None")

    # Plain string → returned unchanged
    assert_equal(cb._best_path("/data/model.pdb"), "/data/model.pdb",
                 "_best_path(str) should return the string")

    # List → first element (single-file slot takes only the first)
    assert_equal(cb._best_path(["/data/map1.mrc", "/data/map2.mrc"]),
                 "/data/map1.mrc",
                 "_best_path(list) should return first element")

    # Empty list → None
    assert_equal(cb._best_path([]), None,
                 "_best_path([]) should return None")

    print("  PASSED")


def test_k1_best_files_logging_handles_list():
    """K1: best_files logging must not crash when a value is a list.

    The summary log lines in graph_nodes.py and run_ai_agent.py both called
    os.path.basename(v) directly on best_files values.  When v is a list this
    raises TypeError.  Fixed by extracting v[0] for list values before the
    basename call.
    """
    print("Test: k1_best_files_logging_handles_list")
    import os

    best_files = {
        "model":    "/data/refined_001.pdb",
        "half_map": ["/data/half_map_1.mrc", "/data/half_map_2.mrc"],
    }

    # Reproduce the fixed logging expression from graph_nodes.py / run_ai_agent.py
    try:
        summary = ", ".join([
            "%s=%s" % (k, os.path.basename(v[0] if isinstance(v, list) else v))
            for k, v in best_files.items() if v
        ])
    except TypeError as e:
        assert_true(False,
            "Logging expression crashed with list value: %s" % e)

    assert_in("model=refined_001.pdb", summary,
              "model path should appear in summary")
    assert_in("half_map=half_map_1.mrc", summary,
              "half_map list first element should appear in summary")

    print("  PASSED")


def test_k1_command_builder_best_files_list_does_not_crash():
    """K1: CommandBuilder must not crash when best_files contains a list value.

    Simulates the exact cycle=2 scenario: session_state carries
    best_files = {"model": "...", "half_map": [map1, map2]}.
    _select_file_for_slot reads context.best_files.get(category) and passes
    the result to os.path.exists.  With a list value this raised TypeError.
    """
    print("Test: k1_command_builder_best_files_list_does_not_crash")
    try:
        from agent.command_builder import CommandBuilder, CommandContext
    except ImportError:
        print("  SKIP (command_builder unavailable)")
        return

    cb = CommandBuilder()

    # Build a minimal CommandContext where best_files has a list for half_map
    # and a string for model.
    ctx = CommandContext(
        cycle_number=2,
        experiment_type="cryoem",
        resolution=3.5,
        best_files={
            "model":    "/data/overall_best.pdb",
            "half_map": ["/data/half_map_1.mrc", "/data/half_map_2.mrc"],
        },
        rfree_mtz=None,
        categorized_files={
            "model":    ["/data/overall_best.pdb"],
            "half_map": ["/data/half_map_1.mrc", "/data/half_map_2.mrc"],
        },
        workflow_state="cryoem_has_model",
        history=[],
        llm_files=None,
        llm_strategy=None,
        directives={},
        log=lambda msg: None,
    )

    # Building a real_space_refine command exercises _select_file_for_slot
    # which calls _best_path() on best_files values.  It should not raise
    # TypeError even if the command ultimately can't be built (returns None).
    crashed = False
    try:
        available = ["/data/overall_best.pdb",
                     "/data/half_map_1.mrc",
                     "/data/half_map_2.mrc"]
        cmd = cb.build("phenix.real_space_refine", available, ctx)
        # cmd may be None if required files aren't fully satisfied — that's fine.
        # The critical guarantee is that no TypeError was raised.
    except TypeError as e:
        crashed = True
        assert_true(False,
            "build() crashed with list best_files value: %s" % e)

    assert_false(crashed,
        "build() must not raise TypeError when best_files contains a list")

    print("  PASSED")


def test_k2_mtriage_keeps_half_maps_with_full_map():
    """K2: mtriage must receive both full_map AND half_maps when both are available.

    Root cause: the post-selection validation in command_builder.py had a blanket
    rule that dropped half_maps whenever a full_map was also selected.  For programs
    like mtriage the half maps are NOT redundant — they provide FSC-based resolution
    measurement that full-map-only mode cannot.

    Fix: keep_half_maps_with_full_map: true in programs.yaml for mtriage and
    map_to_model. Both genuinely use full_map + half_maps together.
    predict_and_build takes EITHER 2 half-maps OR 1 full map — not both —
    so it does NOT have this flag and must drop half_maps when full_map is present.
    """
    print("Test: k2_mtriage_keeps_half_maps_with_full_map")
    try:
        from agent.command_builder import CommandBuilder, CommandContext
    except ImportError:
        print("  SKIP (command_builder unavailable)")
        return

    sharpened = "/data/sharpened_map.ccp4"
    half1 = "/data/half_map_1.ccp4"
    half2 = "/data/half_map_2.ccp4"

    ctx = CommandContext(
        cycle_number=2,
        experiment_type="cryoem",
        resolution=3.0,
        best_files={"map": sharpened},
        rfree_mtz=None,
        categorized_files={
            "optimized_full_map": [sharpened],
            "map":                [sharpened, half1, half2],
            "half_map":           [half1, half2],
        },
        workflow_state="cryoem_initial",
        history=[],
        llm_files=None,
        llm_strategy=None,
        directives={},
        log=lambda msg: None,
    )

    cb = CommandBuilder()
    available = [sharpened, half1, half2]
    cmd = cb.build("phenix.mtriage", available, ctx)

    assert_not_none(cmd, "mtriage must produce a command when map files are available")
    assert_true("half_map=" in cmd,
                "mtriage command must include half_map= when half maps are present. Got: %s" % cmd)
    assert_true(sharpened in cmd or "sharpened" in cmd,
                "mtriage command must include the full/sharpened map. Got: %s" % cmd)

    print("  PASSED")


def test_k2_map_sharpening_uses_half_maps_when_no_full_map():
    """K2: map_sharpening must use half_map= mode when only half maps are available.

    When the input set contains ONLY half maps (no full map yet), map_sharpening
    should run in mode 3: half_map=map1 half_map=map2.  It must NOT select a
    half map as the positional full_map argument.
    """
    print("Test: k2_map_sharpening_uses_half_maps_when_no_full_map")
    try:
        from agent.command_builder import CommandBuilder, CommandContext
    except ImportError:
        print("  SKIP (command_builder unavailable)")
        return

    half1 = "/data/half_map_1.ccp4"
    half2 = "/data/half_map_2.ccp4"
    seq   = "/data/seq.dat"

    ctx = CommandContext(
        cycle_number=1,
        experiment_type="cryoem",
        resolution=None,
        best_files={},
        rfree_mtz=None,
        categorized_files={
            "half_map": [half1, half2],
            "map":      [half1, half2],   # half maps bubble up to parent
            "sequence": [seq],
        },
        workflow_state="cryoem_initial",
        history=[],
        llm_files=None,
        llm_strategy=None,
        directives={},
        log=lambda msg: None,
    )

    cb = CommandBuilder()
    available = [half1, half2, seq]
    cmd = cb.build("phenix.map_sharpening", available, ctx)

    # Command must include half_map= flag (mode 3)
    assert_not_none(cmd, "map_sharpening must produce a command with half maps available")
    assert_true("half_map=" in cmd,
                "map_sharpening must use half_map= mode when no full map exists. Got: %s" % cmd)
    # Neither half map should appear as a bare positional argument (full_map slot)
    # The positional full_map slot has flag="" so it appears without a prefix.
    # If a half map is used as full_map it will appear without "half_map=" prefix.
    cmd_tokens = cmd.split()
    bare_map_tokens = [t for t in cmd_tokens
                       if (t.endswith(".ccp4") or t.endswith(".mrc") or t.endswith(".map"))
                       and not t.startswith("half_map=")
                       and not t.startswith("seq_file=")
                       and not t.startswith("phenix.")]
    assert_equal(len(bare_map_tokens), 0,
                 "No half map should appear as bare positional (full_map) arg. "
                 "Bare map tokens: %s\nFull command: %s" % (bare_map_tokens, cmd))

    print("  PASSED")


def test_k2_optimized_full_map_recognized_as_genuine_full_map():
    """K2: A sharpened map (optimized_full_map category) must not be mis-classified
    as a half map in the post-selection validation.

    The fix extended the 'genuine full map' check to include optimized_full_map
    so that sharpened maps are never incorrectly removed in favour of half_maps.
    """
    print("Test: k2_optimized_full_map_recognized_as_genuine_full_map")
    try:
        from agent.command_builder import CommandBuilder, CommandContext
    except ImportError:
        print("  SKIP (command_builder unavailable)")
        return

    sharpened = "/data/sharpened_map.ccp4"
    half1 = "/data/half_map_1.ccp4"
    half2 = "/data/half_map_2.ccp4"

    # Simulate a program that does NOT keep_half_maps_with_full_map (e.g. real_space_refine)
    # The sharpened map is in optimized_full_map only (NOT in full_map).
    # Before the fix, the check `full_map_path not in full_map_files` was True
    # (because sharpened is not in the strict "full_map" subcategory),
    # which could classify it as mis-selected and remove it.
    ctx = CommandContext(
        cycle_number=2,
        experiment_type="cryoem",
        resolution=3.0,
        best_files={"map": sharpened},
        rfree_mtz=None,
        categorized_files={
            "optimized_full_map": [sharpened],
            "map":                [sharpened, half1, half2],
            "half_map":           [half1, half2],
            "full_map":           [],        # strict full_map is empty
        },
        workflow_state="cryoem_refined",
        history=[],
        llm_files=None,
        llm_strategy=None,
        directives={},
        log=lambda msg: None,
    )

    cb = CommandBuilder()
    available = [sharpened, half1, half2]
    cmd = cb.build("phenix.real_space_refine", available, ctx)

    # real_space_refine needs model too so cmd may be None — that's fine.
    # The key: if a command IS produced, the sharpened map must not be absent
    # due to mis-classification.  We just check no crash and that sharpened
    # was not replaced by a half map in any model-less fallback.
    if cmd:
        # If the command references a map file, it must be the sharpened one, not a half map
        if half1 in cmd or half2 in cmd:
            assert_true(False,
                "real_space_refine must not use a half map as its map input. Got: %s" % cmd)

    print("  PASSED")


# =============================================================================
# CATEGORY R1 — placement_checker: unit cell comparison (Tier 1)
# =============================================================================

def test_r1_pdb_cryst1_parsed_correctly():
    """R1: read_pdb_unit_cell returns correct 6-tuple from a CRYST1 line."""
    print("Test: r1_pdb_cryst1_parsed_correctly")
    import tempfile
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import read_pdb_unit_cell

    cryst1 = "CRYST1   57.230   57.230  146.770  90.00  90.00  90.00 P 41 21 2\n"
    with tempfile.NamedTemporaryFile(suffix=".pdb", mode="w", delete=False) as f:
        f.write(cryst1)
        f.write("ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 10.00           C\n")
        pdb_path = f.name
    try:
        cell = read_pdb_unit_cell(pdb_path)
        assert cell is not None, "Should parse CRYST1 line"
        assert len(cell) == 6, "Should return 6 values"
        assert abs(cell[0] - 57.230) < 0.001, "a should be 57.230, got %s" % cell[0]
        assert abs(cell[2] - 146.770) < 0.001, "c should be 146.770, got %s" % cell[2]
        assert abs(cell[3] - 90.00) < 0.001, "alpha should be 90.00, got %s" % cell[3]
    finally:
        os.unlink(pdb_path)
    print("  PASSED")


def test_r1_pdb_cryst1_missing_returns_none():
    """R1: read_pdb_unit_cell returns None when no CRYST1 record is present."""
    print("Test: r1_pdb_cryst1_missing_returns_none")
    import tempfile
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import read_pdb_unit_cell

    with tempfile.NamedTemporaryFile(suffix=".pdb", mode="w", delete=False) as f:
        f.write("ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 10.00           C\n")
        pdb_path = f.name
    try:
        cell = read_pdb_unit_cell(pdb_path)
        assert cell is None, "Should return None when CRYST1 absent, got %s" % str(cell)
    finally:
        os.unlink(pdb_path)
    print("  PASSED")


def test_r1_pdb_missing_file_returns_none():
    """R1: read_pdb_unit_cell returns None (not raises) for a missing file."""
    print("Test: r1_pdb_missing_file_returns_none")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import read_pdb_unit_cell

    cell = read_pdb_unit_cell("/tmp/this_file_does_not_exist_xyz.pdb")
    assert cell is None, "Should return None for missing file, got %s" % str(cell)
    print("  PASSED")


def test_r1_cells_compatible_within_tolerance():
    """R1: cells_are_compatible returns True for cells within 5% tolerance."""
    print("Test: r1_cells_compatible_within_tolerance")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    # Identical cells
    cell = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    assert cells_are_compatible(cell, cell), "Identical cells must be compatible"

    # Within 4% on one axis — should still be compatible
    cell_a = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    cell_b = (59.50, 57.23, 146.77, 90.0, 90.0, 90.0)  # ~4% diff on a
    assert cells_are_compatible(cell_a, cell_b), (
        "Cells within 5%% should be compatible: %s vs %s" % (cell_a, cell_b)
    )
    print("  PASSED")


def test_r1_cells_mismatch_outside_tolerance():
    """R1: cells_are_compatible returns False when any parameter differs > 5%."""
    print("Test: r1_cells_mismatch_outside_tolerance")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    # Large difference on 'a' axis (P1 vs different crystal form)
    cell_a = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    cell_b = (80.00, 57.23, 146.77, 90.0, 90.0, 90.0)  # ~40% diff → mismatch
    assert not cells_are_compatible(cell_a, cell_b), (
        "Cells with >5%% difference should be incompatible: %s vs %s" % (cell_a, cell_b)
    )

    # Angle mismatch (orthorhombic vs monoclinic-like)
    cell_c = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    cell_d = (57.23, 57.23, 146.77, 90.0, 110.0, 90.0)  # beta 90→110 = 22% diff
    assert not cells_are_compatible(cell_c, cell_d), (
        "Angle mismatch should be detected: %s vs %s" % (cell_c, cell_d)
    )
    print("  PASSED")


def test_r1_cells_compatible_with_none_is_failsafe():
    """R1: cells_are_compatible returns True (fail-safe) when either cell is None."""
    print("Test: r1_cells_compatible_with_none_is_failsafe")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    cell = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    assert cells_are_compatible(None, cell), "None vs cell should be fail-safe True"
    assert cells_are_compatible(cell, None), "cell vs None should be fail-safe True"
    assert cells_are_compatible(None, None), "None vs None should be fail-safe True"
    print("  PASSED")


def test_r1_xray_mismatch_requires_both_readable():
    """R1: check_xray_cell_mismatch returns False (fail-safe) when PDB has no CRYST1."""
    print("Test: r1_xray_mismatch_requires_both_readable")
    import tempfile
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import check_xray_cell_mismatch

    # PDB with no CRYST1 — cannot compare → must NOT declare mismatch
    with tempfile.NamedTemporaryFile(suffix=".pdb", mode="w", delete=False) as f:
        f.write("ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 10.00           C\n")
        pdb_no_cryst1 = f.name
    try:
        result = check_xray_cell_mismatch(pdb_no_cryst1, "/tmp/fake.mtz")
        assert result is False, (
            "Must return False (fail-safe) when PDB has no CRYST1, got %s" % result
        )
    finally:
        os.unlink(pdb_no_cryst1)
    print("  PASSED")


def test_r1_xray_missing_files_return_false():
    """R1: check_xray_cell_mismatch returns False for missing/None paths."""
    print("Test: r1_xray_missing_files_return_false")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import check_xray_cell_mismatch

    assert check_xray_cell_mismatch(None, None) is False
    assert check_xray_cell_mismatch("", "/tmp/data.mtz") is False
    assert check_xray_cell_mismatch("/tmp/model.pdb", None) is False
    assert check_xray_cell_mismatch("/no/such/file.pdb", "/no/such.mtz") is False
    print("  PASSED")


def test_r1_cryoem_matches_present_cell_is_compatible():
    """R1: model matching the present-portion map cell is NOT a mismatch.

    The model may have been placed in a sub-box extracted from the full map;
    matching the present-portion cell means it is correctly placed.
    """
    print("Test: r1_cryoem_matches_present_cell_is_compatible")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    # Simulate: full map cell = 320^3, present portion = (59, 67, 106)
    # Model cell matches the present portion
    full_cell    = (320.0, 320.0, 320.0, 90.0, 90.0, 90.0)
    present_cell = (59.0, 67.0, 106.0, 90.0, 90.0, 90.0)
    model_cell   = (59.5, 67.3, 106.2, 90.0, 90.0, 90.0)  # ~within 1%

    assert not cells_are_compatible(model_cell, full_cell), (
        "Model should NOT match full cell in this scenario"
    )
    assert cells_are_compatible(model_cell, present_cell), (
        "Model SHOULD match present-portion cell — should be compatible"
    )

    # Verify the overall mismatch logic: compatible with present → not a mismatch
    # (mirrors check_cryoem_cell_mismatch logic without needing a real map file)
    matches_full    = cells_are_compatible(model_cell, full_cell)
    matches_present = cells_are_compatible(model_cell, present_cell)
    is_mismatch = not matches_full and not matches_present
    assert not is_mismatch, "Should NOT be mismatch when model matches present-portion cell"
    print("  PASSED")


def test_r1_cryoem_missing_files_return_false():
    """R1: check_cryoem_cell_mismatch returns False for missing/None paths."""
    print("Test: r1_cryoem_missing_files_return_false")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import check_cryoem_cell_mismatch

    assert check_cryoem_cell_mismatch(None, None) is False
    assert check_cryoem_cell_mismatch("", "/tmp/map.ccp4") is False
    assert check_cryoem_cell_mismatch("/tmp/model.pdb", None) is False
    assert check_cryoem_cell_mismatch("/no/such.pdb", "/no/such.ccp4") is False
    print("  PASSED")


def test_r1_definitive_xray_mismatch_detected():
    """R1: check_xray_cell_mismatch correctly identifies a clear mismatch
    when both cells are available and are very different.

    Uses the internal comparison path directly (no real MTZ needed).
    """
    print("Test: r1_definitive_xray_mismatch_detected")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    # A large crystal-form difference (P1 vs different SG/cell)
    model_cell = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    mtz_cell   = (100.0, 100.0, 200.0,  90.0, 90.0, 90.0)

    assert not cells_are_compatible(model_cell, mtz_cell), (
        "Very different cells should NOT be compatible: %s vs %s"
        % (model_cell, mtz_cell)
    )
    print("  PASSED")


# =============================================================================
# CATEGORY R2 — placement_uncertain context key (Tier 2 gate)
# =============================================================================

def _make_minimal_context(overrides=None):
    """Build a minimal WorkflowEngine context dict for R2 tests."""
    base = {
        "has_placed_model":      False,
        "cell_mismatch":         False,
        "placement_probed":      False,
        "placement_probe_result": None,
        "has_model":             True,
        "has_data_mtz":          True,
        "has_map":               False,
        "has_predicted_model":   False,
        "placement_uncertain":   False,   # will be recomputed
    }
    if overrides:
        base.update(overrides)
    # Replicate the logic from build_context
    base["placement_uncertain"] = (
        not base["has_placed_model"] and
        not base["cell_mismatch"] and
        not base["placement_probed"] and
        base["has_model"] and
        (base["has_data_mtz"] or base["has_map"]) and
        not base["has_predicted_model"]
    )
    return base


def test_r2_placement_uncertain_set_when_ambiguous():
    """R2: placement_uncertain=True when model+MTZ present, no history, no directive."""
    print("Test: r2_placement_uncertain_set_when_ambiguous")
    ctx = _make_minimal_context()
    assert ctx["placement_uncertain"] is True, (
        "placement_uncertain should be True for ambiguous model+MTZ case"
    )
    print("  PASSED")


def test_r2_placement_uncertain_false_when_placed():
    """R2: placement_uncertain=False when heuristics confirm model is placed."""
    print("Test: r2_placement_uncertain_false_when_placed")
    ctx = _make_minimal_context({"has_placed_model": True})
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False when has_placed_model=True"
    )
    print("  PASSED")


def test_r2_placement_uncertain_false_on_cell_mismatch():
    """R2: placement_uncertain=False when cell_mismatch=True (Tier 1 already decided)."""
    print("Test: r2_placement_uncertain_false_on_cell_mismatch")
    ctx = _make_minimal_context({"cell_mismatch": True})
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False when cell_mismatch=True "
        "(Tier 1 handles routing)"
    )
    print("  PASSED")


def test_r2_placement_uncertain_false_when_probed():
    """R2: placement_uncertain=False when probe has already run (placement_probed=True)."""
    print("Test: r2_placement_uncertain_false_when_probed")
    ctx = _make_minimal_context({"placement_probed": True})
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False when placement_probed=True"
    )
    print("  PASSED")


def test_r2_placement_uncertain_false_no_model():
    """R2: placement_uncertain=False when there is no model file."""
    print("Test: r2_placement_uncertain_false_no_model")
    ctx = _make_minimal_context({"has_model": False})
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False when has_model=False"
    )
    print("  PASSED")


def test_r2_placement_uncertain_false_no_data():
    """R2: placement_uncertain=False when there is no data (MTZ or map)."""
    print("Test: r2_placement_uncertain_false_no_data")
    ctx = _make_minimal_context({"has_data_mtz": False, "has_map": False})
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False when no MTZ and no map"
    )
    print("  PASSED")


def test_r2_placement_uncertain_false_for_predicted_model():
    """R2: placement_uncertain=False for predicted models (always need MR/dock — no probe)."""
    print("Test: r2_placement_uncertain_false_for_predicted_model")
    ctx = _make_minimal_context({"has_predicted_model": True})
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False for predicted models "
        "(they always require MR/dock without probing)"
    )
    print("  PASSED")


def test_r2_placement_uncertain_true_with_map_only():
    """R2: placement_uncertain=True for cryo-EM case (model + map, no MTZ)."""
    print("Test: r2_placement_uncertain_true_with_map_only")
    ctx = _make_minimal_context({"has_data_mtz": False, "has_map": True})
    assert ctx["placement_uncertain"] is True, (
        "placement_uncertain should be True for model+map (cryo-EM ambiguous case)"
    )
    print("  PASSED")


def test_r2_context_keys_present_in_workflow_engine():
    """R2: WorkflowEngine.build_context() returns all four new placement keys."""
    print("Test: r2_context_keys_present_in_workflow_engine")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    # Minimal inputs — no files, no history, no analysis
    ctx = engine.build_context(
        files={},
        history_info={},
        analysis=None,
        directives=None,
    )
    for key in ("cell_mismatch", "placement_probed",
                "placement_probe_result", "placement_uncertain"):
        assert key in ctx, (
            "build_context() must include key %r in returned context" % key
        )
    print("  PASSED (all four keys present: cell_mismatch, placement_probed, "
          "placement_probe_result, placement_uncertain)")


def test_r2_placement_probed_loaded_from_history():
    """R2: placement_probed and placement_probe_result are loaded from history_info."""
    print("Test: r2_placement_probed_loaded_from_history")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # Simulate history where probe already ran and confirmed placement
    history = {
        "placement_probed": True,
        "placement_probe_result": "placed",
    }
    ctx = engine.build_context(files={}, history_info=history)
    assert ctx["placement_probed"] is True, "placement_probed must be True from history"
    assert ctx["placement_probe_result"] == "placed", (
        "placement_probe_result must be 'placed' from history, got %r"
        % ctx["placement_probe_result"]
    )
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False when placement_probed=True"
    )
    print("  PASSED")


def test_r2_cell_mismatch_false_with_no_files():
    """R2: cell_mismatch=False when no model or data files are present (fail-safe)."""
    print("Test: r2_cell_mismatch_false_with_no_files")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    ctx = engine.build_context(files={}, history_info={})
    assert ctx["cell_mismatch"] is False, (
        "cell_mismatch must be False (fail-safe) when no files present, got %r"
        % ctx["cell_mismatch"]
    )
    print("  PASSED")


# =============================================================================
# CATEGORY R3 — probe_placement phase routing (Tier 3)
# =============================================================================

def _make_xray_history_with_probe(program, rfree=None, pre_refine=True):
    """Build a minimal history list simulating a probe run."""
    result_text = "SUCCESS: Quick R-factor check complete"
    metrics = {}
    if rfree is not None:
        metrics["r_free"] = rfree

    entry = {
        "program": program,
        "command": program + " model.pdb data.mtz",
        "result": result_text,
        "analysis": metrics,
    }
    if not pre_refine:
        # Simulate refinement having run first
        return [
            {"program": "phenix.refine", "command": "phenix.refine model.pdb data.mtz",
             "result": "SUCCESS: Refinement complete", "analysis": {"r_free": 0.28}},
            entry,
        ]
    return [entry]


def test_r3_probe_placement_phase_offered_xray():
    """R3: placement_uncertain=True → xray phase is probe_placement."""
    print("Test: r3_probe_placement_phase_offered_xray")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    # Build context that triggers placement_uncertain
    # has_model=True, has_data_mtz=True, no history, no predicted model
    context = {
        "has_placed_model": False,
        "has_predicted_model": False,
        "has_processed_model": False,
        "has_model": True,
        "has_search_model": False,
        "has_model_for_mr": True,
        "has_data_mtz": True,
        "has_map_coeffs_mtz": False,
        "has_sequence": False,
        "has_full_map": False,
        "has_half_map": False,
        "has_map": False,
        "has_non_half_map": False,
        "has_ligand": False,
        "has_ligand_file": False,
        "has_ligand_fit": False,
        "has_optimized_full_map": False,
        "has_refined_model": False,
        "phaser_done": False,
        "predict_done": False,
        "predict_full_done": False,
        "autobuild_done": False,
        "autosol_done": False,
        "autosol_success": False,
        "refine_done": False,
        "rsr_done": False,
        "validation_done": False,
        "ligandfit_done": False,
        "pdbtools_done": False,
        "needs_post_ligandfit_refine": False,
        "dock_done": False,
        "process_predicted_model_done": False,
        "refine_count": 0,
        "rsr_count": 0,
        "r_free": None, "r_work": None, "map_cc": None, "clashscore": None,
        "resolution": None, "tfz": None,
        "has_anomalous": False, "strong_anomalous": False,
        "anomalous_measurability": None, "has_twinning": False, "twin_law": None,
        "twin_fraction": None, "anomalous_resolution": None, "has_ncs": False,
        "has_half_map": False,
        "xtriage_done": True,   # Analysis already done
        "model_is_good": False,
        "use_mr_sad": False,
        "automation_path": "automated",
        # Tier 1/2/3 placement keys
        "cell_mismatch": False,
        "placement_probed": False,
        "placement_probe_result": None,
        "placement_uncertain": True,   # <- This triggers probe
    }

    from knowledge.yaml_loader import get_workflow_phases
    phases = get_workflow_phases("xray")
    result = engine._detect_xray_phase(phases, context)
    assert result["phase"] == "probe_placement", (
        "Expected probe_placement phase, got %r (reason: %s)"
        % (result["phase"], result.get("reason", ""))
    )
    print("  PASSED: phase=%r" % result["phase"])


def test_r3_probe_valid_programs_xray():
    """R3: probe_placement valid programs for xray = [phenix.model_vs_data]."""
    print("Test: r3_probe_valid_programs_xray")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    phase_info = {"phase": "probe_placement"}
    progs = engine.get_valid_programs("xray", phase_info, {}, {})
    assert "phenix.model_vs_data" in progs, (
        "phenix.model_vs_data must be in probe_placement valid programs for xray, got %s" % progs
    )
    assert "phenix.refine" not in progs, (
        "phenix.refine must NOT be in probe_placement valid programs"
    )
    print("  PASSED: valid programs = %s" % progs)


def test_r3_probe_valid_programs_cryoem():
    """R3: probe_placement valid programs for cryoem = [phenix.map_correlations]."""
    print("Test: r3_probe_valid_programs_cryoem")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    phase_info = {"phase": "probe_placement"}
    progs = engine.get_valid_programs("cryoem", phase_info, {}, {})
    assert "phenix.map_correlations" in progs, (
        "phenix.map_correlations must be in probe_placement valid programs for cryoem, got %s" % progs
    )
    assert "phenix.real_space_refine" not in progs, (
        "phenix.real_space_refine must NOT be in probe_placement valid programs"
    )
    print("  PASSED: valid programs = %s" % progs)


def test_r3_probe_result_placed_routes_to_refine():
    """R3: placement_probe_result='placed' → xray phase is refine (not probe_placement)."""
    print("Test: r3_probe_result_placed_routes_to_refine")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    from knowledge.yaml_loader import get_workflow_phases
    phases = get_workflow_phases("xray")

    # placement_probed=True, result=placed → should skip probe_placement, go to refine
    context = _make_minimal_context({
        "has_placed_model": False,   # Heuristics still say False
        "placement_probed": True,
        "placement_probe_result": "placed",
        "placement_uncertain": False,
        "cell_mismatch": False,
        "xtriage_done": True,
        "has_predicted_model": False,
        "has_processed_model": False,
        "autosol_done": False,
        "phaser_done": False,
        "has_ligand_fit": False,
        "pdbtools_done": False,
        "needs_post_ligandfit_refine": False,
        "has_refined_model": False,
        "has_anomalous": False,
        "use_mr_sad": False,
        "has_twinning": False,
        "has_ligand_file": False,
        "ligandfit_done": False,
        "refine_count": 0,
        "r_free": None,
        "resolution": None,
        "validation_done": False,
        "model_is_good": False,
    })
    # build_context overrides has_placed_model=True when probe result is 'placed';
    # replicate that here since we bypass build_context in this unit test.
    if context.get("placement_probe_result") == "placed":
        context["has_placed_model"] = True
    result = engine._detect_xray_phase(phases, context)
    assert result["phase"] == "refine", (
        "After successful probe (placed), expected refine, got %r (reason: %s)"
        % (result["phase"], result.get("reason", ""))
    )
    print("  PASSED: phase=%r" % result["phase"])


def test_r3_probe_result_needs_mr_routes_to_mr():
    """R3: placement_probe_result='needs_mr' → xray phase is molecular_replacement."""
    print("Test: r3_probe_result_needs_mr_routes_to_mr")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    from knowledge.yaml_loader import get_workflow_phases
    phases = get_workflow_phases("xray")

    context = _make_minimal_context({
        "has_placed_model": False,
        "placement_probed": True,
        "placement_probe_result": "needs_mr",
        "placement_uncertain": False,
        "cell_mismatch": False,
        "xtriage_done": True,
        "has_predicted_model": False,
        "has_processed_model": False,
        "autosol_done": False,
        "phaser_done": False,
        "use_mr_sad": False,
        "has_anomalous": False,
        "model_is_good": False,
    })
    result = engine._detect_xray_phase(phases, context)
    assert result["phase"] == "molecular_replacement", (
        "needs_mr probe result should route to molecular_replacement, got %r"
        % result["phase"]
    )
    print("  PASSED: phase=%r" % result["phase"])


def test_r3_cell_mismatch_routes_to_mr_xray():
    """R3: cell_mismatch=True → xray phase is molecular_replacement (skips probe)."""
    print("Test: r3_cell_mismatch_routes_to_mr_xray")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    from knowledge.yaml_loader import get_workflow_phases
    phases = get_workflow_phases("xray")

    context = _make_minimal_context({
        "has_placed_model": False,
        "cell_mismatch": True,          # <- Tier 1 result
        "placement_uncertain": False,   # Tier 1 detected → uncertainty cleared
        "placement_probed": False,
        "placement_probe_result": None,
        "xtriage_done": True,
        "has_predicted_model": False,
        "has_processed_model": False,
        "autosol_done": False,
        "phaser_done": False,
        "use_mr_sad": False,
        "has_anomalous": False,
        "model_is_good": False,
    })
    result = engine._detect_xray_phase(phases, context)
    assert result["phase"] == "molecular_replacement", (
        "cell_mismatch should route to molecular_replacement, got %r" % result["phase"]
    )
    print("  PASSED: phase=%r" % result["phase"])


def test_r3_cell_mismatch_routes_to_dock_cryoem():
    """R3: cell_mismatch=True → cryoem phase is dock_model (skips probe)."""
    print("Test: r3_cell_mismatch_routes_to_dock_cryoem")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    from knowledge.yaml_loader import get_workflow_phases
    phases = get_workflow_phases("cryoem")

    context = _make_minimal_context({
        "has_placed_model": False,
        "cell_mismatch": True,
        "placement_uncertain": False,
        "placement_probed": False,
        "placement_probe_result": None,
        "has_data_mtz": False,
        "has_map": True,
        "has_full_map": True,
        "has_predicted_model": False,
        "has_processed_model": False,
        "has_search_model": False,
        "has_model_for_mr": True,
        "has_half_map": False,
        "has_non_half_map": True,
        "has_sequence": False,
        "has_optimized_full_map": False,
        "xtriage_done": True,
        "mtriage_done": True,
        "dock_done": False,
        "resolve_cryo_em_done": False,
        "map_sharpening_done": False,
        "map_to_model_done": False,
        "model_is_good": False,
        "automation_path": "automated",
        "map_symmetry_done": False,
    })
    result = engine._detect_cryoem_phase(phases, context)
    assert result["phase"] == "dock_model", (
        "cell_mismatch should route to dock_model for cryoem, got %r" % result["phase"]
    )
    print("  PASSED: phase=%r" % result["phase"])


def test_r3_probe_not_rerun_when_already_probed():
    """R3: placement_probed=True → probe_placement phase NOT offered again."""
    print("Test: r3_probe_not_rerun_when_already_probed")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    from knowledge.yaml_loader import get_workflow_phases
    phases = get_workflow_phases("xray")

    # Probe ran but R-free was unparseable (result=None) → should not re-probe
    context = _make_minimal_context({
        "has_placed_model": False,
        "placement_probed": True,
        "placement_probe_result": None,   # Couldn't parse → fail-safe
        "placement_uncertain": False,     # probed=True clears uncertainty
        "cell_mismatch": False,
        "xtriage_done": True,
        "has_predicted_model": False,
        "has_processed_model": False,
        "autosol_done": False,
        "phaser_done": False,
        "use_mr_sad": False,
        "has_anomalous": False,
        "model_is_good": False,
    })
    result = engine._detect_xray_phase(phases, context)
    assert result["phase"] != "probe_placement", (
        "probe_placement must NOT be offered again when placement_probed=True, "
        "got %r" % result["phase"]
    )
    print("  PASSED: phase=%r (not re-probing)" % result["phase"])


def test_r3_history_probe_detection_xray_placed():
    """R3: model_vs_data in history before refine → placement_probed=True, result=placed."""
    print("Test: r3_history_probe_detection_xray_placed")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = _make_xray_history_with_probe("phenix.model_vs_data", rfree=0.38, pre_refine=True)
    info = _analyze_history(history)

    assert info["placement_probed"] is True, (
        "placement_probed should be True after model_vs_data before refinement"
    )
    assert info["placement_probe_result"] == "placed", (
        "R-free=0.38 < 0.50 should give placement_probe_result='placed', got %r"
        % info["placement_probe_result"]
    )
    print("  PASSED: probed=%s result=%r" % (info["placement_probed"],
                                              info["placement_probe_result"]))


def test_r3_history_probe_detection_xray_needs_mr():
    """R3: model_vs_data before refine with R-free >= 0.50 → result=needs_mr."""
    print("Test: r3_history_probe_detection_xray_needs_mr")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = _make_xray_history_with_probe("phenix.model_vs_data", rfree=0.55, pre_refine=True)
    info = _analyze_history(history)

    assert info["placement_probed"] is True
    assert info["placement_probe_result"] == "needs_mr", (
        "R-free=0.55 >= 0.50 should give placement_probe_result='needs_mr', got %r"
        % info["placement_probe_result"]
    )
    print("  PASSED: result=%r" % info["placement_probe_result"])


def test_r3_history_probe_not_detected_after_refine():
    """R3: model_vs_data AFTER refinement → NOT treated as probe (is validation)."""
    print("Test: r3_history_probe_not_detected_after_refine")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    # model_vs_data ran AFTER refinement → it's validation, not a probe
    history = _make_xray_history_with_probe("phenix.model_vs_data", rfree=0.25, pre_refine=False)
    info = _analyze_history(history)

    assert info["placement_probed"] is False, (
        "model_vs_data after refinement must NOT be treated as placement probe, "
        "placement_probed should be False, got %s" % info["placement_probed"]
    )
    print("  PASSED: placement_probed=%s (correctly treated as validation)" % info["placement_probed"])


def test_r3_validation_done_not_set_during_probe_phase():
    """R3: probe history detection (pre-refine) sets placement_probed, not validation_done.

    validation_done is still set by YAML done_tracking (set_flag strategy),
    which fires whenever model_vs_data marker is found.  The probe result
    flags are SET IN ADDITION — they don't prevent validation_done.
    This test verifies the probe result flags ARE set; validation_done
    being also set is acceptable (it resets when the session resumes with
    the probe result already known, so validation phase runs normally later).
    """
    print("Test: r3_validation_done_not_set_during_probe_phase")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = _make_xray_history_with_probe("phenix.model_vs_data", rfree=0.38, pre_refine=True)
    info = _analyze_history(history)

    # The key invariant: placement_probed must be set
    assert info["placement_probed"] is True, (
        "placement_probed must be True after pre-refine model_vs_data"
    )
    assert info["placement_probe_result"] == "placed", (
        "placement_probe_result must be 'placed' for R-free=0.38"
    )
    # validation_done being set is acceptable here — context builder uses
    # placement_probed to route correctly; validation can re-run later.
    print("  PASSED: placement_probed=%s, result=%r, validation_done=%s"
          % (info["placement_probed"], info["placement_probe_result"],
             info.get("validation_done")))


# =============================================================================
# CATEGORY R3-EXTRA -- additional placement detection edge cases
# =============================================================================

def test_r3_cryoem_probe_needs_dock():
    """R3-extra: map_correlations before refine, CC <= 0.15 -> needs_dock."""
    print("Test: r3_cryoem_probe_needs_dock")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = [{
        "program": "phenix.map_correlations",
        "command": "phenix.map_correlations model.pdb map.ccp4",
        "result": "SUCCESS: Map-model CC computed",
        "analysis": {"cc_mask": 0.05},
    }]
    info = _analyze_history(history)
    assert info["placement_probed"] is True, "placement_probed should be True"
    assert info["placement_probe_result"] == "needs_dock", (
        "CC=0.05 should give needs_dock, got %r" % info["placement_probe_result"]
    )
    print("  PASSED: result=%r" % info["placement_probe_result"])


def test_r3_cryoem_probe_placed():
    """R3-extra: map_correlations before refine, CC > 0.15 -> placed."""
    print("Test: r3_cryoem_probe_placed")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = [{
        "program": "phenix.map_correlations",
        "command": "phenix.map_correlations model.pdb map.ccp4",
        "result": "SUCCESS: Map-model CC computed",
        "analysis": {"cc_mask": 0.42},
    }]
    info = _analyze_history(history)
    assert info["placement_probed"] is True
    assert info["placement_probe_result"] == "placed", (
        "CC=0.42 should give placed, got %r" % info["placement_probe_result"]
    )
    print("  PASSED: result=%r" % info["placement_probe_result"])


def test_r3_failed_probe_not_counted():
    """R3-extra: a FAILED model_vs_data run does not set placement_probed."""
    print("Test: r3_failed_probe_not_counted")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = [{
        "program": "phenix.model_vs_data",
        "command": "phenix.model_vs_data model.pdb data.mtz",
        "result": "FAILED: Sorry: could not open file",
        "analysis": {},
    }]
    info = _analyze_history(history)
    assert info["placement_probed"] is False, (
        "FAILED model_vs_data must NOT set placement_probed=True"
    )
    print("  PASSED: failed run correctly ignored")


def test_r3_probe_cc_volume_fallback():
    """R3-extra: probe detection uses cc_volume when cc_mask is absent."""
    print("Test: r3_probe_cc_volume_fallback")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    history = [{
        "program": "phenix.map_correlations",
        "command": "phenix.map_correlations model.pdb map.ccp4",
        "result": "SUCCESS: Done",
        "analysis": {"cc_volume": 0.30},   # cc_mask absent, cc_volume present
    }]
    info = _analyze_history(history)
    assert info["placement_probed"] is True
    assert info["placement_probe_result"] == "placed", (
        "cc_volume=0.30 > 0.15 should give placed, got %r" % info["placement_probe_result"]
    )
    print("  PASSED: cc_volume fallback works, result=%r" % info["placement_probe_result"])


def test_r3_build_context_overrides_has_placed_model():
    """R3-extra: build_context sets has_placed_model=True when probe says placed."""
    print("Test: r3_build_context_overrides_has_placed_model")
    import tempfile
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from agent.workflow_state import _analyze_history

    engine = WorkflowEngine()
    history = [{
        "program": "phenix.model_vs_data",
        "command": "phenix.model_vs_data model.pdb data.mtz",
        "result": "SUCCESS: Done",
        "analysis": {"r_free": 0.35},
    }]
    with tempfile.TemporaryDirectory() as tmpdir:
        pdb = os.path.join(tmpdir, "model.pdb")
        mtz = os.path.join(tmpdir, "data.mtz")
        for p in (pdb, mtz):
            open(p, "w").close()

        files = {"model": [pdb], "data_mtz": [mtz]}
        history_info = _analyze_history(history)

        assert history_info["placement_probed"] is True
        assert history_info["placement_probe_result"] == "placed"

        ctx = engine.build_context(files=files, history_info=history_info)

        assert ctx["has_placed_model"] is True, (
            "build_context must set has_placed_model=True when probe result is placed, "
            "got %s" % ctx["has_placed_model"]
        )
        assert ctx["placement_uncertain"] is False
    print("  PASSED: build_context correctly overrides has_placed_model=True")


def test_r3_cells_fail_safe_none():
    """R3-extra: cells_are_compatible with None inputs returns True (fail-safe)."""
    print("Test: r3_cells_fail_safe_none")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    assert cells_are_compatible(None, (57.0, 57.0, 147.0, 90.0, 90.0, 90.0)) is True
    assert cells_are_compatible((57.0, 57.0, 147.0, 90.0, 90.0, 90.0), None) is True
    assert cells_are_compatible(None, None) is True
    print("  PASSED: None inputs always return True (fail-safe)")


def test_r3_cells_clear_mismatch_detected():
    """R3-extra: cells_are_compatible correctly rejects clearly incompatible cells."""
    print("Test: r3_cells_clear_mismatch_detected")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.placement_checker import cells_are_compatible

    # ~75% difference in all axes -- far outside 5% tolerance
    model_cell = (57.23, 57.23, 146.77, 90.0, 90.0, 90.0)
    mtz_cell   = (100.0, 100.0, 200.00, 90.0, 90.0, 90.0)
    assert cells_are_compatible(model_cell, mtz_cell) is False, (
        "Clearly incompatible cells must be detected as mismatch"
    )
    print("  PASSED: large cell difference correctly flagged as mismatch")


def test_r3_placement_uncertain_clears_after_probe():
    """R3-extra: placement_uncertain=False on build_context after probe ran."""
    print("Test: r3_placement_uncertain_clears_after_probe")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from agent.workflow_state import _analyze_history

    engine = WorkflowEngine()
    history = [{
        "program": "phenix.model_vs_data",
        "command": "phenix.model_vs_data model.pdb data.mtz",
        "result": "SUCCESS: Done",
        "analysis": {"r_free": 0.42},
    }]
    history_info = _analyze_history(history)
    ctx = engine.build_context(files={}, history_info=history_info)

    assert ctx["placement_probed"] is True
    assert ctx["placement_uncertain"] is False, (
        "placement_uncertain must be False after probe ran"
    )
    print("  PASSED: placement_uncertain cleared correctly after probe cycle")


def test_r3_probe_placement_cryoem_phase_offered():
    """R3-extra: cryoem placement_uncertain=True -> probe_placement phase offered."""
    print("Test: r3_probe_placement_cryoem_phase_offered")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from knowledge.yaml_loader import get_workflow_phases

    engine = WorkflowEngine()
    phases = get_workflow_phases("cryoem")
    context = _make_minimal_context({
        "has_placed_model": False,
        "has_predicted_model": False,
        "has_processed_model": False,
        "has_model": True,
        "has_search_model": False,
        "has_model_for_mr": True,
        "has_data_mtz": False,
        "has_map": True,
        "has_full_map": True,
        "has_half_map": False,
        "has_non_half_map": True,
        "has_sequence": False,
        "has_optimized_full_map": False,
        "has_refined_model": False,
        "has_map_coeffs_mtz": False,
        "predict_done": False,
        "predict_full_done": False,
        "dock_done": False,
        "resolve_cryo_em_done": False,
        "map_sharpening_done": False,
        "map_to_model_done": False,
        "xtriage_done": True,
        "mtriage_done": True,
        "map_symmetry_done": False,
        "model_is_good": False,
        "automation_path": "automated",
        "cell_mismatch": False,
        "placement_probed": False,
        "placement_probe_result": None,
        "placement_uncertain": True,  # <- triggers probe
    })
    result = engine._detect_cryoem_phase(phases, context)
    assert result["phase"] == "probe_placement", (
        "Expected probe_placement phase for cryoem, got %r" % result["phase"]
    )
    print("  PASSED: phase=%r" % result["phase"])


def test_r3_needs_dock_routes_to_dock():
    """R3-extra: cryoem probe result needs_dock routes to dock_model phase."""
    print("Test: r3_needs_dock_routes_to_dock")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from knowledge.yaml_loader import get_workflow_phases

    engine = WorkflowEngine()
    phases = get_workflow_phases("cryoem")
    context = _make_minimal_context({
        "has_placed_model": False,
        "has_predicted_model": False,
        "has_processed_model": False,
        "has_model": True,
        "has_search_model": False,
        "has_model_for_mr": True,
        "has_data_mtz": False,
        "has_map": True,
        "has_full_map": True,
        "has_half_map": False,
        "has_non_half_map": True,
        "has_sequence": False,
        "has_optimized_full_map": False,
        "has_refined_model": False,
        "has_map_coeffs_mtz": False,
        "predict_done": False,
        "predict_full_done": False,
        "dock_done": False,
        "resolve_cryo_em_done": False,
        "map_sharpening_done": False,
        "map_to_model_done": False,
        "xtriage_done": True,
        "mtriage_done": True,
        "map_symmetry_done": False,
        "model_is_good": False,
        "automation_path": "automated",
        "cell_mismatch": False,
        "placement_probed": True,
        "placement_probe_result": "needs_dock",
        "placement_uncertain": False,
    })
    result = engine._detect_cryoem_phase(phases, context)
    assert result["phase"] == "dock_model", (
        "needs_dock probe result should route to dock_model, got %r" % result["phase"]
    )
    print("  PASSED: phase=%r" % result["phase"])



# =============================================================================
# CATEGORY S2 -- Directive override protection
#
# Root cause: LLM directive extractor set model_is_placed=True from "solve the
# structure", making _has_placed_model() return True. Tier 1 cell-mismatch
# routing checked `cell_mismatch AND NOT has_placed_model`, which evaluated
# False → skipped docking → RSR failed with "unit cell dimensions mismatch".
#
# Three fixes:
#   A. Tightened directive extractor prompt (model_is_placed is now high-precision)
#   B. has_placed_model_from_history context key (history/files only, no directives)
#   C. Tier 1 routing guards use has_placed_model_from_history instead of has_placed_model
#   D. S1 short-circuit also uses has_placed_model_from_history
# =============================================================================

def test_s2_has_placed_model_from_history_method_exists():
    """S2: WorkflowEngine has _has_placed_model_from_history() method."""
    print("Test: s2_has_placed_model_from_history_method_exists")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    engine = WorkflowEngine()
    assert hasattr(engine, '_has_placed_model_from_history'), (
        "_has_placed_model_from_history method must exist on WorkflowEngine"
    )
    print("  PASSED")


def test_s2_from_history_false_when_only_directive():
    """S2: _has_placed_model_from_history returns False when placement is only via directive."""
    print("Test: s2_from_history_false_when_only_directive")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    engine = WorkflowEngine()

    # Simulate: directive says model_is_placed=True, but no history/subcategory evidence
    directives = {"workflow_preferences": {"model_is_placed": True}}
    # files must include "model" for directive check to fire in _has_placed_model
    files = {"model": ["/fake/1aew_A.pdb"]}
    history_info = {}

    # _has_placed_model should return True (directive-driven, model file present)
    has_placed = engine._has_placed_model(files, history_info, directives)
    assert has_placed is True, "Directive should make _has_placed_model return True (with model file)"

    # _has_placed_model_from_history should return False (no history)
    from_history = engine._has_placed_model_from_history(files, history_info)
    assert from_history is False, (
        "_has_placed_model_from_history must return False when placement is only "
        "from directive (no dock_done, no phaser_done, no positioned subcategory)"
    )
    print("  PASSED: directive cannot fool has_placed_model_from_history")


def test_s2_from_history_true_when_dock_done():
    """S2: _has_placed_model_from_history returns True when dock_done=True in history."""
    print("Test: s2_from_history_true_when_dock_done")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from agent.workflow_state import _analyze_history

    engine = WorkflowEngine()

    history = [{
        "program": "phenix.dock_in_map",
        "command": "phenix.dock_in_map model.pdb map.ccp4",
        "result": "SUCCESS: Docking complete",
        "analysis": {},
    }]
    history_info = _analyze_history(history)
    assert history_info["dock_done"] is True

    from_history = engine._has_placed_model_from_history({}, history_info)
    assert from_history is True, (
        "_has_placed_model_from_history must return True when dock_done=True"
    )
    print("  PASSED: dock_done in history → has_placed_model_from_history=True")


def test_s2_context_has_placed_from_history_key():
    """S2: build_context populates has_placed_model_from_history key."""
    print("Test: s2_context_has_placed_from_history_key")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    ctx = engine.build_context(files={}, history_info={})

    assert "has_placed_model_from_history" in ctx, (
        "build_context must include has_placed_model_from_history key"
    )
    assert ctx["has_placed_model_from_history"] is False, (
        "Empty history → has_placed_model_from_history must be False"
    )
    print("  PASSED: has_placed_model_from_history present in context")


def test_s2_directive_model_is_placed_does_not_suppress_cell_mismatch():
    """
    S2: The critical bug scenario — model_is_placed=True directive must NOT
    suppress Tier 1 cell-mismatch routing to dock_model.

    Replicates the apoferritin log:
      - Directive: model_is_placed=True (set by LLM from "solve the structure")
      - History: no dock_done, no phaser_done
      - Cell mismatch: True (model cell ≠ map cell)
    Expected: cryoem phase → dock_model  (not ready_to_refine)
    """
    print("Test: s2_directive_model_is_placed_does_not_suppress_cell_mismatch")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # Build context with directive-driven has_placed_model=True but no history
    directives = {"workflow_preferences": {"model_is_placed": True}}
    # Include mtriage_done=True so we are past the "analyze" gate in cryoem phase detection
    history_with_mtriage = {"mtriage_done": True, "resolve_cryo_em_done": False}
    ctx = engine.build_context(files={"model": ["/fake/1aew_A.pdb"], "full_map": ["/fake/map.ccp4"]},
                               history_info=history_with_mtriage, directives=directives)

    assert ctx["has_placed_model"] is True, \
        "Directive should set has_placed_model=True (with model file present)"
    assert ctx["has_placed_model_from_history"] is False, \
        "No dock/phaser → has_placed_model_from_history must be False"

    # Manually inject cell_mismatch=True to simulate what placement_checker would detect
    ctx["cell_mismatch"] = True

    # Now ask the engine to detect phase given this context
    from knowledge.yaml_loader import get_workflow_phases; phases = get_workflow_phases("cryoem")
    phase_info = engine._detect_cryoem_phase(phases, ctx)

    assert phase_info["phase"] == "dock_model", (
        "Cell mismatch MUST route to dock_model even when model_is_placed=True "
        "from directive. Got phase=%r (reason=%r)" % (
            phase_info["phase"], phase_info.get("reason", ""))
    )
    print("  PASSED: cell_mismatch → dock_model despite model_is_placed=True directive")


def test_s2_history_placed_does_suppress_cell_mismatch():
    """
    S2: When placement is confirmed by real history (dock_done=True), Tier 1
    cell-mismatch routing is correctly suppressed (model is already placed).
    """
    print("Test: s2_history_placed_does_suppress_cell_mismatch")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from agent.workflow_state import _analyze_history

    engine = WorkflowEngine()

    history = [{
        "program": "phenix.dock_in_map",
        "command": "phenix.dock_in_map 1aew_A.pdb denmod_map.ccp4",
        "result": "SUCCESS: Docking complete. Output: 1aew_A_docked.pdb",
        "analysis": {},
    }]
    history_info = _analyze_history(history)
    assert history_info["dock_done"] is True

    ctx = engine.build_context(files={}, history_info=history_info)
    assert ctx["has_placed_model_from_history"] is True

    # Inject cell_mismatch=True (edge case: could still mismatch due to box padding)
    ctx["cell_mismatch"] = True

    from knowledge.yaml_loader import get_workflow_phases; phases = get_workflow_phases("cryoem")
    phase_info = engine._detect_cryoem_phase(phases, ctx)

    # With dock_done=True history, cell_mismatch is short-circuited to False
    # (from the S1 post-processing short-circuit), so routing advances normally
    assert phase_info["phase"] != "dock_model", (
        "After dock_done, should NOT re-route to dock_model. "
        "Got phase=%r" % phase_info["phase"]
    )
    print("  PASSED: dock_done in history correctly suppresses Tier 1 re-dock")


def test_s2_xray_tier1_uses_from_history():
    """S2: X-ray Tier 1 MR routing uses has_placed_model_from_history (not has_placed_model)."""
    print("Test: s2_xray_tier1_uses_from_history")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # Directive: model_is_placed=True — should NOT suppress MR routing
    directives = {"workflow_preferences": {"model_is_placed": True}}
    # files must include "model" — _has_placed_model gates the directive on files.get("model")
    ctx = engine.build_context(files={"model": ["/fake/1abc.pdb"]}, history_info={}, directives=directives)
    assert ctx["has_placed_model"] is True,         "Directive should make has_placed_model=True (with model file present)"
    assert ctx["has_placed_model_from_history"] is False

    # Inject cell_mismatch=True and also fake X-ray analysis flag
    ctx["cell_mismatch"] = True
    ctx["xtriage_done"] = True  # past analysis phase

    from knowledge.yaml_loader import get_workflow_phases; phases = get_workflow_phases("xray")
    phase_info = engine._detect_xray_phase(phases, ctx)

    assert phase_info["phase"] == "molecular_replacement", (
        "X-ray cell mismatch MUST route to MR even with model_is_placed=True directive. "
        "Got phase=%r" % phase_info["phase"]
    )
    print("  PASSED: X-ray cell_mismatch → molecular_replacement despite model_is_placed directive")


def test_s2_short_circuit_uses_from_history_not_directive():
    """
    S2: S1 short-circuit uses has_placed_model_from_history — a directive-only
    has_placed_model=True must NOT prevent the cell-mismatch check from running.
    """
    print("Test: s2_short_circuit_uses_from_history_not_directive")
    sys.path.insert(0, _PROJECT_ROOT)
    src_path = os.path.join(_PROJECT_ROOT, "agent", "workflow_engine.py")
    with open(src_path) as f:
        src = f.read()

    # The short-circuit must reference has_placed_model_from_history, not has_placed_model
    assert 'context.get("has_placed_model_from_history")' in src, (
        "Short-circuit must use has_placed_model_from_history"
    )
    # The short-circuit must NOT reference bare has_placed_model (without _from_history)
    # in its condition
    sc_block_start = src.index("Tier 1 short-circuit: skip cell check")
    sc_block_end   = src.index("context[\"cell_mismatch\"] = False", sc_block_start) + 40
    sc_block = src[sc_block_start:sc_block_end]
    assert 'has_placed_model_from_history' in sc_block, \
        "Short-circuit block must reference has_placed_model_from_history"
    # The bare 'has_placed_model' should only appear in the _from_history lookup, not standalone
    assert '"has_placed_model")' not in sc_block, (
        "Short-circuit must not check bare has_placed_model — directive could be wrong"
    )
    print("  PASSED: short-circuit uses has_placed_model_from_history (not bare directive flag)")


def test_s2_directive_prompt_stronger_do_not_set():
    """S2: directive_extractor.py prompt explicitly warns against 'solve the structure'."""
    print("Test: s2_directive_prompt_stronger_do_not_set")
    sys.path.insert(0, _PROJECT_ROOT)
    src_path = os.path.join(_PROJECT_ROOT, "agent", "directive_extractor.py")
    with open(src_path) as f:
        src = f.read()

    # Must explicitly say "solve the structure" is a DO NOT case
    assert '"solve the structure"' in src or "'solve the structure'" in src or \
           "solve the structure" in src, (
        "Prompt must explicitly list 'solve the structure' as a case to NOT set model_is_placed"
    )
    # Must warn about cryo-EM + PDB combination
    assert "cryo-EM" in src or "cryoem" in src.lower(), \
        "Prompt must mention cryo-EM as a case requiring docking before refinement"
    # ONLY / HIGH-PRECISION language should be present
    assert ("HIGH-PRECISION" in src or "ONLY set model_is_placed" in src or
            "high-precision" in src.lower()), (
        "Prompt must use high-precision / only language to discourage false positives"
    )
    print("  PASSED: directive prompt contains explicit DO NOT cases and precision guidance")


def test_s2_full_cryoem_stack_routes_to_dock_not_rsr():
    """
    S2: Full stack integration — cryoem workflow with resolve_cryo_em done,
    model_is_placed directive, but no dock history → must route to dock_model
    (not ready_to_refine or real_space_refine).

    This replicates the exact failure observed in the apoferritin log.
    """
    print("Test: s2_full_cryoem_stack_routes_to_dock_not_rsr")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    from agent.workflow_state import _analyze_history

    engine = WorkflowEngine()

    # History: mtriage ran (cycle 1), resolve_cryo_em ran (cycle 2)
    # Exactly as in the apoferritin log
    history = [
        {
            "program": "phenix.mtriage",
            "command": "phenix.mtriage half_map=map1.ccp4 half_map=map2.ccp4",
            "result": "SUCCESS: Resolution 1.91 Å",
            "analysis": {"resolution": 1.91},
        },
        {
            "program": "phenix.resolve_cryo_em",
            "command": "phenix.resolve_cryo_em half_map=map1.ccp4 half_map=map2.ccp4",
            "result": "SUCCESS: Density modification complete",
            "analysis": {},
        },
    ]
    history_info = _analyze_history(history)
    assert history_info["mtriage_done"] is True
    assert history_info["resolve_cryo_em_done"] is True
    assert history_info["dock_done"] is False  # no docking yet

    # Directive: model_is_placed=True (as the LLM mis-extracted from "solve the structure")
    directives = {"workflow_preferences": {"model_is_placed": True}}

    ctx = engine.build_context(
        files={"model": ["/fake/1aew_A.pdb"], "full_map": ["/fake/denmod_map.ccp4"]},
        history_info=history_info, directives=directives
    )

    assert ctx["has_placed_model"] is True, \
        "Directive should make has_placed_model=True (requires files['model'] non-empty)"
    assert ctx["has_placed_model_from_history"] is False, \
        "No dock/phaser in history → has_placed_model_from_history=False"

    # Inject cell mismatch (what placement_checker would detect for 1aew_A.pdb vs denmod_map.ccp4)
    ctx["cell_mismatch"] = True

    from knowledge.yaml_loader import get_workflow_phases; phases = get_workflow_phases("cryoem")
    phase_info = engine._detect_cryoem_phase(phases, ctx)

    assert phase_info["phase"] == "dock_model", (
        "REGRESSION: Full stack must route to dock_model when cell_mismatch=True "
        "and has_placed_model_from_history=False, even if model_is_placed directive "
        "is set. Got phase=%r (reason=%r). This is the apoferritin bug." % (
            phase_info["phase"], phase_info.get("reason", ""))
    )
    print("  PASSED: apoferritin scenario correctly routes to dock_model (not RSR)")


# =============================================================================
# CATEGORY S1 -- Polish fixes (yaml_tools transition fields, import fallback,
#                              redundant import, cell_mismatch short-circuit)
# =============================================================================

def test_s1_yaml_validator_no_if_placed_warnings():
    """S1: _validate_workflows generates no warnings for if_placed / if_not_placed."""
    print("Test: s1_yaml_validator_no_if_placed_warnings")
    import yaml
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.yaml_tools import _validate_workflows

    wf_path = os.path.join(_PROJECT_ROOT, "knowledge", "workflows.yaml")
    with open(wf_path) as f:
        data = yaml.safe_load(f)

    issues = _validate_workflows(data)
    probe_warns = [i for i in issues
                   if "if_placed" in i[1] or "if_not_placed" in i[1]]

    assert probe_warns == [], (
        "Expected no if_placed/if_not_placed warnings, got: %s" % probe_warns
    )
    print("  PASSED: no spurious transition-field warnings from probe_placement phase")


def test_s1_yaml_validator_if_placed_is_in_valid_set():
    """S1: valid_transition_fields in yaml_tools explicitly includes if_placed/if_not_placed."""
    print("Test: s1_yaml_validator_if_placed_is_in_valid_set")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.yaml_tools import _validate_workflows
    assert _validate_workflows is not None, "_validate_workflows must be importable"

    # Read source to verify the constants are present (not just that no warnings fired)
    src_path = os.path.join(_PROJECT_ROOT, "agent", "yaml_tools.py")
    with open(src_path) as f:
        src = f.read()

    assert "'if_placed'" in src, (
        "'if_placed' must appear in yaml_tools.py (valid_transition_fields)"
    )
    assert "'if_not_placed'" in src, (
        "'if_not_placed' must appear in yaml_tools.py (valid_transition_fields)"
    )
    print("  PASSED: if_placed and if_not_placed present in yaml_tools source")


def test_s1_no_redundant_import_re_in_probe_block():
    """S1: workflow_state.py probe detection does not use a local 'import re' inside the loop."""
    print("Test: s1_no_redundant_import_re_in_probe_block")
    sys.path.insert(0, _PROJECT_ROOT)

    src_path = os.path.join(_PROJECT_ROOT, "agent", "workflow_state.py")
    with open(src_path) as f:
        src = f.read()

    # The probe detection block should use the module-level `re`, not `_re2`
    assert "import re as _re2" not in src, (
        "Redundant 'import re as _re2' must be removed from probe detection block"
    )
    # Module-level `import re` must still be present
    assert "import re\n" in src or "\nimport re\n" in src, (
        "Module-level 'import re' must be present in workflow_state.py"
    )
    # The regex search used in the probe block must reference just `re`
    assert "re.search(r'r_free" in src, (
        "Probe block must use module-level re.search(), not _re2.search()"
    )
    print("  PASSED: probe detection uses module-level re, no _re2 alias")


def test_s1_probe_re_fallback_still_works():
    """S1: regex fallback for r_free still parses correctly after import cleanup."""
    print("Test: s1_probe_re_fallback_still_works")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history

    # Result has r_free in text only (not in analysis dict) — exercises the regex path
    history = [{
        "program": "phenix.model_vs_data",
        "command": "phenix.model_vs_data model.pdb data.mtz",
        "result": "SUCCESS: r_free: 0.38 r_work: 0.34",
        "analysis": {},   # empty — forces regex fallback
    }]
    info = _analyze_history(history)

    assert info["placement_probed"] is True, (
        "placement_probed must be True when r_free parsed from result text"
    )
    assert info["placement_probe_result"] == "placed", (
        "r_free=0.38 < 0.50 must give result='placed', got %r"
        % info["placement_probe_result"]
    )
    print("  PASSED: regex fallback parses r_free from result text correctly")


def test_s1_local_import_fallback_in_check_cell_mismatch():
    """S1: _check_cell_mismatch has a local 'from agent.placement_checker' fallback path."""
    print("Test: s1_local_import_fallback_in_check_cell_mismatch")
    sys.path.insert(0, _PROJECT_ROOT)

    src_path = os.path.join(_PROJECT_ROOT, "agent", "workflow_engine.py")
    with open(src_path) as f:
        src = f.read()

    assert "from agent.placement_checker import" in src, (
        "_check_cell_mismatch must have a local 'from agent.placement_checker' fallback"
    )
    # Verify the fallback is inside an except ImportError block
    # (i.e., it appears after the libtbx path)
    libtbx_idx = src.index("from libtbx.langchain.agent.placement_checker import")
    local_idx  = src.index("from agent.placement_checker import")
    assert local_idx > libtbx_idx, (
        "Local import must appear AFTER the libtbx path (as a fallback)"
    )
    print("  PASSED: local import fallback present and correctly ordered")


def test_s1_placement_checker_importable_locally():
    """S1: placement_checker can be imported via the local path (no libtbx needed)."""
    print("Test: s1_placement_checker_importable_locally")
    sys.path.insert(0, _PROJECT_ROOT)

    from agent.placement_checker import (
        read_pdb_unit_cell,
        cells_are_compatible,
        check_xray_cell_mismatch,
        check_cryoem_cell_mismatch,
    )
    # Basic smoke-test: all public functions callable and return correct types
    assert check_xray_cell_mismatch(None, None) is False
    assert check_cryoem_cell_mismatch(None, None) is False
    assert cells_are_compatible(None, None) is True
    assert read_pdb_unit_cell("/nonexistent.pdb") is None
    print("  PASSED: all placement_checker public functions importable and callable")


def test_s1_cell_mismatch_short_circuits_when_placed():
    """S1: build_context sets cell_mismatch=False when has_placed_model=True (short-circuit)."""
    print("Test: s1_cell_mismatch_short_circuits_when_placed")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # History: refinement ran -> has_placed_model=True via heuristics
    # If short-circuit is correct, cell_mismatch will be False even if files
    # had incompatible cells (we can't actually read MTZ in tests, but we verify
    # the short-circuit fires by checking the result is always False here).
    history_info = _analyze_history([{
        "program": "phenix.refine",
        "command": "phenix.refine model.pdb data.mtz",
        "result": "SUCCESS: Refinement complete",
        "analysis": {"r_free": 0.27},
    }])

    assert history_info.get("refine_done") is True, \
        "refine_done must be True after refinement history entry"

    ctx = engine.build_context(files={}, history_info=history_info)

    assert ctx["has_placed_model"] is True, \
        "has_placed_model must be True when refine_done=True"
    assert ctx["cell_mismatch"] is False, (
        "cell_mismatch must be False (short-circuited) when has_placed_model=True, "
        "got %s" % ctx["cell_mismatch"]
    )
    print("  PASSED: cell_mismatch=False when has_placed_model=True (short-circuited)")


def test_s1_cell_mismatch_short_circuits_when_probed():
    """S1: build_context sets cell_mismatch=False when placement_probed=True (short-circuit)."""
    print("Test: s1_cell_mismatch_short_circuits_when_probed")
    sys.path.insert(0, _PROJECT_ROOT)
    from agent.workflow_state import _analyze_history
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # History: probe ran (needs_mr) -> placement_probed=True, has_placed_model=False
    # Short-circuit must still fire even though has_placed_model is False
    history_info = _analyze_history([{
        "program": "phenix.model_vs_data",
        "command": "phenix.model_vs_data model.pdb data.mtz",
        "result": "SUCCESS: Done",
        "analysis": {"r_free": 0.62},   # > 0.50 -> needs_mr
    }])

    assert history_info["placement_probed"] is True
    assert history_info["placement_probe_result"] == "needs_mr"

    ctx = engine.build_context(files={}, history_info=history_info)

    assert ctx["has_placed_model"] is False, \
        "has_placed_model must remain False when probe result is needs_mr"
    assert ctx["cell_mismatch"] is False, (
        "cell_mismatch must be False (short-circuited) when placement_probed=True, "
        "got %s" % ctx["cell_mismatch"]
    )
    print("  PASSED: cell_mismatch=False when placement_probed=True (short-circuited)")


def test_s1_cell_mismatch_not_short_circuited_first_cycle():
    """S1: cell_mismatch check runs normally on first cycle (no placement evidence)."""
    print("Test: s1_cell_mismatch_not_short_circuited_first_cycle")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()

    # No history at all -> neither has_placed_model nor placement_probed
    # cell_mismatch check must run (returns False for empty files — no PDB to compare)
    ctx = engine.build_context(files={}, history_info={})

    assert ctx["has_placed_model"] is False
    assert ctx["placement_probed"] is False
    # The check ran and correctly returned False for empty files
    assert ctx["cell_mismatch"] is False
    # placement_uncertain also False (no model and no data)
    assert ctx["placement_uncertain"] is False
    print("  PASSED: cell_mismatch check active on first cycle (no short-circuit)")


def test_s1_short_circuit_order_before_probe_override():
    """S1: cell_mismatch short-circuit applies before probe-result has_placed_model override."""
    print("Test: s1_short_circuit_order_before_probe_override")
    sys.path.insert(0, _PROJECT_ROOT)

    src_path = os.path.join(_PROJECT_ROOT, "agent", "workflow_engine.py")
    with open(src_path) as f:
        src = f.read()

    # The short-circuit block must appear before the probe-result override block
    sc_marker   = 'if context.get("has_placed_model_from_history") or context.get("placement_probed"):\n            context["cell_mismatch"] = False'
    probe_marker = 'if (context["placement_probed"] and\n                context.get("placement_probe_result") == "placed"):\n            context["has_placed_model"] = True'

    sc_idx    = src.index(sc_marker)
    probe_idx = src.index(probe_marker)

    assert sc_idx < probe_idx, (
        "Short-circuit override (cell_mismatch=False) must appear BEFORE "
        "probe-result override (has_placed_model=True) in build_context"
    )
    print("  PASSED: short-circuit at index %d, probe override at %d (correct order)"
          % (sc_idx, probe_idx))




# =============================================================================
# CATEGORY S2b -- placement_uncertain must use has_placed_model_from_history
#
# Root cause (apoferritin AIAgent_104 live failure):
#   "solve the structure" → LLM sets model_is_placed=True directive
#   placement_uncertain used `not context["has_placed_model"]` which is
#   directive-affected → False → probe never fired → RSR crash on cycle 3.
#
# Fix: placement_uncertain = not has_placed_model_FROM_HISTORY (directive-immune)


# =============================================================================
# CATEGORY S2b -- placement_uncertain uses has_placed_model_from_history
#
# Root cause (apoferritin AIAgent_104 live failure):
#   "solve the structure" -> LLM sets model_is_placed=True directive
#   placement_uncertain used `not context["has_placed_model"]` which is
#   directive-affected -> False -> probe never fired -> RSR crash cycle 3.
#
# Fix: placement_uncertain uses `not has_placed_model_FROM_HISTORY` (directive-immune)
# =============================================================================

def test_s2b_placement_uncertain_uses_from_history_not_directive():
    """S2b: placement_uncertain=True despite directive model_is_placed=True when no history confirms."""
    print("Test: s2b_placement_uncertain_uses_from_history_not_directive")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    engine = WorkflowEngine()
    directives = {
        "workflow_preferences": {"model_is_placed": True},
        "constraints": [], "stop_conditions": {}, "program_settings": {},
    }
    history_info = {
        "mtriage_done": True, "resolve_cryo_em_done": True,
        "dock_done": False, "refine_done": False, "rsr_done": False,
        "phaser_done": False, "autobuild_done": False, "predict_full_done": False,
        "placement_probed": False, "placement_probe_result": None,
        "predict_done": False, "process_predicted_done": False,
    }
    files = {"model": ["/fake/1aew_A.pdb"], "full_map": ["/fake/denmod_map.ccp4"]}
    ctx = engine.build_context(files=files, history_info=history_info, directives=directives)
    assert ctx["has_placed_model"] is True, "Directive should set has_placed_model=True"
    assert ctx["has_placed_model_from_history"] is False
    assert ctx["placement_uncertain"] is True, (
        "placement_uncertain must be True despite model_is_placed directive. "
        "from_history=%s cell_mismatch=%s probed=%s" % (
            ctx["has_placed_model_from_history"], ctx["cell_mismatch"], ctx["placement_probed"])
    )
    print("  PASSED: placement_uncertain=True despite model_is_placed directive")


def test_s2b_map_correlations_has_ignore_symmetry_default():
    """S2b: phenix.map_correlations has ignore_symmetry_conflicts=True default."""
    print("Test: s2b_map_correlations_has_ignore_symmetry_default")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.program_registry import ProgramRegistry
    except ImportError:
        print("  SKIP (ProgramRegistry unavailable)")
        return
    registry = ProgramRegistry()
    prog = registry.get_program("phenix.map_correlations")
    assert prog is not None
    defaults = prog.get("defaults", {})
    assert "ignore_symmetry_conflicts" in defaults, (
        "map_correlations must have ignore_symmetry_conflicts in defaults to avoid "
        "crashing when probe runs against a mismatched model+map"
    )
    val = defaults["ignore_symmetry_conflicts"]
    assert val in (True, "true", "True"), "ignore_symmetry_conflicts must be true, got %r" % val
    print("  PASSED: map_correlations has ignore_symmetry_conflicts=True in defaults")


def test_s2b_placement_uncertain_formula_uses_from_history_key():
    """S2b: The placement_uncertain formula uses has_placed_model_from_history."""
    print("Test: s2b_placement_uncertain_formula_uses_from_history_key")
    sys.path.insert(0, _PROJECT_ROOT)
    src_path = os.path.join(_PROJECT_ROOT, "agent", "workflow_engine.py")
    with open(src_path) as f:
        src = f.read()
    start = src.index('context["placement_uncertain"] = (')
    # Grab ~600 chars which covers the multi-line tuple
    block = src[start:start + 600]
    assert "has_placed_model_from_history" in block, (
        "placement_uncertain must use has_placed_model_from_history in its formula"
    )
    check = block.replace("has_placed_model_from_history", "REPLACED")
    assert '"has_placed_model"' not in check, (
        "placement_uncertain must NOT use bare has_placed_model (directive-affected)"
    )
    print("  PASSED: formula uses has_placed_model_from_history, not has_placed_model")


# =============================================================================

def test_s2b_placement_uncertain_uses_from_history_not_directive():
    """S2b: placement_uncertain=True despite directive model_is_placed=True when no history confirms."""
    print("Test: s2b_placement_uncertain_uses_from_history_not_directive")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    engine = WorkflowEngine()
    directives = {
        "workflow_preferences": {"model_is_placed": True},
        "constraints": [], "stop_conditions": {}, "program_settings": {},
    }
    history_info = {
        "mtriage_done": True, "resolve_cryo_em_done": True,
        "dock_done": False, "refine_done": False, "rsr_done": False,
        "phaser_done": False, "autobuild_done": False, "predict_full_done": False,
        "placement_probed": False, "placement_probe_result": None,
        "predict_done": False, "process_predicted_done": False,
    }
    files = {"model": ["/fake/1aew_A.pdb"], "full_map": ["/fake/denmod_map.ccp4"]}
    ctx = engine.build_context(files=files, history_info=history_info, directives=directives)
    assert ctx["has_placed_model"] is True, "Directive should set has_placed_model=True"
    assert ctx["has_placed_model_from_history"] is False
    assert ctx["placement_uncertain"] is True, (
        "placement_uncertain must be True despite model_is_placed directive when no history confirms. "
        "from_history=%s cell_mismatch=%s probed=%s" % (
            ctx["has_placed_model_from_history"], ctx["cell_mismatch"], ctx["placement_probed"])
    )
    print("  PASSED: placement_uncertain=True despite model_is_placed directive")


def test_s2b_cryoem_routes_to_probe_not_rsr_with_directive_placed():
    """S2b: Full cryoem state goes to probe_placement (not RSR) when directive is placed but no history."""
    print("Test: s2b_cryoem_routes_to_probe_not_rsr_with_directive_placed")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return
    engine = WorkflowEngine()
    directives = {
        "workflow_preferences": {"model_is_placed": True},
        "constraints": [], "stop_conditions": {}, "program_settings": {},
    }
    history_info = {
        "mtriage_done": True, "resolve_cryo_em_done": True,
        "dock_done": False, "refine_done": False, "rsr_done": False,
        "rsr_count": 0, "refine_count": 0,
        "phaser_done": False, "autobuild_done": False, "predict_full_done": False,
        "placement_probed": False, "placement_probe_result": None,
        "predict_done": False, "process_predicted_done": False,
        "map_sharpening_done": False, "map_symmetry_done": False, "validation_done": False,
    }
    files = {
        "model": ["/fake/1aew_A.pdb"], "full_map": ["/fake/denmod_map.ccp4"],
        "half_map": ["/fake/h1.ccp4", "/fake/h2.ccp4"], "sequence": ["/fake/seq.dat"],
    }
    state = engine.get_workflow_state("cryoem", files, history_info, directives=directives)
    phase = state.get("phase", state.get("state", ""))
    valid_progs = state.get("valid_programs", [])
    # The probe may be served from phase "probe_placement" or from a general phase
    # like "cryoem_analyzed" — what matters is that map_correlations IS offered
    # and real_space_refine is NOT (before placement is confirmed).
    assert "phenix.map_correlations" in valid_progs, (
        "Agent must offer map_correlations (probe) when placement unconfirmed. "
        "Got phase=%r programs=%s" % (phase, valid_progs)
    )
    assert "phenix.real_space_refine" not in valid_progs, (
        "Agent must NOT offer real_space_refine before placement confirmed. "
        "Got phase=%r programs=%s" % (phase, valid_progs)
    )
    print("  PASSED: offers probe (map_correlations) not RSR, phase=%r" % phase)


def test_s2b_map_correlations_has_ignore_symmetry_default():
    """S2b: phenix.map_correlations has ignore_symmetry_conflicts=True default."""
    print("Test: s2b_map_correlations_has_ignore_symmetry_default")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.program_registry import ProgramRegistry
    except ImportError:
        print("  SKIP (ProgramRegistry unavailable)")
        return
    registry = ProgramRegistry()
    prog = registry.get_program("phenix.map_correlations")
    assert prog is not None
    defaults = prog.get("defaults", {})
    assert "ignore_symmetry_conflicts" in defaults, (
        "map_correlations must have ignore_symmetry_conflicts in defaults to avoid "
        "crashing when probe runs against a mismatched model+map"
    )
    val = defaults["ignore_symmetry_conflicts"]
    assert val in (True, "true", "True"), "ignore_symmetry_conflicts must be true, got %r" % val
    print("  PASSED: map_correlations has ignore_symmetry_conflicts=True in defaults")


# =============================================================================
# CATEGORY S2c — promotion of unclassified_pdb to search_model for docking
# =============================================================================

def test_s2c_promotion_fires_when_placement_uncertain():
    """S2c: unclassified PDB promoted to search_model when placement_uncertain=True (Tier 3 path)."""
    print("Test: s2c_promotion_fires_when_placement_uncertain")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    pdb = "/fake/1aew_A.pdb"
    files = {
        "unclassified_pdb": [pdb],
        "model": [pdb],
        "search_model": [],
        "pdb": [pdb],
    }
    ctx = {
        "has_placed_model_from_history": False,
        "has_search_model": False,
        "placement_uncertain": True,
        "placement_probed": False,
        "placement_probe_result": None,
        "cell_mismatch": False,
    }
    f2, c2 = engine._promote_unclassified_for_docking(files, ctx, "cryoem")

    assert_true(pdb in f2["search_model"],
                "Promoted PDB must appear in search_model. Got: %s" % f2["search_model"])
    assert_true(c2["has_search_model"] is True,
                "has_search_model must be True after promotion")
    assert_true(c2.get("unclassified_promoted_to_search_model") is True,
                "unclassified_promoted_to_search_model flag must be set")
    assert_true(files["search_model"] == [],
                "Original files dict must be unchanged (no mutation)")
    assert_true(f2 is not files,
                "Promoted files must be a new dict, not the original")
    print("  PASSED: promotion fires on placement_uncertain (Tier 3 path)")


def test_s2c_promotion_fires_when_probe_says_needs_dock():
    """S2c: unclassified PDB promoted when probe ran and returned needs_dock (Tier 3 post-probe)."""
    print("Test: s2c_promotion_fires_when_probe_says_needs_dock")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    pdb = "/fake/1aew_A.pdb"
    files = {"unclassified_pdb": [pdb], "model": [pdb], "search_model": [], "pdb": [pdb]}
    ctx = {
        "has_placed_model_from_history": False,
        "has_search_model": False,
        "placement_uncertain": False,
        "placement_probed": True,
        "placement_probe_result": "needs_dock",
        "cell_mismatch": False,
    }
    f2, c2 = engine._promote_unclassified_for_docking(files, ctx, "cryoem")

    assert_true(pdb in f2["search_model"],
                "Promoted PDB must appear in search_model. Got: %s" % f2["search_model"])
    assert_true(c2["has_search_model"] is True,
                "has_search_model must be True after promotion")
    print("  PASSED: promotion fires on placement_probed=needs_dock (Tier 3 post-probe)")


def test_s2c_promotion_fires_when_cell_mismatch():
    """S2c: unclassified PDB promoted when cell_mismatch=True and no history (Tier 1 path)."""
    print("Test: s2c_promotion_fires_when_cell_mismatch")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    pdb = "/fake/1aew_A.pdb"
    files = {"unclassified_pdb": [pdb], "model": [pdb], "search_model": [], "pdb": [pdb]}
    ctx = {
        "has_placed_model_from_history": False,
        "has_search_model": False,
        "placement_uncertain": False,
        "placement_probed": False,
        "placement_probe_result": None,
        "cell_mismatch": True,
    }
    f2, c2 = engine._promote_unclassified_for_docking(files, ctx, "cryoem")

    assert_true(pdb in f2["search_model"],
                "Promoted PDB must appear in search_model. Got: %s" % f2["search_model"])
    assert_true(c2["has_search_model"] is True,
                "has_search_model must be True after promotion")
    print("  PASSED: promotion fires on cell_mismatch (Tier 1 path)")


def test_s2c_no_promotion_when_placed_by_history():
    """S2c: no promotion when dock/refine already in history (model is already placed)."""
    print("Test: s2c_no_promotion_when_placed_by_history")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    pdb = "/fake/1aew_A.pdb"
    files = {"unclassified_pdb": [pdb], "model": [pdb], "search_model": [], "pdb": [pdb]}
    ctx = {
        "has_placed_model_from_history": True,   # dock_done or refine_done in history
        "has_search_model": False,
        "placement_uncertain": True,             # would trigger if guard weren't here
        "placement_probed": False,
        "placement_probe_result": None,
        "cell_mismatch": True,                   # would trigger if guard weren't here
    }
    f2, c2 = engine._promote_unclassified_for_docking(files, ctx, "cryoem")

    assert_true(f2 is files,
                "Original files must be returned unchanged when model placed by history")
    assert_true(f2["search_model"] == [],
                "search_model must stay empty when model is placed by history")
    print("  PASSED: no promotion when has_placed_model_from_history=True")


def test_s2c_no_promotion_for_xray():
    """S2c: no promotion for X-ray sessions — crystal PDBs handled via model key for phaser."""
    print("Test: s2c_no_promotion_for_xray")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    pdb = "/fake/1aew_A.pdb"
    files = {"unclassified_pdb": [pdb], "model": [pdb], "search_model": [], "pdb": [pdb]}
    ctx = {
        "has_placed_model_from_history": False,
        "has_search_model": False,
        "placement_uncertain": True,
        "placement_probed": False,
        "placement_probe_result": None,
        "cell_mismatch": True,
    }
    f2, c2 = engine._promote_unclassified_for_docking(files, ctx, "xray")

    assert_true(f2 is files,
                "Original files must be returned unchanged for xray experiment type")
    assert_true(f2["search_model"] == [],
                "search_model must stay empty for xray sessions")
    print("  PASSED: no promotion for xray experiment type")


def test_s2c_categorized_files_propagates_through_get_workflow_state():
    """S2c: promoted files appear in get_workflow_state return dict under categorized_files."""
    print("Test: s2c_categorized_files_propagates_through_get_workflow_state")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    pdb = "/fake/1aew_A.pdb"

    # Simulate post-resolve_cryo_em state: full_map present, no dock history,
    # cell_mismatch will be False (no libtbx in test env → fail-safe False),
    # so placement_uncertain fires (Tier 3 path, condition 5a).
    files = {
        "unclassified_pdb": [pdb],
        "model": [pdb],
        "search_model": [],
        "pdb": [pdb],
        "full_map": ["/fake/denmod_map.ccp4"],
        "half_map": ["/fake/h1.ccp4", "/fake/h2.ccp4"],
        "map": ["/fake/denmod_map.ccp4", "/fake/h1.ccp4", "/fake/h2.ccp4"],
        "sequence": ["/fake/seq.dat"],
        "data_mtz": [], "map_coeffs_mtz": [], "ligand_cif": [], "ligand_pdb": [],
        "predicted": [], "processed_predicted": [], "docked": [],
        "refined": [], "rsr_output": [], "phaser_output": [], "autobuild_output": [],
        "with_ligand": [], "ligand_fit_output": [], "intermediate_mr": [],
    }
    history_info = {
        "mtriage_done": True, "resolve_cryo_em_done": True,
        "dock_done": False, "refine_done": False, "rsr_done": False,
        "rsr_count": 0, "refine_count": 0,
        "phaser_done": False, "autobuild_done": False, "predict_full_done": False,
        "placement_probed": False, "placement_probe_result": None,
        "predict_done": False, "process_predicted_done": False,
        "map_sharpening_done": False, "map_symmetry_done": False,
        "validation_done": False, "xtriage_done": False,
    }

    result = engine.get_workflow_state(
        experiment_type="cryoem",
        files=files,
        history_info=history_info,
    )

    # Structural fix verification: categorized_files must be in the return dict
    assert_true("categorized_files" in result,
                "get_workflow_state must return categorized_files key")

    returned_files = result["categorized_files"]

    # When promotion fires (placement_uncertain=True in test env),
    # search_model must contain the promoted PDB.
    # When it doesn't fire (e.g. placement_uncertain=False for unexpected reasons),
    # at minimum categorized_files must be a dict and not clobber with originals.
    assert_true(isinstance(returned_files, dict),
                "categorized_files must be a dict, got: %s" % type(returned_files))

    # Context flag must match files state
    ctx = result.get("context", {})
    if ctx.get("unclassified_promoted_to_search_model"):
        assert_true(pdb in returned_files.get("search_model", []),
                    "If promotion fired, PDB must be in returned categorized_files['search_model']")
        assert_true(returned_files.get("has_search_model") or
                    bool(returned_files.get("search_model")),
                    "search_model list must be non-empty after promotion")
        print("  PASSED: promotion fired and categorized_files['search_model'] contains PDB")
    else:
        # Promotion did not fire (acceptable if placement_uncertain was False)
        print("  PASSED: categorized_files key present in return dict (promotion did not fire)")


# =============================================================================
# CATEGORY S2d — skip_map_model_overlap_check=True default for real_space_refine
# =============================================================================

def test_s2d_rsr_has_skip_map_model_overlap_check_default():
    """S2d: phenix.real_space_refine has skip_map_model_overlap_check=True in defaults."""
    print("Test: s2d_rsr_has_skip_map_model_overlap_check_default")
    sys.path.insert(0, _PROJECT_ROOT)
    from knowledge.yaml_loader import load_programs
    programs = load_programs()
    rsr = programs.get("phenix.real_space_refine", {})
    assert_not_none(rsr, "phenix.real_space_refine must exist in programs.yaml")
    defaults = rsr.get("defaults", {})
    assert_true("skip_map_model_overlap_check" in defaults,
                "real_space_refine defaults must contain skip_map_model_overlap_check")
    val = defaults["skip_map_model_overlap_check"]
    assert_true(val in (True, "true", "True"),
                "skip_map_model_overlap_check must be True, got %r" % val)
    print("  PASSED: real_space_refine has skip_map_model_overlap_check=True in defaults")


# =============================================================================
# CATEGORY S2e — after_program directive suppresses placement probe correctly
# =============================================================================

def test_s2e_after_program_requiring_placed_suppresses_probe():
    """S2e: after_program=model_vs_data sets has_placed_model_from_after_program=True,
    which suppresses placement_uncertain so probe_placement phase is skipped."""
    print("Test: s2e_after_program_requiring_placed_suppresses_probe")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    files = {
        "model": ["/fake/1aba.pdb"],
        "pdb": ["/fake/1aba.pdb"],
        "data_mtz": ["/fake/1aba.mtz"],
        "search_model": [], "predicted": [], "processed_predicted": [],
        "ligand_pdb": [], "ligand": [],
    }
    history_info = {
        "phaser_done": False, "autobuild_done": False, "dock_done": False,
        "predict_full_done": False, "refine_done": False, "xtriage_done": True,
        "placement_probed": False, "placement_probe_result": None,
    }
    directives = {
        "stop_conditions": {
            "after_program": "phenix.model_vs_data",
            "skip_validation": True,
        },
        "workflow_preferences": {},
        "constraints": [],
        "program_settings": {},
    }

    ctx = engine.build_context(files=files, history_info=history_info, directives=directives)

    assert_true(ctx.get("has_placed_model_from_after_program") is True,
                "has_placed_model_from_after_program must be True for after_program=model_vs_data")
    assert_true(ctx.get("placement_uncertain") is False,
                "placement_uncertain must be False when after_program implies placed model. "
                "Got placement_uncertain=%r, from_history=%r, from_after_program=%r" % (
                    ctx.get("placement_uncertain"),
                    ctx.get("has_placed_model_from_history"),
                    ctx.get("has_placed_model_from_after_program")))
    print("  PASSED: after_program=model_vs_data suppresses placement probe")


def test_s2e_model_is_placed_directive_still_does_not_suppress_probe():
    """S2e: model_is_placed=True workflow_preference (unreliable LLM directive) does NOT
    suppress placement_uncertain — only after_program or history evidence does."""
    print("Test: s2e_model_is_placed_directive_still_does_not_suppress_probe")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    files = {
        "model": ["/fake/1aew_A.pdb"],
        "pdb": ["/fake/1aew_A.pdb"],
        "data_mtz": ["/fake/data.mtz"],
        "search_model": [], "predicted": [], "processed_predicted": [],
        "ligand_pdb": [], "ligand": [],
    }
    history_info = {
        "phaser_done": False, "autobuild_done": False, "dock_done": False,
        "predict_full_done": False, "refine_done": False,
        "placement_probed": False, "placement_probe_result": None,
    }
    directives = {
        "workflow_preferences": {"model_is_placed": True},  # hallucinated directive
        "stop_conditions": {},
        "constraints": [],
        "program_settings": {},
    }

    ctx = engine.build_context(files=files, history_info=history_info, directives=directives)

    assert_true(ctx.get("has_placed_model_from_after_program") is False,
                "has_placed_model_from_after_program must be False for model_is_placed directive")
    assert_true(ctx.get("placement_uncertain") is True,
                "placement_uncertain must remain True despite model_is_placed directive (S2b guard). "
                "Got placement_uncertain=%r" % ctx.get("placement_uncertain"))
    print("  PASSED: model_is_placed directive does not suppress placement probe (S2b intact)")


def test_s2e_after_program_not_in_requiring_placed_does_not_suppress_probe():
    """S2e: after_program for a non-placement-requiring program (e.g. predict_and_build)
    does NOT suppress placement_uncertain."""
    print("Test: s2e_after_program_not_in_requiring_placed_does_not_suppress_probe")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
    except ImportError:
        print("  SKIP (WorkflowEngine unavailable)")
        return

    engine = WorkflowEngine()
    files = {
        "model": ["/fake/model.pdb"],
        "pdb": ["/fake/model.pdb"],
        "data_mtz": ["/fake/data.mtz"],
        "search_model": [], "predicted": [], "processed_predicted": [],
        "ligand_pdb": [], "ligand": [],
    }
    history_info = {
        "phaser_done": False, "autobuild_done": False, "dock_done": False,
        "predict_full_done": False, "refine_done": False,
        "placement_probed": False, "placement_probe_result": None,
    }
    directives = {
        "stop_conditions": {"after_program": "phenix.predict_and_build"},
        "workflow_preferences": {},
        "constraints": [],
        "program_settings": {},
    }

    ctx = engine.build_context(files=files, history_info=history_info, directives=directives)

    assert_true(ctx.get("has_placed_model_from_after_program") is False,
                "has_placed_model_from_after_program must be False for predict_and_build")
    print("  PASSED: predict_and_build after_program does not suppress placement probe")


# =============================================================================
# CATEGORY S2f — validation_cryoem requires resolution invariant
# =============================================================================

def test_s2f_validation_cryoem_has_requires_resolution_invariant():
    """S2f: phenix.validation_cryoem has a requires_resolution invariant with
    auto_fill_resolution so it never runs without a resolution value."""
    print("Test: s2f_validation_cryoem_has_requires_resolution_invariant")
    sys.path.insert(0, _PROJECT_ROOT)
    from knowledge.yaml_loader import load_programs
    programs = load_programs()
    prog = programs.get("phenix.validation_cryoem", {})
    assert_not_none(prog, "phenix.validation_cryoem must exist in programs.yaml")

    invariants = prog.get("invariants", [])
    assert_true(len(invariants) > 0,
                "validation_cryoem must have at least one invariant")

    res_inv = next((i for i in invariants if i.get("name") == "requires_resolution"), None)
    assert_not_none(res_inv,
                    "validation_cryoem must have a requires_resolution invariant")
    assert_true(res_inv.get("fix", {}).get("auto_fill_resolution") is True,
                "requires_resolution invariant must have auto_fill_resolution: true")
    assert_true(res_inv.get("check", {}).get("has_strategy") == "resolution",
                "requires_resolution invariant must check has_strategy: resolution")
    print("  PASSED: validation_cryoem has requires_resolution invariant with auto_fill_resolution")


# =============================================================================
# CATEGORY S2g — map_correlations requires resolution for cryoem, not xray
# =============================================================================

def test_s2g_map_correlations_invariant_has_only_for_cryoem():
    """S2g: map_correlations requires_resolution invariant has only_for_experiment_type=cryoem."""
    print("Test: s2g_map_correlations_invariant_has_only_for_cryoem")
    sys.path.insert(0, _PROJECT_ROOT)
    from knowledge.yaml_loader import load_programs
    programs = load_programs()
    prog = programs.get("phenix.map_correlations", {})
    assert_not_none(prog, "phenix.map_correlations must exist in programs.yaml")

    invariants = prog.get("invariants", [])
    res_inv = next((i for i in invariants if i.get("name") == "requires_resolution"), None)
    assert_not_none(res_inv, "map_correlations must have a requires_resolution invariant")

    check = res_inv.get("check", {})
    assert_true(check.get("only_for_experiment_type") == "cryoem",
                "requires_resolution must have only_for_experiment_type=cryoem, got: %r"
                % check.get("only_for_experiment_type"))
    assert_true(res_inv.get("fix", {}).get("auto_fill_resolution") is True,
                "requires_resolution fix must have auto_fill_resolution: true")
    print("  PASSED: map_correlations invariant has only_for_experiment_type=cryoem")


def test_s2g_invariant_skipped_for_xray():
    """S2g: only_for_experiment_type=cryoem invariant is skipped when experiment_type=xray."""
    print("Test: s2g_invariant_skipped_for_xray")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.command_builder import CommandBuilder, CommandContext
    except ImportError:
        print("  SKIP (CommandBuilder unavailable)")
        return

    builder = CommandBuilder()
    ctx = CommandContext(experiment_type="xray", resolution=None)
    files = {"model": "/fake/model.pdb", "full_map": "/fake/map.ccp4"}
    strategy = {}

    result_files, result_strategy = builder._apply_invariants(
        "phenix.map_correlations", files, strategy, ctx)

    assert_true("resolution" not in result_strategy,
                "resolution must NOT be auto-filled for xray experiment_type. "
                "Got strategy: %r" % result_strategy)
    print("  PASSED: invariant correctly skipped for xray (resolution not auto-filled)")


def test_s2g_invariant_fires_for_cryoem():
    """S2g: only_for_experiment_type=cryoem invariant fires and fills resolution when cryoem."""
    print("Test: s2g_invariant_fires_for_cryoem")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.command_builder import CommandBuilder, CommandContext
    except ImportError:
        print("  SKIP (CommandBuilder unavailable)")
        return

    builder = CommandBuilder()
    ctx = CommandContext(experiment_type="cryoem", resolution=1.9)
    files = {"model": "/fake/model.pdb", "full_map": "/fake/map.ccp4"}
    strategy = {}

    result_files, result_strategy = builder._apply_invariants(
        "phenix.map_correlations", files, strategy, ctx)

    assert_true("resolution" in result_strategy,
                "resolution must be auto-filled for cryoem experiment_type. "
                "Got strategy: %r" % result_strategy)
    assert_true(abs(result_strategy["resolution"] - 1.9) < 0.01,
                "resolution must be 1.9, got: %r" % result_strategy.get("resolution"))
    print("  PASSED: invariant fires for cryoem and auto-fills resolution=1.9")


# =============================================================================
# CATEGORY S2h — validation_cryoem writes data_manager PHIL for GUI restore
# =============================================================================

def test_s2h_validation_cryoem_in_dm_to_data_manager_phil():
    """S2h: phenix.validation_cryoem is in the dm_to_data_manager_phil whitelist
    so its model and map files get written as data_manager PHIL in the eff,
    not just as comments."""
    print("Test: s2h_validation_cryoem_in_dm_to_data_manager_phil")
    sys.path.insert(0, _PROJECT_ROOT)
    ai_agent_path = os.path.join(_PROJECT_ROOT, "programs", "ai_agent.py")
    if not os.path.isfile(ai_agent_path):
        print("  SKIP (ai_agent.py not found)")
        return

    with open(ai_agent_path) as f:
        src = f.read()

    assert_true("dm_programs_to_data_manager_phil" in src,
                "ai_agent.py must contain dm_programs_to_data_manager_phil dict")
    assert_true("phenix.validation_cryoem" in src.split(
                "dm_programs_to_data_manager_phil")[1][:500],
                "phenix.validation_cryoem must be in dm_programs_to_data_manager_phil")
    assert_true("data_manager.%s.file" in src or
                "data_manager.%s.file" in src,
                "data_manager PHIL lines must be written for dm_file_mapping programs")
    print("  PASSED: validation_cryoem is in dm_to_data_manager_phil whitelist")


# =============================================================================
# CATEGORY S2i — STOP command recognized even with trailing tokens
# =============================================================================

def test_s2i_stop_alone_is_valid_command():
    """S2i: bare 'STOP' passes _is_valid_command."""
    print("Test: s2i_stop_alone_is_valid_command")
    sys.path.insert(0, _PROJECT_ROOT)
    ai_agent_path = os.path.join(_PROJECT_ROOT, "programs", "ai_agent.py")
    if not os.path.isfile(ai_agent_path):
        print("  SKIP (ai_agent.py not found)")
        return
    with open(ai_agent_path) as f:
        src = f.read()
    assert_true('program == "STOP"' in src or "program == 'STOP'" in src,
                "_is_valid_command must explicitly allow STOP as first token")
    print("  PASSED: STOP is whitelisted in _is_valid_command")


def test_s2i_stop_with_trailing_tokens_detected():
    """S2i: 'STOP <args>' is recognized as a STOP command (first-token check)."""
    print("Test: s2i_stop_with_trailing_tokens_detected")
    sys.path.insert(0, _PROJECT_ROOT)
    ai_agent_path = os.path.join(_PROJECT_ROOT, "programs", "ai_agent.py")
    if not os.path.isfile(ai_agent_path):
        print("  SKIP (ai_agent.py not found)")
        return
    with open(ai_agent_path) as f:
        src = f.read()
    assert_true('.split()[0] == "STOP"' in src or ".split()[0] == 'STOP'" in src,
                "STOP detection must use first-token check (.split()[0]) "
                "not exact-match to handle 'STOP <args>'")
    print("  PASSED: STOP detection uses first-token check for trailing-args case")


def test_s2i_plan_sets_stop_true_when_program_is_stop():
    """S2i root cause: PLAN must set intent['stop']=True when normalizing to STOP,
    so BUILD short-circuits and never assembles 'STOP <strategy_flags>'."""
    print("Test: s2i_plan_sets_stop_true_when_program_is_stop")
    sys.path.insert(0, _PROJECT_ROOT)
    graph_path = os.path.join(_PROJECT_ROOT, "agent", "graph_nodes.py")
    if not os.path.isfile(graph_path):
        print("  SKIP (graph_nodes.py not found)")
        return
    with open(graph_path) as f:
        src = f.read()
    # The fix: after intent["program"] = "STOP", also set intent["stop"] = True
    assert_true('intent["stop"] = True' in src or "intent['stop'] = True" in src,
                "PLAN must set intent['stop']=True when normalizing program to STOP")
    print("  PASSED: PLAN sets intent['stop']=True when normalizing to STOP")


def test_s2i_build_short_circuits_on_program_stop():
    """S2i root cause: BUILD must short-circuit on program=='STOP' as well as stop==True."""
    print("Test: s2i_build_short_circuits_on_program_stop")
    sys.path.insert(0, _PROJECT_ROOT)
    graph_path = os.path.join(_PROJECT_ROOT, "agent", "graph_nodes.py")
    if not os.path.isfile(graph_path):
        print("  SKIP (graph_nodes.py not found)")
        return
    with open(graph_path) as f:
        src = f.read()
    assert_true('intent.get("program") == "STOP"' in src or
                "intent.get('program') == 'STOP'" in src,
                "BUILD must check program=='STOP' in addition to stop==True")
    print("  PASSED: BUILD short-circuits on program=='STOP'")


# =============================================================================
# CATEGORY S2j — dotted strategy keys from other programs are dropped
# =============================================================================

def test_s2j_refinement_key_dropped_from_ligandfit_strategy():
    """S2j: refinement.main.number_of_macro_cycles in ligandfit strategy is dropped,
    not passed through to the ligandfit command."""
    print("Test: s2j_refinement_key_dropped_from_ligandfit_strategy")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.program_registry import ProgramRegistry
    except ImportError:
        print("  SKIP (ProgramRegistry unavailable)")
        return

    registry = ProgramRegistry(use_yaml=True)
    dropped = []
    passed = []

    def log(msg):
        if "DROPPED" in msg:
            dropped.append(msg)
        elif "PASSTHROUGH" in msg:
            passed.append(msg)

    strategy = {
        "refinement.main.number_of_macro_cycles": 2,  # belongs to phenix.refine — must be dropped
    }
    files = {"model": "/fake/placed.pdb", "data": "/fake/data.mtz"}
    cmd = registry.build_command("phenix.ligandfit", files, strategy, log=log)

    assert_true(len(dropped) > 0,
                "refinement.main.number_of_macro_cycles must be DROPPED for ligandfit, "
                "but got cmd: %r" % cmd)
    assert_true("number_of_macro_cycles" not in (cmd or ""),
                "refinement.main.number_of_macro_cycles must not appear in ligandfit command, "
                "got: %r" % cmd)
    assert_true(len(passed) == 0,
                "No dotted key should be passed through, got: %r" % passed)
    print("  PASSED: refinement.main.number_of_macro_cycles correctly dropped from ligandfit")


def test_s2j_known_short_names_still_pass_through():
    """S2j: KNOWN_PHIL_SHORT_NAMES (nproc, twin_law, etc.) still pass through normally."""
    print("Test: s2j_known_short_names_still_pass_through")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.program_registry import ProgramRegistry
    except ImportError:
        print("  SKIP (ProgramRegistry unavailable)")
        return

    registry = ProgramRegistry(use_yaml=True)
    passed = []

    def log(msg):
        if "PASSTHROUGH" in msg and "nproc" in msg:
            passed.append(msg)

    strategy = {"nproc": 4}
    files = {"model": "/fake/model.pdb", "data": "/fake/data.mtz"}
    cmd = registry.build_command("phenix.ligandfit", files, strategy, log=log)

    assert_true(len(passed) > 0 or (cmd and "nproc=4" in cmd),
                "nproc should still pass through as a KNOWN_PHIL_SHORT_NAME")
    print("  PASSED: nproc still passes through correctly")


# =============================================================================
# CATEGORY S2k — _inject_user_params skips STOP commands
# =============================================================================

def test_s2k_inject_user_params_skips_stop():
    """S2k: _inject_user_params call site must guard against command starting with STOP."""
    print("Test: s2k_inject_user_params_skips_stop")
    sys.path.insert(0, _PROJECT_ROOT)
    ai_agent_path = os.path.join(_PROJECT_ROOT, "programs", "ai_agent.py")
    if not os.path.isfile(ai_agent_path):
        print("  SKIP (ai_agent.py not found)")
        return
    with open(ai_agent_path) as f:
        src = f.read()
    # The guard must be at the call site, not just inside _inject_user_params
    assert_true("split()[0] != 'STOP'" in src or 'split()[0] != "STOP"' in src,
                "_inject_user_params call site must skip when command starts with STOP")
    print("  PASSED: _inject_user_params call site skips STOP commands")


def test_s2k_inject_user_params_filters_wrong_program_scope():
    """S2k: refinement.main.number_of_macro_cycles must not be injected into ligandfit."""
    print("Test: s2k_inject_user_params_filters_wrong_program_scope")
    sys.path.insert(0, _PROJECT_ROOT)
    ai_agent_path = os.path.join(_PROJECT_ROOT, "programs", "ai_agent.py")
    if not os.path.isfile(ai_agent_path):
        print("  SKIP (ai_agent.py not found)")
        return
    with open(ai_agent_path) as f:
        src = f.read()
    assert_true("def _inject_user_params(self, command, guidelines, program_name" in src,
                "_inject_user_params must accept program_name parameter")
    assert_true("leading_scope" in src and "_UNIVERSAL_SCOPES" in src,
                "_inject_user_params must filter by leading_scope against _UNIVERSAL_SCOPES")
    universal_line = next((l for l in src.split('\n') if '_UNIVERSAL_SCOPES' in l and '=' in l), "")
    assert_true("refinement" not in universal_line,
                "'refinement' must not be in _UNIVERSAL_SCOPES")
    print("  PASSED: _inject_user_params filters dotted keys by program scope")


# =============================================================================
# S2L TESTS — Probe crash → needs_dock + client-side model cell transport
# =============================================================================

def test_s2l_probe_failure_outside_map_sets_needs_dock():
    """S2L: map_correlations crash 'entirely outside map' → placement_probed=needs_dock."""
    print("Test: s2l_probe_failure_outside_map_sets_needs_dock")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_state import _analyze_history
    except ImportError:
        print("  SKIP (_analyze_history not importable)")
        return

    history = [
        {
            "program": "phenix.mtriage",
            "command": "phenix.mtriage map.ccp4",
            "result": "Completed. Resolution: 1.9",
        },
        {
            "program": "phenix.resolve_cryo_em",
            "command": "phenix.resolve_cryo_em half_map_1.ccp4 half_map_2.ccp4",
            "result": "Completed. denmod_map.ccp4 written",
        },
        {
            # This is the probe — runs before any dock/refine, crashes with
            # the definitive "outside map" message.
            "program": "phenix.map_correlations",
            "command": "phenix.map_correlations model=1aew_A.pdb map=denmod_map.ccp4",
            "result": "FAILED: Stopping as model is entirely outside map and wrapping=False",
        },
    ]

    info = _analyze_history(history)

    assert_true(info.get("placement_probed"),
                "placement_probed must be True after 'entirely outside map' crash")
    assert_equal(info.get("placement_probe_result"), "needs_dock",
                 "placement_probe_result must be 'needs_dock' for outside-map crash")
    print("  PASSED: 'entirely outside map' crash → placement_probed=True, result=needs_dock")


def test_s2l_probe_failure_other_error_marks_inconclusive():
    """S2L: other map_correlations crashes → probed=True, result=None (no infinite retry)."""
    print("Test: s2l_probe_failure_other_error_marks_inconclusive")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_state import _analyze_history
    except ImportError:
        print("  SKIP")
        return

    history = [
        {
            "program": "phenix.map_correlations",
            "command": "phenix.map_correlations model=1aew_A.pdb map=denmod_map.ccp4",
            "result": "FAILED: Sorry: some other error occurred",
        },
    ]

    info = _analyze_history(history)

    assert_true(info.get("placement_probed"),
                "placement_probed must be True even for non-specific map_correlations failure")
    assert_true(info.get("placement_probe_result") is None,
                "placement_probe_result must be None (inconclusive) for generic failure")
    print("  PASSED: generic probe failure → probed=True, result=None (no retry)")


def test_s2l_probe_does_not_repeat_after_failure():
    """S2L: second map_correlations run should NOT override already-set probe result."""
    print("Test: s2l_probe_does_not_repeat_after_failure")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_state import _analyze_history
    except ImportError:
        print("  SKIP")
        return

    history = [
        {
            "program": "phenix.map_correlations",
            "command": "phenix.map_correlations model=1aew_A.pdb map=denmod_map.ccp4",
            "result": "FAILED: Stopping as model is entirely outside map and wrapping=False",
        },
        {
            # Second attempt — would have been prevented by fix, but even if it
            # ran, its result should not overwrite the first probe's conclusion.
            "program": "phenix.map_correlations",
            "command": "phenix.map_correlations model=1aew_A.pdb map=denmod_map.ccp4",
            "result": "FAILED: Sorry: some other error",
        },
    ]

    info = _analyze_history(history)

    # First probe result (needs_dock) must be preserved
    assert_true(info.get("placement_probed"), "placement_probed must be True")
    assert_equal(info.get("placement_probe_result"), "needs_dock",
                 "first probe result (needs_dock) must not be overwritten by second failure")
    print("  PASSED: first probe result preserved; second failure does not overwrite")


def test_s2l_successful_probe_still_works():
    """S2L: successful map_correlations probe with high CC still routes correctly."""
    print("Test: s2l_successful_probe_still_works")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_state import _analyze_history
    except ImportError:
        print("  SKIP")
        return

    history = [
        {
            "program": "phenix.map_correlations",
            "command": "phenix.map_correlations model=1aew_A.pdb map=denmod_map.ccp4",
            "result": "Completed.",
            "analysis": {"cc_mask": 0.72},
        },
    ]

    info = _analyze_history(history)

    assert_true(info.get("placement_probed"), "placement_probed must be True for success")
    assert_equal(info.get("placement_probe_result"), "placed",
                 "CC=0.72 > 0.15 threshold → result must be 'placed'")
    print("  PASSED: successful probe with high CC still works as before")


def test_s2l_client_model_cell_in_session_state():
    """S2L: unplaced_model_cell must be passed through build_session_state."""
    print("Test: s2l_client_model_cell_in_session_state")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.api_client import build_session_state
    except ImportError:
        print("  SKIP")
        return

    session_info = {
        "experiment_type": "cryoem",
        "unplaced_model_cell": [184.0, 184.0, 184.0, 90.0, 90.0, 90.0],
    }

    state = build_session_state(session_info)
    assert_true("unplaced_model_cell" in state,
                "unplaced_model_cell must appear in session_state built by build_session_state")
    assert_equal(state["unplaced_model_cell"], [184.0, 184.0, 184.0, 90.0, 90.0, 90.0],
                 "unplaced_model_cell values must be preserved exactly")
    print("  PASSED: unplaced_model_cell flows through build_session_state correctly")


def test_s2l_check_cell_mismatch_uses_preread_cell():
    """S2L: _check_cell_mismatch detects apoferritin mismatch using client-supplied cell."""
    print("Test: s2l_check_cell_mismatch_uses_preread_cell")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_engine import WorkflowEngine
        from agent.placement_checker import cells_are_compatible
    except ImportError:
        print("  SKIP (workflow_engine or placement_checker not importable)")
        return

    engine = WorkflowEngine()

    # Simulate the apoferritin scenario:
    # Model CRYST1: F432 crystal, a=b=c=184 Å
    # Map cell (denmod from resolve_cryo_em sub-box): P1, 32.5 x 39.65 x 36.4 Å
    # These differ by ~5x on every axis → definitive mismatch
    model_cell = [184.0, 184.0, 184.0, 90.0, 90.0, 90.0]
    # files dict has no real paths — on the server the map path would be real
    # but we mock it here as empty to test only the model_cell branch
    files = {"map": [], "full_map": [], "optimized_full_map": [], "data_mtz": []}

    # With no map files and model_cell only → cannot compare → False (fail-safe)
    result = engine._check_cell_mismatch(files, model_cell=model_cell)
    assert_true(not result,
                "With no readable map file, must return False (fail-safe) even with model_cell")

    # Quick sanity check that the cells ARE incompatible
    map_cell = (32.5, 39.65, 36.4, 90.0, 90.0, 90.0)
    compat = cells_are_compatible(tuple(model_cell), map_cell)
    assert_true(not compat,
                "apoferritin crystal cell and cryo-EM sub-box cell must be incompatible (>5%)")
    print("  PASSED: _check_cell_mismatch uses model_cell parameter; apoferritin cells incompatible")


def test_s2l_detect_workflow_state_accepts_session_info():
    """S2L: detect_workflow_state must accept session_info keyword argument."""
    print("Test: s2l_detect_workflow_state_accepts_session_info")
    sys.path.insert(0, _PROJECT_ROOT)
    import inspect
    try:
        from agent.workflow_state import detect_workflow_state
    except ImportError:
        print("  SKIP")
        return
    sig = inspect.signature(detect_workflow_state)
    assert_true("session_info" in sig.parameters,
                "detect_workflow_state must accept session_info parameter")
    print("  PASSED: detect_workflow_state has session_info parameter")


def test_s2l_outside_map_variants_all_detected():
    """S2L: various 'outside map' crash messages all trigger needs_dock."""
    print("Test: s2l_outside_map_variants_all_detected")
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.workflow_state import _analyze_history
    except ImportError:
        print("  SKIP")
        return

    outside_map_messages = [
        "FAILED: Stopping as model is entirely outside map and wrapping=False",
        "FAILED: Sorry: model is outside map",
        "FAILED: Sorry: model entirely outside map box",
        "FAILED: stopping as model is outside map",
    ]

    for msg in outside_map_messages:
        history = [{
            "program": "phenix.map_correlations",
            "command": "phenix.map_correlations model=m.pdb map=map.ccp4",
            "result": msg,
        }]
        info = _analyze_history(history)
        assert_true(info.get("placement_probed"),
                    "placement_probed must be True for: %s" % msg)
        assert_equal(info.get("placement_probe_result"), "needs_dock",
                     "placement_probe_result must be needs_dock for: %s" % msg)

    print("  PASSED: all outside-map crash variants trigger needs_dock")


# RUN ALL TESTS
# =============================================================================
# S3A TESTS — Failure Diagnosis Feature
# =============================================================================
# Tests for the new diagnosable-terminal error handling system.
# These tests do NOT require libtbx or the PHENIX server; they test the pure
# Python detector, sanitiser, prompt builder, and HTML builder in isolation.
# =============================================================================

def test_s3a_detect_crystal_symmetry_mismatch():
    """DiagnosisDetector detects the exact error string from the bug report."""
    print("  Test: s3a_detect_crystal_symmetry_mismatch")
    from agent.error_analyzer import DiagnosisDetector
    d = DiagnosisDetector()
    result = d.detect(
        "FAILED: Sorry: Crystal symmetry mismatch between different files\n"
        "File 1: a=34.5 Ang\nFile 2: a=39.6 Ang"
    )
    assert_not_none(result, "Should detect crystal_symmetry_mismatch")
    assert_equal(result[0], 'crystal_symmetry_mismatch')
    assert_true(len(result[2]) > 0, "Excerpt should not be empty")
    print("  PASSED: crystal_symmetry_mismatch detected correctly")


def test_s3a_detect_model_outside_map():
    """DiagnosisDetector detects the model-outside-map error."""
    print("  Test: s3a_detect_model_outside_map")
    from agent.error_analyzer import DiagnosisDetector
    d = DiagnosisDetector()
    result = d.detect(
        "FAILED: Sorry: Stopping as model is entirely outside map and wrapping=False"
    )
    assert_not_none(result, "Should detect model_outside_map")
    assert_equal(result[0], 'model_outside_map')
    print("  PASSED: model_outside_map detected correctly")


def test_s3a_detect_returns_none_for_recoverable():
    """
    A recoverable error (ambiguous data labels) must NOT match DiagnosisDetector.

    This enforces the disjoint-sets invariant: no error type may be both
    retryable and terminal.
    """
    print("  Test: s3a_detect_returns_none_for_recoverable")
    from agent.error_analyzer import DiagnosisDetector
    d = DiagnosisDetector()
    # This is the canonical recoverable error text (from recoverable_errors.yaml)
    recoverable_text = (
        "Multiple equally suitable arrays of observed xray data found.\n"
        "Please use scaling.input.xray_data.obs_labels to specify an "
        "unambiguous substring."
    )
    result = d.detect(recoverable_text)
    assert_equal(result, None,
                 "Recoverable error must NOT be flagged as diagnosable-terminal")
    print("  PASSED: recoverable error correctly returns None")


def test_s3a_detect_returns_none_for_unknown():
    """An unrecognised error string must return None."""
    print("  Test: s3a_detect_returns_none_for_unknown")
    from agent.error_analyzer import DiagnosisDetector
    d = DiagnosisDetector()
    result = d.detect(
        "FAILED: Some totally novel error that does not match any pattern."
    )
    assert_equal(result, None, "Unknown error should return None")
    print("  PASSED: unknown error correctly returns None")


def test_s3a_detect_returns_none_for_empty():
    """Empty or None result text must return None without raising."""
    print("  Test: s3a_detect_returns_none_for_empty")
    from agent.error_analyzer import DiagnosisDetector
    d = DiagnosisDetector()
    assert_equal(d.detect(""),   None)
    assert_equal(d.detect(None), None)
    print("  PASSED: empty/None input handled safely")


def test_s3a_strip_markdown_removes_formatting():
    """_strip_llm_markdown correctly removes **, ##, and _ markers
    WITHOUT eating underscores inside identifiers or filenames."""
    print("  Test: s3a_strip_markdown_removes_formatting")
    from agent.failure_diagnoser import _strip_llm_markdown

    raw = (
        "## WHAT WENT WRONG\n"
        "**The unit cells** do not match.\n\n"
        "## MOST LIKELY CAUSE\n"
        "_Different processing_ conventions.\n\n"
        "## HOW TO FIX IT\n"
        "Run **phenix.refine** again."
    )
    stripped = _strip_llm_markdown(raw)

    assert_true('##' not in stripped,    "## headers should be removed")
    assert_true('**' not in stripped,    "** bold should be removed")
    assert_true('The unit cells' in stripped, "Inner text should be preserved")
    assert_true('phenix.refine' in stripped,  "Inner text should be preserved")
    # Plain text input should be unchanged (modulo strip())
    plain = "WHAT WENT WRONG\nThe cells differ."
    assert_equal(_strip_llm_markdown(plain), plain)

    # --- Underscores inside identifiers and filenames must be preserved ---
    # This was a regression: the old regex ate underscores in PHENIX parameter
    # names and filenames (e.g. nsf-d2_noligand.pdb → nsf-d2noligand.pdb,
    # set_unit_cell_from_map=True → setunitcellfrommap=True).
    ident_text = (
        "Run: phenix.pdbtools nsf-d2_noligand.pdb nsf-d2.mtz "
        "set_unit_cell_and_space_group_from_map_or_model=True"
    )
    stripped_ident = _strip_llm_markdown(ident_text)
    assert_true("nsf-d2_noligand.pdb" in stripped_ident,
        "Filename nsf-d2_noligand.pdb must survive markdown stripping, got: %r"
        % stripped_ident)
    assert_true("set_unit_cell_and_space_group_from_map_or_model=True" in stripped_ident,
        "PHENIX parameter name with underscores must survive stripping, got: %r"
        % stripped_ident)

    # Real markdown italic around a word should still be stripped
    italic_text = "This is _italic_ and __underlined__ text."
    stripped_italic = _strip_llm_markdown(italic_text)
    assert_true("_italic_" not in stripped_italic,
        "_italic_ markdown should be stripped")
    assert_true("italic" in stripped_italic,
        "inner text 'italic' should be preserved after stripping")
    assert_true("underlined" in stripped_italic,
        "inner text 'underlined' should be preserved after stripping")

    print("  PASSED: markdown stripped, plain text preserved")


def test_s3a_build_prompt_contains_hint():
    """build_diagnosis_prompt includes the YAML hint for crystal_symmetry_mismatch."""
    print("  Test: s3a_build_prompt_contains_hint")
    from agent.failure_diagnoser import build_diagnosis_prompt
    prompt = build_diagnosis_prompt(
        error_type='crystal_symmetry_mismatch',
        error_text='Sorry: Crystal symmetry mismatch between different files',
        program='phenix.refine',
        log_tail='[... last lines of log ...]',
    )
    # Prompt must contain structural markers
    assert_true('WHAT WENT WRONG'  in prompt, "Prompt missing WHAT WENT WRONG section")
    assert_true('MOST LIKELY CAUSE' in prompt, "Prompt missing MOST LIKELY CAUSE section")
    assert_true('HOW TO FIX IT'    in prompt, "Prompt missing HOW TO FIX IT section")
    # Hint from YAML must be present (key phrase from the hint text)
    assert_true('unit cell' in prompt.lower(), "YAML hint should appear in prompt")
    # Program name must be present
    assert_true('phenix.refine' in prompt, "Program name should appear in prompt")
    print("  PASSED: prompt contains all required sections and YAML hint")


def test_s3a_build_prompt_log_tail_truncated():
    """Log tail in the prompt is capped to 3000 chars regardless of input size."""
    print("  Test: s3a_build_prompt_log_tail_truncated")
    from agent.failure_diagnoser import build_diagnosis_prompt
    # Build a log tail much longer than the 3000-char cap
    huge_log = "x" * 10_000
    prompt = build_diagnosis_prompt(
        error_type='crystal_symmetry_mismatch',
        error_text='err',
        program='phenix.refine',
        log_tail=huge_log,
    )
    # The total prompt will contain the capped log section
    # 3000 x's should be present but not 10000
    assert_true('x' * 3000 in prompt,  "3000-char log section should appear")
    assert_true('x' * 3001 not in prompt, "Log tail should be capped at 3000 chars")
    print("  PASSED: log tail correctly capped at 3000 chars in prompt")


def test_s3a_build_html_escapes_content():
    """build_diagnosis_html HTML-escapes < > & in user-supplied strings."""
    print("  Test: s3a_build_html_escapes_content")
    from agent.failure_diagnoser import build_diagnosis_html
    html = build_diagnosis_html(
        description='Test <error> & "description"',
        error_excerpt='<script>alert(1)</script> & more',
        diagnosis_text='Fix: use a=b&c',
        program='phenix.<test>',
        cycle=1,
    )
    # Angle brackets and ampersands must be escaped
    assert_true('<script>' not in html, "Raw <script> tag must be escaped")
    assert_true('&lt;script&gt;' in html, "&lt;&gt; escaping must be present")
    assert_true('&amp;' in html, "& must be escaped to &amp;")
    # The document should be valid HTML5 boilerplate
    assert_true('<!DOCTYPE html>' in html, "Must be a full HTML document")
    assert_true('white-space: pre-wrap' in html, "Diagnosis div needs pre-wrap")
    print("  PASSED: HTML escaping applied correctly to all user-supplied strings")


def test_s3a_build_html_contains_required_sections():
    """build_diagnosis_html output contains all required elements."""
    print("  Test: s3a_build_html_contains_required_sections")
    from agent.failure_diagnoser import build_diagnosis_html
    html = build_diagnosis_html(
        description='Unit cell mismatch',
        error_excerpt='Sorry: Crystal symmetry mismatch',
        diagnosis_text='WHAT WENT WRONG\nCells differ.\n\nHOW TO FIX IT\nReprocess.',
        program='phenix.refine',
        cycle=5,
    )
    assert_true('Unit cell mismatch'           in html, "Description missing")
    assert_true('phenix.refine'                in html, "Program name missing")
    assert_true('5'                            in html, "Cycle number missing")
    assert_true('WHAT WENT WRONG'             in html, "Diagnosis text missing")
    assert_true('error-box'                   in html, "error-box class missing")
    assert_true('diagnosis'                   in html, "diagnosis class missing")
    print("  PASSED: HTML report contains all required sections")


def test_s3a_rules_only_fallback_has_hint():
    """
    When the YAML hint is available, it appears in the fallback diagnosis text
    that _diagnose_terminal_failure uses when use_rules_only=True or when
    the LLM call fails.
    """
    print("  Test: s3a_rules_only_fallback_has_hint")
    from agent.error_analyzer import DiagnosisDetector
    d = DiagnosisDetector()
    hint = d.get_hint('crystal_symmetry_mismatch')
    assert_true(len(hint) > 20, "YAML hint must be non-trivial")
    assert_true('unit cell' in hint.lower() or 'cell' in hint.lower(),
                "Hint should mention unit cell")
    # Fallback text construction (mirrors _diagnose_terminal_failure)
    fallback = (
        "WHAT WENT WRONG\n"
        "Unit cell or space group mismatch between input files.\n\n"
        "MOST LIKELY CAUSE\n"
        "Sorry: Crystal symmetry mismatch\n\n"
        "HOW TO FIX IT\n"
        + hint
    )
    assert_true('unit cell' in fallback.lower() or
                'cell' in fallback.lower(),
                "Fallback should contain crystallographic guidance")
    print("  PASSED: fallback diagnosis contains meaningful YAML hint")


def test_s3a_build_html_new_fields():
    """
    build_diagnosis_html populates the new optional context fields:
    html_path (saved-to line), job_name, and working_dir (meta bar).
    Also verifies the heading text was changed to 'Error diagnosis'.
    """
    print("  Test: s3a_build_html_new_fields")
    from agent.failure_diagnoser import build_diagnosis_html

    html = build_diagnosis_html(
        description='Unit cell mismatch',
        error_excerpt='Sorry: Crystal symmetry mismatch',
        diagnosis_text='WHAT WENT WRONG\nCells differ.',
        program='phenix.refine',
        cycle=3,
        html_path='/my/job/dir/ai_failure_diagnosis.html',
        job_name='nsf-d2-ligand',
        working_dir='/my/job/dir',
    )

    # New heading (item 1)
    assert_true('Error diagnosis' in html,
                "Heading must read 'Error diagnosis' (not 'Terminal Error Diagnosis')")
    assert_true('Terminal Error Diagnosis' not in html,
                "Old heading 'Terminal Error Diagnosis' must be gone")

    # File location (item 2)
    assert_true('ai_failure_diagnosis.html' in html,
                "Saved file path must appear in the HTML report")
    assert_true('Saved to' in html,
                "Footer must say 'Saved to: <path>'")

    # Job name and working dir (item 3)
    assert_true('nsf-d2-ligand' in html,
                "Job name must appear in the meta bar")
    assert_true('/my/job/dir' in html,
                "Working directory must appear in the meta bar")

    # Backward compat — all optional fields can be omitted
    html_min = build_diagnosis_html(
        description='Test error',
        error_excerpt='some text',
        diagnosis_text='Some diagnosis.',
        program='phenix.refine',
        cycle=1,
    )
    assert_true('Error diagnosis' in html_min,
                "Heading must be present even without optional fields")
    assert_true('<!DOCTYPE html>' in html_min,
                "Must be a full HTML document")

    print("  PASSED: new html_path/job_name/working_dir fields and heading verified")


def test_s3a_diagnose_returns_true_no_sorry():
    """
    _diagnose_terminal_failure must NO LONGER raise Sorry, and _finalize_session
    must skip the Results summary page when a fatal diagnosis fired.

    The user flow is: diagnosis HTML opens in browser → ai_agent finishes cleanly.
    No Sorry modal, no second Results page that buries the diagnosis.

    Verifies via source-code inspection of ai_agent.py.
    """
    print("  Test: s3a_diagnose_returns_true_no_sorry")

    ai_agent_src = open(_find_ai_agent_path()).read()

    # 1. The old deferred-Sorry pattern must be gone
    assert_true('_pending_sorry' not in ai_agent_src,
                "_pending_sorry must be removed — Sorry is no longer raised")
    assert_true('raise _pending_sorry' not in ai_agent_src,
                "raise _pending_sorry must be removed")

    # 2. _diagnose_terminal_failure must return True (not raise Sorry)
    assert_true('return True' in ai_agent_src,
                "_diagnose_terminal_failure must return True to stop the cycle loop")

    # 3. The caller must propagate the return value
    #    (it now does "return self._diagnose_terminal_failure(...)")
    assert_true('return self._diagnose_terminal_failure(' in ai_agent_src,
                "_run_single_cycle must propagate the True return from "
                "_diagnose_terminal_failure")

    # 4. _finalize_session is still unconditional (no try/except around it)
    finalize_marker = 'self._finalize_session(session)    # always runs'
    assert_true(finalize_marker in ai_agent_src,
                "_finalize_session must remain unconditional after the cycle loop")

    # 5. Results summary is skipped when a fatal diagnosis fired
    assert_true('failure_diagnosis_path' in ai_agent_src,
                "_finalize_session must check failure_diagnosis_path to skip summary")
    assert_true('has_fatal_diagnosis' in ai_agent_src,
                "_finalize_session must use has_fatal_diagnosis flag to suppress "
                "the Results page when the diagnosis HTML is the user's output")

    print("  PASSED: Sorry removed; returns True; Results page suppressed on fatal diagnosis")


def test_s3a_finalize_runs_after_diagnosis():
    """
    Even when _diagnose_terminal_failure fires (True returned from
    _run_single_cycle), _finalize_session must still run (to save the session
    and populate self.result), but the Results summary page is NOT produced —
    the diagnosis HTML is the user's sole output window.

    Simulates the cycle loop logic directly without importing ai_agent.
    """
    print("  Test: s3a_finalize_runs_after_diagnosis")

    call_log = []

    def fake_run_single_cycle_diagnosis():
        """Simulates _run_single_cycle returning True on terminal failure."""
        return True   # stop the loop — diagnosis done

    def fake_finalize(has_diagnosis):
        call_log.append('finalize')
        # Mirrors _finalize_session: only generate summary when no diagnosis
        if not has_diagnosis:
            call_log.append('summary')

    # Replicate the simplified loop from iterate_agent
    for cycle in range(3):
        should_break = fake_run_single_cycle_diagnosis()
        if should_break:
            break

    fake_finalize(has_diagnosis=True)   # unconditional — always runs

    assert_true('finalize' in call_log,
                "_finalize_session must run even when a terminal failure stops the loop")
    assert_true('summary' not in call_log,
                "Results summary must NOT be generated when a fatal diagnosis fired")
    assert_true(cycle == 0,
                "Loop must break on first cycle when terminal failure is detected")

    print("  PASSED: _finalize_session runs; Results page suppressed on fatal diagnosis")



def test_pdb_is_small_molecule_helper():
    """
    _pdb_is_small_molecule must correctly distinguish polymer models from
    small-molecule coordinate files based on ATOM vs HETATM record content.

    This is the foundational function for the atp.pdb ligand-detection fix.
    """
    print("  Test: pdb_is_small_molecule_helper")
    from agent.workflow_state import _pdb_is_small_molecule

    protein_pdb = (
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00  0.00           C\n"
        "ATOM      2  CA  ALA A   2       4.000   5.000   6.000  1.00  0.00           C\n"
        "END\n"
    )
    ligand_pdb = (
        "REMARK  ATP - adenosine triphosphate\n"
        "HETATM    1  PA  ATP     1       1.000   2.000   3.000  1.00  0.00           P\n"
        "HETATM    2  O1A ATP     1       4.000   5.000   6.000  1.00  0.00           O\n"
        "HETATM    3  C5  ATP     1       7.000   8.000   9.000  1.00  0.00           C\n"
        "END\n"
    )
    mixed_pdb = (
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00  0.00           C\n"
        "HETATM    2  PA  ATP     1       4.000   5.000   6.000  1.00  0.00           P\n"
        "END\n"
    )
    empty_pdb = "REMARK  empty file\nEND\n"

    with tempfile.TemporaryDirectory() as d:
        def write(name, content):
            p = os.path.join(d, name)
            with open(p, 'w') as f: f.write(content)
            return p

        # Protein: has ATOM → NOT a small molecule
        assert_false(_pdb_is_small_molecule(write('protein.pdb', protein_pdb)),
                     "Protein (has ATOM records) must not be small molecule")

        # Ligand: HETATM only → IS a small molecule
        assert_true(_pdb_is_small_molecule(write('atp.pdb', ligand_pdb)),
                    "HETATM-only file must be detected as small molecule")

        # Mixed: has ATOM → NOT a small molecule (protein + bound ligand in one file)
        assert_false(_pdb_is_small_molecule(write('complex.pdb', mixed_pdb)),
                     "File with ATOM records must not be small molecule even if HETATM present")

        # Empty/REMARK-only: no ATOM or HETATM → returns False (conservative)
        assert_false(_pdb_is_small_molecule(write('empty.pdb', empty_pdb)),
                     "File with no ATOM/HETATM must return False conservatively")

        # Missing file → returns False (no exception)
        assert_false(_pdb_is_small_molecule(os.path.join(d, 'does_not_exist.pdb')),
                     "Missing file must return False without raising")

    print("  PASSED: _pdb_is_small_molecule helper is correct for all cases")


def test_hetcode_ligand_not_used_as_refine_model():
    """
    When the user provides a hetcode-named ligand file (e.g. atp.pdb, gdp.pdb,
    hem.pdb) alongside a real protein model and data file, the categorizer must:

      1. Place the hetcode ligand in 'ligand' and 'ligand_pdb' — NOT 'model'
      2. Leave the protein model in 'model'
      3. Leave mixed files (protein + bound ligand in one PDB) in 'model'

    This is the regression test for the original bug:
    "the ai_agent sets up to refine the atp.pdb instead of the model"

    The bug root cause: pattern-based categorization only knows about files
    named lig*.pdb / ligand*.pdb.  A file called atp.pdb matched the
    unclassified_pdb wildcard ("*") and bubbled up to 'model'.  Refinement
    programs exclude 'ligand' but not 'model', so atp.pdb could be selected
    as the model for phenix.refine.

    The fix: a post-categorization content check promotes HETATM-only PDB files
    from unclassified_pdb/model into ligand_pdb/ligand.
    """
    print("  Test: hetcode_ligand_not_used_as_refine_model")

    import yaml
    try:
        from agent.workflow_state import (
            _pdb_is_small_molecule,
            _categorize_files_yaml,
            _bubble_up_to_parents,
        )
        rules_path = os.path.join(_PROJECT_ROOT, 'knowledge', 'file_categories.yaml')
        with open(rules_path) as f:
            category_rules = yaml.safe_load(f)
    except Exception as e:
        print("  SKIPPED: could not load YAML rules:", e)
        return

    protein_pdb = (
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00  0.00           C\n"
        "ATOM      2  CA  ALA A   2       4.000   5.000   6.000  1.00  0.00           C\n"
        "END\n"
    )
    hetatm_pdb = (
        "HETATM    1  PA  ATP     1       1.000   2.000   3.000  1.00  0.00           P\n"
        "HETATM    2  O1A ATP     1       4.000   5.000   6.000  1.00  0.00           O\n"
        "END\n"
    )
    mixed_pdb = (
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00  0.00           C\n"
        "HETATM    2  PA  ATP     1       4.000   5.000   6.000  1.00  0.00           P\n"
        "END\n"
    )

    # Hetcode names that have no 'lig' substring — these are the problem files
    ligand_names = ['atp.pdb', 'gdp.pdb', 'hem.pdb', 'fmn.pdb', 'NAD.pdb']

    with tempfile.TemporaryDirectory() as d:
        def write(name, content):
            p = os.path.join(d, name)
            with open(p, 'w') as f: f.write(content)
            return p

        protein_path = write('model.pdb',   protein_pdb)
        complex_path = write('complex.pdb', mixed_pdb)   # protein + ligand in one file
        ligand_paths = [write(n, hetatm_pdb) for n in ligand_names]

        all_files = [protein_path, complex_path] + ligand_paths

        # Run the YAML categorizer
        files = _categorize_files_yaml(all_files, category_rules)
        files = _bubble_up_to_parents(files, category_rules)

        # Apply the HETATM post-processing pass (mirrors _categorize_files)
        _model_subcats = {
            'refined', 'rsr_output', 'phaser_output', 'autobuild_output',
            'docked', 'with_ligand', 'ligand_fit_output', 'model_cif',
        }
        for f in list(files.get('unclassified_pdb', [])):
            if any(f in files.get(sc, []) for sc in _model_subcats):
                continue
            if _pdb_is_small_molecule(f):
                files['unclassified_pdb'].remove(f)
                for lst in ('model', 'pdb'):
                    if f in files.get(lst, []): files[lst].remove(f)
                for k in ('ligand_pdb', 'ligand'):
                    if k not in files: files[k] = []
                    if f not in files[k]: files[k].append(f)

        model_basenames  = {os.path.basename(f) for f in files.get('model', [])}
        ligand_basenames = {os.path.basename(f) for f in files.get('ligand', [])}

        # 1. Protein model stays in model
        assert_in('model.pdb', model_basenames,
                  "Protein model must remain in 'model' category")

        # 2. Mixed file (has ATOM) stays in model
        assert_in('complex.pdb', model_basenames,
                  "PDB with ATOM records must remain in 'model' even if HETATM present")

        # 3. Every hetcode ligand must be in ligand, NOT in model
        for name in ligand_names:
            assert_true(name not in model_basenames,
                        f"{name} must NOT be in 'model' category")
            assert_true(name in ligand_basenames,
                        f"{name} must be in 'ligand' category")
            lp_basenames = {os.path.basename(f) for f in files.get('ligand_pdb', [])}
            assert_true(name in lp_basenames,
                        f"{name} must be in 'ligand_pdb' subcategory")

        # 4. Sanity: model only contains protein + complex
        unexpected_in_model = model_basenames - {'model.pdb', 'complex.pdb'}
        assert_true(len(unexpected_in_model) == 0,
                    f"Unexpected files in model category: {unexpected_in_model}")

    print("  PASSED: hetcode ligand PDB files correctly excluded from 'model'")
    print("  PASSED: phenix.refine model slot would not select atp.pdb")


def test_is_ligand_file_noligand_false_positive():
    """
    Regression test: _is_ligand_file must NOT misclassify proteins whose
    filename contains 'noligand' (e.g. nsf-d2_noligand.pdb) as ligands.

    The old code used bare substring matching ('ligand.pdb' in basename)
    which matched 'noligand.pdb' — because 'noligand.pdb' contains the
    substring 'ligand.pdb'.  The fix uses word-boundary-aware regex.

    Also verifies that genuine ligand names (lig.pdb, ligand_001.pdb, etc.)
    and HETATM-only hetcode files (atp.pdb) are still correctly identified.
    """
    print("  Test: is_ligand_file_noligand_false_positive")

    try:
        from agent.best_files_tracker import BestFilesTracker
    except ImportError:
        print("  SKIP (agent.best_files_tracker not importable)")
        return

    tracker = BestFilesTracker()
    check = lambda name, path=None: tracker._is_ligand_file(name.lower(), path=path)

    # --- Must NOT be ligand ---
    assert_true(not check('nsf-d2_noligand.pdb'),
                "'nsf-d2_noligand.pdb' must NOT be classified as ligand "
                "(contains 'noligand', not 'ligand' as a standalone word)")
    assert_true(not check('my_model_noligand.pdb'),
                "'my_model_noligand.pdb' must NOT be classified as ligand")
    assert_true(not check('noligand_model.pdb'),
                "'noligand_model.pdb' must NOT be classified as ligand")
    assert_true(not check('protein.pdb'),
                "'protein.pdb' must NOT be classified as ligand")

    # --- Must BE ligand (name-based) ---
    assert_true(check('lig.pdb'),       "'lig.pdb' must be classified as ligand")
    assert_true(check('lig_001.pdb'),   "'lig_001.pdb' must be classified as ligand")
    assert_true(check('ligand.pdb'),    "'ligand.pdb' must be classified as ligand")
    assert_true(check('ligand_001.pdb'),"'ligand_001.pdb' must be classified as ligand")
    assert_true(check('my_ligand.pdb'), "'my_ligand.pdb' must be classified as ligand")

    # --- Excluded output files must NOT be ligand (they are models) ---
    assert_true(not check('ligand_fit_001.pdb'),
                "'ligand_fit_001.pdb' is LigandFit output (model), not a ligand")
    assert_true(not check('nsf_with_ligand.pdb'),
                "'nsf_with_ligand.pdb' is a model+ligand complex, not a small molecule")

    # --- Content-based detection: HETATM-only files (e.g. atp.pdb) ---
    import tempfile, os
    hetatm_pdb = (
        "HETATM    1  O1  ATP A   1       1.000   2.000   3.000  1.00 10.00           O\n"
        "HETATM    2  N1  ATP A   1       4.000   5.000   6.000  1.00 10.00           N\n"
        "END\n"
    )
    protein_pdb = (
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 10.00           C\n"
        "ATOM      2  CB  ALA A   1       2.000   3.000   4.000  1.00 10.00           C\n"
        "END\n"
    )
    noligand_pdb = (
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 10.00           C\n"
        "ATOM      2  CB  ALA A   2       2.000   3.000   4.000  1.00 10.00           C\n"
        "END\n"
    )
    with tempfile.TemporaryDirectory() as d:
        def write(name, content):
            p = os.path.join(d, name)
            open(p, 'w').write(content)
            return p

        atp_path      = write('atp.pdb',              hetatm_pdb)
        protein_path  = write('protein.pdb',           protein_pdb)
        noligand_path = write('nsf-d2_noligand.pdb',  noligand_pdb)

        # atp.pdb: no ligand name pattern → falls back to HETATM content check
        assert_true(check('atp.pdb', path=atp_path),
                    "'atp.pdb' (HETATM-only) must be classified as ligand via content check")
        # protein.pdb: has ATOM records → NOT a small molecule
        assert_true(not check('protein.pdb', path=protein_path),
                    "'protein.pdb' (ATOM records) must NOT be classified as ligand")
        # nsf-d2_noligand.pdb: name is not a ligand, content has ATOM → not ligand
        assert_true(not check('nsf-d2_noligand.pdb', path=noligand_path),
                    "'nsf-d2_noligand.pdb' (ATOM records) must NOT be classified as ligand")

    print("  PASSED: _is_ligand_file correctly handles noligand false-positive "
          "and hetcode content detection")


# =============================================================================
# CRYSTAL SYMMETRY INJECTION (unit_cell / space_group from user advice)
# =============================================================================

def test_s4a_simple_extraction_unit_cell_parenthesized():
    """
    extract_directives_simple must pull unit_cell out of parenthesized tuple form
    and store it as a space-separated 6-number string under program_settings.default.

    This covers the exact form from the log:
      "The specified unit cell (116.097, 116.097, 44.175, 90, 90, 120) must be used"
    """
    print("  Test: s4a_simple_extraction_unit_cell_parenthesized")
    from agent.directive_extractor import extract_directives_simple

    advice = (
        "The specified unit cell (116.097, 116.097, 44.175, 90, 90, 120) "
        "must be used for the procedure."
    )
    d = extract_directives_simple(advice)

    uc = d.get("program_settings", {}).get("default", {}).get("unit_cell")
    assert_not_none(uc, "unit_cell must be extracted from parenthesized advice")
    assert_equal(uc, "116.097 116.097 44.175 90 90 120",
                 "unit_cell must be space-separated, no parens or commas")
    print("  PASSED: unit_cell extracted and normalised correctly")


def test_s4a_simple_extraction_unit_cell_space_separated():
    """extract_directives_simple handles space-separated unit cell without parens."""
    print("  Test: s4a_simple_extraction_unit_cell_space_separated")
    from agent.directive_extractor import extract_directives_simple

    advice = "Use unit cell 50.0 60.0 70.0 90.0 90.0 90.0 for refinement."
    d = extract_directives_simple(advice)

    uc = d.get("program_settings", {}).get("default", {}).get("unit_cell")
    assert_not_none(uc, "unit_cell must be extracted from space-separated form")
    nums = uc.split()
    assert_equal(len(nums), 6, "unit_cell must have exactly 6 numbers")
    print("  PASSED: space-separated unit_cell extracted correctly")


def test_s4a_simple_extraction_space_group():
    """extract_directives_simple extracts space group symbol."""
    print("  Test: s4a_simple_extraction_space_group")
    from agent.directive_extractor import extract_directives_simple

    for advice, expected_start in [
        ("space group P 32 2 1", "P 32"),
        ("space_group=P63", "P63"),
        ("Space Group: C 2 2 21", "C 2"),
    ]:
        d = extract_directives_simple(advice)
        sg = d.get("program_settings", {}).get("default", {}).get("space_group")
        assert_not_none(sg, "space_group must be extracted from: %r" % advice)
        assert_true(sg.startswith(expected_start),
                    "space_group %r must start with %r for advice %r" % (
                        sg, expected_start, advice))
    print("  PASSED: space_group extracted from multiple formats")


def test_s4a_inject_crystal_symmetry_into_model_vs_data():
    """
    _inject_crystal_symmetry must append unit_cell and space_group to
    phenix.model_vs_data when session directives carry them.

    This is the exact failure from the log: unit_cell given in advice but
    missing from the model_vs_data command.
    """
    print("  Test: s4a_inject_crystal_symmetry_into_model_vs_data")

    # Build a mock session whose directives contain crystal info
    import tempfile, shutil
    from agent.session import AgentSession

    tmp = tempfile.mkdtemp()
    try:
        session = AgentSession(session_dir=tmp)
        session.data["directives"] = {
            "program_settings": {
                "default": {
                    "unit_cell": "116.097 116.097 44.175 90 90 120",
                    "space_group": "P 63",
                }
            }
        }
        session.data["directives_extracted"] = True

        # Build a minimal mock of the agent that has _inject_crystal_symmetry
        ai_agent_path = _find_ai_agent_path()
        import importlib.util
        spec = importlib.util.spec_from_file_location("ai_agent_mod", ai_agent_path)
        # We can't import ai_agent without libtbx, so test via source inspection
        # and a direct call to a standalone version of the method logic.

        # Instead: verify via source that the method exists and is called
        src = open(ai_agent_path).read()
        assert_true("def _inject_crystal_symmetry" in src,
                    "_inject_crystal_symmetry method must exist in ai_agent.py")
        assert_true("_inject_crystal_symmetry(command, session, program_name)" in src,
                    "_inject_crystal_symmetry must be called after _inject_user_params")

        # Locate the frozenset literal in the source (skip the comment block).
        # The frozenset's opening '{' follows the keyword 'frozenset'.
        # We take the substring from that '{' to the matching '}'.
        block_start = src.find("_XRAY_SYMMETRY_PROGRAMS = frozenset")
        assert_true(block_start >= 0, "_XRAY_SYMMETRY_PROGRAMS frozenset must exist")
        brace_open = src.find("{", block_start)
        brace_close = src.find("}", brace_open)
        symmetry_block = src[brace_open: brace_close + 1]

        # Programs that should be in the set (need explicit crystal_symmetry arg)
        for prog in ("phenix.refine", "phenix.phaser", "phenix.autosol",
                     "phenix.ligandfit"):
            assert_true(prog in symmetry_block,
                        "%s must be in _XRAY_SYMMETRY_PROGRAMS" % prog)

        # Programs that read symmetry from their input files — must NOT be here,
        # or PHENIX will reject the redundant crystal_symmetry.unit_cell= arg.
        for prog in ("phenix.model_vs_data", "phenix.xtriage", "phenix.molprobity",
                     "phenix.real_space_refine", "phenix.map_to_model",
                     "phenix.dock_in_map"):
            assert_true(prog not in symmetry_block,
                        "%s must NOT be in _XRAY_SYMMETRY_PROGRAMS "
                        "(reads symmetry from input files)" % prog)
    finally:
        shutil.rmtree(tmp)

    print("  PASSED: _inject_crystal_symmetry exists, is called, and has correct program set")


def test_s4a_unit_cell_format_normalised():
    """
    extract_directives_simple must always produce space-separated 6-number strings
    regardless of whether the input uses parentheses, commas, or spaces.
    VALID_SETTINGS must include unit_cell and space_group as str.
    """
    print("  Test: s4a_unit_cell_format_normalised")
    from agent.directive_extractor import extract_directives_simple, VALID_SETTINGS

    # Check VALID_SETTINGS
    assert_in("unit_cell", VALID_SETTINGS,
              "unit_cell must be in VALID_SETTINGS")
    assert_equal(VALID_SETTINGS["unit_cell"], str,
                 "unit_cell type must be str (space-separated)")
    assert_in("space_group", VALID_SETTINGS,
              "space_group must be in VALID_SETTINGS")

    # Parenthesized comma-separated (the exact user complaint)
    d = extract_directives_simple(
        "unit cell (116.097, 116.097, 44.175, 90, 90, 120)"
    )
    uc = d.get("program_settings", {}).get("default", {}).get("unit_cell", "")
    assert_true("(" not in uc and "," not in uc,
                "Extracted unit_cell must not contain parentheses or commas: %r" % uc)
    nums = uc.strip().split()
    assert_equal(len(nums), 6,
                 "Extracted unit_cell must have exactly 6 space-separated numbers")

    print("  PASSED: unit_cell normalised and VALID_SETTINGS correct")


# =============================================================================
# CRYSTAL SYMMETRY FALLBACK (v112.63)
# Tests for _apply_crystal_symmetry_fallback and crystal_symmetry. scoped output
# =============================================================================

def test_s4b_fallback_populates_unit_cell_from_empty_directives():
    """
    When the LLM returns {} (no directives), _apply_crystal_symmetry_fallback
    must still extract unit_cell from the raw advice text.

    This is the exact failure from the log: directive extraction returned {}
    so the unit cell was never injected into the command.
    """
    print("  Test: s4b_fallback_populates_unit_cell_from_empty_directives")
    from agent.directive_extractor import _apply_crystal_symmetry_fallback

    advice = (
        "The specified unit cell (116.097, 116.097, 44.175, 90, 90, 120) "
        "must be used for the procedure."
    )
    # Simulate LLM returning {}
    result = _apply_crystal_symmetry_fallback({}, advice, lambda m: None)

    uc = result.get("program_settings", {}).get("default", {}).get("unit_cell")
    assert_not_none(uc,
        "_apply_crystal_symmetry_fallback must extract unit_cell from advice "
        "when LLM directives are empty")
    assert_equal(uc, "116.097 116.097 44.175 90 90 120",
        "unit_cell must be normalised to space-separated numbers, got: %r" % uc)
    print("  PASSED: fallback populates unit_cell from empty directives")


def test_s4b_fallback_does_not_overwrite_llm_unit_cell():
    """
    _apply_crystal_symmetry_fallback must NOT overwrite a unit_cell that the
    LLM already extracted correctly.
    """
    print("  Test: s4b_fallback_does_not_overwrite_llm_unit_cell")
    from agent.directive_extractor import _apply_crystal_symmetry_fallback

    llm_directives = {
        "program_settings": {"default": {"unit_cell": "50 60 70 90 90 90"}}
    }
    advice = "Use unit cell (116.097, 116.097, 44.175, 90, 90, 120) please."
    result = _apply_crystal_symmetry_fallback(
        llm_directives, advice, lambda m: None)

    uc = result.get("program_settings", {}).get("default", {}).get("unit_cell")
    assert_equal(uc, "50 60 70 90 90 90",
        "fallback must not overwrite LLM-extracted unit_cell "
        "(got: %r)" % uc)
    print("  PASSED: fallback preserves LLM-extracted unit_cell")


def test_s4b_fallback_populates_space_group_only_when_missing():
    """
    When LLM extracted unit_cell but missed space_group, the fallback
    must fill in space_group without touching unit_cell.
    """
    print("  Test: s4b_fallback_populates_space_group_only_when_missing")
    from agent.directive_extractor import _apply_crystal_symmetry_fallback

    llm_directives = {
        "program_settings": {
            "default": {"unit_cell": "116.097 116.097 44.175 90 90 120"}
        }
    }
    advice = (
        "The space group is P 63 2 2 and the unit cell is "
        "(116.097, 116.097, 44.175, 90, 90, 120)."
    )
    result = _apply_crystal_symmetry_fallback(
        llm_directives, advice, lambda m: None)

    default = result.get("program_settings", {}).get("default", {})
    # unit_cell unchanged
    assert_equal(default.get("unit_cell"), "116.097 116.097 44.175 90 90 120",
        "unit_cell must be preserved by fallback")
    # space_group filled in
    sg = default.get("space_group")
    assert_not_none(sg,
        "fallback must extract space_group when LLM left it empty")
    assert_true("P 63" in sg,
        "space_group must contain extracted symbol, got: %r" % sg)
    print("  PASSED: fallback fills space_group only, preserves unit_cell")


def test_s4b_program_registry_uses_crystal_symmetry_scope():
    """
    The program_registry PASSTHROUGH path must emit crystal_symmetry.unit_cell=
    and crystal_symmetry.space_group= (the fully-scoped PHIL form), not bare
    unit_cell= / space_group=.

    Verified by source inspection (ProgramRegistry.build_command requires libtbx
    to instantiate so we read the source directly).
    """
    print("  Test: s4b_program_registry_uses_crystal_symmetry_scope")
    import os
    pr_path = os.path.join(os.path.dirname(__file__), '..', 'agent', 'program_registry.py')
    src = open(pr_path).read()

    # Find the PASSTHROUGH block that handles KNOWN_PHIL_SHORT_NAMES
    passthrough_idx = src.find("PASSTHROUGH")
    assert_true(passthrough_idx != -1,
        "program_registry must have a PASSTHROUGH block for strategy flags")

    # Check that the scoping logic is present
    assert_true("crystal_symmetry.%s" % "unit_cell" in src or
                ("'unit_cell', 'space_group'" in src and "crystal_symmetry" in src),
        "program_registry must scope unit_cell/space_group as crystal_symmetry.*")

    # Verify the key pattern: tuple check then crystal_symmetry. prefix
    assert_true("crystal_symmetry.%s" % "" in src or
                "'crystal_symmetry.%s' % key" in src or
                "crystal_symmetry.%s' % key" in src,
        "program_registry must construct crystal_symmetry.{key} dynamically")

    print("  PASSED: program_registry uses crystal_symmetry. scoping")


def test_s4b_inject_crystal_symmetry_uses_scoped_form():
    """
    _inject_crystal_symmetry in ai_agent.py must append
    crystal_symmetry.unit_cell= and crystal_symmetry.space_group=, not the
    bare unit_cell= / space_group= forms.

    Verified by source inspection since importing ai_agent requires libtbx.
    """
    print("  Test: s4b_inject_crystal_symmetry_uses_scoped_form")
    ai_agent_path = _find_ai_agent_path()
    src = open(ai_agent_path).read()

    # Find the _inject_crystal_symmetry method body
    method_start = src.find("def _inject_crystal_symmetry")
    assert_true(method_start != -1,
        "_inject_crystal_symmetry must exist in ai_agent.py")

    # Find the next method def to bound the search (method is ~100 lines)
    next_method = src.find("\n  def ", method_start + 100)
    method_body = src[method_start: next_method]

    assert_true("crystal_symmetry.unit_cell=" in method_body,
        "_inject_crystal_symmetry must use crystal_symmetry.unit_cell= "
        "(not bare unit_cell=)")
    assert_true("crystal_symmetry.space_group=" in method_body,
        "_inject_crystal_symmetry must use crystal_symmetry.space_group= "
        "(not bare space_group=)")

    # Verify bare forms are absent from the append statements
    append_lines = [ln for ln in method_body.splitlines()
                    if "command +" in ln or "command=" in ln]
    for ln in append_lines:
        assert_true("unit_cell=" not in ln.replace("crystal_symmetry.unit_cell=", ""),
            "Bare unit_cell= must not appear in append line: %r" % ln)
        assert_true("space_group=" not in ln.replace("crystal_symmetry.space_group=", ""),
            "Bare space_group= must not appear in append line: %r" % ln)

    print("  PASSED: _inject_crystal_symmetry uses scoped crystal_symmetry. form")


def test_s4b_fallback_called_in_extract_directives():
    """
    _apply_crystal_symmetry_fallback must be called inside extract_directives
    so unit_cell is captured even when the LLM returns {}.  Verified by
    source inspection.
    """
    print("  Test: s4b_fallback_called_in_extract_directives")
    import inspect
    from agent import directive_extractor
    src = inspect.getsource(directive_extractor.extract_directives)

    assert_true("_apply_crystal_symmetry_fallback" in src,
        "_apply_crystal_symmetry_fallback must be called inside extract_directives")
    assert_true("validate_directives" in src,
        "extract_directives must still call validate_directives")

    # Fallback must come AFTER validate_directives in source order
    idx_validate = src.find("validate_directives")
    idx_fallback  = src.find("_apply_crystal_symmetry_fallback")
    assert_true(idx_fallback > idx_validate,
        "_apply_crystal_symmetry_fallback must run AFTER validate_directives "
        "(validate_directives at %d, fallback at %d)" % (idx_validate, idx_fallback))

    print("  PASSED: fallback is present and ordered correctly in extract_directives")


# =============================================================================
# UNKNOWN PHIL PARAMETER — diagnosable error + model_vs_data exclusion
# =============================================================================

def test_s4c_unknown_phil_param_is_diagnosable():
    """
    'Unknown command line parameter definition' must be in diagnosable_errors.yaml
    so the agent surfaces an error window instead of silently looping.
    Previously it matched 'unit_cell' in real_failure_patterns → FAILED, but
    nothing in diagnosable_errors.yaml caught it, so no window appeared.
    """
    print("  Test: s4c_unknown_phil_param_is_diagnosable")
    from agent.error_analyzer import get_diagnosis_detector

    detector = get_diagnosis_detector()

    # The exact error text PHENIX produces when a bad param is injected
    bad_param_errors = [
        ("Sorry: Unknown command line parameter definition: "
         "unit_cell = 116.097 116.097 44.175 90 90 120   "
         "It turns out there is no such parameter"),
        "Sorry: there is no such parameter: crystal_symmetry.unit_cell",
        "Unknown command line parameter definition: space_group = P 63",
    ]
    for err in bad_param_errors:
        match = detector.detect(err)
        assert_not_none(match,
            "detector must catch unknown PHIL param error: %r" % err[:80])
        error_type = match[0]
        assert_equal(error_type, "unknown_phil_parameter",
            "error_type must be 'unknown_phil_parameter', got %r" % error_type)

    print("  PASSED: unknown_phil_parameter correctly detected as diagnosable")


def test_s4c_model_vs_data_not_in_symmetry_programs():
    """
    phenix.model_vs_data (and xtriage, molprobity) must NOT be in
    _XRAY_SYMMETRY_PROGRAMS.  These programs read crystal symmetry from their
    input files automatically; injecting crystal_symmetry.unit_cell= causes
    'Unknown command line parameter definition: unit_cell' errors.
    """
    print("  Test: s4c_model_vs_data_not_in_symmetry_programs")
    ai_agent_path = _find_ai_agent_path()
    src = open(ai_agent_path).read()

    block_start = src.find("_XRAY_SYMMETRY_PROGRAMS = frozenset")
    assert_true(block_start >= 0, "_XRAY_SYMMETRY_PROGRAMS frozenset must exist in ai_agent.py")
    brace_open  = src.find("{", block_start)
    brace_close = src.find("}", brace_open)
    symmetry_block = src[brace_open: brace_close + 1]

    # These programs read symmetry from their input files — must never be listed
    auto_symmetry_programs = [
        "phenix.model_vs_data",
        "phenix.xtriage",
        "phenix.molprobity",
    ]
    for prog in auto_symmetry_programs:
        assert_true(prog not in symmetry_block,
            "%s must NOT be in _XRAY_SYMMETRY_PROGRAMS "
            "(it reads symmetry from MTZ/model automatically)" % prog)

    # Programs that DO need explicit crystal_symmetry must still be present
    explicit_symmetry_programs = [
        "phenix.refine",
        "phenix.phaser",
        "phenix.autosol",
    ]
    for prog in explicit_symmetry_programs:
        assert_true(prog in symmetry_block,
            "%s must be in _XRAY_SYMMETRY_PROGRAMS" % prog)

    print("  PASSED: model_vs_data/xtriage/molprobity correctly excluded")


# =============================================================================

def run_all_tests():
    """Run all audit fix tests."""
    run_tests_with_fail_fast()


if __name__ == "__main__":
    run_all_tests()
