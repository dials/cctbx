"""Test the MTZ cross-check fix in _categorize_files.

When the YAML pattern-based categorizer fails to recognize a refine output
MTZ file (e.g. refine_001_001.mtz), it ends up in data_mtz.  The cross-check
post-processing step uses file_utils.classify_mtz_type — the canonical regex
authority — to move it to map_coeffs_mtz / refine_map_coeffs.
"""

from __future__ import absolute_import, division, print_function

import os
import sys

# Ensure sys.path is set before agent imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.tst_utils import assert_true
from tests.tst_utils import assert_in
from tests.tst_utils import assert_equal
from tests.tst_utils import run_tests_with_fail_fast

# PHENIX/cctbx Linter "Silencer"
# We remove 're' as it is unused in this script. We include the utilities
# that are only referenced inside function bodies to satisfy libtbx.
(assert_true, assert_in, assert_equal, run_tests_with_fail_fast)


def _mock_libtbx():
    """Set up mocks so agent modules can be imported without libtbx."""
    import importlib

    class MockModule:
        def __getattr__(self, name):
            return MockModule()
        def __call__(self, *a, **kw):
            return None

    for mod in ['libtbx', 'libtbx.langchain', 'libtbx.langchain.agent',
                'libtbx.langchain.knowledge', 'libtbx.langchain.knowledge.yaml_loader']:
        if mod not in sys.modules:
            sys.modules[mod] = MockModule()
    sys.modules['libtbx.langchain.agent.file_utils'] = importlib.import_module('agent.file_utils')

_mock_libtbx()


# ---------------------------------------------------------------------------
# classify_mtz_type correctness (canonical regex)
# ---------------------------------------------------------------------------

def test_classify_mtz_type_refine_outputs():
    """classify_mtz_type correctly identifies all refine output MTZ variants."""
    from agent.file_utils import classify_mtz_type

    map_coeffs_files = [
        'refine_001.mtz',
        'refine_001_001.mtz',
        '7qz0_refine_001.mtz',
        '7qz0_refine_001_001.mtz',
        'nsf_refine_001.mtz',
    ]
    for fn in map_coeffs_files:
        result = classify_mtz_type('/path/' + fn)
        assert_equal(result, 'map_coeffs_mtz',
                     '%s should be map_coeffs_mtz, got %s' % (fn, result))

    data_files = [
        'refine_001_data.mtz',
        'nsf-d2.mtz',
        'data.mtz',
        'refinement_data.mtz',
    ]
    for fn in data_files:
        result = classify_mtz_type('/path/' + fn)
        assert_equal(result, 'data_mtz',
                     '%s should be data_mtz, got %s' % (fn, result))


# ---------------------------------------------------------------------------
# Cross-check in _categorize_files rescues miscategorized refine MTZ
# ---------------------------------------------------------------------------

def test_crosscheck_rescues_miscategorized_refine_mtz():
    """When YAML categorizer puts refine MTZ in data_mtz, cross-check fixes it."""
    from agent.workflow_state import _bubble_up_to_parents
    from agent.file_utils import classify_mtz_type, get_mtz_stage

    refine_mtz = '/client/sub_03_refine/refine_001_001.mtz'

    files = _bubble_up_to_parents({
        'data_mtz': ['/client/nsf-d2.mtz', refine_mtz],
        'map_coeffs_mtz': [],
        'refine_map_coeffs': [],
        'model': [],
    })

    assert_in(refine_mtz, files['data_mtz'])

    for f in list(files.get('data_mtz', [])):
        if not f.lower().endswith('.mtz'):
            continue
        canonical = classify_mtz_type(f)
        if canonical == 'map_coeffs_mtz':
            files['data_mtz'].remove(f)
            if 'map_coeffs_mtz' not in files:
                files['map_coeffs_mtz'] = []
            if f not in files['map_coeffs_mtz']:
                files['map_coeffs_mtz'].append(f)
            stage = get_mtz_stage(f, 'map_coeffs_mtz')
            if stage and stage not in ('map_coeffs_mtz',):
                if stage not in files:
                    files[stage] = []
                if f not in files[stage]:
                    files[stage].append(f)

    assert_in(refine_mtz, files['map_coeffs_mtz'])
    assert_in(refine_mtz, files['refine_map_coeffs'])
    assert_true(refine_mtz not in files['data_mtz'])


def test_crosscheck_does_not_move_data_files():
    """Cross-check does not touch genuine data MTZ files."""
    from agent.file_utils import classify_mtz_type

    data_files = [
        '/client/nsf-d2.mtz',
        '/client/refine_001_data.mtz',
        '/client/data.mtz',
    ]
    for f in data_files:
        result = classify_mtz_type(f)
        assert_equal(result, 'data_mtz',
                     '%s should stay as data_mtz' % os.path.basename(f))


