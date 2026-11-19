import io
from collections.abc import Mapping
from typing import Any
from CTFd.models import (
    Files,
)
import CTFd.utils.uploads as ctfd_uploads
import yaml
from lightkube import codecs
from lightkube.codecs import AnyResource
from CTFd.utils.uploads.uploaders import FilesystemUploader
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename
from ..k8s import API_VERSION
from ..utils import get_logger

logger = get_logger(__name__)


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


def parse_inline_kube_spec(raw: Any, expected_kind: str) -> dict:
    """
    Validate an inline challenge spec, given either as a mapping (e.g. JSON sent by ctfcli
    from `extra.kube_spec`) or as a YAML string. Raises ValueError on bad input.
    """
    if isinstance(raw, str):
        try:
            docs = [d for d in yaml.safe_load_all(raw) if d is not None]
        except yaml.YAMLError as e:
            raise ValueError(f"invalid kube_spec yaml: {e}") from e
        if len(docs) != 1:
            raise ValueError(
                f"kube_spec doesn't contain a single object, found {len(docs)}"
            )
        raw = docs[0]

    if not isinstance(raw, Mapping):
        raise ValueError("kube_spec must be a single object")  # noqa: TRY004

    if raw.get("apiVersion") != API_VERSION:
        raise ValueError(
            f"bad object api version `{raw.get('apiVersion')}` != `{API_VERSION}`"
        )
    if raw.get("kind") != expected_kind:
        raise ValueError(f"bad object kind `{raw.get('kind')}` != `{expected_kind}`")

    metadata = raw.get("metadata")
    if not isinstance(metadata, Mapping) or not metadata.get("name"):
        raise ValueError("malformed kube_spec, missing metadata.name")

    return dict(raw)


def store_inline_kube_spec(data: dict, expected_kind: str, name: str) -> int | None:
    """
    If the challenge create/update payload carries an inline `kube_spec`, validate it,
    store it as a CTFd file (same kind of standalone file the admin UI uploads) and
    replace it in `data` with the resulting `yaml_id`. Raises ValueError on bad input.

    Returns the id of the uploaded file, or None if there was no inline spec.
    """
    if "kube_spec" not in data:
        return None

    raw = data.pop("kube_spec")
    if data.get("yaml_id") not in (None, ""):
        raise ValueError("specify either `kube_spec` or `yaml_id`, not both")

    spec = parse_inline_kube_spec(raw, expected_kind)
    content = yaml.safe_dump(spec, sort_keys=False).encode()
    filename = f"{secure_filename(name or '') or 'kube-spec'}.yaml"
    file_row = ctfd_uploads.upload_file(
        file=FileStorage(
            stream=io.BytesIO(content),
            filename=filename,
            content_type="application/yaml",
        ),
        type="standard",
    )
    data["yaml_id"] = file_row.id
    return file_row.id


def discard_inline_kube_spec(file_id: int | None) -> None:
    """
    Delete a file uploaded by `store_inline_kube_spec` when the create/update it was
    uploaded for fails. No-op for None, i.e. when the spec came in via `yaml_id`.
    """
    if file_id is None:
        return
    try:
        ctfd_uploads.delete_file(file_id)
    except Exception:
        logger.exception(f"failed to delete inline kube_spec file [id: {file_id}]")


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
