# Per-type deadlock checks

A type is deadlocked at a global marking when its local final condition is
false and no possible continuation can enable a global transition involving
that type. The pipeline checks whether any such marking is reachable:

```text
EF ((- LocalFinal_a) /\ (- EF CanAct_a))
```

All quantification is over the global reachability graph. The check is run
for every declared type before pruning. It reports a possibility of permanent
blocking, rather than requiring every execution to block.

## Mapping participation to the converted net

`ConversionResult.transition_origins[t].global_transition.members` records
which types participate in each generated transition `t`.
`ConversionResult.agent_transitions` groups these transitions by type,
including all generated weight variants and members that participate only
through read arcs. Types with no generated transitions get an empty set.

For example, suppose `serve` synchronizes Customer and Clerk, while `work`
involves Clerk alone. Muse predicates equivalent to the following identify
states where each type can act immediately:

```text
CanAct_Customer = <E.serve>T:S
CanAct_Clerk    = <(E.serve \/ E.work)>T:S
```

`E.serve` denotes edges labelled by the converted transition `serve`.
`<...>T:S` requires an actual outgoing edge matching those labels; the target
can be any state. An empty participation set becomes `F:S` (false).

The checker inserts these expressions and the existing local-final clauses
directly into the per-type formula. For Customer with local final
`waiting=0, done=1`, the query is equivalent to:

```text
EF ((- ((S.waiting = 0) /\ (S.done = 1))) /\ (- EF (<E.serve>T:S)))
```

If Clerk can loop on `work` but `serve` can never become enabled, Customer
is deadlocked even though the system still has a firing transition.

## Completion and temporary waiting

- [Local finals](completion.md) exempt a type only at states satisfying that
  condition. A type can leave a local final and deadlock later.
- With no local final declarations, the final predicate is false. The type
  receives no completion exemption, including at globally final states.
- A temporarily disabled type is not deadlocked if other types can move to a
  state where one of its participating transitions is enabled.
- An execution can starve a type by ignoring its enabled transitions forever.
  This does not make participation permanently impossible, so starvation
  alone does not satisfy this deadlock property.
- Temporal self-loops used for terminal-state CTL semantics are not actual
  transition firings and do not satisfy `CanAct_a`.

## Results and implementation

The CLI passes both `agent_final_clauses` and `agent_transitions` to
`tina.verify_properties()`. Per-type deadlocks, completion, global checks, and
custom properties share one property graph and Muse session. The helper
`_can_act_expression` builds the event predicates, and `_final_expression`
builds the local-final predicates.

The terminal prints `Agent Customer: reachable deadlock: yes/no`. JSON stores
booleans under `agent_deadlocks`, for example:

```json
"agent_deadlocks": {"Customer": true, "Clerk": false}
```

A detected deadlock is a completed verification result, not a tool error.
Only dead transitions are pruned. On a property-check failure, the report has
status `failed` and `agent_deadlocks: null`; no per-type verdict is claimed.
