# Witnesses for built-in violations

Tina builds the full reachability graph of the original net, before pruning.
Each graph node is a complete marking: the token counts at every place. Muse
checks our CTL properties on that graph and identifies the states needed to
explain a violation.

## How we obtain the evidence

| Check | How we obtain the evidence |
|---|---|
| Completion becomes impossible | Muse finds a marking with no route to a final; `pathto` finds a run to it |
| An agent type becomes permanently blocked | Muse finds an unfinished marking with no future participation; `pathto` finds a run to it |
| A non-final deadlock is reachable | Muse finds the deadlock marking; `pathto` finds a run to it |
| Some execution never completes | Muse identifies states where completion can be avoided; our code follows edges among them until reaching a deadlock or repeating a marking |

## What we ask Muse to find

A **predicate** is a condition that is true or false at a marking. We derive a
bad-state predicate from each property. For example, `AG (EF IsFinal)` says that
every reachable marking has some path to a final. Its bad-state predicate is
`- (EF IsFinal)`: from this marking, no final is reachable.

In the faulty club, the initial marking does not satisfy that predicate because
completion is possible. After `request_work -> quit`, it does: too few instructors
remain to form teams. Muse proves that no route to a final exists; the returned
run shows how we reached that situation.

For blocking, the predicate is `(- LocalFinal) /\ (- EF CanAct)`. For a non-final
deadlock, it is `IsDeadlock /\ (- IsFinal)`. Global and per-type checks use their
corresponding final predicates, including zero constraints and alternatives.

Muse returns matching state IDs. We pass a selected ID and the original binary
graph to `pathto TARGET -s net.ktz`, which returns a finite sequence of states.
The target need not be the nearest bad state.

## How infinite counterexamples work

For `AF Final`, Muse computes `EG (- Final)`: states from which some execution
can keep avoiding finals. A state can belong to this set even if it also has an
exit leading to completion. Outside the set, every execution eventually reaches
a final.

Our code starts at the initial state and follows **one path**, choosing only
edges whose targets are also in this set. It does not enumerate all paths.
Because the checked graph is finite, this walk must reach a real deadlock or
repeat a complete marking. A repetition splits the run into a prefix and a
cycle; returning to the same marking makes the cycle repeatable forever.

The prefix and cycle must both avoid finals. Reaching a final and only then
entering a loop does not refute `AF Final`. No fairness is assumed, so repeatedly
choosing a cycle is allowed even when an exit exists. A non-final deadlock is
also a failed execution; its terminal stuttering is not a real transition.

We replay every returned transition against the original net, checking the
resulting markings against Tina's graph and checking that cycles close.

## Report format

The pipeline always saves `traces` in JSON. Terminal output shows a count;
`--verbose` (or `-v`) prints full counterexamples. API users request them with
`verify_properties(..., include_traces=True)`.

Each record names the check (`section`, `property`, optional `agent`), its
`reason`, and its `kind`: `path`, `deadlock`, `cycle`, or `unreachable`.
`initial_marking` starts the `prefix`, which ends at `marking`. Any `cycle`
returns to that marking and can be repeated forever. An empty prefix starts
directly at the initial marking. Steps contain the exact converted `transition`
and resulting marking; agent steps also identify the source `activity` and
variable `weights`. Zero token counts are omitted.

A false per-type `EF Final` produces an `unreachable` record with no steps:
the complete-graph check proves that no successful run exists.

Initial-final membership and allowed final deadlocks receive no counterexample.
Dead source activities retain their coverage report: one run cannot prove that
an activity never fires. Custom CTL formulas remain verdict-only.

## Implementation notes

All verdicts share one Muse query on Tina's completed, bounded `-R` graph.
Finite-path targets use a batched `-s -limit 1` query; AF sets use `-s -limit 0`.
`ktzio -ktj` exports the same graph to JSON for our edge and marking lookups.
No second exploration is performed; the graph is loaded only when needed.
Tina 4.0.0 emits identifiers verbatim in JSON strings, so known whole identifiers
are normalized before parsing to preserve quotes, braces, and backslashes.

All tools, parsing, and replay share the property-stage timeout. Tool errors,
malformed evidence, replay mismatches, or timeouts fail the stage without
publishing partial verdicts or retaining old evidence. `tests/test_witness.py`
checks the evidence against independent source graphs for all three scenarios,
along with edge cases and failure handling.
