from datetime import UTC, datetime
from typing import Any, Mapping, Type, cast

from lightkube.core.resource_registry import resource_registry
from lightkube.generic_resource import GenericGlobalResource
from lightkube.resources.apps_v1 import DaemonSet, Deployment, ReplicaSet, StatefulSet
from lightkube.resources.core_v1 import Namespace, Pod
from lightkube.types import PatchType
import lightkube

_shared_chall_type: Type[Any] | None = None
_isolated_chall_type: Type[Any] | None = None
_chall_instance_type: Type[Any] | None = None

FIELD_MANAGER = "prism-ctf.pwnlentoni.team/ctfd"
API_VERSION = "prism-ctf.pwnlentoni.team/v1"
SHARED_KIND = "SharedChallenge"
ISOLATED_KIND = "IsolatedChallenge"
INSTANCE_KIND = "ChallengeInstance"

CTFD_ID_LABEL = "prism-ctf.pwnlentoni.team/ctfd-id"
CHALLENGE_LABEL = "prism-ctf.pwnlentoni.team/challenge"
CHALLENGE_NAMESPACE_LABEL = "prism-ctf.pwnlentoni.team/challenge-namespace"
TEAM_LABEL = "prism-ctf.pwnlentoni.team/team"

kube = lightkube.Client(field_manager=FIELD_MANAGER)

_OWNED_DISCOVERY_RESOURCES = (Deployment, StatefulSet, DaemonSet, ReplicaSet, Pod)
_ROLLABLE_RESOURCES = (Deployment, StatefulSet, DaemonSet)
_MAX_OWNED_RESOURCES = 200
_MAX_LOG_PODS = 20
_MAX_LOG_CONTAINERS_PER_POD = 10


class UserFacingException(Exception):
    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


class UserFacingNotFound(Exception):
    pass


def get_shared_chall_type() -> Type[Any]:
    global _shared_chall_type
    if _shared_chall_type is None:
        _shared_chall_type = resource_registry.get(API_VERSION, SHARED_KIND)  # type: ignore
        if _shared_chall_type is None:
            raise Exception("prism-ctf crds not correctly installed")
    return _shared_chall_type


def get_isolated_chall_type() -> Type[Any]:
    global _isolated_chall_type
    if _isolated_chall_type is None:
        _isolated_chall_type = resource_registry.get(API_VERSION, ISOLATED_KIND)  # type: ignore
        if _isolated_chall_type is None:
            raise Exception("prism-ctf crds not correctly installed")
    return _isolated_chall_type


def get_chall_instance_type() -> Type[Any]:
    global _chall_instance_type
    if _chall_instance_type is None:
        _chall_instance_type = resource_registry.get(API_VERSION, INSTANCE_KIND)  # type: ignore
        if _chall_instance_type is None:
            raise Exception("prism-ctf crds not correctly installed")
    return _chall_instance_type


def list_shared_challenges():
    return kube.list(get_shared_chall_type())


def get_shared_challenge(ctfd_id: int) -> GenericGlobalResource | None:
    challs_iter = kube.list(
        get_shared_chall_type(), labels={CTFD_ID_LABEL: str(ctfd_id)}
    )
    try:
        return next(iter(challs_iter))
    except StopIteration:
        return None


def get_instance(challenge: int, owner: str):
    instance_iter = kube.list(
        get_chall_instance_type(),
        labels={CTFD_ID_LABEL: str(challenge)},
        fields={"spec.team": owner},
    )
    try:
        return next(iter(instance_iter))
    except StopIteration:
        return None


def _resource_metadata(resource: Any):
    metadata = getattr(resource, "metadata", None)
    if metadata is None:
        raise UserFacingException("resource is missing metadata")
    return metadata


def _resource_name(resource: Any) -> str:
    name = getattr(_resource_metadata(resource), "name", None)
    if not name:
        raise UserFacingException("resource is missing metadata.name")
    return name


def _resource_uid(resource: Any) -> str | None:
    return getattr(_resource_metadata(resource), "uid", None)


def _resource_identity(resource: Any) -> tuple[str, str, str]:
    return (
        resource.__class__.__name__,
        _resource_namespace(resource) or "",
        _resource_name(resource),
    )


def _resource_labels(resource: Any) -> dict[str, str]:
    labels = getattr(_resource_metadata(resource), "labels", None)
    return labels or {}


def _resource_namespace(resource: Any) -> str | None:
    return getattr(_resource_metadata(resource), "namespace", None)


def _resource_namespace_or_raise(resource: Any) -> str:
    namespace = _resource_namespace(resource)
    if namespace is None:
        raise UserFacingException("resource is missing metadata.namespace")
    return namespace


def _root_spec(root: Any) -> dict[str, Any]:
    return getattr(root, "spec", None) or {}


def _root_challenge_name(root: Any) -> str:
    return str(_root_spec(root).get("challenge") or _resource_name(root))


def _root_team(root: Any) -> str | None:
    team = _root_spec(root).get("team")
    return None if team is None else str(team)


def _root_search_namespaces(root: Any) -> list[str]:
    if resource_namespace := _resource_namespace(root):
        return [resource_namespace]

    namespace_labels: Mapping[str, str] = {
        CHALLENGE_LABEL: _root_challenge_name(root),
        CHALLENGE_NAMESPACE_LABEL: "true",
    }
    if root_team := _root_team(root):
        namespace_labels = {
            **namespace_labels,
            TEAM_LABEL: root_team,
        }

    namespaces = [
        namespace.metadata.name
        for namespace in kube.list(
            Namespace,
            labels=cast(Any, namespace_labels),
        )
        if namespace.metadata and namespace.metadata.name
    ]
    return namespaces or [kube.namespace]


