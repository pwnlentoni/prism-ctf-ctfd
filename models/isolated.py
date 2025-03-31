import datetime
import json

from CTFd.plugins.dynamic_challenges import DynamicChallenge, DynamicValueChallenge

from CTFd.utils import get_config
from CTFd.cache import cache
from CTFd.models import db
from CTFd.exceptions.challenges import (
    ChallengeCreateException,
    ChallengeUpdateException,
)

from flask import Request
from lightkube import ApiError

from .blueprint import blueprint
from ..utils import (
    get_logger,
    get_current_user,
    ASSETS_DIR,
    parse_golang_duration,
    get_current_user_account_id,
)
from ..k8s import (
    kube,
    FIELD_MANAGER,
    ISOLATED_KIND,
    get_isolated_chall_type,
    UserFacingNotFound,
)
from .utils import get_kube_spec_file, to_bool, constant_time_compare
from .instance import Instances
from ..isolated_challenges import delete_instance

logger = get_logger(__name__)


class DynamicIsolatedChallenge(DynamicChallenge):
    __mapper_args__ = {"polymorphic_identity": "prism_isolated"}
    id = db.Column(
        db.Integer,
        db.ForeignKey("dynamic_challenge.id", ondelete="CASCADE"),
        primary_key=True,
    )

    yaml_id = db.Column(db.Integer, db.ForeignKey("files.id"))
    destroy_on_flag = db.Column(db.Boolean, default=False)

    def __init__(self, *args, **kwargs):
        super(DynamicIsolatedChallenge, self).__init__(**kwargs)

    def __str__(self):
        return f"DynamicIsolatedChallenge(id={self.id}, destroy_on_flag={self.destroy_on_flag})"

    @cache.memoize()
    def get_kube_name(self) -> str:
        spec = get_kube_spec_file(self.yaml_id, ISOLATED_KIND)
        assert spec.metadata
        assert spec.metadata.name
        return spec.metadata.name

    @cache.memoize()
    def get_lifetime(self) -> datetime.timedelta:
        spec = get_kube_spec_file(self.yaml_id, ISOLATED_KIND)
        return parse_golang_duration(spec["spec"]["lifetime"])


