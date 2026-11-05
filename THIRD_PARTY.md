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

Installed packages and the Python/Debian base image retain their upstream license
files. The downloaded model snapshot includes LICENSE.md. Kernel files are acquired
separately through the Hugging Face cache. Review complete transitive dependencies
and redistribution notices before publishing a container release or baking weights
into an image. Model licensing does not assign a license to this application's source.
