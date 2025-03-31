from .utils import get_logger
from .k8s import list_shared_challenges, CTFD_ID_LABEL, get_shared_challenge
from .models import DynamicSharedChallenge
from CTFd.models import (
    db,
)

import json

logger = get_logger(__name__)


def refresh_shared_challs():
    challs = list_shared_challenges()

    for chall in challs:
        assert chall.metadata
        assert chall.metadata.labels
        kube_id = int(chall.metadata.labels[CTFD_ID_LABEL])
        chall_model: DynamicSharedChallenge = DynamicSharedChallenge.query.filter_by(
            id=kube_id,
        ).first()
        if chall_model is None:
            raise Exception(f"chall model not found for id={kube_id}")
        db.session.add(chall_model)
        chall_model.connection_info = json.dumps(chall["status"]["exposedUrls"])
        logger.info(
            f"refreshed shared challenge {chall.metadata.name!r} [{chall_model.name!r} {chall_model.id}] connection info"
        )
    db.session.commit()


def refresh_shared_chall(id: int):
    chall_model: DynamicSharedChallenge = DynamicSharedChallenge.query.filter_by(
        id=id
    ).first()
    if chall_model is None:
        raise Exception("chall model not found")
    chall_crd = get_shared_challenge(id)
    if chall_crd is None:
        raise Exception("crd object not found")
    assert chall_crd.metadata

    db.session.add(chall_model)
    chall_model.connection_info = json.dumps(chall_model["status"]["exposedUrls"])
    logger.info(
        f"refreshed shared challenge {chall_crd.metadata.name!r} [{chall_model.name!r} {chall_model.id}] connection info"
    )
    db.session.commit()
