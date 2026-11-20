from __future__ import annotations

from typing import TYPE_CHECKING

from .utils import get_logger
from .k8s import list_shared_challenges, CTFD_ID_LABEL, get_shared_challenge
from CTFd.cache import cache
from CTFd.models import (
    db,
)

import json

if TYPE_CHECKING:
    from .models import SharedChallenge

logger = get_logger(__name__)

# The live connection info (the CR's status.exposedUrls, JSON-encoded) is cached so that
# opening a challenge costs at most one kube API call per challenge per TTL across all
# workers. "" means "not available": CR missing, not ready yet or kube unreachable.
_CONN_CACHE_KEY = "prism_ctf:shared_conn:{}"
_CONN_TTL_READY = 30
_CONN_TTL_PENDING = 5


def _conn_cache_key(chal_id: int) -> str:
    return _CONN_CACHE_KEY.format(chal_id)


def _cache_connection_info(chal_id: int, live: str) -> None:
    cache.set(
        _conn_cache_key(chal_id),
        live,
        timeout=_CONN_TTL_READY if live else _CONN_TTL_PENDING,
    )


def invalidate_connection_info(chal_id: int) -> None:
    cache.delete(_conn_cache_key(chal_id))


def _encode_exposed_urls(chall_crd) -> str:
    urls = (chall_crd.get("status") or {}).get("exposedUrls")
    return json.dumps(urls) if urls else ""


def _fetch_connection_info(chal_id: int) -> str:
    """Read the live connection info from kube and cache it. Raises on kube errors."""
    chall_crd = get_shared_challenge(chal_id)
    live = "" if chall_crd is None else _encode_exposed_urls(chall_crd)
    _cache_connection_info(chal_id, live)
    return live


def _store_connection_info(chall_model: SharedChallenge, live: str) -> None:
    """Persist a non-empty live value if it differs from the stored one."""
    if not live or live == chall_model.connection_info:
        return
    chall_model.connection_info = live
    db.session.commit()
    logger.info(
        f"updated shared challenge {chall_model.name!r} [id: {chall_model.id}] connection info"
    )


def sync_connection_info(chall_model: SharedChallenge) -> None:
    """
    Bring a shared challenge's stored connection_info in line with the live CR status:
    the first fill once the challenge is ready, and any later hostname change. Keeps the
    stored value when the CR is missing, not ready or kube is unreachable. Never raises.
    """
    live = cache.get(_conn_cache_key(chall_model.id))
    if live is None:
        try:
            live = _fetch_connection_info(chall_model.id)
        except Exception:
            logger.warning(
                f"failed to get kube status of shared challenge {chall_model.name!r} [id: {chall_model.id}]",
                exc_info=True,
            )
            _cache_connection_info(chall_model.id, "")
            return
    try:
        _store_connection_info(chall_model, live)
    except Exception:
        db.session.rollback()
        logger.warning(
            f"failed to store connection info of shared challenge {chall_model.name!r} [id: {chall_model.id}]",
            exc_info=True,
        )


def refresh_shared_challs():
    from .models import SharedChallenge  # models.shared imports this module

    challs = list_shared_challenges()

    for chall in challs:
        assert chall.metadata
        assert chall.metadata.labels
        kube_id = int(chall.metadata.labels[CTFD_ID_LABEL])
        chall_model: SharedChallenge = SharedChallenge.query.filter_by(
            id=kube_id,
        ).first()
        if chall_model is None:
            raise Exception(f"chall model not found for id={kube_id}")
        live = _encode_exposed_urls(chall)
        _cache_connection_info(kube_id, live)
        _store_connection_info(chall_model, live)
        logger.info(
            f"refreshed shared challenge {chall.metadata.name!r} [{chall_model.name!r} {chall_model.id}] connection info"
        )


def refresh_shared_chall(id: int):
    from .models import SharedChallenge  # models.shared imports this module

    chall_model: SharedChallenge = SharedChallenge.query.filter_by(id=id).first()
    if chall_model is None:
        raise Exception("chall model not found")
    live = _fetch_connection_info(id)
    if not live:
        raise Exception("crd object not found or not ready")
    _store_connection_info(chall_model, live)
    logger.info(
        f"refreshed shared challenge [{chall_model.name!r} {chall_model.id}] connection info"
    )
