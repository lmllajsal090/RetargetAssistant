"""Small native editor workflow, driven by Content Browser selections."""

import os
import traceback
from pathlib import Path

import unreal

from . import unreal_adapter as api
from . import __version__

MENU_NAME = "LevelEditor.MainMenu.Tools.RetargetAssistant"
_startup_handle = None
_startup_attempts = 0


def _message(text):
    unreal.EditorDialog.show_message("动画重定向助手", text, unreal.AppMsgType.OK)


def _pick_mesh():
    assets = unreal.EditorUtilityLibrary.get_selected_assets()
    if len(assets) != 1 or not isinstance(assets[0], unreal.SkeletalMesh):
        raise ValueError("请在内容浏览器中只选中一个 Skeletal Mesh（骨骼网格），然后重试。")
    return assets[0].get_path_name()


def dispatch(action):
    try:
        _dispatch(action)
    except Exception as exc:
        unreal.log_error(traceback.format_exc())
        _message(str(exc))


def _dispatch(action):
    api.require_sandbox()
    job = api.load_session()
    if action.startswith('ik_') or action.startswith('rig_'):
        from . import workbench, control_rig
        if action == 'ik_select':
            assets = unreal.EditorUtilityLibrary.get_selected_assets()
            if len(assets) != 1 or not isinstance(assets[0], unreal.AnimBlueprint):
                raise ValueError('请只选择实际使用的 Animation Blueprint。')
            job['ik_blueprint'] = assets[0].get_path_name()
            api.save_session(job)
            _message('已选择 IK 动画蓝图：\n'+job['ik_blueprint'])
        elif action == 'ik_inspect':
            report = workbench.inspect_blueprint(job.get('ik_blueprint', ''))
            folder = workbench.save_diagnosis(report)
            os.startfile(str(Path(folder)/'AI诊断报告.md'))
        elif action == 'ik_aim':
            assets = unreal.EditorUtilityLibrary.get_selected_assets()
            if len(assets) != 1 or not isinstance(assets[0], unreal.BlendSpace):
                raise ValueError('请选择一个 AimOffset / BlendSpace，检查中心样本与加法参考。')
            folder = workbench.save_diagnosis(workbench.inspect_aim_offset(assets[0].get_path_name()))
            os.startfile(str(Path(folder)/'AI诊断报告.md'))
        elif action in ('ik_contact', 'ik_pie'):
            if action == 'ik_pie':
                world = unreal.EditorLevelLibrary.get_game_world()
                character = unreal.GameplayStatics.get_player_character(world, 0) if world else None
                if not character:
                    raise ValueError('请先启动 PIE，并确认本地玩家是 Character。')
                report = workbench.capture_contact(character, expected_anim_blueprint=job.get('ik_blueprint',''))
            else:
                actors = list(unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors())
                characters = [a for a in actors if isinstance(a, unreal.Character)]
                if len(characters) != 1 or len(actors) > 2:
                    raise ValueError('请在场景中选择一个 Character，可同时选择一把武器。')
                weapon = next((a for a in actors if a != characters[0]), None)
                report = workbench.capture_contact(characters[0], weapon, expected_anim_blueprint=job.get('ik_blueprint',''))
            folder = workbench.save_diagnosis(report)
            os.startfile(str(Path(folder)/'AI诊断报告.md'))
        elif action == 'ik_preset':
            os.startfile(workbench.create_preset(job.get('ik_blueprint', '')))
        elif action == 'ik_apply':
            report = workbench.apply_preset(workbench.load_config(job.get('ik_preset', '')))
            unreal.EditorAssetLibrary.sync_browser_to_objects([report['copy']])
            _message('已生成 IK 校准副本，原动画蓝图与玩家配置未改变。\n'+report['copy'])
        elif action == 'ik_restore':
            result = workbench.restore_generated(workbench.load_config(job.get('last_ik_restore', '')))
            _message('已局部恢复生成副本中的 '+str(result['count'])+' 个引脚。后续有冲突时会停止恢复。')
        elif action == 'rig_create':
            report = control_rig.create_arm_rig(job.get('target_mesh', ''))
            unreal.EditorAssetLibrary.sync_browser_to_objects([report['rig']])
            _message('已建立双臂 Control Rig。打开后可操作双手目标、肘部与独立权重；先预览，再接入测试角色。\n'+report['rig'])
        elif action in ('rig_open', 'ik_open'):
            path = job.get('last_control_rig' if action == 'rig_open' else 'last_ik_copy')
            if not path:
                raise ValueError('请先生成对应资产。')
            unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([unreal.load_asset(path)])
        return
    if action in ("source", "target"):
        job[action + "_mesh"] = _pick_mesh()
        job.pop("setup", None)
        if action == "source":
            job["animations"] = []
        api.save_session(job)
        _message(("源角色" if action == "source" else "目标角色") + "已选定：\n" + job[action + "_mesh"])
    elif action == "animations":
        assets = unreal.EditorUtilityLibrary.get_selected_assets()
        if not assets or any(not isinstance(a, unreal.AnimSequence) for a in assets):
            raise ValueError("请只选中要转换的 Animation Sequence（动画序列）；首版暂不处理动画蓝图或混合空间。")
        candidate = dict(job, animations=list(dict.fromkeys(a.get_path_name() for a in assets)))
        report = api.inspect_job(candidate)
        if any(i["severity"] == "error" for i in report["issues"]):
            raise ValueError("源动画骨架不匹配，请选择源角色对应的动画。")
        api.save_session(candidate)
        _message("已选择 " + str(len(assets)) + " 段动画。下一步运行“检查并生成 AI 报告”。")
    elif action == "inspect":
        report = api.inspect_job(job)
        folder = api.write_report(report)
        job["last_report_dir"] = folder
        api.save_session(job)
        _message(("骨架静态检查通过。" if report["can_create"] else "骨架检查发现阻塞问题。") + "\n报告：" + folder)
        os.startfile(str(Path(folder) / "AI诊断报告.md"))
    elif action == "setup":
        report = api.create_setup(job)
        _message("已生成源/目标 IK Rig 和 IK Retargeter。\n下一步打开预览，检查手臂姿势、脚接触与角色比例。\n" + report["output_path"])
    elif action == "preview":
        path = job.get("setup", {}).get("retargeter")
        if not path:
            raise ValueError("请先建立重定向配置。")
        unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([unreal.load_asset(path)])
    elif action == "export":
        if not job.get("setup"):
            raise ValueError("请先建立重定向配置并打开预览。")
        answer = unreal.EditorDialog.show_message(
            "导出重定向动画", "将使用当前配置，把所选动画导出到本次生成目录。\n请先在预览中检查姿势；导出后仍需要检查脚接触、Root Motion 和武器握持。\n继续导出？",
            unreal.AppMsgType.YES_NO, unreal.AppReturnType.NO)
        if answer != unreal.AppReturnType.YES:
            return
        report = api.export_animations(job)
        unreal.EditorAssetLibrary.sync_browser_to_objects([item["path"] for item in report["outputs"]])
        _message("已导出 " + str(len(report["outputs"])) + " 段新动画。\n请预览验收；AI 报告可从菜单打开。")
    elif action == "report":
        folder = job.get("last_report_dir") or job.get("setup", {}).get("report_dir")
        if not folder:
            raise ValueError("请先生成检查报告。")
        report = Path(folder).resolve() / "AI诊断报告.md"
        if not report.is_relative_to(api.require_sandbox()):
            raise ValueError("报告路径不属于当前工程。")
        os.startfile(str(report))
    elif action == "status":
        _message("源角色：" + job.get("source_mesh", "未选择") + "\n目标角色：" + job.get("target_mesh", "未选择")
                 + "\n动画数量：" + str(len(job.get("animations", [])))
                 + "\n配置：" + job.get("setup", {}).get("retargeter", "未建立")
                 + ("\n当前模式：已授权的项目安装。只在助手的新目录中生成副本。"
                    if api.workspace_mode() == "project" else "\n当前模式：独立测试工程。"))


