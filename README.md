# prism-ctf-ctfd

CTFd plugin for [prism-ctf](https://github.com/pwnlentoni/prism-ctf): adds the
`prism_shared` and `prism_isolated` challenge types, backed by the prism-ctf
`SharedChallenge` / `IsolatedChallenge` custom resources
(`prism-ctf.pwnlentoni.team/v1`), plus an admin page at `/admin/prism-ctf`.

Tested against CTFd 3.8.9.

## Install (Kubernetes image volume)

The plugin is published as `ghcr.io/pwnlentoni/prism-ctf-ctfd`. The image is
`FROM scratch` and its root *is* the plugin directory: the plugin sources sit at `/`
and the Python dependencies (lightkube, httpx, pyyaml, ...) are vendored in
`/vendor`. On import the plugin appends `vendor/` to `sys.path` (appended, so CTFd's
own pinned packages win on conflicts), so a stock `ctfd/ctfd` image works without
rebuilding it.

Mount it with an [image volume](https://kubernetes.io/docs/tasks/configure-pod-container/image-volumes/)
at `/opt/CTFd/CTFd/plugins/prism_ctf` (the directory **must** be called `prism_ctf`):

```yaml
spec:
  serviceAccountName: ctfd
  containers:
    - name: ctfd
      image: ctfd/ctfd:3.8.9
      env:
        - name: REDIS_URL
          value: redis://redis:6379
      volumeMounts:
        - name: prism-plugin
          mountPath: /opt/CTFd/CTFd/plugins/prism_ctf
          readOnly: true
  volumes:
    - name: prism-plugin
      image:
        reference: ghcr.io/pwnlentoni/prism-ctf-ctfd:<tag>
        pullPolicy: IfNotPresent
```

Image tags: `sha-<short sha>` and `latest` for every push to `master`, and
`<version>` / `<major>.<minor>` for `v*` git tags. Prefer pinning a `sha-` or
version tag over `latest`.

The image is built for `linux/amd64` and `linux/arm64`. pyyaml ships native wheels,
so when building it yourself build for the platform CTFd runs on (`docker build .`).

## Requirements

- **Server-sent events** enabled in CTFd (`SERVER_SENT_EVENTS`, on by default); the
  plugin refuses to load otherwise.
- **Redis** as CTFd's cache (`REDIS_URL`).
- CTFd must **run in the cluster** with a service account: the plugin builds a
  `lightkube.Client()` from the in-cluster config at import time and discovers the
  prism-ctf CRDs on load.
- **RBAC** for that service account, roughly:

  ```yaml
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRole
  metadata:
    name: ctfd-prism
  rules:
    - apiGroups: ["apiextensions.k8s.io"]     # CRD discovery on load
      resources: ["customresourcedefinitions"]
      verbs: ["get", "list"]
    - apiGroups: ["prism-ctf.pwnlentoni.team"]
      resources: ["sharedchallenges", "isolatedchallenges", "challengeinstances"]
      verbs: ["get", "list", "watch", "create", "patch", "delete"]
    - apiGroups: [""]                         # locating challenge namespaces
      resources: ["namespaces"]
      verbs: ["list"]
  ```

  bound to the CTFd service account with a `ClusterRoleBinding`.
- A **`PrismServiceAccount`** pointing at the CTFd service account, so the operator
  grants it pod logs and deployment restarts in the challenge namespaces (used by
  the admin page):

  ```yaml
  apiVersion: prism-ctf.pwnlentoni.team/v1
  kind: PrismServiceAccount
  metadata:
    name: ctfd
  spec:
    namespace: ctfd   # namespace of the CTFd service account
    name: ctfd        # name of the CTFd service account
  ```

## Creating challenges

### Admin UI

Pick the `prism_shared` / `prism_isolated` type and upload the YAML spec of the
`SharedChallenge` / `IsolatedChallenge`. The file is stored as a CTFd file and
referenced by `yaml_id`.

### ctfcli

[ctfcli](https://github.com/CTFd/ctfcli) cannot upload the spec before creating the
challenge, so the spec can be given inline as `extra.kube_spec` (ctfcli merges
`extra` into the create/update payload). The plugin:

1. validates it: a single object, `apiVersion: prism-ctf.pwnlentoni.team/v1`, `kind`
   matching the challenge type, `metadata.name` set;
2. stores it as a CTFd file named `<challenge name>.yaml`, the same kind of standalone
   file the admin UI uploads (not a player-visible challenge file, so ctfcli's `files`
   handling never touches it);
3. sets `yaml_id` to that file and applies the spec to the cluster.

On `ctf challenge sync` a `kube_spec` replaces the stored spec and is re-applied,
exactly like uploading a new YAML in the admin UI (the previous file is kept). If the
create or update then fails (the stored spec doesn't load, or applying it to the
cluster fails), the uploaded file is deleted again; files referenced via `yaml_id`
are never deleted.

`kube_spec` may also be a YAML string (`kube_spec: |`). Passing both `kube_spec`
and `yaml_id` is an error.

Shared challenge (one deployment shared by every player):

```yaml
name: whoami
category: misc
description: Who am I?
value: 100
type: prism_shared

extra:
  kube_spec:
    apiVersion: prism-ctf.pwnlentoni.team/v1
    kind: SharedChallenge
    metadata:
      name: whoami-tcp
    spec:
      containers:
        - ports:
            - port: 8080
              proto: TCP
          egress: false
          replicas: 2
          spec:
            name: backend
            image: traefik/whoamitcp:latest
            resources:
              limits: {cpu: 20m, memory: 64Mi}
              requests: {cpu: 10m, memory: 32Mi}
      exposes:
        - container: backend
          port: 8080
          protocol: TCP

flags:
  - flag{static_flag_for_shared_challenges}
```

Isolated challenge (one instance per team/user, with a per-instance flag generated
from `flag_template`; `destroy_on_flag` tears the instance down on a correct flag):

```yaml
name: whoami isolated
category: web
description: Your very own whoami.
value: 200
type: prism_isolated

extra:
  destroy_on_flag: true
  kube_spec:
    apiVersion: prism-ctf.pwnlentoni.team/v1
    kind: IsolatedChallenge
    metadata:
      name: whoami-iso-http
    spec:
      lifetime: 10m
      flag_template: "flag{th1s_1s_s0_r4nd0m_{{ random 8 }}}"
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
              limits: {cpu: 20m, memory: 64Mi}
              requests: {cpu: 10m, memory: 32Mi}
      exposes:
        - container: backend
          port: 80
          protocol: HTTP
```

`connection_info` is owned by the plugin (shared challenges get it from the cluster
status), so any `connection_info` in the create/update payload is ignored, including
the `null` ctfcli always sends: a plain `ctf challenge sync` won't clear it.

`ctf challenge verify` compares `extra` with what CTFd returns, which never matches
for these types, so use `ctf challenge verify --ignore=extra`. For shared challenges
whose `connection_info` the plugin has already filled in, also ignore it:
`--ignore=extra,connection_info`.

## Local development

```sh
uv sync                                           # editor tooling / type checking
pip install --target vendor -r requirements.txt   # vendored deps, picked up on import (gitignored)
```

Then mount (or symlink) the repository as `CTFd/plugins/prism_ctf` in a CTFd 3.8
checkout or container. `requirements.txt` is hash-pinned and generated with
`uv export --locked --no-dev > requirements.txt`.

The `kube_spec` create/update path can be tested against a stock CTFd image, with
Kubernetes faked:

```sh
docker build -t prism-ctf-ctfd:dev .
docker run --rm \
  --mount type=image,source=prism-ctf-ctfd:dev,target=/opt/CTFd/CTFd/plugins/prism_ctf \
  -v "$PWD/tests:/work:ro" -w /opt/CTFd --entrypoint python \
  ctfd/ctfd:3.8.9 /work/ctfd_kube_spec_test.py
```
