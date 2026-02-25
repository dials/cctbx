"""
Command post-processing functions for the PHENIX AI Agent.

These are pure functions (no class/self dependencies) that transform a
command string based on state data.  They are called by both:

  - graph_nodes.py BUILD node (server-side, primary authority)
  - ai_agent.py _get_command_for_cycle (client-side, shadow/safety-net)

During the Phase 1 migration (Strangler Fig), both call sites are active
and their outputs are compared.  After Phase 2 cutover, only BUILD calls
these functions.

Functions:
  inject_crystal_symmetry  — append unit_cell/space_group from directives
  sanitize_command         — strip placeholder values and blacklisted params
  inject_user_params       — append user key=value params missing from command
  inject_program_defaults  — append defaults from programs.yaml if missing

Dependencies passed explicitly (no session/self access):
  - directives: dict from session.get_directives()
  - bad_inject_params: set from session.get_bad_inject_params(program)
  - user_advice: string from session processed_advice or raw advice
"""

from __future__ import absolute_import, division, print_function

import re


# =========================================================================
# inject_crystal_symmetry
# =========================================================================

# Programs that accept explicit crystal symmetry on the command line.
_XRAY_SYMMETRY_PROGRAMS = frozenset({
    'phenix.refine',
    'phenix.phaser',
    'phenix.autosol',
    'phenix.autobuild',
    'phenix.autobuild_denmod',
    'phenix.ligandfit',
    'phenix.polder',
})

# Programs using crystal_info.* scope (autobuild family)
_CRYSTAL_INFO_PROGRAMS = frozenset({
    'phenix.autobuild',
    'phenix.autobuild_denmod',
    'phenix.autosol',
})

# Programs using xray_data.* scope (phaser)
_PHASER_CS_PROGRAMS = frozenset({
    'phenix.phaser',
})

# Placeholder patterns for invalid space group values
_INVALID_SG_PATTERNS = (
    "not mentioned", "not specified", "not provided",
    "unknown", "n/a", "none", "null", "tbd", "to be determined",
)


def inject_crystal_symmetry(command, directives, program_name, log=None):
    """Append unit_cell and space_group to commands whose programs accept them.

    Crystal symmetry specified in user advice (extracted as
    program_settings.default.unit_cell / space_group in directives) must flow
    into every X-ray program that accepts it.

    Only injects for X-ray crystallography programs; cryo-EM programs do not
    accept a unit cell on the command line.

    Args:
        command:      Full command string
        directives:   Dict from session.get_directives() or graph state
        program_name: Program name (e.g. "phenix.refine")
        log:          Optional callable for logging (e.g. lambda msg: ...)

    Returns:
        Updated command string (may be unchanged).
    """
    if program_name not in _XRAY_SYMMETRY_PROGRAMS:
        return command

    if not directives:
        return command

    prog_settings = directives.get("program_settings", {})

    # Gather crystal info from program-specific scope first, then "default"
    unit_cell   = (prog_settings.get(program_name, {}).get("unit_cell") or
                   prog_settings.get("default", {}).get("unit_cell"))
    space_group = (prog_settings.get(program_name, {}).get("space_group") or
                   prog_settings.get("default", {}).get("space_group"))

    if not unit_cell and not space_group:
        return command

    command_lower = command.lower()

    # ── unit cell ──────────────────────────────────────────────────────────
    if unit_cell and "unit_cell" not in command_lower:
        nums = re.findall(r'[-+]?\d+(?:\.\d+)?', str(unit_cell))
        if len(nums) == 6:
            uc_str = " ".join(nums)
            if program_name in _CRYSTAL_INFO_PROGRAMS:
                param = 'crystal_info.unit_cell="%s"' % uc_str
            elif program_name in _PHASER_CS_PROGRAMS:
                param = 'xray_data.unit_cell="%s"' % uc_str
            else:
                param = 'crystal_symmetry.unit_cell="%s"' % uc_str
            command = command + ' ' + param
            if log:
                log("  [inject_crystal_symmetry] appended %s" % param)

    # ── space group ────────────────────────────────────────────────────────
    if space_group and "space_group" not in command_lower:
        sg_str = str(space_group).strip()

        _sg_lower = sg_str.lower()
        _is_placeholder = (
            any(p in _sg_lower for p in _INVALID_SG_PATTERNS) or
            len(sg_str) > 20 or
            not sg_str[0].isalpha()
        )
        if _is_placeholder:
            if log:
                log("  [inject_crystal_symmetry] skipping space_group=%r "
                    "(placeholder / invalid value)" % sg_str)
        else:
            if " " in sg_str and not sg_str.startswith('"'):
                sg_str = '"%s"' % sg_str
            if program_name in _CRYSTAL_INFO_PROGRAMS:
                param = 'crystal_info.space_group=%s' % sg_str
            elif program_name in _PHASER_CS_PROGRAMS:
                param = 'xray_data.space_group=%s' % sg_str
            else:
                param = 'crystal_symmetry.space_group=%s' % sg_str
            command = command + ' ' + param
            if log:
                log("  [inject_crystal_symmetry] appended %s" % param)

    return command


