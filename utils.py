import logging
import os
import sys
import functools
import datetime
import re

from flask import abort
from CTFd.models import Users, Teams
from CTFd.utils import user
from CTFd.utils import config

from CTFd.utils.user import is_admin  # noqa: F401, reexported

ASSETS_DIR = "/plugins/prism_ctf/assets"


def get_logger(name: str) -> logging.Logger:
    name = name.removeprefix("CTFd.plugins.")
    logger = logging.getLogger(name)
    level = logging.getLevelNamesMapping().get(
        os.getenv("LOG_LEVEL", "INFO"), logging.INFO
    )
    logger.setLevel(level)
    if not logger.hasHandlers():
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger


def get_current_user() -> Users:
    return user.get_current_user()  # type: ignore


def get_current_user_account_id() -> int:
    return user.get_current_user().account_id  # type: ignore


def get_current_team() -> Teams:
    return user.get_current_team()  # type: ignore


def require_joined_team(f):
    """
    Decorator to restrict an endpoint to users that have joined a team if ctfd is set to team mode
    :param f:
    :return:
    """

    @functools.wraps(f)
    def _require_joined_team(*args, **kwargs):
        if config.is_teams_mode() and get_current_team() is None:
            abort(403)
        return f(*args, **kwargs)

    return _require_joined_team


def parse_golang_duration(duration: str) -> datetime.timedelta:
    pattern = r"(\d+)([wdhmsu]+)"
    matches = re.findall(pattern, duration.replace(" ", ""))

    time_params = {"w": 0, "d": 0, "h": 0, "m": 0, "s": 0, "ms": 0, "us": 0}

    for value, unit in matches:
        if unit not in time_params:
            # shouldn't ever happen except if this function is borked because the durations are validated by kube
            raise ValueError("invalid duration format")
        time_params[unit] = int(value)

    return datetime.timedelta(
        weeks=time_params["w"],
        days=time_params["d"],
        hours=time_params["h"],
        minutes=time_params["m"],
        seconds=time_params["s"],
        milliseconds=time_params["ms"],
        microseconds=time_params["us"],
    )
