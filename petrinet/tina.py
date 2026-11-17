"""Export ordinary weighted nets to Tina and check dead transitions.

Tina's full marking reachability graph (-R) has exactly the firing semantics
of PetriNet. Its liveness report lists transitions that never fire. A partial
enumeration, including one stopped by unboundedness, is never a verdict.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from .net import PetriNet, Transition


class TinaError(RuntimeError):
    pass


_BUNDLED_ROOT = Path(__file__).resolve().parent.parent / "tools" / "tina" / "4.0.0"


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
