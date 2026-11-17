"""Parser for the multi-agent net text format.

    index: K = 1..3, S = {north, south}      # named index sets

    agent: Customer                          # starts the agent net N_Customer
      places: home, waiting, served          # P_a
      transitions: arrive, leave, buy[k] for k in K    # T_a
      labels: buy[k]=serve[k] for k in K     # l_a; unlisted transitions are silent
      arcs: (home, arrive, *) (arrive, waiting, *)     # F_a with W_a:
            (waiting, buy[k]) (buy[k], served) for k in K  #   weight 1, k > 1, or *
      read: (served, leave)                  # C_a, read arcs (place, transition)

    initial: home=3                          # M0, unlisted places are 0
    final: served=3                          # one marking of MF per 'final:'

Rules:
  - '#' starts a comment. A line without a keyword continues the previous section.
  - `for v in SET, w in SET2` at the end of a line repeats everything on that line
    for each combination of values. SET is `a..b`, `{x, y, z}` or a name from `index:`.
    Inside brackets, `v`, `v+1` and `v-1` are replaced by the value.
    Multiple indices use repeated brackets, e.g. q[i][j], never q[i,j].
    Expanded names must be valid, non-reserved LoLA identifiers.
  - `places`, `transitions`, `labels`, `arcs`, `read` belong to the most recent `agent:`.
"""

from __future__ import annotations

import itertools
import re

from ..names import validate_lola_name
from .model import VAR, AgentNet, MultiAgentNet, Weight

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
NAME = rf"{IDENT}(?:\[[^\]\[]*\])*"          # p, p[i], or p[i][j]
KEYWORDS = ("agent", "index", "places", "transitions", "labels", "arcs", "read", "initial", "final")
AGENT_SECTIONS = ("places", "transitions", "labels", "arcs", "read")
SILENT = ("eps", "epsilon", "ε", "tau")

KEYWORD_RE = re.compile(rf"^({'|'.join(KEYWORDS)})\s*:(.*)$")
FOR_RE = re.compile(r"\bfor\b(.*)$")
BINDING_RE = re.compile(rf"({IDENT})\s+in\s+(\{{[^}}]*\}}|-?\d+\s*\.\.\s*-?\d+|{IDENT})")
INDEX_DEF_RE = re.compile(rf"({IDENT})\s*=\s*(\{{[^}}]*\}}|-?\d+\s*\.\.\s*-?\d+)")
BRACKET_RE = re.compile(r"\[([^\]\[]*)\]")
ARC_RE = re.compile(rf"\(\s*({NAME})\s*,\s*({NAME})\s*(?:,\s*(\d+|\*)\s*)?\)")
READ_RE = re.compile(rf"\(\s*({NAME})\s*,\s*({NAME})\s*\)")
ASSIGN_RE = re.compile(rf"({NAME})\s*=\s*(\d+)")
LABEL_RE = re.compile(rf"({NAME})\s*=\s*({NAME}|ε)")
NAME_LIST_RE = re.compile(NAME)


class ParseError(ValueError):
    def __init__(self, lineno: int, msg: str):
        super().__init__(f"line {lineno}: {msg}" if lineno else msg)
        self.lineno = lineno


# ---- index sets and `for` expansion ---------------------------------------

IndexValue = int | str


def _parse_set(text: str, indices: dict[str, list[IndexValue]], lineno: int) -> list[IndexValue]:
    text = text.strip()
    if m := re.fullmatch(r"(-?\d+)\s*\.\.\s*(-?\d+)", text):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise ParseError(lineno, f"empty range {text}")
        return list(range(lo, hi + 1))
    if text.startswith("{"):
        items = [s.strip() for s in text[1:-1].split(",") if s.strip()]
        if not items:
            raise ParseError(lineno, "empty index set {}")
        out: list[IndexValue] = []
        for s in items:
            if re.fullmatch(r"-?\d+", s):
                out.append(int(s))
            elif re.fullmatch(IDENT, s):
                out.append(s)
            else:
                raise ParseError(lineno, f"bad index value {s!r}")
        return out
    if text in indices:
        return indices[text]
    raise ParseError(lineno, f"unknown index set {text!r} (define it with 'index: {text} = ...')")


def _substitute(body: str, env: dict[str, IndexValue], lineno: int) -> str:
    """Replace index expressions inside [...] using env, and normalise spacing."""

    def component(c: str) -> str:
        if "," in c:
            raise ParseError(lineno, "commas are not allowed inside names; use q[i][j] instead of q[i,j]")
        c = c.strip()
        m = re.fullmatch(rf"({IDENT})\s*(?:([+-])\s*(\d+))?", c)
        if m and m.group(1) in env:
            v = env[m.group(1)]
            if m.group(2):
                if not isinstance(v, int):
                    raise ParseError(lineno, f"cannot do arithmetic on non-numeric index {m.group(1)}={v!r}")
                v = v + int(m.group(3)) if m.group(2) == "+" else v - int(m.group(3))
            return str(v)
        if re.fullmatch(rf"-?\d+|{IDENT}", c):
            return c
        raise ParseError(lineno, f"bad index expression {c!r}")

    return BRACKET_RE.sub(lambda m: "[" + component(m.group(1)) + "]", body)


