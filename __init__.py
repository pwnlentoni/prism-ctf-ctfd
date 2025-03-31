import sys
import subprocess

from CTFd.api import CTFd_API_v1
from CTFd.config import Config
from CTFd.plugins import register_admin_plugin_script, register_plugin_assets_directory
from CTFd.plugins.challenges import CHALLENGE_CLASSES
from CTFd.plugins.migrations import upgrade

from flask import Blueprint

try:
    import lightkube
except ModuleNotFoundError:
    # this atrocious thing is a workaround for CTFd only installing `requirements.txt` at docker build time
    # this allows us to just mount the plugin as a volume and have everything just work.
    subprocess.check_call([sys.executable, "-m", "pip", "install", "lightkube==0.17.1"])
    import lightkube  # noqa: F401

from lightkube.generic_resource import load_in_cluster_generic_resources

from . import utils
from .api import admin_namespace, user_namespace
from .k8s import kube
from .models import DynamicSharedValueChallenge, DynamicIsolatedValueChallenge

_CHALLENGE_TYPES = [
    DynamicSharedValueChallenge,
    DynamicIsolatedValueChallenge,
]


def load(app):
    logger = utils.get_logger(__name__)

    if not Config.SERVER_SENT_EVENTS:
        err = "server sent events are required for prism-ctf to work"
        logger.critical(err)
        raise Exception(err)

    app.config["RESTX_ERROR_404_HELP"] = False
    app.db.create_all()
    logger.info("initialized")

    register_plugin_assets_directory(
        app, base_path=utils.ASSETS_DIR, endpoint="plugins.prism_ctf.assets"
    )
    register_admin_plugin_script(f"{utils.ASSETS_DIR}/admin-utils.js")
    logger.info("Plugin assets directory registered.")

    upgrade()
    logger.info("Database migrations applied.")

    try:
        load_in_cluster_generic_resources(kube)
        logger.info(f"loaded kube config, running in namespace {kube.namespace}")
    except Exception as e:
        raise Exception("failed to setup kube resources") from e

    for chall_type in _CHALLENGE_TYPES:
        CHALLENGE_CLASSES[chall_type.id] = chall_type
    logger.info("challenge types registered")

    page_blueprint = Blueprint(
        "prism_ctf",
        __name__,
        template_folder="assets",  # needed to make include working
        static_folder="assets",
        url_prefix="/plugins/prism_ctf",
    )

    CTFd_API_v1.add_namespace(admin_namespace, path="/plugins/prism_ctf/admin")
    CTFd_API_v1.add_namespace(user_namespace, path="/plugins/prism_ctf")
    app.register_blueprint(page_blueprint)
    logger.info("api namespaces added")
    # TODO: read from config/env
    app.jinja_env.globals.update(prism_default_tcp_port="1337")
    app.jinja_env.globals.update(prism_default_http_port="443")
