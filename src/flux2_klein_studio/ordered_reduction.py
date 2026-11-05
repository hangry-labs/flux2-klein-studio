"""Run-to-run ordered SVDQ low-rank accumulation on the selected CUDA profile.

Each row/rank tile is owned by one program. Tensor-core partial products are
accumulated in ascending 128-channel blocks, without cross-program atomics.
This applies the fixed-order principle described by NVIDIA CCCL's determinism
article; it does not invoke CUB or promise GPU-to-GPU reproducibility.

Packed layouts and the projection call convention follow the Apache-2.0
Nunchaku Lite and Hugging Face Diffusers implementations. See THIRD_PARTY.md.
"""

import torch
import triton
import triton.language as tl
from torch import nn


@triton.jit
def _ordered_down(
    inputs,
    weights,
    output,
    rows: tl.constexpr,
    channels: tl.constexpr,
    rank: tl.constexpr,
    block_m: tl.constexpr = 16,
    block_k: tl.constexpr = 128,
):
    m = tl.program_id(0) * block_m + tl.arange(0, block_m)
    r = tl.arange(0, 32)
    k = tl.arange(0, block_k)
    accumulator = tl.full((block_m, 32), 0, tl.float32)
    for block in range(tl.cdiv(channels, block_k)):
        column = block * block_k + k
        x = tl.load(
            inputs + m[:, None] * channels + column[None, :],
            (m[:, None] < rows) & (column[None, :] < channels),
            0,
        )
        w = tl.load(
            weights + column[:, None] * rank + r[None, :],
            (column[:, None] < channels) & (r[None, :] < rank),
            0,
        )
        accumulator = tl.dot(x, w, accumulator)

    # Native layout: [M/256, R/16, warp, row-tile, MMA-register, lane].
    lane = (m[:, None] % 8) * 4 + (r[None, :] % 8) // 2
    register = (
        ((r[None, :] % 16) // 8) * 4 + ((m[:, None] % 16) // 8) * 2 + r[None, :] % 2
    )
    offset = (
        (
            (
                ((m[:, None] // 256) * (rank // 16) + r[None, :] // 16) * 8
                + (m[:, None] % 256) // 32
            )
            * 2
            + (m[:, None] % 32) // 16
        )
        * 8
        + register
    ) * 32 + lane
    tl.store(output + offset, accumulator, m[:, None] < tl.cdiv(rows, 256) * 256)


def unpack_down(packed):
    """Convert checkpoint MMA fragments to a channel-by-rank matrix on GPU."""
    channels, rank = packed.shape
    return (
        packed.view(channels // 16, rank // 16, 8, 4, 2, 2, 2)
        .permute(0, 1, 4, 2, 5, 3, 6)
        .contiguous()
        .view(channels // 16, rank // 16, 16, 16)
        .permute(1, 2, 0, 3)
        .reshape(rank, channels)
        .t()
        .contiguous()
    )


def ordered_down(inputs, logical_weights):
    rows, channels = inputs.shape
    if inputs.device.type != "cuda" or logical_weights.device != inputs.device:
        raise ValueError(
            "Ordered SVDQ accumulation requires tensors on the same CUDA device"
        )
    if inputs.dtype != torch.bfloat16 or logical_weights.dtype != torch.bfloat16:
        raise ValueError(
            "The ordered profile currently requires BF16 inputs and correction weights"
        )
    if not inputs.is_contiguous() or not logical_weights.is_contiguous():
        raise ValueError("Ordered SVDQ inputs and weights must be contiguous")
    if logical_weights.shape != (channels, 32) or channels % 128:
        raise ValueError(
            "The ordered profile requires rank 32 and 128-aligned channels"
        )
    result = torch.empty(
        (triton.cdiv(rows, 256) * 256, 32), device=inputs.device, dtype=torch.float32
    )
    _ordered_down[(triton.cdiv(result.shape[0], 16),)](
        inputs,
        logical_weights,
        result,
        rows,
        channels,
        32,
        num_warps=4,
    )
    return result


class OrderedSVDQLinear(nn.Module):
    """Keep native NVFP4 quantization/GEMM and replace its low-rank side result.

    Wrapping a selected module avoids process-global patches to Diffusers or the
    downloaded kernel. The original checkpoint tensors remain registered under
    ``base``; the unpacked buffer is transient and never serialized.
    """

    def __init__(self, base):
        super().__init__()
        if (
            base.rank != 32
            or base.precision != "nvfp4"
            or base.proj_down.dtype != torch.bfloat16
        ):
            raise ValueError(
                "Ordered accumulation supports the NVFP4 rank-32 BF16 profile only"
            )
        self.base = base
        self.register_buffer(
            "_logical_down", unpack_down(base.proj_down), persistent=False
        )
        self._weight_version = base.proj_down._version
        self._weight_pointer = base.proj_down.data_ptr()
        self.train(base.training)

    def forward(self, inputs, output=None):
        # Projection interface follows Diffusers v0.42.1 quantizers/nunchaku/utils.py.
        from diffusers.quantizers.nunchaku.utils import _gemm_w4a4, ops

        base = self.base
        shape = inputs.shape
        x = inputs.reshape(-1, shape[-1]).contiguous()
        rows = x.shape[0]
        padded_rows = triton.cdiv(rows, 256) * 256
        if (
            self._weight_version != base.proj_down._version
            or self._weight_pointer != base.proj_down.data_ptr()
        ):
            self._logical_down = unpack_down(base.proj_down)
            self._weight_version = base.proj_down._version
            self._weight_pointer = base.proj_down.data_ptr()
        quantized = torch.empty(
            (padded_rows, base.in_features // 2), device=x.device, dtype=torch.uint8
        )
        scales = torch.empty(
            (base.in_features // 16, padded_rows),
            device=x.device,
            dtype=torch.float8_e4m3fn,
        )
        scratch = torch.empty(
            (padded_rows, base.rank), device=x.device, dtype=torch.float32
        )
        ops.quantize_w4a4_act_fuse_lora(
            x,
            quantized,
            scales,
            base.proj_down,
            scratch,
            base.smooth_factor,
            False,
            True,
        )
        # The native side result is discarded, never reused or cached by input.
        correction = ordered_down(x, self._logical_down)
        if output is None:
            output = torch.empty(
                (rows, base.out_features), dtype=x.dtype, device=x.device
            )
        _gemm_w4a4(
            quantized,
            base.qweight,
            output,
            scales,
            base.wscales,
            correction,
            base.proj_up,
            base.bias,
            base.act_unsigned,
            [1.0] * triton.cdiv(base.rank, 16),
            True,
            base.wtscale,
            base.wcscales,
        )
        return output.reshape(*shape[:-1], base.out_features)


def install_ordered_reduction(transformer):
    """Wrap only the pinned Diffusers SVDQ modules in this transformer."""
    from diffusers.quantizers.nunchaku.utils import SVDQW4A4Linear

    targets = [
        (parent, name, child)
        for parent in transformer.modules()
        if not isinstance(parent, OrderedSVDQLinear)
        for name, child in parent.named_children()
        if type(child) is SVDQW4A4Linear
    ]
    for parent, name, child in targets:
        setattr(parent, name, OrderedSVDQLinear(child))
    return len(targets)
