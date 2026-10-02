# Lifecycle repository-escape guard: false positives (2026-10-02)

Branch `vic/fix/lifecycle-guard-false-positive`, from `main` (`d6efc100`).

## What happened

Two cold SDO lifecycles with Codex gpt-6-luna in the Tier 1 mini-stream (`/mnt/data/shli/clc-runs/mini2-a`, `mini2-b`, 2026-10-02) did not finish.

- `mini2-b` failed after 708 s: the health judge's three bounded attempts (round 1) each ended with `read outside the application repository; discarding its output`, then `LifecycleError: health judge round 1 exhausted bounded correction attempts`.
- `mini2-a` logged the same rejection for attempt 1 (05:17:42), then attempt 2 ran for over 10 minutes until Tier 1 stopped it. It was not a hang or a loop in code: the per-session timeout is 900 s and a luna judge turn that writes detectors, a traffic generator and tests can take that long.
- Tier 0's lifecycle (`mixed-smoke`, 678 s) passed. Its judge had two ordinary validation retries and no guard rejection.

## Root cause

`_command_escapes_repository` (`sdo/agent_runtime/lifecycle/agents.py`) treats every `/...` token that is not preceded by an identifier character as an absolute path. Two normal judge commands tripped it:

1. Attempts 1 and 2 (both runs): `cat > .sdo/diagnostics/traffic/generators/hotel.go <<'EOF' ... EOF`. The heredoc body is Go source with route strings such as `"/hotels?inDate=..."` and `"/recommendations"`. The body is data written to a file, not a path the shell opens.
2. Attempt 3 (`mini2-b`): `rg --files | rg '(frontend|hotelreservation|search)/.*\.go$|...'`. The quoted `/` follows a regex group `)`. The existing mask only covered a `/` after `|` or `(`.

The guard rejection is retried (feedback to a fresh session, 3 attempts), so the recovery the directive asks about already exists. It failed because the false positive is deterministic: the judge writes its generator the same way each time, so every attempt repeats it, and each costs 2.5 to 10 minutes.

## Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | Mask the body of a `cat`/`tee` heredoc with a quoted delimiter before the path audit. | Mask every heredoc body. Drop the audit of file writes. | A quoted delimiter means no expansion, and `cat`/`tee` only write the text. A heredoc fed to `bash`/`sh`, piped to a shell (`cat <<'EOF' \| sh`), or with an unquoted delimiter (`$(cat /etc/passwd)`) is executed or expanded, so it stays audited. Text after the closing delimiter is audited too. |
| 2 | Extend the quoted-regex mask to a `/` after `)`, `]`, `*`, `+`, `?`, `}`. | Add `)` only. Mask all quoted slashes. | A path the shell opens starts a token (after whitespace, `=` or a quote). A slash after a group, class or quantifier is mid-token. Masking all quoted slashes would hide `cat '/etc/passwd'`, which a test keeps rejected. |
| 3 | The rejection now names the offending path (`read outside the application repository ('/hotels?...')`). `_first_repository_escape` returns `(command, path)`; `_command_escapes_repository` stays a boolean wrapper. | Keep the 300-char command prefix only. | The 300-char prefix of the failing heredoc command showed only `package generators`, so neither the log nor the judge's retry feedback said which token tripped the guard. |
| 4 | No change to retry policy (3 bounded attempts, feedback to a fresh session). | Abort on first guard failure; one extra retry for guard failures. | Feedback and retry already exist. Fixing the false positives removes the repeated failure. A real escape should still end the lifecycle after bounded attempts. |
| 5 | No change to the 900 s per-session timeout. | Lower it. | A slow judge turn is legitimate work; mini2-a's 10 minute attempt was within the limit. Slowness of luna lifecycles is a separate cost question. |

## Tests

`tests/unit/sdo/agent_runtime/lifecycle/test_agents.py`:

- `test_repository_audit_allows_data_and_regex_slashes` (red before the fix): quoted heredoc file writes (single and double quoted delimiters, `cat` and `tee`, bare and `bash -lc` wrapped) and regex group slashes.
- `test_repository_audit_still_rejects_executed_or_external_paths`: heredoc fed to `bash`, heredoc piped to `sh`, unquoted heredoc with `$(cat /etc/passwd)`, `cat /etc/passwd` or `../secret` after a heredoc, and `rg -n '(a|b)' /etc/passwd`.
- `test_repository_escape_error_names_the_offending_path`.
- The existing escape tests (`../`, absolute paths, symlink and Claude task-output cases) pass unchanged.

## Not done

No cluster or Codex run: a confirming luna lifecycle on hotel-reservation is the real check and is left to the next Tier 1 attempt. Symlink escapes are unchanged by this work (the audit is lexical, as before).