# =========================================================================
# sanitize_command
# =========================================================================

# Programs that accept only positional file arguments (no key=value params)
_PROBE_ONLY_FILE_PROGRAMS = frozenset({
    'phenix.model_vs_data',  'mmtbx.model_vs_data',
    'phenix.xtriage',        'mmtbx.command_line.xtriage',
    'phenix.molprobity',
    'phenix.mtriage',
    'phenix.validation_cryoem',
})

# Placeholder patterns for sanitize
_PLACEHOLDER_PATTERNS = (
    "not mentioned", "not specified", "not provided",
    "unknown", "n/a", "none", "null", "tbd", "to be determined",
)

# Universal keys that are always allowed for any program
_UNIVERSAL_KEYS = {
    'nproc', 'output_prefix', 'output_dir', 'output_directory',
    'prefix', 'overwrite', 'verbose', 'quiet',
}


def _is_placeholder_value(val):
    """Return True if val is a known extraction-failure placeholder."""
    v = val.strip().strip('"').strip("'").lower()
    return any(p in v for p in _PLACEHOLDER_PATTERNS)


def _load_prog_allowlist(program_name):
    """Load strategy_flags allowlist for a program from programs.yaml.

    Returns:
        tuple: (allowlist_set_or_None, strategy_flags_dict)
    """
    try:
        try:
            from libtbx.langchain.knowledge.yaml_loader import get_program as _get_prog
        except ImportError:
            from knowledge.yaml_loader import get_program as _get_prog
        prog_def = _get_prog(program_name) if program_name else None
        if prog_def is not None:
            strategy_flags = prog_def.get('strategy_flags') or {}
            allowlist = set(_UNIVERSAL_KEYS)
            for sfkey, sfdef in strategy_flags.items():
                allowlist.add(sfkey.lower())
                if isinstance(sfdef, dict):
                    flag_tpl = sfdef.get('flag', '')
                    flag_bare = flag_tpl.split('=')[0].strip().split('.')[-1].lower()
                    if flag_bare and flag_bare != '{value}':
                        allowlist.add(flag_bare)
            return allowlist, strategy_flags
    except Exception:
        pass
    return None, {}


