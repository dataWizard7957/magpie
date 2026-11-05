"""
Muse Glimmer (open weights, 30B) on Modal, served by Ollama with an OpenAI-compatible API.

    modal run glimmer_modal.py::pull     # once: download weights into a Modal Volume (CPU only)
    modal deploy glimmer_modal.py        # prints the URL; set GLIMMER_URL=<url>/v1

Proxy auth is required, so strangers can't find the URL and burn your credits.
Create a token in the Modal dashboard: Settings -> Proxy Auth Tokens.
"""
import subprocess
import time

import modal

MODEL = "muse-glimmer:30b-q4_K_M"   # ~18 GB, fits a 24 GB GPU. Confirm the tag at ollama.com/library
MODELS_DIR = "/models"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("curl", "zstd", "ca-certificates")
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
    .env({
        "OLLAMA_HOST": "0.0.0.0:11434",
        "OLLAMA_MODELS": MODELS_DIR,       # weights live on the Volume, not re-downloaded
        "OLLAMA_CONTEXT_LENGTH": "8192",   # enough for a session turn + Engram facts
        "OLLAMA_KEEP_ALIVE": "-1",         # keep model in GPU while container is up
        "OLLAMA_NUM_PARALLEL": "1",        # one KV cache only, avoids OOM on 24 GB
    })
)

vol = modal.Volume.from_name("glimmer-weights", create_if_missing=True)
app = modal.App("magpie-glimmer", image=image)


def _start_ollama():
    subprocess.Popen(["ollama", "serve"])
    for _ in range(60):
        try:
            subprocess.run(["ollama", "list"], check=True, capture_output=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            time.sleep(1)
    raise RuntimeError("Ollama did not start")


@app.function(volumes={MODELS_DIR: vol}, timeout=60 * 60)
def pull():
    """Run ONCE: downloads weights into the Volume. CPU only, so it's cheap."""
    _start_ollama()
    subprocess.run(["ollama", "pull", MODEL], check=True)
    vol.commit()
    print(f"Saved {MODEL} to volume.")


@app.function(
    gpu="A10G",              # 24 GB. Try "L40S" for the demo recording if replies feel slow
    volumes={MODELS_DIR: vol},
    min_containers=0,        # never always-on
    max_containers=1,        # no accidental parallel copies
    scaledown_window=180,    # shut down 3 min after last call
    timeout=60 * 60,
)
@modal.concurrent(max_inputs=4)
@modal.web_server(port=11434, startup_timeout=600, requires_proxy_auth=True, label="glimmer")
def serve():
    _start_ollama()