def _expand(body: str, indices: dict[str, list[IndexValue]], lineno: int) -> list[str]:
    """Split off a trailing `for ...` clause and return the body once per binding."""
    m = FOR_RE.search(body)
    if not m:
        return [_substitute(body, {}, lineno)]
    clause, body = m.group(1), body[: m.start()]
    bindings, pos = [], 0
    for b in BINDING_RE.finditer(clause):
        if clause[pos:b.start()].strip(" \t,"):
            break
        bindings.append((b.group(1), _parse_set(b.group(2), indices, lineno)))
        pos = b.end()
    if not bindings or clause[pos:].strip(" \t,"):
        raise ParseError(lineno, f"bad for-clause 'for{clause}' (expected 'for v in SET, ...')")
    names = [v for v, _ in bindings]
    if len(set(names)) != len(names):
        raise ParseError(lineno, "same variable bound twice in for-clause")
    return [
        _substitute(body, dict(zip(names, combo)), lineno)
        for combo in itertools.product(*(vals for _, vals in bindings))
    ]


def _scan(body: str, pattern: re.Pattern, lineno: int, what: str) -> list[re.Match]:
    """Match pattern repeatedly over body; only whitespace/commas may separate matches."""
    matches, pos = [], 0
    for m in pattern.finditer(body):
        if body[pos:m.start()].strip(" \t,"):
            break
        matches.append(m)
        pos = m.end()
    rest = body[pos:].strip(" \t,")
    if rest:
        raise ParseError(lineno, f"bad {what} near {rest!r}")
    return matches


def _unique(items: list, expanded: bool) -> list:
    """Drop repeats produced by expanding a for-clause over items that don't use the variable."""
    if not expanded:
        return items
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _hint(name: str) -> str:
    if any(re.fullmatch(IDENT, m.group(1)) for m in BRACKET_RE.finditer(name)):
        return " (missing a 'for ... in ...' clause on this line? it only applies to its own line)"
    return ""


def _check_lola_name(name: str, lineno: int) -> None:
    try:
        validate_lola_name(name)
    except ValueError as e:
        raise ParseError(lineno, str(e)) from None


# ---- main parser ------------------------------------------------------------

