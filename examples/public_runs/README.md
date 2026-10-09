# Example recordings

These two bundles are example case X00, produced with `repair-agent run --model fake`. Each `public.json` records `provider: fake` and `model_id: scripted-fake`. Estimated cost is not applicable.

- Iterative `x00-pass`: the agent claims repaired and the evaluator records passed.
- One-shot `x00-fail`: the agent claims repaired and the evaluator records failed.

They are sample recordings, not held-out results. Development recordings for D01–D08 live in `examples/dev_runs/`. The directory `public_runs/` at the repository root is gitignored.
