import copy
import unittest
from unittest.mock import patch

import torch
from torch import nn

from flux2_klein_studio.lora import install_lora, remove_lora
from flux2_klein_studio import cli


class LoRATests(unittest.TestCase):
    def test_invalid_cli_strength_fails_before_model_loading(self):
        for value in ("nan", "inf", "-1", "3"):
            with (
                self.subTest(value=value),
                patch("sys.argv", ["studio", "generate", "--lora-strength", value]),
                self.assertRaisesRegex(SystemExit, "LoRA strength"),
            ):
                cli.main()

    def setUp(self):
        torch.manual_seed(17)
        self.model = nn.Sequential(nn.Linear(7, 5, dtype=torch.bfloat16))
        self.base = self.model[0]
        self.x = torch.randn(2, 3, 7, dtype=torch.bfloat16)
        self.down = torch.randn(2, 7)
        self.up = torch.randn(5, 2)
        self.weights = {
            "transformer.0.lora_A.weight": self.down,
            "transformer.0.lora_B.weight": self.up,
        }

    def test_matches_additive_reference_with_rank_stabilized_scaling(self):
        original = copy.deepcopy(self.base.state_dict())
        for stabilized in (False, True):
            with self.subTest(stabilized=stabilized):
                config = {
                    "transformer.r": 2,
                    "transformer.lora_alpha": 4,
                    "transformer.use_rslora": stabilized,
                }
                before = self.model(self.x)
                install_lora(self.model, self.weights, config, 0.75, require_cuda=False)
                denominator = 2**0.5 if stabilized else 2
                delta = (self.x.float() @ self.down.T @ self.up.T) * (
                    4 / denominator * 0.75
                )
                expected = (before + delta).to(before.dtype)
                self.assertTrue(torch.equal(self.model(self.x), expected))
                for key, value in self.base.state_dict().items():
                    self.assertTrue(torch.equal(value, original[key]))
                remove_lora(self.model)
                self.assertIs(self.model[0], self.base)
                self.assertTrue(torch.equal(self.model(self.x), before))

    def test_zero_strength_is_exact_base_result(self):
        before = self.model(self.x)
        install_lora(self.model, self.weights, {}, 0, require_cuda=False)
        self.assertTrue(torch.equal(self.model(self.x), before))

    def test_invalid_later_tensor_does_not_partially_install(self):
        weights = {**self.weights, "transformer.missing.lora_A.weight": self.down}
        with self.assertRaises(ValueError):
            install_lora(self.model, weights, {}, require_cuda=False)
        self.assertIs(self.model[0], self.base)

    def test_rejects_unsupported_variants_and_invalid_weights(self):
        for field in ("use_dora", "fan_in_fan_out", "use_qalora", "lora_bias"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                install_lora(
                    self.model, self.weights, {field: True}, require_cuda=False
                )
        for strength in (float("nan"), float("inf"), -1, 3):
            with self.subTest(strength=strength), self.assertRaises(ValueError):
                install_lora(self.model, self.weights, {}, strength, require_cuda=False)
        broken = {**self.weights, "transformer.0.lora_B.weight": torch.ones(6, 2)}
        with self.assertRaises(ValueError):
            install_lora(self.model, broken, {}, require_cuda=False)
        broken["transformer.0.lora_B.weight"] = self.up.clone().fill_(float("nan"))
        with self.assertRaises(ValueError):
            install_lora(self.model, broken, {}, require_cuda=False)
        with self.assertRaisesRegex(ValueError, "GPU resident"):
            install_lora(self.model, self.weights, {})

    def test_rejects_stacking_until_existing_adapter_removed(self):
        install_lora(self.model, self.weights, {}, require_cuda=False)
        with self.assertRaisesRegex(ValueError, "existing LoRA"):
            install_lora(self.model, self.weights, {}, require_cuda=False)
