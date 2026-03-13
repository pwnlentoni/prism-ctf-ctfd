from flask import abort, stream_with_context, Response, json, request
from flask_restx import Namespace, Resource

from CTFd.utils import db
from CTFd.utils.decorators import (
    admins_only,
    authed_only,
    require_verified_emails,
    during_ctf_time_only,
)

from .utils import (
    get_logger,
    get_current_user,
    require_joined_team,
    is_admin,
    get_current_user_account_id,
)
from .shared_challenges import refresh_shared_challs, refresh_shared_chall
from .models import Instances, IsolatedChallenge, SharedChallenge
from .isolated_challenges import (
    create_instance,
    delete_instance,
    watch_instance_status,
    get_instance,
    extend_instance,
    get_instance_status,
    instance_name,
)
from .k8s import (
    CTFD_ID_LABEL,
    UserFacingException,
    UserFacingNotFound,
    get_owned_pod_logs,
    list_instances,
    get_shared_challenge,
    restart_owned_rollouts,
)

logger = get_logger(__name__)

admin_namespace = Namespace("prism-ctf-admin", decorators=[admins_only])
user_namespace = Namespace(
    "prism-ctf-user",
    decorators=[
        authed_only,
        require_verified_emails,
        during_ctf_time_only,
        require_joined_team,
    ],
)


def _serialize_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return f"{value.isoformat()}Z"
    return value.isoformat()


