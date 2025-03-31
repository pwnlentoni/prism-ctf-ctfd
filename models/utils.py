from typing import Any
from CTFd.models import (
    Files,
)
import CTFd.utils.uploads as ctfd_uploads
from lightkube import codecs
from lightkube.codecs import AnyResource
from CTFd.utils.uploads.uploaders import FilesystemUploader
from ..k8s import API_VERSION


uploader: FilesystemUploader = ctfd_uploads.get_uploader()


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if (v := {"true": True, "false": False}.get(value)) is not None:
            return v
    raise Exception(f"Bad boolean value {value!r}")


def get_kube_spec_file(file_id: Any, expected_kind: str) -> AnyResource:
    yaml_file: Files = Files.query.filter_by(id=int(file_id)).first()
    with uploader.open(yaml_file.location, mode="r") as f:
        objs = codecs.load_all_yaml(f.read())

    if len(objs) != 1:
        err = f"yaml file doesn't contain a single object, found {len(objs)}"
        raise Exception(err)

    challenge_def = objs[0]

    if challenge_def.metadata is None:
        raise Exception("malformed yaml definition, missing metadata")

    if challenge_def.apiVersion != API_VERSION:
        raise Exception(
            f"bad object api version `{challenge_def.apiVersion}` != `{API_VERSION}`"
        )

    if challenge_def.kind != expected_kind:
        raise Exception(f"bad object kind `{challenge_def.kind}` != `{expected_kind}`")
    return challenge_def


def constant_time_compare(a: str, b: str, case_insensitive: bool) -> bool:
    if len(a) != len(b):
        return False
    if case_insensitive:
        it = zip(a.lower(), b.lower())
    else:
        it = zip(a, b)
    result = 0
    for x, y in it:
        result |= ord(x) ^ ord(y)
    return result == 0
