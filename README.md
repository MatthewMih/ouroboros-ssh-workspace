# SSH Workspace

`ssh_workspace` is a small, dependency-free instruction skill and command-line
helper for working directly in an **authorized** POSIX workspace over OpenSSH.
It transfers a bounded standard-library Python worker over one SSH channel per
operation (v0.2.3); it does not mirror the project, install anything remotely, or keep a
persistent remote shell.

## What it provides

The CLI emits ASCII-escaped JSON so non-UTF-8 POSIX filenames remain safely printable. Search and read use matching 1-based LF-delimited line numbers. Timed-out exec results preserve captured output and explicitly report an unconfirmed process-group stop when applicable.

- bounded file listing, reading, literal search, and log reads;
- exclusive safe creation and SHA-256-checked targeted text edits with a
  recoverable remote backup;
- bounded non-interactive commands using an explicit argument vector;
- optional, task-owned OpenSSH connection multiplexing; and
- explicit `completed`, remote `error`, and transport/protocol `unknown`
  outcomes.

The full operating contract, limits, recovery rules, and host-custody guidance
are in [`SKILL.md`](SKILL.md). Read it before using the helper.

## Requirements

- Python 3.8+ locally and on the remote POSIX host;
- OpenSSH client locally; and
- an already-authorized SSH alias or `user@host`, with host keys and identity
  configuration maintained **outside this repository**.

There are no third-party Python dependencies.

## Basic use

Create a JSON request file such as:

```json
{"op":"read","path":"src/main.py","start_line":1,"max_lines":80,"max_bytes":16000}
```

Then run:

```sh
python3 ssh_workspace.py \
  --host devbox \
  --root /srv/authorized-project \
  --request request.json
```

Use `--ssh-config /absolute/path/to/config` only for an external, private SSH
configuration. Never put keys, host configuration, connection sockets, request
transcripts, or remote logs in this package or its public repository.

`--root` bounds file-oriented operations, but `exec` is not a security sandbox:
only run commands already authorized for the selected account and project.
Treat exit status `2` and `outcome: "unknown"` as requiring reconciliation
before repeating a mutating operation.

## Offline verification

The test suite uses temporary local directories and a fake SSH transport; it
does not contact a remote host:

```sh
python3 tests.py
python3 ssh_workspace.py --help
```

## Repository contents

- `SKILL.md` — manifest and operating guidance.
- `ssh_workspace.py` — local CLI and OpenSSH transport.
- `remote.py` — transient remote worker sent in memory on each request.
- `tests.py` — offline contract tests.

## License

MIT. See [`LICENSE`](LICENSE).