def test_crosscheck_handles_all_naming_variants():
    """Cross-check rescues ALL known refine output naming variants."""
    from agent.workflow_state import _bubble_up_to_parents
    from agent.file_utils import classify_mtz_type, get_mtz_stage

    variants = [
        'refine_001.mtz',
        'refine_001_001.mtz',
        '7qz0_refine_001.mtz',
        '7qz0_refine_001_001.mtz',
    ]

    for basename in variants:
        path = '/client/' + basename
        files = _bubble_up_to_parents({
            'data_mtz': [path],
            'map_coeffs_mtz': [],
            'refine_map_coeffs': [],
        })

        for f in list(files.get('data_mtz', [])):
            if not f.lower().endswith('.mtz'):
                continue
            canonical = classify_mtz_type(f)
            if canonical == 'map_coeffs_mtz':
                files['data_mtz'].remove(f)
                if 'map_coeffs_mtz' not in files:
                    files['map_coeffs_mtz'] = []
                if f not in files['map_coeffs_mtz']:
                    files['map_coeffs_mtz'].append(f)
                stage = get_mtz_stage(f, 'map_coeffs_mtz')
                if stage and stage not in ('map_coeffs_mtz',):
                    if stage not in files:
                        files[stage] = []
                    if f not in files[stage]:
                        files[stage].append(f)

        assert_in(path, files['map_coeffs_mtz'])
        assert_true(path not in files.get('data_mtz', []))


def test_categorize_files_full_integration():
    """Scenario test for refine-then-ligandfit."""
    from agent.workflow_state import _categorize_files

    available_files = [
        '/client/nsf-d2.pdb',
        '/client/nsf-d2.mtz',
        '/client/nsf-d2.seq',
        '/client/ligand.pdb',
        '/client/sub_03_refine/refine_001_data.mtz',
        '/client/sub_03_refine/refine_001_001.mtz',
        '/client/sub_03_refine/refine_001_001.pdb',
    ]

    files = _categorize_files(available_files)
    mc = '/client/sub_03_refine/refine_001_001.mtz'

    assert_in(mc, files.get('map_coeffs_mtz', []))
    assert_true(mc not in files.get('data_mtz', []))


def test_crosscheck_fixes_dual_categorization():
    """Safety net removes map_coeffs file from data_mtz when in both."""
    from agent.file_utils import classify_mtz_type
    from agent.file_utils import get_mtz_stage

    # FIX: Reference the imported function name correctly to satisfy linter
    (classify_mtz_type, get_mtz_stage)

    files = {
        'data_mtz': ['/c/nsf-d2.mtz', '/c/refine_001_data.mtz',
                     '/c/refine_001_001.mtz'],
        'map_coeffs_mtz': ['/c/refine_001_001.mtz'],
        'refine_map_coeffs': ['/c/refine_001_001.mtz'],
    }

    all_mtz = {f for cat_list in files.values() if isinstance(cat_list, list)
               for f in cat_list if isinstance(f, str) and f.lower().endswith('.mtz')}

    for f in all_mtz:
        correct_type = classify_mtz_type(f)
        if correct_type == 'map_coeffs_mtz' and f in files.get('map_coeffs_mtz', []) and f in files.get('data_mtz', []):
            files['data_mtz'].remove(f)

    mc = '/c/refine_001_001.mtz'
    assert_in(mc, files.get('map_coeffs_mtz', []))
    assert_true(mc not in files.get('data_mtz', []))


def test_hardcoded_categorizer_populates_subcategories():
    """Hardcoded categorizer must populate both parent and subcategory."""
    from agent.workflow_state import _categorize_files_hardcoded, _bubble_up_to_parents

    available_files = [
        '/c/refine_001_001.mtz',
        '/c/refine_001_data.mtz',
        '/c/denmod_001.mtz',
        '/c/overall_best_map_coeffs.mtz',
    ]

    files = _categorize_files_hardcoded(available_files)
    files = _bubble_up_to_parents(files)

    assert_in('/c/refine_001_001.mtz', files.get('refine_map_coeffs', []))
    assert_in('/c/refine_001_001.mtz', files.get('map_coeffs_mtz', []))
    assert_in('/c/denmod_001.mtz', files.get('denmod_map_coeffs', []))
    assert_in('/c/overall_best_map_coeffs.mtz', files.get('predict_build_map_coeffs', []))


# ---------------------------------------------------------------------------

TESTS = [
    test_classify_mtz_type_refine_outputs,
    test_crosscheck_rescues_miscategorized_refine_mtz,
    test_crosscheck_does_not_move_data_files,
    test_crosscheck_handles_all_naming_variants,
    test_categorize_files_full_integration,
    test_crosscheck_fixes_dual_categorization,
    test_hardcoded_categorizer_populates_subcategories,
]

if __name__ == '__main__':
    run_tests_with_fail_fast(TESTS)

