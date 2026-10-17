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
        ("report", "打开最近的 AI 报告", "Info"),
        ("status", "查看当前选择与状态", "Info"),
    ]
    for section, label in (("Setup", "选择和检查"), ("Retarget", "配置与导出"), ("Info", "帮助")):
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
