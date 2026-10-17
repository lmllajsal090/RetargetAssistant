"""Real failure boundaries for the name-and-hierarchy diagnostic core."""

import copy
import importlib.util
from pathlib import Path
import unittest


_PATH = Path(__file__).resolve().parents[1] / "Content" / "Python" / "retarget_assistant" / "core.py"
_SPEC = importlib.util.spec_from_file_location("retarget_assistant_core", str(_PATH))
core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(core)


def mannequin():
    entries = [
        ("root", -1), ("pelvis", 0), ("spine_01", 1), ("spine_02", 2),
        ("spine_03", 3), ("neck_01", 4), ("head", 5),
        ("clavicle_l", 4), ("upperarm_l", 7), ("lowerarm_l", 8), ("hand_l", 9),
        ("clavicle_r", 4), ("upperarm_r", 11), ("lowerarm_r", 12), ("hand_r", 13),
        ("thigh_l", 1), ("calf_l", 15), ("foot_l", 16),
        ("thigh_r", 1), ("calf_r", 18), ("foot_r", 19),
    ]
    return {"mesh_path": "/Game/Source/SK_Mannequin", "skeleton_path": "/Game/Source/Skeleton", "bones": [{"name": name, "parent_index": parent} for name, parent in entries]}


def mixamo():
    entries = [
        ("Hips", -1), ("Spine", 0), ("Spine1", 1), ("Spine2", 2),
        ("Neck", 3), ("Head", 4),
        ("LeftShoulder", 3), ("LeftArm", 6), ("LeftForeArm", 7), ("LeftHand", 8),
        ("RightShoulder", 3), ("RightArm", 10), ("RightForeArm", 11), ("RightHand", 12),
        ("LeftUpLeg", 0), ("LeftLeg", 14), ("LeftFoot", 15),
        ("RightUpLeg", 0), ("RightLeg", 17), ("RightFoot", 18),
    ]
    return {"mesh_path": "/Game/Target/SK_Mixamo", "bones": [{"name": "mixamorig:" + name, "parent_index": parent} for name, parent in entries]}


def codes(result):
    return {item["code"] for item in result["issues"]}


