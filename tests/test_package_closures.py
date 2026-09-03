from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters import DependencyClosedCatalog, PortableDesignCatalog
from openrtl.application import PackageBundlePin, dependency_install_order
from openrtl.cli import main
from openrtl.domain import (
    DesignPackage,
    InterfacePort,
    InterfaceRequirement,
    PackageDependency,
    PackageFile,
    Parameter,
    PortDirection,
    TrustLevel,
)


class PackageClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        self.source = self.root / "producer"
        self.source.mkdir()
        self.catalog_root = self.root / "catalog"
        self.portable = PortableDesignCatalog(self.catalog_root)
        self.leaf = self._package("test.leaf", "leaf")
        self.root_package = self._package(
            "test.root",
            "root",
            (PackageDependency(self.leaf.package_id, self.leaf.version, self.leaf.content_digest),),
        )
        self.leaf_receipt = self.portable.store_package(
            self.source, self.leaf, self._supporting_files("leaf")
        )
        self.root_receipt = self.portable.store_package(
            self.source, self.root_package, self._supporting_files("root")
        )
        self.pins = (
            PackageBundlePin("test.root", "1.0.0", self.root_receipt.manifest_digest),
            PackageBundlePin("test.leaf", "1.0.0", self.leaf_receipt.manifest_digest),
        )

    def _write(self, relative: str, content: str) -> PackageFile:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        encoded = path.read_bytes()
        kind = "rtl" if relative.endswith(".sv") else "support"
        return PackageFile(relative, kind, f"sha256:{hashlib.sha256(encoded).hexdigest()}")

    def _package(
        self,
        package_id: str,
        label: str,
        dependencies: tuple[PackageDependency, ...] = (),
    ) -> DesignPackage:
        source = self._write(f"sources/{label}.sv", f"module {label}; endmodule\n")
        return DesignPackage(
            package_id,
            "1.0.0",
            f"design.{label}",
            "Apache-2.0",
            TrustLevel.SIMULATION_VERIFIED,
            (
                InterfacePort("clk", PortDirection.INPUT, 1),
                InterfacePort("ready", PortDirection.OUTPUT, 1),
            ),
            (Parameter("width", 8, 1, 64),),
            (source,),
            (f"evidence.{label}",),
            dependencies,
        )

    def _supporting_files(self, label: str) -> tuple[PackageFile, ...]:
        values = (
            ("simulation-profile", "json"),
            ("simulation-evidence", "json"),
            ("simulation-log", "log"),
            ("simulation-results", "xml"),
            ("simulation-waveform", "vcd"),
        )
        return tuple(
            PackageFile(
                value.path,
                kind,
                value.content_digest,
            )
            for kind, suffix in values
            for value in (self._write(f"support/{label}/{kind}.{suffix}", f"{label}:{kind}\n"),)
        )

    def test_dependency_closure_survives_producer_removal_and_materializes(self) -> None:
        catalog = DependencyClosedCatalog(self.catalog_root)
        lock = catalog.resolve("test.root", "1.0.0", self.pins)
        self.assertEqual(lock.install_order, ("test.leaf", "test.root"))
        lock_path = self.root / "closure.lock.json"
        lock_digest = catalog.write_lock(lock, lock_path)
        shutil.rmtree(self.source)

        destination = self.root / "workspace"
        report = catalog.materialize(
            lock_path,
            lock_digest,
            destination,
            (InterfaceRequirement("ready", PortDirection.OUTPUT, 1),),
            (("width", 16),),
        )
        self.assertEqual(report.install_order, ("test.leaf", "test.root"))
        self.assertTrue((destination / "packages/test.leaf/sources/leaf.sv").is_file())
        self.assertTrue((destination / "packages/test.root/sources/root.sv").is_file())
        self.assertTrue((destination / "openrtl-package-closure.json").is_file())
        self.assertEqual(len(report.materialization_receipts), 2)

    def test_cli_locks_and_materializes_exact_closure(self) -> None:
        lock_path = self.root / "cli-closure.lock.json"
        output = io.StringIO()
        with patch("sys.stdout", output):
            result = main((
                "lock-package-closure",
                "--catalog-root", str(self.catalog_root),
                "--root-package-id", "test.root",
                "--root-version", "1.0.0",
                "--bundle-pin", f"test.root@1.0.0={self.root_receipt.manifest_digest}",
                "--bundle-pin", f"test.leaf@1.0.0={self.leaf_receipt.manifest_digest}",
                "--output", str(lock_path),
            ))
        locked = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(locked["install_order"], ["test.leaf", "test.root"])

        output = io.StringIO()
        destination = self.root / "cli-workspace"
        with patch("sys.stdout", output):
            result = main((
                "materialize-package-closure",
                "--catalog-root", str(self.catalog_root),
                "--lock", str(lock_path),
                "--expected-lock-digest", locked["lock_digest"],
                "--destination", str(destination),
                "--require-port", "ready:output:1",
                "--parameter", "width=8",
            ))
        materialized = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(materialized["install_order"], ["test.leaf", "test.root"])
        self.assertTrue(Path(materialized["receipt"]).is_file())

    def test_missing_unused_conflicting_and_digest_drift_fail_closed(self) -> None:
        catalog = DependencyClosedCatalog(self.catalog_root)
        with self.assertRaisesRegex(ValueError, "dependency is missing"):
            catalog.resolve("test.root", "1.0.0", (self.pins[0],))
        with self.assertRaisesRegex(ValueError, "unique by package_id"):
            catalog.resolve("test.root", "1.0.0", (self.pins[0], self.pins[0]))

        other = self._package("test.other", "other")
        other_receipt = self.portable.store_package(
            self.source, other, self._supporting_files("other")
        )
        with self.assertRaisesRegex(ValueError, "unused pins"):
            catalog.resolve(
                "test.root",
                "1.0.0",
                self.pins + (PackageBundlePin("test.other", "1.0.0", other_receipt.manifest_digest),),
            )

        drift_catalog_root = self.root / "drift-catalog"
        drift_catalog = PortableDesignCatalog(drift_catalog_root)
        drift_root = self._package(
            "drift.root",
            "drift_root",
            (PackageDependency("test.leaf", "1.0.0", "sha256:" + "0" * 64),),
        )
        drift_leaf_receipt = drift_catalog.store_package(
            self.source, self.leaf, self._supporting_files("drift_leaf")
        )
        drift_root_receipt = drift_catalog.store_package(
            self.source, drift_root, self._supporting_files("drift_root")
        )
        with self.assertRaisesRegex(ValueError, "dependency digest mismatch"):
            DependencyClosedCatalog(drift_catalog_root).resolve(
                "drift.root",
                "1.0.0",
                (
                    PackageBundlePin("drift.root", "1.0.0", drift_root_receipt.manifest_digest),
                    PackageBundlePin("test.leaf", "1.0.0", drift_leaf_receipt.manifest_digest),
                ),
            )

    def test_cycle_lock_tamper_and_incompatibility_leave_no_workspace(self) -> None:
        digest = "sha256:" + "0" * 64
        cycle_a = self._package(
            "cycle.a", "cycle_a", (PackageDependency("cycle.b", "1.0.0", digest),)
        )
        cycle_b = self._package(
            "cycle.b", "cycle_b", (PackageDependency("cycle.a", "1.0.0", digest),)
        )
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            dependency_install_order("cycle.a", "1.0.0", (cycle_a, cycle_b))
        with self.assertRaisesRegex(ValueError, "conflicting pins"):
            dependency_install_order("test.leaf", "1.0.0", (self.leaf, self.leaf))

        catalog = DependencyClosedCatalog(self.catalog_root)
        lock = catalog.resolve("test.root", "1.0.0", self.pins)
        lock_path = self.root / "tampered.lock.json"
        lock_digest = catalog.write_lock(lock, lock_path)
        lock_path.write_text(lock_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lock digest mismatch"):
            catalog.read_lock(lock_path, lock_digest)

        valid_lock = self.root / "valid.lock.json"
        valid_digest = catalog.write_lock(lock, valid_lock)
        destination = self.root / "incompatible-workspace"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            catalog.materialize(
                valid_lock,
                valid_digest,
                destination,
                (InterfaceRequirement("ready", PortDirection.INPUT, 1),),
            )
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
