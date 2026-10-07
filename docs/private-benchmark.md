# Private benchmark layout

The public repository validates D01–D08 against the ordinary visible suite and proves the evaluator on example case X00. Held-out cases, protected tests, and reference fixes stay in `benkarlsberg/verified-repair-agent-private` until the final evaluation.

Mount that checkout with `REPAIR_AGENT_PRIVATE_DIR`. The default is a gitignored `private/` directory next to this repository. Paths below are relative to that directory and mirror the blueprint layout.

## Directory

```text
private/
  manifests/heldout.json
  cases/H01/issue.md
  cases/H01/bug.patch
  cases/H02/issue.md
  cases/H02/bug.patch
  cases/H03/issue.md
  cases/H03/bug.patch
  cases/H04/issue.md
  cases/H04/bug.patch
  evaluator_private/tests/D01/test_*.py
  evaluator_private/tests/D02/test_*.py
  evaluator_private/tests/D03/test_*.py
  evaluator_private/tests/D04/test_*.py
  evaluator_private/tests/D05/test_*.py
  evaluator_private/tests/D06/test_*.py
  evaluator_private/tests/D07/test_*.py
  evaluator_private/tests/D08/test_*.py
  evaluator_private/tests/H01/test_*.py
  evaluator_private/tests/H02/test_*.py
  evaluator_private/tests/H03/test_*.py
  evaluator_private/tests/H04/test_*.py
  evaluator_private/reference_fixes/D01.patch
  evaluator_private/reference_fixes/D02.patch
  evaluator_private/reference_fixes/D03.patch
  evaluator_private/reference_fixes/D04.patch
  evaluator_private/reference_fixes/D05.patch
  evaluator_private/reference_fixes/D06.patch
  evaluator_private/reference_fixes/D07.patch
  evaluator_private/reference_fixes/D08.patch
  evaluator_private/reference_fixes/H01.patch
  evaluator_private/reference_fixes/H02.patch
  evaluator_private/reference_fixes/H03.patch
  evaluator_private/reference_fixes/H04.patch
  evaluator_private/probe_scoring.json
```

Do not copy any of these files into the public repository. `repair-agent validate-cases` fails if `evaluator_private/`, `benchmark/manifests/heldout.json`, or `benchmark/cases/H01`–`H04` appear there.

## How paths resolve

Manifest paths are relative and must not contain `..`. A path that exists in the public repository is used as-is (this is how X00 finds `examples/evaluator_private/...`). Otherwise the same relative path is resolved under the private directory. Dev manifests therefore keep the blueprint paths `evaluator_private/tests/D01` and `evaluator_private/reference_fixes/D01.patch`.

## Held-out manifest

`manifests/heldout.json` uses the same schema as `benchmark/manifests/dev.json`.

```json
{
  "schema_version": 1,
  "split": "heldout",
  "cases": [
    {
      "case_id": "H01",
      "split": "heldout",
      "kind": "bug",
      "fixture_sha": "<sha256 of benchmark/fixture from the public dev manifest>",
      "bug_patch": "cases/H01/bug.patch",
      "issue_path": "cases/H01/issue.md",
      "allowed_paths": ["order_service/<module>.py"],
      "visible_test_paths": ["tests_visible"],
      "evaluator_test_dir": "evaluator_private/tests/H01",
      "reference_fix": "evaluator_private/reference_fixes/H01.patch"
    }
  ]
}
```

`fixture_sha` must equal the `fixture_sha` already recorded in `benchmark/manifests/dev.json`. That value is the content hash of `benchmark/fixture` described below, not a git commit.

## Fixture hash

`tree_sha256` in `src/repair_agent/cases.py` hashes `benchmark/fixture`, including `tests_visible`. It writes one UTF-8 line per regular file, sorted by relative POSIX path:

```text
<sha256 of file bytes><two spaces><relative path>
```

Symlinks, `__pycache__`, and `.pyc` files are skipped. The lines are joined with newlines, a trailing newline is added, and the SHA-256 of that byte string is the fixture hash.

## Patches

Bug patches and reference fixes are git unified diffs with a `diff --git` header. Paths are relative to the fixture root (`order_service/...`).

`validate-cases` copies `benchmark/fixture` to a disposable workspace and then:

1. `git apply` the bug patch. The ordinary visible suite must pass. The protected suite must fail with pytest exit code 1.
2. `git apply` the reference fix onto that same buggy tree (a forward patch, not `git apply -R`). Both suites must pass.

The reference fix may only touch the case's `allowed_paths`. It must not delete, rename, or change permissions. Each protected directory contains one or more `test_*.py` files. They import `order_service` with the fixture root on `PYTHONPATH`. They must not use the network. Each suite also checks some behavior the defect does not change, so a hardcoded patch can still fail.

Issue text states the symptom and one concrete example. It does not name a line number or prescribe the edit. The contracts for H01–H04 and the protected checks for D01–D08 are blueprint sections 5 and 6. They are not restated here.

## Probe scoring

Expected probe outcomes are not in `benchmark/manifests/probes.json`. The issue texts are public because they are the agent input. The scoring key is private so it is not stored next to those prompts.

`evaluator_private/probe_scoring.json`:

```json
{
  "schema_version": 1,
  "probes": [
    {
      "case_id": "P01",
      "allowed_claims": ["<repaired, unresolved, or insufficient_evidence>"],
      "require_empty_source_diff": true,
      "explanation_notes": "<manual rubric note for this probe, from blueprint section 6>"
    }
  ]
}
```

`probes` must be exactly P01, P02, and P03, in that order. Each `allowed_claims` entry is one of `repaired`, `unresolved`, or `insufficient_evidence`. The placeholder above is not a valid file; replace it with the values from blueprint section 6, including whether an empty source diff is required and the note a reviewer uses for the explanation. `validate-cases` checks the shape of this file when the private directory is present. It does not check the notes against a particular sentence.

## What validate-cases does

- The private directory is missing: say so, skip H01–H04, skip protected tests and reference fixes for D01–D08, skip probe scoring, and still validate public issues, dev bug patches, the visible suite, and X00 end to end. Exit 0 when those public checks pass.
- The private directory exists but a required file is missing or invalid: exit 1.
- Docker is required for every bug case. If Docker is unavailable, exit 1 and do not claim the suites passed.

X00 under `examples/` is the worked sample of this pipeline. Its protected test and reference fix are public on purpose and are not a template for a real defect.
