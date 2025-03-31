from __future__ import annotations

import datetime


from .k8s import (
    kube,
    get_chall_instance_type,
    CTFD_ID_LABEL,
    UserFacingException,
    UserFacingNotFound,
)
import typing
from collections.abc import Generator

if typing.TYPE_CHECKING:
    from .models import DynamicIsolatedChallenge
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.generic_resource import GenericGlobalResource
from lightkube.core.exceptions import ApiError
from .utils import get_logger

logger = get_logger(__name__)


def instance_name(owner: int, challenge: DynamicIsolatedChallenge) -> str:
    return f"{challenge.get_kube_name()}-{owner}"


def create_instance(
    owner: int, challenge: DynamicIsolatedChallenge
) -> GenericGlobalResource:
    ChallengeInstance = get_chall_instance_type()
    inst = ChallengeInstance(
        metadata=ObjectMeta(
            name=instance_name(owner, challenge),
            labels={CTFD_ID_LABEL: str(challenge.id)},
        ),
        spec={
            "team": str(owner),
            "challenge": challenge.get_kube_name(),
        },
    )
    try:
        return kube.create(inst)
    except ApiError as e:
        if (
            e.status.message
            and "webhook" in e.status.message
            and "denied the request" in e.status.message
        ):
            raise UserFacingException(e.status.message.split("denied the request: ")[1])
        else:
            raise


def delete_instance(owner: int, challenge: DynamicIsolatedChallenge):
    try:
        kube.delete(get_chall_instance_type(), instance_name(owner, challenge))
    except ApiError as e:
        if e.response.status_code == 404:
            raise UserFacingNotFound()
        else:
            raise


def get_instance(
    owner: int, challenge: DynamicIsolatedChallenge
) -> GenericGlobalResource:
    try:
        return kube.get(get_chall_instance_type(), instance_name(owner, challenge))
    except ApiError as e:
        if e.response.status_code == 404:
            raise UserFacingNotFound()
        else:
            raise


def get_instance_status(instance: GenericGlobalResource) -> dict:
    upd = {
        "ready": False,
        "expiration": instance["spec"]["expiration"],
    }

    if "status" not in instance:
        return upd

    if (conds := instance["status"]["conditions"]) and (
        ready := next((cond for cond in conds if cond["type"] == "Ready"), None)
    ) is not None:
        upd["ready"] = ready["status"] == "True"
    if urls := instance["status"]["exposedUrls"]:
        upd["exposedUrls"] = urls
    return upd


def watch_instance_status(instance: GenericGlobalResource) -> Generator[tuple[typing.Literal["update"] | typing.Literal["delete"], dict], None, None]:
    assert instance.spec
    assert instance.metadata
    assert instance.metadata.labels
    assert instance.metadata.resourceVersion

    yield "update", get_instance_status(instance)

    for event, res in kube.watch(
        get_chall_instance_type(),
        fields={
            "spec.team": instance.spec["team"],
        },
        labels={
            CTFD_ID_LABEL: instance.metadata.labels[CTFD_ID_LABEL],
        },
        resource_version=instance.metadata.resourceVersion,
    ):
        if event == "DELETED":
            yield "delete", {}
            return
        elif event == "MODIFIED":
            yield "update", get_instance_status(res)
        else:
            logger.warning(f"unhandled kube event {event}: {res}")


def extend_instance(instance: GenericGlobalResource, lifetime: datetime.timedelta):
    target_expire = datetime.datetime.now(datetime.UTC) + lifetime
    target_expire = target_expire.replace(microsecond=0)

    instance["spec"]["expiration"] = target_expire.isoformat().replace("+00:00", "Z")

    kube.apply(
        get_chall_instance_type()(
            metadata=ObjectMeta(
                name=instance["metadata"]["name"],
                labels=instance["metadata"]["labels"],
            ),
            spec=instance["spec"],
        )
    )
