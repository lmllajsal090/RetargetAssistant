"""Name and hierarchy checks for humanoid retargeting, independent of Unreal.

The checks deliberately consume only a bone snapshot.  They cannot establish
that a reference pose, animation, contact, or root-motion configuration is good.
"""

import re


_LIMIT = (
    "这里只检查骨骼名称与层级；尚未验证参考姿势、骨骼朝向、比例、动画效果、"
    "脚部接触/滑步或 root motion，创建配置后仍需在独立验证项目中预览。"
)

_REQUIRED = (
    "pelvis", "head", "upperarm_l", "lowerarm_l", "hand_l",
    "upperarm_r", "lowerarm_r", "hand_r", "thigh_l", "calf_l", "foot_l",
    "thigh_r", "calf_r", "foot_r",
)

_LABELS = {
    "pelvis": "骨盆", "spine": "脊柱", "head": "头部",
    "upperarm_l": "左上臂", "lowerarm_l": "左前臂", "hand_l": "左手",
    "upperarm_r": "右上臂", "lowerarm_r": "右前臂", "hand_r": "右手",
    "thigh_l": "左大腿", "calf_l": "左小腿", "foot_l": "左脚",
    "thigh_r": "右大腿", "calf_r": "右小腿", "foot_r": "右脚",
}

_UE_ROLES = {"pelvis": "pelvis", "head": "head"}
for _side in ("l", "r"):
    for _part in ("upperarm", "lowerarm", "hand", "thigh", "calf", "foot"):
        _UE_ROLES[_part + _side] = _part + "_" + _side

_MIXAMO_ROLES = {"hips": "pelvis", "head": "head"}
for _side, _prefix in (("l", "left"), ("r", "right")):
    for _name, _part in (
        ("arm", "upperarm"), ("forearm", "lowerarm"), ("hand", "hand"),
        ("upleg", "thigh"), ("leg", "calf"), ("foot", "foot"),
    ):
        _MIXAMO_ROLES[_prefix + _name] = _part + "_" + _side


def _canonical(name):
    """Strip namespace prefixes, preserving the original name in all results."""
    leaf = name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    leaf = re.sub(r"[^a-z0-9]", "", leaf.casefold())
    # Some exported Mixamo files concatenate their namespace and bone name.
    return re.sub(r"^mixamorig\d*", "", leaf)


