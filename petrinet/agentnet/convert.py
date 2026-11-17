"""Expand a token-preserving multi-agent net into a weighted P/T net.

Each local variable-arc pair is bounded by that agent type's initial population.
No reachability analysis or pruning is performed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import product
import re

from ..names import validate_agent_name
from ..net import Arc, InputArc, Marking, OutputArc, PetriNet, Place, Transition
from .model import VAR, AgentNet, GlobalTransition, MultiAgentNet


class ConversionError(ValueError):
    """The source does not satisfy the requirements for bounded expansion."""


@dataclass(frozen=True)
class TransitionOrigin:
    global_transition: GlobalTransition
    # ((agent name, local transition name), chosen weight), in sorted order.
    multiplicities: tuple[tuple[tuple[str, str], int], ...] = ()

    @property
    def display_name(self) -> str:
        name = self.global_transition.name
        if not self.multiplicities:
            return name
        choices = ", ".join(f"{a}.{t}={k}" for (a, t), k in self.multiplicities)
        return f"{name}\n{choices}"


@dataclass(frozen=True)
class ConversionResult:
    net: PetriNet
    place_map: dict[str, Place]
    transition_origins: dict[Transition, TransitionOrigin]


def _name(name: str) -> None:
    try:
        validate_agent_name(name)
    except ValueError as e:
        raise ConversionError(str(e)) from None


def _validate(source: MultiAgentNet) -> None:
    """Check mutable/programmatically built models as well as parsed ones."""
    if not isinstance(source, MultiAgentNet):
        raise ConversionError("source must be a MultiAgentNet")
    nodes: set[str] = set()
    places: set[str] = set()
    for key, agent in source.agents.items():
        if not isinstance(agent, AgentNet):
            raise ConversionError(f"{key!r} is not an AgentNet")
        if key != agent.name or not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConversionError(f"agent key {key!r} must match its valid agent name")
        for name in [*agent.places, *agent.transitions]:
            _name(name)
            if name in nodes:
                raise ConversionError(f"node {name!r} declared more than once")
            nodes.add(name)
        places.update(agent.places)
        local_places, local_transitions = set(agent.places), set(agent.transitions)
        labels: set[str] = set()
        for transition, label in agent.labels.items():
            if transition not in local_transitions:
                raise ConversionError(f"{agent.name}: label on undeclared transition {transition!r}")
            if label is not None:
                _name(label)
                if label in labels:
                    raise ConversionError(f"{agent.name}: label {label!r} used twice")
                labels.add(label)
        for ends, weight in agent.arcs.items():
            if not isinstance(ends, tuple) or len(ends) != 2:
                raise ConversionError(f"{agent.name}: invalid arc {ends!r}")
            src, dst = ends
            if not ((src in local_places and dst in local_transitions)
                    or (src in local_transitions and dst in local_places)):
                raise ConversionError(f"{agent.name}: arc {ends!r} must connect a local place and transition")
            if weight != VAR and (not isinstance(weight, int) or isinstance(weight, bool) or weight < 1):
                raise ConversionError(f"{agent.name}: arc {ends!r} weight must be a positive integer or '*'")
        reads: set[tuple[str, str]] = set()
        for ends in agent.read_arcs:
            if not isinstance(ends, tuple) or len(ends) != 2:
                raise ConversionError(f"{agent.name}: invalid read arc {ends!r}")
            p, t = ends
            if p not in local_places or t not in local_transitions:
                raise ConversionError(f"{agent.name}: read arc {ends!r} must connect a local place to a transition")
            if ends in agent.arcs or ends in reads:
                raise ConversionError(f"{agent.name}: overlapping or duplicate read arc {ends!r}")
            reads.add(ends)
    for description, marking in [("initial", source.initial), *[("final", m) for m in source.finals]]:
        if not isinstance(marking, dict):
            raise ConversionError(f"{description} marking must map place names to token counts")
        for p, count in marking.items():
            if p not in places:
                raise ConversionError(f"{description} marking: {p!r} is not a declared place")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ConversionError(f"{description} marking: tokens at {p!r} must be a natural number")
    errors = source.well_formedness_errors()
    if errors:
        raise ConversionError("net is not well-formed: " + "; ".join(errors))


def _unique_name(base: str, used: set[str]) -> str:
    name, suffix = base, 2
    while name in used:
        name = f"{base}__{suffix}"
        suffix += 1
    used.add(name)
    return name


def convert(source: MultiAgentNet) -> ConversionResult:
    """Convert without renaming places, removing transitions, or changing source."""
    _validate(source)
    place_map = {name: Place(name) for name in sorted(source.places)}
    bounds = {a.name: sum(source.initial.get(p, 0) for p in a.places) for a in source.agents.values()}
    transitions: set[Transition] = set()
    arcs: set[Arc] = set()
    origins: dict[Transition, TransitionOrigin] = {}
    used_names = set(place_map)
    globals_ = sorted(source.global_transitions(), key=lambda g: (g.name, g.label is None, tuple(sorted(g.members))))

    for global_transition in globals_:
        variables = sorted(
            (a, t) for a, t in global_transition.members if source.agents[a].has_variable_arcs(t)
        )
        for weights in product(*(range(1, bounds[a] + 1) for a, _ in variables)):
            choices = dict(zip(variables, weights))
            base = global_transition.name + "".join(f"__k_{a}_{k}" for (a, _), k in choices.items())
            transition = Transition(_unique_name(base, used_names))
            transitions.add(transition)
            origins[transition] = TransitionOrigin(global_transition, tuple(choices.items()))
            pre: Counter[Place] = Counter()
            post: Counter[Place] = Counter()
            for agent_name, local in global_transition.members:
                agent = source.agents[agent_name]
                for p, w in agent.preset(local).items():
                    pre[place_map[p]] += choices[(agent_name, local)] if w == VAR else w
                for p, w in agent.postset(local).items():
                    post[place_map[p]] += choices[(agent_name, local)] if w == VAR else w
                for p in agent.reads(local):
                    pre[place_map[p]] += 1
                    post[place_map[p]] += 1
            arcs.update(InputArc(p, transition, w) for p, w in pre.items())
            arcs.update(OutputArc(transition, p, w) for p, w in post.items())

    def marking(tokens: dict[str, int]) -> Marking:
        return Marking({place_map[p]: n for p, n in tokens.items()})

    net = PetriNet(
        set(place_map.values()), transitions, arcs, marking(source.initial),
        final_markings=[marking(m) for m in source.finals],
    )
    return ConversionResult(net, place_map, origins)
