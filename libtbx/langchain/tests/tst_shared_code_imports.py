"""
Shared-code import safety tests.

The agent/ directory ships with both the client (in PHENIX) and the server.
Shared modules must not import:
  1. LLM/server-only dependencies (langchain_core, openai, anthropic, etc.)
  2. Agent modules that don't exist in the shipped codebase
  3. Third-party packages not bundled with PHENIX

See docs/guides/BACKWARD_COMPATIBILITY.md — RULE 7: The agent/ shared code trap.

Run with:
    PYTHONPATH=. python tests/tst_shared_code_imports.py
"""


import os
import re
import sys

assert sys is not None
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.tst_utils import assert_true
from tests.tst_utils import run_tests_with_fail_fast

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =========================================================================
# Classification of agent/ modules
# =========================================================================

# Shared modules: run on BOTH client and server.
# These are the dangerous ones — imports here must work everywhere.
SHARED_MODULES = [
    "command_builder.py",
    "workflow_engine.py",
    "workflow_state.py",
    "planner.py",
    "command_postprocessor.py",
    "file_utils.py",
    "advice_preprocessor.py",
    "contract.py",
]

# Server-only: runs exclusively on the REST server.
# Can import LLM SDKs, LangGraph, etc.
SERVER_ONLY_MODULES = [
    "graph_nodes.py",
]

# Client-only: runs exclusively on the user's PHENIX installation.
# Can import PHENIX GUI/session modules.
CLIENT_ONLY_MODULES = [
    "session.py",
    "best_files_tracker.py",
]

# =========================================================================
# Import safety rules
# =========================================================================

# LLM / server-only packages that must NEVER appear in shared modules.
# These are installed on the server but not in the PHENIX distribution.
FORBIDDEN_IN_SHARED = [
    "langchain",         # langchain, langchain_core, langchain_community, etc.
    "langgraph",
    "openai",
    "anthropic",
    "google.generativeai",
    "google.genai",
    "tiktoken",
    "httpx",             # Often an openai transitive dep
]

# PHENIX-internal modules (under libtbx.langchain.agent/ or agent/) that are
# known to exist in all supported client versions.  If a shared module imports
# an agent module NOT in this list, it's flagged for review.
#
# Update this list when new modules are added to PHENIX releases.
KNOWN_AGENT_MODULES = {
    "advice_preprocessor",
    "best_files_tracker",
    "command_builder",
    "command_postprocessor",
    "contract",
    "event_log",
    "file_utils",
    "graph_nodes",
    "metrics_analyzer",
    "nl_to_phil",
    "pattern_manager",
    "perceive_checks",
    "placement_checker",
    "planner",
    "program_registry",
    "rate_limit_handler",
    "rules_selector",
    "session",
    "template_builder",
    "workflow_engine",
    "workflow_state",
}

# Known knowledge/ modules
KNOWN_KNOWLEDGE_MODULES = {
    "program_registration",
    "prompts_hybrid",
    "yaml_loader",
}


def _get_shared_file_paths():
    """Return absolute paths for shared modules that exist."""
    paths = []
    for name in SHARED_MODULES:
        path = os.path.join(_PROJECT_ROOT, "agent", name)
        if os.path.exists(path):
            paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Test: No LLM / server-only imports in shared code
# ---------------------------------------------------------------------------

def test_no_forbidden_imports_in_shared():
    """
    Shared modules must not import LLM SDKs or server-only packages.

    These packages are installed on the server but NOT in the PHENIX
    distribution.  Importing them in shared code would crash clients
    that don't have them installed.
    """
    violations = []

    for filepath in _get_shared_file_paths():
        basename = os.path.basename(filepath)
        with open(filepath) as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if not (stripped.startswith("from ") or stripped.startswith("import ")):
                    continue

                # Skip libtbx.langchain.* (PHENIX internal — uses "langchain" in path name)
                if "libtbx.langchain" in stripped:
                    continue

                for forbidden in FORBIDDEN_IN_SHARED:
                    # Match "import langchain" or "from langchain" etc.
                    pattern = r'(?:from|import)\s+' + re.escape(forbidden)
                    if re.search(pattern, stripped):
                        violations.append(
                            "%s:%d  %s" % (basename, lineno, stripped[:100])
                        )

    if violations:
        msg = (
            "Found %d forbidden import(s) in shared modules:\n  %s\n\n"
            "These packages are server-only and will crash on clients.\n"
            "Move this logic to graph_nodes.py or guard with try/except."
            % (len(violations), "\n  ".join(violations))
        )
        assert_true(False, msg)
    print("  PASSED: No forbidden imports in %d shared modules" % len(SHARED_MODULES))


# ---------------------------------------------------------------------------
# Test: All agent/ imports reference known modules
# ---------------------------------------------------------------------------

