"""
Integration test for the inline `kube_spec` path, run inside stock ctfd/ctfd:3.8.9 with the
plugin image mounted at /opt/CTFd/CTFd/plugins/prism_ctf. Kubernetes is faked: the CRDs are
registered locally and kube.apply is replaced with a recorder.

    docker build -t prism-ctf-ctfd:dev .
    docker run --rm \
        --mount type=image,source=prism-ctf-ctfd:dev,target=/opt/CTFd/CTFd/plugins/prism_ctf \
        -v "$PWD/tests:/work:ro" -w /opt/CTFd --entrypoint python \
        ctfd/ctfd:3.8.9 /work/ctfd_kube_spec_test.py
"""

import io
import json
import os
import sys

PLUGIN = "/opt/CTFd/CTFd/plugins/prism_ctf"

# dummy kubeconfig so lightkube.Client() at plugin import time works without a cluster
os.makedirs("/tmp/kube", exist_ok=True)
with open("/tmp/kube/config", "w") as f:
    f.write(
        """apiVersion: v1
kind: Config
clusters: [{name: c, cluster: {server: "https://127.0.0.1:1"}}]
users: [{name: u, user: {token: x}}]
contexts: [{name: c, context: {cluster: c, user: u, namespace: default}}]
current-context: c
"""
    )
os.environ["KUBECONFIG"] = "/tmp/kube/config"

# fake CRD discovery: patch before the plugin does `from ... import load_in_cluster_generic_resources`
sys.path.append(f"{PLUGIN}/vendor")
import lightkube.generic_resource as gr


def fake_load(client):
    for kind, plural in (
        ("SharedChallenge", "sharedchallenges"),
        ("IsolatedChallenge", "isolatedchallenges"),
        ("ChallengeInstance", "challengeinstances"),
    ):
        gr.create_global_resource("prism-ctf.pwnlentoni.team", "v1", kind, plural)


gr.load_in_cluster_generic_resources = fake_load

sys.path.insert(0, "/opt/CTFd")
from CTFd.models import Challenges, Files, Users, db
from CTFd.utils.security.auth import generate_user_token
from CTFd.utils.uploads import get_uploader
from tests.helpers import create_ctfd

app = create_ctfd(enable_plugins=True)
from CTFd.plugins.challenges import CHALLENGE_CLASSES

assert "prism_shared" in CHALLENGE_CLASSES

from CTFd.plugins.prism_ctf import k8s

applied = []
fail_apply = {"on": False}


def fake_apply(obj, field_manager=None):
    if fail_apply["on"]:
        raise RuntimeError("boom")
    applied.append(obj)


k8s.kube.apply = fake_apply

# fake SharedChallenge CRs as seen by the plugin: ctfd id -> status (no entry: CR missing)
from CTFd.plugins.prism_ctf import shared_challenges as sc

crs = {}
kube_down = {"on": False}
kube_calls = []


def fake_cr(ctfd_id):
    return k8s.get_shared_chall_type()(
        {
            "apiVersion": "prism-ctf.pwnlentoni.team/v1",
            "kind": "SharedChallenge",
            "metadata": {
                "name": f"chal-{ctfd_id}",
                "labels": {k8s.CTFD_ID_LABEL: str(ctfd_id)},
            },
            "status": crs[ctfd_id],
        }
    )


def fake_get_shared_challenge(ctfd_id):
    kube_calls.append(ctfd_id)
    if kube_down["on"]:
        raise RuntimeError("kube unreachable")
    return fake_cr(ctfd_id) if ctfd_id in crs else None


sc.get_shared_challenge = fake_get_shared_challenge
sc.list_shared_challenges = lambda: [fake_cr(i) for i in crs]

# make loading the stored spec fail on demand (create/update "spec validation failed" path)
from CTFd.plugins.prism_ctf.models import isolated as iso_mod
from CTFd.plugins.prism_ctf.models import shared as shared_mod

fail_spec = {"on": False}


def failing_spec_getter(orig):
    def get(*args, **kwargs):
        if fail_spec["on"]:
            raise RuntimeError("spec load boom")
        return orig(*args, **kwargs)

    return get


shared_mod.get_kube_spec_file = failing_spec_getter(shared_mod.get_kube_spec_file)
iso_mod.get_kube_spec_file = failing_spec_getter(iso_mod.get_kube_spec_file)

