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
EF ((S.waiting = 0) /\ (S.done = 3))
AF ((S.waiting = 0) /\ (S.done = 3))
```

`S.waiting` and `S.done` are the token counts at a global state. The omitted
place is therefore checked by the explicit equality `S.waiting = 0` in both
queries. Every other omitted place owned by Customer gets the same zero check.
Places owned by other types are unconstrained by Customer's local condition.

- **Can eventually complete (`EF`):** some execution from the initial state
  reaches this local condition.
- **Always completes (`AF`):** every maximal execution from the initial state
  reaches this local condition. Other types' loops may make this false.

Matching the initial state counts as completion. These checks require reaching
the condition at least once; they do not require staying final afterward.

## Unrestricted finals and missing declarations

With `unrestricted final: done=3`, `waiting` remains unconstrained. The local
queries are equivalent to `EF (S.done = 3)` and `AF (S.done = 3)`.

A declaration contributes a local final only if it explicitly mentions a
place owned by that type. An explicit `waiting=0` counts as a mention; the
parser preserves it. A type with no such entries has no inferred local final
and is reported as unspecified (`null` in `agent_completion`).

Multiple local final clauses are joined with OR inside EF/AF. Global finals
still require one whole global alternative to match; independently matching
local alternatives does not establish global completion.

## Code path

1. [agentnet/parser.py](../petrinet/agentnet/parser.py) retains explicit final
   entries, including zeros.
2. [agentnet/convert.py](../petrinet/agentnet/convert.py) uses each type's place
   list to build `ConversionResult.agent_final_clauses` alongside the global net.
3. [__main__.py](../petrinet/__main__.py) passes the net and these clauses to
   `verify_properties(agent_final_clauses=...)`.
4. [tina.py](../petrinet/tina.py) translates the clauses with `_final_expression`
   and evaluates per-type EF/AF, global checks, and custom properties in one
   Muse session. Results are recorded under `agent_completion` in the JSON.

The existing global option-to-complete check is stronger than EF: it uses
`AG EF IsFinal`, requiring completion to remain possible from every reachable
state. The per-type **can eventually complete** check uses EF only.
