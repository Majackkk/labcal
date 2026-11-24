# 通用实验室校准方案与证书审查

当前版本为0.12.0，以聊天为主要入口。支持方法要求检索、证书自身诊断、用途评价、正式确认表和实验室Excel汇总；方案编制与方案验收基准独立。先看[当前工作流程](docs/workflow.md)，再按任务查阅具体执行规则。已有HTML保留原有功能。

本版按实验室汇集实际用途、设备配置、测量分工及有据选择。带明确设备身份键的新确认固定上下文，检查已有用途的处理去向；变更提示与技术结论分别保存。适用的B3副本在finish时与Word、结果记录共同归档，汇总增加“变更与跟进”。历史版本兼容读取，不自动从旧结论生成实际配置或要求全部重做。

方法原件、已拆解范围、实验室采用、设备用途及确认状态从各自记录查询，不在本说明中维护另一套实时数量。原页核对、工程验证与人员技术批准分别管理。周期、趋势和异常影响继续预留。


设备确认表按“先核清技术判断，再填表”执行；新增`./labcal confirmation analyze`，格式化与发布共用同一检查。证据不足不自动限定设备使用，详见[确认表工作流](docs/equipment-confirmation-forms.md#技术判断先于正式填表2026-11-07)。

## 从这里开始

使用支持项目技能的智能体时，可直接在聊天中说明实验室、设备或证书，以及需要检查的用途。系统先检索已采用标准和设备用途，完成证书内部检查，再进行逐项对照。以下是保留的本地HTML入口及CLI操作。

在本目录使用Python 3.11或更新版本，不需要安装核心第三方依赖。首次运行先初始化空工作空间，再登记自己的实验室：

```bash
./labcal init
./labcal lab add --id LAB_DEMO --name "示例实验室"
./labcal serve --port 8765
```

日常使用可双击桌面的“校准证书工作台.command”，或执行 `./labcal open`：自动检查并启动后台服务，再打开浏览器；服务已运行时直接复用。启动日志位于 `data/runtime/workbench.log`，可用 `./labcal install-desktop` 重新安装桌面入口。

在浏览器打开 `http://127.0.0.1:8765`。界面采用Windows XP / Luna风格：蓝色标题栏、任务导航、米灰工作区、立体按钮及紧凑列表。识别字段和诊断原文的页码位于右侧；原页默认整页预览。点击“开始诊断”显示原页扫描和绿色进度块，快速完成时保留短暂完成反馈，结果即时可读。工作台在本机运行，核心业务服务与命令行共用。证书页左侧为整份报告原页，右上为报告信息，右下为诊断结果，三块分别滚动；区域分隔条和信息、诊断表头均可拖动调整并记忆宽度，双击分隔条或使用“恢复列宽”可恢复默认。默认“紧凑对照”适合日常查看，可展开专注对照或切换完整数据列；参数、点位和对应状态均可筛选，页码定位原行或原表。实验室选择只列实际工作空间，历史迁移和待归属样本从“历史与待归属资料”查看。

也可使用命令行：

```bash
./labcal lab list
./labcal plan list --lab LAB_DEMO
./labcal certificate list --lab LAB_DEMO
./labcal doctor --lab LAB_DEMO
```

若默认Python不可用，也可执行 `python3 labcal ...`。PDF接入、定位和批注使用可选PDF依赖，扫描页OCR使用本地Tesseract及中文语言包；需在使用环境另行安装。PDF依赖可安装 `python3 -m pip install -e '.[pdf]'`。

| 文档 | 用途 |
| --- | --- |
| [当前工作流程](docs/workflow.md) | 当前入口、任务选择、两条线路、正式确认顺序及输出责任 |
| [正式确认表](docs/equipment-confirmation-forms.md) | 方法优先、器具参考、覆盖检查、Word/Excel及发布规则 |
| [不确定度评价](docs/uncertainty-decision-rules.md) | 适用分支、U/MPEV计算、判断及正式表记录 |
| [B3原页批注](docs/report-annotations.md) | 斜线、圆点避让、字段卡及U问题依据 |
| [报告工作台](docs/report-workbench.md) | 自动流程、文件结构、匹配依据和使用边界 |
| [中控台指南](docs/console-guide.md) | 多实验室、年度批次、Excel接入、方案闭环及智能体辅助 |
| [第三阶段设计](docs/phase-three/design.md) | 目录、架构、逻辑与建设边界 |
| [操作指南](docs/user-guide.md) | 从候选方案到用户确认版本和验收基准 |
| [证书工作台指南](docs/certificate-guide.md) | 接入、审查、原页定位、补充问题、核验与导出 |
| [系统架构](docs/architecture.md) | 模块、目录、实验室隔离和固定版本 |
| [数据模型](docs/data-model.md) | 参数、证据、确认、规则及交换结构 |
| [原件管理](docs/source-policy.md) | 原件接入、核验范围、版本与缺口 |
| [迁移说明](docs/migration.md) | 已迁移内容、候选状态和待完成资料 |

## 工程结构

`src/labcal/domain` 保存参数结构和确定性规则；`application` 实现业务流程；`adapters` 管理文件、数据库、迁移和导出。`schemas` 是由程序契约生成的交换格式，不能单独修改它来改变校验行为。`data` 存放按实验室隔离的原件、数据库和输出；`inbox` 存放待接入资料。这两个运行目录不随代码提交。

实际实验室、历史迁移和待归属样本由`workspace_role`分类，使用`./labcal lab list`查询。资料空间不自动成为真实实验室，资料接入也不代表设备或用途已确认。

## 验证与备份

```bash
python3 -m unittest discover -s tests -v
./labcal backup --lab LAB_DEMO --output inbox/backups/lab-demo.zip
./labcal --workspace /新的空目录 restore --archive /备份文件的绝对路径.zip
```

恢复会拒绝覆盖已有实验室或参考库。测试使用临时目录和明确标记的模拟实验室，不向真实业务库写入模拟确认记录。

模型服务未独立部署。项目内的 `labcal-calibration` 技能支持方案流程，`labcal-certificates` 支持证书解释、候选发现和工具调用。关键结果原页核验、方案确认与问题复核分别记录；系统不代填真实人员审核或自动技术批准。


当前目录职责、HTML功能与数据库对应及长期维护边界：[文件结构与工作台功能对应](docs/structure-and-function-map.md)。

## 文件与资料管理

工作台按实验室隔离，年度和批次管理固定引用。输入、处理和输出及两条业务线路采用[文件存储契约](docs/storage-layout.md)。使用`./labcal organize --lab LAB_ID`预览既有目录整理；日常仍从桌面入口或`./labcal open`使用，勿手动编辑原件对象、数据库和固定清单。

## 通过聊天检查证书自身

可直接在聊天中要求检查某份或某批校准／检定报告。使用项目labcal-self-diagnosis技能及`./labcal self-diagnosis`，保存原页证据、计算与诊断副本；无须打开网页或绑定方案。具体操作和支持范围见[聊天式证书诊断](docs/chat-self-diagnosis.md)。

## 源码发布与本地资料

公开源码不附带实验室数据库、客户证书、标准原件、认可范围附件、个人路径或历史交付物。新工作空间从空库开始；真实资料由使用者自行接入。项目技能需要支持技能和工具调用的智能体环境，CLI本身不附带模型服务。

开源前清理范围及历史处理见[源码发布说明](docs/open-source-release.md)。当前许可证尚未选定，后续由维护者选择并补齐LICENSE。