SHARED = {
    "apiVersion": "prism-ctf.pwnlentoni.team/v1",
    "kind": "SharedChallenge",
    "metadata": {"name": "whoami-tcp"},
    "spec": {
        "containers": [
            {
                "ports": [{"port": 8080, "proto": "TCP"}],
                "egress": False,
                "replicas": 2,
                "spec": {"name": "backend", "image": "traefik/whoamitcp:latest"},
            }
        ],
        "exposes": [{"container": "backend", "port": 8080, "protocol": "TCP"}],
    },
}
ISOLATED_YAML = """apiVersion: prism-ctf.pwnlentoni.team/v1
kind: IsolatedChallenge
metadata:
  name: whoami-iso-tcp
spec:
  lifetime: 10m
  flag_template: "flag{w0w_{{ random 8 }}}"
  containers:
    - ports: [{port: 8080, proto: TCP}]
      egress: false
      replicas: 1
      spec: {name: backend, image: "traefik/whoamitcp:latest"}
  exposes:
    - {container: backend, port: 8080, protocol: TCP}
"""


def ctfcli_payload(name, type_, extra):
    # mirrors ctfcli 0.1.8 _get_initial_challenge_payload
    return {
        "name": name,
        "category": "web",
        "description": "desc",
        "attribution": "",
        "type": type_,
        "state": "hidden",
        "value": 100,
        "max_attempts": 0,
        "connection_info": None,
        **extra,
    }


results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(
        ("PASS " if cond else "FAIL ")
        + name
        + (f"  [{info}]" if info and not cond else "")
    )