def test_agent_imports_reference_known_modules():
    """
    When shared code does `from agent.X import ...` or
    `from libtbx.langchain.agent.X import ...`, verify X is a
    known module that exists in all supported client versions.

    New modules must be added to KNOWN_AGENT_MODULES when they
    are first shipped in a PHENIX release.
    """
    unknown = []

    for filepath in _get_shared_file_paths():
        basename = os.path.basename(filepath)
        with open(filepath) as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue

                # Match agent module imports
                for m in re.finditer(
                    r'from\s+(?:libtbx\.langchain\.)?agent\.(\w+)', stripped
                ):
                    module_name = m.group(1)
                    if module_name not in KNOWN_AGENT_MODULES:
                        unknown.append(
                            "%s:%d  agent.%s — not in KNOWN_AGENT_MODULES"
                            % (basename, lineno, module_name)
                        )

                # Match knowledge module imports
                for m in re.finditer(
                    r'from\s+(?:libtbx\.langchain\.)?knowledge\.(\w+)', stripped
                ):
                    module_name = m.group(1)
                    if module_name not in KNOWN_KNOWLEDGE_MODULES:
                        unknown.append(
                            "%s:%d  knowledge.%s — not in KNOWN_KNOWLEDGE_MODULES"
                            % (basename, lineno, module_name)
                        )

    if unknown:
        msg = (
            "Found %d import(s) of unknown modules in shared code:\n  %s\n\n"
            "If these modules exist in all PHENIX versions you support,\n"
            "add them to KNOWN_AGENT_MODULES / KNOWN_KNOWLEDGE_MODULES.\n"
            "If they're new, ensure old clients can handle their absence."
            % (len(unknown), "\n  ".join(unknown))
        )
        assert_true(False, msg)
    print("  PASSED: All agent/knowledge imports reference known modules")


# ---------------------------------------------------------------------------
# Test: Server-only module has expected LLM imports
# ---------------------------------------------------------------------------

def test_server_only_has_llm_imports():
    """
    Sanity check: graph_nodes.py (server-only) SHOULD have LLM imports.
    If it doesn't, something is wrong with the classification.
    """
    graph_nodes = os.path.join(_PROJECT_ROOT, "agent", "graph_nodes.py")
    if not os.path.exists(graph_nodes):
        print("  SKIP (graph_nodes.py not found)")
        return

    with open(graph_nodes) as f:
        content = f.read()

    # Should have at least one LLM-related import (not under libtbx.langchain)
    has_llm = bool(re.search(
        r'(?:from|import)\s+(?:langchain_core|langchain_google|langchain_openai)',
        content
    ))
    # Or references to LLM providers
    has_provider = "provider" in content and ("openai" in content or "google" in content)

    assert_true(has_llm or has_provider,
                "graph_nodes.py should have LLM imports — "
                "verify SERVER_ONLY classification is correct")
    print("  PASSED: graph_nodes.py correctly contains LLM imports (server-only)")


# ---------------------------------------------------------------------------
# Test: Shared modules use guarded imports for intra-agent deps
# ---------------------------------------------------------------------------

def test_shared_imports_are_guarded():
    """
    Intra-agent imports in shared modules should use the dual-path pattern:

        try:
            from libtbx.langchain.agent.X import Y
        except ImportError:
            from agent.X import Y

    Top-level unguarded imports are acceptable for modules that are
    guaranteed to exist (e.g., same-file or always-shipped modules),
    but we flag them as warnings for awareness.
    """
    unguarded = []

    for filepath in _get_shared_file_paths():
        basename = os.path.basename(filepath)
        with open(filepath) as f:
            lines = f.readlines()

        # Simple state machine: track if we're inside a try block
        in_try = False
        try_depth = 0

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            if stripped.startswith("try:"):
                in_try = True
                try_depth += 1
                continue
            if stripped.startswith("except") and "Import" in stripped:
                try_depth = max(0, try_depth - 1)
                if try_depth == 0:
                    in_try = False
                continue

            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue

            # Check for agent/knowledge imports at module level (not indented much)
            indent = len(line) - len(line.lstrip())
            if indent > 8:  # Deeply indented = inside a function, less risky
                continue

            is_agent_import = bool(re.match(
                r'from\s+(?:libtbx\.langchain\.)?(?:agent|knowledge)\.',
                stripped
            ))

            if is_agent_import and not in_try:
                unguarded.append(
                    "%s:%d  %s" % (basename, i, stripped[:100])
                )

    if unguarded:
        print("  WARNING: %d top-level unguarded intra-agent import(s) in shared code:"
              % len(unguarded))
        for u in unguarded:
            print("    %s" % u)
        print("  (These work if the modules ship with PHENIX, but consider"
              " try/except for safety)")
    else:
        print("  PASSED: All intra-agent imports in shared code are guarded")

    # Warning-only for now — promote to failure after cleanup
    print("  PASS (warning-only for now)")


# =========================================================================
# Run
# =========================================================================

def run_all_tests():
    run_tests_with_fail_fast()


if __name__ == "__main__":
    run_all_tests()
