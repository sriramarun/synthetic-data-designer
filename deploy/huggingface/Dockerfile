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
# LICENSE is not optional here: pyproject declares `license = { file = ... }`,
# and hatchling refuses to generate metadata without it.
COPY --chown=user pyproject.toml README.md LICENSE ./
COPY --chown=user src ./src
COPY --chown=user packs ./packs
RUN pip install --no-cache-dir --user '.[web]'

# Uploads and generated panels. Inside $HOME because that is what the app user
# owns; ephemeral, and wiped whenever the Space restarts.
RUN mkdir -p /home/user/workspace
ENV SDD_WORKSPACE=/home/user/workspace

# Ceilings, because this is one machine serving strangers. Unset for local use.
#
# Sized against CPU Basic — 2 vCPU, 16 GB RAM, 50 GB of non-persistent disk —
# and against what a run actually costs, measured rather than guessed:
#
#     entities x periods      rows        peak RSS     workspace
#      5,000 x 12             58k          471 MB         32 MB
#     20,000 x 24            447k        1,437 MB        241 MB
#     50,000 x 24          1,117k        2,518 MB        603 MB
#
# So the 50,000 ceiling peaks around 2.5 GB. Two of those at once — the run pool
# is two workers — is 5 GB, comfortably inside 16 GB. Raising it is fine here and
# is not fine on a 512 MB box elsewhere, which is why the number lives in an
# environment variable rather than in the code.
ENV SDD_SHARED=1 \
    SDD_MAX_RECORDS=50000 \
    SDD_MAX_PERIODS=60 \
    SDD_MAX_UPLOAD_MB=50

EXPOSE 7860
CMD ["sdd", "ui", "--host", "0.0.0.0", "--port", "7860"]
