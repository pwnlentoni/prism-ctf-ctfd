from CTFd.plugins.dynamic_challenges import DynamicChallenge, DynamicValueChallenge

from CTFd.models import (
    Files,
    db,
)
from CTFd.exceptions.challenges import (
    ChallengeCreateException,
    ChallengeUpdateException,
)

import CTFd.utils.uploads as ctfd_uploads
from CTFd.utils.uploads.uploaders import FilesystemUploader

from flask import Request
from lightkube import codecs
from lightkube import ApiError

import json

from .blueprint import blueprint
from ..utils import get_logger, get_current_user, ASSETS_DIR
from ..k8s import kube, FIELD_MANAGER, API_VERSION, SHARED_KIND, get_shared_chall_type

logger = get_logger(__name__)
uploader: FilesystemUploader = ctfd_uploads.get_uploader()


class DynamicSharedChallenge(DynamicChallenge):
    __mapper_args__ = {"polymorphic_identity": "prism_shared"}
    id = db.Column(
        db.Integer,
        db.ForeignKey("dynamic_challenge.id", ondelete="CASCADE"),
        primary_key=True,
    )

    yaml_id = db.Column(db.Integer, db.ForeignKey("files.id"))

    def __init__(self, *args, **kwargs):
        super(DynamicSharedChallenge, self).__init__(**kwargs)

    def __str__(self):
        return f"DynamicSharedChallenge(id={self.id})"


class DynamicSharedValueChallenge(DynamicValueChallenge):
    id = "prism_shared"
    name = "prism_shared"
    templates = {  # Nunjucks templates used for each aspect of challenge editing & viewing
        "create": f"{ASSETS_DIR}/shared-create.html",  # nunjucks on admin create
        "update": f"{ASSETS_DIR}/shared-update.html",  # nunjucks on admin update
        "view": f"{ASSETS_DIR}/shared-view.html",  # nunjucks on admin preview, also used as a jinja template by user frontend, thanks ctfd
    }

    scripts = {  # Scripts that are loaded when a template is loaded
        "create": f"{ASSETS_DIR}/shared-create.js",
        "update": f"{ASSETS_DIR}/shared-update.js",
        "view": f"{ASSETS_DIR}/shared-view.js",
    }
    # Route at which files are accessible. This must be registered using register_plugin_assets_directory()
    route = f"{ASSETS_DIR}/"
    # Blueprint used to access the static_folder directory.
    blueprint = blueprint
    challenge_model = DynamicSharedChallenge

    @staticmethod
    def get_kube_spec(chal: DynamicSharedChallenge):
        yaml_file: Files = Files.query.filter_by(id=int(chal.yaml_id)).first()
        with uploader.open(yaml_file.location, mode="r") as f:
            objs = codecs.load_all_yaml(f.read())

        if len(objs) != 1:
            err = f"yaml file doesn't contain a single object, found {len(objs)}"
            logger.error(err)
            raise Exception(err)

        challenge_def = objs[0]

        if challenge_def.metadata is None:
            raise Exception("malformed yaml definition, missing metadata")

        if challenge_def.apiVersion != API_VERSION:
            raise Exception(
                f"bad object api version `{challenge_def.apiVersion}` != `{API_VERSION}`"
            )

        if challenge_def.kind != SHARED_KIND:
            raise Exception(
                f"bad object kind `{challenge_def.kind}` != `{SHARED_KIND}`"
            )

        if challenge_def["metadata"].get("labels") is None:
            challenge_def["metadata"]["labels"] = {}

        assert challenge_def.metadata.labels
        challenge_def.metadata.labels["prism-ctf.pwnlentoni.team/ctfd-id"] = str(
            chal.id
        )
        return challenge_def

    @classmethod
    def create(cls, request: Request):
        user = get_current_user()
        logger.debug(f"shared challenge created by {user.name!r} [id: {user.id}]")
        data = request.form.to_dict() or request.get_json()

        if not isinstance(data, dict):
            logger.error(f"invalid creation request data: {data!r}")
            return

        if "yaml_id" not in data.keys():
            logger.error("missing required `yaml_id`")
            raise ChallengeCreateException("missing required `yaml_id`")

        challenge = cls.challenge_model(**data)
        db.session.add(challenge)
        db.session.commit()

        logger.info(f"created CTFd challenge {challenge.name!r} [id: {challenge.id}]")

        try:
            chal_spec = cls.get_kube_spec(challenge)
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
            raise ChallengeCreateException("kube apply failed") from e

        return challenge

    @classmethod
    def read(cls, challenge: DynamicSharedChallenge):
        """
        This method is in used to access the data of a challenge in a format processable by the front end.

        :param challenge:
        :return: Challenge object, data dictionary to be returned to the user
        """
        chal: DynamicSharedChallenge = DynamicSharedChallenge.query.filter_by(
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
    def update(cls, challenge: DynamicSharedChallenge, request: Request):
        user = get_current_user()
        logger.debug(
            f"shared challenge update requested by {user.name!r} [id: {user.id}] for {challenge.name!r} [id: {challenge.id}]"
        )
        data = request.form.to_dict() or request.get_json()

        if "yaml_id" in data:
            challenge.yaml_id = data["yaml_id"]
            chal_spec = cls.get_kube_spec(challenge)
            try:
                kube.apply(chal_spec, field_manager=FIELD_MANAGER)
            except Exception as e:
                logger.exception(
                    f"kube apply failed for shared chall {challenge.name!r} [id: {challenge.id}]"
                )
                raise ChallengeUpdateException("kube apply failed") from e

        return super().update(challenge, request)

    @classmethod
    def delete(cls, challenge: DynamicSharedChallenge):
        user = get_current_user()
        logger.debug(
            f"shared challenge delete requested by {user.name!r} [id: {user.id}] for {challenge.name!r} [id: {challenge.id}]"
        )

        chal_spec = cls.get_kube_spec(challenge)

        try:
            kube.get(get_shared_chall_type(), chal_spec.metadata.name)  # type: ignore
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
                kube.delete(get_shared_chall_type(), chal_spec.metadata.name)  # type: ignore
                logger.info(
                    f"deleted kube spec for {challenge.name!r} [id: {challenge.id}]"
                )
            except Exception as e:
                logger.exception(
                    f"failed to delete kube spec for {challenge.name!r} [id: {challenge.id}]"
                )
                raise Exception("kube delete failed") from e

        super().delete(challenge)
        logger.info(
            f"deleted from CTFd challenge {challenge.name!r} [id: {challenge.id}]"
        )
