import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from local_llm_host import models


def touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return path


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = self.folder.name

    def tearDown(self):
        self.folder.cleanup()

    def test_a_folder_with_a_projector_is_one_model_that_sees_images(self):
        weights = touch(self.root, "gemma", "gemma-Q4_K_M.gguf")
        projector = touch(self.root, "gemma", "mmproj-F16.gguf")
        found = models.scan(self.root)
        self.assertEqual([(m.id, m.label, m.path, m.projector, m.vision) for m in found],
                         [("gemma", "gemma", weights, projector, True)])

    def test_a_loose_file_is_a_text_only_model_named_by_its_file(self):
        touch(self.root, "Qwen3-14B-Q4_K_M.gguf")
        touch(self.root, "notes.txt")
        found = models.scan(self.root)
        self.assertEqual([(m.id, m.vision) for m in found], [("Qwen3-14B-Q4_K_M", False)])

    def test_a_split_model_is_listed_once_by_its_first_part(self):
        first = touch(self.root, "big", "big-00001-of-00002.gguf")
        touch(self.root, "big", "big-00002-of-00002.gguf")
        found = models.scan(self.root)
        self.assertEqual([(m.id, m.path) for m in found], [("big", first)])

    def test_several_models_in_one_folder_are_each_named_by_their_file(self):
        touch(self.root, "family", "a-Q4.gguf")
        touch(self.root, "family", "a-Q8.gguf")
        self.assertEqual([m.id for m in models.scan(self.root)], ["family/a-Q4", "family/a-Q8"])

    def test_folders_without_a_model_are_left_out(self):
        touch(self.root, "mobilesam", "encoder.onnx")
        touch(self.root, "only-projector", "mmproj.gguf")
        self.assertEqual(models.scan(self.root), [])

    def test_a_missing_folder_has_no_models(self):
        self.assertEqual(models.scan(os.path.join(self.root, "missing")), [])

    def test_model_json_sets_only_what_it_may(self):
        touch(self.root, "qwen", "qwen.gguf")
        with open(os.path.join(self.root, "qwen", "model.json"), "w", encoding="utf-8") as f:
            json.dump({"label": "Qwen3 14B", "context": 8192, "cache_type": "q8_0", "gpu_layers": -1, "unknown": 1}, f)
        with redirect_stdout(StringIO()) as said:
            [model] = models.scan(self.root)
        self.assertEqual(model.label, "Qwen3 14B")
        self.assertEqual(model.options, {"label": "Qwen3 14B", "context": 8192, "cache_type": "q8_0"})
        self.assertIn("gpu_layers", said.getvalue())
        self.assertIn("unknown", said.getvalue())

    def test_an_unreadable_model_json_is_ignored(self):
        touch(self.root, "broken", "broken.gguf")
        with open(os.path.join(self.root, "broken", "model.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        with redirect_stdout(StringIO()):
            [model] = models.scan(self.root)
        self.assertEqual(model.options, {})

    def test_find(self):
        touch(self.root, "one.gguf")
        self.assertEqual(models.find(self.root, "one").id, "one")
        self.assertIsNone(models.find(self.root, "two"))


if __name__ == "__main__":
    unittest.main()
