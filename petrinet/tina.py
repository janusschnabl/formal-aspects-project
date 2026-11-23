"""Export weighted nets to Tina and verify reachability properties.

Tina's full marking reachability graph (-R) has exactly the firing semantics
of PetriNet. Its liveness report lists transitions that never fire. A partial
enumeration, including one stopped by unboundedness, is never a verdict.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import TypedDict

from .net import PetriNet, Place, Transition
from . import witness


class TinaError(RuntimeError):
    pass


class _VerificationVerdicts(TypedDict):
    completion: dict[str, bool] | None
    deadlocks: dict[str, bool]
    properties: dict[str, bool]
    agent_completion: dict[str, dict[str, bool] | None]
    agent_deadlocks: dict[str, bool]


class VerificationResults(_VerificationVerdicts, total=False):
    traces: list[dict]


_BUNDLED_ROOT = Path(__file__).resolve().parent.parent / "tools" / "tina" / "4.0.0"

_COMPLETION_PROPERTIES = {
    "option_to_complete": "AG (EF IsFinal)",
    "all_traces_complete": "AF IsFinal",
}
_AGENT_COMPLETION_PROPERTIES = {
    "initially_final": "IsFinal",
    "can_complete": "EF IsFinal",
    "option_to_complete": _COMPLETION_PROPERTIES["option_to_complete"],
    "all_traces_complete": _COMPLETION_PROPERTIES["all_traces_complete"],
}
_DEADLOCK_PROPERTIES = {
    "reachable": "EF IsDeadlock",
    "non_final_reachable": "EF (IsDeadlock /\\ (- IsFinal))",
}
_AGENT_DEADLOCK_PROPERTIES = {
    "reachable": "EF ((- {final}) /\\ (- EF {can_act}))",
}
_CTL_PRELUDE = r"""prefix EF g = min x | g \/ <T>x;
prefix AG g = max x | g /\ [T]x;
prefix AF g = min x | g \/ (<T>T /\ [T]x);
prefix EG g = max x | g /\ ([T]F \/ <T>x);
prefix EX g = <T>g \/ ([T]F /\ g);
prefix AX g = [T]g /\ (<T>T \/ g);
op EU f g = min x | g \/ (f /\ <T>x);
op AU f g = min x | g \/ (f /\ <T>T /\ [T]x);
op IsDeadlock = [T]F;
"""


def resolve_executable(tina_bin: str | None = None) -> str:
    """Prefer an explicit executable, TINA_BIN, bundled Tina, then PATH.

    Bundled paths are relative to this module, independent of the caller's
    working directory. Windows checkouts may lose Linux executable bits, so
    restore user execute permission for the bundled Linux checker if needed.
    """
    override = tina_bin or os.environ.get("TINA_BIN")
    if override:
        return override
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        target = {"win32": ("windows-amd64", "tina.exe"), "linux": ("linux-amd64", "tina")}.get(sys.platform)
        if target:
            directory, executable = target
            bundled = _BUNDLED_ROOT / directory / "bin" / executable
            if bundled.is_file():
                if sys.platform == "linux" and not os.access(bundled, os.X_OK):
                    try:
                        bundled.chmod(bundled.stat().st_mode | 0o100)
                    except OSError as e:
                        raise TinaError(f"cannot make bundled Tina executable: {e}") from None
                return str(bundled)
    installed = shutil.which("tina")
    if installed:
        return installed
    raise TinaError(
        f"no Tina binary for {sys.platform}/{machine}; "
        "install Tina on PATH, set TINA_BIN, or use --tina PATH"
    )


def _identifier(name: str) -> str:
    # Braced identifiers preserve indexed names and escape Tina's delimiters.
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise TinaError(f"Tina identifiers cannot contain control characters: {name!r}")
    return "{" + re.sub(r"([\\{}])", r"\\\1", name) + "}"


def to_tina(net: PetriNet) -> str:
    """Write a Tina .net file, preserving names, markings, and arc weights.

    Timing is omitted (Tina's default [0,w[); analysis explicitly uses -R.
    Final markings remain acceptance metadata in Python.
    """
    lines = [f"pl {_identifier(p.name)} ({net.initial_marking[p]})" for p in sorted(net.places)]
    for t in sorted(net.transitions):
        inputs = " ".join(
            f"{_identifier(p.name)}*{net.weight(p, t)}" for p in sorted(net.preset(t))
        )
        outputs = " ".join(
            f"{_identifier(p.name)}*{net.weight(t, p)}" for p in sorted(net.postset(t))
        )
        lines.append(f"tr {_identifier(t.name)} {inputs} -> {outputs}")
    return "\n".join(lines) + "\n"


def _parse_names(text: str) -> list[str]:
    pattern = r"\{(?:\\.|[^{}\\])*\}|[A-Za-z0-9_']+"
    names = []
    position = 0
    for match in re.finditer(pattern, text):
        if text[position:match.start()].strip():
            raise TinaError("could not read Tina's dead-transition names")
        token = match.group()
        names.append(re.sub(r"\\([\\{}])", r"\1", token[1:-1]) if token.startswith("{") else token)
        position = match.end()
    if text[position:].strip():
        raise TinaError("could not read Tina's dead-transition names")
    return names


def _parse_result(lines: Iterable[str], net: PetriNet) -> list[Transition]:
    bounded = completed = incomplete = False
    count = None
    names = []
    for line in lines:
        line = line.strip()
        bounded |= line == "bounded"
        completed |= line.startswith("ANALYSIS COMPLETED ")
        incomplete |= line in {"enumeration not completed", "partial enumeration", "net unbounded"}
        match = re.fullmatch(r"(\d+) dead transition\(s\), \d+ live transition\(s\)", line)
        if match:
            count = int(match[1])
        if line.startswith("dead transition(s):"):
            names.extend(_parse_names(line.removeprefix("dead transition(s):")))
    if incomplete or not bounded or not completed:
        raise TinaError("Tina did not complete a bounded reachability analysis (the net may be unbounded)")
    transitions = {t.name: t for t in net.transitions}
    if count is None or len(names) != count or len(set(names)) != count or not set(names) <= transitions.keys():
        raise TinaError("could not read Tina's dead-transition result")
    return sorted(transitions[name] for name in names)


def dead_transitions(net: PetriNet, tina_bin: str | None = None, timeout: float | None = None) -> list[Transition]:
    """Check a bounded net using one native Tina invocation.

    Keep the full report on disk so a large graph need not be held in memory.
    Timeouts and incomplete exploration raise TinaError instead of reporting
    unvisited transitions as dead.
    """
    if not net.transitions:
        return []
    tina_bin = resolve_executable(tina_bin)
    with tempfile.TemporaryDirectory() as tmp:
        source, report = Path(tmp) / "net.net", Path(tmp) / "result.txt"
        source.write_text(to_tina(net), encoding="utf-8")
        cmd = [tina_bin, "-NET", "-R", "-v", str(source), str(report)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise TinaError(f"Tina executable {tina_bin!r} not found; use --tina PATH") from None
        except subprocess.TimeoutExpired:
            raise TinaError(f"Tina timed out after {timeout} seconds") from None
        if proc.returncode != 0:
            raise TinaError(f"Tina exited with code {proc.returncode}:\n{proc.stderr or proc.stdout}")
        if not report.is_file():
            raise TinaError(f"Tina did not write a result:\n{proc.stderr or proc.stdout}")
        with report.open(encoding="utf-8") as stream:
            return _parse_result(stream, net)


def _proposition(kind: str, name: str) -> str:
    # Tina canonicalizes simple names without braces in .ktz files.
    identifier = name if re.fullmatch(r"[A-Za-z0-9_']+", name) else _identifier(name)
    return f"{kind}.{identifier}"


def _can_act_expression(transitions: Iterable[Transition]) -> str:
    """A real outgoing edge involving this type; terminal self-loops do not count."""
    events = " \\/ ".join(_proposition("E", t.name) for t in sorted(set(transitions)))
    return f"(<({events})>T:S)" if events else "F:S"


def _final_expression(clauses: Iterable[Mapping[Place, int]]) -> str:
    """Build a state predicate for a union of explicit place constraints."""
    terms = []
    for clause in clauses:
        constraints = []
        for p, count in sorted(clause.items()):
            constraints.append(f"({_proposition('S', p.name)} = {count})")
        terms.append("(" + " /\\ ".join(constraints) + ")" if constraints else "T:S")
    return " \\/ ".join(sorted(terms)) or "F:S"


def _companion_tool(binary: Path, name: str) -> Path:
    tool = binary.with_name(name + (".exe" if binary.suffix.lower() == ".exe" else ""))
    if (sys.platform == "linux" and tool.parent == (_BUNDLED_ROOT / "linux-amd64" / "bin").resolve()
            and tool.is_file() and not os.access(tool, os.X_OK)):
        try:
            tool.chmod(tool.stat().st_mode | 0o100)
        except OSError as e:
            raise TinaError(f"cannot make bundled {name} executable: {e}") from None
    return tool


def _check_ctl(net: PetriNet, expressions: list[str], tina_bin: str | None, timeout: float | None,
               *, trace_groups=None, traces=None) -> list[bool]:
    """Evaluate CTL expressions at the initial state of Tina's full graph."""
    clauses = [{p: marking[p] for p in net.places} for marking in net.final_markings]
    clauses.extend(net.unrestricted_final_markings)
    final = _final_expression(clauses)

    executable = resolve_executable(tina_bin)
    binary = Path(shutil.which(executable) or executable).resolve()
    deadline = None if timeout is None else time.monotonic() + timeout

    def remaining_time():
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        if remaining == 0:
            raise TinaError("Tina/Muse property check timed out (including trace extraction)")
        return remaining

    def run(cmd):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=remaining_time())
        except FileNotFoundError:
            raise TinaError(f"Property-check executable {cmd[0]!r} not found") from None
        except subprocess.TimeoutExpired:
            raise TinaError("Tina/Muse property check timed out") from None
        if proc.returncode != 0:
            raise TinaError(f"Property check failed: {proc.stderr or proc.stdout}")
        return proc.stdout

    with tempfile.TemporaryDirectory() as tmp:
        source, graph, formulas = (Path(tmp) / name for name in ("net.net", "net.ktz", "properties.mmc"))
        source.write_text(to_tina(net), encoding="utf-8")
        digest = run([str(binary), "-NET", "-R", "-ktz", str(source), str(graph)])
        if not re.search(r"^#\s+bounded(?:,|\s)", digest, re.MULTILINE) or not graph.is_file():
            raise TinaError("Tina did not complete a bounded reachability analysis for property checking")
        # AF/EG include terminal executions. EX/AX treat a deadlock as an
        # implicit self-loop, as in a total Kripke structure (no net arcs added).
        prelude = _CTL_PRELUDE + f"op IsFinal = {final};\n"
        formulas.write_text(prelude + ";\n".join(expressions) + ";\n", encoding="utf-8")
        tool = lambda name: _companion_tool(binary, name)
        answers = run([str(tool("muse")), "-q", "-b", str(graph), str(formulas)]).splitlines()
        if len(answers) != len(expressions) or any(answer not in {"TRUE", "FALSE"} for answer in answers):
            raise TinaError("could not read Muse CTL results: " + "\n".join(answers))
        verdicts = [answer == "TRUE" for answer in answers]
        if traces is not None:
            try:
                traces.extend(witness.collect(net, trace_groups, verdicts, graph=graph, formulas=formulas,
                                              prelude=prelude, run=run, tool=tool,
                                              identifier=lambda name: _proposition("S", name)[2:],
                                              check_timeout=remaining_time))
            except witness.WitnessError as e:
                raise TinaError(str(e)) from e
        return verdicts


