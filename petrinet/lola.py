"""Export a PetriNet to LoLA 2 syntax and ask LoLA which transitions are dead.

A transition t is dead iff no reachable marking enables it, i.e. the
reachability query  EF FIREABLE(t)  is false. We run one LoLA query per
transition.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from .net import PetriNet


class LolaError(RuntimeError):
    pass


def to_lola(net: PetriNet) -> str:
    def arcs(d: dict[str, int]) -> str:
        return ", ".join(f"{p}: {w}" for p, w in d.items())

    lines = [f"PLACE {', '.join(net.places)};", ""]
    marking = [f"{p}: {n}" for p, n in net.initial.items() if n > 0]
    lines += [f"MARKING {', '.join(marking)};", ""]
    for t in net.transitions:
        lines += [
            f"TRANSITION {t.name}",
            f"  CONSUME {arcs(t.pre)};",
            f"  PRODUCE {arcs(t.post)};",
            "",
        ]
    return "\n".join(lines)


def _parse_result(stdout: str, stderr: str, json_path: str) -> bool:
    """Return the truth value LoLA reported for the formula."""
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        result = data["analysis"]["result"]
        if isinstance(result, bool):
            return result
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # Fall back to the human-readable line, e.g. "lola: result: yes"
    m = re.search(r"result:\s*(yes|no)\b", stderr + stdout)
    if m:
        return m.group(1) == "yes"
    raise LolaError(f"could not read LoLA result.\nstdout:\n{stdout}\nstderr:\n{stderr}")


def check_formula(lola_file: str, formula: str, lola_bin: str = "lola", timeout: float | None = None) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        json_path = os.path.join(tmp, "result.json")
        cmd = [lola_bin, lola_file, f"--formula={formula}", f"--json={json_path}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise LolaError(f"LoLA executable {lola_bin!r} not found") from None
        except subprocess.TimeoutExpired:
            raise LolaError(f"LoLA timed out on {formula!r}") from None
        return _parse_result(proc.stdout, proc.stderr, json_path)


def dead_transitions(net: PetriNet, lola_bin: str = "lola", timeout: float | None = None) -> list[str]:
    if shutil.which(lola_bin) is None and not os.path.isfile(lola_bin):
        raise LolaError(f"LoLA executable {lola_bin!r} not found on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "net.lola")
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_lola(net))
        return [
            t.name
            for t in net.transitions
            if not check_formula(path, f"EF FIREABLE({t.name})", lola_bin, timeout)
        ]
