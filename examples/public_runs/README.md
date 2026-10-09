# Example recordings

These two bundles are example case X00, produced with `repair-agent run --model fake`. Each `public.json` records `provider: fake` and `model_id: scripted-fake`. Estimated cost is not applicable.

- Iterative `x00-pass`: the agent claims repaired and the evaluator records passed.
- One-shot `x00-fail`: the agent claims repaired and the evaluator records failed.

They are a recorded demo, not held-out results. The directory `public_runs/` at the repository root is gitignored. Benchmark recordings are published there separately.
