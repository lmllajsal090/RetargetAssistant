"""Deterministic contact diagnostics. No UE import and no asset writes."""
import math


def vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("位置必须包含三个有限数值。")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError("位置必须包含三个有限数值。")
    return tuple(float(x) for x in value)


def distance(a, b):
    return math.dist(vector(a), vector(b))


def reach_metrics(shoulder, elbow, wrist, goal):
    upper = distance(shoulder, elbow)
    lower = distance(elbow, wrist)
    if min(upper, lower) < 1e-5:
        raise ValueError("手臂骨段长度为零，不能计算可达范围。")
    target = distance(shoulder, goal)
    spare = upper + lower - target
    minimum = abs(upper - lower)
    return {"upper_cm": upper, "lower_cm": lower, "target_cm": target,
            "spare_cm": spare, "reachable": minimum - 1e-4 <= target <= upper + lower + 1e-4,
            "extension_ratio": target / (upper + lower),
            "wrist_error_cm": distance(wrist, goal)}


def issue(code, message, severity="warning", evidence=None):
    return dict(code=code, message=message, severity=severity, evidence=evidence)


def diagnose_contact(snapshot):
    """Input is an actual snapshot, never a claim of visual/gameplay acceptance."""
    findings = []
    owners = snapshot.get("grip_owners", [])
    if len(owners) != 1:
        findings.append(issue("GRIP_SOCKET_MISSING" if not owners else "GRIP_SOCKET_AMBIGUOUS",
                              "握点必须唯一归属于实际武器的一个组件；请核对组件和 Socket。", "error", owners))
    if snapshot.get("expected_weapon_class") and snapshot.get("expected_weapon_class") != snapshot.get("weapon_class"):
        findings.append(issue("ACTIVE_WEAPON_MISMATCH", "配置的武器类与实际装备类不同。", "error"))
    if snapshot.get("expected_anim_class") and snapshot.get("expected_anim_class") != snapshot.get("anim_class"):
        findings.append(issue("ACTIVE_ANIM_MISMATCH", "当前动画实例与配置的动画类不同。", "error"))
    if snapshot.get("weapon_socket_exists") is False:
        findings.append(issue("WEAPON_SOCKET_MISSING", "实际角色 Mesh 中没有挂枪 Socket。检查 Mesh Only 归属。", "error"))
    if snapshot.get("position_space") and snapshot.get("position_space") != snapshot.get("effector_space"):
        findings.append(issue("POSITION_SPACE_MISMATCH", "目标位置的生成空间与 IK 接收空间不同。", "error"))
    if snapshot.get("rotation_space") and snapshot.get("rotation_space") != snapshot.get("rotation_input_space"):
        findings.append(issue("ROTATION_SPACE_MISMATCH", "旋转数据空间与手腕旋转节点不一致。", "error"))
    metrics = {}
    for side, chain in snapshot.get("arms", {}).items():
        try:
            metric = reach_metrics(chain["shoulder"], chain["elbow"], chain["wrist"], chain["goal"])
            metrics[side] = metric
            if not metric["reachable"]:
                findings.append(issue("ARM_UNREACHABLE", side + "臂目标超出无拉伸可达范围。先检查整枪与右手位置。", "error", metric))
            elif metric["extension_ratio"] > .97:
                findings.append(issue("ARM_NEAR_STRAIGHT", side + "臂接近伸直，移动和瞄准中应继续检查。", evidence=metric))
            if metric["wrist_error_cm"] > 1.0:
                findings.append(issue("WRIST_TARGET_ERROR", side + "手腕与目标相差超过 1 cm。检查目标、权重和执行顺序。", evidence=metric))
        except (KeyError, TypeError, ValueError) as exc:
            findings.append(issue("ARM_SAMPLE_INVALID", side + "臂数据不完整或无效：" + str(exc), "error"))
    for node in snapshot.get("nodes", []):
        if node.get("alpha_driven") is not True and node.get("alpha") == 0:
            findings.append(issue("IK_ALPHA_ZERO", "节点权重为零：" + node["name"]))
        if node.get("translation_mode") == "Ignore" and any(node.get("translation", [0, 0, 0])):
            findings.append(issue("TRANSLATION_IGNORED", "平移数值被 Ignore：" + node["name"]))
        if node.get("rotation_space") and node.get("rotation_input_space") and node["rotation_space"] != node["rotation_input_space"]:
            findings.append(issue("ROTATION_SPACE_MISMATCH", "旋转空间不匹配：" + node["name"], "error"))
    return {"kind": "ik_contact", "issues": findings, "metrics": metrics,
            "can_calibrate": not any(i["severity"] == "error" for i in findings),
            "validation": {"pose_snapshot": "checked", "visual_clipping": "not_checked",
                           "finger_contact": "not_checked", "movement": "not_checked"}}


def validate_edits(edits):
    """Only explicit constant pose parameters; never graph execution or arbitrary code."""
    allowed = {"EffectorLocation": 3, "JointTargetLocation": 3,
               "Translation": 3, "Rotation": 3, "Alpha": 1}
    if not isinstance(edits, list) or not edits:
        raise ValueError("请提供非空 edits 列表。")
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("node"), str) or not edit["node"]:
            raise ValueError("每项调整需要完整节点名称。")
        pin = edit.get("pin")
        if pin not in allowed:
            raise ValueError("只允许校准目标位置、关节位置、平移、旋转和 Alpha。")
        key = edit["node"], pin
        if key in seen:
            raise ValueError("同一引脚不能重复调整。")
        seen.add(key)
        if not isinstance(edit.get("expected"), str):
            raise ValueError("必须提供诊断时的 expected 引脚值以防覆盖新修改。")
        value = edit.get("value")
        if allowed[pin] == 3:
            vector(value)
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Alpha 必须位于 0 到 1。")
    return edits


def pin_text(edit):
    return str(float(edit["value"])) if edit["pin"] == "Alpha" else ",".join("%.6f" % x for x in vector(edit["value"]))
