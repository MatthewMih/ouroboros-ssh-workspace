#!/usr/bin/env python3
"""Portable CLI: one JSON request, one transient OpenSSH Python worker."""
import argparse
import base64
import json
from pathlib import Path
import re
import os
import selectors
import time
import shlex
import subprocess
import stat
import sys
import uuid


def transport(cmd, script, timeout):
    """Drain SSH while sending stdin; cap protocol/noise memory, never retry."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    for stream, event, name in [(proc.stdin, selectors.EVENT_WRITE, 'input'),
                                 (proc.stdout, selectors.EVENT_READ, 'stdout'),
                                 (proc.stderr, selectors.EVENT_READ, 'stderr')]:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, event, name)
    offset = 0
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    totals = {'stdout': 0, 'stderr': 0}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            for key, _ in selector.select(0.05):
                stream, name = key.fileobj, key.data
                if name == 'input':
                    try:
                        offset += os.write(stream.fileno(), script[offset:offset+65536])
                    except BrokenPipeError:
                        offset = len(script)
                    if offset == len(script):
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), 65536)
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    totals[name] += len(chunk)
                    cap = 1024 * 1024 if name == 'stdout' else 8000
                    buffers[name].extend(chunk[:max(0, cap-len(buffers[name]))])
        rc = proc.wait(timeout=max(0.01, deadline-time.monotonic()))
        return subprocess.CompletedProcess(cmd, rc, bytes(buffers['stdout']), bytes(buffers['stderr'])), totals
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if not stream.closed:
                stream.close()


def ssh_command(host, config=None, control_path=None, master=False, ssh='ssh'):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*', host):
        raise ValueError('invalid SSH destination')
    cmd = [ssh]
    if config:
        if not Path(config).is_absolute():
            raise ValueError('ssh-config must be absolute')
        cmd += ['-F', config]
    cmd += ['-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-o', 'ControlPersist=no', '-o', 'ForkAfterAuthentication=no']
    if control_path:
        p = Path(control_path)
        if not p.is_absolute() or len(os.fsencode(p)) > 100 or p.parent.resolve() != p.parent:
            raise ValueError('control-path must be short, absolute, without symlink parents')
        s = p.parent.stat()
        if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
            raise ValueError('control socket parent must be owned by current user with mode 0700')
        if master and os.path.lexists(p):
            raise ValueError('master socket already exists; reconcile it, never overwrite')
        cmd += ['-S', str(p), '-o', 'ControlMaster=' + ('yes' if master else 'no')]
        if master:
            cmd += ['-N']  # ControlMaster=yes above; a second -M means ask/confirmation.
        else:
            # OpenSSH otherwise silently reconnects when a mux socket is absent.
            cmd += ['-o', 'ProxyCommand=false']
    elif master:
        raise ValueError('--master requires --control-path')
    else:
        cmd += ['-o', 'ControlPath=none', '-o', 'ControlMaster=no']
    return cmd + [host]


def call(host, root, request, config=None, python='python3', ssh='ssh', control_path=None):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*', host):
        raise ValueError('host must be a configured SSH alias or user@host, not options')
    if not root.startswith('/'):
        raise ValueError('root must be an absolute remote path')
    if config and not Path(config).is_absolute():
        raise ValueError('ssh-config must be absolute')
    if not python or '\x00' in python:
        raise ValueError('invalid Python executable')
    encoded = json.dumps(dict(root=root, request=request), ensure_ascii=True).encode()
    if len(encoded) > 3 * 1024 * 1024:
        raise ValueError('request limit 3 MiB')
    source = Path(__file__).with_name('remote.py').read_bytes()
    marker = 'SSHWS_' + uuid.uuid4().hex + ':'
    script = ("import base64,json\nexec(compile(base64.b64decode(%r),'<ssh_workspace>','exec'))\n"
              "print(%r+json.dumps(main(json.loads(base64.b64decode(%r))),ensure_ascii=True))\n" %
              (base64.b64encode(source).decode(), marker, base64.b64encode(encoded).decode())).encode()
    cmd = ssh_command(host, config, control_path, ssh=ssh) + [shlex.quote(python) + ' -']
    timeout = request.get('timeout', 30) if request.get('op') == 'exec' else 30
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError('timeout must be integer 1..300')
    try:
        process, totals = transport(cmd, script, timeout + 35)
    except subprocess.TimeoutExpired:
        return dict(ok=False, outcome='unknown', error='local_timeout',
                    message='SSH killed locally; remote effects/termination are unknown. Reconcile before retry.')
    stderr = process.stderr[:8000].decode('utf-8', errors='replace')
    frame = marker.encode()
    rows = [line[len(frame):] for line in process.stdout.splitlines() if line.startswith(frame)]
    if process.returncode != 0 or len(rows) != 1 or totals['stdout'] > len(process.stdout):
        return dict(ok=False, outcome='unknown', error='ssh_or_protocol_error',
                    ssh_returncode=process.returncode, stderr=stderr,
                    stderr_shortened=totals['stderr'] > 8000,
                    message='No confirmed operation result; effects may have occurred. Do not blindly retry.')
    try:
        reply = json.loads(rows[0])
        if reply.get('outcome') not in ('completed', 'error') or not isinstance(reply.get('ok'), bool):
            raise ValueError('invalid envelope')
    except (ValueError, AttributeError):
        return dict(ok=False, outcome='unknown', error='invalid_remote_json')
    if stderr:
        reply['ssh_stderr'] = stderr
        reply['ssh_stderr_shortened'] = totals['stderr'] > 8000
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--root', required=True, help='absolute authorized remote workspace')
    parser.add_argument('--ssh-config', help='absolute external SSH config (no secrets in skill)')
    parser.add_argument('--python', default='python3', help='remote Python executable')
    parser.add_argument('--request', help='JSON request file; default stdin')
    parser.add_argument('--control-path', help='explicit private socket; require existing master, never reconnect')
    parser.add_argument('--master', action='store_true', help='exec foreground SSH master; own with start_service')
    args = parser.parse_args()
    try:
        if args.master:
            cmd = ssh_command(args.host, args.ssh_config, args.control_path, master=True)
            os.execvp(cmd[0], cmd)
        if args.request:
            with open(args.request, 'rb') as f:
                raw = f.read(3 * 1024 * 1024 + 1)
        else:
            raw = sys.stdin.buffer.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError('request too large')
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError('request must be object')
        reply = call(args.host, args.root, request, args.ssh_config, args.python, control_path=args.control_path)
    except (OSError, ValueError, TypeError) as error:
        reply = dict(ok=False, outcome='local_error', error=type(error).__name__, message=str(error))
    print(json.dumps(reply, ensure_ascii=True))
    return 0 if reply.get('ok') else 1 if reply.get('outcome') == 'error' else 2


if __name__ == '__main__':
    sys.exit(main())
