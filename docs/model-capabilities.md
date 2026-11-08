# Model capability review

Reviewed 2026-11-08 for the pinned Nunchaku Lite NVFP4 rank-32 checkpoint,
NF4 Qwen3 encoder and Diffusers 0.42.1. Text-to-image is qualified locally;
single-reference editing is now qualified at 1024×1024. Other additional model
modes below still need their own validation.

The [publisher's pinned model card](https://huggingface.co/lite-infer/flux.2-klein-4b-nunchaku-lite-nvfp4-r32/blob/79dbbc2fdcfaf228863583d0cfabf56ff3e3c216/README.md)
describes a quantized conversion of FLUX.2 Klein 4B and text-to-image calibration
at four steps and 1024×1024. Its examples do not qualify editing. The cached
model_index.json identifies Flux2KleinPipeline with is_distilled=true.

Black Forest Labs documents single-image editing and multi-reference generation
for this [4B model family](https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence).
This establishes upstream capability; our conversion, ordered reduction and
GPU memory limits still need their own validation.

| Candidate | What it enables | Evidence and work remaining |
| --- | --- | --- |
| Single-reference editing | Upload a picture or reuse a generated image, then describe a change: lighting, background, clothing or artistic style. | Implemented in Refine, HTTP edits and MCP refine_image. See the separate 1024×1024 editing benchmarks. |
| Multiple reference images | Combine subject/outfit/setting references, or several views of one person. | Up to four ordered references implemented in base-model Refine, HTTP edits and MCP. Fixed-1024 memory and synthetic-person quality trials are recorded in the [multi-reference reports](../benchmarks/multiref/README.md). Likeness is not guaranteed. |
| Multiple outputs / prompt batches | Produce several alternatives in one workflow. | Sequential queue and n=1..16 outputs are implemented for HTTP/MCP generation and edits. GPU tensor batching and prompt-list requests remain unqualified. |
| LoRA adapters | Small additional weights that specialize a model's appearance or subject. | Curated [Pixel Art, Koni Anime and NASA concept art adapters](lora.md) are available in Generate, HTTP/MCP generation and CLI, using pinned exports and adapter-specific browser/API prompt templates. Other adapters remain unqualified. Rank 32 describes quantization correction, not a user adapter. |

The [pinned pipeline implementation](https://github.com/huggingface/diffusers/blob/v0.42.1/src/diffusers/pipelines/flux2/pipeline_flux2_klein.py)
contains reference-image encoding and concatenation, batch arguments and a LoRA
loader mixin. Reference images add tokens to the denoising sequence, so existing
text-only latency and VRAM measurements do not establish editing capacity.
It resizes references above 1024×1024 total pixels and aligns their dimensions
to the VAE's required multiple; output size is independently configurable.

The [Nunchaku Lite linear implementations](https://github.com/huggingface/diffusers/blob/v0.42.1/src/diffusers/quantizers/nunchaku/utils.py)
are custom modules with packed weights and correction projections. A generic
LoRA loader alone does not demonstrate that adapters can target these modules.
The [quantizer](https://github.com/huggingface/diffusers/blob/v0.42.1/src/diffusers/quantizers/nunchaku/nunchaku_quantizer.py)
also marks training unsupported; training is outside this application's scope.

## Controls to avoid promising yet

The distilled pipeline disables classifier-free guidance, so a conventional
guidance or negative-prompt control would not provide the expected behavior.
Its call has no strength or mask_image input: editing is reference conditioning.
An experimental [Green Fill adapter](lora.md#green-fill--outpaint-experimental)
fills pure-green marks or borders through that image input; it does not add
pixel-preserving native mask/compositing semantics. An experimental
[Red Zoom adapter](lora.md#red-zoom--detail-focus-experimental) generates a
close-up of a red-outlined region and supports iterative result reuse. It can
invent details or ignore a selection; it is not faithful super-resolution.
ControlNet, depth/pose controls, native transparent output, upscaling and video
are not established by this checkpoint and pipeline. A format conversion does
not add those model capabilities.

## Implemented milestone and next investigation

**Refine** now supports one to four ordered references plus an instruction, uploads, generated-result
and gallery reuse, before/after viewing and iterative refinement. HTTP
`/v1/images/edits` and MCP `refine_image` use the shared sequential GPU queue.
Reference hashes and preprocessing settings accompany recipes; gallery storage
remains opt-in. See the separate [editing measurements](../benchmarks/refine/README.md).

Refine uses 1024×1024 as its benchmark baseline. Multi-reference trials keep that
output size and vary reference count and input detail. Real-photo identity quality
and broader prompt coverage remain future evaluation. The [curated LoRA adapters](lora.md)
provide initial compatibility evidence with UI/API selection; broader adapter
support needs its own validation.
