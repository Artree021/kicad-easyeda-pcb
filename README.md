# KiCad → 嘉立创EDA PCB Skill

把画板过程中的工程经验，整理成 AI 能持续执行、复查和复用的工作流。

这是从一次实际 PCB 设计、迁移和制造交付过程中提炼的 skill：**人提出要求、传授方法并检查纠偏，AI 通过工程工具落实设计并留下验证依据。** 覆盖原理图审查、布局与逐网布线、KiCad 到嘉立创EDA的迁移核对，以及双面 SMT 交付检查。

**作者：**[树生_Artree](https://space.bilibili.com/1303002512) · **许可：**[MIT](LICENSE)

English: A reusable agent skill for evidence-based KiCad PCB design, EasyEDA migration review and manufacturing handoff. Includes a read-only Python inspector for `.epro2` exports.

## 从哪里开始

- 给 AI 的入口：[SKILL.md](SKILL.md)
- 设计与逐网布线：[design-and-review.md](references/design-and-review.md)
- 嘉立创EDA原生工程与迁移：[easyeda-native.md](references/easyeda-native.md)
- 制造与双面贴装交付：[manufacturing-handoff.md](references/manufacturing-handoff.md)
- 环境、依赖与检查证据：[local-environment-and-evidence.md](references/local-environment-and-evidence.md)

## 能帮你做什么

| 阶段 | 工作重点 |
| --- | --- |
| 需求和原理图 | 明确器件、供电与接口约束；核对引脚、网络和图面可读性 |
| 布局和布线 | 摆放时考虑出口、参考地和换层；逐网规划，复查分支、转角及实际铜边 |
| KiCad → 嘉立创EDA | 对比引脚网络、原生几何、铺铜、层厚单位、封装与 3D 模型 |
| 制造和装配 | 核对同版本 Gerber、钻孔、BOM、CPL、板面及底面器件贴装方向 |
| 记录和复用 | 保留实际命令、源文件版本、哈希、结果及未完成检查 |

它提供工程方法和一个离线检查助手。执行设计还需要能使用相应工具的 AI 环境、EDA 软件以及人的工程判断。零 DRC、导入成功、3D 看起来正确和实板通过测试是不同状态，应分别记录。

## 安装

将**整个仓库内容**放进一个名为 `kicad-easyeda-pcb` 的 skill 文件夹，确认 `SKILL.md` 直接位于该文件夹内，而不是多套一层目录。

使用 Codex 默认个人目录时，结构如下；如果已配置自定义 skills 目录，则放到自己的配置位置：

```text
~/.codex/skills/kicad-easyeda-pcb/
├── SKILL.md
├── agents/openai.yaml
├── references/
└── scripts/inspect_epro2.py
```

可以使用 GitHub 的 **Code → Download ZIP** 下载并解压。已有同名自定义 skill 时先比较内容，保留自己的修改。

也可以直接克隆（目标目录尚不存在时）：

```sh
git clone https://github.com/Artree021/kicad-easyeda-pcb.git ~/.codex/skills/kicad-easyeda-pcb
```

Windows PowerShell：

```powershell
git clone https://github.com/Artree021/kicad-easyeda-pcb.git "$env:USERPROFILE/.codex/skills/kicad-easyeda-pcb"
```

## 怎么使用

在新任务中调用 `$kicad-easyeda-pcb`，同时给出工程位置、目标和允许操作的范围。例如：

```text
使用 $kicad-easyeda-pcb 审查我提供的 KiCad 工程。
先检查供电、接口保护、器件引脚与布局回流，列出问题和验证依据。
这次只审查，不修改工程、不下单。
```

继续布线时，可以具体描述自己的约束：

```text
使用 $kicad-easyeda-pcb 继续当前 PCB 布线。
保留已确认的板框、器件和层叠；逐网规划并落实走线，复查铜边圆角和连通。
本项目不使用自动布线。保存修改，并记录实际检查结果及待办。
```

迁移和制造审查也可以独立使用：

```text
使用 $kicad-easyeda-pcb 对比原 KiCad 工程与导出的嘉立创EDA原生工程。
核对引脚网络、铜几何、层厚、模型绑定与底面贴装方向；列出真实差异。
```

例子中的“禁止自动布线”是该任务的用户约束，其他项目可自行决定。工具版本、层数、尺寸、料号和工艺也应按自己的项目确认。

## 需要哪些工具

| 使用范围 | 依赖 |
| --- | --- |
| 读取 skill 工作指引 | 支持 skill 或能读取 Markdown 指令的 AI 工具 |
| KiCad 工程操作 | KiCad；按具体工作需要使用 `kicad-cli` 或兼容的 `pcbnew` Python 环境 |
| 嘉立创EDA原生自动化 | 嘉立创EDA专业版，以及当前环境可用的扩展/API 通道或兼容桥接工具 |
| 包内离线检查脚本 | Python 3.10+，仅使用标准库，无需 pip 安装依赖 |

本仓库不打包 EDA 软件、第三方 SDK、`easyeda-api` 桥接代码、私人设计工程、制造资料、账号数据或实验室标志。第三方工具需从各自来源安装并遵守其许可。没有原生接口时，可先完成离线审查并明确哪些步骤仍需在编辑器中确认。

## 离线检查 `.epro2`

在仓库根目录运行：

```sh
python scripts/inspect_epro2.py --help
python scripts/inspect_epro2.py board.epro2 --output work/inventory.json
python scripts/inspect_epro2.py board.epro2 --baseline work/inventory.json --output work/after.json
```

脚本不会连接账号或修改输入工程。它读取导出包中**唯一的一份 `.epru`**，统计图元、元件与模型绑定、铜区和物理层，并对各类原始有效记录生成摘要。可与同一工程的前一次报告比较。

仅在确认源数据单位后指定 `--thickness-unit mil` 或 `--thickness-unit mm`；默认 `unknown` 不猜测单位。单份 `.epru` 解压大小上限为 256 MiB。

输出会隐藏显示字段中的本地绝对路径，并省略文档头账号元数据，但仍可能包含工程标识、位号和料号。分享报告前自行检查。摘要基于原始记录，数据相同或图元数量相同不代表电气、铺铜、SMT 朝向或制造正确。

## 验证

```sh
python -B tests/test_inspect_epro2.py
```

测试使用临时目录中的合成导出样例，覆盖记录修订与删除、单位转换、路径脱敏、基线比较、输入覆盖保护、错误输入和文档链接。它验证离线助手的行为，不代表所有 EDA 版本或实际板卡均已验收。

## 反馈和贡献

欢迎提交 Issue 或 Pull Request。说明工具版本、触发步骤、预期与实际结果，并尽可能提供最小复现。请先去掉账号、订单、私人路径和未获授权的设计资料。

优先把经过验证的方法写进对应参考文件；保持 `SKILL.md` 简洁，不把一次项目的临时坐标或个人偏好变成通用规则。修改脚本后运行上面的测试。

## 许可

本仓库原创工作指引与脚本采用 [MIT License](LICENSE)，可使用、修改和分发，须保留版权和许可声明。第三方软件、品牌及用户自己提供的设计文件不因本仓库的许可而改变权利归属。
