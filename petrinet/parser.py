"""Read a Petri net from the plain-text format.

The format mirrors N = ((P, T, F), m_0):

    places: p1 p2 p3                  # P
    transitions: t1 t2                # T
    initial: p1=1 p3=2                # m_0 (places not listed get 0)
    arcs: (p1, t1) (t1, p2)           # F, each arc is (place, transition)
          (p2, t2, 2) (t2, p3)        #    or (transition, place); the optional
                                      #    3rd element is the weight (default 1)

`#` starts a comment. Each keyword may be repeated; a line that does not
start with a keyword continues the previous one. Names may contain
letters, digits and underscores, and cannot start with a digit.

Parsing happens in two steps:
  1. read the file into declarations, each remembering its line number;
  2. turn the names into Place, Transition, Arc and Marking objects and
     build the PetriNet from them.
Step 2 checks the things a set would hide (a name or an arc given twice),
so that the error can point at the line.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field

from .net import Arc, InputArc, Marking, OutputArc, PetriNet, PetriNetError, Place, Transition

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
NAME_RE = re.compile(NAME)
KEYWORD_RE = re.compile(r"^(places|transitions|initial|arcs)\s*:(.*)$")
ARC_RE = re.compile(rf"\(\s*({NAME})\s*,\s*({NAME})\s*(?:,\s*(\d+)\s*)?\)")
MARKING_RE = re.compile(rf"({NAME})\s*=\s*(\d+)")


class ParseError(ValueError):
    def __init__(self, lineno: int, msg: str):
        super().__init__(f"line {lineno}: {msg}" if lineno else msg)
        self.lineno = lineno


@dataclass
class Declarations:
    """What the file says, before any checks. Each entry starts with its line number."""

    places: list[tuple[int, str]] = field(default_factory=list)               # (line, name)
    transitions: list[tuple[int, str]] = field(default_factory=list)          # (line, name)
    tokens: list[tuple[int, str, int]] = field(default_factory=list)          # (line, place, tokens)
    arcs: list[tuple[int, str, str, int]] = field(default_factory=list)       # (line, source, target, weight)


def load(path: str) -> PetriNet:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def parse(text: str) -> PetriNet:
    return build_net(read_declarations(text))


# --- step 2: declarations -> PetriNet --------------------------------------


def build_net(decl: Declarations) -> PetriNet:
    # Every declared name, looked up when reading the marking and the arcs.
    nodes: dict[str, Place | Transition] = {}

    places: set[Place] = set()
    for lineno, name in decl.places:
        _check_unused(name, nodes, lineno)
        place = Place(name)
        nodes[name] = place
        places.add(place)

    transitions: set[Transition] = set()
    for lineno, name in decl.transitions:
        _check_unused(name, nodes, lineno)
        transition = Transition(name)
        nodes[name] = transition
        transitions.add(transition)

    tokens: dict[Place, int] = {}
    for lineno, name, count in decl.tokens:
        place = nodes.get(name)
        if not isinstance(place, Place):
            raise ParseError(lineno, f"initial marking: {name!r} is not a declared place")
        if place in tokens:
            raise ParseError(lineno, f"initial marking for {name!r} given twice")
        tokens[place] = count

    arcs: set[Arc] = set()
    pairs_seen: set[tuple[str, str]] = set()
    for lineno, source, target, weight in decl.arcs:
        for name in (source, target):
            if name not in nodes:
                raise ParseError(lineno, f"arc ({source}, {target}): {name!r} is not a declared place or transition")
        if (source, target) in pairs_seen:
            raise ParseError(lineno, f"arc ({source}, {target}) given twice")
        pairs_seen.add((source, target))
        with _errors_at_line(lineno):
            arcs.add(_make_arc(nodes[source], nodes[target], weight, lineno))

    return PetriNet(places, transitions, arcs, Marking(tokens))


def _make_arc(source: Place | Transition, target: Place | Transition, weight: int, lineno: int) -> Arc:
    """(place, transition) becomes an InputArc, (transition, place) an OutputArc."""
    if isinstance(source, Place) and isinstance(target, Transition):
        return InputArc(source, target, weight)
    if isinstance(source, Transition) and isinstance(target, Place):
        return OutputArc(source, target, weight)
    kind = "place" if isinstance(source, Place) else "transition"
    raise ParseError(
        lineno,
        f"arc ({source}, {target}) connects two {kind}s; arcs must go place->transition or transition->place",
    )


def _check_unused(name: str, nodes: dict[str, Place | Transition], lineno: int) -> None:
    if name in nodes:
        kind = "place" if isinstance(nodes[name], Place) else "transition"
        raise ParseError(lineno, f"{name!r} already declared as a {kind}")


@contextmanager
def _errors_at_line(lineno: int):
    """Turn a PetriNetError into a ParseError that points at the line."""
    try:
        yield
    except PetriNetError as e:
        raise ParseError(lineno, str(e)) from None


# --- step 1: text -> declarations -----------------------------------------


def read_declarations(text: str) -> Declarations:
    decl = Declarations()

    section = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        m = KEYWORD_RE.match(line)
        if m:
            section = m.group(1)
            body = m.group(2)
        elif section is None:
            raise ParseError(lineno, f"expected 'places:', 'transitions:', 'initial:' or 'arcs:', got {line!r}")
        else:
            body = line  # continuation of the previous section

        if section == "places":
            for name in _names(body, lineno):
                decl.places.append((lineno, name))
        elif section == "transitions":
            for name in _names(body, lineno):
                decl.transitions.append((lineno, name))
        elif section == "initial":
            for mm in _scan(body, MARKING_RE, lineno, "marking (expected place=N)"):
                decl.tokens.append((lineno, mm.group(1), int(mm.group(2))))
        else:  # arcs
            for mm in _scan(body, ARC_RE, lineno, "arc (expected (x, y) or (x, y, weight))"):
                weight = int(mm.group(3)) if mm.group(3) else 1
                decl.arcs.append((lineno, mm.group(1), mm.group(2), weight))

    return decl


def _names(body: str, lineno: int) -> list[str]:
    """Split a list of names separated by spaces and/or commas."""
    names = []
    for name in re.split(r"[,\s]+", body.strip()):
        if not name:
            continue
        if not NAME_RE.fullmatch(name):
            raise ParseError(lineno, f"bad name {name!r}")
        names.append(name)
    return names


def _scan(body: str, pattern: re.Pattern, lineno: int, what: str) -> list[re.Match]:
    """Match `pattern` repeatedly over body, allowing only whitespace/commas between matches."""
    matches = []
    pos = 0
    for m in pattern.finditer(body):
        if body[pos:m.start()].strip(" \t,"):
            break
        matches.append(m)
        pos = m.end()
    rest = body[pos:].strip(" \t,")
    if rest:
        raise ParseError(lineno, f"bad {what} near {rest!r}")
    return matches
