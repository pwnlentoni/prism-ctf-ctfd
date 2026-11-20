# Plugin image meant to be mounted into a stock CTFd image as a Kubernetes image
# volume at /opt/CTFd/CTFd/plugins/prism_ctf: the image root is the plugin dir.

# Same Python minor as ctfd/ctfd:3.8.x (python:3.11-slim-bookworm). Built for the
# target platform since pyyaml ships native wheels.
FROM python:3.11-slim-bookworm AS vendor

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes \
        -r /tmp/requirements.txt --target /vendor

FROM scratch

COPY *.py config.json /
COPY models/ /models/
COPY assets/ /assets/
COPY --from=vendor /vendor /vendor
