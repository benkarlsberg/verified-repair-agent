# Verified Repair Agent

A local agent that repairs deterministic bugs in a small Python order service, then submits the patch to an independent evaluator. The evaluator decides whether the repair holds. The agent only records what it believes it did.

This repository is the public half of that project. It is week 1 of 4: the order-service fixture, the eight development cases, three abstention probes, a patch policy, and an offline Docker runner. The model adapter, repair loop, one-shot baseline, replay viewer, and evaluation report are not implemented yet.

## Quickstart

Python 3.11 and Docker are required. [uv](https://docs.astral.sh/uv/) installs the locked dependencies.

```bash
uv sync
uv run pytest
uv run repair-agent validate-cases --split all
uv run repair-agent freeze --output freeze.json
```

`pytest` covers the visible fixture suite, the patch policy, and the runner. The runner checks start a container and are skipped when Docker is not available. `validate-cases` does not skip those runs: without Docker it exits 1 and says that no fixture tests were executed.

`repair-agent run`, `evaluate`, `report`, and `publish` exit 2. They belong to later weeks. Nothing in week 1 calls a model or reads an API key.

## Two repositories

| Repository | What it holds |
| --- | --- |
| `benkarlsberg/verified-repair-agent` (this repo, public) | Fixture, visible tests, D01–D08 issues and bug patches, probe issues, example case X00, runner, patch policy |
| `benkarlsberg/verified-repair-agent-private` | `evaluator_private/` for every case, held-out cases H01–H04, `manifests/heldout.json`, probe scoring |

Point `REPAIR_AGENT_PRIVATE_DIR` at a checkout of the private repo. The default is a gitignored `private/` directory. The layout the private repo must match is in [`docs/private-benchmark.md`](docs/private-benchmark.md).

If that directory is missing, `validate-cases` says so and still checks everything public. It skips H01–H04, the protected tests and reference fixes for D01–D08, and probe scoring. It does not treat those skips as passes of the hidden suites.

A public repository is not secrecy from model pretraining. The split is workspace isolation: agent tools in later weeks see one materialized case, not the oracle files. Publishing the held-out set after the frozen evaluation is fine if that disclosure is explicit. Tuning on those outcomes would need a new holdout.

## Example case X00

X00 is a misspelled display label in `order_service/example_ops.py`. It is not one of D01–D08 or H01–H04. Its protected test and reference fix live under `examples/` so this repository can show the full oracle path without publishing a benchmark answer.

`validate-cases` checks X00 the way it will check a real case once the private directory is mounted:

- the buggy fixture passes the ordinary visible tests
- the buggy fixture fails the protected test
- applying the reference fix passes both

## Cases in this repository

D01–D08 each have an `issue.md` and a `bug.patch` under `benchmark/cases/`. The issue states the symptom and one example. It does not name the faulty line. Visible tests cover routine behavior and do not catch that case's defect. The correct fixture is `benchmark/fixture/order_service/` (541 lines). Amounts are integer cents, clocks are injected, and there is no network, randomness, or sleeping.

P01–P03 are abstention probes. Their issue texts are public. The expected allowed outcomes are not in the public manifest; they belong in `evaluator_private/probe_scoring.json` in the private checkout. Putting that key next to the prompts would publish the intended response beside the prompt. The shared rule from the blueprint still applies: correct abstention is an empty source diff plus an unresolved or insufficient-evidence claim whose explanation fits the probe. Explanations are reviewed manually, and probe results stay outside the repair-rate denominator.

## Runner and patch policy

`docker/runner.Dockerfile` bakes the locked Python 3.11 and pytest dependencies. A run uses no network, a non-root user, a read-only root filesystem, dropped capabilities, `no-new-privileges`, a PID limit of 128, 1 CPU, 512 MB of memory, a 64 MB `/tmp` tmpfs, and a 30 second timeout. Only the disposable workspace is mounted, plus a read-only protected-test directory during an oracle run. Logs are capped at 64 KB and generated files at 10 MB. The container is removed on exit, timeout, and interrupt.

The patch policy allowlists the case's source paths and new files matching `agent_tests/test_*.py`. It rejects traversal, binaries, deletes, renames, permission changes, and symlinks, and it rejects edits to existing tests, dependency files, and pytest configuration. Diffs are limited to 200 changed lines and 5 files. `git apply --check` runs in a disposable copy. The policy does not import the service.

`repair-agent freeze` writes hashes of the fixture, the public manifests, `config/defaults.json`, and, when the private directory is present, every file in that checkout.

## Honest limitations

Docker limits reduce exposure for this owned fixture. They are not a strong boundary against hostile arbitrary code. Generated tests and modified service code are untrusted. This project does not accept outside repositories, credentials, or live public repair execution.

The patch policy is a curated-task filter. It does not detect removal of an API by rewriting a function body, and it is not a malware scanner. Protected tests can be inspected by code running in the evaluator container; host-side hashes and fixed test paths are the checks this release uses. Strong adversarial evaluation is out of scope.

The benchmark is twelve hand-written defects in one service, plus three probes. Four of the defects are held out. A result on that set is not a claim about software repair in general, and it is not a comparison with commercial agents. D04 and H02 both involve order idempotency; the evaluation report has to say that this is within-service generalization, not transfer to unfamiliar code.

Week 1 does not select or call a hosted model. `config/defaults.json` records the budgets from the blueprint and leaves the model id unset. The id is chosen with the adapter in week 2 and frozen in week 3. No paid LLM calls are part of this setup.

## Layout

```text
src/repair_agent/     CLI, schemas, patch policy, runner, case validation
benchmark/fixture/    correct order service and visible tests
benchmark/cases/      D01–D08 issue text and bug patches
benchmark/probes/     P01–P03 issue text
benchmark/manifests/  dev.json and probes.json
examples/             public X00 oracle
docker/               runner image
tests/                policy, runner, and manifest checks
docs/                 architecture and private-benchmark layout
```

Stub modules (`model.py`, `loop.py`, `tools.py`, `evaluate.py`, `artifacts.py`, `publish.py`, `web.py`) mark the later weeks. See [`docs/architecture.md`](docs/architecture.md) for the week-1 path.
