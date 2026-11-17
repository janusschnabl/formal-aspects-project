import os
import unittest

from petrinet.agentnet import VAR, ParseError, load, parse

SHOP = os.path.join(os.path.dirname(__file__), "..", "examples", "agents", "shop.txt")


class ShopExampleTests(unittest.TestCase):
    def setUp(self):
        self.net = load(SHOP)

    def test_structure(self):
        net = self.net
        self.assertEqual(list(net.agents), ["Customer", "Clerk"])
        c, k = net.agents["Customer"], net.agents["Clerk"]
        self.assertEqual(c.places, ["home", "queue", "done"])
        self.assertEqual(c.transitions, ["enter", "leave", "buy[1]", "buy[2]"])
        self.assertEqual(k.places, ["open[1]", "away[1]", "open[2]", "away[2]"])
        self.assertEqual(c.arcs[("home", "enter")], VAR)
        self.assertEqual(c.arcs[("queue", "buy[2]")], 1)
        self.assertEqual(k.read_arcs, [("open[1]", "sell[1]"), ("open[2]", "sell[2]")])
        self.assertEqual(c.labels, {"enter": None, "leave": None, "buy[1]": "serve[1]", "buy[2]": "serve[2]"})
        self.assertEqual(net.initial, {"home": 3, "open[1]": 1, "open[2]": 1})
        self.assertEqual(net.finals, [{"home": 3, "open[1]": 1, "open[2]": 1}])
        self.assertEqual(net.labels, {"serve[1]", "serve[2]"})

    def test_global_transitions(self):
        g = {x.name: x.members for x in self.net.global_transitions()}
        self.assertEqual(g["serve[1]"], (("Customer", "buy[1]"), ("Clerk", "sell[1]")))
        self.assertEqual(g["enter"], (("Customer", "enter"),))
        self.assertEqual(len(g), 2 + 2 + 4)  # 2 synced + enter/leave + pause/resume x2

    def test_well_formed(self):
        self.assertEqual(self.net.well_formedness_errors(), [])

class IndexTests(unittest.TestCase):
    def test_many_indices_and_mixed_line(self):
        net = parse(
            "index: I = 1..2, J = {a, b}, D = 0..1\n"
            "agent: A\n"
            "  places: home, q[i][j][d] for i in I, j in J, d in D\n"
            "initial: home=1, q[i][a][0]=2 for i in I\n"
        )
        self.assertEqual(len(net.agents["A"].places), 1 + 2 * 2 * 2)
        self.assertEqual(net.agents["A"].places[:3], ["home", "q[1][a][0]", "q[1][a][1]"])
        self.assertEqual(net.initial, {"home": 1, "q[1][a][0]": 2, "q[2][a][0]": 2})

    def test_arithmetic_and_product(self):
        net = parse(
            "index: I = 1..3, D = {x, y}\n"
            "agent: A\n"
            "  places: p[i] for i in I\n"
            "  transitions: t[i] for i in 1..2\n"
            "  arcs: (p[i], t[i]) (t[i], p[i+1]) for i in 1..2\n"
            "agent: B\n"
            "  places: q[i][d] for i in I, d in D\n"
        )
        a = net.agents["A"]
        self.assertEqual(a.postset("t[2]"), {"p[3]": 1})
        self.assertEqual(net.agents["B"].places, ["q[1][x]", "q[1][y]", "q[2][x]", "q[2][y]", "q[3][x]", "q[3][y]"])

    def test_literal_index_and_spacing(self):
        net = parse("agent: A\n places: p[ 1 ], p[north]\n transitions: t\n arcs: (p[1], t) (t, p[north])")
        self.assertEqual(net.agents["A"].places, ["p[1]", "p[north]"])

    def test_repeated_indices_in_all_sections_and_negative_arithmetic(self):
        net = parse(
            "index: I = -1..0, J = {north, south}\n"
            "agent: A\n"
            " places: p[i][j] for i in -1..1, j in J\n"
            " transitions: t[i][j] for i in I, j in J\n"
            " labels: t[i][j]=go[i][j] for i in I, j in J\n"
            " arcs: (p[i][j], t[i][j]) (t[i][j], p[i+1][j]) for i in I, j in J\n"
            " read: (p[ 1 ][j], t[-1][j]) for j in J\n"
            " initial: p[-1][j]=1 for j in J\n"
            " final: p[1][j]=1 for j in J\n"
        )
        a = net.agents["A"]
        self.assertEqual(a.postset("t[-1][north]"), {"p[0][north]": 1})
        self.assertEqual(a.labels["t[0][south]"], "go[0][south]")
        self.assertIn(("p[1][north]", "t[-1][north]"), a.read_arcs)
        self.assertEqual(net.initial, {"p[-1][north]": 1, "p[-1][south]": 1})
        self.assertEqual(net.finals, [{"p[1][north]": 1, "p[1][south]": 1}])

    def test_old_multi_index_syntax_has_migration_error(self):
        base = "agent: A\n places: p[1][a]\n transitions: t\n"
        for statement in ["places: q[1,a]", "arcs: (p[1,a], t)", "read: (p[1,a], t)",
                          "labels: t=go[1,a]", "initial: p[1,a]=1", "final: p[1,a]=1"]:
            with self.subTest(statement=statement), self.assertRaises(ParseError) as cm:
                parse(base + statement)
            self.assertEqual(cm.exception.lineno, 4)
            self.assertIn("q[i][j]", str(cm.exception))

    def test_missing_for_hint_checks_all_brackets(self):
        with self.assertRaisesRegex(ParseError, "missing a 'for"):
            parse("agent: A\n places: p[1][a]\n initial: p[1][j]=1")

    def test_checker_keywords_are_valid_names(self):
        for name in ["PLACE", "FIREABLE", "TRUE", "oo", "A", "AG", "AGEF", "XX"]:
            for statement in [f"places: {name}", f"transitions: {name}", f"labels: t={name}"]:
                with self.subTest(name=name, statement=statement):
                    net = parse("agent: A\n transitions: t\n" + statement)
                    self.assertIn("A", net.agents)
        self.assertEqual(parse("agent: A\n places: AG[1] place\n").places, ["AG[1]", "place"])


