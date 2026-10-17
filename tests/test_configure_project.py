import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


_SPEC = importlib.util.spec_from_file_location(
    "configure_project", Path(__file__).resolve().parents[1] / "tools" / "configure_project.py")
setup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(setup)


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve() / "Project"
        self.project.mkdir()
        self.uproject = self.project / "Example.uproject"
        self.original = b'{"FileVersion": 3, "Plugins": []}\n'
        self.uproject.write_bytes(self.original)
        self.descriptor = self.project / "Plugins" / "RetargetAssistant" / "RetargetAssistant.uplugin"
        self.descriptor.parent.mkdir(parents=True)
        self.descriptor.write_text("{}", encoding="utf-8")
        self.marker = self.project / "Config" / "RetargetAssistantProject.json"

    def test_binds_project_without_changing_uproject(self):
        self.assertEqual(setup.configure_project(self.uproject), self.marker)
        policy = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertIs(policy["allow_generated_assets"], True)
        self.assertEqual(Path(policy["project_file"]), self.uproject)
        self.assertEqual(self.uproject.read_bytes(), self.original)
        self.assertFalse((self.project / "Content").exists())

    def test_matching_policy_is_not_rewritten(self):
        setup.configure_project(self.uproject)
        before = self.marker.read_bytes()
        timestamp = self.marker.stat().st_mtime_ns
        setup.configure_project(self.uproject)
        self.assertEqual(self.marker.read_bytes(), before)
        self.assertEqual(self.marker.stat().st_mtime_ns, timestamp)

    def test_conflicting_policy_is_preserved(self):
        self.marker.parent.mkdir()
        self.marker.write_text('{"allow_generated_assets": false}', encoding="utf-8")
        before = self.marker.read_bytes()
        with self.assertRaises(ValueError):
            setup.configure_project(self.uproject)
        self.assertEqual(self.marker.read_bytes(), before)

    def test_refuses_missing_plugin(self):
        self.descriptor.unlink()
        with self.assertRaises(ValueError):
            setup.configure_project(self.uproject)
        self.assertFalse(self.marker.exists())

    def test_refuses_existing_sandbox(self):
        self.marker.parent.mkdir()
        (self.marker.parent / "RetargetAssistantSandbox.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            setup.configure_project(self.uproject)
        self.assertFalse(self.marker.exists())

    def test_refuses_config_link_outside_project(self):
        original_resolve = Path.resolve
        config = self.marker.parent

        def resolve(path, *args, **kwargs):
            if path == config:
                return self.project.parent / "Outside"
            return original_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", resolve):
            with self.assertRaises(ValueError):
                setup.configure_project(self.uproject)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
