from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
from typing import cast
import unittest
from unittest.mock import patch

from openrtl.adapters import (
    LocalDesignCatalog,
    PortableDesignCatalog,
    build_verified_package_candidate,
    load_verified_simulation_evidence,
    load_verified_simulation_profile,
)
from openrtl.cli import main
from openrtl.application import VerifiedPackageCandidate
from openrtl.domain import InterfaceRequirement, PortDirection


class VerifiedPackageCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        repository = Path(__file__).resolve().parents[1]
        for design in ("fifo", "skid_buffer"):
            profile = repository / f"examples/{design}/verified-profile.json"
            destination = self.root / f"examples/{design}/verified-profile.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(profile.read_bytes())
            payload = json.loads(profile.read_text(encoding="utf-8"))
            for item in payload["files"]:
                source = self.root / item["path"]
                source.parent.mkdir(parents=True, exist_ok=True)
                if source.suffix == ".sv":
                    source.write_text(f"module {payload['run']['top']};\nendmodule\n", encoding="utf-8")
                else:
                    source.write_text(f"fixture for {item['artifact_id']}\n", encoding="utf-8")
        self.fifo_manifest = self._write_evidence("fifo")
        self.skid_manifest = self._write_evidence("skid_buffer")

    def _write_evidence(self, design: str) -> Path:
        profile = json.loads((self.root / f"examples/{design}/verified-profile.json").read_text())
        output = self.root / f"build/{design}"
        output.mkdir(parents=True)
        top = profile["run"]["top"]
        symbols = tuple(chr(33 + index) for index, _ in enumerate(profile["focus_signals"]))
        definitions = "".join(
            f"$var wire 1 {symbol} {signal.rsplit('.', 1)[-1]} $end\n"
            for symbol, signal in zip(symbols, profile["focus_signals"], strict=True)
        )
        transitions = "".join(f"0{symbol}\n" for symbol in symbols) + "#10\n" + "".join(f"1{symbol}\n" for symbol in symbols)
        waveform = output / "waves.vcd"
        waveform.write_text(
            f"$timescale 1 ns $end\n$scope module {top} $end\n{definitions}$upscope $end\n$enddefinitions $end\n#0\n{transitions}",
            encoding="utf-8",
        )
        log = output / "run.log"
        log.write_text("PASS=1 FAIL=0\n", encoding="utf-8")
        results = output / "results.xml"
        testcase = profile["run"]["testcase"].rsplit(".", 1)[-1]
        results.write_text(f'<testsuites><testsuite><testcase name="{testcase}" /></testsuite></testsuites>\n', encoding="utf-8")
        rtl = self.root / next(item["path"] for item in profile["files"] if item["kind"] == "rtl")

        def record(path: Path) -> dict[str, object]:
            content = path.read_bytes()
            return {
                "path": path.relative_to(self.root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }

        if design == "fifo":
            payload: dict[str, object] = {
                "artifacts": {"log": record(log), "results": record(results), "waveform": record(waveform)},
                "requirements": profile["requirements"],
                "rtl": record(rtl),
                "run_id": "fifo.verilator.canary",
                "schema": profile["run"]["manifest_schema"],
                "seed": 1,
                "status": "passed",
                "testcase": profile["run"]["testcase"],
                "tool_profile_id": "verilator.cocotb",
                "top": top,
            }
        else:
            payload = {
                "artifacts": {
                    "before_focus": record(log), "before_log": record(log), "before_results": record(results),
                    "before_waveform": record(waveform), "comparison": record(log), "debug_session": record(log),
                    "repair_proposal": record(log), "repaired_focus": record(log), "repaired_log": record(log),
                    "repaired_results": record(results), "repaired_waveform": record(waveform),
                },
                "production_rtl": record(rtl),
                "requirements": profile["requirements"],
                "schema": profile["run"]["manifest_schema"],
                "status": "passed",
            }
        manifest = output / "evidence.json"
        manifest.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        return manifest

    def _candidate(self, design: str, manifest: Path, catalog: LocalDesignCatalog | None = None) -> VerifiedPackageCandidate:
        profile = load_verified_simulation_profile(self.root, Path(f"examples/{design}/verified-profile.json"))
        evidence = load_verified_simulation_evidence(self.root, profile, manifest)
        return build_verified_package_candidate(self.root, profile, evidence, catalog)

    def test_fifo_and_skid_profiles_produce_verified_catalog_candidates(self) -> None:
        catalog = LocalDesignCatalog((self.root / "build/catalog").resolve())
        fifo = self._candidate("fifo", self.fifo_manifest, catalog)
        skid = self._candidate("skid_buffer", self.skid_manifest, catalog)
        self.assertEqual(fifo.package.package_id, "community.sync.fifo")
        self.assertEqual(skid.package.package_id, "community.ready-valid.skid-buffer")
        self.assertTrue(fifo.package.publication_ready)
        self.assertTrue(skid.package.publication_ready)
        self.assertTrue(all(value.covered for value in fifo.coverage))
        self.assertTrue(all(value.covered for value in skid.coverage))
        self.assertEqual(catalog.package_ids(), ("community.ready-valid.skid-buffer", "community.sync.fifo"))
        manifest = catalog.read_manifest(skid.package.package_id, skid.package.version)
        provenance = cast(list[dict[str, str]], manifest["provenance"])
        self.assertEqual(
            [value["kind"] for value in provenance],
            ["simulation-profile", "simulation-evidence"],
        )

    def test_mixed_profile_source_and_waveform_states_fail_closed(self) -> None:
        fifo_profile = load_verified_simulation_profile(self.root, Path("examples/fifo/verified-profile.json"))
        with self.assertRaisesRegex(ValueError, "profile schema"):
            load_verified_simulation_evidence(self.root, fifo_profile, self.skid_manifest)

        payload = json.loads(self.skid_manifest.read_text(encoding="utf-8"))
        fifo_rtl = self.root / "examples/fifo/rtl/sync_fifo.sv"
        fifo_content = fifo_rtl.read_bytes()
        payload["production_rtl"] = {
            "path": "examples/fifo/rtl/sync_fifo.sv",
            "sha256": hashlib.sha256(fifo_content).hexdigest(),
            "size_bytes": len(fifo_content),
        }
        self.skid_manifest.write_text(json.dumps(payload), encoding="utf-8")
        skid_profile = load_verified_simulation_profile(self.root, Path("examples/skid_buffer/verified-profile.json"))
        with self.assertRaisesRegex(ValueError, "source does not match"):
            load_verified_simulation_evidence(self.root, skid_profile, self.skid_manifest)

    def test_cli_builds_and_stores_profile_candidate(self) -> None:
        output = io.StringIO()
        catalog = self.root / "build/cli-catalog"
        with patch("sys.stdout", output):
            status = main((
                "verified-package", "--root", str(self.root),
                "--profile", "examples/skid_buffer/verified-profile.json",
                "--manifest", self.skid_manifest.relative_to(self.root).as_posix(),
                "--catalog-root", str(catalog),
            ))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["profile_id"], "verified.skid-buffer.verilator")
        self.assertTrue(report["publication_ready"])
        self.assertTrue(Path(report["catalog_manifest"]).is_file())

    def test_portable_catalog_survives_source_removal_and_materializes(self) -> None:
        catalog_root = (self.root / "portable-catalog").resolve()
        catalog = PortableDesignCatalog(catalog_root)
        fifo = self._candidate("fifo", self.fifo_manifest)
        skid = self._candidate("skid_buffer", self.skid_manifest)
        fifo_receipt = catalog.store_candidate(self.root, fifo)
        skid_receipt = catalog.store_candidate(self.root, skid)

        shutil.rmtree(self.root / "examples")
        shutil.rmtree(self.root / "build")

        loaded_fifo = catalog.read_package(
            fifo_receipt.package_id,
            fifo_receipt.version,
            fifo_receipt.manifest_digest,
        )
        loaded_skid = catalog.read_package(
            skid_receipt.package_id,
            skid_receipt.version,
            skid_receipt.manifest_digest,
        )
        self.assertEqual(loaded_fifo.package.content_digest, fifo.package.content_digest)
        self.assertEqual(loaded_skid.package.content_digest, skid.package.content_digest)

        destination = (self.root / "consumer/fifo").resolve()
        report = catalog.materialize(
            fifo_receipt.package_id,
            fifo_receipt.version,
            fifo_receipt.manifest_digest,
            destination,
            (
                InterfaceRequirement("clk", PortDirection.INPUT, 1),
                InterfaceRequirement("wr_ready", PortDirection.OUTPUT, 1),
            ),
            (("width", 8),),
        )
        self.assertEqual(report.package_digest, fifo.package.content_digest)
        self.assertTrue((destination / "examples/fifo/rtl/sync_fifo.sv").is_file())
        self.assertTrue(Path(report.receipt_uri).is_file())

    def test_portable_catalog_rejects_digest_tamper_symlink_and_incompatibility(self) -> None:
        candidate = self._candidate("fifo", self.fifo_manifest)

        digest_catalog = PortableDesignCatalog((self.root / "digest-catalog").resolve())
        digest_receipt = digest_catalog.store_candidate(self.root, candidate)
        with self.assertRaisesRegex(ValueError, "manifest digest mismatch"):
            digest_catalog.read_package(candidate.package.package_id, "1.0.0", "sha256:" + "0" * 64)

        tamper_catalog = PortableDesignCatalog((self.root / "tamper-catalog").resolve())
        tamper_receipt = tamper_catalog.store_candidate(self.root, candidate)
        tampered = self.root / "tamper-catalog/community.sync.fifo/1.0.0/payload/package/examples/fifo/rtl/sync_fifo.sv"
        tampered.write_text("module changed; endmodule\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "payload digest or size mismatch"):
            tamper_catalog.read_package(candidate.package.package_id, "1.0.0", tamper_receipt.manifest_digest)

        missing_catalog = PortableDesignCatalog((self.root / "missing-catalog").resolve())
        missing_receipt = missing_catalog.store_candidate(self.root, candidate)
        missing = self.root / "missing-catalog/community.sync.fifo/1.0.0/payload/evidence/simulation-results.xml"
        missing.unlink()
        with self.assertRaisesRegex(ValueError, "is missing"):
            missing_catalog.read_package(candidate.package.package_id, "1.0.0", missing_receipt.manifest_digest)

        symlink_catalog = PortableDesignCatalog((self.root / "symlink-catalog").resolve())
        symlink_receipt = symlink_catalog.store_candidate(self.root, candidate)
        linked = self.root / "symlink-catalog/community.sync.fifo/1.0.0/payload/package/examples/fifo/spec.md"
        linked.unlink()
        linked.symlink_to(self.root / "examples/fifo/spec.md")
        with self.assertRaisesRegex(ValueError, "contains a symlink"):
            symlink_catalog.read_package(candidate.package.package_id, "1.0.0", symlink_receipt.manifest_digest)

        linked_root = self.root / "linked-root-catalog"
        linked_root.mkdir()
        (linked_root / "community.sync.fifo").symlink_to(self.root / "examples/fifo")
        with self.assertRaisesRegex(ValueError, "non-symlink directory"):
            PortableDesignCatalog(linked_root).store_candidate(self.root, candidate)

        incompatible_catalog = PortableDesignCatalog((self.root / "incompatible-catalog").resolve())
        incompatible_receipt = incompatible_catalog.store_candidate(self.root, candidate)
        destination = (self.root / "consumer/incompatible").resolve()
        with self.assertRaisesRegex(ValueError, "incompatible"):
            incompatible_catalog.materialize(
                candidate.package.package_id,
                "1.0.0",
                incompatible_receipt.manifest_digest,
                destination,
                (InterfaceRequirement("clk", PortDirection.OUTPUT, 1),),
            )
        self.assertFalse(destination.exists())

    def test_portable_package_cli_round_trip(self) -> None:
        catalog = (self.root / "cli-portable-catalog").resolve()
        output = io.StringIO()
        with patch("sys.stdout", output):
            status = main((
                "portable-package", "--root", str(self.root),
                "--profile", "examples/skid_buffer/verified-profile.json",
                "--manifest", self.skid_manifest.relative_to(self.root).as_posix(),
                "--catalog-root", str(catalog),
            ))
        self.assertEqual(status, 0)
        stored = json.loads(output.getvalue())
        destination = (self.root / "consumer/skid").resolve()
        output = io.StringIO()
        with patch("sys.stdout", output):
            status = main((
                "materialize-package", "--catalog-root", str(catalog),
                "--package-id", stored["package_id"], "--version", stored["version"],
                "--expected-manifest-digest", stored["manifest_digest"],
                "--destination", str(destination),
                "--require-port", "s_ready:output:1", "--parameter", "width=8",
            ))
        self.assertEqual(status, 0)
        materialized = json.loads(output.getvalue())
        self.assertEqual(materialized["package_digest"], stored["package_digest"])
        self.assertTrue((destination / "examples/skid_buffer/rtl/skid_buffer.sv").is_file())


if __name__ == "__main__":
    unittest.main()
