import unittest
from types import SimpleNamespace

import torch

from flux2_klein_studio.inference import check_quantization, check_residency, check_step


class ResidencyTests(unittest.TestCase):
    def test_rejects_accelerate_cpu_offload_without_boolean_flag(self):
        from accelerate.hooks import CpuOffload

        module = torch.nn.Module()
        module._hf_hook = CpuOffload(execution_device="cuda")
        with self.assertRaisesRegex(RuntimeError, "offload hook"):
            check_residency(SimpleNamespace(components={"encoder": module}))

    def test_rejects_cpu_weights(self):
        pipe = SimpleNamespace(components={"encoder": torch.nn.Linear(2, 2)})
        with self.assertRaisesRegex(RuntimeError, "non-CUDA"):
            check_residency(pipe)

    def test_rejects_nested_offload_hooks(self):
        module = torch.nn.Module()
        module._hf_hook = SimpleNamespace(hooks=[SimpleNamespace(offload=True)])
        with self.assertRaisesRegex(RuntimeError, "offload hook"):
            check_residency(SimpleNamespace(components={"encoder": module}))

    def test_rejects_disk_device_map_without_parameters(self):
        module = torch.nn.Module()
        module.hf_device_map = {"layer": "disk"}
        with self.assertRaisesRegex(RuntimeError, "device map"):
            check_residency(SimpleNamespace(components={"encoder": module}))


class QuantizationTests(unittest.TestCase):
    def test_rejects_awq_encoder_for_nf4_profile(self):
        transformer = SimpleNamespace(
            config=SimpleNamespace(
                quantization_config={
                    "quant_method": "nunchaku_lite",
                    "svdq_w4a4": {"precision": "nvfp4", "rank": 32},
                }
            )
        )
        encoder = SimpleNamespace(
            config=SimpleNamespace(quantization_config={"quant_method": "awq"})
        )
        with self.assertRaisesRegex(RuntimeError, "NF4"):
            check_quantization(
                SimpleNamespace(transformer=transformer, text_encoder=encoder)
            )

    def test_rejects_wrong_rank_even_if_filename_says_r32(self):
        transformer = SimpleNamespace(
            config=SimpleNamespace(
                quantization_config={
                    "quant_method": "nunchaku_lite",
                    "svdq_w4a4": {"precision": "nvfp4", "rank": 128},
                }
            )
        )
        with self.assertRaisesRegex(RuntimeError, "rank-32"):
            check_quantization(SimpleNamespace(transformer=transformer))


class NumericalTests(unittest.TestCase):
    def test_rejects_nan_and_infinite_denoising(self):
        for value in (float("nan"), float("inf")):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(RuntimeError, "non-finite"),
            ):
                check_step(None, 0, None, {"latents": torch.tensor([value])})


if __name__ == "__main__":
    unittest.main()