class AnalyzeSkeletonTests(unittest.TestCase):
    def test_mannequin_chains_preserve_original_names(self):
        result = core.analyze_skeleton(mannequin())
        self.assertTrue(result["can_create"])
        self.assertEqual(result["family"], "ue_mannequin")
        self.assertEqual(result["pelvis"], "pelvis")
        self.assertEqual(result["root_bone"], "root")
        arms = {item["name"]: item for item in result["chains"]}
        self.assertEqual(arms["LeftArm"]["start_bone"], "upperarm_l")
        self.assertEqual(arms["RightLeg"]["end_bone"], "foot_r")
        self.assertIn("STATIC_ANALYSIS_LIMIT", codes(result))

    def test_namespace_and_mixamo_without_separate_root(self):
        result = core.analyze_skeleton(mixamo())
        self.assertTrue(result["can_create"])
        self.assertEqual(result["family"], "mixamo")
        self.assertEqual(result["root_bone"], "mixamorig:Hips")
        self.assertEqual(result["pelvis"], "mixamorig:Hips")
        chains = {item["name"]: item for item in result["chains"]}
        self.assertNotIn("Root", chains)
        self.assertEqual(chains["LeftArm"]["start_bone"], "mixamorig:LeftArm")

    def test_concatenated_namespace(self):
        snapshot = mixamo()
        for bone in snapshot["bones"]:
            bone["name"] = bone["name"].replace("mixamorig:", "mixamorig1")
        self.assertTrue(core.analyze_skeleton(snapshot)["can_create"])

    def test_optional_ik_twist_and_finger_bones(self):
        snapshot = mannequin()
        snapshot["bones"].extend([
            {"name": "ik_foot_root", "parent_index": 0},
            {"name": "ik_foot_l", "parent_index": 21},
            {"name": "upperarm_twist_01_l", "parent_index": 8},
            {"name": "index_01_l", "parent_index": 10},
            {"name": "VB ik_foot_l_foot_l", "parent_index": 22},
        ])
        result = core.analyze_skeleton(snapshot)
        self.assertTrue(result["can_create"])
        self.assertIn("OPTIONAL_IK_BONES", codes(result))

    def test_wrong_side_parent_blocks_creation(self):
        snapshot = mannequin()
        snapshot["bones"][9]["parent_index"] = 12
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("BROKEN_CHAIN", codes(result))
        self.assertIn("CROSSED_LIMB_HIERARCHY", codes(result))
        self.assertEqual(result["chains"], [])

    def test_other_limb_inserted_in_left_arm_path_is_rejected(self):
        snapshot = mannequin()
        snapshot["bones"][12]["parent_index"] = 8
        snapshot["bones"][9]["parent_index"] = 12
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("CROSSED_LIMB_HIERARCHY", codes(result))

    def test_missing_critical_bone_blocks_creation(self):
        for index in (1, 2, 6, 8, 9, 10, 15, 16, 17):
            with self.subTest(index=index):
                snapshot = mannequin()
                snapshot["bones"][index]["name"] = "unmapped_%d" % index
                if index == 2:
                    for bone in snapshot["bones"]:
                        if bone["name"].startswith("spine_"):
                            bone["name"] = "unmapped_" + bone["name"]
                result = core.analyze_skeleton(snapshot)
                self.assertFalse(result["can_create"])
                self.assertIn("MISSING_REQUIRED_BONE", codes(result))

    def test_namespace_ambiguity_is_not_silently_resolved(self):
        snapshot = mannequin()
        snapshot["bones"].append({"name": "extra:hand_l", "parent_index": 9})
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("AMBIGUOUS_REQUIRED_BONE", codes(result))
        self.assertIn("AMBIGUOUS_BONE_NAME", codes(result))
        self.assertNotIn("hand_l", result["role_bones"])

    def test_spine_same_number_with_different_padding_is_ambiguous(self):
        snapshot = mannequin()
        snapshot["bones"].append({"name": "spine_1", "parent_index": 2})
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("AMBIGUOUS_SPINE", codes(result))

    def test_disconnected_cycle_is_found_even_with_one_valid_root(self):
        snapshot = mannequin()
        snapshot["bones"].extend([
            {"name": "helper_a", "parent_index": 22},
            {"name": "helper_b", "parent_index": 21},
        ])
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("HIERARCHY_CYCLE", codes(result))

    def test_multiple_roots_are_rejected(self):
        snapshot = mannequin()
        snapshot["bones"][18]["parent_index"] = -1
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIsNone(result["root_bone"])
        self.assertIn("ROOT_COUNT", codes(result))

    def test_invalid_parent_values_are_errors(self):
        for parent in (999, -2, True, None, "1"):
            with self.subTest(parent=parent):
                snapshot = mannequin()
                snapshot["bones"][10]["parent_index"] = parent
                result = core.analyze_skeleton(snapshot)
                self.assertFalse(result["can_create"])
                self.assertIn("INVALID_PARENT_INDEX", codes(result))

    def test_self_parent_and_duplicate_names(self):
        snapshot = mannequin()
        snapshot["bones"][10]["parent_index"] = 10
        snapshot["bones"].append({"name": "HAND_L", "parent_index": 9})
        result = core.analyze_skeleton(snapshot)
        self.assertIn("SELF_PARENT", codes(result))
        self.assertIn("DUPLICATE_BONE_NAME", codes(result))
        self.assertFalse(result["can_create"])

    def test_unknown_skeleton_is_reported_not_guessed(self):
        result = core.analyze_skeleton({"bones": [{"name": "Bone", "parent_index": -1}]})
        self.assertEqual(result["family"], "unknown")
        self.assertFalse(result["can_create"])
        self.assertIn("UNSUPPORTED_SKELETON", codes(result))

    def test_hybrid_naming_requires_explicit_mapping(self):
        snapshot = mannequin()
        snapshot["bones"][1]["name"] = "mixamorig:Hips"
        result = core.analyze_skeleton(snapshot)
        self.assertEqual(result["family"], "mixed")
        self.assertFalse(result["can_create"])
        self.assertIn("MIXED_NAMING_FAMILY", codes(result))
        self.assertEqual(core.suggest_chains(snapshot), [])

    def test_malformed_snapshots_do_not_crash(self):
        for snapshot in (None, [], {}, {"bones": []}, {"bones": [None]}, {"bones": [{"name": [], "parent_index": 0}]}):
            with self.subTest(snapshot=snapshot):
                result = core.analyze_skeleton(snapshot)
                self.assertFalse(result["can_create"])
                self.assertTrue(result["issues"])

    def test_spine_branch_and_number_reversal_are_errors(self):
        snapshot = mannequin()
        snapshot["bones"][3]["parent_index"] = 1
        self.assertIn("BROKEN_CHAIN", codes(core.analyze_skeleton(snapshot)))
        snapshot = mannequin()
        snapshot["bones"][2]["name"] = "spine_03"
        snapshot["bones"][4]["name"] = "spine_01"
        self.assertIn("SPINE_ORDER", codes(core.analyze_skeleton(snapshot)))

    def test_neck_on_unrelated_branch_is_error(self):
        snapshot = mannequin()
        snapshot["bones"][5]["parent_index"] = 15
        snapshot["bones"][6]["parent_index"] = 4
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("BROKEN_NECK_CHAIN", codes(result))

    def test_head_below_hand_is_error_even_if_spine_is_ancestor(self):
        snapshot = mannequin()
        snapshot["bones"][5]["parent_index"] = 10
        result = core.analyze_skeleton(snapshot)
        self.assertFalse(result["can_create"])
        self.assertIn("CROSSED_LIMB_HIERARCHY", codes(result))

    def test_ue5_extra_spine_and_neck_bones(self):
        snapshot = mannequin()
        snapshot["bones"].extend([
            {"name": "spine_04", "parent_index": 4},
            {"name": "spine_05", "parent_index": 21},
            {"name": "neck_02", "parent_index": 5},
        ])
        snapshot["bones"][5]["parent_index"] = 22
        snapshot["bones"][6]["parent_index"] = 23
        snapshot["bones"][7]["parent_index"] = 22
        snapshot["bones"][11]["parent_index"] = 22
        result = core.analyze_skeleton(snapshot)
        self.assertTrue(result["can_create"])
        chains = {item["name"]: item for item in result["chains"]}
        self.assertEqual(chains["Spine"]["end_bone"], "spine_05")
        self.assertEqual(chains["Head"]["start_bone"], "neck_01")

    def test_input_is_not_mutated(self):
        snapshot = mannequin()
        before = copy.deepcopy(snapshot)
        core.analyze_skeleton(snapshot)
        self.assertEqual(snapshot, before)