def sanitize_command(command, program_name=None, bad_inject_params=None,
                     log=None):
    """Remove known-bad parameter values from an LLM-generated command.

    Strips:
      1. crystal_symmetry/unit_cell with placeholder values ("Not specified")
      2. Parameters blacklisted by session (bad_inject_params)
      3. All key=value tokens from probe-only programs
      4. Hallucinated cross-program params (strategy_flags allowlist)

    Args:
        command:           Raw command string from the LLM
        program_name:      Program name (derived from command if not supplied)
        bad_inject_params: Set of blacklisted param names for this program
        log:               Optional callable for logging

    Returns:
        Sanitised command string.
    """
    if not command:
        return command

    if not program_name:
        parts = command.strip().split()
        program_name = parts[0] if parts else ''

    if bad_inject_params is None:
        bad_inject_params = set()

    # ── 1. Probe-only programs: strip ALL key=value tokens ─────────────
    if program_name in _PROBE_ONLY_FILE_PROGRAMS:
        # Pre-pass: multi-word placeholder patterns
        _MULTIWORD_PLACEHOLDER_RE = re.compile(
            r'[\w.]+\s*=\s*(?:None|Not|Unknown|N/?A|TBD)'
            r'(?:\s+[A-Za-z]\w*)?',
            re.IGNORECASE)
        def _log_multiword(m):
            if log:
                log("  [sanitize_command] removed multi-word placeholder %r from %s"
                    % (m.group(0).strip(), program_name))
            return ''
        command = _MULTIWORD_PLACEHOLDER_RE.sub(_log_multiword, command)

        # Token loop: collapse quoted values, strip key=value tokens
        # EXCEPT those whose value looks like a file path (contains '/' or
        # has a crystallographic extension).  The CommandBuilder legitimately
        # produces key=value file assignments like half_map=/path/to/map.mrc
        # that must survive sanitization.
        _FILE_EXTS = frozenset({
            '.pdb', '.cif', '.mtz', '.sca', '.hkl',
            '.mrc', '.ccp4', '.map',
            '.fa', '.fasta', '.seq', '.dat',
            '.ncs_spec', '.eff',
        })
        command_for_tokens = re.sub(
            r'(\S+=)"([^"]*)"',
            lambda m: m.group(1) + m.group(2).replace(' ', '\x00'),
            command)
        stripped_tokens = []
        for tok in command_for_tokens.split():
            tok_display = tok.replace('\x00', ' ')
            if '=' in tok and not tok.startswith('-'):
                # Check if value looks like a file path
                val_part = tok.split('=', 1)[1].strip("'\"").replace('\x00', ' ')
                import os.path as _osp
                _ext = _osp.splitext(val_part)[1].lower()
                if '/' in val_part or _ext in _FILE_EXTS:
                    # Looks like a file path — keep it
                    stripped_tokens.append(tok_display)
                else:
                    if log:
                        log("  [sanitize_command] removed param %r from %s "
                            "(probe program takes only file args)" % (tok_display, program_name))
            else:
                stripped_tokens.append(tok_display)
        return ' '.join(stripped_tokens)

    # ── 2. Load strategy_flags allowlist ───────────────────────────────
    prog_allowlist, strategy_flags = _load_prog_allowlist(program_name)

    # ── 3. Generic sanitization loop ──────────────────────────────────
    _ALL_KV_RE = re.compile(
        r'\s*([\w.]+)\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s]+)'
    )
    _SG_NOT_WORD_RE = re.compile(
        r'\s*[\w.]*(?:space_group|unit_cell)[\w.]*\s*=\s*Not\s+\w+',
        re.IGNORECASE
    )

    def _strip_sg_not_word(text):
        def _replace(m):
            if log:
                log("  [sanitize_command] removed 'Not <word>' placeholder: %r"
                    % m.group(0).strip())
            return ''
        return _SG_NOT_WORD_RE.sub(_replace, text)

    sanitized = command
    changed = True
    while changed:
        prev = sanitized

        # Pass A: generic key=value scan
        new_tokens = []
        last_end = 0

        for m in _ALL_KV_RE.finditer(sanitized):
            key_full = m.group(1)
            key_short = key_full.split('.')[-1]
            token = m.group(0)
            eq_pos = token.find('=')
            val = token[eq_pos + 1:].strip() if eq_pos >= 0 else ''

            _strip = False

            # Rule A: blacklisted parameter name
            if key_full in bad_inject_params or key_short in bad_inject_params:
                _strip = True
                if log:
                    log("  [sanitize_command] removed blacklisted param: %r"
                        % token.strip())

            # Rule B: space_group or unit_cell with placeholder value
            if not _strip and ('space_group' in key_full or 'unit_cell' in key_full):
                if _is_placeholder_value(val):
                    _strip = True
                    if log:
                        log("  [sanitize_command] removed placeholder token: %r"
                            % token.strip())

            # Rule C: key=value on a program with no strategy_flags
            if (not _strip and prog_allowlist is not None and
                    len(strategy_flags) == 0):
                bare = key_short.lower()
                if bare not in _UNIVERSAL_KEYS:
                    _strip = True
                    if log:
                        log("  [sanitize_command] removed key=value param %r from "
                            "%s (program takes only file arguments)"
                            % (token.strip(), program_name))

            # Rule D: bare (unscoped) key=value not in allowlist for
            # programs that HAVE strategy_flags.  Scoped PHIL params
            # (containing dots, e.g. xray_data.r_free_flags.generate)
            # are kept — they go through PHIL validation downstream.
            if (not _strip and prog_allowlist is not None and
                    len(strategy_flags) > 0 and '.' not in key_full):
                bare = key_full.lower()
                if bare not in prog_allowlist:
                    _strip = True
                    if log:
                        log("  [sanitize_command] removed bare param %r from "
                            "%s (not in strategy_flags or universal keys)"
                            % (token.strip(), program_name))

            if not _strip:
                new_tokens.append(sanitized[last_end:m.end()])
            else:
                new_tokens.append(sanitized[last_end:m.start()])
            last_end = m.end()

        new_tokens.append(sanitized[last_end:])
        sanitized = ''.join(new_tokens)

        # Pass B: "Not <word>" space_group/unit_cell placeholders
        sanitized = _strip_sg_not_word(sanitized)

        changed = (sanitized != prev)

    # Collapse extra internal spaces
    sanitized = re.sub(r'  +', ' ', sanitized).strip()
    return sanitized


