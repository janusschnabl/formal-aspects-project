"""Conversion is checked against direct firing of source global transitions."""

from collections import deque
from copy import deepcopy
from itertools import product
import os
import unittest

from petrinet import Marking, Place
from petrinet import explore, tina
from petrinet.agentnet import VAR, ConversionError, convert, load, parse


EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples", "agents")
PAIRS = """agent: A
 places: p q
 transitions: move
 labels: move=go
 arcs: (p, move, *) (move, q, *)
agent: B
 places: r s
 transitions: shift
 labels: shift=go
 arcs: (r, shift, *) (shift, s, *)
initial: p=3 r=2
final: q=3 s=2
"""
INDEXED = """agent: A
 places: p[-1][north] p[0][north]
 transitions: move[-1][north]
 labels: move[-1][north]=go[-1][north]
 arcs: (p[-1][north], move[-1][north], *) (move[-1][north], p[0][north], *)
initial: p[-1][north]=2
final: p[0][north]=2
final:
"""


def source_steps(source, tokens):
    """Evaluate the source rule directly, choosing k from CURRENT input tokens.

    This does not construct P/T arcs or use the converter's population bounds.
    Read requirements are checked independently of consumption and production.
    """
    for g in source.global_transitions():
        variables = sorted((a, t) for a, t in g.members if source.agents[a].has_variable_arcs(t))
        domains = []
        for a, t in variables:
            variable_input = next(p for p, w in source.agents[a].preset(t).items() if w == VAR)
            domains.append(range(1, tokens[variable_input] + 1))
        for weights in product(*domains):
            choices = dict(zip(variables, weights))
            next_tokens = tokens.copy()
            enabled = True
            for a, t in g.members:
                agent = source.agents[a]
                if any(tokens[p] < 1 for p in agent.reads(t)):
                    enabled = False
                for p, w in agent.preset(t).items():
                    count = choices[(a, t)] if w == VAR else w
                    if tokens[p] < count:
                        enabled = False
                    next_tokens[p] -= count
                for p, w in agent.postset(t).items():
                    next_tokens[p] += choices[(a, t)] if w == VAR else w
            if enabled:
                yield g, tuple(choices.items()), next_tokens


def source_graph(source):
    names = sorted(source.places)
    initial = tuple(source.initial.get(p, 0) for p in names)
    seen, edges = {initial}, set()
    queue = deque([initial])
    while queue:
        before = queue.popleft()
        for g, choices, tokens in source_steps(source, dict(zip(names, before))):
            after = tuple(tokens[p] for p in names)
            edges.add((before, g, choices, after))
            if after not in seen:
                seen.add(after)
                queue.append(after)
    return seen, edges


def converted_graph(result):
    net = result.net
    places = sorted(net.places)
    queue = deque([net.initial_marking])
    seen, edges = {net.initial_marking}, set()
    while queue:
        marking = queue.popleft()
        before = tuple(marking[p] for p in places)
        for t in net.enabled_transitions(marking):
            successor = net.fire(marking, t)
            origin = result.transition_origins[t]
            after = tuple(successor[p] for p in places)
            edges.add((before, origin.global_transition, origin.multiplicities, after))
            if successor not in seen:
                seen.add(successor)
                queue.append(successor)
    return {tuple(m[p] for p in places) for m in seen}, edges