class WellFormednessTests(unittest.TestCase):
    def errors(self, body):
        return parse("agent: A\n places: a b c\n transitions: t\n" + body).well_formedness_errors()

    def test_not_preserving(self):
        self.assertIn("does not preserve agents", self.errors(" arcs: (a, t, 2) (t, b)")[0])

    def test_unpaired_variable_arc(self):
        self.assertIn("variable arc", self.errors(" arcs: (a, t, *) (t, b)")[0])

    def test_two_variable_arcs(self):
        self.assertIn("variable arc", self.errors(" arcs: (a, t, *) (b, t, *) (t, c, *)")[0])

    def test_no_incoming_arc(self):
        self.assertIn("no incoming arc", self.errors(" read: (a, t)")[0])

    def test_sync_needs_only_one_incoming(self):
        net = parse(
            "agent: A\n places: a\n transitions: t\n labels: t=go\n read: (a, t)\n"
            "agent: B\n places: b c\n transitions: u\n labels: u=go\n arcs: (b, u) (u, c)\n"
        )
        self.assertEqual(net.well_formedness_errors(), [])


class ParseErrorTests(unittest.TestCase):
    def test_errors(self):
        base = "agent: A\n places: a b\n transitions: t\n"
        for text in [
            "places: a",                                             # before any agent
            base + " arcs: (a, b)",                                  # place -> place
            base + " arcs: (a, x)",                                  # undeclared
            base + " arcs: (a, t) (a, t)",                           # duplicate arc
            base + " arcs: (a, t)\n read: (a, t)",                   # C and F overlap
            base + " read: (t, a)",                                  # read arc wrong direction
            base + "agent: B\n places: a",                           # place in two agents
            base + "agent: B\n places: c\n transitions: u\n arcs: (a, u)",  # cross-agent arc
            base + " transitions: u\n labels: t=go, u=go",           # label twice in one agent
            base + " initial: t=1",                                  # marking on transition
            base + " places: p[i] for i in J",                       # unknown index set
            base + " arcs: a -> t",                                  # not a tuple
            "agent: A\nagent: A",                                    # duplicate agent
        ]:
            with self.subTest(text=text), self.assertRaises(ParseError):
                parse(text)

    def test_line_number(self):
        with self.assertRaises(ParseError) as cm:
            parse("agent: A\n places: a\n transitions: t\n\n arcs: (a, nope)")
        self.assertEqual(cm.exception.lineno, 5)


if __name__ == "__main__":
    unittest.main()
