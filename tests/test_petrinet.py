import os
import unittest

from petrinet import InputArc, Marking, OutputArc, ParseError, PetriNet, PetriNetError, Place, Transition, parse
from petrinet import explore, tina

HERE = os.path.dirname(__file__)
EXAMPLE = os.path.join(HERE, "nets", "two_dead.txt")


def load_example() -> PetriNet:
    with open(EXAMPLE) as f:
        return parse(f.read())


class ElementTests(unittest.TestCase):
    """Place, Transition, Arc and Marking each enforce their part of the definition."""

    def test_same_name_same_element(self):
        self.assertEqual(Place("p"), Place("p"))
        self.assertEqual(len({Place("p"), Place("p")}), 1)
        self.assertNotEqual(Place("x"), Transition("x"))  # P ∩ T = ∅

    def test_arcs(self):
        p, t = Place("p"), Transition("t")
        self.assertEqual((InputArc(p, t).source, InputArc(p, t).target), (p, t))
        self.assertEqual((OutputArc(t, p).source, OutputArc(t, p).target), (t, p))
        self.assertEqual(InputArc(p, t).weight, 1)
        self.assertNotEqual(InputArc(p, t), OutputArc(t, p))

    def test_bad_elements(self):
        p, q, t, u = Place("p"), Place("q"), Transition("t"), Transition("u")
        cases = {
            "empty name": lambda: Place(""),
            "InputArc P -> P": lambda: InputArc(p, q),
            "InputArc T -> T": lambda: InputArc(t, u),
            "InputArc the wrong way round": lambda: InputArc(t, p),
            "OutputArc P -> P": lambda: OutputArc(p, q),
            "OutputArc T -> T": lambda: OutputArc(t, u),
            "OutputArc the wrong way round": lambda: OutputArc(p, t),
            "arc from a string": lambda: InputArc("p", t),
            "weight 0": lambda: InputArc(p, t, 0),
            "weight not an int": lambda: OutputArc(t, p, 1.5),
            "negative tokens": lambda: Marking({p: -1}),
            "tokens on a transition": lambda: Marking({t: 1}),
        }
        for description, make in cases.items():
            with self.subTest(description), self.assertRaises(PetriNetError):
                make()

    def test_marking(self):
        p, q = Place("p"), Place("q")
        m = Marking({p: 2, q: 0})
        self.assertEqual((m[p], m[q], m[Place("other")]), (2, 0, 0))
        self.assertEqual(m, Marking({p: 2}))  # 0 tokens = not mentioned
        self.assertEqual(len({m, Marking({p: 2})}), 1)