# =========================================================================
# inject_user_params
# =========================================================================

# Programs that should never receive injected key=value params
_NO_PARAM_INJECT_PROGRAMS = frozenset({
    'phenix.model_vs_data',  'mmtbx.model_vs_data',
    'phenix.xtriage',        'mmtbx.command_line.xtriage',
    'phenix.molprobity',
    'phenix.mtriage',
    'phenix.validation_cryoem',
})


def inject_user_params(command, user_advice, program_name='',
                       bad_inject_params=None, log=None):
    """Append key=value params from user advice that are missing from command.

    Only injects a dotted-path key (e.g. refinement.main.number_of_macro_cycles)
    if its leading scope matches the current program or is a known universal scope.

    Args:
        command:           Command string
        user_advice:       User guidelines/advice text
        program_name:      Program name (e.g. "phenix.refine")
        bad_inject_params: Set of blacklisted param names for this program
        log:               Optional callable for logging

    Returns:
        Updated command string.
    """
    if program_name in _NO_PARAM_INJECT_PROGRAMS:
        return command

    if bad_inject_params is None:
        bad_inject_params = set()

    prog_base = program_name.replace('phenix.', '').replace('mmtbx.', '').lower()

    # ── Natural-language → PHIL conversion ──────────────────────────────
    nl_extra = []
    try:
        try:
            from libtbx.langchain.agent.nl_to_phil import extract_nl_params
        except ImportError:
            from agent.nl_to_phil import extract_nl_params
        nl_extra = extract_nl_params(user_advice, program_name)
    except Exception as e:
        if log:
            log("  [inject_user_params] NL->PHIL lookup failed: %s" % e)

    guidelines_augmented = user_advice
    if nl_extra:
        guidelines_augmented = user_advice + '\n' + ' '.join(nl_extra)
        if log:
            log("  [inject_user_params] NL->PHIL: %s" % ', '.join(nl_extra))

    # Match PHIL-style key=value pairs
    _kv_re = re.compile(
        r'(?<![/\w])'
        r'([\w]+(?:\.[\w]+)*)'
        r'\s*=\s*'
        r'('
          r'True|False|None'
          r'|"[^"]*"'
          r"|'[^']*'"
          r'|[-+]?\d+(?:\.\d*)?'
          r'|[A-Za-z_]\w*'
        r')'
    )

    _UNIVERSAL_SCOPES = {'general', 'output', 'job', 'data_manager', 'nproc'}
    _SKIP_KEYS = {'e', 'i', 'http', 'https', 'key', 'param', 'setting'}

    command_lower = command.lower()
    appended = []
    skipped = []

    for m in _kv_re.finditer(guidelines_augmented):
        key, val = m.group(1), m.group(2)
        if key.lower() in _SKIP_KEYS or len(key) < 2:
            continue

        # For dotted keys, check whether the leading scope belongs to this program
        if '.' in key:
            leading_scope = key.split('.')[0].lower()
            if leading_scope not in _UNIVERSAL_SCOPES:
                scope_matches = (
                    bool(prog_base) and len(prog_base) >= 4 and
                    (leading_scope == prog_base or
                     (leading_scope.startswith(prog_base) and len(prog_base) >= 4) or
                     (prog_base.startswith(leading_scope) and len(leading_scope) >= 4))
                )
                if not scope_matches:
                    skipped.append(key)
                    continue

        short_key = key.split('.')[-1]

        # Skip blacklisted parameters
        if bad_inject_params and (key in bad_inject_params or
                                  short_key in bad_inject_params):
            skipped.append('%s (blacklisted)' % key)
            continue

        if key.lower() in command_lower or short_key.lower() in command_lower:
            continue

        param = '%s=%s' % (key, val)
        command = command + ' ' + param
        appended.append(param)

    if appended and log:
        log("  [inject_user_params] appended: %s" % ', '.join(appended))
    if skipped and log:
        log("  [inject_user_params] skipped (wrong program scope): %s"
            % ', '.join(skipped))

    return command


