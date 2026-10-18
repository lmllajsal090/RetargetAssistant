"""Build real two-arm Control Rig assets in new assistant run directories."""
import math
import unreal

from . import unreal_adapter as api
from .core import analyze_skeleton


def key(name, kind=None):
    return unreal.RigElementKey(name=name, type=kind or unreal.RigElementType.BONE)


def _unit(controller, struct, name, x, y):
    node = controller.add_unit_node_from_struct_path(
        '/Script/ControlRig.' + struct, 'Execute', unreal.Vector2D(x, y), name)
    if not node:
        raise RuntimeError('无法建立 Control Rig 节点：' + name)
    return node.get_name()


def _set(controller, path, value):
    if not controller.set_pin_default_value(path, value):
        raise RuntimeError('无法设置 Control Rig 引脚：' + path)


def _link(controller, source, target):
    if not controller.add_link(source, target):
        raise RuntimeError('无法连接 Control Rig：' + source + ' → ' + target)


def _xyz(v):
    return '(X=%.9f,Y=%.9f,Z=%.9f)' % (v.x, v.y, v.z)


def _normalized(v):
    length = math.sqrt(v.x*v.x + v.y*v.y + v.z*v.z)
    if length < 1e-6:
        raise ValueError('骨段或方向长度为零，不能建立手臂 IK。')
    return unreal.Vector(v.x/length, v.y/length, v.z/length)


def _control(rig, name, transform=None, weight=False, color=None):
    settings = unreal.RigControlSettings()
    settings.control_type = unreal.RigControlType.FLOAT if weight else unreal.RigControlType.EULER_TRANSFORM
    settings.shape_visible = not weight
    settings.shape_color = color or unreal.LinearColor(0, .8, 1, 1)
    value = (unreal.RigHierarchy.make_control_value_from_float(1.0) if weight else
             unreal.RigHierarchy.make_control_value_from_euler_transform(unreal.EulerTransform(scale=[1,1,1])))
    control = rig.get_hierarchy_controller().add_control(name, unreal.RigElementKey(), settings, value)
    if not weight:
        rig.hierarchy.set_control_offset_transform(control, transform, True)
    return control


