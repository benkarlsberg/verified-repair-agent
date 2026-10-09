# Verified Repair Agent

A local agent that repairs deterministic bugs in a small Python order service, then submits the patch to an independent evaluator. The evaluator decides whether the repair holds. The agent only records what it believes it did.

This repository is the public half of that project. Held-out cases and protected tests live in a separate private checkout.

## Status

Implemented:

- order-service fixture and visible tests
- development cases D01–D08
- abstention probes P01–P03
- example case X00, with its protected test and reference fix
- patch policy
- offline Docker runner
- iterative repair loop and one-shot baseline
- OpenAI adapter and a scripted fake model
- independent evaluator and run bundles
- `repair-agent validate-cases`
- `repair-agent freeze`
- `repair-agent run`
- `repair-agent evaluate`

Not implemented yet:

- replay viewer
- publish step
- evaluation report

## Quickstart

Python 3.11 and Docker are required. [uv](https://docs.astral.sh/uv/) installs the locked dependencies.

```bash
uv sync
uv run pytest
uv run repair-agent validate-cases --split all
uv run repair-agent freeze --output freeze.json
```

`pytest` covers the visible fixture suite, the patch policy, the runner, and the repair loop. The runner checks and the X00 fake-model pipeline start a container and are skipped when Docker is not available. `validate-cases` does not skip those runs: without Docker it exits 1 and says that no fixture tests were executed. Loop tests use the fake model and do not call the network.

`repair-agent report` and `publish` exit 2. They are not implemented yet. The replay viewer is not implemented yet.

## Two repositories

| Repository | What it holds |
| --- | --- |
| `benkarlsberg/verified-repair-agent` (this repo, public) | Fixture, visible tests, D01–D08 issues and bug patches, probe issues, example case X00, runner, patch policy, repair loop, evaluator |
| `benkarlsberg/verified-repair-agent-private` | `evaluator_private/` for every case, held-out cases H01–H04, `manifests/heldout.json`, probe scoring |

Point `REPAIR_AGENT_PRIVATE_DIR` at a checkout of the private repo. The default is a gitignored `private/` directory. The layout the private repo must match is in [`docs/private-benchmark.md`](docs/private-benchmark.md).

If that directory is missing, `validate-cases` says so and still checks everything public. It skips H01–H04, the protected tests and reference fixes for D01–D08, and probe scoring. It does not treat those skips as passes of the hidden suites.

A public repository is not secrecy from model pretraining. The split is workspace isolation: agent tools see one materialized case, not the oracle files. Publishing the held-out set after the frozen evaluation is fine if that disclosure is explicit. Tuning on those outcomes would need a new holdout.

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

The patch policy allowlists the case's source paths and new files matching `agent_tests/test_*.py`. It rejects traversal, binaries, deletes, renames, permission changes, and symlinks, and it rejects edits to existing tests, dependency files, and pytest configuration. Diffs are limited to 200 changed lines and 5 files. `git apply --check --recount` runs in a disposable copy, so a unified diff with a wrong hunk line count can still apply. Other patch formats, including `*** Begin Patch`, are rejected. The policy does not import the service.

`repair-agent freeze` writes hashes of the fixture, the public manifests, `config/defaults.json`, and, when the private directory is present, the benchmark and config files in that checkout. A relative `--output` path is written from the current working directory. VCS metadata, virtual environments, and caches are left out, so committing the freeze does not invalidate the private-dir hashes.

## Honest limitations

Docker limits reduce exposure for this owned fixture. They are not a strong boundary against hostile arbitrary code. Generated tests and modified service code are untrusted. This project does not accept outside repositories, credentials, or live public repair execution.

The patch policy is a curated-task filter. It does not detect removal of an API by rewriting a function body, and it is not a malware scanner. Protected tests can be inspected by code running in the evaluator container; host-side hashes and fixed test paths are the checks this release uses. Strong adversarial evaluation is out of scope.

The benchmark is twelve hand-written defects in one service, plus three probes. Four of the defects are held out. A result on that set is not a claim about software repair in general, and it is not a comparison with commercial agents. D04 and H02 both involve order idempotency; the evaluation report has to say that this is within-service generalization, not transfer to unfamiliar code.

`config/defaults.json` pins `gpt-5.4-mini-2026-03-17` for both methods. That is the dated snapshot on the OpenAI model page; `gpt-5.4-mini` is its alias. Requests set `reasoning.effort` to `low` and do not send `temperature` or `top_p`. Reasoning tokens count as output and toward the 4,000-token response cap. Cost is estimated from the October 2026 prices on that page ($0.75 / 1M input, $0.075 / 1M cached input, $4.50 / 1M output). A missing cache split is labeled unavailable. No paid call is made unless you run `repair-agent run` or `evaluate` with a key.

## Run an attempt

`run` and `evaluate` read `OPENAI_API_KEY` from the environment when `--model openai` is selected (the default). They do not read a key file, and they do not write the key into `runs/`. `validate-cases`, `freeze`, and `--model fake` do not need a key.

Point `REPAIR_AGENT_PRIVATE_DIR` at a checkout of the private benchmark before a development or held-out attempt. Without it, D01–D08 can still be attempted, but the evaluator has no protected tests and records `infra_error` once a source diff exists. An empty source diff on a bug case is `rejected`. Example case X00 carries its protected test in this repository.

```bash
export OPENAI_API_KEY=...
export REPAIR_AGENT_PRIVATE_DIR=/path/to/verified-repair-agent-private

uv run repair-agent run --case D01 --method iterative --repeat 1
uv run repair-agent run --case D01 --method one_shot --repeat 1
```

`evaluate` runs a split. `--method both` alternates which method starts each repetition. Held-out cases are refused unless you pass `--allow-heldout`.

```bash
uv run repair-agent evaluate --split dev --method both --repeats 1
uv run repair-agent evaluate --split heldout --method both --repeats 3 --allow-heldout
```

`--model fake` runs the same controller, patch policy, and Docker evaluator with a scripted model. `x00-pass` and `x00-fail` only apply to example case X00. `abstain` finishes with an empty diff on any case.

```bash
uv run repair-agent run --case X00 --method iterative --model fake --fake-script x00-pass --repeat 1
uv run repair-agent run --case X00 --method iterative --model fake --fake-script x00-fail --repeat 1
```

Each attempt writes `runs/<run_id>/` with `result.json`, `events.jsonl`, `final.patch`, `visible_tests.txt`, `evaluator.json`, and `evaluator.txt`. `runs/` is gitignored. The evaluator runs after model access is closed, in a fresh workspace, and its output is not sent back to the model.

Budgets for one attempt are 10 minutes, 12 model responses, 30 tool calls, 3 source patch submissions, a provisional cumulative cap of 160,000 input plus output tokens, and 4,000 output tokens per response. The cumulative cap is provisional until the final freeze. Measured D01 usage was about 82–84k input tokens over 8–9 responses, about 10.5k re-sent per turn, roughly 85% of it cached. Cached input still counts at full weight toward that token budget. The loop stops before a request whose estimated input plus the output cap would exceed the remaining tokens. When a normal tool round no longer fits and a call that allows only `finish` does, that finish-only call is the last model request. If even that call would not fit, the attempt stops as `unresolved`. A budget stop keeps the latest valid source diff and records an `unresolved` claim when `finish` was not called.

## Layout

```text
src/repair_agent/     CLI, model adapter, loop, tools, evaluator, bundles
benchmark/fixture/    correct order service and visible tests
benchmark/cases/      D01–D08 issue text and bug patches
benchmark/probes/     P01–P03 issue text
benchmark/manifests/  dev.json and probes.json
examples/             public X00 oracle
config/               model id, budgets, pricing, service contract
docker/               runner image
tests/                policy, runner, loop, and manifest checks
docs/                 architecture and private-benchmark layout
runs/                 local attempt bundles, gitignored
```

`publish.py` and `web.py` are still stubs. See [`docs/architecture.md`](docs/architecture.md).
