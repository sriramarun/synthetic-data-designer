# Hugging Face Space image — Docker SDK.
#
# Spaces runs the container as uid 1000 with a read-only image layer, so the
# workspace has to live somewhere the app user owns, and the server has to bind
# 0.0.0.0:7860 rather than localhost.
#
# The `deep` extra is deliberately left out. CTGAN and TVAE pull in torch, which
# would multiply the image size and train unusably slowly on a free CPU Space.
# The interface already greys those two methods out and says why.

FROM python:3.12-slim

# Spaces expects uid 1000. Anything written by another user is unwritable at run
# time, and the failure surfaces as a permission error mid-upload.
RUN useradd --create-home --uid 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /home/user/app

# Dependencies before source, so editing a file does not reinstall scipy.
COPY --chown=user pyproject.toml README.md ./
COPY --chown=user src ./src
COPY --chown=user packs ./packs
RUN pip install --no-cache-dir --user '.[web]'

# Uploads and generated panels. Inside $HOME because that is what the app user
# owns; ephemeral, and wiped whenever the Space restarts.
RUN mkdir -p /home/user/workspace
ENV SDD_WORKSPACE=/home/user/workspace

# Ceilings, because this is one machine serving strangers. Unset for local use.
ENV SDD_SHARED=1 \
    SDD_MAX_RECORDS=50000 \
    SDD_MAX_PERIODS=60 \
    SDD_MAX_UPLOAD_MB=50

EXPOSE 7860
CMD ["sdd", "ui", "--host", "0.0.0.0", "--port", "7860"]