def create_arm_rig(mesh_path):
    """Import the target mesh; create targets, poles, weights and actual solve links.

    Rig global coordinates are mesh component coordinates, NOT level world space.
    Originals and player AnimClass are never changed.
    """
    api.require_sandbox()
    api._require_supported_engine()
    unreal.load_module('ControlRigDeveloper')
    mesh = api._load(mesh_path, unreal.SkeletalMesh)
    diagnosis = analyze_skeleton(api.snapshot_mesh(mesh))
    if not diagnosis['can_create']:
        raise ValueError('目标骨架诊断未通过，请先检查缺失骨骼或左右层级。')
    roles = diagnosis['role_bones']
    run = api._run_id()
    path = api._assert_output(api.OUTPUT_ROOT + '/' + run + '/CR_Arms')
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        raise ValueError('生成路径已存在，禁止覆盖。')
    rig = unreal.ControlRigBlueprintFactory.create_new_control_rig_asset(path)
    if not rig:
        raise RuntimeError('Control Rig 创建失败，请检查是否启用 ControlRig 插件。')
    report = {'kind': 'control_rig', 'rig': path, 'mesh': mesh_path, 'arms': [],
              'validation': {'solver_graph': 'pending', 'visual_pose': 'not_checked', 'gameplay': 'not_checked'}}
    try:
        rig.set_auto_vm_recompile(False)
        rig.set_preview_mesh(mesh)
        rig.get_hierarchy_controller().import_bones_from_skeletal_mesh(mesh, '')
        hierarchy = rig.hierarchy
        controller = rig.get_controller()
        previous = _unit(controller, 'RigUnit_BeginExecution', 'ForwardSolve', 0, 0)
        for index, side in enumerate(('r', 'l')):
            bones = [roles[part + '_' + side] for part in ('upperarm', 'lowerarm', 'hand')]
            transforms = [hierarchy.get_global_transform(key(b), True) for b in bones]
            a, b, c = [t.translation for t in transforms]
            upper_axis = _normalized(transforms[0].inverse_transform_location(b))
            lower_axis = _normalized(transforms[1].inverse_transform_location(c))
            dot = upper_axis.x*lower_axis.x + upper_axis.y*lower_axis.y + upper_axis.z*lower_axis.z
            if dot < .9:
                raise ValueError('双骨段主轴差异过大，当前 Basic IK 模板需要人工配置：' + side)
            line = _normalized(c-a)
            ba = b-a
            projection = line * (ba.x*line.x + ba.y*line.y + ba.z*line.z)
            bend = ba-projection
            if math.sqrt(bend.x*bend.x + bend.y*bend.y + bend.z*bend.z) < .1:
                # Straight reference arm: explicit stable hint, not a claim of ideal elbows.
                bend = unreal.Vector(0, 0, -1)
            pole_position = b + _normalized(bend)*25.0
            hand_name, pole_name, weight_name = 'hand_'+side+'_ik', 'elbow_'+side+'_pole', 'arm_'+side+'_weight'
            _control(rig, hand_name, transforms[2])
            _control(rig, pole_name, unreal.Transform(location=pole_position), color=unreal.LinearColor(.3, 1, .2, 1))
            _control(rig, weight_name, weight=True)
            y = index*520
            hand = _unit(controller, 'RigUnit_GetTransform', 'Hand_'+side, 300, y+100)
            pole = _unit(controller, 'RigUnit_GetTransform', 'Pole_'+side, 300, y+260)
            weight = _unit(controller, 'RigUnit_GetControlFloat', 'Weight_'+side, 300, y+410)
            solve = _unit(controller, 'RigUnit_TwoBoneIKSimplePerItem', 'ArmIK_'+side, 700+index*400, y)
            for node, name in ((hand, hand_name), (pole, pole_name)):
                _set(controller, node+'.Item', '(Type=Control,Name="'+name+'")')
                _set(controller, node+'.Space', 'GlobalSpace')
                _set(controller, node+'.bInitial', 'False')
            _set(controller, weight+'.Control', weight_name)
            for pin, bone in zip(('ItemA', 'ItemB', 'EffectorItem'), bones):
                _set(controller, solve+'.'+pin, '(Type=Bone,Name="'+bone+'")')
            _set(controller, solve+'.PrimaryAxis', _xyz(upper_axis))
            _set(controller, solve+'.SecondaryAxisWeight', '0.0')
            _set(controller, solve+'.PoleVectorKind', 'Location')
            _set(controller, solve+'.bEnableStretch', 'False')
            _link(controller, hand+'.Transform', solve+'.Effector')
            _link(controller, pole+'.Transform.Translation', solve+'.PoleVector')
            _link(controller, weight+'.FloatValue', solve+'.Weight')
            _link(controller, previous+'.ExecuteContext', solve+'.ExecuteContext')
            previous = solve
            report['arms'].append({'side': side, 'bones': bones, 'target_control': hand_name,
                                   'pole_control': pole_name, 'weight_control': weight_name,
                                   'primary_axis': [upper_axis.x, upper_axis.y, upper_axis.z],
                                   'reference_straight_hint': math.dist([a.x,a.y,a.z], [c.x,c.y,c.z]) >
                                       .99*(math.dist([a.x,a.y,a.z],[b.x,b.y,b.z])+math.dist([b.x,b.y,b.z],[c.x,c.y,c.z]))})
        rig.recompile_vm()
        unreal.BlueprintEditorLibrary.compile_blueprint(rig)
        if 'ERROR' in str(rig.get_editor_property('status')).upper():
            raise RuntimeError('Control Rig 编译失败。')
        api._save_new(rig)
        report['validation']['solver_graph'] = 'created_and_compiled'
        report['nodes'] = [n.get_name() for n in rig.get_model().get_nodes()]
        report['links'] = [str(l.get_source_pin().get_pin_path())+' -> '+str(l.get_target_pin().get_pin_path()) for l in rig.get_model().get_links()]
    except Exception as exc:
        report['error'] = str(exc)
        api.write_report(report)
        raise
    folder = api.write_report(report)
    job = api.load_session()
    job.update(last_report_dir=folder, last_control_rig=path)
    api.save_session(job)
    return report
