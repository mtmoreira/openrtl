"""Synthetic Verilator JSON contracts; no compiler execution is claimed."""

from __future__ import annotations

import json
import unittest

from openrtl.adapters.verilator_hierarchy import parse_verilator_hierarchy


SOURCE = "sha256:" + "a" * 64
INPUT = "sha256:" + "b" * 64


def fixture() -> tuple[bytes, bytes]:
    tree = {"type": "NETLIST", "name": "", "addr": "(A)", "modulesp": [
        {"type": "MODULE", "name": "top", "origName": "top", "addr": "(T)",
         "loc": "e,1:1,1:10", "stmtsp": [
             {"type": "CELL", "name": "left", "origName": "left", "addr": "(L)",
              "modp": "(W)", "loc": "e,1:1,1:10"},
             {"type": "CELL", "name": "right", "origName": "right", "addr": "(R)",
              "modp": "(W)", "loc": "e,1:1,1:10"},
             {"type": "GENFOR", "name": "g", "stmtsp": [
                 {"type": "CELL", "name": "top.g[0].u", "origName": "u",
                  "addr": "(G)", "modp": "(W)", "loc": "e,1:1,1:10"}]}]},
        {"type": "MODULE", "name": "leaf__W8", "origName": "leaf", "addr": "(W)",
         "loc": "e,1:1,1:10", "stmtsp": []},
    ]}
    meta = {"files": {"e": {"filename": "/tmp/openrtl-input/rtl/top.sv",
                            "realpath": "/tmp/openrtl-input/rtl/top.sv"}}}
    return json.dumps(tree).encode(), json.dumps(meta).encode()


class VerilatorHierarchyTest(unittest.TestCase):
    def parse(self, tree: bytes, meta: bytes) -> dict:
        return parse_verilator_hierarchy(
            tree, meta, revision=7, source_root="/tmp/openrtl-input",
            sources={"rtl/top.sv": SOURCE}, top="top", input_digest=INPUT,
            tool_version="5.046", options=["--top-module", "top"])

    def test_repeated_specialized_generated_instances_and_source(self) -> None:
        result = self.parse(*fixture())
        self.assertEqual([row["id"] for row in result["instances"]],
                         ["top", "top.left", "top.right", "top.g[0].u"])
        self.assertEqual({row["specialization"] for row in result["instances"][1:]},
                         {"leaf__W8"})
        self.assertEqual(result["instances"][-1]["definition"],
                         {"revision": 7, "path": "rtl/top.sv", "line": 1, "digest": SOURCE})

    def test_missing_target_and_external_source_fail_closed(self) -> None:
        tree, meta = fixture()
        broken = json.loads(tree)
        broken["modulesp"][0]["stmtsp"][0]["modp"] = "(unknown)"
        with self.assertRaisesRegex(ValueError, "target_unavailable"):
            self.parse(json.dumps(broken).encode(), meta)
        external = json.loads(meta)
        external["files"]["e"]["filename"] = "/outside/rtl/top.sv"
        with self.assertRaisesRegex(ValueError, "source_unavailable"):
            self.parse(tree, json.dumps(external).encode())

    def test_version_and_size_are_bounded(self) -> None:
        tree, meta = fixture()
        with self.assertRaisesRegex(ValueError, "version_unsupported"):
            parse_verilator_hierarchy(tree, meta, revision=7, source_root="/tmp/openrtl-input",
                                      sources={"rtl/top.sv": SOURCE}, top="top", input_digest=INPUT,
                                      tool_version="5.052", options=[])
        with self.assertRaisesRegex(ValueError, "output_too_large"):
            self.parse(b"x" * (16 * 1024 * 1024 + 1), meta)


if __name__ == "__main__":
    unittest.main()