class AnalyzePairTests(unittest.TestCase):
    def test_supported_cross_family_pair_preserves_limits(self):
        result = core.analyze_pair(mannequin(), mixamo())
        self.assertTrue(result["can_create"])
        self.assertIn("CROSS_FAMILY_RETARGET", codes(result))
        self.assertIn("ROOT_CHAIN_MISMATCH", codes(result))
        limit = next(item["message"] for item in result["issues"] if item["code"] == "STATIC_ANALYSIS_LIMIT")
        self.assertIn("root motion", limit)
        self.assertIn("脚部接触/滑步", limit)
        self.assertIn("尚未验证参考姿势", limit)

    def test_pair_reports_which_side_failed(self):
        target = mixamo()
        target["bones"][9]["name"] = "UnknownHand"
        result = core.analyze_pair(mannequin(), target)
        self.assertFalse(result["can_create"])
        failed = [item for item in result["issues"] if item["code"] == "MISSING_REQUIRED_BONE"]
        self.assertEqual(failed[0]["skeleton"], "target")
        self.assertTrue(failed[0]["message"].startswith("目标骨架"))

    def test_same_mesh_is_warning_and_does_not_prove_animation_quality(self):
        result = core.analyze_pair(mannequin(), mannequin())
        self.assertTrue(result["can_create"])
        self.assertIn("SAME_MESH", codes(result))
        self.assertIn("STATIC_ANALYSIS_LIMIT", codes(result))


if __name__ == "__main__":
    unittest.main()
