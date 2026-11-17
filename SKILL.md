---
name: ssh_workspace
title: SSH Workspace
version: 0.2.1
license: MIT
type: instruction
description: Work directly in an authorized remote POSIX workspace over OpenSSH with bounded reads, search, conflict-checked edits, safe creation, and explicit command outcomes; no project mirror or remote installation.
permissions: [net, fs, subprocess]
when_to_use: Develop or inspect code on an explicitly authorized SSH server without copying the remote project locally.
---

# SSH Workspace

Use when developing or inspecting code on an explicitly authorized SSH server. This is an instruction skill, not a new tool namespace. Run its CLI from this skill's installed directory (for example, `python3 ssh_workspace.py ...`); in Ouroboros, select this skill payload as the command working directory. The package is location-independent and does not assume an `external` installation bucket.

## Setup and authority

Requires local Python 3.8+, OpenSSH, and remote POSIX Python 3.8+. No third-party packages, daemon, remote installation or local project mirror. It sends a small standard-library program on each operation's SSH channel. Python is selected explicitly with `--python` (one executable name/path, not shell syntax).

Keep actual SSH config, host keys and private keys OUTSIDE this payload. Supply a configured SSH alias with `--host` and optionally an absolute external `--ssh-config`. Configure the identity, port, bastion and known_hosts there. Independently verify a new host fingerprint; the CLI requires strict host-key checking and batch authentication, disables forwarding and never auto-accepts keys. Do not publish private configuration, requests, transcripts or test evidence with the skill.

Before acting, read the owner's server/project rules and confirm the SSH-visible workspace path, allowed effects and budgets. Paths may differ from a notebook's view. Never infer permission from connectivity. The CLI's `--root` bounds its FILE operations, not arbitrary commands or the SSH account: **exec is not sandboxed**. Respect the owner's boundaries in commands as well. Path checks are best-effort against accidental escapes, not isolation against hostile concurrent directory changes. Use a restricted server account for real isolation.

## Invocation

```text
python3 ssh_workspace.py --host devbox --root /srv/my-project --request request.json
python3 ssh_workspace.py --host devbox --root /srv/my-project --ssh-config /absolute/private/config < request.json
```

`--request` reads a local JSON file; otherwise JSON comes from stdin (use a request file with tools that do not expose stdin). Requests and output are UTF-8 JSON. CLI exit 0 = operation completed, 1 = remote operation error, 2 = transport/protocol outcome UNKNOWN or local input error. For exec, inspect `returncode` and `timed_out`: CLI success is not program success. No automatic retries. SSH stderr is bounded but may contain private paths; keep it private.

Examples (placeholder paths/host only):

```json
{"op":"list","path":".","limit":100}
{"op":"read","path":"src/train.py","start_line":20,"max_lines":80,"max_bytes":16000}
{"op":"read","path":"src/train.py","offset":16384,"max_bytes":16000}
{"op":"search","path":".","query":"learning_rate","limit":40,"max_files":1000}
{"op":"create","path":"notes.txt","content":"new file\n"}
{"op":"patch","path":"notes.txt","expected_sha256":"SHA256_FROM_READ","edits":[{"old":"new file","new":"revised file","count":1}]}
{"op":"exec","cwd":".","argv":["python3","-c","print('hello')"],"timeout":10,"max_bytes":8000}
{"op":"log-tail","path":"runs/train.log","max_lines":60,"max_bytes":16000}
```

- All file paths and command cwd are relative to `--root` or absolute inside it. Root must already exist. `list` does not follow symlink entries. `search` is literal, UTF-8 only, skips symlinks and common generated directories. Its scan/file/byte/time bounds and gaps are explicit; incomplete search is NOT absence. Search returns bounded lines and is not AST navigation.
- `read` returns whole-file SHA256 plus bounded text, byte offsets and `next_byte`; start_line is 1-based. Continue via `offset=next_byte` (byte chunks may split Unicode characters; output uses replacement decoding and reports it). Files can change while reading; revision is not an atomic snapshot. `log-tail` reads only a bounded end window, reports omitted prefix and window bytes; it does not hash a huge log. Use `log-read` for growing logs, without the 64 MiB read/hash limit.
- `create` exclusively creates a NEW UTF-8 file (0600 default, optional integer `mode`); no overwrite or implicit directories. `patch` handles UTF-8 files up to 2 MiB, checks whole-file SHA256 and the exact nonzero count for every ordered replacement before writing. Returns a bounded diff, new hash and an exclusive recovery backup path. Preserves ordinary mode bits; refuses symlink/hardlink targets. Uses a same-directory temporary file then rename, with a final recheck. No multi-file transaction, no guarantee against an uncooperative writer between recheck/rename. ACLs/xattrs/ownership are not preserved by replacement; use on ordinary user-owned files, not special metadata files. Backups stay remotely until explicitly authorized cleanup; never delete unique results silently.
- `exec` requires argv, cwd and timeout (1–300 s). To use shell features choose `["sh","-c","..."]` explicitly; prefer an absolute venv interpreter. No persistent shell/PTY, no inherited cd or activate. It drains both streams without unbounded buffering and returns exact byte counts plus bounded prefixes. A remote timeout sends SIGKILL to the started process group and reports the signal attempt, **not proof that every descendant exited**. Daemonized or queued jobs may outlive it. For long experiments use the site's queue, retain job IDs, reconcile state before retry. No built-in queue abstraction.