class NetTests(unittest.TestCase):
    def setUp(self):
        # t needs 2 tokens in a and 1 in b, and puts 3 in c.
        self.a, self.b, self.c = Place("a"), Place("b"), Place("c")
        self.t = Transition("t")
        self.net = PetriNet(
            places={self.a, self.b, self.c},
            transitions={self.t},
            arcs={InputArc(self.a, self.t, 2), InputArc(self.b, self.t), OutputArc(self.t, self.c, 3)},
        )

    def test_parts(self):
        self.assertEqual(self.net.places, {self.a, self.b, self.c})
        self.assertEqual(self.net.transitions, {self.t})
        self.assertEqual(len(self.net.arcs), 3)
        self.assertEqual(self.net.initial_marking, Marking())
        self.assertEqual(self.net.final_markings, frozenset())

    def test_final_markings_are_exact_and_do_not_stop_firing(self):
        p, q, t = Place("p"), Place("q"), Transition("t")
        final = Marking({p: 1})
        net = PetriNet({p, q}, {t}, {InputArc(p, t), OutputArc(t, q)}, final,
                       final_markings=[final, Marking(), final])
        self.assertEqual(net.final_markings, frozenset({final, Marking()}))
        self.assertTrue(net.is_final(final))
        self.assertTrue(net.is_final(Marking()))
        self.assertFalse(net.is_final(Marking({p: 1, q: 1})))
        self.assertEqual(net.fire(final, t), Marking({q: 1}))
        self.assertFalse(self.net.is_final(Marking()))

    def test_final_markings_must_be_markings_over_places(self):
        for finals in [[{self.a: 1}], [Marking({Place("outside"): 1})]]:
            with self.subTest(finals=finals), self.assertRaises(PetriNetError):
                PetriNet({self.a}, set(), set(), final_markings=finals)

    def test_weight_preset_postset(self):
        self.assertEqual(self.net.weight(self.a, self.t), 2)
        self.assertEqual(self.net.weight(self.t, self.a), 0)
        self.assertEqual(self.net.preset(self.t), {self.a, self.b})
        self.assertEqual(self.net.postset(self.t), {self.c})

    def test_enabled_needs_all_input_places(self):
        a, b, t = self.a, self.b, self.t
        self.assertTrue(self.net.is_enabled(Marking({a: 2, b: 1}), t))
        self.assertFalse(self.net.is_enabled(Marking({a: 1, b: 1}), t))
        self.assertFalse(self.net.is_enabled(Marking({a: 2}), t))

    def test_fire(self):
        a, b, c, t = self.a, self.b, self.c, self.t
        self.assertEqual(self.net.fire(Marking({a: 3, b: 1}), t), Marking({a: 1, c: 3}))

    def test_fire_disabled(self):
        with self.assertRaises(PetriNetError):
            self.net.fire(Marking(), self.t)

    def test_parts_must_fit_together(self):
        p, t, x = Place("p"), Transition("t"), Place("x")
        cases = {
            "transition in P": lambda: PetriNet({p, t}, set(), set()),
            "place in T": lambda: PetriNet({p}, {t, x}, set()),
            "same name for a place and a transition": lambda: PetriNet({p}, {Transition("p")}, set()),
            "arc to a place not in P": lambda: PetriNet({p}, {t}, {InputArc(x, t)}),
            "arc to a transition not in T": lambda: PetriNet({p}, set(), {InputArc(p, t)}),
            "two weights for one arc": lambda: PetriNet({p}, {t}, {InputArc(p, t, 1), InputArc(p, t, 2)}),
            "F contains a non-arc": lambda: PetriNet({p}, {t}, {(p, t, 1)}),
            "m_0 marks a place not in P": lambda: PetriNet({p}, {t}, set(), Marking({x: 1})),
            "m_0 is not a Marking": lambda: PetriNet({p}, {t}, set(), {p: 1}),
        }
        for description, make in cases.items():
            with self.subTest(description), self.assertRaises(PetriNetError):
                make()


class ParserTests(unittest.TestCase):
    def test_example(self):
        net = load_example()
        p1, p2, p4 = Place("p1"), Place("p2"), Place("p4")
        self.assertEqual(net.places, {p1, p2, Place("p3"), p4})
        self.assertEqual(net.transitions, {Transition("t1"), Transition("t2"), Transition("t3"), Transition("t4")})
        self.assertEqual(net.initial_marking, Marking({p1: 1}))
        self.assertEqual(len(net.arcs), 8)
        self.assertIn(InputArc(p2, Transition("t4"), 2), net.arcs)
        self.assertIn(OutputArc(Transition("t4"), p4, 1), net.arcs)

    def test_multiple_inputs_weights_and_continuation_lines(self):
        net = parse(
            "places: a, b, c\ntransitions: t u\n"
            "arcs: (a, t), (b,t)\n  (t, c, 3)\n"
            "arcs: (c, u)"
        )
        a, b, c, t, u = Place("a"), Place("b"), Place("c"), Transition("t"), Transition("u")
        self.assertEqual(net.arcs, {InputArc(a, t), InputArc(b, t), OutputArc(t, c, 3), InputArc(c, u)})

    def test_errors(self):
        for text in [
            "places: a\ntransitions: t\narcs: (b, t)",       # undeclared node
            "places: a a",                                   # duplicate place
            "places: a\ntransitions: a",                     # place/transition clash
            "places: a b\narcs: (a, b)",                     # place -> place
            "transitions: t u\narcs: (t, u)",                # transition -> transition
            "places: a\ntransitions: t\narcs: (a, t, 0)",    # zero weight
            "places: a\ntransitions: t\narcs: (a, t) (a, t)",  # duplicate arc
            "places: a\ntransitions: t\narcs: a -> t",       # not a tuple
            "places: a\ninitial: a",                         # bad marking
            "places: a\ninitial: b=1",                       # marking on non-place
            "places: a\ninitial: a=1 a=2",                   # marking twice
            "(a, t)",                                        # no section keyword
        ]:
            with self.subTest(text=text), self.assertRaises(ParseError):
                parse(text)

    def test_error_has_line_number(self):
        with self.assertRaises(ParseError) as ctx:
            parse("places: a b\ntransitions: t\narcs: (a, t)\n  (a, b)")
        self.assertEqual(ctx.exception.lineno, 4)


