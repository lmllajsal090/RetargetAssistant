"""Bind an already installed plugin to one project; never edit assets or uproject."""

import argparse
import json
from pathlib import Path


def configure_project(project_file):
    project_file = Path(project_file).expanduser().resolve(strict=True)
    if not project_file.is_file() or project_file.suffix.lower() != ".uproject":
        raise ValueError("Expected an existing .uproject file.")
    json.loads(project_file.read_text(encoding="utf-8-sig"))
    project = project_file.parent
    descriptor = project / "Plugins" / "RetargetAssistant" / "RetargetAssistant.uplugin"
    if not descriptor.is_file():
        raise ValueError("Install the plugin in Plugins/RetargetAssistant first.")
    config = (project / "Config").resolve()
    for child in (config, (project / "Content").resolve(), (project / "Saved").resolve()):
        if not child.is_relative_to(project):
            raise ValueError("Project directories must stay inside the project.")
    if (config / "RetargetAssistantSandbox.json").exists():
        raise ValueError("A sandbox policy already exists; inspect it instead of adding another policy.")
    marker = config / "RetargetAssistantProject.json"
    if not marker.resolve().is_relative_to(config):
        raise ValueError("Policy file must stay inside Config.")
    policy = {"allow_generated_assets": True, "project_file": str(project_file)}
    if marker.exists():
        current = json.loads(marker.read_text(encoding="utf-8-sig"))
        if (current.get("allow_generated_assets") is True
                and Path(current.get("project_file", "")).resolve() == project_file):
            return marker
        raise ValueError("A different policy already exists; nothing was overwritten.")
    config.mkdir(parents=True, exist_ok=True)
    with marker.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")
    return marker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_file", help="Absolute or relative path to your .uproject")
    args = parser.parse_args()
    try:
        marker = configure_project(args.project_file)
    except (OSError, ValueError) as exc:
        parser.exit(1, "Configuration stopped: " + str(exc) + "\n")
    print("Project policy ready: " + str(marker))
    print("Enable RetargetAssistant in the editor's Plugins window and restart.")


if __name__ == "__main__":
    main()
