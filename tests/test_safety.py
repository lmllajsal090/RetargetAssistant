"""Isolation boundaries tested without importing or launching a real UE editor.

Only throwaway directories are written. Junction traversal is simulated by
patching Path.resolve, so these tests need no link privileges or source project.
"""

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


_PACKAGE_PATH = Path(__file__).resolve().parents[1] / "Content" / "Python" / "retarget_assistant"
_PACKAGE_NAME = "_retarget_assistant_safety_tests"
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_PACKAGE_PATH)]
_PACKAGE.__version__ = "0.2.0"
sys.modules[_PACKAGE_NAME] = _PACKAGE
_SPEC = importlib.util.spec_from_file_location(
    _PACKAGE_NAME + ".unreal_adapter", str(_PACKAGE_PATH / "unreal_adapter.py"))
adapter = importlib.util.module_from_spec(_SPEC)
with mock.patch.dict(sys.modules, {"unreal": types.ModuleType("unreal")}):
    _SPEC.loader.exec_module(adapter)


class _Asset:
    def __init__(self, path, skeleton=None):
        self.path = path
        self.skeleton = skeleton

    def get_path_name(self):
        return self.path

    def get_editor_property(self, name):
        if name == "skeleton":
            return self.skeleton
        return {"additive_anim_type": "NONE", "enable_root_motion": False,
                "force_root_lock": False}[name]

    def get_play_length(self):
        return 1.0


class _SkeletalMesh(_Asset):
    pass


class _AnimSequence(_Asset):
    pass


