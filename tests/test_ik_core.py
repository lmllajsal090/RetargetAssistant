"""Regression cases taken from the character replacement and grip debugging."""
import copy
import importlib.util
import math
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ik_core', Path(__file__).resolve().parents[1]/'Content/Python/retarget_assistant/ik_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def snapshot():
    return {'grip_owners': ['MagazineMesh'], 'arms': {'l': {
        'shoulder': [0,0,0], 'elbow': [20,0,0], 'wrist': [30,10,0], 'goal': [30,10,0]}}}


def codes(report):
    return {item['code'] for item in report['issues']}


class GripDiagnosticsTests(unittest.TestCase):
    def test_contact_does_not_claim_visual_or_movement_acceptance(self):
        report = core.diagnose_contact(snapshot())
        self.assertTrue(report['can_calibrate'])
        self.assertEqual(report['validation']['visual_clipping'], 'not_checked')
        self.assertEqual(report['validation']['movement'], 'not_checked')

    def test_missing_grip_is_not_replaced_by_actor_origin(self):
        value = snapshot(); value['grip_owners'] = []
        self.assertIn('GRIP_SOCKET_MISSING', codes(core.diagnose_contact(value)))
        self.assertFalse(core.diagnose_contact(value)['can_calibrate'])

    def test_ambiguous_grip_is_not_guessed(self):
        value = snapshot(); value['grip_owners'].append('WeaponMesh')
        self.assertIn('GRIP_SOCKET_AMBIGUOUS', codes(core.diagnose_contact(value)))

    def test_wrong_actual_weapon(self):
        value = snapshot(); value.update(expected_weapon_class='Bp_Rifle', weapon_class='BP_Rifle_AnimationSafe')
        self.assertIn('ACTIVE_WEAPON_MISMATCH', codes(core.diagnose_contact(value)))

    def test_wrong_actual_anim(self):
        value = snapshot(); value.update(expected_anim_class='Preview', anim_class='Player')
        self.assertIn('ACTIVE_ANIM_MISMATCH', codes(core.diagnose_contact(value)))

    def test_missing_mesh_socket(self):
        value = snapshot(); value['weapon_socket_exists'] = False
        self.assertIn('WEAPON_SOCKET_MISSING', codes(core.diagnose_contact(value)))

    def test_position_space_disagreement(self):
        value = snapshot(); value.update(position_space='Bone', effector_space='World')
        self.assertIn('POSITION_SPACE_MISMATCH', codes(core.diagnose_contact(value)))

    def test_rotation_space_disagreement(self):
        value = snapshot(); value.update(rotation_space='World', rotation_input_space='Bone')
        self.assertIn('ROTATION_SPACE_MISMATCH', codes(core.diagnose_contact(value)))

    def test_right_hand_move_can_make_left_unreachable(self):
        value = snapshot(); value['arms']['l']['goal'] = [60,0,0]
        self.assertIn('ARM_UNREACHABLE', codes(core.diagnose_contact(value)))

    def test_near_extension_is_reported_separately_from_miss(self):
        value = snapshot(); value['arms']['l']['goal'] = [34,0,0]
        self.assertIn('ARM_NEAR_STRAIGHT', codes(core.diagnose_contact(value)))
        self.assertIn('WRIST_TARGET_ERROR', codes(core.diagnose_contact(value)))

    def test_inner_reach_boundary_is_not_ignored(self):
        result = core.reach_metrics([0,0,0], [30,0,0], [35,0,0], [1,0,0])
        self.assertFalse(result['reachable'])

    def test_zero_length_chain(self):
        value = snapshot(); value['arms']['l']['elbow'] = [0,0,0]
        self.assertIn('ARM_SAMPLE_INVALID', codes(core.diagnose_contact(value)))

    def test_non_finite_positions_are_rejected(self):
        for bad in (float('nan'), float('inf'), True, '3'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.vector([bad, 0, 0])

    def test_invalid_vector_dimensions(self):
        for bad in ([], [0,0], [0,0,0,0], 'abc', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.vector(bad)

    def test_ignore_translation_and_zero_alpha(self):
        value = snapshot(); value['nodes'] = [dict(name='UpperArm', translation_mode='Ignore', translation=[1,2,3], alpha=0)]
        self.assertTrue({'TRANSLATION_IGNORED','IK_ALPHA_ZERO'}.issubset(codes(core.diagnose_contact(value))))

    def test_driven_alpha_does_not_use_constant_default(self):
        value = snapshot(); value['nodes'] = [dict(name='IK', alpha=0, alpha_driven=True)]
        self.assertNotIn('IK_ALPHA_ZERO', codes(core.diagnose_contact(value)))


class CalibrationInputTests(unittest.TestCase):
    def edit(self):
        return dict(node='AnimGraphNode_TwoBoneIK_1', pin='EffectorLocation', expected='0,0,0', value=[4.2,-8.7,-6.1])

    def test_valid_edit_and_format(self):
        edit = self.edit()
        self.assertEqual(core.validate_edits([edit]), [edit])
        self.assertEqual(core.pin_text(edit), '4.200000,-8.700000,-6.100000')

    def test_arbitrary_property_or_code_rejected(self):
        for name in ('Execute', 'Class', 'script', 'ComponentPose'):
            edit = self.edit(); edit['pin'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                core.validate_edits([edit])

    def test_expected_required_for_conflict_protection(self):
        edit = self.edit(); edit.pop('expected')
        with self.assertRaises(ValueError): core.validate_edits([edit])

    def test_duplicate_edits(self):
        with self.assertRaises(ValueError): core.validate_edits([self.edit(), self.edit()])

    def test_alpha_bounds_and_invalid_types(self):
        for bad in (-1, 2, True, float('nan'), '1', None):
            edit = self.edit(); edit.update(pin='Alpha', value=bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.validate_edits([edit])

    def test_empty_or_malformed_edits(self):
        for bad in (None, [], {}, ['code'], [dict(pin='Alpha', value=.5)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError): core.validate_edits(bad)


if __name__ == '__main__':
    unittest.main()
