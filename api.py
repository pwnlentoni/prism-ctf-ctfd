from flask_restx import Namespace, Resource

from CTFd.utils.decorators import admins_only, authed_only

from .utils import get_logger, get_current_user
from .shared_challenges import refresh_shared_challs

logger = get_logger(__name__)

admin_namespace = Namespace("prism-ctf-admin", decorators=[admins_only])
user_namespace = Namespace("prism-ctf-user", decorators=[authed_only])


@admin_namespace.route("/shared/refresh")
class AdminInstance(Resource):
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
