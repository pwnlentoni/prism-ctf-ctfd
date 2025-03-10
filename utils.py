import logging
import os
import sys

from CTFd.models import Users
from CTFd.utils import user

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
