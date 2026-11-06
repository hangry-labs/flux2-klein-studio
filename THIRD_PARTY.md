# Third-party components

The application uses the following model and runtime components, which retain
their own licenses and notices.
This inventory does not assign one blanket license to the eventual container distribution.

| Component | Source / licensing reference |
| --- | --- |
| Quantized model | [Lite Infer model](https://huggingface.co/lite-infer/flux.2-klein-4b-nunchaku-lite-nvfp4-r32); [Apache-2.0 license file](https://huggingface.co/lite-infer/flux.2-klein-4b-nunchaku-lite-nvfp4-r32/blob/79dbbc2fdcfaf228863583d0cfabf56ff3e3c216/LICENSE.md) |
| Base model | [Black Forest Labs FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B); its LICENSE.md declares Apache-2.0 |
| Quantization tools | [diffuse-compressor](https://github.com/rootonchair/diffuse-compressor), building on Nunchaku, DeepCompressor and SVDQuant |
| CUDA kernels | [rootonchair/nunchaku-lite-kernels](https://huggingface.co/kernels/rootonchair/nunchaku-lite-kernels); retained upstream notices include Apache-2.0 and NVIDIA BSD-3-Clause source notices |
| Inference libraries | [Diffusers](https://github.com/huggingface/diffusers), [Transformers](https://github.com/huggingface/transformers), [bitsandbytes](https://github.com/bitsandbytes-foundation/bitsandbytes), [PyTorch](https://github.com/pytorch/pytorch) |
| Ordered accumulation | Application Triton kernel inspired by [NVIDIA's determinism article](https://developer.nvidia.com/blog/?p=113316). Projection call conventions and packed-layout conversion adapted from the Apache-2.0 [Diffusers quantizer](https://github.com/huggingface/diffusers/blob/v0.42.1/src/diffusers/quantizers/nunchaku/utils.py) and [Nunchaku Lite layout helpers](https://github.com/rootonchair/nunchaku-lite/blob/e663e48ccf3ec5264355e1872aeebef0284706c9/src/nunchaku_lite/lora/core/layout.py). The applicable upstream license is included in [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt). |

The browser server uses [FastAPI](https://github.com/fastapi/fastapi) and
[Uvicorn](https://github.com/encode/uvicorn), retaining their MIT and BSD-3-Clause
licenses respectively. The studio shell follows Hangry Labs design conventions;
its product artwork and Hangry Labs logo are brand assets supplied for the project.
The agent endpoint uses the MIT-licensed [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

Installed packages and the Python/Debian base image retain their upstream license
files. The downloaded model snapshot includes LICENSE.md. Kernel files are acquired
separately through the Hugging Face cache. Review complete transitive dependencies
and redistribution notices before publishing a container release or baking weights
into an image. Model licensing does not assign a license to this application's source.


## Container publication evidence

Full images bundle the pinned public model snapshot and matching Linux CUDA
kernel. Tiny images download these into the persistent cache when first needed.
The full image retains model LICENSE.md and kernel notices in that cache.
Both variants include `/app/licenses/runtime/inventory.json`, Python/Debian
license files and source-package identities. The workflow collects exact Debian
sources and reviewed wheel sources in a separate `*_sources` image before
publishing application images. Its manifest and checksums describe the evidence;
they are not automatic legal approval. CUDA/NVIDIA components retain their own
redistribution terms. Source collection does not relicense the combined image.

The torch 2.14 CUDA wheel's libgomp ELF build ID matches AlmaLinux
`gcc-8.5.0-28.el8_10.alma.1`; the debuglink identifies the same build. The wheel's
binary hash differs after wheel repair and is checked independently. NumPy's
bundled Fortran/quadmath hashes match the reviewed Faster Qwen source inventory.
`tools/runtime_sources.toml` binds the reviewed versions, native hashes and source
archives. Dependency changes require updating this review.

Publication planning, release promotion, runtime license/source collection and
cache merge logic are adapted from Faster Qwen3 TTS Studio; the original MIT
notice is retained in `licenses/faster-qwen-MIT.txt`.

## Supplemental notices for installed wheels

These exact installed wheels omit a license file from RECORD. The upstream
versioned license texts are retained under licenses/; this table fills only the
identified packaging gaps. Other versions require a fresh review.

| Package | Supplemental notice |
| --- | --- |
| kernels 0.18.1 | Hugging Face kernels, Apache-2.0; `licenses/kernels-Apache-2.0.txt`, from upstream v0.18.1/LICENSE. |
| tokenizers 0.23.3 | Hugging Face tokenizers, Apache-2.0; `licenses/tokenizers-Apache-2.0.txt`, from upstream v0.23.3/LICENSE. |
| cuda-toolkit 13.0.3.0 | NVIDIA metadata-only package: its RECORD contains only dist-info, no software payload. Installed NVIDIA component wheels supply their own license notices, collected separately. |
