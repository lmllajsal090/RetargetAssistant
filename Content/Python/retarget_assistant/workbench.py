"""Inspect real assets/actors and calibrate generated copies, never user originals."""
import json
from pathlib import Path
import unreal

from . import unreal_adapter as api
from .core import analyze_skeleton
from .ik_core import diagnose_contact, issue, pin_text, validate_edits


def _property(obj, name, default=None):
    try:
        return obj.get_editor_property(name)
    except Exception:
        return default


def _vector(value):
    return [float(value.x), float(value.y), float(value.z)]


def _transform(value):
    return {'location': _vector(value.translation), 'rotation': [value.rotation.x, value.rotation.y, value.rotation.z, value.rotation.w],
            'scale': _vector(value.scale3d)}


def _nodes(bp):
    prefix = bp.get_path_name()+':'
    # Compilers create transient ExecuteUbergraph copies under the same Blueprint.
    # Only nodes in its authoritative authored graphs may be inspected/calibrated.
    graphs = set(unreal.BlueprintEditorLibrary.list_graphs(bp))
    return [n for n in unreal.ObjectIterator(unreal.EdGraphNode)
            if n.get_path_name().startswith(prefix) and n.get_outer() in graphs]


def _rotation_origins(pin, nodes, seen=None):
    """Follow links and blueprint variable assignments without assuming variable names."""
    E, P = unreal.BlueprintEditorLibrary, unreal.BlueprintGraphPinLibrary
    seen = seen or set()
    results = []
    for source in P.list_connected_pins(pin):
        owner = P.get_owning_node(source)
        name = str(P.get_pin_name(source))
        token = owner.get_path_name()+':'+name
        if token in seen:
            continue
        seen.add(token)
        inputs = E.list_input_pins(owner)
        if name == 'OutRotation' and any(str(P.get_pin_name(p)) == 'BoneName' for p in inputs):
            results.append('Bone')
        elif name == 'Rotation' and owner.get_class().get_name() == 'K2Node_CallFunction':
            # A generic Break Transform can break any space: report unknown unless
            # upstream GetSocketTransform explicitly requests RTS_World.
            for incoming in inputs:
                for transform_source in P.list_connected_pins(incoming):
                    getter = P.get_owning_node(transform_source)
                    for gp in E.list_input_pins(getter):
                        if str(P.get_pin_name(gp)) == 'TransformSpace' and 'World' in P.get_pin_value(gp):
                            results.append('World')
        elif owner.get_class().get_name() == 'K2Node_VariableGet':
            for candidate in nodes:
                if candidate.get_class().get_name() == 'K2Node_VariableSet':
                    for assignment in E.list_input_pins(candidate):
                        if str(P.get_pin_name(assignment)) == name:
                            results.extend(_rotation_origins(assignment, nodes, seen))
        else:
            for incoming in inputs:
                if str(P.get_pin_name(incoming)) not in ('self', 'execute', 'ComponentPose'):
                    results.extend(_rotation_origins(incoming, nodes, seen))
    return sorted(set(results))


def _enum(value):
    name = getattr(value, 'name', str(value)).split('.')[-1].upper().replace('_', '')
    return {'BCSBONESPACE': 'Bone', 'BCSWORLDSPACE': 'World', 'BCSCOMPONENTSPACE': 'Component',
            'BMMIGNORE': 'Ignore', 'BMMREPLACE': 'Replace', 'BMMADDITIVE': 'Add'}.get(name, str(value))


