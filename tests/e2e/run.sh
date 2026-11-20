#!/usr/bin/env bash
# Real-cluster e2e: kind + the prism-ctf operator + stock CTFd with the plugin image built
# from the working tree, driven through the CTFd API and ctfcli (tests/e2e/test_shared_conn.py).
#
#   tests/e2e/run.sh          # create/reuse the cluster, run, tear down
#   KEEP=1 tests/e2e/run.sh   # leave the cluster up (re-running reuses it)
#
# Needs docker, kubectl, helm, python3 and ctfcli (`ctf`). Uses kind >= $KIND_VERSION from
# PATH, or downloads that version into tests/e2e/.bin. Uses its own kubeconfig file
# ($E2E_KUBECONFIG), never ~/.kube/config.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

CLUSTER="${CLUSTER:-prism-ctfd-e2e}"
export KUBECONFIG="${E2E_KUBECONFIG:-${TMPDIR:-/tmp}/${CLUSTER}.kubeconfig}"
PORT="${PORT:-18000}"
PLUGIN_IMAGE="prism-ctf-ctfd:e2e"
ADMIN_TOKEN="ctfd_e2e0000000000000000000000000000000000000000000000000000000000"

# kindest/node v1.36 needs kind >= v0.33.0 (containerd config version 4)
KIND_VERSION="v0.33.0"
declare -A KIND_SHA256=(
    [amd64]=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
    [arm64]=20022bee6cfcd5086cb7234d218e3454e6090022f2a8f55d1fa7fcf42c3867a2
)
GATEWAY_API_VERSION="v1.7.0"
CILIUM_VERSION="v1.20.5"
PROMETHEUS_OPERATOR_VERSION="v0.95.2"
CERT_MANAGER_VERSION="v1.21.3"
PRISM_CTF_VERSION="v0.1.0"
CHALLS_DOMAIN="challs.e2e.test"

log() { printf '\n=== %s\n' "$*"; }

KIND=kind
installed="$(kind version 2>/dev/null | awk '{print $2}' || true)"
if [ -z "$installed" ] || [ "$(printf '%s\n' "$KIND_VERSION" "$installed" | sort -V | head -1)" != "$KIND_VERSION" ]; then
    KIND="$here/.bin/kind-$KIND_VERSION"
    if [ ! -x "$KIND" ]; then
        arch="$(uname -m)"
        case "$arch" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; esac
        log "kind ${installed:-not found} is older than $KIND_VERSION, downloading it to $KIND"
        mkdir -p "$here/.bin"
        curl -fsSLo "$KIND.tmp" "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-linux-$arch"
        echo "${KIND_SHA256[$arch]}  $KIND.tmp" | sha256sum -c -
        chmod +x "$KIND.tmp" && mv "$KIND.tmp" "$KIND"
    fi
fi

pf_pid=""
cleanup() {
    rc=$?
    if [ -n "$pf_pid" ]; then kill "$pf_pid" 2>/dev/null || true; fi
    if [ "$rc" -ne 0 ] && [ -f "$KUBECONFIG" ]; then
        log "FAILED (exit $rc), diagnostics"
        kubectl get pods -A -o wide || true
        kubectl get sharedchallenges.prism-ctf.pwnlentoni.team -o yaml || true
        kubectl -n ctfd logs deploy/ctfd --tail=200 || true
        kubectl -n prism-ctf-system logs deploy/prism-ctf-controller-manager --tail=100 || true
    fi
    if [ "${KEEP:-0}" = "1" ]; then
        log "KEEP=1: cluster $CLUSTER left running (KUBECONFIG=$KUBECONFIG)"
    else
        log "deleting cluster $CLUSTER"
        "$KIND" delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" || true
        rm -f "$KUBECONFIG"
    fi
    exit "$rc"
}
trap cleanup EXIT

log "cluster $CLUSTER"
if "$KIND" get clusters | grep -qx "$CLUSTER"; then
    "$KIND" export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG"
else
    "$KIND" create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" \
        --config "$here/kind-config.yaml" --wait 5m
fi

log "operator dependencies: Gateway API, ServiceMonitor and CiliumNetworkPolicy CRDs, cert-manager"
kubectl apply --server-side -f \
    "https://github.com/kubernetes-sigs/gateway-api/releases/download/$GATEWAY_API_VERSION/standard-install.yaml"
kubectl apply --server-side -f \
    "https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/$PROMETHEUS_OPERATOR_VERSION/example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml"
kubectl apply --server-side -f \
    "https://raw.githubusercontent.com/cilium/cilium/$CILIUM_VERSION/pkg/k8s/apis/cilium.io/client/crds/v2/ciliumnetworkpolicies.yaml"
helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
    --version "$CERT_MANAGER_VERSION" --namespace cert-manager --create-namespace \
    --set crds.enabled=true \
    --set config.apiVersion=controller.config.cert-manager.io/v1alpha1 \
    --set config.kind=ControllerConfiguration \
    --set config.gatewayAPI.enabled=true \
    --wait --timeout 5m

log "prism-ctf $PRISM_CTF_VERSION"
curl -fsSL "https://github.com/pwnlentoni/prism-ctf/releases/download/$PRISM_CTF_VERSION/install.yaml" |
    sed -e "s/\${challs_domain}/$CHALLS_DOMAIN/g" \
        -e "s/\${nodeport_mode}/false/g" \
        -e "s/\${use_affinity}/false/g" |
    kubectl apply --server-side -f -
kubectl -n prism-ctf-system wait deploy --all --for=condition=Available --timeout=5m

log "plugin image $PLUGIN_IMAGE"
docker build -t "$PLUGIN_IMAGE" "$repo"
"$KIND" load docker-image "$PLUGIN_IMAGE" --name "$CLUSTER"

log "CTFd"
kubectl apply --server-side -f "$here/manifests/ctfd.yaml"
# a reused cluster keeps the old pod, whose image volume still holds the previous build
kubectl -n ctfd rollout restart deploy/ctfd
kubectl -n ctfd rollout status deploy/valkey --timeout=5m
kubectl -n ctfd rollout status deploy/ctfd --timeout=10m

log "port-forward localhost:$PORT -> svc/ctfd"
kubectl -n ctfd port-forward svc/ctfd "$PORT:80" >/dev/null 2>&1 &
pf_pid=$!
for _ in $(seq 60); do
    curl -fsS "http://127.0.0.1:$PORT/healthcheck" >/dev/null 2>&1 && break
    sleep 1
done

log "test"
CTFD_URL="http://127.0.0.1:$PORT" CTFD_TOKEN="$ADMIN_TOKEN" CHALLS_DOMAIN="$CHALLS_DOMAIN" \
    python3 "$here/test_shared_conn.py"
