# Agent soundness

The `--agentnet --pipeline` command combines three checks on the original model:

1. **Option to complete:** every reachable global marking can reach a global
   final marking (`AG EF IsFinal`).
2. **No per-type deadlocks:** no reachable state leaves an unfinished type
   permanently unable to participate. See [per-type deadlocks](deadlocks.md).
3. **No dead source activities:** every original global activity can fire with
   at least one legal choice of variable weights.

The verdict is computed from the existing results:

```python
soundness = (
    completion["option_to_complete"]
    and not any(agent_deadlocks.values())
    and not dead_activities
)
```

The declared global finals are the completion contract. This is the project's
agent-soundness definition. `AF IsFinal` remains a separate, stronger check for
inevitable completion; it is not required for soundness.

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

## Reports

The terminal prints `Soundness: yes/no` and lists any dead source activities.
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
dead source activities are still reported. Standard-net inputs have no agent
metadata and do not receive this agent-soundness verdict.

An unsound model still has a completed verification result and exit code 0.
Checker failures keep soundness unset and use exit code 2. No successful
soundness verdict from an earlier run is carried into a failed rerun.
