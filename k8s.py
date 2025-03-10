from lightkube.core.resource_registry import resource_registry
from lightkube.generic_resource import GenericGlobalResource
from typing import Type
import lightkube

kube = lightkube.Client()

_shared_chall_type: Type[GenericGlobalResource] | None = None

FIELD_MANAGER = "prism-ctf.pwnlentoni.team/ctfd"
API_VERSION = "prism-ctf.pwnlentoni.team/v1"
SHARED_KIND = "SharedChallenge"


def get_shared_chall_type() -> Type[GenericGlobalResource]:
    global _shared_chall_type
    if _shared_chall_type is None:
        _shared_chall_type = resource_registry.get(API_VERSION, SHARED_KIND)  # type: ignore
        if _shared_chall_type is None:
            raise Exception("prism-ctf crds not correctly installed")
    return _shared_chall_type


def list_shared_challenges():
    return kube.list(get_shared_chall_type())
