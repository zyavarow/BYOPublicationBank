"""Meaning-based search vectors from a small language model that runs on this computer (no API key, nothing leaves the machine).

Model: BAAI/bge-small-en-v1.5 (MIT license; 12 layers, 384 numbers per passage; CLS pooling).
It is run here with plain NumPy rather than PyTorch, because Windows Smart App Control blocks PyTorch's python bridge (torch_python.dll) on
this laptop. The weights (safetensors) and the tokenizer (tokenizers) load fine. Results are checked in scripts/test_embeddings.py.
"""
import glob
import json
import math
import os
import threading

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_TOKENS = 512
_lock = threading.Lock()
_state = None


def _snapshot():
    """Folder of the downloaded model files (only the files needed were downloaded, so look in the cache directly)."""
    from huggingface_hub.constants import HF_HUB_CACHE
    found = sorted(glob.glob(os.path.join(HF_HUB_CACHE, "models--" + MODEL_NAME.replace("/", "--"), "snapshots", "*")))
    if not found:
        raise FileNotFoundError(f"The search model {MODEL_NAME} has not been downloaded yet.")
    return found[-1]


def _load():
    global _state
    with _lock:
        if _state is None:
            from safetensors.numpy import load_file
            from tokenizers import Tokenizer
            root = _snapshot()
            cfg = json.load(open(os.path.join(root, "config.json")))
            w = {k: v.astype(np.float32) for k, v in load_file(os.path.join(root, "model.safetensors")).items()}
            tok = Tokenizer.from_file(os.path.join(root, "tokenizer.json"))
            tok.enable_truncation(max_length=MAX_TOKENS)
            tok.enable_padding(pad_id=cfg["pad_token_id"], pad_token="[PAD]")
            _state = (tok, w, cfg)
    return _state


def _layer_norm(x, w, b, eps):
    mu = x.mean(-1, keepdims=True)
    var = ((x - mu) ** 2).mean(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + eps) * w + b


def _gelu(x):
    """GELU in its widely used tanh form (error below 0.001 against the exact form), built from np.exp, which is several times faster than
    the exact erf on this machine."""
    z = 1.5957691216057308 * (x + 0.044715 * x * x * x)          # 2 * sqrt(2/pi) * (x + 0.044715 x^3)
    return x / (1.0 + np.exp(np.clip(-z, -60.0, 60.0)))           # = 0.5 x (1 + tanh(z/2)); the clip only avoids a harmless overflow warning


def _forward(ids, mask, w, cfg):
    """BERT encoder; returns the final hidden state of the first ([CLS]) token for each row."""
    n, t = ids.shape
    h, heads = cfg["hidden_size"], cfg["num_attention_heads"]
    d = h // heads
    eps = cfg["layer_norm_eps"]
    x = w["embeddings.word_embeddings.weight"][ids] + w["embeddings.position_embeddings.weight"][:t][None] + w["embeddings.token_type_embeddings.weight"][0]
    x = _layer_norm(x, w["embeddings.LayerNorm.weight"], w["embeddings.LayerNorm.bias"], eps)
    bias = ((1.0 - mask[:, None, None, :].astype(np.float32)) * -1e9).astype(np.float32)           # padding positions cannot be attended to
    for i in range(cfg["num_hidden_layers"]):
        p = f"encoder.layer.{i}."
        def lin(name, v):
            return v @ w[p + name + ".weight"].T + w[p + name + ".bias"]
        q = (lin("attention.self.query", x) * (1.0 / math.sqrt(d))).reshape(n, t, heads, d).transpose(0, 2, 1, 3)
        k = lin("attention.self.key", x).reshape(n, t, heads, d).transpose(0, 2, 1, 3)
        v = lin("attention.self.value", x).reshape(n, t, heads, d).transpose(0, 2, 1, 3)
        s = (q @ k.transpose(0, 1, 3, 2)) + bias
        s = np.exp(s - s.max(-1, keepdims=True))
        a = (s / s.sum(-1, keepdims=True)) @ v
        a = a.transpose(0, 2, 1, 3).reshape(n, t, h)
        x = _layer_norm(x + lin("attention.output.dense", a), w[p + "attention.output.LayerNorm.weight"], w[p + "attention.output.LayerNorm.bias"], eps)
        f = lin("output.dense", _gelu(lin("intermediate.dense", x)))
        x = _layer_norm(x + f, w[p + "output.LayerNorm.weight"], w[p + "output.LayerNorm.bias"], eps)
    return x[:, 0]


def embed(texts, query=False, batch_size=16, on_progress=None):
    """Unit-length vectors, one row per text (float32 array, shape [n, 384]). Set query=True for search questions."""
    tok, w, cfg = _load()
    if query:
        texts = [QUERY_PREFIX + t for t in texts]
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))          # similar lengths together: much less wasted work
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    done = 0
    for i in range(0, len(order), batch_size):
        idx = order[i:i + batch_size]
        enc = tok.encode_batch([texts[j] for j in idx])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        vec = _forward(ids, mask, w, cfg)
        out[idx] = vec / np.linalg.norm(vec, axis=1, keepdims=True)
        done += len(idx)
        if on_progress:
            on_progress(done, len(texts))
    return out


def count_tokens(texts):
    tok = _load()[0]
    return [len(e.ids) - 2 for e in tok.encode_batch(list(texts), add_special_tokens=True)]      # without [CLS] and [SEP]
