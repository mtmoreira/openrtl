from __future__ import annotations

import unittest

import openrtl


class PackageTest(unittest.TestCase):
    def test_version_is_exposed(self) -> None:
        self.assertEqual(openrtl.__version__, "0.3.0")


if __name__ == "__main__":
    unittest.main()
