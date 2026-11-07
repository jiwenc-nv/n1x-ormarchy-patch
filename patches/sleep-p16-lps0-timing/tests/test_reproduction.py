"""Exercise source-integrity guards without downloading or building a kernel."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sleep_builder', ROOT / 'build.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ReproductionTests(unittest.TestCase):
    def test_archived_input_integrity(self):
        BUILDER.reproduction_inputs()

    def test_missing_changed_and_symlinked_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'provenance').mkdir()
            tree = root / 'nvidia'
            tree.mkdir()
            original = b'original source\n'
            (root / 'provenance/nvidia-source-checksums.json').write_text(json.dumps(
                {'driver.c': hashlib.sha256(original).hexdigest()}))
            with patch.object(BUILDER, 'ROOT', root):
                with self.assertRaises(RuntimeError):
                    BUILDER.verify_nvidia_sources(tree)
                source = tree / 'driver.c'
                source.write_bytes(original)
                BUILDER.verify_nvidia_sources(tree, exact=True)
                source.write_bytes(b'changed\n')
                with self.assertRaises(RuntimeError):
                    BUILDER.verify_nvidia_sources(tree)
                source.unlink()
                (root / 'outside.c').write_bytes(original)
                source.symlink_to(root / 'outside.c')
                with self.assertRaises(RuntimeError):
                    BUILDER.verify_nvidia_sources(tree)

    def test_build_outputs_allowed_only_after_exact_source_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'provenance').mkdir()
            (root / 'provenance/nvidia-source-checksums.json').write_text('{}')
            tree = root / 'nvidia'
            tree.mkdir()
            (tree / 'module.ko').write_bytes(b'compiled output')
            with patch.object(BUILDER, 'ROOT', root):
                BUILDER.verify_nvidia_sources(tree)
                with self.assertRaises(RuntimeError):
                    BUILDER.verify_nvidia_sources(tree, exact=True)


if __name__ == '__main__':
    unittest.main()