class ExportTests(unittest.TestCase):
    def test_export_preserves_indexed_and_quoted_names(self):
        net = PetriNet({Place("p.a[1][-2]")}, {Transition("step.done")}, set())
        self.assertIn("pl {p.a[1][-2]} (0)", tina.to_tina(net))
        self.assertIn("tr {step.done}", tina.to_tina(net))

    def test_empty_nets_can_be_exported(self):
        self.assertEqual(tina.to_tina(PetriNet(set(), set(), set())), "\n")
        self.assertEqual(tina.to_tina(PetriNet({Place("p")}, set(), set())), "pl {p} (0)\n")

    def test_markings_weights_and_source_transitions_are_exported(self):
        net = parse("places: a b\ntransitions: t u\ninitial: a=2\narcs: (a, t, 2) (t, b) (u, a)")
        exported = tina.to_tina(net)
        self.assertIn("pl {a} (2)", exported)
        self.assertIn("pl {b} (0)", exported)
        self.assertIn("tr {t} {a}*2 -> {b}*1", exported)
        self.assertIn("tr {u}  -> {a}*1", exported)


class BuiltinTests(unittest.TestCase):
    def test_removing_dead_transitions_preserves_behavior_and_metadata(self):
        net = load_example()
        before = (net.transitions, net.arcs)
        dead = explore.dead_transitions(net)
        pruned = net.without_transitions(dead)
        self.assertEqual(pruned.transitions, {Transition("t1"), Transition("t2")})
        self.assertEqual(len(pruned.arcs), 4)
        self.assertEqual(pruned.places, net.places)
        self.assertEqual(pruned.initial_marking, net.initial_marking)
        self.assertEqual(pruned.final_markings, net.final_markings)
        self.assertEqual((net.transitions, net.arcs), before)

        def reachable(model):
            seen, pending = {model.initial_marking}, [model.initial_marking]
            while pending:
                marking = pending.pop()
                for t in model.enabled_transitions(marking):
                    successor = model.fire(marking, t)
                    if successor not in seen:
                        seen.add(successor)
                        pending.append(successor)
            return seen

        self.assertEqual(reachable(pruned), reachable(net))
        self.assertEqual(explore.dead_transitions(pruned), [])

    def test_removal_handles_all_or_no_transitions_and_preserves_finals(self):
        p, q, t = Place("p"), Place("q"), Transition("t")
        net = PetriNet({p, q}, {t}, {InputArc(p, t), OutputArc(t, q)}, final_markings=[Marking({q: 1})])
        empty = net.without_transitions({t})
        self.assertFalse(empty.transitions)
        self.assertFalse(empty.arcs)
        self.assertEqual(empty.places, net.places)
        self.assertEqual(empty.final_markings, net.final_markings)
        self.assertEqual(net.without_transitions([]).arcs, net.arcs)
        with self.assertRaises(PetriNetError):
            net.without_transitions([Transition("outside")])

    def test_example(self):
        self.assertEqual(explore.dead_transitions(load_example()), [Transition("t3"), Transition("t4")])

    def test_unbounded_gives_up(self):
        net = parse("places: a\ntransitions: t u\narcs: (t, a) (a, u, 1000000) (u, a)")
        with self.assertRaises(explore.StateSpaceTooLarge):
            explore.dead_transitions(net, limit=100)


if __name__ == "__main__":
    unittest.main()