def _issue(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def _empty(snapshot):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {
        "mesh_path": snapshot.get("mesh_path"),
        "skeleton_path": snapshot.get("skeleton_path"),
        "issues": [], "root_bone": None, "pelvis": None, "chains": [],
        "family": "unknown", "bone_count": 0, "role_bones": {},
        "can_create": False,
    }


def _ancestors(index, parents):
    """Yield parents safely, including when an invalid snapshot has a cycle."""
    seen = {index}
    parent = parents[index]
    while isinstance(parent, int) and 0 <= parent < len(parents):
        if parent in seen:
            return
        seen.add(parent)
        yield parent
        parent = parents[parent]


def _ordered_spine(spine, parents):
    # Numeric suffixes are only a tie breaker.  Depth is the real order.
    return sorted(spine, key=lambda item: (len(list(_ancestors(item[1], parents))), item[0]))


def analyze_skeleton(snapshot):
    """Return conservative diagnostics and suggested chains for one snapshot.

    ``family`` is ``ue_mannequin``, ``mixamo``, ``mixed``, or ``unknown``.
    ``can_create`` only means names and hierarchy pass these static checks.
    Any error clears ``chains`` so callers cannot accidentally apply an unsafe
    partial mapping. Optional IK/twist/finger/toe bones do not count as errors.
    """
    result = _empty(snapshot)
    issues = result["issues"]
    if not isinstance(snapshot, dict):
        issues.append(_issue("error", "INVALID_SNAPSHOT", "骨架快照必须是对象。"))
        return result
    bones = snapshot.get("bones")
    if not isinstance(bones, list) or not bones:
        issues.append(_issue("error", "INVALID_BONES", "骨架快照必须包含非空 bones 列表。"))
        return result
    result["bone_count"] = len(bones)
    names, parents, canonical = [], [], []
    for index, bone in enumerate(bones):
        if not isinstance(bone, dict):
            issues.append(_issue("error", "INVALID_BONE", "索引 %d 的骨骼记录不是对象。" % index))
            names.append("")
            parents.append(None)
            canonical.append("")
            continue
        name, parent = bone.get("name"), bone.get("parent_index")
        if not isinstance(name, str) or not name.strip():
            issues.append(_issue("error", "INVALID_BONE_NAME", "索引 %d 的骨骼缺少有效名称。" % index))
            name = ""
        names.append(name)
        canonical.append(_canonical(name) if name else "")
        if type(parent) is not int or parent < -1 or parent >= len(bones):
            issues.append(_issue("error", "INVALID_PARENT_INDEX", "骨骼 %s 的父索引无效。" % (name or index)))
            parents.append(None)
        elif parent == index:
            issues.append(_issue("error", "SELF_PARENT", "骨骼 %s 将自己设为了父骨骼。" % name))
            parents.append(parent)
        else:
            parents.append(parent)

    exact_seen, canonical_seen = {}, {}
    for index, name in enumerate(names):
        if not name:
            continue
        key = name.casefold()
        if key in exact_seen:
            issues.append(_issue("error", "DUPLICATE_BONE_NAME", "骨骼名称重复：%s。" % name))
        else:
            exact_seen[key] = index
        normalized = canonical[index]
        if normalized and normalized in canonical_seen and key != names[canonical_seen[normalized]].casefold():
            issues.append(_issue("error", "AMBIGUOUS_BONE_NAME", "去除命名空间后名称冲突：%s 与 %s。" % (names[canonical_seen[normalized]], name)))
        elif normalized:
            canonical_seen[normalized] = index

    roots = [index for index, parent in enumerate(parents) if parent == -1]
    if len(roots) == 1:
        result["root_bone"] = names[roots[0]] or None
    else:
        issues.append(_issue("error", "ROOT_COUNT", "需要一棵连通骨架，实际找到 %d 个根骨骼。" % len(roots)))

    # Follow every path; this also catches a disconnected cycle beside a valid root.
    finished, cycles = set(), set()
    for start in range(len(bones)):
        path, positions = [], {}
        current = start
        while type(current) is int and 0 <= current < len(parents) and current not in finished:
            if current in positions:
                cycle = frozenset(path[positions[current]:])
                if cycle not in cycles:
                    cycles.add(cycle)
                    issues.append(_issue("error", "HIERARCHY_CYCLE", "骨骼层级存在循环：%s。" % "、".join(names[item] or str(item) for item in sorted(cycle))))
                break
            positions[current] = len(path)
            path.append(current)
            current = parents[current]
        finished.update(path)

    ue_markers = sum(name in _UE_ROLES and name != "head" for name in canonical)
    mixamo_markers = sum(name in _MIXAMO_ROLES and name != "head" for name in canonical)
    if ue_markers and mixamo_markers:
        result["family"] = "mixed"
        issues.append(_issue("error", "MIXED_NAMING_FAMILY", "同时发现 UE Mannequin 与 Mixamo 的关键骨命名，暂不自动创建；请提供明确映射。"))
    elif ue_markers:
        result["family"] = "ue_mannequin"
    elif mixamo_markers:
        result["family"] = "mixamo"
    else:
        issues.append(_issue("error", "UNSUPPORTED_SKELETON", "尚未识别为受支持的 UE Mannequin 或 Mixamo 骨架；需要人工确认骨骼映射。"))

    candidates = {role: [] for role in _REQUIRED}
    spine, neck = [], []
    for index, name in enumerate(canonical):
        roles = {_UE_ROLES.get(name), _MIXAMO_ROLES.get(name)} - {None}
        for role in roles:
            candidates[role].append(index)
        spine_match = re.fullmatch(r"spine(\d*)", name)
        if spine_match:
            spine.append((int(spine_match.group(1) or 0), index))
        if re.fullmatch(r"neck\d*", name):
            neck.append(index)

    resolved = {}
    for role, indices in candidates.items():
        if len(indices) == 1:
            resolved[role] = indices[0]
        elif not indices:
            issues.append(_issue("error", "MISSING_REQUIRED_BONE", "缺少必需骨骼：%s（%s）。" % (_LABELS[role], role)))
        else:
            issues.append(_issue("error", "AMBIGUOUS_REQUIRED_BONE", "%s匹配到多个候选：%s。" % (_LABELS[role], "、".join(names[item] for item in indices))))
    if not spine:
        issues.append(_issue("error", "MISSING_REQUIRED_BONE", "缺少必需骨骼：脊柱（spine）。"))

    # Each numbered spine role must be unique, even with different zero padding.
    spine_numbers = {}
    for number, index in spine:
        if number in spine_numbers:
            issues.append(_issue("error", "AMBIGUOUS_SPINE", "脊柱序号 %d 有多个候选：%s 与 %s。" % (number, names[spine_numbers[number]], names[index])))
        spine_numbers[number] = index
    spine = _ordered_spine(spine, parents)
    result["role_bones"] = {role: names[index] for role, index in resolved.items()}
    result["pelvis"] = result["role_bones"].get("pelvis")

    def check_path(start, end, label):
        if start is not None and end is not None and start not in set(_ancestors(end, parents)):
            issues.append(_issue("error", "BROKEN_CHAIN", "%s层级不连通：%s 必须是 %s 的祖先。" % (label, names[start], names[end])))

    if spine:
        check_path(resolved.get("pelvis"), spine[0][1], "骨盆到脊柱")
        for previous, current in zip(spine, spine[1:]):
            check_path(previous[1], current[1], "脊柱")
            if previous[0] >= current[0]:
                issues.append(_issue("error", "SPINE_ORDER", "脊柱数字顺序与父子层级不一致：%s、%s。" % (names[previous[1]], names[current[1]])))
        check_path(spine[-1][1], resolved.get("head"), "脊柱到头部")
    for side, side_label in (("l", "左"), ("r", "右")):
        arm = [resolved.get(part + "_" + side) for part in ("upperarm", "lowerarm", "hand")]
        leg = [resolved.get(part + "_" + side) for part in ("thigh", "calf", "foot")]
        if spine:
            check_path(spine[0][1], arm[0], side_label + "臂与躯干")
        check_path(resolved.get("pelvis"), leg[0], side_label + "腿与骨盆")
        for start, end in zip(arm, arm[1:]):
            check_path(start, end, side_label + "臂")
        for start, end in zip(leg, leg[1:]):
            check_path(start, end, side_label + "腿")

    # A side chain may not run through the opposite arm/leg or another limb.
    limb_groups = (
        ("upperarm_l", "lowerarm_l", "hand_l"),
        ("upperarm_r", "lowerarm_r", "hand_r"),
        ("thigh_l", "calf_l", "foot_l"),
        ("thigh_r", "calf_r", "foot_r"),
    )
    limb_indices = {resolved[role] for group in limb_groups for role in group if role in resolved}
    torso_indices = [index for _, index in spine] + neck
    if resolved.get("head") is not None:
        torso_indices.append(resolved["head"])
    for index in torso_indices:
        wrong = limb_indices.intersection(_ancestors(index, parents))
        if wrong:
            issues.append(_issue("error", "CROSSED_LIMB_HIERARCHY", "躯干/头颈骨骼 %s 挂在肢体骨骼 %s 下；无法安全创建链。" % (names[index], "、".join(names[item] for item in sorted(wrong)))))
    for group in limb_groups:
        allowed = set(group)
        for role in group:
            if role not in resolved:
                continue
            ancestors = set(_ancestors(resolved[role], parents))
            wrong = [other for other in resolved if other not in allowed and other not in ("pelvis", "head") and resolved[other] in ancestors]
            if wrong:
                issues.append(_issue("error", "CROSSED_LIMB_HIERARCHY", "%s的祖先包含其他肢体骨骼：%s；无法按名称可靠映射左右链。" % (_LABELS[role], "、".join(names[resolved[other]] for other in wrong))))

    # A named optional neck must still belong to the head's actual parent path.
    head = resolved.get("head")
    head_ancestors = set(_ancestors(head, parents)) if head is not None else set()
    valid_neck = [index for index in neck if index in head_ancestors]
    for index in neck:
        if index not in head_ancestors:
            issues.append(_issue("error", "BROKEN_NECK_CHAIN", "颈部骨骼 %s 不在头部的祖先路径上。" % names[index]))
        elif spine:
            check_path(spine[-1][1], index, "脊柱到颈部")

    ik_count = sum(name.startswith("ik") for name in canonical)
    if ik_count:
        issues.append(_issue("info", "OPTIONAL_IK_BONES", "发现 %d 个可选 IK 骨；仅凭骨骼存在无法确认求解器与脚部接触设置有效。" % ik_count))
    issues.append(_issue("info", "STATIC_ANALYSIS_LIMIT", _LIMIT))
    result["can_create"] = result["family"] in ("ue_mannequin", "mixamo") and not any(item["severity"] == "error" for item in issues)
    if result["can_create"]:
        def chain(name, start, end):
            return {"name": name, "start_bone": names[start], "end_bone": names[end]}
        if roots[0] != resolved["pelvis"]:
            result["chains"].append(chain("Root", roots[0], roots[0]))
        result["chains"].append(chain("Spine", spine[0][1], spine[-1][1]))
        head_start = min(valid_neck, key=lambda index: len(list(_ancestors(index, parents)))) if valid_neck else head
        result["chains"].append(chain("Head", head_start, head))
        for side, prefix in (("l", "Left"), ("r", "Right")):
            result["chains"].append(chain(prefix + "Arm", resolved["upperarm_" + side], resolved["hand_" + side]))
            result["chains"].append(chain(prefix + "Leg", resolved["thigh_" + side], resolved["foot_" + side]))
    return result


def suggest_chains(snapshot):
    """Return chains only when the complete static skeleton check passes."""
    return analyze_skeleton(snapshot)["chains"]


def analyze_pair(source, target):
    """Analyze two snapshots without reading or modifying any Unreal assets."""
    source_result, target_result = analyze_skeleton(source), analyze_skeleton(target)
    issues = []
    for label, result, key in (("源骨架", source_result, "source"), ("目标骨架", target_result, "target")):
        for issue in result["issues"]:
            if issue["code"] == "STATIC_ANALYSIS_LIMIT":
                continue
            copied = dict(issue)
            copied["message"] = label + "：" + copied["message"]
            copied["skeleton"] = key
            issues.append(copied)
    if isinstance(source, dict) and isinstance(target, dict):
        source_path, target_path = source.get("mesh_path"), target.get("mesh_path")
        if source_path and source_path == target_path:
            issues.append(_issue("warning", "SAME_MESH", "源与目标使用同一个网格；请确认这符合预期。"))
    if source_result["family"] != target_result["family"] and source_result["can_create"] and target_result["can_create"]:
        issues.append(_issue("warning", "CROSS_FAMILY_RETARGET", "源与目标使用不同命名体系；链名可以对应，仍需人工调整重定向姿势和运动设置。"))
    source_chains = {item["name"] for item in source_result["chains"]}
    target_chains = {item["name"] for item in target_result["chains"]}
    if "Root" in source_chains.symmetric_difference(target_chains):
        issues.append(_issue("warning", "ROOT_CHAIN_MISMATCH", "只有一侧具有独立的根骨骼链；工具不会为缺少独立根骨的一侧补造骨骼，root motion 需另行配置和验证。"))
    issues.append(_issue("info", "STATIC_ANALYSIS_LIMIT", _LIMIT))
    return {
        "source": source_result, "target": target_result, "issues": issues,
        "can_create": source_result["can_create"] and target_result["can_create"],
    }
