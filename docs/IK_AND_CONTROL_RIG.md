# IK 与 Control Rig 使用说明（0.3）

这次扩展来自人形角色替换后的持枪调试：实际蓝图不一致、握点组件选错、Mesh Only Socket、旋转空间不匹配、Ignore 平移、Alpha 为零、右手推远导致左臂够不到，以及 AimOffset 中心与参考动作不同。

## 1. 入口与基本顺序

UE 5.8：**Tools → 动画重定向助手**。原重定向步骤 1～7 保留，新增“持枪 IK 诊断与校准”和“Control Rig”分组。

推荐顺序：

1. 重定向并预览基础待机、移动、瞄准，先修正明显的基础姿势问题。
2. 选择实际使用的动画蓝图，检查节点、目标空间和权重。
3. 在场景或 PIE 采样实际角色与武器，核对握点归属与双臂可达范围。
4. 用新副本校准右臂和整枪，再调整左手目标。
5. 需要图形控制器时，为目标角色生成双臂 Control Rig。
6. 预览并测试移动、瞄准、跳跃和手掌接触后，再决定正式接入。

所有生成资产仍在新建的 `/Game/RetargetAssistant/Runs/<任务编号>` 目录；不会自动替换玩家、修改源动画或修改原武器 Socket。

## 2. 检查实际动画蓝图

在内容浏览器选择一个 Anim Blueprint，点击 **选择实际使用的动画蓝图**，然后 **检查 IK 节点、空间和权重**。

报告列出 Two Bone IK、Transform (Modify) Bone 和 Control Rig 节点，保存完整节点名、参数和引脚是否连线。重点检查：

- 常量 Alpha 是否为 0；若 Alpha 由变量驱动，不把常量默认值误当成运行值。
- Translation 不为零但 Mode = Ignore，提示该平移没有参与计算。
- 左手 IK 的受控骨骼与参考骨骼分别是什么。
- 可追踪到的 `Transform to Bone Space.Out Rotation` 若进入世界旋转节点，会报告空间不匹配。

**不能静态确认的输入空间会保持未知。** 例如数据经过任意自定义函数时，工具不会凭变量名字断言它是世界空间。

仅选择蓝图不代表游戏正在用它。先核对玩家 Mesh 的 Anim Class，再用 PIE 采样确认运行实例。

### AimOffset 检查

选择一个 AimOffset / BlendSpace，点击 **检查 AimOffset 中心与加法参考**。工具列出样本动画和参考动画，对“唯一零坐标中心与加法参考不是同一资产”给出提示。

不同资产也可能包含相同姿势，所以此项是排查线索，不会仅凭路径差异判定动画错误；仍需预览或采样真实加法姿势。

## 3. 采样角色和武器

### 场景模式

在场景 Outliner 选择一个 Character，可同时选择一把武器，再点 **检查所选角色与武器握点**。没有显式选择武器时，优先读取角色的 `EquippedWeapon`；仅有一个附着 Actor 时才尝试作为武器，不能唯一确认时会要求明确选择。

### PIE 模式

先 Play，再点击 **采样当前 PIE 玩家握持**，采样本地玩家 0 的实际 Character。没有游戏世界或玩家不是 Character 时会停止，避免把编辑器预览实例当成玩家。

默认插槽名为 `LeftHandGrip` 和 `WeaponSocket`。项目使用其他命名时可通过 Python API 显式指定：

```python
from retarget_assistant import workbench
report = workbench.capture_contact(
    character, weapon,
    grip_socket="LeftHandGrip",
    grip_component="MagazineMesh",
    weapon_socket="WeaponSocket")
workbench.save_diagnosis(report)
```

这里的 `character`、`weapon` 必须是当前工程内的实际 Actor 对象，不是资产路径字符串。

报告内容：

- 实际 Actor、世界、Mesh、Anim Class 和武器类路径。
- 哪个武器组件拥有握点；缺失和重名均报错，不使用 Actor 原点兜底。
- 角色 Mesh / Skeleton 上 Socket 的归属。
- 握点世界 Transform、相对右手的位置、左右臂骨段和目标距离。
- 超出最大或最小可达范围、接近完全伸直、手腕目标偏差。
- 动画实例公开了 `LeftHandGripWorldTransform` 时，对比它与真实握点，帮助发现函数覆盖或数据链错误。

这是单帧采样，不会常驻 Tick，也不会自动判定穿模、自然度或手指握合。角色未运行动画的场景采样可能是参考姿势；最终应使用 PIE。

## 4. 校准参数副本与局部恢复

点击 **导出可编辑的 IK 校准配置**，在默认文本编辑器里打开新 JSON。`available_pins` 列出没有被连线驱动的受支持参数。复制需要修改的项目到 `edits` 并填入 `value`：

