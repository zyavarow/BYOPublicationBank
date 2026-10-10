"""Checks that the NumPy version of the embedding model behaves like a correct model (no PyTorch available to compare with).
  .venv/Scripts/python.exe scripts/test_embeddings.py
Tests: unit length; the same text gives the same vector alone or inside a padded batch (proves the padding mask works); related sentences score
higher than unrelated ones; a question retrieves the right passage; speed.
"""
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank import embeddings as E  # noqa: E402

DOCS = [
    "Deficiency of example enzyme 1 (EXD) causes vasculitis, early-onset stroke and fever.",
    "Mice lacking CD73 are more susceptible to pneumococcal pneumonia.",
    "Anti-TNF therapy such as etanercept reduced strokes in EXD patients.",
    "The cryo-EM structure of the gene3a hexamer reveals an ATPase-competent state.",
    "Hematopoietic stem cell transplantation corrected the bone marrow failure in patients with GENE1 deficiency.",
]
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, detail)


v = E.embed(DOCS)
check("unit length", np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-4))
alone = E.embed([DOCS[0]])[0]
padded = E.embed([DOCS[0], "A much longer sentence " * 40, DOCS[3]])
sims = {i: float(np.dot(alone, padded[i])) for i in range(3)}
i0 = int(np.argmax([float(np.dot(alone, p)) for p in padded]))
check("same text alone and in a padded batch gives the same vector", float(np.dot(alone, padded[0])) > 0.9995 or float(np.dot(alone, padded[i0])) > 0.9995,
      f"(cosine {max(sims.values()):.5f})")
q = E.embed(["Which treatment prevents stroke in EXD?"], query=True)[0]
scores = v @ q
check("treatment question finds the anti-TNF passage first", int(np.argmax(scores)) == 2, str([round(float(s), 3) for s in scores]))
q2 = E.embed(["What does the structure of gene3a look like?"], query=True)[0]
check("structure question finds the cryo-EM passage first", int(np.argmax(v @ q2)) == 3)
q3 = E.embed(["Can a bone marrow transplant cure GENE1 deficiency?"], query=True)[0]
check("transplant question finds the transplant passage first", int(np.argmax(v @ q3)) == 4)
check("related EXD sentences closer to each other than to unrelated ones", float(v[0] @ v[2]) > float(v[0] @ v[1]) and float(v[0] @ v[2]) > float(v[0] @ v[3]))
texts = ["The patient presented with fever and livedo reticularis and was treated with etanercept. " * 22] * 48
t = time.time()
E.embed(texts)
dt = time.time() - t
print(f"speed: 48 passages of ~{E.count_tokens(texts[:1])[0]} tokens in {dt:.1f}s = {48 / dt:.1f} passages/s")
print("ALL PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