def _resource_matches_root_fallback(resource: Any, root: Any) -> bool:
    resource_name = _resource_name(resource)
    root_name = _resource_name(root)
    root_challenge = _root_challenge_name(root)
    root_team = _root_team(root)
    if resource_name == root_name or resource_name.startswith(f"{root_name}-"):
        return True

    resource_labels = _resource_labels(resource)
    root_labels = _resource_labels(root)

    if root_name in resource_labels.values():
        return True

    if resource_labels.get(CHALLENGE_LABEL) == root_challenge:
        if root_team is None or resource_labels.get(TEAM_LABEL) == root_team:
            return True

    root_ctfd_id = root_labels.get(CTFD_ID_LABEL)
    if root_ctfd_id is None:
        return False

    if resource_labels.get(CTFD_ID_LABEL) != root_ctfd_id:
        return False

    if root_team is None:
        return True

    for label_key, label_value in resource_labels.items():
        if root_team == str(label_value) and (
            "team" in label_key.lower() or "owner" in label_key.lower()
        ):
            return True

    return False


def _owner_reference_uids(resource: Any) -> set[str]:
    owner_references = (
        getattr(_resource_metadata(resource), "ownerReferences", None) or []
    )
    return {
        uid
        for owner_reference in owner_references
        if (uid := getattr(owner_reference, "uid", None)) is not None
    }


def get_owned_resources(root: Any) -> list[Any]:
    root_uid = _resource_uid(root)
    if root_uid is None:
        raise UserFacingException("resource is missing metadata.uid")

    known_uids = {root_uid}
    seen_resources: set[tuple[str, str, str]] = set()
    discovered: list[Any] = []
    namespaces = _root_search_namespaces(root)

    for _ in range(len(_OWNED_DISCOVERY_RESOURCES) + 2):
        added_resource = False
        for resource_type in _OWNED_DISCOVERY_RESOURCES:
            for namespace in namespaces:
                for resource in kube.list(resource_type, namespace=namespace):
                    resource_identity = _resource_identity(resource)
                    if resource_identity in seen_resources:
                        continue

                    has_owner_reference_match = bool(
                        _owner_reference_uids(resource) & known_uids
                    )
                    if (
                        not has_owner_reference_match
                        and not _resource_matches_root_fallback(resource, root)
                    ):
                        continue

                    discovered.append(resource)
                    seen_resources.add(resource_identity)
                    if resource_uid := _resource_uid(resource):
                        known_uids.add(resource_uid)
                    if len(discovered) > _MAX_OWNED_RESOURCES:
                        raise UserFacingException(
                            "too many owned resources were discovered for this object"
                        )
                    added_resource = True

        if not added_resource:
            break

    return discovered


def get_owned_pods(root: Any) -> list[Pod]:
    pods = [
        resource for resource in get_owned_resources(root) if isinstance(resource, Pod)
    ]
    if len(pods) > _MAX_LOG_PODS:
        raise UserFacingException("too many pods found for this resource")
    return sorted(pods, key=_resource_name)


def get_owned_rollable_resources(root: Any) -> list[Any]:
    workloads = [
        resource
        for resource in get_owned_resources(root)
        if isinstance(resource, _ROLLABLE_RESOURCES)
    ]
    return sorted(
        workloads,
        key=lambda resource: (resource.__class__.__name__, _resource_name(resource)),
    )


def get_owned_pod_logs(root: Any, tail_lines: int = 200) -> list[dict[str, str]]:
    pods = get_owned_pods(root)
    if not pods:
        raise UserFacingException("no pods found for this resource")

    logs: list[dict[str, str]] = []
    for pod in pods:
        pod_name = _resource_name(pod)
        containers = [
            container.name
            for container in getattr(getattr(pod, "spec", None), "containers", [])
            if getattr(container, "name", None)
        ]
        if len(containers) > _MAX_LOG_CONTAINERS_PER_POD:
            raise UserFacingException(f"too many containers found for pod {pod_name}")
        if not containers:
            containers = [""]

        for container_name in containers:
            kwargs = {
                "namespace": _resource_namespace_or_raise(pod),
                "tail_lines": tail_lines,
                "timestamps": True,
            }
            if container_name:
                kwargs["container"] = container_name

            log_output = kube.log(pod_name, **kwargs)
            if isinstance(log_output, bytes):
                rendered_output = log_output.decode()
            elif isinstance(log_output, str):
                rendered_output = log_output
            else:
                rendered_output = "".join(log_output)

            logs.append(
                {
                    "pod": pod_name,
                    "container": container_name or "default",
                    "log": rendered_output,
                }
            )

    return logs


def restart_owned_rollouts(root: Any) -> dict[str, Any]:
    workloads = get_owned_rollable_resources(root)
    if not workloads:
        raise UserFacingException("no restartable workloads found for this resource")

    restarted_at = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    restarted: list[dict[str, str]] = []
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                    }
                }
            }
        }
    }

    for workload in workloads:
        kube.patch(
            workload.__class__,
            _resource_name(workload),
            patch,
            namespace=_resource_namespace_or_raise(workload),
            patch_type=PatchType.STRATEGIC,
        )
        restarted.append(
            {
                "kind": workload.__class__.__name__,
                "name": _resource_name(workload),
            }
        )

    return {
        "restarted_at": restarted_at,
        "workloads": restarted,
    }