class DynamicIsolatedValueChallenge(DynamicValueChallenge):
    id = "prism_isolated"
    name = "prism_isolated"
    templates = {  # Nunjucks templates used for each aspect of challenge editing & viewing
        "create": f"{ASSETS_DIR}/isolated/create.html",  # nunjucks on admin create
        "update": f"{ASSETS_DIR}/isolated/update.html",  # nunjucks on admin update
        "view": f"{ASSETS_DIR}/isolated/view.html",  # nunjucks on admin preview, also used as a jinja template by user frontend, thanks ctfd
    }

    scripts = {  # Scripts that are loaded when a template is loaded
        "create": f"{ASSETS_DIR}/isolated/create.js",
        "update": f"{ASSETS_DIR}/isolated/update.js",
        "view": f"{ASSETS_DIR}/isolated/view.js",
    }
    # Route at which files are accessible. This must be registered using register_plugin_assets_directory()
    route = f"{ASSETS_DIR}/"
    # Blueprint used to access the static_folder directory.
    blueprint = blueprint
    challenge_model = DynamicIsolatedChallenge

    @classmethod
    def create(cls, request: Request):
        user = get_current_user()
        logger.debug(f"Isolated challenge created by {user.name!r} [id: {user.id}]")
        data: dict[str, str | bool] = request.form.to_dict() or request.get_json()  # type: ignore

        if not isinstance(data, dict):
            logger.error(f"invalid creation request data: {data!r}")
            return

        if "yaml_id" not in data:
            logger.error("missing required `yaml_id`")
            raise ChallengeCreateException("missing required `yaml_id`")

        if "destroy_on_flag" in data:
            data["destroy_on_flag"] = to_bool(data["destroy_on_flag"])

        challenge = cls.challenge_model(**data)
        db.session.add(challenge)
        db.session.commit()

        logger.info(f"created CTFd challenge {challenge.name!r} [id: {challenge.id}]")

        try:
            chal_spec = get_kube_spec_file(challenge.yaml_id, ISOLATED_KIND)
        except Exception as e:
            logger.exception(
                f"spec get failed for {challenge.name!r}, deleting from CTFd"
            )
            super().delete(challenge)
            logger.info(f"deleted successfully {challenge.name!r}")
            raise ChallengeCreateException(
                f"spec validation failed: {e.args[0]}"
            ) from e

        try:
            logger.info(f"pushing challenge {challenge.name!r} spec to kube")
            kube.apply(chal_spec, field_manager=FIELD_MANAGER)
            logger.info(f"succesfully pushed challenge {challenge.name!r} spec to kube")
        except Exception as e:
            logger.exception(
                f"kube apply failed for {challenge.name!r}, deleting from CTFd"
            )
            super().delete(challenge)
            logger.info(f"deleted successfully {challenge.name!r}")
            if isinstance(e, ApiError):
                raise ChallengeCreateException(
                    f"kube apply failed: {e.status.message}"
                ) from e
            else:
                raise ChallengeCreateException("kube apply failed") from e

        return challenge

    @classmethod
    def read(cls, challenge: DynamicIsolatedChallenge):
        """
        This method is in used to access the data of a challenge in a format processable by the front end.

        :param challenge:
        :return: Challenge object, data dictionary to be returned to the user
        """
        chal: DynamicIsolatedChallenge = DynamicIsolatedChallenge.query.filter_by(
            id=challenge.id
        ).first()
        data = super().read(chal)
        data.update(
            {
                "yaml_id": chal.yaml_id,
            }
        )
        if conn := data.get("connection_info"):
            data["connection_info"] = json.loads(conn)

        return data

    @classmethod
    def update(cls, challenge: DynamicIsolatedChallenge, request: Request):
        user = get_current_user()
        logger.debug(
            f"isolated challenge update requested by {user.name!r} [id: {user.id}] for {challenge.name!r} [id: {challenge.id}]"
        )
        data = request.form.to_dict() or request.get_json()

        if "yaml_id" in data:
            challenge.yaml_id = data["yaml_id"]
            chal_spec = get_kube_spec_file(challenge.yaml_id, ISOLATED_KIND)
            try:
                kube.apply(chal_spec, field_manager=FIELD_MANAGER)
            except Exception as e:
                logger.exception(
                    f"kube apply failed for isolated chall {challenge.name!r} [id: {challenge.id}]"
                )

                if isinstance(e, ApiError):
                    raise ChallengeUpdateException(
                        f"kube apply failed: {e.status.message}"
                    ) from e
                else:
                    raise ChallengeUpdateException("kube apply failed") from e
            cache.delete_memoized(DynamicIsolatedChallenge.get_kube_name)
            cache.delete_memoized(DynamicIsolatedChallenge.get_lifetime)

        return super().update(challenge, request)

    @classmethod
    def delete(cls, challenge: DynamicIsolatedChallenge):
        user = get_current_user()
        logger.debug(
            f"isolated challenge delete requested by {user.name!r} [id: {user.id}] for {challenge.name!r} [id: {challenge.id}]"
        )

        chal_kube_name = challenge.get_kube_name()

        try:
            kube.get(get_isolated_chall_type(), chal_kube_name)
        except ApiError as e:
            if e.status.code == 404:
                logger.info(
                    f"Challenge {challenge.name!r} [id: {challenge.id}] not found on kube, ignoring delete"
                )
            else:
                logger.exception(
                    f"failed to get challenge {challenge.name!r} [id: {challenge.id}] from kube"
                )
                raise Exception("kube get failed") from e
        else:
            try:
                logger.debug(
                    f"deleting kube challenge spec for {challenge.name!r} [id: {challenge.id}]"
                )
                kube.delete(get_isolated_chall_type(), chal_kube_name)
                logger.info(
                    f"deleted kube spec for {challenge.name!r} [id: {challenge.id}]"
                )
            except Exception as e:
                logger.exception(
                    f"failed to delete kube spec for {challenge.name!r} [id: {challenge.id}]"
                )
                raise Exception("kube delete failed") from e

        super().delete(challenge)
        cache.delete_memoized(DynamicIsolatedChallenge.get_kube_name)
        cache.delete_memoized(DynamicIsolatedChallenge.get_lifetime)
        logger.info(
            f"deleted from CTFd challenge {challenge.name!r} [id: {challenge.id}]"
        )

    @classmethod
    def _internal_attempt(
        cls, challenge: DynamicIsolatedChallenge, request: Request
    ) -> tuple[bool, str]:
        data = request.form or request.get_json()
        submission = data["submission"].strip()
        if get_config("user_mode") == "teams":
            user_instances: list[Instances] = (
                Instances.query.filter_by(
                    challenge=challenge, team_id=get_current_user().team_id
                )
                .order_by(Instances.started_at.desc())
                .all()
            )
        else:
            user_instances: list[Instances] = (
                Instances.query.filter_by(
                    challenge=challenge, user_id=get_current_user().id
                )
                .order_by(Instances.started_at.desc())
                .all()
            )
        for instance in user_instances:
            if constant_time_compare(submission, instance.flag, False):
                return True, "Correct"
        return False, "Incorrect"

    @classmethod
    def attempt(
        cls, challenge: DynamicIsolatedChallenge, request: Request
    ) -> tuple[bool, str]:
        verdict, msg = cls._internal_attempt(challenge, request)
        if not verdict:
            verdict, msg = super().attempt(challenge, request)
            if verdict:
                user = get_current_user()
                logger.warning(
                    f"user {user.name!r} [id: {user.id}] submitted a flag catched by CTFd for challenge {challenge.name!r} [id: {challenge.id}]"
                )

        if verdict and challenge.destroy_on_flag:
            try:
                delete_instance(get_current_user_account_id(), challenge)
                msg = "Correct, instance destroyed"
            except UserFacingNotFound:
                pass
            except Exception:
                logger.exception("error while deleting instance after flag")

        return verdict, msg