def _check_groups(
    net: PetriNet, groups: Mapping[str, Mapping[str, str]],
    tina_bin: str | None, timeout: float | None, *, traces=None,
) -> dict[str, dict[str, bool]]:
    """Batch named formulas and map answers back to their report sections."""
    expressions = [formula for properties in groups.values() for formula in properties.values()]
    answers = iter(_check_ctl(net, expressions, tina_bin, timeout, trace_groups=groups, traces=traces))
    return {group: {name: next(answers) for name in properties} for group, properties in groups.items()}


def check_completion(net: PetriNet, tina_bin: str | None = None, timeout: float | None = None) -> dict[str, bool] | None:
    """Check AG EF IsFinal and AF IsFinal; None means no final declarations."""
    if not net.final_markings and not net.unrestricted_final_markings:
        return None
    return _check_groups(net, {"completion": _COMPLETION_PROPERTIES}, tina_bin, timeout)["completion"]


def check_deadlocks(net: PetriNet, tina_bin: str | None = None, timeout: float | None = None) -> dict[str, bool]:
    """Detect reachable terminal markings, including those outside the final set.

    With no final declarations, every deadlock is non-final. A temporal
    self-loop used by AX/EX does not make a terminal marking non-deadlocked.
    """
    return _check_groups(net, {"deadlocks": _DEADLOCK_PROPERTIES}, tina_bin, timeout)["deadlocks"]