def inspect_blueprint(path):
    api.require_sandbox()
    bp = api._load(path, unreal.AnimBlueprint)
    E, P = unreal.BlueprintEditorLibrary, unreal.BlueprintGraphPinLibrary
    records, issues = [], []
    all_nodes = _nodes(bp)
    for node in all_nodes:
        cls = node.get_class().get_name()
        if cls not in ('AnimGraphNode_TwoBoneIK', 'AnimGraphNode_ModifyBone', 'AnimGraphNode_ControlRig'):
            continue
        data = node.get_editor_property('node')
        pins = []
        for pin in E.list_all_pins(node):
            links = P.list_connected_pins(pin)
            pins.append({'name': str(P.get_pin_name(pin)), 'value': P.get_pin_value(pin),
                         'linked': bool(links)})
        alpha = next((p for p in pins if p['name'] == 'Alpha'), {})
        record = {'name': node.get_path_name().split(':',1)[1], 'path': node.get_path_name(), 'class': cls, 'pins': pins,
                  'settings': data.export_text(), 'alpha': float(data.get_editor_property('alpha')),
                  'alpha_driven': alpha.get('linked', False)}
        if cls == 'AnimGraphNode_TwoBoneIK':
            record.update(ik_bone=str(data.get_editor_property('ik_bone').get_editor_property('bone_name')),
                          effector_space=_enum(data.get_editor_property('effector_location_space')),
                          effector_target=str(data.get_editor_property('effector_target').get_editor_property('bone_reference').get_editor_property('bone_name')),
                          stretching=bool(data.get_editor_property('allow_stretching')))
            if record['ik_bone'].lower() == 'hand_l' and record['effector_space'] == 'Bone' and record['effector_target'].lower() == 'hand_l':
                issues.append(issue('SELF_REFERENCE_LEFT_TARGET', '左手目标参考自身骨骼；若目标由右手空间生成，请改用相同参考系。', evidence=record['name']))
        elif cls == 'AnimGraphNode_ModifyBone':
            record.update(bone=str(data.get_editor_property('bone_to_modify').get_editor_property('bone_name')),
                          translation=_vector(data.get_editor_property('translation')),
                          translation_mode=_enum(data.get_editor_property('translation_mode')),
                          rotation_space=_enum(data.get_editor_property('rotation_space')))
            rotation_pin = next((p for p in E.list_input_pins(node) if str(P.get_pin_name(p)) == 'Rotation'), None)
            if rotation_pin:
                origins = _rotation_origins(rotation_pin, all_nodes)
                record['rotation_origin_evidence'] = origins
                if len(origins) == 1:
                    record['rotation_input_space'] = origins[0]
        records.append(record)
    issues.extend(diagnose_contact({'grip_owners': ['not_sampled'], 'nodes': records})['issues'])
    if not records:
        issues.append(issue('NO_IK_NODES', '当前动画蓝图没有受支持的 IK / Control Rig 节点；可以先生成双臂 Control Rig。'))
    return {'kind': 'ik_blueprint', 'blueprint': path, 'nodes': records, 'issues': issues,
            'validation': {'graph_parameters': 'checked', 'target_data_space': 'not_checked',
                           'actual_player_instance': 'not_checked', 'visual_pose': 'not_checked'}}


def inspect_aim_offset(path):
    """Catch the center/reference discrepancy encountered during this TPS repair."""
    api.require_sandbox()
    asset = api._load(path, unreal.BlendSpace)
    samples = list(asset.get_editor_property('sample_data'))
    centers = [s.get_editor_property('animation') for s in samples if s.get_editor_property('animation') and
               math_length(s.get_editor_property('sample_value')) < 1e-4]
    issues, records = [], []
    for sample in samples:
        animation = sample.get_editor_property('animation')
        if not animation:
            continue
        reference = _property(animation, 'ref_pose_seq')
        record = {'animation': animation.get_path_name(), 'sample': _vector(sample.get_editor_property('sample_value')),
                  'additive_type': str(_property(animation, 'additive_anim_type')),
                  'reference': reference.get_path_name() if reference else '',
                  'reference_type': str(_property(animation, 'ref_pose_type'))}
        records.append(record)
        if len(centers) == 1 and reference and reference != centers[0]:
            issues.append(issue('AIM_REFERENCE_DIFFERS_FROM_CENTER',
                                '加法参考与中心动画不是同一资源；需比较实际姿势，不能仅凭名字断言错误。', evidence=record))
    if len(centers) != 1:
        issues.append(issue('AIM_CENTER_NOT_UNIQUE', '没有唯一零坐标中心样本，需检查 AimOffset 中心定义。'))
    return {'kind': 'ik_aim_offset', 'blueprint': path, 'issues': issues, 'samples': records,
            'validation': {'sample_reference_identity': 'checked', 'additive_pose_delta': 'not_checked', 'visual_pose': 'not_checked'}}