# =========================================================================
# postprocess_command — single entry point for BUILD
# =========================================================================

def postprocess_command(command, program_name, directives=None,
                        user_advice='', bad_inject_params=None, log=None):
    """Apply all server-safe post-processing transforms to a command.

    This is the single entry point called by the BUILD node.
    Applies transforms in order:
      1. sanitize_command
      2. inject_user_params
      3. inject_crystal_symmetry
      4. inject_program_defaults

    Client-only transforms (inject_missing_required_files) are NOT
    included here — they remain in ai_agent.py.

    Args:
        command:           Raw command string from command builder
        program_name:      Program name (e.g. "phenix.refine")
        directives:        Dict from session directives
        user_advice:       User guidelines/advice text
        bad_inject_params: Set of blacklisted param names for this program
        log:               Optional callable for logging

    Returns:
        Post-processed command string.
    """
    if not command or not command.strip():
        return command
    if command.strip().split()[0] == 'STOP':
        return command

    # 1. Sanitize (strip placeholders, blacklisted params)
    command = sanitize_command(
        command, program_name=program_name,
        bad_inject_params=bad_inject_params, log=log)

    # 2. Inject user params from advice text
    if user_advice:
        command = inject_user_params(
            command, user_advice, program_name=program_name,
            bad_inject_params=bad_inject_params, log=log)

    # 3. Inject crystal symmetry from directives
    if directives:
        command = inject_crystal_symmetry(
            command, directives, program_name, log=log)

    # 4. Inject program defaults from programs.yaml (safety net)
    command = inject_program_defaults(command, program_name, log=log)

    return command


# =========================================================================
# inject_program_defaults
# =========================================================================

def inject_program_defaults(command, program_name, log=None):
    """Append defaults from programs.yaml that are missing from command.

    This ensures critical parameters like r_free_flags.generate=True
    are always present regardless of LLM behaviour.

    Args:
        command:       Command string (after sanitize/inject steps)
        program_name:  Program name (e.g. "phenix.refine")
        log:           Optional callable for logging

    Returns:
        Command with any missing defaults appended.
    """
    if not command or not program_name:
        return command

    try:
        try:
            from libtbx.langchain.knowledge.yaml_loader import get_program as _get_prog
        except ImportError:
            from knowledge.yaml_loader import get_program as _get_prog
        prog_def = _get_prog(program_name) if program_name else None
        if prog_def is None:
            return command
        defaults = prog_def.get('defaults', {})
        if not defaults:
            return command
    except Exception:
        return command

    cmd_lower = command.lower()
    for key, val in defaults.items():
        # Check if key (or its leaf) is already in the command
        leaf = key.split('.')[-1].lower()
        if leaf in cmd_lower or key.lower() in cmd_lower:
            continue
        param = "%s=%s" % (key, val)
        command = command + ' ' + param
        if log:
            log("  [inject_program_defaults] appended default %s" % param)

    return command
