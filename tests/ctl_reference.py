"""Independent CTL evaluation on finite graphs, without Muse or PetriNet helpers."""

from functools import cache


def satisfying_states(states, edges, atoms, formula):
    """Evaluate tuple formulas, e.g. ('AG', ('EF', 'final')).

    Edges are (source, target) pairs. Terminal states are totalized only for
    temporal operators; the 'deadlock' atom records the original empty successor sets.
    """
    states = set(states)
    successors = {state: set() for state in states}
    for source, target in edges:
        successors[source].add(target)
    deadlocks = {state for state in states if not successors[state]}
    for state in deadlocks:
        successors[state].add(state)
    atoms = {**atoms, "deadlock": deadlocks}

    def predecessors(targets, universal):
        return {state for state, following in successors.items()
                if (following <= targets if universal else bool(following & targets))}

    @cache
    def evaluate(expression):
        if isinstance(expression, str):
            return frozenset(atoms[expression])
        operator, *arguments = expression
        first = evaluate(arguments[0])
        if operator == "not":
            return frozenset(states - first)
        if operator in {"and", "or"}:
            second = evaluate(arguments[1])
            return first & second if operator == "and" else first | second
        if operator not in {"EX", "AX", "EF", "AF", "EG", "AG", "EU", "AU"}:
            raise ValueError(f"unknown CTL operator {operator}")
        universal = operator.startswith("A")
        if operator.endswith("X"):
            return frozenset(predecessors(first, universal))
        greatest = operator.endswith("G")
        if greatest:
            guard, target = first, set()
        elif operator.endswith("F"):
            guard, target = states, first
        else:
            guard, target = first, evaluate(arguments[1])
        current = set(states) if greatest else set()
        while True:
            following = target | (guard & predecessors(current, universal))
            if following == current:
                return frozenset(current)
            current = following

    return evaluate(formula)


def expected_verification(states, edges, initial, finals, has_finals):
    """Calculate default verdicts from an independently constructed graph."""
    def holds(formula):
        return initial in satisfying_states(states, edges, {"final": finals}, formula)

    return {
        "completion": {
            "option_to_complete": holds(("AG", ("EF", "final"))),
            "all_traces_complete": holds(("AF", "final")),
        } if has_finals else None,
        "deadlocks": {
            "reachable": holds(("EF", "deadlock")),
            "non_final_reachable": holds(("EF", ("and", "deadlock", ("not", "final")))),
        },
        "properties": {},
        "agent_completion": {},
    }