def math_length(v):
    return (v.x*v.x+v.y*v.y+v.z*v.z)**.5


def capture_contact(character, weapon=None, grip_socket='LeftHandGrip', grip_component='', weapon_socket='WeaponSocket',
                    expected_anim_blueprint='', expected_weapon_class=''):
    api.require_sandbox()
    if isinstance(character, unreal.Character):
        mesh = character.get_editor_property('mesh')
    else:
        components = list(character.get_components_by_class(unreal.SkeletalMeshComponent))
        if len(components) != 1:
            raise ValueError('角色必须只有一个候选 SkeletalMeshComponent，或使用 Character。')
        mesh = components[0]
    asset = mesh.get_skeletal_mesh_asset()
    if not asset:
        raise ValueError('所选角色未设置 Skeletal Mesh。')
    equipped = _property(character, 'equipped_weapon')
    if weapon and equipped and weapon != equipped:
        raise ValueError('所选武器与角色 EquippedWeapon 不同，请选择实际装备对象。')
    weapon = weapon or equipped
    if not weapon:
        attached = list(character.get_attached_actors())
        if len(attached) == 1:
            weapon = attached[0]
    if not weapon:
        raise ValueError('未能唯一确认实际武器。请同时选择角色与武器，或提供 EquippedWeapon。')
    roles = analyze_skeleton(api.snapshot_mesh(asset))['role_bones']
    owners = []
    for component in weapon.get_components_by_class(unreal.MeshComponent):
        if component.does_socket_exist(grip_socket) and (not grip_component or component.get_name() == grip_component):
            owners.append(component)
    anim = mesh.get_anim_instance()
    snapshot = {'actor': character.get_path_name(), 'world': character.get_world().get_path_name(),
                'mesh': asset.get_path_name(), 'anim_class': anim.get_class().get_path_name() if anim else '',
                'weapon_class': weapon.get_class().get_path_name(),
                'weapon_socket_exists': mesh.does_socket_exist(weapon_socket),
                'weapon_socket': weapon_socket, 'grip_socket': grip_socket,
                'grip_owners': [c.get_name() for c in owners], 'arms': {},
                'mesh_world_transform': _transform(mesh.get_world_transform()),
                'scope': 'single_live_pose', 'socket_ownership': []}
    if expected_anim_blueprint:
        chosen = api._load(expected_anim_blueprint, unreal.AnimBlueprint)
        snapshot['expected_anim_class'] = chosen.generated_class().get_path_name()
    if expected_weapon_class:
        snapshot['expected_weapon_class'] = expected_weapon_class
    # Mesh-only sockets must not be confused with a shared Skeleton socket.
    for obj in unreal.ObjectIterator(unreal.SkeletalMeshSocket):
        if obj.get_outer() in (asset, asset.get_editor_property('skeleton')):
            snapshot['socket_ownership'].append({'name': str(obj.socket_name), 'owner': obj.get_outer().get_path_name(),
                                                  'bone': str(obj.bone_name)})
    if len(owners) == 1:
        transform = owners[0].get_socket_transform(grip_socket, unreal.RelativeTransformSpace.RTS_WORLD)
        goal = transform.translation
        snapshot['grip_world_transform'] = _transform(transform)
        try:
            # Reflection dispatches BlueprintNativeEvent overrides; a generated
            # native Python wrapper can bypass the override in some versions.
            getter = weapon.call_method('GetLeftHandGripTransform')
            if isinstance(getter, unreal.Transform):
                snapshot['getter_world_transform'] = _transform(getter)
        except Exception:
            snapshot['getter_check'] = 'unavailable'
        if 'hand_r' in roles:
            right = mesh.get_socket_transform(roles['hand_r'], unreal.RelativeTransformSpace.RTS_WORLD)
            snapshot['grip_in_right_hand'] = _vector(right.inverse_transform_location(goal))
        for side in ('l', 'r'):
            names = [roles.get(part+'_'+side) for part in ('upperarm', 'lowerarm', 'hand')]
            if all(names):
                points = [mesh.get_socket_location(b) for b in names]
                snapshot['arms'][side] = dict(zip(('shoulder', 'elbow', 'wrist'), [_vector(v) for v in points]))
                # Right hand is inspected at its current target, not erroneously against the left grip.
                snapshot['arms'][side]['goal'] = _vector(goal if side == 'l' else points[2])
                snapshot['arms'][side]['goal_source'] = 'grip_socket' if side == 'l' else 'current_wrist_not_configured_target'
        if anim:
            stored = _property(anim, 'left_hand_grip_world_transform')
            if stored:
                snapshot['stored_grip_world'] = _transform(stored)
    report = diagnose_contact(snapshot)
    report['validation']['right_configured_ik_target'] = 'not_checked'
    report['snapshot'] = snapshot
    if snapshot.get('getter_world_transform'):
        from .ik_core import distance
        error = distance(snapshot['getter_world_transform']['location'], snapshot['grip_world_transform']['location'])
        report['getter_target_error_cm'] = error
        if error > 1:
            report['issues'].append(issue('WEAPON_GETTER_TARGET_MISMATCH', '实际武器的握点函数返回值与真实 Socket 不符。检查当前武器类的覆盖实现。', 'error', error))
            report['can_calibrate'] = False
    if snapshot.get('stored_grip_world'):
        from .ik_core import distance
        error = distance(snapshot['stored_grip_world']['location'], snapshot['grip_world_transform']['location'])
        report['stored_target_error_cm'] = error
        if error > 1:
            report['issues'].append(issue('GETTER_TARGET_MISMATCH', '动画存储目标与组件真实 Socket 不一致。检查实际武器类中的覆盖函数。', 'error', error))
            report['can_calibrate'] = False
    return report


