from lightkube.core.resource_registry import resource_registry
from lightkube.generic_resource import GenericGlobalResource
from typing import Type
import lightkube

_shared_chall_type: Type[GenericGlobalResource] | None = None
_isolated_chall_type: Type[GenericGlobalResource] | None = None
_chall_instance_type: Type[GenericGlobalResource] | None = None

FIELD_MANAGER = "prism-ctf.pwnlentoni.team/ctfd"
API_VERSION = "prism-ctf.pwnlentoni.team/v1"
SHARED_KIND = "SharedChallenge"
ISOLATED_KIND = "IsolatedChallenge"
INSTANCE_KIND = "ChallengeInstance"

CTFD_ID_LABEL = "prism-ctf.pwnlentoni.team/ctfd-id"

kube = lightkube.Client(field_manager=FIELD_MANAGER)


class UserFacingException(Exception):
    def __init__(self, msg: str):
        self.msg = msg


class UserFacingNotFound(Exception):
    pass


def get_shared_chall_type() -> Type[GenericGlobalResource]:
    global _shared_chall_type
    if _shared_chall_type is None:
        _shared_chall_type = resource_registry.get(API_VERSION, SHARED_KIND)  # type: ignore
        if _shared_chall_type is None:
            raise Exception("prism-ctf crds not correctly installed")
    return _shared_chall_type


def get_isolated_chall_type() -> Type[GenericGlobalResource]:
    global _isolated_chall_type
    if _isolated_chall_type is None:
        _isolated_chall_type = resource_registry.get(API_VERSION, ISOLATED_KIND)  # type: ignore
        if _isolated_chall_type is None:
            raise Exception("prism-ctf crds not correctly installed")
    return _isolated_chall_type


def get_chall_instance_type() -> Type[GenericGlobalResource]:
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
