from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import validate


class RepositoryValidationTests(unittest.TestCase):
    def test_generated_virtual_environment_is_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("valid\n", encoding="utf-8")
            generated = root / ".venv"
            generated.mkdir()
            (generated / ".gitignore").write_text("generated", encoding="utf-8")

            with patch.object(validate, "ROOT", root):
                validate._validate_text_files()

    def test_repository_text_still_requires_final_newline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("invalid", encoding="utf-8")

            with patch.object(validate, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "missing final newline"):
                    validate._validate_text_files()


if __name__ == "__main__":
    unittest.main()