def parse(text: str) -> MultiAgentNet:
    indices: dict[str, list[IndexValue]] = {}
    agents: dict[str, AgentNet] = {}
    kind: dict[str, tuple[str, str]] = {}          # name -> ("place"|"transition", agent)
    arc_lines: list[tuple[int, AgentNet, str, str, Weight]] = []
    read_lines: list[tuple[int, AgentNet, str, str]] = []
    label_lines: list[tuple[int, AgentNet, str, str | None]] = []
    initial_lines: list[tuple[int, str, int]] = []
    final_lines: list[list[tuple[int, str, int]]] = []

    agent: AgentNet | None = None
    section: str | None = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = KEYWORD_RE.match(line)
        if m:
            section, body = m.group(1), m.group(2).strip()
            if section == "final":
                final_lines.append([])
        elif section is None:
            raise ParseError(lineno, f"expected a keyword ({', '.join(k + ':' for k in KEYWORDS)}), got {line!r}")
        else:
            body = line

        if section == "agent":
            if not re.fullmatch(IDENT, body):
                raise ParseError(lineno, f"bad agent name {body!r}")
            if body in agents:
                raise ParseError(lineno, f"agent {body!r} defined twice")
            agent = agents[body] = AgentNet(body)
            section = "agent-header"
            continue
        if section == "agent-header":
            raise ParseError(lineno, f"unexpected {line!r} after 'agent:' line")
        if section == "index":
            for mm in _scan(body, INDEX_DEF_RE, lineno, "index definition (expected NAME = a..b or NAME = {x, y})"):
                if mm.group(1) in indices:
                    raise ParseError(lineno, f"index set {mm.group(1)!r} defined twice")
                indices[mm.group(1)] = _parse_set(mm.group(2), indices, lineno)
            continue
        if section in AGENT_SECTIONS and agent is None:
            raise ParseError(lineno, f"'{section}:' must come after an 'agent:' line")

        bodies = _expand(body, indices, lineno)

        if section in ("places", "transitions"):
            names = _unique([n.group(0) for b in bodies for n in _scan(b, NAME_LIST_RE, lineno, "name")], len(bodies) > 1)
            what = "place" if section == "places" else "transition"
            for n in names:
                _check_lola_name(n, lineno)
                if n in kind:
                    k, a = kind[n]
                    raise ParseError(lineno, f"{n!r} already declared as a {k} of agent {a}")
                kind[n] = (what, agent.name)
                (agent.places if what == "place" else agent.transitions).append(n)

        elif section == "labels":
            for t, l in _unique([(m.group(1), m.group(2)) for b in bodies for m in _scan(b, LABEL_RE, lineno, "label (expected transition=label)")], len(bodies) > 1):
                if l not in SILENT:
                    _check_lola_name(l, lineno)
                label_lines.append((lineno, agent, t, None if l in SILENT else l))

        elif section == "arcs":
            for src, dst, w in _unique([(m.group(1), m.group(2), m.group(3)) for b in bodies for m in _scan(b, ARC_RE, lineno, "arc (expected (x, y), (x, y, n) or (x, y, *))")], len(bodies) > 1):
                arc_lines.append((lineno, agent, src, dst, VAR if w == "*" else int(w or 1)))

        elif section == "read":
            for p, t in _unique([(m.group(1), m.group(2)) for b in bodies for m in _scan(b, READ_RE, lineno, "read arc (expected (place, transition))")], len(bodies) > 1):
                read_lines.append((lineno, agent, p, t))

        elif section in ("initial", "final"):
            entries = [(lineno, m.group(1), int(m.group(2))) for b in bodies for m in _scan(b, ASSIGN_RE, lineno, "marking entry (expected place=N)")]
            (initial_lines if section == "initial" else final_lines[-1]).extend(_unique(entries, len(bodies) > 1))

    # ---- semantic checks --------------------------------------------------

    def must_be(lineno: int, name: str, what: str, a: AgentNet | None, ctx: str) -> None:
        if name not in kind:
            raise ParseError(lineno, f"{ctx}: {name!r} is not declared{_hint(name)}")
        k, owner = kind[name]
        if k != what:
            raise ParseError(lineno, f"{ctx}: {name!r} is a {k}, expected a {what}")
        if a is not None and owner != a.name:
            raise ParseError(lineno, f"{ctx}: {name!r} belongs to agent {owner}, not {a.name}")

    for lineno, a, t, l in label_lines:
        must_be(lineno, t, "transition", a, "label")
        if t in a.labels:
            raise ParseError(lineno, f"transition {t!r} labelled twice")
        if l is not None and l in a.labels.values():
            other = next(x for x, y in a.labels.items() if y == l)
            raise ParseError(lineno, f"label {l!r} used twice in agent {a.name} ({other} and {t})")
        a.labels[t] = l
    for a in agents.values():
        for t in a.transitions:
            a.labels.setdefault(t, None)

    for lineno, a, src, dst, w in arc_lines:
        ctx = f"arc ({src}, {dst})"
        for n in (src, dst):
            if n not in kind:
                raise ParseError(lineno, f"{ctx}: {n!r} is not declared{_hint(n)}")
            must_be(lineno, n, kind[n][0], a, ctx)
        if kind[src][0] == kind[dst][0]:
            raise ParseError(lineno, f"{ctx} connects two {kind[src][0]}s")
        if (src, dst) in a.arcs:
            raise ParseError(lineno, f"{ctx} given twice")
        if w != VAR and w < 1:
            raise ParseError(lineno, f"{ctx}: weight must be a positive integer")
        a.arcs[(src, dst)] = w

    for lineno, a, p, t in read_lines:
        ctx = f"read arc ({p}, {t})"
        must_be(lineno, p, "place", a, ctx)
        must_be(lineno, t, "transition", a, ctx)
        if (p, t) in a.read_arcs:
            raise ParseError(lineno, f"{ctx} given twice")
        if (p, t) in a.arcs:
            raise ParseError(lineno, f"{ctx} is also an ordinary arc (read arcs and arcs must be disjoint)")
        a.read_arcs.append((p, t))

    def marking(lines: list[tuple[int, str, int]], ctx: str) -> dict[str, int]:
        m: dict[str, int] = {}
        seen: set[str] = set()
        for lineno, p, n in lines:
            must_be(lineno, p, "place", None, ctx)
            if p in seen:
                raise ParseError(lineno, f"{ctx}: {p!r} given twice")
            seen.add(p)
            if n:
                m[p] = n
        return m

    return MultiAgentNet(
        agents=agents,
        initial=marking(initial_lines, "initial marking"),
        finals=[marking(f, "final marking") for f in final_lines],
    )


def load(path: str) -> MultiAgentNet:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())