class _IKRetargeter(_Asset):
    pass


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="retarget_safety_")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve() / "IsolatedProject"
        self.project.mkdir()
        for child in ("Config", "Content", "Saved"):
            (self.project / child).mkdir()
        self.project_file = self.project / "RetargetLab.uproject"
        self.project_file.write_text("{}", encoding="utf-8")
        self.assets = {}
        self.unreal = types.SimpleNamespace(
            Paths=types.SimpleNamespace(
                project_dir=mock.Mock(return_value=str(self.project)),
                get_project_file_path=mock.Mock(return_value=str(self.project_file)),
                convert_relative_path_to_full=mock.Mock(side_effect=lambda value: value),
            ),
            SystemLibrary=types.SimpleNamespace(get_engine_version=mock.Mock(return_value="5.8.0-test")),
            SkeletalMesh=_SkeletalMesh,
            AnimSequence=_AnimSequence,
            IKRetargeter=_IKRetargeter,
            load_asset=mock.Mock(side_effect=self.assets.get),
            EditorAssetLibrary=types.SimpleNamespace(
                save_loaded_asset=mock.Mock(return_value=True),
                does_directory_exist=mock.Mock(return_value=False),
                find_asset_data=mock.Mock(),
            ),
            IKRetargetBatchOperation=types.SimpleNamespace(run_batch_retarget=mock.Mock(return_value=[])),
        )
        self.unreal_patch = mock.patch.object(adapter, "unreal", self.unreal)
        self.unreal_patch.start()
        self.addCleanup(self.unreal_patch.stop)

    def marker(self, **overrides):
        policy = {"project_file": str(self.project_file), "allow_generated_assets": True}
        policy.update(overrides)
        (self.project / "Config" / "RetargetAssistantSandbox.json").write_text(
            json.dumps(policy), encoding="utf-8")

    def redirect_resolve(self, link, destination):
        original = Path.resolve

        def resolve(path, *args, **kwargs):
            resolved = original(path, *args, **kwargs)
            if resolved.is_relative_to(link):
                return destination / resolved.relative_to(link)
            return resolved

        return mock.patch.object(Path, "resolve", new=resolve)

    def assert_no_asset_write(self):
        self.unreal.EditorAssetLibrary.save_loaded_asset.assert_not_called()
        self.unreal.IKRetargetBatchOperation.run_batch_retarget.assert_not_called()

    def test_ordinary_project_without_marker_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "独立测试工程"):
            adapter.require_sandbox()
        self.unreal.load_asset.assert_not_called()
        self.assert_no_asset_write()

    def test_marker_for_another_project_is_rejected(self):
        self.marker(project_file=str(self.project / "Different.uproject"))
        with self.assertRaisesRegex(RuntimeError, "路径.*不符"):
            adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_marker_without_explicit_write_authorization_is_rejected(self):
        for allowed in (False, "true", 1, None):
            with self.subTest(allowed=allowed):
                self.marker(allow_generated_assets=allowed)
                with self.assertRaises(RuntimeError):
                    adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_valid_isolated_project_and_output_are_accepted(self):
        self.marker()
        self.assertEqual(adapter.require_sandbox(), self.project)
        output = adapter.OUTPUT_ROOT + "/R_Test/IK_Source.IK_Source"
        self.assertEqual(adapter._assert_output(output), output)
        saved = adapter._saved_dir()
        self.assertTrue(saved.is_relative_to(self.project))
        self.assertTrue(saved.is_dir())
        self.assert_no_asset_write()

    def project_marker(self, **overrides):
        policy = {"project_file": str(self.project_file), "allow_generated_assets": True}
        policy.update(overrides)
        (self.project / "Config" / "RetargetAssistantProject.json").write_text(
            json.dumps(policy), encoding="utf-8")

    def test_explicit_project_install_is_accepted_without_sandbox_marker(self):
        self.project_marker()
        self.assertEqual(adapter.require_sandbox(), self.project)
        self.assertEqual(adapter.workspace_mode(), "project")
        self.assertEqual(adapter._assert_output(adapter.OUTPUT_ROOT + "/R_New/Anim"),
                         adapter.OUTPUT_ROOT + "/R_New/Anim")
        self.assert_no_asset_write()

    def test_project_install_binding_to_another_project_is_rejected(self):
        self.project_marker(project_file=str(self.project / "Other.uproject"))
        with self.assertRaisesRegex(RuntimeError, "路径.*不符"):
            adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_project_install_requires_strict_boolean_authorization(self):
        for value in (False, "true", "false", 1, None):
            with self.subTest(value=value):
                self.project_marker(allow_generated_assets=value)
                with self.assertRaises(RuntimeError):
                    adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_project_install_still_rejects_existing_asset_output_paths(self):
        self.project_marker()
        with self.assertRaises(ValueError):
            adapter._assert_output("/Game/Characters/ExistingAnimation")
        self.assert_no_asset_write()

    def test_project_install_cannot_escape_through_content_link(self):
        self.project_marker()
        with self.redirect_resolve(self.project / "Content", self.project.parent / "OriginalContent"):
            with self.assertRaises(RuntimeError):
                adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_sandbox_policy_still_takes_precedence(self):
        self.marker(project_file=str(self.project / "Other.uproject"))
        self.project_marker()
        with self.assertRaises(RuntimeError):
            adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_package_paths_cannot_escape_generated_root(self):
        self.marker()
        values = (
            "/Game/Characters/SK_Original",
            adapter.OUTPUT_ROOT + "Other/R_Test",
            adapter.OUTPUT_ROOT + "/R_Test/../Original",
            adapter.OUTPUT_ROOT + "/./R_Test",
            adapter.OUTPUT_ROOT + "/R_Test\\..\\..\\Original",
        )
        for value in values:
            with self.subTest(path=value):
                with self.assertRaises((ValueError, RuntimeError)):
                    adapter._assert_output(value)
        self.assert_no_asset_write()

    def test_saved_root_junction_outside_project_is_rejected(self):
        self.marker()
        with self.redirect_resolve(self.project / "Saved", self.project.parent / "OriginalSaved"):
            with self.assertRaises(RuntimeError):
                adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_saved_internal_junction_is_rejected_before_session_write(self):
        self.marker()
        link = self.project / "Saved" / "RetargetAssistant"
        with self.redirect_resolve(link, self.project.parent / "OriginalSaved"):
            with self.assertRaises((ValueError, RuntimeError)):
                adapter.save_session({"source_mesh": "/Game/Input/Source"})
        self.assertFalse((link / "session.json").exists())
        self.assert_no_asset_write()

    def test_content_root_junction_outside_project_is_rejected(self):
        self.marker()
        with self.redirect_resolve(self.project / "Content", self.project.parent / "OriginalContent"):
            with self.assertRaises(RuntimeError):
                adapter.require_sandbox()
        self.assert_no_asset_write()

    def test_content_internal_run_junction_is_rejected(self):
        self.marker()
        link = self.project / "Content" / "RetargetAssistant" / "Runs" / "R_Test"
        with self.redirect_resolve(link, self.project.parent / "OriginalContent"):
            with self.assertRaises((ValueError, RuntimeError)):
                adapter._assert_output(adapter.OUTPUT_ROOT + "/R_Test/IK_Source.IK_Source")
        self.assert_no_asset_write()

    def test_explicit_report_destination_outside_project_is_rejected(self):
        self.marker()
        destination = self.project.parent / "OriginalReports"
        with self.assertRaises((ValueError, RuntimeError)):
            adapter.write_report({}, destination)
        self.assertFalse(destination.exists())
        self.assert_no_asset_write()

    def test_nested_report_junction_outside_project_is_rejected(self):
        self.marker()
        link = self.project / "Saved" / "RetargetAssistant" / "Report_Test"
        with self.redirect_resolve(link, self.project.parent / "OriginalReports"):
            with self.assertRaises((ValueError, RuntimeError)):
                adapter.write_report({}, link)
        self.assert_no_asset_write()

    def test_wrong_animation_skeleton_is_rejected_and_blocks_job(self):
        self.marker()
        source_skeleton, target_skeleton = object(), object()
        source = _SkeletalMesh("/Game/Input/Source", source_skeleton)
        target = _SkeletalMesh("/Game/Input/Target", target_skeleton)
        animation = _AnimSequence("/Game/Input/ForeignAnimation", target_skeleton)
        self.assets.update({asset.path: asset for asset in (source, target, animation)})
        with self.assertRaisesRegex(ValueError, "不属于.*源角色"):
            adapter._animation_info(animation.path, source_skeleton)
        static_report = {"source": {}, "target": {}, "issues": [], "can_create": True}
        with mock.patch.object(adapter, "snapshot_mesh", return_value={}), mock.patch.object(
                adapter, "analyze_pair", return_value=copy.deepcopy(static_report)):
            report = adapter.inspect_job({"source_mesh": source.path, "target_mesh": target.path,
                                          "animations": [animation.path]})
        self.assertFalse(report["can_create"])
        self.assertEqual(report["animations"], [])
        self.assertTrue(any(issue["code"] == "animation_skeleton_mismatch" and issue["severity"] == "error"
                            for issue in report["issues"]))
        self.assert_no_asset_write()

    def test_changing_either_character_blocks_export_before_batch_operation(self):
        self.marker()
        report = {"can_create": True, "animations": [{"path": "/Game/Input/Walk"}]}
        for changed in ("source_mesh", "target_mesh"):
            with self.subTest(changed=changed):
                job = {"source_mesh": "/Game/Input/Source", "target_mesh": "/Game/Input/Target"}
                job["setup"] = dict(job)
                job[changed] += "Changed"
                with mock.patch.object(adapter, "inspect_job", return_value=copy.deepcopy(report)):
                    with self.assertRaisesRegex(ValueError, "角色已变化"):
                        adapter.export_animations(job)
                self.unreal.load_asset.assert_not_called()
                self.assert_no_asset_write()

    def test_unvalidated_engine_version_blocks_both_write_entry_points(self):
        self.marker()
        self.unreal.SystemLibrary.get_engine_version.return_value = "5.7.4-test"
        for entry in (adapter.create_setup, adapter.export_animations):
            with self.subTest(entry=entry.__name__):
                with mock.patch.object(adapter, "inspect_job") as inspect:
                    with self.assertRaisesRegex(RuntimeError, "5.8"):
                        entry({})
                    inspect.assert_not_called()
        self.assert_no_asset_write()


if __name__ == "__main__":
    unittest.main()