with app.app_context():
    token = generate_user_token(
        Users.query.filter_by(id=1).first(), expiration=None
    ).value
    c = app.test_client()
    H = {"Authorization": f"Token {token}", "Content-Type": "application/json"}

    def file_text(file_id):
        f = Files.query.filter_by(id=file_id).first()
        with get_uploader().open(f.location, mode="r") as fp:
            return f, fp.read()

    # 1. ctfcli create, shared, kube_spec as mapping
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload("Who Am I", "prism_shared", {"kube_spec": SHARED}),
        headers=H,
    )
    j = r.get_json()
    check(
        "shared create 200",
        r.status_code == 200 and j["success"],
        f"{r.status_code} {j}",
    )
    sid = j["data"]["id"]
    yid = j["data"]["yaml_id"]
    f, txt = file_text(yid)
    check(
        "shared yaml file stored as standard file",
        f.type == "standard" and f.location.endswith("/Who_Am_I.yaml"),
        repr(f),
    )
    import yaml

    check("stored yaml round-trips", yaml.safe_load(txt) == SHARED, txt)
    check(
        "kube.apply called with SharedChallenge + ctfd-id label",
        len(applied) == 1
        and applied[0].kind == "SharedChallenge"
        and applied[0].metadata.labels["prism-ctf.pwnlentoni.team/ctfd-id"] == str(sid),
    )

    # 2. ctfcli create, isolated, kube_spec as YAML string, destroy_on_flag JSON bool
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload(
            "iso",
            "prism_isolated",
            {"kube_spec": ISOLATED_YAML, "destroy_on_flag": True},
        ),
        headers=H,
    )
    j = r.get_json()
    check(
        "isolated create 200",
        r.status_code == 200 and j["success"],
        f"{r.status_code} {j}",
    )
    iid = j["data"]["id"]
    ch = Challenges.query.filter_by(id=iid).first()
    check("isolated destroy_on_flag bool", ch.destroy_on_flag is True)
    check(
        "isolated applied",
        applied[-1].kind == "IsolatedChallenge"
        and applied[-1].metadata.name == "whoami-iso-tcp",
    )
    check(
        "isolated get_kube_name/lifetime",
        ch.get_kube_name() == "whoami-iso-tcp"
        and ch.get_lifetime().total_seconds() == 600,
    )

    # 3. wrong kind -> error, nothing created
    n_chals, n_files, n_applied = (
        Challenges.query.count(),
        Files.query.count(),
        len(applied),
    )
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload("bad", "prism_isolated", {"kube_spec": SHARED}),
        headers=H,
    )
    j = r.get_json()
    check(
        "wrong kind rejected",
        r.status_code == 500 and "bad object kind" in json.dumps(j),
        f"{r.status_code} {j}",
    )
    check(
        "wrong kind: no challenge/file/apply",
        Challenges.query.count() == n_chals
        and Files.query.count() == n_files
        and len(applied) == n_applied,
    )

    # 3b. wrong apiVersion / multi-doc / non-object
    for label, spec, needle in (
        ("bad apiVersion", {**SHARED, "apiVersion": "v1"}, "bad object api version"),
        ("multi-doc yaml", ISOLATED_YAML + "---\n" + ISOLATED_YAML, "single object"),
        ("list", [SHARED], "single object"),
        ("missing name", {**SHARED, "metadata": {}}, "metadata.name"),
        ("bad yaml", "a: [", "invalid kube_spec yaml"),
    ):
        r = c.post(
            "/api/v1/challenges",
            json=ctfcli_payload("bad", "prism_shared", {"kube_spec": spec}),
            headers=H,
        )
        check(
            f"{label} rejected",
            r.status_code == 500 and needle in json.dumps(r.get_json()),
            json.dumps(r.get_json()),
        )

    # 4. both yaml_id and kube_spec
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload(
            "both", "prism_shared", {"kube_spec": SHARED, "yaml_id": yid}
        ),
        headers=H,
    )
    check(
        "yaml_id + kube_spec rejected",
        r.status_code == 500 and "not both" in json.dumps(r.get_json()),
    )

    # 5. ctfcli sync PATCH with new kube_spec
    new_spec = json.loads(json.dumps(SHARED))
    new_spec["spec"]["containers"][0]["replicas"] = 3
    n_applied = len(applied)
    r = c.patch(
        f"/api/v1/challenges/{sid}",
        json=ctfcli_payload("Who Am I", "prism_shared", {"kube_spec": new_spec}),
        headers=H,
    )
    j = r.get_json()
    check(
        "shared sync patch 200",
        r.status_code == 200 and j["success"],
        f"{r.status_code} {j}",
    )
    new_yid = j["data"]["yaml_id"]
    check(
        "patch replaced yaml_id",
        new_yid != yid
        and Challenges.query.filter_by(id=sid).first().yaml_id == new_yid,
    )
    check(
        "patch re-applied new spec",
        len(applied) == n_applied + 1
        and applied[-1].spec["containers"][0]["replicas"] == 3,
    )
    check(
        "old yaml file kept (mirrors admin UI)",
        Files.query.filter_by(id=yid).first() is not None,
    )

    # 6. PATCH without kube_spec -> no apply, yaml_id unchanged
    n_applied = len(applied)
    r = c.patch(
        f"/api/v1/challenges/{sid}",
        json=ctfcli_payload("Who Am I", "prism_shared", {}),
        headers=H,
    )
    check(
        "plain patch ok, no apply",
        r.status_code == 200
        and len(applied) == n_applied
        and r.get_json()["data"]["yaml_id"] == new_yid,
    )

    # 6b. isolated PATCH with kube_spec + destroy_on_flag
    r = c.patch(
        f"/api/v1/challenges/{iid}",
        json=ctfcli_payload(
            "iso",
            "prism_isolated",
            {
                "kube_spec": ISOLATED_YAML.replace("10m", "20m"),
                "destroy_on_flag": False,
            },
        ),
        headers=H,
    )
    ch = Challenges.query.filter_by(id=iid).first()
    check(
        "isolated patch ok",
        r.status_code == 200
        and ch.destroy_on_flag is False
        and ch.get_lifetime().total_seconds() == 1200,
        json.dumps(r.get_json()),
    )

    # 6c. bad kube_spec on update -> error, yaml_id unchanged
    r = c.patch(
        f"/api/v1/challenges/{iid}",
        json=ctfcli_payload("iso", "prism_isolated", {"kube_spec": SHARED}),
        headers=H,
    )
    db.session.expire_all()
    check(
        "bad kube_spec on update rejected",
        r.status_code == 500 and "bad object kind" in json.dumps(r.get_json()),
    )

    # 7. admin UI flow: upload standard file, then create with yaml_id (form-encoded like the admin UI)
    r = c.post(
        "/api/v1/files",
        data={
            "file": (io.BytesIO(yaml.safe_dump(SHARED).encode()), "spec.yaml"),
            "type": "standard",
        },
        headers={"Authorization": f"Token {token}"},
        content_type="multipart/form-data",
    )
    up_id = r.get_json()["data"][0]["id"]
    r = c.post(
        "/api/v1/challenges",
        json={
            "name": "ui",
            "category": "x",
            "description": "d",
            "value": 1,
            "state": "hidden",
            "type": "prism_shared",
            "yaml_id": str(up_id),
        },
        headers=H,
    )
    check(
        "admin-UI yaml_id create still works",
        r.status_code == 200 and r.get_json()["data"]["yaml_id"] == up_id,
        json.dumps(r.get_json()),
    )

    # 8. kube apply failure -> challenge rolled back
    fail_apply["on"] = True
    n_chals = Challenges.query.count()
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload("fails", "prism_shared", {"kube_spec": SHARED}),
        headers=H,
    )
    fail_apply["on"] = False
    check(
        "apply failure -> 500 and rollback",
        r.status_code == 500 and Challenges.query.count() == n_chals,
        json.dumps(r.get_json()),
    )

    # 10. connection_info is plugin-owned: ignored on create and update
    conn = [{"name": "backend", "url": "tcp://whoami.example:1337"}]
    chal = Challenges.query.filter_by(id=sid).first()
    chal.connection_info = json.dumps(conn)  # what shared_challenges.py does from kube
    db.session.commit()
    r = c.patch(
        f"/api/v1/challenges/{sid}",
        json=ctfcli_payload("Who Am I", "prism_shared", {}),  # connection_info: None
        headers=H,
    )
    db.session.expire_all()
    check(
        "ctfcli sync (connection_info null) keeps shared connection_info",
        r.status_code == 200
        and r.get_json()["data"]["connection_info"] == conn
        and Challenges.query.filter_by(id=sid).first().connection_info
        == json.dumps(conn),
        json.dumps(r.get_json()),
    )
    r = c.patch(
        f"/api/v1/challenges/{sid}",
        json=ctfcli_payload(
            "Who Am I", "prism_shared", {"connection_info": "nc evil 1"}
        ),
        headers=H,
    )
    check(
        "string connection_info on shared update ignored",
        r.status_code == 200 and r.get_json()["data"]["connection_info"] == conn,
        json.dumps(r.get_json()),
    )
    r = c.patch(
        f"/api/v1/challenges/{iid}",
        json=ctfcli_payload("iso", "prism_isolated", {"connection_info": "nc x 1"}),
        headers=H,
    )
    db.session.expire_all()
    r2 = c.get(f"/api/v1/challenges/{iid}", headers=H)
    check(
        "string connection_info on isolated update ignored, read still works",
        r.status_code == 200
        and Challenges.query.filter_by(id=iid).first().connection_info is None
        and r2.status_code == 200,
        f"{r.status_code} {r2.status_code}",
    )
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload(
            "conn", "prism_shared", {"kube_spec": SHARED, "connection_info": "nc x 1"}
        ),
        headers=H,
    )
    check(
        "connection_info on create ignored",
        r.status_code == 200 and r.get_json()["data"]["connection_info"] is None,
        json.dumps(r.get_json()),
    )

    # 11. failed inline create/update deletes the uploaded spec file, never a yaml_id one
    upload_dir = app.config["UPLOAD_FOLDER"]

    def state():
        on_disk = sum(len(fs) for _, _, fs in os.walk(upload_dir))
        return Challenges.query.count(), Files.query.count(), on_disk

    def request_failing(flag, method, url, payload):
        flag["on"] = True
        try:
            return getattr(c, method)(url, json=payload, headers=H)
        finally:
            flag["on"] = False

    before = state()
    r = request_failing(
        fail_apply,
        "post",
        "/api/v1/challenges",
        ctfcli_payload("fails", "prism_shared", {"kube_spec": SHARED}),
    )
    check(
        "create: apply failure deletes inline file",
        r.status_code == 500 and state() == before,
        f"{before} -> {state()}",
    )

    r = request_failing(
        fail_spec,
        "post",
        "/api/v1/challenges",
        ctfcli_payload("fails", "prism_isolated", {"kube_spec": ISOLATED_YAML}),
    )
    check(
        "create: spec load failure deletes inline file",
        r.status_code == 500
        and "spec validation failed" in json.dumps(r.get_json())
        and state() == before,
        f"{r.get_json()} {before} -> {state()}",
    )

    try:
        r = c.post(
            "/api/v1/challenges",
            json=ctfcli_payload(
                "fails", "prism_shared", {"kube_spec": SHARED, "no_such_column": 1}
            ),
            headers=H,
        )
        ok = r.status_code == 500
    except TypeError:  # TESTING propagates the model constructor error
        ok = True
    db.session.rollback()
    check("create: model error deletes inline file", ok and state() == before, state())

    spec2 = json.loads(json.dumps(SHARED))
    spec2["spec"]["containers"][0]["replicas"] = 5
    r = request_failing(
        fail_apply,
        "patch",
        f"/api/v1/challenges/{sid}",
        ctfcli_payload("Who Am I", "prism_shared", {"kube_spec": spec2}),
    )
    db.session.expire_all()
    check(
        "update: apply failure deletes inline file, keeps yaml_id + old file",
        r.status_code == 500
        and state() == before
        and Challenges.query.filter_by(id=sid).first().yaml_id == new_yid
        and Files.query.filter_by(id=new_yid).first() is not None,
        f"{r.get_json()} {before} -> {state()}",
    )

    iso_yid = Challenges.query.filter_by(id=iid).first().yaml_id
    r = request_failing(
        fail_spec,
        "patch",
        f"/api/v1/challenges/{iid}",
        ctfcli_payload("iso", "prism_isolated", {"kube_spec": ISOLATED_YAML}),
    )
    db.session.expire_all()
    check(
        "update: spec load failure deletes inline file, keeps yaml_id",
        r.status_code == 500
        and "spec validation failed" in json.dumps(r.get_json())
        and state() == before
        and Challenges.query.filter_by(id=iid).first().yaml_id == iso_yid,
        f"{r.get_json()} {before} -> {state()}",
    )

    r = c.post(
        "/api/v1/files",
        data={
            "file": (io.BytesIO(yaml.safe_dump(SHARED).encode()), "spec.yaml"),
            "type": "standard",
        },
        headers={"Authorization": f"Token {token}"},
        content_type="multipart/form-data",
    )
    ui_id = r.get_json()["data"][0]["id"]
    before = state()
    r = request_failing(
        fail_apply,
        "post",
        "/api/v1/challenges",
        {
            "name": "ui-fail",
            "category": "x",
            "description": "d",
            "value": 1,
            "state": "hidden",
            "type": "prism_shared",
            "yaml_id": str(ui_id),
        },
    )
    check(
        "create via yaml_id: apply failure keeps the file",
        r.status_code == 500
        and state() == before
        and Files.query.filter_by(id=ui_id).first() is not None,
        f"{before} -> {state()}",
    )
    r = request_failing(
        fail_apply,
        "patch",
        f"/api/v1/challenges/{sid}",
        {"yaml_id": str(ui_id)},
    )
    db.session.expire_all()
    check(
        "update via yaml_id: apply failure keeps the file, restores yaml_id",
        r.status_code == 500
        and state() == before
        and Files.query.filter_by(id=ui_id).first() is not None
        and Challenges.query.filter_by(id=sid).first().yaml_id == new_yid,
        f"{before} -> {state()}",
    )

    # 12. shared connection_info follows the CR status without a manual refresh
    r = c.post(
        "/api/v1/challenges",
        json=ctfcli_payload("auto", "prism_shared", {"kube_spec": SHARED}),
        headers=H,
    )
    aid = r.get_json()["data"]["id"]

    def urls(name):
        return [
            {
                "name": name,
                "hostname": f"whoami-tcp-{name}.challs.test",
                "protocol": "TCP",
            }
        ]

    def view(chal_id):
        db.session.expire_all()
        r = c.get(f"/api/v1/challenges/{chal_id}", headers=H)
        d = r.get_json()["data"]
        stored = Challenges.query.filter_by(id=chal_id).first().connection_info
        return r.status_code, d["connection_info"], d["view"], stored

    crs[aid] = {}  # CR exists, no exposedUrls yet
    status, conn, html, stored = view(aid)
    check(
        "pending: no connection_info, view shows starting-up message",
        status == 200
        and conn is None
        and stored is None
        and "starting up" in html
        and "open a ticket" not in html,
        f"{status} {conn} {stored}",
    )

    crs[aid] = {"exposedUrls": urls("a")}
    status, conn, html, stored = view(aid)
    check("pending value is cached briefly", conn is None, conn)
    sc.invalidate_connection_info(aid)
    status, conn, html, stored = view(aid)
    check(
        "first fill: URLs returned, persisted and rendered",
        status == 200
        and conn == urls("a")
        and stored == json.dumps(urls("a"))
        and "starting up" not in html,
        f"{status} {conn} {stored}",
    )
    n_calls = len(kube_calls)
    view(aid)
    check("ready value cached: no extra kube call", len(kube_calls) == n_calls)

    crs[aid] = {"exposedUrls": urls("b")}
    status, conn, html, stored = view(aid)
    check("hostname change not visible before cache expiry", conn == urls("a"))
    sc.invalidate_connection_info(aid)
    status, conn, html, stored = view(aid)
    check(
        "hostname change picked up and persisted",
        conn == urls("b") and stored == json.dumps(urls("b")),
        f"{conn} {stored}",
    )

    crs[aid] = {"exposedUrls": urls("c")}
    spec3 = json.loads(json.dumps(SHARED))
    spec3["spec"]["exposes"][0]["name"] = "c"
    r = c.patch(
        f"/api/v1/challenges/{aid}",
        json=ctfcli_payload("auto", "prism_shared", {"kube_spec": spec3}),
        headers=H,
    )
    status, conn, html, stored = view(aid)
    check(
        "spec update invalidates the cache",
        r.status_code == 200 and conn == urls("c") and stored == json.dumps(urls("c")),
        f"{r.status_code} {conn} {stored}",
    )
    crs[aid] = {"exposedUrls": urls("d")}
    c.patch(
        f"/api/v1/challenges/{aid}",
        json=ctfcli_payload("auto", "prism_shared", {}),
        headers=H,
    )
    status, conn, html, stored = view(aid)
    check("update without spec change keeps the cache", conn == urls("c"), conn)

    sc.invalidate_connection_info(aid)
    kube_down["on"] = True
    try:
        status, conn, html, stored = view(aid)
    finally:
        kube_down["on"] = False
    check(
        "kube error: 200 with the stored value",
        status == 200 and conn == urls("c") and stored == json.dumps(urls("c")),
        f"{status} {conn}",
    )

    for label, cr in (("not ready", {}), ("empty exposedUrls", {"exposedUrls": []})):
        crs[aid] = cr
        sc.invalidate_connection_info(aid)
        status, conn, html, stored = view(aid)
        check(
            f"CR {label}: stored value kept",
            status == 200 and conn == urls("c") and stored == json.dumps(urls("c")),
            f"{status} {conn}",
        )
    del crs[aid]
    sc.invalidate_connection_info(aid)
    status, conn, html, stored = view(aid)
    check("CR missing: stored value kept", status == 200 and conn == urls("c"), conn)

    crs[aid] = {"exposedUrls": urls("e")}
    r = c.post(
        f"/api/v1/plugins/prism_ctf/admin/shared/refresh/{aid}", json={}, headers=H
    )
    db.session.expire_all()
    check(
        "admin refresh (one) bypasses the cache and persists",
        r.status_code == 200
        and r.get_json()["success"]
        and Challenges.query.filter_by(id=aid).first().connection_info
        == json.dumps(urls("e")),
        f"{r.status_code} {r.get_json()}",
    )
    crs[aid] = {"exposedUrls": urls("f")}
    r = c.post("/api/v1/plugins/prism_ctf/admin/shared/refresh", json={}, headers=H)
    status, conn, html, stored = view(aid)
    check(
        "admin refresh (all) persists and refreshes the cache",
        r.status_code == 200 and r.get_json()["success"] and conn == urls("f"),
        f"{r.status_code} {r.get_json()} {conn}",
    )
    crs[aid] = {}
    r = c.post(
        f"/api/v1/plugins/prism_ctf/admin/shared/refresh/{aid}", json={}, headers=H
    )
    check(
        "admin refresh of a not-ready CR reports failure",
        r.status_code == 500 and not r.get_json()["success"],
        f"{r.status_code} {r.get_json()}",
    )
    del crs[aid]

    # 9. read via GET (what ctfcli load_installed_challenge uses)
    r = c.get(f"/api/v1/challenges/{sid}", headers=H)
    d = r.get_json()["data"]
    check(
        "GET read ok",
        r.status_code == 200
        and d["yaml_id"] == new_yid
        and d["type"] == "prism_shared",
    )

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
