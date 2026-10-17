"""Register only in an explicitly configured sandbox or installed project."""
import unreal

try:
    from retarget_assistant import unreal_adapter
    unreal_adapter.require_sandbox()
    from retarget_assistant import menu
    menu.install_when_ready()
except Exception as exc:
    unreal.log("RetargetAssistant: " + str(exc))
