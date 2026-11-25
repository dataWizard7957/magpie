# Magpie web app (API + web UI) for Render or any Docker host.
FROM python:3.11-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# `modal` is only needed to deploy Muse Glimmer (glimmer_modal.py), not to run the app.
COPY --chown=user requirements.txt .
RUN grep -v '^modal' requirements.txt > runtime-requirements.txt \
 && pip install --no-cache-dir --user -r runtime-requirements.txt

COPY --chown=user . .

# Render (and most hosts) set $PORT; default to 8000 elsewhere.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
