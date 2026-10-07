# Architecture

Week 1 of 4. The repair loop, model adapter, viewer, and evaluation report are not built yet.

```text
validate-cases / freeze
        |
        v
case materializer  -- copies benchmark/fixture and applies one bug patch
        |
        v
patch policy       -- allowlist, size limits, git apply --check
        |
        v
Docker runner      -- pytest only, offline, non-root, disposable container
        |
        v
oracle check       -- visible suite, then protected suite when the files are mounted
```

The host process calls Docker and hashes files. It does not import the order service while it is validating a patch. The container receives one workspace directory and, for an oracle run, a read-only protected-test directory. It does not receive the repository, the Docker socket, reference fixes, or environment secrets.

`REPAIR_AGENT_PRIVATE_DIR` points at the private checkout described in `docs/private-benchmark.md`. When that directory is absent, held-out cases and the D01–D08 oracles are skipped and the public checks still run.

Example case X00 uses the same steps with files under `examples/`. It is not part of the benchmark score.

Later weeks add the model adapter and the bounded loop on this runner. The evaluator still applies only the final source diff, in a fresh workspace, after model access has ended. The public viewer will read sanitized bundles and will not run repairs.
