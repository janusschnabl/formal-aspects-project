# Agent soundness

The `--agentnet --pipeline` command combines three checks on the original model:

1. **Completion always remains possible:** every reachable global marking can
   still reach a global final marking (`AG EF IsFinal`). This includes states
   reached after a previous final; it is not just a check from the initial state.
2. **No per-type deadlocks:** no reachable state leaves an unfinished type
   permanently unable to participate. See [per-type deadlocks](deadlocks.md).
3. **No dead source activities:** every original global activity can fire with
   at least one legal choice of variable weights.

The first condition is the global `completion["option_to_complete"]` result.
It is defined as `AG (EF IsFinal)` in `_COMPLETION_PROPERTIES` in
[tina.py](../petrinet/tina.py). `_prune_pipeline` in
[__main__.py](../petrinet/__main__.py) requires that result in the verdict:

```python
soundness = (
    completion["option_to_complete"]
    and not any(agent_deadlocks.values())
    and not dead_activities
)
```

The declared global finals are the completion contract. This is the project's
agent-soundness definition. A model with one successful execution can still be
unsound if another choice reaches a state from which completion is impossible.
For example, the airport model fails this condition when all passengers miss
their flights and an aircraft can no longer open boarding.

## Global and per-type completion

The [per-type completion results](completion.md) report initial local-final
membership and three temporal properties:

| JSON field | Formula | Meaning |
|---|---|---|
| `initially_final` | `LocalFinal` | The initial marking already satisfies the type's local final condition |
| `can_complete` | `EF LocalFinal` | Some execution from the initial state reaches the type's local final |
| `option_to_complete` | `AG EF LocalFinal` | The type's local final remains reachable from every reachable global marking |
| `all_traces_complete` | `AF LocalFinal` | Every execution from the initial state reaches the type's local final at least once |

Soundness uses the **global** `AG EF IsFinal`, together with the deadlock and
activity checks above. The per-type results are additional diagnostics; checking
each local final separately does not establish that a whole global final is
reachable.

`AF IsFinal` is reported separately and is not required for soundness. A loop
with an exit to a final can satisfy `AG EF` while failing `AF`. Conversely, an
initially final marking satisfies `AF` immediately, even if a later transition
leaves the final set permanently and makes `AG EF` false. Because finals do
not stop firing, neither property implies the other in general.

## Source activities and generated variants

A source activity is one member of the formalism's set of global transitions
`G`: either a synchronization group or a singleton local transition. Variable
arcs can produce several standard-net transitions for the same activity.

| Tina result for generated variants | Source activity verdict |
|---|---|
| At least one variant can fire | Live |
| Every variant is dead | Dead |
| No variants were generated | Dead |

`ConversionResult.source_activities` retains the complete original activity
set. `transition_origins` maps generated transitions back to these activities.
Given Tina's dead-transition set, `ConversionResult.dead_activities(dead)`
returns every original activity with no live variant. This also covers fixed
activities, which have one generated transition.

Activity identity includes its label and local members. Display names alone
are insufficient: a silent local transition can share a name with a label.

This grouping requires no additional reachability search. Pruning still removes
all dead standard-net transitions, but the source-activity verdict is retained
even when all variants of an activity are removed. The pruned net is not
rechecked to determine soundness.

The CLI's `Dead converted transitions` list refers to individual generated
transitions, including fixed activities with one variant. A dead variant alone
does not imply an unsound agent model. A dead source activity does fail the
activity requirement, even if global completion remains possible.

For example, the airport has three passengers in total, so expansion creates
`open[2]__k_Passenger_1`, `_2`, and `_3`. Flight 2 has only one passenger;
`Waiting[2]` can never contain two or three tokens. Those larger choices are
dead, but `open[2]` itself is live through its weight-1 variant. The airport has
no dead source activities; its unsoundness comes from losing completion and
reachable per-type deadlocks.

## Reports

The terminal separates `Global verification`, `Agent verification`, optional
`CTL properties`, and `Soundness`. Each type's completion and deadlock results
stay together in the agent section.

The soundness section prints the combined `Soundness: yes/no` and marks all
three requirements as PASS/FAIL. A failed completion check explains that a
reachable marking cannot reach a global final. Deadlock failures name every
affected type; activity failures list each dead source activity and its local
members. All failures are reported, even when more than one condition fails.
The `Dead converted transitions` list remains in the global section and is
not itself a soundness failure.

For the airport model:

```text
Soundness
---------
  Soundness: no
  [FAIL] Global option to complete
    A reachable marking has no path to a global final.
  [FAIL] No per-type deadlocks
    Deadlocked type: Aircraft
    Deadlocked type: Crew
    Deadlocked type: Passenger
  [PASS] No dead source activities
```

JSON contains `soundness` and `dead_activities`; each dead activity records its
name, label, and participating local transitions. For example:

```json
{
  "soundness": false,
  "dead_activities": [
    {
      "name": "serve",
      "label": "serve",
      "members": [
        {"agent": "Clerk", "transition": "sell"},
        {"agent": "Customer", "transition": "buy"}
      ]
    }
  ]
}
```

Without global final declarations, soundness is unspecified (`null`), while
the terminal marks global completion as SKIP and still reports the known
per-type deadlocks and dead source activities. Standard-net inputs have no
agent metadata and do not receive this agent-soundness verdict.

An unsound model still has a completed verification result and exit code 0.
Checker failures keep soundness unset and use exit code 2. No successful
soundness verdict from an earlier run is carried into a failed rerun.
