from .utils import get_logger
from .k8s import list_shared_challenges
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
        chall_model: DynamicSharedChallenge = DynamicSharedChallenge.query.filter_by(
            id=int(chall.metadata.labels["prism-ctf.pwnlentoni.team/ctfd-id"])
        ).first()
        db.session.add(chall_model)
        chall_model.connection_info = json.dumps(chall["status"]["exposedUrls"])
        logger.info(
            f"refreshed shared challenge {chall.metadata.name} [{chall_model.name!r} {chall_model.id}] connection info"
        )
    db.session.commit()
