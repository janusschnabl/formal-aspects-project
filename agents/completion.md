# Per-type completion checks

Markings and the reachability graph are global. Each type gets a final-state
condition that tests only the places owned by that type. Muse evaluates that
condition along executions of the complete system.

## From a final declaration to CTL

Consider a type with two places:

```text
agent: Customer
places: waiting done

final: done=3
```

Customer owns both `waiting` and `done`. Since this is an exact `final:`, its
local final condition requires `done=3` **and `waiting=0`**. The converter stores
both constraints explicitly:

```python
agent_final_clauses["Customer"] = (
    {Place("waiting"): 0, Place("done"): 3},
)
```

The zero is added by this expression in the converter:

```python
{place_map[p]: final.get(p, 0) for p in sorted(places)}
```

`places` contains every place owned by Customer. `final.get(p, 0)` returns the
declared count, or zero when that place was omitted. `place_map` connects each
source name to the corresponding place in the converted Petri net.

The checker translates the stored constraints into queries equivalent to:

```text
((S.waiting = 0) /\ (S.done = 3))
EF ((S.waiting = 0) /\ (S.done = 3))
AG (EF ((S.waiting = 0) /\ (S.done = 3)))
AF ((S.waiting = 0) /\ (S.done = 3))
```

`S.waiting` and `S.done` are the token counts at a global state. The omitted
place is therefore checked by the explicit equality `S.waiting = 0` in all four
queries. Every other omitted place owned by Customer gets the same zero check.
Places owned by other types are unconstrained by Customer's local condition.

- **Initial marking is locally final (`LocalFinal`):** the initial marking
  already satisfies this type's local final condition. JSON records this as
  `agent_completion[type]["initially_final"]`.
- **Can complete from initial marking (`EF`):** some execution from the initial
  state reaches this local condition.
- **Option to complete from every reachable marking (`AG EF`):** at every
  reachable global marking, some continuation can reach this local condition.
  JSON records this as `agent_completion[type]["option_to_complete"]`.
- **All traces reach a local final (`AF`):** every maximal execution from the
  initial state reaches this local condition. Other types' loops may make this false.

Matching the initial state satisfies `EF` and `AF` immediately. `AG EF` also
checks every later reachable marking: leaving a local final and losing all
routes back makes it false. Finals do not disable transitions, and none of
these properties requires staying final after reaching it.

For the airport model, Crew is locally final initially because `Available=2`.
The CLI reports `Initial marking is locally final: yes` for Crew and `no` for
Aircraft and Passenger.
Thus `all traces reach a local final` is true, but its `option_to_complete` is
false: if all passengers miss their flights, the crew can prepare a flight and
become stuck with no route back to `Available=2`. Aircraft and Passenger also
have a false `option_to_complete`, although both can complete along a successful
execution.

## Unrestricted finals and missing declarations

With `unrestricted final: done=3`, `waiting` remains unconstrained. The initial
check uses `S.done = 3`; the temporal queries use `EF (S.done = 3)`,
`AG (EF (S.done = 3))`, and `AF (S.done = 3)`.

A declaration contributes a local final only if it explicitly mentions a
place owned by that type. An explicit `waiting=0` counts as a mention; the
parser preserves it. A type with no such entries has no inferred local final
and is reported as unspecified (`null` in `agent_completion`).

Multiple local final clauses are joined with OR inside EF, AG EF, and AF.
Global finals still require one whole global alternative to match; independently
matching local alternatives does not establish global completion.

## Code path

1. [agentnet/parser.py](../petrinet/agentnet/parser.py) retains explicit final
   entries, including zeros.
2. [agentnet/convert.py](../petrinet/agentnet/convert.py) uses each type's place
   list to build `ConversionResult.agent_final_clauses` alongside the global net.
3. [__main__.py](../petrinet/__main__.py) passes the net and these clauses to
   `verify_properties(agent_final_clauses=...)`.
4. [tina.py](../petrinet/tina.py) translates the clauses with `_final_expression`
   and evaluates the local final predicate initially, per-type EF/AG EF/AF,
   global checks, and custom properties in one Muse session. Results are
   recorded under `agent_completion` in the JSON.

The global option-to-complete check uses `AG EF IsFinal` and is required by
[soundness](soundness.md). It requires a path to a whole global final from every
reachable marking. The per-type `option_to_complete` uses `AG EF LocalFinal`
and is reported separately; independently reaching each type's local final
does not guarantee that a global final can be reached.