def _parse_connection_info(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _extract_ready_status(resource) -> dict | None:
    status = resource.get("status", {}) if resource is not None else {}
    conditions = status.get("conditions") or []
    ready = next(
        (condition for condition in conditions if condition.get("type") == "Ready"),
        None,
    )
    if ready is None:
        return None
    return {"ready": ready.get("status") == "True"}


def _get_instance_owner_id(instance: Instances) -> int:
    if instance.team_id is not None:
        return instance.team_id
    if instance.user_id is not None:
        return instance.user_id
    raise UserFacingException("instance is missing both team and user ownership")


def _get_admin_instance_root(instance: Instances):
    if instance.challenge is None:
        raise UserFacingException("instance challenge relationship is missing")
    owner_id = _get_instance_owner_id(instance)
    return get_instance(owner_id, instance.challenge)


def _active_instance_key_from_resource(resource) -> tuple[int, int] | None:
    metadata = getattr(resource, "metadata", None)
    labels = getattr(metadata, "labels", None) or {}
    spec = getattr(resource, "spec", None) or {}

    challenge_id = labels.get(CTFD_ID_LABEL)
    owner_id = spec.get("team")
    if challenge_id is None or owner_id is None:
        return None

    try:
        return int(challenge_id), int(owner_id)
    except (TypeError, ValueError):
        return None


def _list_active_admin_instances() -> list[Instances]:
    active_keys = {
        key
        for resource in list_instances()
        if (key := _active_instance_key_from_resource(resource)) is not None
    }

    instances: list[Instances] = []
    seen_keys: set[tuple[int, int]] = set()
    for instance in Instances.query.order_by(
        Instances.started_at.desc(), Instances.id.desc()
    ).all():
        if instance.challenge_id is None or instance.challenge is None:
            continue

        owner_id = _get_instance_owner_id(instance)
        instance_key = (instance.challenge_id, owner_id)
        if instance_key not in active_keys or instance_key in seen_keys:
            continue

        instances.append(instance)
        seen_keys.add(instance_key)

    return instances


def _serialize_instance(instance: Instances) -> dict:
    owner_id = _get_instance_owner_id(instance)
    challenge = instance.challenge
    if challenge is None:
        raise UserFacingException("instance challenge relationship is missing")

    status = None
    crd_present = False
    status_error = None
    try:
        kube_instance = _get_admin_instance_root(instance)
        status = get_instance_status(kube_instance)
        crd_present = True
    except UserFacingNotFound:
        pass
    except Exception:
        logger.exception(
            "failed to fetch kubernetes status for instance %s", instance.id
        )
        status_error = "failed to fetch kubernetes status"

    return {
        "id": instance.id,
        "challenge_id": challenge.id,
        "challenge_name": challenge.name,
        "owner_id": owner_id,
        "user_id": instance.user_id,
        "user_name": getattr(instance.user, "name", None),
        "team_id": instance.team_id,
        "team_name": getattr(instance.team, "name", None),
        "started_at": _serialize_datetime(instance.started_at),
        "kube_name": instance_name(owner_id, challenge),
        "resource_present": crd_present,
        "status": status,
        "status_error": status_error,
    }


def _serialize_shared_challenge(challenge: SharedChallenge) -> dict:
    connection_info = _parse_connection_info(challenge.connection_info)
    crd_present = False
    status = None
    status_error = None

    try:
        kube_challenge = get_shared_challenge(challenge.id)
    except Exception:
        logger.exception("failed to fetch kubernetes shared challenge %s", challenge.id)
        kube_challenge = None
        status_error = "failed to fetch kubernetes status"

    if kube_challenge is not None:
        crd_present = True
        status = _extract_ready_status(kube_challenge)
        if kube_challenge.get("status", {}).get("exposedUrls"):
            connection_info = kube_challenge["status"]["exposedUrls"]

    return {
        "id": challenge.id,
        "name": challenge.name,
        "connection_info": connection_info,
        "resource_present": crd_present,
        "status": status,
        "status_error": status_error,
    }


@admin_namespace.route("/shared/refresh")
class SharedRefreshAll(Resource):
    @staticmethod
    def post():
        user = get_current_user()
        logger.info(
            f"shared challenges refresh requested by {user.name!r} [id: {user.id}]"
        )
        try:
            refresh_shared_challs()
            return {"success": True}
        except Exception as e:
            logger.exception("failed to refresh all shared challenges")
            return {"success": False, "message": repr(e)}, 500


@admin_namespace.route("/shared/refresh/<int:id>")
class SharedRefreshSpecific(Resource):
    @staticmethod
    def post(id: int):
        user = get_current_user()
        logger.info(
            f"shared challenge refresh by id: {id} requested by {user.name!r} [id: {user.id}]"
        )
        try:
            refresh_shared_chall(id)
            return {"success": True}
        except Exception as e:
            logger.exception("failed to refresh shared challenge %s", id)
            return {"success": False, "message": repr(e)}, 500


@admin_namespace.route("/overview")
class PrismAdminOverview(Resource):
    @staticmethod
    def get():
        instances = [
            _serialize_instance(instance) for instance in _list_active_admin_instances()
        ]
        shared_challenges = [
            _serialize_shared_challenge(challenge)
            for challenge in SharedChallenge.query.order_by(
                SharedChallenge.id.asc()
            ).all()
        ]
        return {
            "success": True,
            "instances": instances,
            "shared_challenges": shared_challenges,
        }


@admin_namespace.route("/instance/<int:instance_id>/logs")
class PrismInstanceLogs(Resource):
    @staticmethod
    def get(instance_id: int):
        instance = Instances.query.filter_by(id=instance_id).first()
        if instance is None:
            return {"success": False, "message": "instance not found"}, 404

        try:
            logs = get_owned_pod_logs(_get_admin_instance_root(instance))
        except UserFacingNotFound:
            return {
                "success": False,
                "message": "instance resource not found in kubernetes",
            }, 404
        except UserFacingException as e:
            return {"success": False, "message": e.msg}, 400
        except Exception:
            logger.exception("failed to fetch logs for instance %s", instance_id)
            return {"success": False, "message": "failed to fetch logs"}, 500

        return {"success": True, "logs": logs}


@admin_namespace.route("/instance/<int:instance_id>/restart")
class PrismInstanceRestart(Resource):
    @staticmethod
    def post(instance_id: int):
        instance = Instances.query.filter_by(id=instance_id).first()
        if instance is None:
            return {"success": False, "message": "instance not found"}, 404

        try:
            restarted = restart_owned_rollouts(_get_admin_instance_root(instance))
        except UserFacingNotFound:
            return {
                "success": False,
                "message": "instance resource not found in kubernetes",
            }, 404
        except UserFacingException as e:
            return {"success": False, "message": e.msg}, 400
        except Exception:
            logger.exception("failed to restart workloads for instance %s", instance_id)
            return {"success": False, "message": "failed to restart workloads"}, 500

        return {"success": True, **restarted}


@admin_namespace.route("/shared/<int:id>/logs")
class PrismSharedLogs(Resource):
    @staticmethod
    def get(id: int):
        challenge = SharedChallenge.query.filter_by(id=id).first()
        if challenge is None:
            return {"success": False, "message": "shared challenge not found"}, 404

        try:
            kube_challenge = get_shared_challenge(id)
        except Exception:
            logger.exception("failed to resolve shared challenge %s in kubernetes", id)
            return {
                "success": False,
                "message": "failed to query kubernetes for the shared challenge",
            }, 500
        if kube_challenge is None:
            return {
                "success": False,
                "message": "shared challenge resource not found in kubernetes",
            }, 404

        try:
            logs = get_owned_pod_logs(kube_challenge)
        except UserFacingException as e:
            return {"success": False, "message": e.msg}, 400
        except Exception:
            logger.exception("failed to fetch logs for shared challenge %s", id)
            return {"success": False, "message": "failed to fetch logs"}, 500

        return {"success": True, "logs": logs}


@admin_namespace.route("/shared/<int:id>/restart")
class PrismSharedRestart(Resource):
    @staticmethod
    def post(id: int):
        challenge = SharedChallenge.query.filter_by(id=id).first()
        if challenge is None:
            return {"success": False, "message": "shared challenge not found"}, 404

        try:
            kube_challenge = get_shared_challenge(id)
        except Exception:
            logger.exception("failed to resolve shared challenge %s in kubernetes", id)
            return {
                "success": False,
                "message": "failed to query kubernetes for the shared challenge",
            }, 500
        if kube_challenge is None:
            return {
                "success": False,
                "message": "shared challenge resource not found in kubernetes",
            }, 404

        try:
            restarted = restart_owned_rollouts(kube_challenge)
        except UserFacingException as e:
            return {"success": False, "message": e.msg}, 400
        except Exception:
            logger.exception("failed to restart workloads for shared challenge %s", id)
            return {"success": False, "message": "failed to restart workloads"}, 500

        return {"success": True, **restarted}


@user_namespace.route("/instance/<int:id>/extend")
class ExtendIsolatedInstance(Resource):
    @staticmethod
    def post(id: int):
        chal: IsolatedChallenge = IsolatedChallenge.query.get_or_404(id)
        if chal.state != "visible" and not is_admin():
            abort(404)

        try:
            instance = get_instance(get_current_user_account_id(), chal)
        except UserFacingNotFound:
            abort(404)

        user = get_current_user()
        logger.info(
            f"instance extension requested by {user.name!r} [id: {user.id}] for challenge {chal.name!r} [id: {chal.id}]"
        )

        try:
            extend_instance(instance, chal.get_lifetime())
            return {"success": True}
        except Exception:
            logger.exception("failed to extend instance")
            return {"success": False, "message": "failed to extend instance"}, 500


@user_namespace.route("/instance/<int:id>")
class IsolatedInstance(Resource):
    @staticmethod
    def get(id: int):
        chal: IsolatedChallenge = IsolatedChallenge.query.get_or_404(id)
        if chal.state != "visible" and not is_admin():
            abort(404)

        try:
            instance = get_instance(get_current_user_account_id(), chal)
        except UserFacingNotFound:
            abort(404)

        if request.accept_mimetypes.best == "text/event-stream":

            def gen():
                prev_ev = ""
                for ev_type, event in watch_instance_status(instance):
                    ev = json.dumps(event)
                    if ev == prev_ev:
                        continue
                    prev_ev = ev
                    yield "\n".join([f"event: {ev_type}", f"data: {ev}"]) + "\n\n"

            return Response(stream_with_context(gen()), mimetype="text/event-stream")

        try:
            return get_instance_status(instance)
        except UserFacingException as e:
            abort(500, e.msg)

    @staticmethod
    def put(id: int):
        chal: IsolatedChallenge = IsolatedChallenge.query.get_or_404(id)
        if chal.state != "visible" and not is_admin():
            abort(404)
        user = get_current_user()

        logger.info(
            f"instance creation requested by {user.name!r} [id: {user.id}] for challenge {chal.name!r} [id: {chal.id}]"
        )

        try:
            kube_instance = create_instance(get_current_user_account_id(), chal)
        except UserFacingException as e:
            logger.warning(f"instance creation rejected: {e.msg}")
            return {"success": False, "message": e.msg}, 400
        except Exception:
            logger.exception("instance creation failed")
            return {"success": False, "message": "instance creation failed"}, 500

        assert kube_instance.spec

        instance = Instances(
            challenge=chal,
            user=user,
            team=user.team,
            flag=kube_instance.spec["flag"],
        )

        db.session.add(instance)
        db.session.commit()

        return {"success": True}

    @staticmethod
    def delete(id: int):
        chal: IsolatedChallenge = IsolatedChallenge.query.get_or_404(id)
        if chal.state != "visible" and not is_admin():
            abort(404)
        user = get_current_user()
        logger.info(
            f"instance deletion requested by {user.name!r} [id: {user.id}] for challenge {chal.name!r} [id: {chal.id}]"
        )
        try:
            delete_instance(get_current_user_account_id(), chal)
        except UserFacingNotFound:
            abort(404)
        except UserFacingException as e:
            return {"success": False, "message": e.msg}, 400
        except Exception:
            return {
                "success": False,
                "message": "An error occured, please open a ticket",
            }, 500
        return {"success": True}
