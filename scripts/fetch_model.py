"""Download the small search model (BAAI/bge-small-en-v1.5, about 130 MB, MIT license) into the Hugging Face cache.
Run once when building the Docker image; only the three files the app uses are fetched.
"""
from huggingface_hub import snapshot_download

path = snapshot_download("BAAI/bge-small-en-v1.5", allow_patterns=["config.json", "model.safetensors", "tokenizer.json"])
print("model files in", path)
