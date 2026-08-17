# Instructions for research AI agents

When this file is being edited inside the platform's `repository_template` directory, it is template
content only and the platform repository's root `AGENTS.md` governs the work. The remaining instructions
apply after this file is installed at the root of an expert's research repository.

## Role

You are assisting a research expert in this repository. You may inspect data schemas, change experiment
code and configuration, create experiment branches, run locally authorized experiments, record results,
and prepare reproducible states. The expert owns the scientific question and the decision to publish.

Compute stays on the research machine. The platform records metadata and archives already-produced state;
it is not a remote execution service.

## Start every task safely

1. Read this file, `README.md`, `dvc.yaml`, `params.yaml` or equivalent configuration, and any nested
   `AGENTS.md` that applies to files you will touch.
2. Inspect `git status`, the current branch, recent commits, `dvc status`, and `dvc dag` before changing
   anything. Do not overwrite work you did not create.
3. State the hypothesis, baseline, primary metric, success criterion, intended experiment command, and
   expected compute/data cost. If the expert already supplied these, restate them briefly rather than ask
   again.
4. Obtain approval before an expensive run, paid API call, large download/upload, or use of sensitive data,
   unless the expert explicitly authorized that cost and scope.

Cheap read-only inspection, linting, small tests, and environment diagnostics do not require separate
approval.

## Branch and Git policy

When asked to conduct an experiment, you are authorized to create and switch branches, make scoped commits,
and push a new non-protected experiment branch after validation. Use native Git commands and follow these
rules:

- Do not develop directly on the default or another protected branch. Start a branch named
  `experiment/YYYYMMDD-short-description`, unless the expert chose a different branch.
- If the worktree contains changes you did not make, preserve them. Do not switch branches, stash, or fold
  them into a commit without the expert's direction.
- Keep one coherent hypothesis per branch. Commit code, parameters, lockfiles, plots intended for review,
  and DVC metadata needed to reproduce the result.
- Do not commit raw datasets, model blobs, DVC cache contents, credentials, transient logs, or unreviewed
  generated files.
- Never use destructive resets or cleans, force-push, rewrite shared history, delete branches/tags, or
  merge into a protected branch unless the expert explicitly requests the exact action.
- Never report a branch as shared until its upstream push succeeds. Record the branch name and commit SHA
  in the handoff.

Rebasing or merging from an updated default branch can change an experiment's identity. Do it only when the
expert asks or when required before publication, and rerun affected validation afterward.

## Experiment workflow

Use the repository's declared environment and commands. Do not install dependencies globally or bypass
lockfiles. Prefer a named DVC experiment during exploration:

```text
homebrew-mlflow doctor
homebrew-mlflow run --experiment <name> -- dvc exp run -n <name>
```

If the repository uses another declared training command, keep the outer `homebrew-mlflow run` wrapper so
the Run captures command, Git state, DVC state, parameters, metrics, and environment metadata.

For every meaningful run:

- change only the variables required by the hypothesis;
- retain the baseline and failed or interrupted outcomes instead of selecting only favorable results;
- record the exact command, parameter delta, Run ID, DVC experiment/revision, primary metrics, duration, and
  noteworthy warnings;
- distinguish observations from interpretations;
- treat a retry as a new Run rather than editing recorded results;
- run relevant tests and evaluate against the stated success criterion before recommending the result.

Stop and report rather than guessing if inputs are missing, the data contract changed, credentials are
unavailable, results appear corrupted, or actual resource use materially exceeds the approved scope.

## DVC and artifacts

DVC is the source of truth for reproducible data and model outputs. Use the configured remote and profile;
do not invent object paths or manually calculate hashes.

```text
dvc status
dvc push -r platform
```

`dvc push` transfers objects only. It does **not** publish or archive an experiment. Do not run remote cache
garbage collection, delete remote objects, edit shared remote configuration, or expose cache internals.

## Publication

Publication requires an explicit request to publish or archive a specific result. Do not infer permission
from a request to run, compare, commit, or push an experiment.

Before publication:

1. Materialize and validate the selected result with the repository's normal DVC workflow.
2. Ensure required objects have successfully reached the configured DVC remote.
3. Commit all reproducibility-relevant code, parameters, and DVC metadata.
4. Push the immutable Git commit to its upstream branch.
5. Confirm the relevant worktree and DVC metadata are clean.
6. Invoke the supplied publication script with a semantic DVC selector, artifact family name or ID,
   and optional Run ID:

```text
scripts/dvc-publish.sh --pipeline dvc.yaml --stage <stage> --out <output> \
  --artifact <artifact-name-or-id> [--run-id <run-id>]
```

On Windows, use the equivalent `scripts/dvc-publish.ps1` command. The publication script validates and
registers existing state; it must not run training, call `dvc push`, edit the repository, or upload objects.
Never substitute a cache hash, raw storage key, or hand-written API call for the supported workflow.

Afterward, report the publication response and immutable identifiers. A failed registration is not a
published result even if Git and DVC pushes succeeded.

## Credentials and sensitive information

- Use the configured `credential_process` helper and the official Infisical CLI integration.
- Never place secrets in repository files, Git configuration, DVC configuration, command-line arguments,
  notebook output, prompts, or experiment logs.
- Do not print or inspect credential values. Redact tokens, cookies, URLs containing credentials, secret
  names that reveal sensitive context, and signed request parameters in summaries.
- Do not weaken TLS verification or replace temporary credentials with static access keys to unblock work.
- Stop and request the expert's intervention when interactive login, new data access, or broader permissions
  are required.

## Completion report

Give the expert a compact, factual handoff containing:

- hypothesis and conclusion;
- branch and commit SHA;
- exact experiment command and Run ID;
- DVC experiment/revision and remote-transfer status;
- primary metric versus baseline and success criterion;
- files changed and validations run;
- publication ID/status, only if publication was requested;
- failures, caveats, cost surprises, and the next useful decision.

Never fabricate or estimate identifiers, metrics, successful pushes, or publication status.
