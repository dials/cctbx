"""PERCEIVE stop checks — standalone helpers.

These functions implement stop-condition logic for the PERCEIVE node
of the AI Agent graph.  They are pure functions with no heavy imports,
making them easy to test outside the full PHENIX environment.

Called from graph_nodes.perceive() at the start of each cycle to decide
whether to halt before running the LLM.
"""
from __future__ import absolute_import, division, print_function


def check_directive_stop(directives, history, cycle_number,
                         current_metrics=None):
    """Check if user directives say we should stop.

    Runs at the START of cycle N based on completed history.  Returns
    (should_stop, reason) just like Session.check_directive_stop_conditions,
    but operates purely on graph-side data without touching the Session.

    Args:
        directives: dict with stop_conditions, workflow_preferences, etc.
        history: list of history dicts (each has 'program', 'command', 'result')
        cycle_number: current cycle number (the one about to start)
        current_metrics: dict with r_free, map_cc etc. from current log analysis

    Returns:
        (bool, str or None): (should_stop, reason)
    """
    stop_cond = (directives or {}).get("stop_conditions", {})
    if not stop_cond or not history:
        return False, None

    last_entry = history[-1]
    last_program = last_entry.get("program", "")
    last_command = last_entry.get("command", "")
    completed_cycles = cycle_number - 1

    # after_cycle
    ac = stop_cond.get("after_cycle")
    if ac is not None and completed_cycles >= ac:
        return True, ("Reached cycle %d (directive: after_cycle=%d)"
                       % (completed_cycles, ac))

    # after_program
    ap = stop_cond.get("after_program")
    if ap and last_program:
        ap_norm = ap.replace("phenix.", "")
        lp_norm = last_program.replace("phenix.", "")
        if lp_norm == ap_norm or last_program == ap:
            # Guard: predict_and_build with stop_after_predict
            if ("predict_and_build" in last_program
                    and last_command
                    and "stop_after_predict" in last_command.lower()):
                return False, None
            return True, "Completed %s (directive: after_program)" % ap

    # metrics targets
    if current_metrics:
        rft = stop_cond.get("r_free_target")
        if rft is not None and current_metrics.get("r_free") is not None:
            if current_metrics["r_free"] <= rft:
                return True, ("R-free %.3f reached target %.3f"
                               % (current_metrics["r_free"], rft))
        mcct = stop_cond.get("map_cc_target")
        if mcct is not None and current_metrics.get("map_cc") is not None:
            if current_metrics["map_cc"] >= mcct:
                return True, ("Map CC %.3f reached target %.3f"
                               % (current_metrics["map_cc"], mcct))

    return False, None


def check_consecutive_program_cap(history, max_consecutive=3):
    """Check if the same program ran too many consecutive times.

    Counts consecutive SUCCESS entries at the tail of *history* with the
    same program name (matches Session.get_consecutive_program_count
    semantics).

    Args:
        history: list of history dicts (each has 'program', 'result')
        max_consecutive: threshold before stopping (default 3)

    Returns:
        (bool, str or None): (should_stop, reason)
    """
    if not history:
        return False, None

    tail_prog = None
    tail_count = 0
    for h in reversed(history):
        prog = h.get("program", "")
        result = h.get("result", "")
        if not prog:
            continue
        if not result.startswith("SUCCESS"):
            continue
        if tail_prog is None:
            tail_prog = prog
        if prog == tail_prog:
            tail_count += 1
        else:
            break

    if tail_count >= max_consecutive:
        reason = ("%s ran %d consecutive times without progress (max %d)"
                  % (tail_prog, tail_count, max_consecutive))
        return True, reason

    return False, None