def save_diagnosis(report):
    folder = api.write_report(report)
    job = api.load_session()
    job['last_report_dir'] = folder
    api.save_session(job)
    return folder


def create_preset(blueprint_path):
    report = inspect_blueprint(blueprint_path)
    preset = {'schema_version': 1, 'blueprint': blueprint_path, 'edits': [],
              'instructions': '从 available_pins 复制 node/pin/expected，并填入 value。只在新副本生效。',
              'available_pins': []}
    for node in report['nodes']:
        for pin in node['pins']:
            if pin['name'] in ('EffectorLocation', 'JointTargetLocation', 'Translation', 'Rotation', 'Alpha') and not pin['linked']:
                preset['available_pins'].append({'node': node['name'], 'pin': pin['name'], 'expected': pin['value']})
    path = api._saved_dir()/('IKPreset_'+api._run_id()+'.json')
    path.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding='utf-8')
    job = api.load_session()
    job['ik_preset'] = str(path)
    api.save_session(job)
    return str(path)


def _node_pin(bp, name, pin_name):
    matches = [n for n in _nodes(bp) if (n.get_path_name().split(':',1)[1] == name or n.get_name() == name) and n.get_class().get_name() in
               ('AnimGraphNode_TwoBoneIK', 'AnimGraphNode_ModifyBone')]
    if len(matches) != 1:
        raise ValueError('IK 节点不存在或不唯一：'+name)
    E, P = unreal.BlueprintEditorLibrary, unreal.BlueprintGraphPinLibrary
    pins = [p for p in E.list_input_pins(matches[0]) if str(P.get_pin_name(p)) == pin_name]
    if len(pins) != 1 or P.list_connected_pins(pins[0]):
        raise ValueError('引脚不存在或由连线驱动，禁止覆盖：'+name+'.'+pin_name)
    return pins[0]


