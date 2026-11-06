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
its geometric product mark is temporary and the Hangry Labs logo is brand artwork.

Installed packages and the Python/Debian base image retain their upstream license
files. The downloaded model snapshot includes LICENSE.md. Kernel files are acquired
separately through the Hugging Face cache. Review complete transitive dependencies
and redistribution notices before publishing a container release or baking weights
into an image. Model licensing does not assign a license to this application's source.
