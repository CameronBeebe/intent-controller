# Research note: context relaxation and escaping idea basins

Status: user-requested future direction, not implemented; not a prerequisite for finishing the intent-controller baseline.

## User intent

Add a "psychedelic" function to the harness: an analogy to dropout or relaxation of a default mode network, applied to context to escape fixation. This is an exploration mechanism serving the maintained intent, not permission to weaken that intent or remove authoritative constraints. Biological analogy is not an established equivalence.

Reference supplied by user (preserved):
https://x.com/AlexGDimakis/status/2121022776794644848?a=FeR-tpH5bbNagL5bOsw&s=20

Retrieved post describes "idea basins": agents become anchored to previously tried methods. It cites Fork-and-Flush and Scaling Discovery requires forgetting as context-deletion approaches. It reports declining exploration later in runs and larger average gains from new-basin exploration. These are the author's reported findings; the underlying study/blog was not independently reviewed here. Do not treat those effect sizes as established for Hermes.

## Connection to intent control

Keep the destination stable while relaxing attachment to the current route. State estimation and feedback should recognize stalled approaches, not merely push harder on the same plan. Context relaxation is a possible exploration action in the existing observe/correct/continue loop.

Candidate design, not an agreed API:

1. Preserve canonical user intent, original authoritative requirements, manual edits/pins, permissions, essential environment facts, and independently verified outcomes.
2. In a fresh exploratory branch/request, selectively omit or compress model-authored plans, rationales and repetitive attempt history that may anchor the search. Preserve a compact factual failure record where needed to avoid unsafe or already-refuted repetition.
3. Propose materially different approaches, not merely different wording. New approaches do not authorize new scope or external actions.
4. Evaluate candidate approaches/artifacts against the unchanged task and evidence. Reintegrate useful findings with provenance, not overwrite canonical history.

Use separate exploration context rather than rewriting a live session's cached prefix or silently deleting its durable transcript. Keep omitted/retained context inspectable. 'Dropout' here is context-level perturbation, not neural weight dropout or simply raising sampling temperature.

## Dynamical-systems framing

Treat phase-space descriptions and dynamical-systems mechanisms as central to the research design, not decorative metaphors. Operationalize them using observable state and testable interventions:

- State: actor-visible context, working intent, current approach, tool/environment state, artifact evidence and unresolved discrepancies. The model/harness/environment jointly generate trajectories; text alone is an incomplete observation.
- Phase-space description: inspectable coordinates such as approach family, unresolved requirements, verified outcomes, uncertainty and implementation complexity. These are task-dependent projections, not a claimed reconstruction of neural latent space.
- Attractor/basin: recurrent, self-reinforcing patterns of approach and behavior. Distinguish an intended goal region (satisfying full user intent) from a method basin (which can trap work without achieving that intent). Not every trajectory has a fixed-point attractor; user steering also changes the dynamics.
- Feedback/control: observations update the state estimate; discrepancies inform actions that change artifacts, gather evidence, or change the search process itself. Repeated activity with little verified progress can motivate exploration rather than more of the same action.
- Context dropout/forgetting: selectively reduce conditioning on prior attempts while retaining authoritative requirements and essential evidence. This changes effective history-dependent priors, not the model's trained weights.
- Annealing/relaxation: temporarily broaden exploration of approaches or weaken commitment to a current plan, then reconsolidate around independently verified useful findings. Sampling temperature is one possible lever, not a complete implementation of simulated annealing; any annealing claim needs a specified proposal/selection schedule and objective.
- Better basins: approaches producing more of the full intended outcome with less unnecessary complexity, cost or user intervention, subject to constraints. Novelty alone is not improvement, and a new basin is a hypothesis to test rather than a label inferred from different wording.

The key experiment is whether a controlled change in context or search policy changes subsequent trajectories and improves outcomes under matched resources. Operational measures must distinguish productive basin transitions from random drift, repeated failures, and scope dilution. No assumption of a single meaningful scalar distance or monotonic progress is required.

## Open questions for later experiments

- Trigger: user request, repeated unproductive attempts, stalled verified progress, or scheduled exploration?
- Intervention: selective masking, compressed evidence-only restart, or multiple independently seeded branches?
- Retention: what minimal evidence prevents repeating failures without recreating fixation?
- Evaluation: compare unchanged-context continuation against relaxation under matched model/work budgets; measure independently verified improvement, genuinely different methods, repeated failures, constraint adherence and cost.

No implementation, paid experiment, scheduler, or automatic context deletion is authorized by this research note alone.