def verify_properties(
    net: PetriNet, tina_bin: str | None = None, timeout: float | None = None,
    *, agent_final_clauses: Mapping[str, Iterable[Mapping[Place, int]]] | None = None,
    agent_transitions: Mapping[str, Iterable[Transition]] | None = None,
    include_traces: bool = False,
) -> VerificationResults:
    """Check global and per-type properties on one global reachability graph.

    Per-type completion checks the inferred local final predicate initially,
    as well as EF, AG EF, and AF of that predicate.
    A type with no explicit final clauses has a None result.
    Per-type deadlocks are reachable unfinished states from which participation
    is permanently impossible. Missing local finals give no completion exemption.
    With include_traces, add replayed evidence for built-in violations using
    this same graph. Arbitrary user CTL formulas retain boolean results only.
    """
    has_finals = bool(net.final_markings or net.unrestricted_final_markings)
    groups = {
        "completion": _COMPLETION_PROPERTIES if has_finals else {},
        "deadlocks": _DEADLOCK_PROPERTIES,
        "properties": net.ctl_properties,
    }
    local_finals = {name: tuple(clauses) for name, clauses in sorted((agent_final_clauses or {}).items())}
    for name, clauses in local_finals.items():
        if clauses:
            final = "(" + _final_expression(clauses) + ")"
            groups[f"agent:{name}"] = {key: formula.replace("IsFinal", final)
                                      for key, formula in _AGENT_COMPLETION_PROPERTIES.items()}
    for name, transitions in sorted((agent_transitions or {}).items()):
        final = "(" + _final_expression(local_finals.get(name, ())) + ")"
        can_act = _can_act_expression(transitions)
        groups[f"agent_deadlock:{name}"] = {
            key: formula.format(final=final, can_act=can_act)
            for key, formula in _AGENT_DEADLOCK_PROPERTIES.items()
        }
    traces = [] if include_traces else None
    results = _check_groups(net, groups, tina_bin, timeout, traces=traces)
    verification: VerificationResults = {
        "completion": results["completion"] if has_finals else None,
        "deadlocks": results["deadlocks"],
        "properties": results["properties"],
        "agent_completion": {name: results.get(f"agent:{name}") for name in local_finals},
        "agent_deadlocks": {name: results[f"agent_deadlock:{name}"]["reachable"]
                            for name in sorted(agent_transitions or {})},
    }
    if traces is not None:
        verification["traces"] = traces
    return verification
