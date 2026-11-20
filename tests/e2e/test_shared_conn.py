"""
e2e: a shared challenge's connection_info follows the operator's status.exposedUrls with no
manual refresh. Run by tests/e2e/run.sh (needs CTFD_URL, CTFD_TOKEN, CHALLS_DOMAIN, a
KUBECONFIG for the e2e cluster, and ctfcli as `ctf`). Standard library only.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CTFD_URL = os.environ["CTFD_URL"].rstrip("/")
TOKEN = os.environ["CTFD_TOKEN"]
DOMAIN = os.environ["CHALLS_DOMAIN"]

CHALL_NAME = "whoami e2e"
CR_NAME = "whoami-e2e"
PENDING_MSG = "This challenge is starting up"
TIMEOUT = 300  # pulling traefik/whoami + reconcile; the cache TTL is 30s at most

CHALLENGE_YML = """\
name: {chall_name}
author: e2e
category: e2e
description: A shared HTTP service.
value: 100
type: prism_shared
flags:
  - flag{{e2e}}

extra:
  kube_spec:
    apiVersion: prism-ctf.pwnlentoni.team/v1
    kind: SharedChallenge
    metadata:
      name: {cr_name}
    spec:
      containers:
        - ports:
            - port: 80
              proto: TCP
          egress: false
          replicas: 1
          spec:
            name: backend
            image: traefik/whoami:latest
            resources:
              requests: {{ cpu: 10m, memory: 16Mi }}
              limits: {{ cpu: 50m, memory: 32Mi }}
      exposes:
        - container: backend
          port: 80
          protocol: HTTP
{expose_name}"""


def api(method, path, body=None):
    req = urllib.request.Request(
        f"{CTFD_URL}/api/v1{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def ctf(project, *args):
    env = {**os.environ, "CTFCLI_URL": CTFD_URL, "CTFCLI_ACCESS_TOKEN": TOKEN}
    print("+ ctf", *args, flush=True)
    subprocess.run(["ctf", *args], cwd=project, env=env, check=True)


def cr_exposed_urls():
    out = subprocess.run(
        [
            "kubectl",
            "get",
            f"sharedchallenges.prism-ctf.pwnlentoni.team/{CR_NAME}",
            "-o",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return (json.loads(out).get("status") or {}).get("exposedUrls")


def find_challenge():
    status, body = api("GET", "/challenges?view=admin")
    assert status == 200, body
    return next((c["id"] for c in body["data"] if c["name"] == CHALL_NAME), None)


def wait_for(desc, fn, timeout=TIMEOUT, interval=3):
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {desc}")
        time.sleep(interval)


def write_challenge(project, expose_name=None):
    chall_dir = project / "whoami"
    chall_dir.mkdir(exist_ok=True)
    (chall_dir / "challenge.yml").write_text(
        CHALLENGE_YML.format(
            chall_name=CHALL_NAME,
            cr_name=CR_NAME,
            expose_name=f"          name: {expose_name}\n" if expose_name else "",
        )
    )


def get_challenge(chal_id):
    status, body = api("GET", f"/challenges/{chal_id}")
    assert status == 200, body
    return body["data"]


def hostnames(conn):
    return sorted(u["hostname"] for u in conn or [])


def main():
    # idempotent re-runs on a kept cluster: start from a clean slate
    if (old := find_challenge()) is not None:
        print(f"deleting challenge left over from a previous run [id: {old}]")
        status, body = api("DELETE", f"/challenges/{old}")
        assert status == 200, body

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / ".ctf").mkdir()
        (project / ".ctf" / "config").write_text(
            "[config]\nurl =\naccess_token =\n\n[challenges]\n"
        )

        # 1. first fill, no refresh call
        write_challenge(project)
        ctf(project, "challenge", "install", "whoami")
        chal_id = find_challenge()
        assert chal_id is not None, "challenge not created"

        data = get_challenge(chal_id)
        if not data["connection_info"]:
            assert PENDING_MSG in data["view"], data["view"]
            print(
                "PASS pending: no connection_info yet, view shows the starting-up message"
            )
        else:
            print("NOTE pending state not observed (status was already there)")

        expected = [f"{CR_NAME}.{DOMAIN}"]
        conn = wait_for(
            f"connection_info {expected}",
            lambda: (
                (c := get_challenge(chal_id)["connection_info"])
                and hostnames(c) == expected
                and c
            ),
        )
        assert conn == cr_exposed_urls(), (conn, cr_exposed_urls())
        assert PENDING_MSG not in get_challenge(chal_id)["view"]
        print(f"PASS first fill without refresh: {conn}")

        # 2. hostname change via ctfcli sync, no refresh call
        yaml_id = get_challenge(chal_id)["yaml_id"]
        write_challenge(project, expose_name="web")
        ctf(project, "challenge", "sync", "whoami")
        assert get_challenge(chal_id)["yaml_id"] != yaml_id, (
            "ctf challenge sync didn't update the spec"
        )
        expected = [f"{CR_NAME}-web.{DOMAIN}"]
        conn = wait_for(
            f"connection_info {expected}",
            lambda: (
                (c := get_challenge(chal_id)["connection_info"])
                and hostnames(c) == expected
                and c
            ),
        )
        assert conn == cr_exposed_urls(), (conn, cr_exposed_urls())
        print(f"PASS hostname change picked up after ctfcli sync: {conn}")

        # 3. admin refresh endpoints still work
        for path in (
            f"/plugins/prism_ctf/admin/shared/refresh/{chal_id}",
            "/plugins/prism_ctf/admin/shared/refresh",
        ):
            status, body = api("POST", path, {})
            assert status == 200 and body.get("success"), (path, status, body)
        assert get_challenge(chal_id)["connection_info"] == cr_exposed_urls()
        print("PASS admin refresh endpoints")

        # clean up so the operator deletes the challenge namespace
        status, body = api("DELETE", f"/challenges/{chal_id}")
        assert status == 200, body
        print("PASS challenge deleted")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, subprocess.CalledProcessError) as e:
        print(f"FAIL {e!r}", file=sys.stderr)
        sys.exit(1)
    print("e2e passed")
