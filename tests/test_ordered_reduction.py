"""GPU numerical/reference tests; CPU Docker builds skip this class explicitly."""

import unittest

import torch

from flux2_klein_studio.ordered_reduction import ordered_down


def packed_positions(rows, rank, device):
    # Independent mapping from NVIDIA m16n8 accumulator registers to matrix cells.
    m = torch.arange(rows, device=device)[:, None]
    r = torch.arange(rank, device=device)[None, :]
    block = m // 256
    warp = m % 256 // 32
    tile = m % 32 // 16
    lane = (m % 8) * 4 + r % 8 // 2
    register = (r % 16 // 8) * 4 + (m % 16 // 8) * 2 + r % 2
    return (
        block * 256 * rank
        + (r // 16) * 4096
        + warp * 512
        + tile * 256
        + register * 32
        + lane
    )


@unittest.skipUnless(
    torch.cuda.is_available(), "Ordered reduction numerical tests require CUDA"
)
class OrderedReductionTests(unittest.TestCase):
    def test_matches_float64_reference_and_is_bit_repeatable(self):
        # Includes row padding boundaries and the largest channel count in this model.
        for rows, channels in (
            (1, 128),
            (17, 256),
            (255, 3072),
            (257, 384),
            (512, 6144),
            (4096, 12288),
        ):
            with self.subTest(rows=rows, channels=channels), torch.inference_mode():
                generator = torch.Generator("cuda").manual_seed(2468)
                inputs = (
                    torch.randn(
                        (rows, channels),
                        device="cuda",
                        dtype=torch.bfloat16,
                        generator=generator,
                    )
                    * 0.1
                )
                weights = (
                    torch.randn(
                        (channels, 32),
                        device="cuda",
                        dtype=torch.bfloat16,
                        generator=generator,
                    )
                    * 0.01
                )
                first = ordered_down(inputs, weights)
                second = ordered_down(inputs, weights)
                self.assertTrue(torch.equal(first, second))
                reference = inputs.double() @ weights.double()
                positions = packed_positions(rows, 32, "cuda")
                actual = first.flatten()[positions]
                torch.testing.assert_close(
                    actual.double(), reference, rtol=2e-4, atol=3e-5
                )
                if first.shape[0] > rows:
                    padded_positions = packed_positions(first.shape[0], 32, "cuda")[
                        rows:
                    ]
                    self.assertEqual(
                        torch.count_nonzero(first.flatten()[padded_positions]).item(), 0
                    )

    def test_refuses_unsupported_profile(self):
        inputs = torch.zeros((1, 128), device="cuda", dtype=torch.float16)
        weights = torch.zeros((128, 32), device="cuda", dtype=torch.float16)
        with self.assertRaisesRegex(ValueError, "BF16"):
            ordered_down(inputs, weights)
