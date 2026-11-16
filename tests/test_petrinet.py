import os
import subprocess
import unittest
from unittest import mock

from petrinet import ParseError, parse
from petrinet import explore, lola

HERE = os.path.dirname(__file__)
EXAMPLE = os.path.join(HERE, "nets", "two_dead.txt")


class ParserTests(unittest.TestCase):
    def test_example(self):
        with open(EXAMPLE) as f:
            net = parse(f.read())
        self.assertEqual(net.places, ["p1", "p2", "p3", "p4"])
        self.assertEqual(net.initial, {"p1": 1})
        t4 = net.transitions[3]
        self.assertEqual((t4.name, t4.pre, t4.post), ("t4", {"p2": 2}, {"p4": 1}))

    def test_multiple_inputs_weights_and_continuation_lines(self):
        net = parse(
            "places: a, b, c\ntransitions: t u\n"
            "arcs: (a, t), (b,t)\n  (t, c, 3)\n"
            "arcs: (c, u)"
        )
        t, u = net.transitions
        self.assertEqual((t.pre, t.post), ({"a": 1, "b": 1}, {"c": 3}))
        self.assertEqual((u.pre, u.post), ({"c": 1}, {}))

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
            "(a, t)",                                        # no section keyword
        ]:
            with self.subTest(text=text), self.assertRaises(ParseError):
                parse(text)


class ExportTests(unittest.TestCase):
    def test_to_lola(self):
        net = parse("places: a b\ntransitions: t u\ninitial: a=2\narcs: (a, t, 2) (t, b) (u, a)")
        self.assertEqual(
            lola.to_lola(net),
            "PLACE a, b;\n\nMARKING a: 2;\n\n"
            "TRANSITION t\n  CONSUME a: 2;\n  PRODUCE b: 1;\n\n"
            "TRANSITION u\n  CONSUME ;\n  PRODUCE a: 1;\n",
        )


class BuiltinTests(unittest.TestCase):
    def test_example(self):
        with open(EXAMPLE) as f:
            self.assertEqual(explore.dead_transitions(parse(f.read())), ["t3", "t4"])

    def test_unbounded_gives_up(self):
        net = parse("places: a\ntransitions: t u\narcs: (t, a) (a, u, 1000000) (u, a)")
        with self.assertRaises(explore.StateSpaceTooLarge):
            explore.dead_transitions(net, limit=100)


class LolaDriverTests(unittest.TestCase):
    """Drive lola.dead_transitions with a fake LoLA that answers from the builtin explorer."""

    def test_uses_ef_fireable_and_reads_result(self):
        with open(EXAMPLE) as f:
            net = parse(f.read())
        live = {"t1", "t2"}
        queries = []

        def fake_run(cmd, **kw):
            formula = cmd[2].removeprefix("--formula=")
            queries.append(formula)
            name = formula.removeprefix("EF FIREABLE(").removesuffix(")")
            verdict = "yes" if name in live else "no"
            return subprocess.CompletedProcess(cmd, 0, "", f"lola: result: {verdict}\n")

        with mock.patch("petrinet.lola.shutil.which", return_value="/usr/bin/lola"), \
             mock.patch("petrinet.lola.subprocess.run", side_effect=fake_run):
            self.assertEqual(lola.dead_transitions(net), ["t3", "t4"])
        self.assertEqual(queries, [f"EF FIREABLE({t})" for t in ["t1", "t2", "t3", "t4"]])

    def test_json_result_preferred(self):
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"analysis": {"result": False}}, f)
        try:
            self.assertFalse(lola._parse_result("", "lola: result: yes", f.name))
        finally:
            os.unlink(f.name)


@unittest.skipUnless(__import__("shutil").which("lola"), "lola not on PATH")
class RealLolaTests(unittest.TestCase):
    def test_example(self):
        with open(EXAMPLE) as f:
            self.assertEqual(lola.dead_transitions(parse(f.read())), ["t3", "t4"])

    def test_empty_marking_and_unbounded(self):
        net = parse("places: a b\ntransitions: src sink needb\narcs: (src, a) (a, sink) (b, needb) (needb, a)")
        self.assertEqual(lola.dead_transitions(net), ["needb"])


if __name__ == "__main__":
    unittest.main()
