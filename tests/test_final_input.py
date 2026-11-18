"""Final declarations form a union of exact markings and partial clauses."""

import unittest

from petrinet import Marking, PetriNet, PetriNetError, Place, Transition, parse
from petrinet import agentnet
from petrinet.dot import to_agent_dot, to_dot


class FinalInputTests(unittest.TestCase):
    def test_named_ctl_declarations_are_preserved_by_both_parsers(self):
        declarations = "ctl: can_finish = AG EF IsFinal\nctl: enough = AG (S.{p[1]} >= 0)\n"
        expected = {"can_finish": "AG EF IsFinal", "enough": "AG (S.{p[1]} >= 0)"}
        self.assertEqual(parse("places: p\n" + declarations).ctl_properties, expected)
        source = agentnet.parse("agent: A\n places: p[1]\n" + declarations)
        self.assertEqual(source.ctl_properties, expected)

    def test_ctl_declarations_survive_conversion_and_pruning_without_aliasing(self):
        source = agentnet.parse("agent: A\n places: p q\n transitions: t\n"
                                "arcs: (p,t) (t,q)\nctl: can_finish = AG EF IsFinal\n")
        net = agentnet.convert(source).net
        source.ctl_properties["can_finish"] = "F:S"
        returned = net.ctl_properties
        returned["can_finish"] = "T:S"
        self.assertEqual(net.without_transitions(net.transitions).ctl_properties,
                         {"can_finish": "AG EF IsFinal"})

    def test_invalid_or_duplicate_ctl_declarations_are_rejected(self):
        for text in ["ctl: 1bad = EF IsFinal", "ctl: empty = ", "ctl: commands = T:S; F:S",
                     "ctl: same = T:S\nctl: same = F:S", "ctl: split = AG\n EF IsFinal"]:
            for parser in [parse, agentnet.parse]:
                with self.subTest(parser=parser.__module__, text=text), self.assertRaises(ValueError):
                    parser(text)
        for properties in [{"bad name": "T:S"}, {"empty": ""}, {"commands": "T:S; F:S"}]:
            with self.subTest(properties=properties), self.assertRaises(PetriNetError):
                PetriNet(set(), set(), set(), ctl_properties=properties)

    def test_standard_exact_and_unrestricted_finals_form_a_union(self):
        net = parse("places: boarded closed crew cancelled\n"
                    "final: cancelled=3\n"
                    "unrestricted final: boarded=3\n closed=2\n")
        boarded, closed, crew, cancelled = map(Place, ["boarded", "closed", "crew", "cancelled"])
        self.assertTrue(net.is_final(Marking({boarded: 3, closed: 2, crew: 8})))
        self.assertFalse(net.is_final(Marking({boarded: 2, closed: 2, crew: 8})))
        self.assertTrue(net.is_final(Marking({cancelled: 3})))
        self.assertFalse(net.is_final(Marking({cancelled: 3, crew: 1})))

    def test_empty_exact_final_is_zero_and_empty_unrestricted_final_is_universal(self):
        p = Place("p")
        exact = parse("places: p\nfinal:\n")
        unrestricted = parse("places: p\nunrestricted final:\n")
        self.assertTrue(exact.is_final(Marking()))
        self.assertFalse(exact.is_final(Marking({p: 1})))
        self.assertTrue(unrestricted.is_final(Marking()))
        self.assertTrue(unrestricted.is_final(Marking({p: 10})))
        self.assertEqual(unrestricted.unrestricted_final_markings, ({},))

    def test_explicit_zero_is_a_constraint_only_when_present(self):
        net = parse("places: waiting crew\nunrestricted final: waiting=0\n")
        self.assertEqual(net.unrestricted_final_markings, ({Place("waiting"): 0},))
        self.assertTrue(net.is_final(Marking({Place("crew"): 5})))
        self.assertFalse(net.is_final(Marking({Place("waiting"): 1})))

    def test_agent_indices_and_continuations_preserve_both_final_kinds(self):
        source = agentnet.parse("agent: A\n places: p[i] for i in 1..2\n places: crew\n"
                                "final: crew=1\n"
                                "unrestricted final: p[i]=0 for i in 1..2\n crew=2\n")
        net = agentnet.convert(source).net
        self.assertEqual(source.finals, [{"crew": 1}])
        self.assertEqual(source.unrestricted_finals, [{"p[1]": 0, "p[2]": 0, "crew": 2}])
        self.assertTrue(net.is_final(Marking({Place("crew"): 1})))
        self.assertTrue(net.is_final(Marking({Place("crew"): 2})))
        self.assertFalse(net.is_final(Marking({Place("crew"): 2, Place("p[1]"): 1})))
        for text in [to_dot(net), to_agent_dot(source)]:
            self.assertIn("unlisted places = 0", text)
            self.assertIn("unlisted places = any", text)
            self.assertIn("p[1]=0", text)

    def test_unknown_duplicate_negative_and_transition_constraints_are_rejected(self):
        for suffix in ["missing=0", "p=0 p=1", "p=-1", "t=1"]:
            with self.subTest(suffix=suffix):
                for parser, header in [(parse, "places: p\ntransitions: t\n"),
                                       (agentnet.parse, "agent: A\nplaces: p\ntransitions: t\n")]:
                    with self.assertRaises(ValueError):
                        parser(header + "unrestricted final: " + suffix)

    def test_model_copies_constraints_and_pruning_preserves_them(self):
        p, t = Place("p"), Transition("t")
        clause = {p: 0}
        net = PetriNet({p}, {t}, set(), unrestricted_final_markings=[clause])
        clause[p] = 1
        returned_clause = net.unrestricted_final_markings[0]
        returned_clause[p] = 2
        pruned = net.without_transitions([t])
        self.assertEqual(pruned.unrestricted_final_markings, ({p: 0},))
        self.assertTrue(pruned.is_final(Marking()))
        self.assertFalse(pruned.is_final(Marking({p: 1})))

    def test_programmatic_constraints_validate_place_and_count(self):
        p = Place("p")
        for clause in [{Place("missing"): 0}, {"p": 0}, {p: -1}, {p: True}, {p: 1.5}]:
            with self.subTest(clause=clause), self.assertRaises(PetriNetError):
                PetriNet({p}, set(), set(), unrestricted_final_markings=[clause])
        source = agentnet.parse("agent: A\n places: p\n")
        source.unrestricted_finals = [{"missing": 0}]
        with self.assertRaises(agentnet.ConversionError):
            agentnet.convert(source)


if __name__ == "__main__":
    unittest.main()
