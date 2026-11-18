"""Data model for multi-agent nets.

Follows "Formal Aspects of Process Science", section 1:

    N_a = (P_a, T_a, F_a, W_a, C_a, l_a)        one agent net per agent type a
    N   = ((A, {N_a | a in A}, L), M0, MF)      the complete net

The source structure is modelled here; convert() supplies execution semantics
through a standard P/T net for verification. Names of places and
transitions are global (agent nets are disjoint), so markings are dicts from
place name to token count, with missing places meaning 0 tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
from typing import Literal, Union

VAR: Literal["*"] = "*"
"""Weight of a variable arc (written * in the formalism)."""

Weight = Union[int, Literal["*"]]


@dataclass
class AgentNet:
    """N_a = (P_a, T_a, F_a, W_a, C_a, l_a)."""

    name: str
    places: list[str] = field(default_factory=list)                     # P_a
    transitions: list[str] = field(default_factory=list)                # T_a
    arcs: dict[tuple[str, str], Weight] = field(default_factory=dict)   # F_a with W_a
    read_arcs: list[tuple[str, str]] = field(default_factory=list)      # C_a, (place, transition)
    labels: dict[str, str | None] = field(default_factory=dict)         # l_a, None = epsilon (silent)

    def label(self, t: str) -> str | None:
        return self.labels.get(t)

    def is_silent(self, t: str) -> bool:
        return self.label(t) is None

    def preset(self, t: str) -> dict[str, Weight]:
        """Places t consumes from, with arc weights: {p: W(p, t)}."""
        return {p: w for (p, x), w in self.arcs.items() if x == t}

    def postset(self, t: str) -> dict[str, Weight]:
        """Places t produces into, with arc weights: {p: W(t, p)}."""
        return {p: w for (x, p), w in self.arcs.items() if x == t}

    def reads(self, t: str) -> list[str]:
        """Places t has a read arc from."""
        return [p for p, x in self.read_arcs if x == t]

    def has_variable_arcs(self, t: str) -> bool:
        return VAR in self.preset(t).values() or VAR in self.postset(t).values()


@dataclass(frozen=True)
class GlobalTransition:
    """An element g of G: a non-empty set of local transitions that fire together.

    `label` is the shared label, or None for a silent transition (then
    `members` has exactly one element). Members are (agent, transition) pairs.
    """

    label: str | None
    members: tuple[tuple[str, str], ...]

    @property
    def name(self) -> str:
        return self.label if self.label is not None else self.members[0][1]

    @property
    def transitions(self) -> tuple[str, ...]:
        return tuple(t for _, t in self.members)


@dataclass
class MultiAgentNet:
    """N = ((A, {N_a | a in A}, L), M0, MF)."""

    agents: dict[str, AgentNet] = field(default_factory=dict)   # A and the N_a
    initial: dict[str, int] = field(default_factory=dict)       # M0
    finals: list[dict[str, int]] = field(default_factory=list)  # MF; retain explicit zero entries
    unrestricted_finals: list[dict[str, int]] = field(default_factory=list)  # unlisted places unrestricted
    ctl_properties: dict[str, str] = field(default_factory=dict)

    # ---- structure -------------------------------------------------------

    @property
    def places(self) -> list[str]:
        """P = union of all P_a."""
        return list(chain.from_iterable(a.places for a in self.agents.values()))

    @property
    def transitions(self) -> list[str]:
        """All local transitions."""
        return list(chain.from_iterable(a.transitions for a in self.agents.values()))

    @property
    def labels(self) -> set[str]:
        """L, the non-silent labels in use."""
        return {l for a in self.agents.values() for l in a.labels.values() if l is not None}

    def owner(self, name: str) -> AgentNet:
        """The agent net a place or transition belongs to."""
        for a in self.agents.values():
            if name in a.places or name in a.transitions:
                return a
        raise KeyError(name)

    def global_transitions(self) -> list[GlobalTransition]:
        """G = {T_l | l in L, T_l nonempty} u {{t} | l_a(t) = epsilon}."""
        by_label: dict[str, list[tuple[str, str]]] = {}
        silent: list[GlobalTransition] = []
        for a in self.agents.values():
            for t in a.transitions:
                l = a.label(t)
                if l is None:
                    silent.append(GlobalTransition(None, ((a.name, t),)))
                else:
                    by_label.setdefault(l, []).append((a.name, t))
        return [GlobalTransition(l, tuple(m)) for l, m in by_label.items()] + silent

    # ---- well-formedness -------------------------------------------------

    def well_formedness_errors(self) -> list[str]:
        """Violations of the well-formedness conditions; empty list = well-formed."""
        errors = []
        for a in self.agents.values():
            for t in a.transitions:
                pre, post = a.preset(t), a.postset(t)
                var_in = sum(w == VAR for w in pre.values())
                var_out = sum(w == VAR for w in post.values())
                if var_in != var_out or var_in > 1:
                    errors.append(
                        f"{a.name}.{t}: needs at most one incoming and one outgoing variable arc, "
                        f"and both or neither (has {var_in} in, {var_out} out)"
                    )
                fixed_in = sum(w for w in pre.values() if w != VAR)
                fixed_out = sum(w for w in post.values() if w != VAR)
                if fixed_in != fixed_out:
                    errors.append(
                        f"{a.name}.{t}: does not preserve agents (consumes {fixed_in}, produces {fixed_out})"
                    )
        for g in self.global_transitions():
            if not any(self.owner(t).preset(t) for t in g.transitions):
                errors.append(f"global transition {g.name}: has no incoming arc, so it consumes nothing")
        return errors

    def is_well_formed(self) -> bool:
        return not self.well_formedness_errors()