```json
{
  "schema_version": 1,
  "blueprint": "/Game/YourFolder/ABP_Character",
  "edits": [
    {
      "node": "AnimGraph.AnimGraphNode_TwoBoneIK_1",
      "pin": "EffectorLocation",
      "expected": "0.000000,0.000000,0.000000",
      "value": [4.2, -8.7, -6.1]
    }
  ]
}
```

示例不是通用持枪参数。带子图路径的节点名和 `expected` 要复制当前导出值，避免不同子图中的同名节点混淆；坐标空间必须先在报告中确认。

保存后点击 **按配置生成 IK 校准副本**。工具先检查全部输入，再复制动画蓝图并改常量参数，编译、保存并记录差异。

- 支持 EffectorLocation、JointTargetLocation、Translation、Rotation、Alpha。
- 不自动拆掉连线，不覆盖变量驱动的输入，不执行任意配置代码。
- 检查 `expected` 防止诊断后源参数已变化。
- 不自动插入缺失的 IK 节点，不重建任意游戏事件图；没有节点时可使用下节的新 Control Rig。
- 副本不会自动成为玩家 Anim Class；使用测试角色预览后再接入。

**局部恢复最近副本的 IK 参数** 只修改本次生成副本的相应引脚，并先核对值仍是本次修改后的值。之后有同一引脚改动会停止恢复；其他引脚不在恢复范围内。每次操作的报告目录同时保存 `restore.json`。

## 5. 生成和使用双臂 Control Rig

先通过原菜单第 2 项选定目标 Skeletal Mesh，再点击 **为目标角色生成双臂 Control Rig**，然后 **打开最近的 Control Rig**。

生成的内容不是空资产：

| 元素 | 用途 |
|---|---|
| 导入的目标骨骼 | 根据目标 Skeletal Mesh 的骨骼与参考姿势建立 |
| `hand_r_ik` / `hand_l_ik` | 双手目标的位置和旋转 |
| `elbow_r_pole` / `elbow_l_pole` | 左右肘部弯曲提示 |
| `arm_r_weight` / `arm_l_weight` | 双臂独立权重；通常使用 0～1 |
| Forward Solve → 两个 Basic IK | 真正执行双臂求解；不启用骨段拉伸 |

在 Rig 预览中移动手部控制器，手臂会跟随；移动肘部控制器改变弯曲方向。直臂参考姿势只能提供稳定的初始肘部提示，需要根据实际持枪姿势继续校准。

**Control Rig 的 Global Space 是 Rig / Mesh 的组件坐标，不是关卡世界坐标。** 将武器 Socket 世界 Transform 作为目标前，应相对角色 Mesh 世界 Transform 做转换。

### 接入动画蓝图时

在测试动画蓝图中加入 Control Rig 节点，选择本次生成的 Rig，让已有基础姿势进入该节点，再输出最终姿势。按 UE Control Rig 节点的控制器映射 / 引脚设置传入双手目标和权重，核对是否传入输入姿势。

本模板默认控制器位于目标参考姿势，适合在预览或 Sequencer 中操作。**不能直接把默认参考目标覆盖到所有移动姿势后就当成最终持枪逻辑。** 接入游戏时应提供随当前姿势更新的目标；右手与枪先确定，左手再追踪握点。

不要同时全权重启用旧左手 IK 和新 Rig 对同一条手臂求解。先在测试副本中明确由哪套逻辑负责，换弹时再按需求降低相应手臂权重。

当前不自动烘焙手指、不自动做网格碰撞避障，也不重写已有足部 Control Rig。自然持枪和脚接触需画面验收。

## 6. 源码、证据与版本

- `Content/Python/retarget_assistant/ik_core.py`：可达范围、空间和校准输入诊断。
- `workbench.py`：真实资产 / Actor 读取、参数副本、冲突保护和局部恢复。
- `control_rig.py`：Control Rig 层级、控制器和真实求解图生成。
- `tests/test_ik_core.py`：可脱离 UE 运行的诊断边界测试。
- [0.3 验证记录](VALIDATION_0_3_0.md)：本地引擎验证范围与可复现的验收步骤。

公开仓库提供通用安装与操作说明，不包含本机启动器、安装历史、原始工程报告或角色素材。0.2 工程绑定规则继续有效。

接口依据：[Epic Control Rig Python](https://dev.epicgames.com/documentation/en-us/unreal-engine/control-rig-python-scripting-in-unreal-engine)、本机 UE 5.8 的 ControlRig / RigVM / BlueprintEditorLibrary 实际头文件和运行验证。
