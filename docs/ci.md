# Continuous integration

seatrial needs no plugin for any CI platform. Every integration uses three
outputs that work anywhere:

| Output | Flag | Use |
|---|---|---|
| Comment Markdown on stdout | `--format comment` | Post as a pull request or merge request comment |
| Comment Markdown appended to a file | `--summary-file PATH` | Fill a job summary, or keep as an artifact |
| One JSON document on stdout | `--json` | Parse verdicts and readings in scripts |

The exit code gates the job: `--fail-on-regression` exits 4 on any `regressed`
verdict. [Exit codes](agents.md#exit-codes) lists the rest.

## What the comment contains

`--format comment` prints Markdown in this order:

1. One line per verdict count, such as `- **regressed**: 1`, with how many
   of them are `cheaper` or `costlier`.
2. **Noisy cases**, in an A/A run: cases whose identical arms still gave
   `improved` or `regressed`, with how far they drifted.
3. **Needs attention**: the `regressed` and `inconclusive` results, each with
   its effect, its reading and, when the run was too small, how many trials
   would decide it.
4. **Pooled by skill**: one line per skill, marked as reported and not gated.
5. Notes on the A/A label or noise floor, the correction, and sequential
   stops, when the run used them, then the total host-reported cost.
6. The full results table and the reading of every verdict, inside an HTML
   `<details>` block. Renderers without HTML support show the tags as text.

Progress and the run directory go to stderr, so stdout holds only the Markdown.
`--summary-file` appends the same Markdown with any `--format`, and with
`--json`. The run directory stays out of the Markdown because it is a local path.

## Any platform

```sh
seatrial compare --base origin/main --skill my-skill --n 10 --max-cost-usd 5 \
  --format comment --summary-file seatrial.md --fail-on-regression
```

Then post `seatrial.md` with your platform's comment command or API, or keep it
as a job artifact. Run the post step even when seatrial exits 4, so the comment
explains the failure, and skip it when the file is empty: seatrial then failed
before it printed a report. A run that stopped early says so on the comment's
first line. Above about 50,000 characters, the comment replaces the full table
with a pointer to `seatrial report`.

To gate on more than regressions, read the JSON. This command fails the job when
any comparison was too small to decide:

```sh
seatrial report latest --json | jq -e '[.summary.cases[].hosts[].interpretation | select(. != null and .underpowered)] | length == 0'
```

[Using seatrial from an agent](agents.md#json-output) lists the `interpretation`
fields.

## Host CLIs in CI

Each native trial runs a host CLI, so the CI image needs that CLI installed and
signed in, and every trial spends that account's quota. Set `--n`,
`--max-runs`, `--max-minutes`, and `--max-cost-usd` in the job.
[Hosts](hosts.md) describes each adapter.

## Sign in with an API key

CI has no browser, so sign in with an API key stored as a CI secret, not with a
subscription login. Expose the key to the seatrial step as an environment
variable. seatrial passes each adapter's own variables to its trials and drops
the rest of the environment:

| Host | Secret as | Adapter passes | Extra step |
|---|---|---|---|
| `claude` | `ANTHROPIC_API_KEY` | `ANTHROPIC_*`, `CLAUDE_CODE_*`, `AWS_*`, and cloud provider variables | None. Each trial gets a private `CLAUDE_CONFIG_DIR` |
| `codex` | `OPENAI_API_KEY` | `OPENAI_*`, `AZURE_OPENAI_*` | Write a login file once, before seatrial runs (below) |

Codex CLI reads its login from `CODEX_HOME`, and seatrial copies that login into
each trial. Create it from the key:

```sh
export CODEX_HOME="$RUNNER_TEMP/codex-home"   # any private folder
mkdir -p "$CODEX_HOME" && chmod 700 "$CODEX_HOME"
printenv OPENAI_API_KEY | codex login -c 'cli_auth_credentials_store="file"' --with-api-key
```

Keep `CODEX_HOME` set for the seatrial step. A proxy or gateway that needs other
variables, such as `MY_PROXY_TOKEN`, works once you name them in
[`run.pass_env`](configuration.md). seatrial writes no environment to the run
directory, but the agent in a trial sees the key and can print it into its
trace. The run directory is as sensitive as the key: anyone who can download
the job's artifacts can read it. Keep `set -x` and `env` out of the
job, or the log shows the key.

Run `seatrial doctor` in the job to check that each host CLI is on `PATH`.
`doctor` does not test the key; the first trial does, and a rejected key stops
the run with exit 1.

## Two lanes

Split the cost between a fast check on every pull request and a full check on a
schedule. Both use `--model HOST=MODEL`, so neither lane changes
`seatrial.toml`:

| Lane | Trigger | Model | Trials | Selection |
|---|---|---|---|---|
| Pull request | Each push to a pull request | A small, cheap model, such as `--model claude=claude-haiku-5-5` | `--n 10` with `--sequential`: a clear case stops after 5 trials per revision. `--sequential` needs an `--n` above 5 to take more than one look | `--changed origin/<target>`: only the cases the change affects |
| Nightly | A schedule on the default branch | The model people use, such as `--model claude=claude-opus-5-5` | The configured `run.n`, or 10 to claim an effect | Every case, compared with the last release tag or a fixed baseline revision |

A cheap model can miss a regression that the full model shows, and the reverse.
Treat the pull request lane as a smoke check and the nightly lane as the
evidence. Use `--correction holm` when many cases run, so one lucky case does
not fail the gate; the reusable workflow below uses it by default.
[Statistics](statistics.md) explains the corrections and how many trials
decide a verdict.

A run whose two revisions mount identical skills, such as a push to the default
branch compared with itself, is labelled `a/a`: its differences measure noise,
and its report lists noisy cases first. Pass such a run to later comparisons
with `--noise-floor RUN_DIR`; see [the noise floor](statistics.md#noise-floor-aa-runs).

## GitHub Actions

### Reusable workflow

The seatrial repository ships `.github/workflows/seatrial.yml`. It installs
seatrial, runs your setup script, signs in to Codex CLI from `OPENAI_API_KEY`,
runs `seatrial compare --changed`, posts the comment, uploads the run
directory, and sets the job result from seatrial's exit code:

| Exit | Job | Comment |
|---|---|---|
| 0 | Passes | Posted |
| 5, no affected case | Passes | None; the job summary says why |
| 4, a regression | Fails | Posted |
| 1, stopped early at `max-cost-usd`, `max-minutes`, a usage limit, or a sign-in failure | Fails | Posted; its first line says the results are partial |
| 1, could not start, such as on a missing host CLI | Fails | None; read the Evaluate step's log |
| 2 or 3, a usage or configuration error | Fails | None; read the Evaluate step's log |

Pin the workflow to a release tag or commit SHA, and set `seatrial-version` to
the same release, so the CLI accepts the options the workflow passes:

<!-- x-release-please-start-version -->

```yaml
name: Skills

on:
  pull_request:
  schedule:
    - cron: "0 3 * * *"

jobs:
  pull-request:
    if: github.event_name == 'pull_request'
    uses: otaviobarros/seatrial/.github/workflows/seatrial.yml@v0.5.0
    permissions:
      contents: read
      pull-requests: write
    with:
      seatrial-version: 0.5.0
      setup: npm install -g @anthropic-ai/claude-code
      hosts: claude
      models: claude=claude-haiku-5-5
      n: 10
      max-cost-usd: "2"
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

  nightly:
    if: github.event_name == 'schedule'
    uses: otaviobarros/seatrial/.github/workflows/seatrial.yml@v0.5.0
    permissions:
      contents: read
    with:
      seatrial-version: 0.5.0
      setup: npm install -g @anthropic-ai/claude-code
      hosts: claude
      models: claude=claude-opus-5-5
      base-ref: origin/stable
      changed: false
      sequential: false
      n: 10
      max-cost-usd: "40"
      retention-days: 30
      history: true
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

<!-- x-release-please-end -->

The nightly job compares the default branch with `origin/stable`, a branch
that holds your last released skills in this example; a release tag works too.
Without `base-ref`, the workflow uses `origin/` plus the pull request's target
branch, or the default branch on other events. On the default
branch itself, such as on a schedule, that compares the branch with itself, so
set `base-ref`.

The nightly lane runs every case 10 times per revision. With more than five
cases on one host, that exceeds the default `limits.max_runs` of 100, and one
trial at a time can outlast `limits.max_minutes` and `timeout-minutes`. Raise
`[limits]` and `run.jobs` in `seatrial.toml` and `timeout-minutes` to fit.

| Input | Default | Meaning |
|---|---|---|
| `seatrial-version` | latest | Version from PyPI; match the workflow's tag |
| `seatrial-package` | none | pip requirement installed instead, such as a Git URL; a relative path resolves against the checkout |
| `python-version` | `3.12` | Python for seatrial |
| `runs-on` | `ubuntu-latest` | Runner label |
| `working-directory` | `.` | Folder with `seatrial.toml` |
| `setup` | none | Shell script that installs the host CLIs |
| `base-ref` | `origin/<target>` | Revision passed to `--base` and `--changed` |
| `changed` | `true` | Add `--changed <base-ref>` |
| `select` | none | More selectors, such as `--skill my-skill` |
| `hosts` | each case's hosts | Space-separated hosts, each passed as `--host` |
| `models` | `[hosts.models]` | Space-separated `HOST=MODEL` pairs, each passed as `--model`; name only selected hosts, or set `hosts`, because a pair for an unselected host is a usage error |
| `n` | `run.n` | Trials per case, host, and revision; `0` keeps the configured value |
| `sequential` | `true` | Add `--sequential` |
| `correction` | `holm` | `--correction`: `none`, `holm`, or `bh` |
| `max-cost-usd` | no cap | `--max-cost-usd`, as a string |
| `max-minutes` | `limits.max_minutes` | `--max-minutes`; `0` keeps the configured limit |
| `extra-args` | none | More `compare` options, such as `--effort low` |
| `dry-run` | `false` | Write the trial plan to the job summary; make no native calls |
| `comment` | `true` | Post the comment on the pull request |
| `retention-days` | `14` | Days to keep the run directory artifact; `0` uses the repository setting |
| `artifact-name` | `seatrial-run-<run id>-<attempt>` | Run directory artifact name; set a distinct one for each call in one workflow run |
| `history` | `false` | Keep the trend history in the Actions cache and add `seatrial trend` to the job summary |
| `history-key` | `seatrial-history` | Cache key prefix for the history; give each lane, and each call in one workflow run, its own |
| `history-last` | `10` | Runs per case, host, and arm in the job summary's trend; `0` shows all |
| `env` | none | Non-secret `NAME=VALUE` lines for the host CLIs, one per line |
| `timeout-minutes` | `120` | Job timeout |

Every secret is optional; pass the ones your hosts sign in with.

| Secret | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | The `claude` host, with an Anthropic API key |
| `CLAUDE_CODE_OAUTH_TOKEN` | The `claude` host, with a Claude Code OAuth token in place of an API key |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | The `claude` host through Amazon Bedrock |
| `OPENAI_API_KEY` | The `codex` host; the workflow runs `codex login` with it |
| `AZURE_OPENAI_API_KEY` | The `codex` host through Azure OpenAI |

A provider also needs settings that are not secret. Put them in `env`; the
workflow rejects lines that are not `NAME=VALUE`. For Claude through Bedrock:

```yaml
    with:
      env: |
        CLAUDE_CODE_USE_BEDROCK=1
        AWS_REGION=us-east-1
    secrets:
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

Only `ANTHROPIC_API_KEY` gives each Claude trial a private `CLAUDE_CONFIG_DIR`.
With the other sign-ins, trials share the runner's Claude configuration, which
is empty on a fresh hosted runner. Vertex AI needs a credentials file that the
workflow does not create; use your own steps for it.

The workflow sets `SEATRIAL_STATE` to a temporary folder and uploads it as the
artifact `seatrial-run-<run id>-<attempt>`, or `artifact-name`. Two calls in
one workflow run, such as one job for each host, need distinct `artifact-name`
values, or the second upload fails. `actions/upload-artifact` skips
hidden paths, so copied logins never reach the artifact. A `run.state_dir` in
`seatrial.toml` overrides `SEATRIAL_STATE`; remove it, or the artifact is
empty. The job output `exit-code` holds seatrial's exit code. Each run posts a
new comment.

The job takes the caller's token permissions. Grant `pull-requests: write` for
the comment. On a fork's pull request, the token stays read-only and secrets are
absent, so the comment step fails without failing the job and native trials
cannot sign in. Run the reusable workflow only for branches in your repository,
or set `dry-run: true` for forks.

### Trend history

Each runner starts empty, so the history that `seatrial trend` reads needs a
place between runs. With `history: true`, the workflow restores
`history.jsonl` from the Actions cache before the run, appends the run's rows,
adds the trend table to the job summary, and saves the file under a new cache
key. The run directory artifact holds a copy too.

Turn it on for the nightly lane, which runs every case on the same baseline and
model each night. Leave it off for the pull request lane: its rows come from
branches under review and from a cheaper model, so they would break the series.
Two lanes that both keep history need distinct `history-key` values, and so
do two calls in one workflow run, such as one job for each host: calls that
share a key save to the same cache entry, and only the first save keeps its
rows.

`trend` keeps only rows whose project root matches the checkout's absolute
path. Keep the lane on one kind of runner: a runner whose workspace path
differs, such as a self-hosted or container runner after `ubuntu-latest`,
starts a new series, and the earlier rows stay in the file but are hidden.

GitHub evicts a cache entry unused for 7 days, and a pull request branch reads
the default branch's entries but cannot write them. A nightly lane keeps its
entry in use. To keep the history longer, copy `history.jsonl` from the artifact
to storage you control.

On another platform, keep `history.jsonl` from the state directory
(`SEATRIAL_STATE`, or `run.state_dir`) in that platform's cache, and restore it
there before the next run.

### Your own steps

```yaml
permissions:
  contents: read
  pull-requests: write

steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0
  - name: Evaluate
    run: >
      seatrial compare --base origin/${{ github.base_ref }} --skill my-skill --n 10
      --max-cost-usd 5 --format comment --summary-file "$GITHUB_STEP_SUMMARY"
      --fail-on-regression > seatrial.md
  - name: Comment
    if: always() && github.event_name == 'pull_request'
    run: |
      [ -s seatrial.md ] || exit 0
      gh pr comment ${{ github.event.pull_request.number }} --body-file seatrial.md
    env:
      GH_TOKEN: ${{ github.token }}
```

`GITHUB_STEP_SUMMARY` names the job summary file, so the same Markdown also
appears on the run page. Pull requests from forks get a read-only token, so the
Comment step fails there; the job summary still works. An empty `seatrial.md`
means seatrial failed before it printed: read the Evaluate step's log.

## GitLab CI

```yaml
seatrial:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - status=0
    - seatrial compare --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --skill my-skill --n 10
      --max-cost-usd 5 --format comment --fail-on-regression > seatrial.md || status=$?
    - glab mr note "$CI_MERGE_REQUEST_IID" --message "$(cat seatrial.md)"
    - exit $status
  artifacts:
    when: always
    paths: [seatrial.md]
```

`glab` needs a token with API scope in `GITLAB_TOKEN`.

## Azure Pipelines

```yaml
steps:
  - checkout: self
    fetchDepth: 0
  - script: >
      seatrial compare --base origin/main --skill my-skill --n 10
      --max-cost-usd 5 --format comment --summary-file $(Agent.TempDirectory)/seatrial.md
      --fail-on-regression
    displayName: Evaluate
  - script: echo "##vso[task.uploadsummary]$(Agent.TempDirectory)/seatrial.md"
    displayName: Attach summary
    condition: always()
```

The `task.uploadsummary` command adds the Markdown to the run's summary tab.
Replace `origin/main` with the pull request's target branch.
