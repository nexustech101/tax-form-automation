# Third-party licenses

What this project runs on, under which license, and what that means when the
system is delivered to a client. The project's own code (`api/`, `core/`,
`scripts/`, `tests/`, `n8n/`) is not covered here; its owner sets its license.

Versions are the ones locked in `uv.lock`. Licenses are taken from each
package's own metadata and license files (regenerate the list with the
command at the end), and from the model cards for model weights.

## Summary

| | Risk for commercial delivery |
|---|---|
| Python packages | None found. All are permissive (MIT, BSD, Apache-2.0, PSF, MIT-CMU). Keep their notices when you redistribute them. |
| Docling layout model, RapidOCR models, Qwen3.5 | None found. Apache-2.0. |
| **tev1** (classifier) | **Unresolved.** The publisher's model card says the weights' release license "is being finalized"; the model is labelled experimental. See [tev1](#tev1). Set `TEV_MODEL=none` to run without it. |
| **n8n** | **Conditions apply.** Sustainable Use License: you may build and maintain workflows for a client on *their* n8n; you may not host n8n for clients so they can build workflows. See [n8n](#n8n). |

## Python packages (runtime)

Direct dependencies (`pyproject.toml`):

| Package | Version | License | Used for |
|---|---|---|---|
| docling | 2.143.0 | MIT | PDF / image parsing, layout, OCR orchestration |
| fastapi | 0.162.0 | MIT | HTTP API |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI server |
| pydantic-settings | 2.16.1 | MIT | `.env` / environment settings |
| python-multipart | 0.0.32 | Apache-2.0 | File uploads |
| ollama | 0.6.3 | MIT | Client for the local models |
| onnxruntime | 1.31.1 | MIT | Runs the OCR models |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 (bundles third-party code, see its `LICENSE-3RD-PARTY.txt`) | Checkbox and cell-rule detection |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Page images |
| pillow | 13.0.0 | MIT-CMU | Image decoding |
| pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0 (PDFium) | PDF text layer, page rendering |
| registers | 7.5.0 | MIT | API-key store |
| sqlmodel | 0.0.48 | MIT | Database layer under `registers` |
| typing-extensions | 4.16.0 | PSF-2.0 | Typing back-ports |

Notable packages installed with the above:

| Package | Version | License |
|---|---|---|
| docling-core / docling-parse / docling-ibm-models | 2.104.0 / 7.23.1 / 4.0.4 | MIT |
| rapidocr | 3.10.0 | Apache-2.0 (code); bundled OCR models: see below |
| torch / torchvision | 2.14.1 / 0.29.1 | Apache-2.0 AND BSD-2/3-Clause AND BSL-1.0 AND MIT (torch); BSD (torchvision) |
| transformers / huggingface-hub / safetensors | 5.21.0 / 1.33.0 / 0.8.0 | Apache-2.0 |
| scipy | 1.18.1 | BSD-3-Clause |
| starlette / httpx | 1.10.0 / 0.28.1 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| sqlalchemy | 2.1.6 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |

Development and test only (not needed to run the service): pytest 9.1.1 (MIT),
reportlab 5.1.0 (BSD), used to generate the synthetic sample forms.

## Model weights

| Model | Where it comes from | License | Notes |
|---|---|---|---|
| Docling layout model "heron" | Hugging Face `docling-project/docling-layout-heron`, downloaded on first start (or `main.py download-models`) | Apache-2.0 (model card) | |
| PP-OCRv6 small detection / recognition, `ch_ppocr_mobile_v2.0_cls` | Bundled in the `rapidocr` wheel | Apache-2.0; weights © Baidu / PaddleOCR rights holders | RapidOCR's `MODEL_LICENSES.md` lists the files and their SHA-256. Redistribution must keep the Apache-2.0 notices. The direction classifier is not used (`use_cls=False`). |
| Qwen3.5 4B (`qwen3.5:4b`) | Ollama library | Apache-2.0 (Alibaba Cloud / Qwen team) | Field mapping and the n8n Accountant Assistant |
| tev1 / tev1:0.8b | Ollama library (publisher: Together AI) | Not final, see below | Form classification |

### tev1

* Publisher: Together AI. Base model: Qwen3.5, fine-tuned to 4B and 0.8B; labelled "experimental".
* The Ollama library page states that the dataset builders and training scripts are MIT licensed, but gives no license for the weights.
* The Ollama package carries an MIT license file (copyright "open-jev contributors").
* A user asked the publisher on GitHub (`togethercomputer/tev1` issue #2) to clarify commercial self-hosting, citing a model card statement that "the fine-tuned weights' release license is being finalized". The issue was closed by its author on 2026-10-17 with no answer from the publisher.

Until the publisher states a license for the weights, treat commercial use of
tev1 as unconfirmed. The service runs without it: `TEV_MODEL=none` identifies
forms from their printed headings (`core.tev.HeadingClassifier`), which needs no
model and no GPU memory. On the bundled samples, headings identify every form
in every file, digital and scanned (see `README.md`, Verification).

## Runtime services

### Ollama

Runs the models locally. MIT license (github.com/ollama/ollama). Model weights
pulled through Ollama keep their own licenses (above).

### n8n

The optional intake workflow (`n8n/`) runs on n8n, which is "fair-code" under
the **Sustainable Use License**, not an open-source license. From n8n's
license FAQ:

* **Allowed:** charging a client for installing, configuring, building
  workflows and maintaining n8n on their behalf, on n8n the client runs.
* **Not allowed:** hosting n8n as a service so clients can build or change
  workflows, or selling a product whose value is substantially n8n itself.
* **Grey areas** (for example running workflows for clients on your own
  instance): n8n asks that you contact license@n8n.io; a commercial license is
  available from sales@n8n.io.

Deliver the workflow for the client's own n8n (self-hosted or their own n8n
Cloud account) and charge for the build and maintenance.

## When you deliver the system

* Installing with `uv sync` on the client's machine downloads each package
  from PyPI under its own license. If you hand over a bundled environment,
  installer or container instead, you are redistributing those packages:
  include this file and the license texts that ship inside each package
  (`.venv/Lib/site-packages/<package>-<version>.dist-info/licenses/`), and
  keep Apache-2.0 `NOTICE` files.
* Model weights are downloaded by the client's machine from Hugging Face and
  Ollama under the licenses above.
* Resolve the tev1 question (or ship with `TEV_MODEL=none`) before a paid delivery.

## Regenerate the package list

```bash
uv run python -m scripts.licenses
```

## Sources

* n8n license FAQ: https://docs.n8n.io/n8n-community-license/community-license/license-faq
* tev1 on Ollama: https://ollama.com/library/tev1 · license file of the Ollama package: https://ollama.com/library/tev1:0.8b/blobs/ff9fd7d3f054
* tev1 license question: https://github.com/togethercomputer/tev1/issues/2
* Qwen3.5-4B: https://llm-stats.com/models/qwen3.5-4b
* Docling layout model card: https://huggingface.co/docling-project/docling-layout-heron
* RapidOCR model licenses: `MODEL_LICENSES.md` in the `rapidocr` package