def apply_preset(preset):
    api.require_sandbox()
    api._require_supported_engine()
    validate_edits(preset.get('edits'))
    if preset.get('schema_version') != 1:
        raise ValueError('不支持的 IK 配置版本。')
    original = api._load(preset.get('blueprint', ''), unreal.AnimBlueprint)
    P = unreal.BlueprintGraphPinLibrary
    # Complete optimistic concurrency checks BEFORE creating or changing anything.
    for edit in preset['edits']:
        pin = _node_pin(original, edit['node'], edit['pin'])
        if P.get_pin_value(pin) != edit['expected']:
            raise ValueError('诊断之后源引脚已变化，请重新导出配置：'+edit['node']+'.'+edit['pin'])
    path = api._assert_output(api.OUTPUT_ROOT+'/'+api._run_id()+'/ABP_IKCalibration')
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        raise ValueError('输出已经存在。')
    copy = unreal.EditorAssetLibrary.duplicate_asset(original.get_path_name(), path)
    if not copy:
        raise RuntimeError('动画蓝图副本创建失败。')
    changes = []
    try:
        for edit in preset['edits']:
            pin = _node_pin(copy, edit['node'], edit['pin'])
            before = P.get_pin_value(pin)
            if not P.set_pin_value(pin, pin_text(edit)):
                raise ValueError('引擎拒绝引脚值：'+edit['pin'])
            changes.append(dict(node=edit['node'], pin=edit['pin'], before=before, after=P.get_pin_value(pin)))
        unreal.BlueprintEditorLibrary.compile_blueprint(copy)
        if 'ERROR' in str(copy.get_editor_property('status')).upper():
            raise RuntimeError('副本编译失败，源蓝图未改动。')
        api._save_new(copy)
    except Exception as exc:
        save_diagnosis({'kind': 'ik_calibration', 'source': original.get_path_name(), 'copy': path,
                        'changes': changes, 'error': str(exc)})
        raise
    report = {'kind': 'ik_calibration', 'source_blueprint': original.get_path_name(), 'copy': path,
              'changes': changes, 'validation': {'compile': 'checked', 'visual_pose': 'not_checked', 'gameplay': 'not_checked'}}
    folder = save_diagnosis(report)
    # Reverse preset uses expected values, preserving unrelated later edits.
    reverse = {'schema_version': 1, 'blueprint': copy.get_path_name(), 'edits': [], 'restore_only': True}
    for item in changes:
        reverse['edits'].append(dict(node=item['node'], pin=item['pin'], expected=item['after'],
                                    value=float(item['before']) if item['pin'] == 'Alpha' else
                                    [float(x) for x in item['before'].split(',')]))
    (Path(folder)/'restore.json').write_text(json.dumps(reverse, indent=2), encoding='utf-8')
    job = api.load_session()
    job.update(last_ik_copy=path, last_ik_restore=str(Path(folder)/'restore.json'))
    api.save_session(job)
    return report


def restore_generated(preset):
    """Restore only constant pins on an assistant-generated copy, with conflict checks."""
    api.require_sandbox()
    api._require_supported_engine()
    path = preset.get('blueprint', '')
    api._assert_output(path)
    validate_edits(preset.get('edits'))
    bp = api._load(path, unreal.AnimBlueprint)
    P = unreal.BlueprintGraphPinLibrary
    pins = []
    for edit in preset['edits']:
        pin = _node_pin(bp, edit['node'], edit['pin'])
        if P.get_pin_value(pin) != edit['expected']:
            raise ValueError('恢复冲突：引脚已有后续修改，停止恢复。')
        pins.append((pin, edit))
    with unreal.ScopedEditorTransaction('恢复助手 IK 参数'):
        for pin, edit in pins:
            if not P.set_pin_value(pin, pin_text(edit)):
                raise RuntimeError('恢复引脚失败。')
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        api._save_new(bp)
    return {'restored': path, 'count': len(pins)}


def load_config(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(api._saved_dir()):
        raise ValueError('配置路径必须位于当前工程 Saved/RetargetAssistant。')
    return json.loads(resolved.read_text(encoding='utf-8-sig'))
