# Model capability review

Reviewed 2026-11-06 for the pinned Nunchaku Lite NVFP4 rank-32 checkpoint,
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
| Multiple reference images | Combine a subject from one image with a setting or visual style from another. | Upstream capability; the pipeline accepts a list of references. Establish a measured limit rather than borrowing hosted API limits. |
| Multiple outputs / prompt batches | Produce several alternatives in one workflow. | Sequential queue and n=1..16 outputs are implemented for HTTP/MCP generation and edits. GPU tensor batching and prompt-list requests remain unqualified. |
| LoRA adapters | Small additional weights that specialize a model's appearance or subject. | The pipeline has a LoRA loader, but our quantized linear replacements need compatibility work. Treat adapter inference as an investigation, not an available feature. Rank 32 in this checkpoint describes quantization correction, not a user adapter. |

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
Its call has no strength or mask_image input: editing is reference conditioning,
and precise masked inpainting/outpainting would need a separate workflow.
ControlNet, depth/pose controls, native transparent output, upscaling and video
are not established by this checkpoint and pipeline. A format conversion does
not add those model capabilities.

## Implemented milestone and next investigation

**Refine** now supports one reference plus an instruction, uploads, generated-result
and gallery reuse, before/after viewing and iterative refinement. HTTP
`/v1/images/edits` and MCP `refine_image` use the shared sequential GPU queue.
Reference hashes and preprocessing settings accompany recipes; gallery storage
remains opt-in. See the separate [editing measurements](../benchmarks/refine/README.md).

Refine uses 1024×1024 as its benchmark baseline. Next, investigate two references
at that baseline and measure memory, speed, seed controls and
subject/style adherence before raising the count. LoRA support should follow a
focused compatibility proof before adding adapter controls to the UI.
