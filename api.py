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
from .models import Instances, DynamicIsolatedChallenge
from .isolated_challenges import (
    create_instance,
    delete_instance,
    watch_instance_status,
    get_instance,
    extend_instance,
    get_instance_status,
)
from .k8s import UserFacingException, UserFacingNotFound

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
            return {"success": False, "error": repr(e)}


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
            return {"success": False, "error": repr(e)}


@user_namespace.route("/instance/<int:id>/extend")
class ExtendIsolatedInstance(Resource):
    @staticmethod
    def post(id: int):
        chal: DynamicIsolatedChallenge = DynamicIsolatedChallenge.query.get_or_404(id)
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
            return {"success": False}


@user_namespace.route("/instance/<int:id>")
class IsolatedInstance(Resource):
    @staticmethod
    def get(id: int):
        chal: DynamicIsolatedChallenge = DynamicIsolatedChallenge.query.get_or_404(id)
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
                    yield "\n".join([
                        f"event: {ev_type}",
                        f"data: {ev}"
                    ]) + "\n\n"

            return Response(stream_with_context(gen()), mimetype="text/event-stream")

        try:
            return get_instance_status(instance)
        except UserFacingException as e:
            abort(500, e.msg)
        

    @staticmethod
    def put(id: int):
        chal: DynamicIsolatedChallenge = DynamicIsolatedChallenge.query.get_or_404(id)
        if chal.state != "visible" and not is_admin():
            abort(404)
        user = get_current_user()

        logger.info(
            f"instance creation requested by {user.name!r} [id: {user.id}] for challenge {chal.name!r} [id: {chal.id}]"
        )

        try:
            kube_instance = create_instance(get_current_user_account_id(), chal)
        except UserFacingException as e:
            logger.warn(f"instance creation rejected: {e.args[0]}")
            return {"success": False, "message": e.args[0]}
        except Exception:
            logger.exception("instance creation failed")
            return {"success": False}

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
        chal: DynamicIsolatedChallenge = DynamicIsolatedChallenge.query.get_or_404(id)
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
            return {"success": False, "message": e.msg}
        except Exception:
            return {
                "success": False,
                "message": "An error occured, please open a ticket",
            }
        return {"success": True}
