# agent-doctor reference

`agent-doctor` diagnoses selected agent-kit installation pieces with local,
read-only checks. It never executes checked commands, imports their code,
invokes a provider/model, signs, recovers, repairs or creates caches/config.
It uses only Python's standard library on macOS and Linux.

```sh
./bin/agent-doctor
./bin/agent-doctor --installed --command agent-quota --command agent-task
./bin/agent-doctor --skill-root ~/.agents/skills
./bin/agent-doctor --records-config ~/.config/agent-kit/records.json
```

The default command scope is the directory containing the doctor's resolved
source. Existing known commands are checked; absent unselected commands are
skipped. `--command NAME` makes that command required. `--installed` resolves
the known command names on PATH and inspects files without running them.
`--bin-dir DIR` selects a different directory. Missing explicitly selected
commands and missing command directories are errors. Requirements apply only
to present or selected pieces; tmux, Git and uv are checked for executable
availability, never invoked. Providers and authentication remain unverified.

## Checks and scope

- Commands: executable files, Python syntax and required local imports or
  literal companion module filenames. Local dependencies are followed within
  each source directory, with realpath resolution for Python modules.
  Python env entrypoints also require `python3` on PATH (its version is not
  queried). Invocation-relative shell wrappers and `agent-status` also need
  companions beside their installed invocation paths. Partial individual-file links can
  therefore be diagnosed even when their original source tree is complete.
- Skills: only immediate entries in explicit `--skill-root DIR` directories.
  Whole-directory symlinks are supported. Dangling links, missing local files
  linked by inline Markdown, and missing name/description frontmatter fields
  are reported. Reference targets outside the resolved skill directory are
  left unchecked. Angle-bracket destinations with spaces and balanced
  parentheses are supported; ambiguous destinations are warnings rather than
  missing-file errors. Code spans, fences and indented examples are excluded.
  Frontmatter checks are structural presence checks, not YAML parsing or
  proof that an agent product accepts a skill.
- Configuration: only an explicit `--records-config FILE` is read. The doctor
  checks the current records JSON field types and estimate-policy choices,
  without printing values. It does not inspect arbitrary Claude/Codex settings,
  credentials, transcript stores or provider caches.
- Records paths: explicit `--tasks-dir`/`--changelog-dir` override the usual
  `AGENT_TASKS_DIR`/`AGENT_CHANGELOG_DIR` environment variables, which override
  paths in the selected configuration. Unconfigured stores are optional skips.
  Checks cover existing directories, main-checkout `.git` structure, access
  indications, marker kind and the existing `.records.lock` opened read-only.
  A nonblocking shared lock protects the marker read; contention is a warning
  and prevents marker inspection. No lock is provisioned. A pending recovery
  journal is reported without reading it or other record contents. Records
  doctors are different: they are explicit write/signing probes.

Only selected roots, known companion files and explicitly configured records
paths are inspected. Symlinks identify the selected source; links out of a
skill are reported without following their contents. Source inspection is
limited to 1 MiB per file and 256 dependency files. Other text inputs have a
256 KiB limit. Skill roots are limited to 256 immediate entries, with at most
16 roots and four instruction files. Special files and unreadable/non-UTF-8
inputs fail without printing their contents. Messages name paths and issue
locations, so the output is local diagnostic data: review it before sharing.

## Instruction review

A deterministic installation check cannot honestly judge whether instructions
contradict each other, duplicate requirements intentionally, or reflect stale
project decisions. Supply exact files and ask for a copyable review prompt:

```sh
./bin/agent-doctor --instruction ./AGENTS.md \
  --instruction ./tests/AGENTS.md --review-prompt
```

The doctor checks readability and prints a bounded prompt naming those files;
it uses absolute file paths, does not include their contents, and does not
run the review. Paste the prompt into an authorized agent session. It requests read-only review, file/line findings,
confidence, consequences and proposed corrections, with no reference chasing
outside the listed files. Explicit owner instructions remain authoritative.

## Output and limits

Human output groups each check's status, code, location and remedy.
`--json` returns schema version 1 with `checks`, status `counts`, a nullable
`review_prompt`, and explicit read-only/network/signing/semantic-review flags.
Each check has `status` (`ok`, `warning`, `error`, `skip`), stable `code`,
`location`, `message` and `remedy`. No raw configuration, lock-holder,
instruction, journal or skill content is copied into the report.

Exit codes: **0** means no detected errors (warnings/skips may exist), **1**
means at least one diagnostic error, and **2** means invalid command usage.
Passing is structural installation evidence, not a health certification.
Permission checks use access indications and an actual read-only lock open;
they do not prove a future write or signing operation can succeed. Git
integrity, record contents, provider authentication, runtime compatibility,
full shell dependency analysis and semantic consistency remain unverified.
No command is run, so runtime behavior and external services need separate,
explicitly authorized validation. No automatic fixes are available.
