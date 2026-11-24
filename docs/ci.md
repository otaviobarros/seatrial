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

1. One line per verdict count, such as `- **regressed**: 1`.
2. The `regressed` and `inconclusive` results, each with its reading and, when
   the run was too small, how many trials would decide it.
3. The total host-reported cost.
4. The full results table and the reading of every verdict, inside an HTML
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

## GitHub Actions

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
