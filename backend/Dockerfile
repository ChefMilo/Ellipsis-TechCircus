# syntax=docker/dockerfile:1
#
# Multi-stage build for the Dasfax backend (FastAPI + WS5/WS6/WS3 orchestrator).
#
# Dependency footprint check (done before writing this file, not assumed -- see
# docs/ws3/WS3-DEPLOY.md): `git diff` of backend/requirements.txt between pre- and
# post-"real-providers" main shows that PR added only two OPTIONAL, commented-out
# lines -- `openai` and `tavily-python`, both thin HTTP client SDKs -- neither
# installed by default, neither imported anywhere except lazily inside the one
# provider class that needs it (app/clients/openai_llm.py, openai_assessor.py,
# tavily_search.py). `grep -rniE "torch|transformers|tensorflow|onnx|huggingface"` over
# requirements.txt and the whole app/ tree returns nothing. This image installs the
# base (mock-path) requirements.txt as-is -- no model weights, no ML framework, no
# multi-GB risk. Measured, not estimated: see docs/ws3/WS3-DEPLOY.md for the actual `docker
# image ls` output from a real build.
#
# Pinned to Python 3.13.3 -- the exact interpreter this repo's 147-test backend suite
# has actually been run against in this environment (`python --version`), not just a
# floating "3.11+" from pyproject.toml's `requires-python`.

# --- deps stage --------------------------------------------------------------
FROM python:3.13.3-slim-bookworm AS deps

WORKDIR /build

# Manifest only, first -- this (the slow layer) is cached across every later change to
# application code that doesn't touch requirements.txt.
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- runtime stage -------------------------------------------------------------
FROM python:3.13.3-slim-bookworm AS runtime

# Fixed, non-root UID/GID -- not the default "nobody", so a bind-mounted volume (if one
# is ever added) gets predictable ownership instead of colliding with the host's own
# "nobody" mapping.
RUN groupadd --gid 10001 dasfax \
 && useradd --uid 10001 --gid dasfax --no-create-home --shell /usr/sbin/nologin dasfax

COPY --from=deps /install /usr/local

WORKDIR /app
COPY app ./app
COPY README.md ./

# App code only, after deps: an app-code change invalidates only this layer, not the
# pip install above it.
RUN chown -R dasfax:dasfax /app
USER dasfax

EXPOSE 8000

# Same "app.main:app" import path every dev instruction in this repo already uses
# (backend/README.md, DEMO.md) -- keep them in sync if this ever changes. No
# `--reload`: that's for local `uvicorn` dev only (backend/README.md), not this image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
