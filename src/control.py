"""Authenticated loopback control for actions running outside the leader."""

import hmac
import json
import os
import secrets
import tempfile
import urllib.request


def _token_path(data_dir):
    return os.path.join(data_dir, "control.token")


def ensure_token(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    path = _token_path(data_dir)
    fd, temporary = tempfile.mkstemp(prefix=".control-", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as file:
            file.write(secrets.token_hex(32))
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)


def verify_token(data_dir, authorization):
    if not data_dir or not authorization.startswith("Bearer "):
        return False
    try:
        with open(_token_path(data_dir), encoding="ascii") as file:
            expected = file.read().strip()
    except OSError:
        return False
    return bool(expected) and hmac.compare_digest(expected, authorization[7:])


def reload_streams(data_dir, port):
    with open(_token_path(data_dir), encoding="ascii") as file:
        token = file.read().strip()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/internal/control/reload",
        data=b"", method="POST", headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return int(json.load(response)["killed"])