def install(quiet=False):
    """Idempotent registration; no files or assets are written."""
    menus = unreal.ToolMenus.get()
    tools_menu = menus.find_menu("LevelEditor.MainMenu.Tools")
    if not tools_menu:
        if not quiet:
            unreal.log("RetargetAssistant: waiting for the Level Editor menu.")
        return False
    if menus.find_menu(MENU_NAME):
        menus.remove_menu(MENU_NAME)
    menu = tools_menu.add_sub_menu("RetargetAssistant", "Tools", "RetargetAssistant", "动画重定向助手")
    entries = [
        ("source", "1. 把选中的网格设为源角色", "Setup"),
        ("target", "2. 把选中的网格设为目标角色", "Setup"),
        ("animations", "3. 选择要转换的动画序列", "Setup"),
        ("inspect", "4. 检查并生成 AI 报告", "Setup"),
        ("setup", "5. 建立重定向配置", "Retarget"),
        ("preview", "6. 打开姿势与动画预览", "Retarget"),
        ("export", "7. 导出新动画", "Retarget"),
        ("ik_select", "选择实际使用的动画蓝图", "IK"),
        ("ik_inspect", "检查 IK 节点、空间和权重", "IK"),
        ("ik_aim", "检查 AimOffset 中心与加法参考", "IK"),
        ("ik_contact", "检查所选角色与武器握点", "IK"),
        ("ik_pie", "采样当前 PIE 玩家握持", "IK"),
        ("ik_preset", "导出可编辑的 IK 校准配置", "IK"),
        ("ik_apply", "按配置生成 IK 校准副本", "IK"),
        ("ik_open", "打开最近的 IK 校准副本", "IK"),
        ("ik_restore", "局部恢复最近副本的 IK 参数", "IK"),
        ("rig_create", "为目标角色生成双臂 Control Rig", "Rig"),
        ("rig_open", "打开最近的 Control Rig", "Rig"),
        ("report", "打开最近的 AI 报告", "Info"),
        ("status", "查看当前选择与状态", "Info"),
    ]
    for section, label in (("Setup", "选择和检查"), ("Retarget", "配置与导出"),
                           ("IK", "持枪 IK 诊断与校准"), ("Rig", "Control Rig"), ("Info", "帮助")):
        menu.add_section(section, label)
    for action, label, section in entries:
        entry = unreal.ToolMenuEntry(name="RetargetAssistant_" + action, type=unreal.MultiBlockType.MENU_ENTRY)
        entry.set_label(label)
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, "", "import retarget_assistant.menu as ra; ra.dispatch(" + repr(action) + ")")
        menu.add_menu_entry(section, entry)
    menus.refresh_all_widgets()
    unreal.log("RetargetAssistant " + __version__ + ": Tools > 动画重定向助手")
    return True


def install_when_ready():
    """Python startup can precede the Level Editor's Tools menu."""
    global _startup_handle, _startup_attempts
    if install(quiet=True):
        return
    _startup_attempts = 0
    if _startup_handle is not None:
        return

    def on_tick(delta_time):
        global _startup_handle, _startup_attempts
        _startup_attempts += 1
        if install(quiet=True) or _startup_attempts >= 300:
            unreal.unregister_slate_post_tick_callback(_startup_handle)
            _startup_handle = None

    _startup_handle = unreal.register_slate_post_tick_callback(on_tick)
