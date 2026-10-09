# Architecture

```text
repair-agent run / evaluate
        |
        v
case materializer  -- fresh buggy workspace; probes copy the clean fixture
        |
        v
model adapter      -- OpenAI Responses API, or a scripted fake model
        |
        v
tool loop          -- list, read, search, patch, visible tests, finish
        |              one-shot: one JSON response, no tools
        v
patch policy       -- allowlist, size limits, git apply --check --recount
        |
        v
model access ends
        |
        v
evaluator          -- fresh workspace, final source diff only
        |
        v
run bundle         -- events.jsonl, result.json, final.patch, private evaluator files
        |
        v
publish            -- PublicRun only: summary, sanitized trace, diff, visible tests
        |
        v
replay viewer      -- read-only FastAPI app over public_runs
```

The host process calls the model and Docker. It does not import the order service while it is validating a patch. A target container receives one workspace and, during evaluation, a read-only protected-test directory. It does not receive the repository, the Docker socket, reference fixes, or environment secrets.

Agent tools resolve paths only inside that workspace. The initial prompt contains the issue, the public service contract, and the capped buggy source and visible tests. It does not contain the split, evaluator paths, or a reference fix.

The iterative loop asks for a reproduction under `agent_tests/test_*.py` and refuses source edits until that test has been run. Visible test output can inform a later edit. One malformed iterative response may be repaired; that extra response counts toward the budgets. The system prompt tells the agent to call `finish` once its reproduction and the visible tests pass. When the remaining token budget cannot fit another normal tool round, the loop makes one last call that allows only `finish`, if that call fits. The one-shot baseline gets the same case packet and one Responses API JSON-schema response. The text is still validated with Pydantic. A malformed response is rejected with no retry.

Responses are not stored. The next request resends sanitized function-call and assistant message items, without output-only fields such as `status`. Reasoning items are not replayed and are omitted from `events.jsonl` and `result.json`. Token totals are the provider usage of each request that was sent; cached input counts at full weight. The API key is read from `OPENAI_API_KEY` at runtime and is not written into a bundle.

The evaluator rebuilds the original buggy tree, applies only the net source diff, and runs the ordinary suite and the protected suite. Agent-written tests are not part of that workspace. `agent_claim` and `verification` are both stored. A repaired claim with a failed verification stays a false repair claim.

`REPAIR_AGENT_PRIVATE_DIR` points at the private checkout described in `docs/private-benchmark.md`. When that directory is absent, held-out cases and the D01–D08 oracles are skipped by `validate-cases`, and the public checks still run. Held-out repair attempts also require `--allow-heldout`.

Example case X00 uses the same steps with files under `examples/`. It is not part of the benchmark score.

`repair-agent publish` writes one `public.json` per finished attempt. The document is a `PublicRun`, not a copy of `result.json`. Evaluator logs, the evaluator hash, reference fixes, full prompts, host paths, and key-like strings are left out. A scan of the written files refuses the export if any of those remain. `repair-agent report` reads the local bundles and writes the evaluation tables. Development results stay in their own section.

The viewer process loads `public_runs`, or `examples/public_runs` when that directory is empty. It does not open `runs/` or the private checkout, and it has no route that starts a model call or a container. `/healthz` returns the schema version and the number of loaded bundles.