## Reuse the SSH transport (optional)

Use a foreground OpenSSH master, not a daemon or automatic `ControlPersist` process. Create a fresh, short local directory outside the skill, owned by you with mode 0700; select a socket path inside it. Keep this socket exclusive to this task and the exact host/config/account. A control socket is itself access to that authenticated connection: never reuse another task's socket or switch host/config while retaining it. The CLI does not authenticate a socket's destination; the task owns that association.

Start `python3 ssh_workspace.py --host devbox --root /srv/my-project --ssh-config /private/config --control-path /private/unique-dir/c --master` with the host's `start_service` (explicit installed skill directory as cwd), default `keep_alive=false`. The helper execs SSH in the foreground with `ControlPersist=no` and `ForkAfterAuthentication=no`. No remote process or file is installed. Wait for the socket, then confirm readiness using `ssh -F /private/config -S /private/unique-dir/c -O check devbox` and one harmless CLI operation. File existence alone is not readiness.

Subsequent CLI operations add the SAME `--control-path`. Each gets its own stateless channel; no new authentication/handshake. Missing/dead mux cannot silently fall back to another connection (`ProxyCommand=false`); report transport UNKNOWN and reconcile. Without `--control-path`, operations use independent connections explicitly. Do not kill the master because one channel times out. Concurrent channels are possible, but the remote file race limitations still apply.

At the end use `stop_service`, verify its process exit and `-O check` failure; never leave a master running unowned. Panic tracks the foreground local master through host custody, but this does NOT prove remote commands or queue jobs stopped. Do not detach using `-f`, shell `&`, `nohup` or `ControlPersist`. A cold/background handoff must stop the service first; warm waits may retain it. For a new task open a fresh private master. Private config/keys/sockets and readiness evidence stay outside payload.

## Growing logs

`{"op":"log-read","path":"runs/train.log","max_bytes":16000}` starts at byte zero. Set `"start":"end"` on the FIRST request to observe only future appends, or use `log-tail` for recent history. Save the entire returned `cursor` and send it with the same path on the next `log-read`; omit `start` then. Each read consumes at most `max_bytes` (1..65536) and reports `more`, byte offsets and a new cursor. At EOF it returns zero bytes and the same offset; call again later. No full-file hash or local log mirror.

Cursor v1 binds resolved path, device/inode, byte offset and a SHA256 of at most 64 bytes before the offset. Replacement, smaller size, or changed anchor returns `reset_required=true` WITHOUT consuming bytes. Choose explicitly whether to restart at begin or end without the cursor; never silently lose or replay history. This detects common rotation/copytruncate, not every rewrite: truncate-and-regrow with the same anchor or rewrites earlier than that window may go undetected. Deleted files return ordinary file errors. Reported size/change/path-replacement facts are observations, not an atomic snapshot; coordinate with log producers if strict snapshots are needed.

`text` is convenient replacement-decoded UTF-8; chunks can split a character or a line. `decoding_replaced`/`partial_last_line` say so. `data_base64` preserves EXACT bytes: concatenate decoded byte chunks before UTF-8 decoding if exact reconstruction matters. Cursor offsets always count bytes, not characters. Results include both text and base64 (bounded); request smaller chunks to economize context.

## Work loop

Read rules → list/search → read relevant ranges → patch with observed SHA256 → inspect diff/read back → exec short check → read relevant output/log only. Do not copy projects, datasets or checkpoints locally. Do not send complete secrets or huge logs to the model. Large-file errors mean choose a bounded operation, not increase everything blindly.

A connection loss, local timeout, malformed reply or SSH exit 255 yields `outcome: unknown`. This is NOT confirmation of remote failure, rollback, cancellation or zero effects. Reconcile files/jobs before repeating a mutating operation. Local Panic/closing SSH does not guarantee cancellation on the server. There is no remote detached-process custody or persistent shell/PTY. A shared SSH transport is not a persistent shell.

## Verification and scope

`python3 tests.py` runs offline tests with temporary directories and a fake SSH transport (never connects). `python3 ssh_workspace.py --help` checks the CLI. Before declaring a new host usable, run the installed CLI against an owner-approved unique remote test folder: read existing harmless file without changing it, create/read/search/patch, reject stale hash without modifying bytes, safe exec with nonzero exit/output bounds/timeout, and bounded log-tail; preserve originals. No packages, GPU work or queue jobs merely to test this skill. Keep run-specific evidence outside the publishable payload.

Only POSIX Python/OpenSSH is supported in this version. Remote gateway shells may reject exec/stdin; report unsupported behavior instead of silently switching to interactive execution. This skill has no UI, automatic monitoring, credentials, server-specific rules, or publication side effects.