class ConversionTests(unittest.TestCase):
    def test_synchronized_example(self):
        source = load(os.path.join(EXAMPLES, "example.txt"))
        result = convert(source)
        net = result.net
        self.assertEqual((len(net.places), len(net.transitions), len(net.arcs)), (4, 1, 4))
        t, = net.transitions
        self.assertEqual(t.name, "dukOp")
        self.assertEqual(result.transition_origins[t].global_transition.transitions, ("dukOp1", "dukOp2"))
        self.assertTrue(net.is_final(net.fire(net.initial_marking, t)))

    def test_shop_uses_per_type_bounds_and_preserves_read_tokens(self):
        source = load(os.path.join(EXAMPLES, "shop.txt"))
        result = convert(source)
        net = result.net
        self.assertEqual((len(net.places), len(net.transitions), len(net.arcs)), (7, 12, 28))
        enters = [t for t, o in result.transition_origins.items() if o.global_transition.name == "enter"]
        self.assertEqual(len(enters), 3)
        self.assertEqual({net.weight(Place("home"), t) for t in enters}, {1, 2, 3})
        enter_one = next(t for t in enters if net.weight(Place("home"), t) == 1)
        marking = net.fire(net.initial_marking, enter_one)
        serve = next(t for t, o in result.transition_origins.items() if o.global_transition.name == "serve[1]")
        after = net.fire(marking, serve)
        self.assertEqual(after[Place("open[1]")], 1)
        self.assertEqual(after[Place("done")], 1)
        self.assertEqual(after[Place("queue")], 0)
        self.assertTrue(net.is_final(net.initial_marking))

    def test_independent_pairs_generate_cartesian_product(self):
        result = convert(parse(PAIRS))
        net = result.net
        self.assertEqual(len(net.transitions), 6)
        self.assertEqual({tuple(k for _, k in o.multiplicities) for o in result.transition_origins.values()},
                         set(product(range(1, 4), range(1, 3))))
        for t, origin in result.transition_origins.items():
            a, b = (k for _, k in origin.multiplicities)
            self.assertEqual((net.weight(Place("p"), t), net.weight(t, Place("q"))), (a, a))
            self.assertEqual((net.weight(Place("r"), t), net.weight(t, Place("s"))), (b, b))
            self.assertIn("A.move=", origin.display_name)
            self.assertIn("B.shift=", origin.display_name)

    def test_place_names_and_acceptance_are_preserved(self):
        source = parse(INDEXED)
        snapshot = deepcopy(source)
        result = convert(source)
        self.assertEqual(source, snapshot)
        self.assertEqual({p.name for p in result.net.places}, set(source.places))
        self.assertEqual(result.place_map, {name: Place(name) for name in source.places})
        self.assertEqual(result.net.initial_marking, Marking({Place("p[-1][north]"): 2}))
        self.assertEqual(result.net.final_markings,
                         frozenset({Marking(), Marking({Place("p[0][north]"): 2})}))
        exported = tina.to_tina(result.net)
        self.assertIn("pl {p[-1][north]} (2)", exported)
        self.assertIn("pl {p[0][north]} (0)", exported)
        self.assertIn("go[-1][north]__k_A_2", exported)

    def test_read_arc_adds_to_existing_output(self):
        result = convert(parse(
            "agent: A\n places: p q\n transitions: t\n"
            " arcs: (q, t) (t, p)\n read: (p, t)\n initial: p=1 q=1\n"
        ))
        net = result.net
        t, = net.transitions
        self.assertEqual(net.weight(Place("p"), t), 1)
        self.assertEqual(net.weight(t, Place("p")), 2)
        self.assertFalse(net.is_enabled(Marking({Place("q"): 1}), t))
        self.assertEqual(net.fire(net.initial_marking, t), Marking({Place("p"): 2}))

    def test_dead_variants_and_fixed_transitions_are_not_pruned(self):
        result = convert(parse(
            "agent: A\n places: p q r s\n transitions: t dead\n"
            " arcs: (p, t, *) (t, s, *) (q, t) (t, r) (r, dead, 4) (dead, q, 4)\n"
            " initial: p=2 q=1\n"
        ))
        self.assertEqual(len(result.net.transitions), 4)  # all k=1..3 plus fixed 'dead'
        self.assertEqual({t.name for t in explore.dead_transitions(result.net)}, {"dead", "t__k_A_3"})

    def test_zero_bound_and_empty_source(self):
        source = parse("agent: A\n places: p q\n transitions: t\n arcs: (p, t, *) (t, q, *)")
        result = convert(source)
        self.assertEqual(result.net.transitions, frozenset())
        self.assertEqual(result.transition_origins, {})
        self.assertEqual(len(result.net.places), 2)
        self.assertEqual(convert(parse("")).net.places, frozenset())
        fixed = convert(parse("agent: A\n places: p q\n transitions: t\n arcs: (p, t) (t, q)"))
        self.assertEqual(len(fixed.net.transitions), 1)

    def test_transition_collisions_never_rename_places(self):
        source = parse(
            "agent: A\n places: join p q\n transitions: local join__2\n labels: local=join\n"
            " arcs: (join, local) (local, p) (p, join__2) (join__2, q)\n initial: join=1\n"
        )
        first, second = convert(source), convert(source)
        self.assertEqual(first.net.places, {Place(p) for p in source.places})
        self.assertEqual({t.name for t in first.net.transitions}, {"join__2", "join__2__2"})
        self.assertEqual(tina.to_tina(first.net), tina.to_tina(second.net))

    def test_global_label_can_match_a_silent_transition(self):
        source = parse(
            "agent: A\n places: p q\n transitions: move go\n labels: move=go\n"
            " arcs: (p, move) (move, q) (q, go) (go, p)\n initial: p=1\n"
        )
        result = convert(source)
        self.assertEqual({t.name for t in result.net.transitions}, {"go", "go__2"})
        self.assertEqual({o.global_transition.label for o in result.transition_origins.values()}, {"go", None})

    def test_reachable_graphs_match_direct_source_semantics(self):
        sources = [parse(PAIRS), parse(INDEXED), load(os.path.join(EXAMPLES, "shop.txt")), parse(
            "agent: A\n places: p q r s\n transitions: mix\n"
            " arcs: (p, mix, *) (mix, q, *) (r, mix) (mix, s)\n"
            " read: (q, mix)\n initial: p=2 q=1 r=1\n"
        )]
        for source in sources:
            with self.subTest(places=source.places):
                result = convert(source)
                markings, edges = converted_graph(result)
                self.assertEqual((markings, edges), source_graph(source))
                names = sorted(source.places)
                for marking in markings:
                    tokens = dict(zip(names, marking))
                    for agent in source.agents.values():
                        self.assertEqual(sum(tokens[p] for p in agent.places),
                                         sum(source.initial.get(p, 0) for p in agent.places))

    def test_programmatic_models_are_validated_before_expansion(self):
        def arc(weight):
            return lambda n: n.agents["A"].arcs.update({("p", "move"): weight})

        changes = [
            arc(0), arc(-1), arc(True), arc(1.5), arc("bad"), arc(1),
            lambda n: n.agents["A"].places.append("q[1,a]"),
            lambda n: n.agents["A"].places.append(""),
            lambda n: n.agents["A"].places.append("p"),
            lambda n: n.agents["A"].places.append("r"),
            lambda n: setattr(n.agents["A"], "name", "wrong"),
            lambda n: n.agents["A"].labels.update({"missing": "go"}),
            lambda n: n.agents["A"].labels.update({"move": "bad label"}),
            lambda n: n.agents["A"].arcs.update({("p", "q"): 1}),
            lambda n: n.agents["A"].arcs.update({("r", "move"): 1}),
            lambda n: n.agents["A"].read_arcs.append(("p", "move")),
            lambda n: n.agents["A"].read_arcs.append(("r", "move")),
            lambda n: n.agents["A"].read_arcs.extend([("q", "move"), ("q", "move")]),
            lambda n: n.initial.update({"p": True}),
            lambda n: n.initial.update({"missing": 0}),
            lambda n: n.finals.append({"p": -1}),
            lambda n: n.finals.append({"missing": 0}),
        ]
        for i, change in enumerate(changes):
            with self.subTest(change=i):
                source = parse(PAIRS)
                change(source)
                with self.assertRaises(ConversionError):
                    convert(source)
        with self.assertRaises(ConversionError):
            convert(parse("agent: A\n places: p\n transitions: t\n read: (p, t)"))


if __name__ == "__main__":
    unittest.main()
