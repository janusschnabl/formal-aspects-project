"""The classical place/transition Petri net, as defined in the course.

    N = ((P, T, F), m_0), optionally with accepting markings M_F

    P                               a finite set of places          -> set of Place
    T                               a finite set of transitions     -> set of Transition
    F ⊆ (P × T × w) ∪ (T × P × w)   the weighted arcs, w ∈ ℤ⁺       -> set of InputArc
                                                                       and OutputArc
    m_0 : P → ℕ                     the initial marking             -> Marking

Each class enforces its own part of the definition:

    Place, Transition   different types, so P ∩ T = ∅ automatically
    InputArc            (p, t, w) ∈ P × T × w, with w ≥ 1
    OutputArc           (t, p, w) ∈ T × P × w, with w ≥ 1
    Marking             maps places to natural numbers (unlisted places have 0)
    PetriNet            checks that the four parts fit together: every arc
                        uses places in P and transitions in T, there is at most
                        one arc per (x, y), and m_0 only marks places in P

We write

    W(x, y) = w   if (x, y, w) ∈ F,   and 0 otherwise
    •t = {p ∈ P | W(p, t) > 0}        (preset of t)
    t• = {p ∈ P | W(t, p) > 0}        (postset of t)

Execution semantics, for a marking m:

    t is enabled in m    iff   m(p) ≥ W(p, t) for every p ∈ •t
    firing t in m gives  m'    with  m'(p) = m(p) - W(p, t) + W(t, p)

Example:

    p1, p2 = Place("p1"), Place("p2")
    t = Transition("t")
    net = PetriNet(
        places={p1, p2},
        transitions={t},
        arcs={InputArc(p1, t), OutputArc(t, p2, weight=2)},
        initial_marking=Marking({p1: 1}),
    )
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


class PetriNetError(ValueError):
    """Raised when something would break the definition of a Petri net."""


def _is_natural_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_name(name: object, kind: str) -> None:
    if not isinstance(name, str) or not name:
        raise PetriNetError(f"a {kind} needs a non-empty name, got {name!r}")


# --- P and T ------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Place:
    """An element of P. Two places are the same place iff they have the same name."""

    name: str

    def __post_init__(self) -> None:
        _check_name(self.name, "place")

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, order=True)
class Transition:
    """An element of T. Two transitions are the same iff they have the same name."""

    name: str

    def __post_init__(self) -> None:
        _check_name(self.name, "transition")

    def __str__(self) -> str:
        return self.name


# --- F --------------------------------------------------------------------
#
# F ⊆ (P × T × w) ∪ (T × P × w) is a union of two kinds of triple, so there is
# one class for each kind. The field types say which side is the place and
# which is the transition, so an arc P -> P or T -> T cannot be written down.


@dataclass(frozen=True)
class InputArc:
    """(p, t, w) ∈ P × T × w: transition t takes w tokens from place p."""

    place: Place
    transition: Transition
    weight: int = 1

    def __post_init__(self) -> None:
        _check_arc(self.place, self.transition, self.weight, str(self))

    @property
    def source(self) -> Place:
        return self.place

    @property
    def target(self) -> Transition:
        return self.transition

    def __str__(self) -> str:
        return f"({self.place}, {self.transition}, {self.weight})"


@dataclass(frozen=True)
class OutputArc:
    """(t, p, w) ∈ T × P × w: transition t puts w tokens in place p."""

    transition: Transition
    place: Place
    weight: int = 1

    def __post_init__(self) -> None:
        _check_arc(self.place, self.transition, self.weight, str(self))

    @property
    def source(self) -> Transition:
        return self.transition

    @property
    def target(self) -> Place:
        return self.place

    def __str__(self) -> str:
        return f"({self.transition}, {self.place}, {self.weight})"


# An element of F.
Arc = InputArc | OutputArc


def _check_arc(place: object, transition: object, weight: object, arc: str) -> None:
    """Python does not check type hints when the program runs, so check them here."""
    if not isinstance(place, Place):
        raise PetriNetError(f"arc {arc}: {place!r} must be a Place")
    if not isinstance(transition, Transition):
        raise PetriNetError(f"arc {arc}: {transition!r} must be a Transition")
    if not _is_natural_number(weight) or weight == 0:
        raise PetriNetError(f"arc {arc}: weight must be a positive integer")


# --- markings -------------------------------------------------------------


class Marking:
    """A marking m : P → ℕ. Places that are not mentioned have 0 tokens.

    A Marking never changes after it is made (firing gives a new one), so
    markings can be compared with == and put in sets.
    """

    def __init__(self, tokens: dict[Place, int] | None = None) -> None:
        self._tokens: dict[Place, int] = {}  # only places with at least one token
        if tokens is None:
            return
        for place, count in tokens.items():
            if not isinstance(place, Place):
                raise PetriNetError(f"a marking gives tokens to places, but {place!r} is not a Place")
            if not _is_natural_number(count):
                raise PetriNetError(f"m({place}) must be a natural number, got {count!r}")
            if count > 0:
                self._tokens[place] = count

    def __getitem__(self, place: Place) -> int:
        """m(place)"""
        return self._tokens.get(place, 0)

    def marked_places(self) -> set[Place]:
        """The places with at least one token."""
        return set(self._tokens)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Marking) and self._tokens == other._tokens

    def __hash__(self) -> int:
        return hash(frozenset(self._tokens.items()))

    def __repr__(self) -> str:
        parts = []
        for place in sorted(self._tokens):
            parts.append(f"{place}: {self._tokens[place]}")
        return "Marking({" + ", ".join(parts) + "})"


# --- the net --------------------------------------------------------------


class PetriNet:
    """N = ((P, T, F), m_0), optionally with a set of final markings M_F.

    Final markings are acceptance conditions; they do not stop firing.
    """

    def __init__(
        self,
        places: set[Place],
        transitions: set[Transition],
        arcs: set[Arc],
        initial_marking: Marking | None = None,
        *,
        final_markings: Iterable[Marking] | None = None,
    ) -> None:
        self._places = frozenset(places)
        self._transitions = frozenset(transitions)
        self._arcs = frozenset(arcs)
        if initial_marking is None:
            initial_marking = Marking()
        self._initial_marking = initial_marking

        self._check_places_and_transitions()
        self._check_arcs()
        self._check_initial_marking()
        finals = []
        for marking in (() if final_markings is None else final_markings):
            self._check_marking(marking, "final marking")
            finals.append(marking)
        self._final_markings = frozenset(finals)

        # W, so that weight(x, y) does not have to search F every time.
        self._weights: dict[tuple[Place | Transition, Place | Transition], int] = {}
        for arc in self._arcs:
            self._weights[(arc.source, arc.target)] = arc.weight

    @property
    def places(self) -> frozenset[Place]:
        return self._places

    @property
    def transitions(self) -> frozenset[Transition]:
        return self._transitions

    @property
    def arcs(self) -> frozenset[Arc]:
        return self._arcs

    @property
    def initial_marking(self) -> Marking:
        return self._initial_marking

    @property
    def final_markings(self) -> frozenset[Marking]:
        return self._final_markings

    def is_final(self, marking: Marking) -> bool:
        """Whether marking is exactly one of M_F (unlisted places have zero)."""
        return marking in self._final_markings

    def _check_places_and_transitions(self) -> None:
        names_seen: set[str] = set()
        for place in self._places:
            if not isinstance(place, Place):
                raise PetriNetError(f"P may only contain places, but contains {place!r}")
            names_seen.add(place.name)
        for transition in self._transitions:
            if not isinstance(transition, Transition):
                raise PetriNetError(f"T may only contain transitions, but contains {transition!r}")
            # Place("a") and Transition("a") are different, but the same name
            # for both would make the net ambiguous to read (and to LoLA).
            if transition.name in names_seen:
                raise PetriNetError(f"{transition.name!r} is the name of both a place and a transition")

    def _check_arcs(self) -> None:
        pairs_seen: set[tuple[Place | Transition, Place | Transition]] = set()
        for arc in self._arcs:
            if not isinstance(arc, (InputArc, OutputArc)):
                raise PetriNetError(f"F may only contain arcs, but contains {arc!r}")
            if arc.place not in self._places:
                raise PetriNetError(f"arc {arc}: {arc.place} is not in P")
            if arc.transition not in self._transitions:
                raise PetriNetError(f"arc {arc}: {arc.transition} is not in T")
            pair = (arc.source, arc.target)
            if pair in pairs_seen:
                raise PetriNetError(f"F has more than one arc from {arc.source} to {arc.target}")
            pairs_seen.add(pair)

    def _check_initial_marking(self) -> None:
        self._check_marking(self._initial_marking, "m_0")

    def _check_marking(self, marking: Marking, description: str) -> None:
        if not isinstance(marking, Marking):
            raise PetriNetError(f"{description} must be a Marking, got {marking!r}")
        for place in marking.marked_places():
            if place not in self._places:
                raise PetriNetError(f"{description} gives tokens to {place}, which is not in P")

    def _check_transition(self, transition: Transition) -> None:
        if transition not in self._transitions:
            raise PetriNetError(f"{transition!r} is not in T")

    # --- W, presets and postsets -----------------------------------------

    def weight(self, source: Place | Transition, target: Place | Transition) -> int:
        """W(source, target): the weight of the arc, or 0 if there is none."""
        return self._weights.get((source, target), 0)

    def preset(self, transition: Transition) -> set[Place]:
        """•t: the places with an arc into the transition."""
        self._check_transition(transition)
        result: set[Place] = set()
        for arc in self._arcs:
            if isinstance(arc, InputArc) and arc.transition == transition:
                result.add(arc.place)
        return result

    def postset(self, transition: Transition) -> set[Place]:
        """t•: the places with an arc out of the transition."""
        self._check_transition(transition)
        result: set[Place] = set()
        for arc in self._arcs:
            if isinstance(arc, OutputArc) and arc.transition == transition:
                result.add(arc.place)
        return result

    # --- execution semantics ---------------------------------------------

    def is_enabled(self, marking: Marking, transition: Transition) -> bool:
        """m(p) >= W(p, t) for every p in •t."""
        for place in self.preset(transition):
            if marking[place] < self.weight(place, transition):
                return False
        return True

    def enabled_transitions(self, marking: Marking) -> set[Transition]:
        result: set[Transition] = set()
        for transition in self._transitions:
            if self.is_enabled(marking, transition):
                result.add(transition)
        return result

    def fire(self, marking: Marking, transition: Transition) -> Marking:
        """m'(p) = m(p) - W(p, t) + W(t, p). The transition must be enabled."""
        if not self.is_enabled(marking, transition):
            raise PetriNetError(f"transition {transition} is not enabled in {marking!r}")
        new_tokens: dict[Place, int] = {}
        for place in self._places:
            new_tokens[place] = marking[place] - self.weight(place, transition) + self.weight(transition, place)
        return Marking(new_tokens)
