# 系统架构

当前执行入口、任务选择及输出职责见[当前工作流程](workflow.md)。采用一个本地Python应用及按实验室隔离的SQLite存储；业务通过固定版本和只读引用衔接。带阶段/版本的下文章节保留演进背景，不把早期HTML流程当作聊天确认的前置步骤。

2026-11-10多实验室增量升级沿用以上分层和数据库：business_context汇集既有方法用途、设备台账及meta中的版本化上下文说明；confirmation_updates比较依赖并管理有原件引用的问题进展；confirmation_delivery固定同次交付物。实际用途只有method_uses一处可编辑来源，实际配置只有equipment一处来源，标准要求只有共享固定方法包。任务上下文是只读快照，不反向写事实、判据或方案。

新上下文和跟进说明复用本实验室meta及事件链，不新建数据库。新契约通过现有schema入口生成；技术判定仍由原有依据、判断链和U模块负责。当前记录投影复用既有汇总选择逻辑，历史未固定上下文的任务兼容读取。代码已建立本地版本基线，业务原件及outputs保持独立存储和既有备份机制。

## 当前分层

| 层 | 责任 | 边界 |
| --- | --- | --- |
| 聊天技能、CLI、已有HTML | 解释任务并调用应用服务 | 不各自维护技术判据或第二份限值 |
| application | 固定上下文、来源核对、流程编排、发布和汇总 | 原件/数据库操作集中在服务及适配器 |
| domain | 契约、依据角色、判断链、U复算及确定性比较 | 不加载Word、原件或实验室数据库 |
| adapters | SQLite、对象存储、PDF/OCR、Word/XLSX及路径 | 可选解析依赖集中接入；沿用layout 2 |

`review_confirmation`仍是analyze、format、finish的共同检查入口。`confirmation_quality`负责自身诊断、原页和项目映射的加载核对；纯方法依据及角色检查由`domain/confirmation_basis.py`唯一实现，旧导入路径保留别名兼容。判断链和U评价继续各在原domain模块维护，函数行为与技术边界未改变。

汇总的当前确认版本按批次、固定证书及明确设备标签选择。同一证书同一设备的多次提取/确认取最新完整版本；不同证书及同一证书中的不同设备分别保留。名称别名只在身份有据时归一，保存显示标签调整事件，不改固定技术结果。历史记录仍全部核对完整性，不因未显示在当前投影就跳过核验。

## 核心服务与演进记录

证书确认采用`prepare → 技术分析/analyze → Word/format → 渲染 → finish → Excel`。`domain/confirmation_reasoning.py`只检查判断关系及限制依据；`application/document_templates.review_confirmation`统一核对固定输入、方法覆盖与技术分析，analyze、format、finish共用。判断依据未核清时不进入正式填表；旧归档不追写，方案编制和固定基准不依赖这组记录。详见[技术判断前置](equipment-confirmation-forms.md#技术判断先于正式填表2026-11-07)。

0.11.0的聊天主流程、共享标准要求、实验室采用/用途关联与Excel汇总见[方法要求与用途诊断](method-requirements.md)。新增domain/method_contracts、domain/method_requirements、application/methods、application/method_diagnosis和application/laboratory_summary。复用原件、批次、原页证据和PDF批注，方案确认继续独立。self_diagnosis应用服务仍仅处理报告自身，外层CLI分别调用用途诊断和汇总投影。

当前功能、文件与数据库的逐项对应及长期维护边界，见[文件结构与工作台功能对应](structure-and-function-map.md)。本地OCR适配见[OCR说明](local-ocr.md)。

0.7的独立面板、原页引线和模型候选问题接入见[报告弹窗与问题标注](report-windows-and-callouts.md)。面板和引线共用固定审查数据，不维护第二份限值或问题事实。

第三阶段首版的中控台组织、批次关系和Excel／智能体适配，见[第三阶段设计](phase-three/design.md)与[操作指南](console-guide.md)。新增应用层不改写已有技术快照；业务数据库通过追加表迁移至批次结构版本3。

第一阶段采用一个本地Python应用、统一命令入口和按实验室隔离的SQLite数据库。初始实现面向检测及校准实验室；其他类型保存类型与准则配置，但需要对应规则和原件后才可开展其技术评价。

```text
智能体／操作人员
      ↓
统一CLI和应用服务
      ↓
参数结构、版本校验、确定性计量规则
      ↓
实验室数据库及原件存储
```

## 模块职责

| 模块 | 职责 |
| --- | --- |
| domain/contracts.py及各契约模块 | 统一注册版本化交换结构及输入验证，schemas由其生成 |
| domain/readiness.py | 逐参数覆盖与结果判定就绪状态 |
| domain/rules.py | 十进制计算、单位换算和明确规则的比较 |
| application/sources.py | 原件接入、哈希校验与缺口 |
| application/laboratories.py | 实验室及设备配置版本 |
| application/plans.py | 候选方案、原页核验、确认、固定基准和差异 |
| application/migration.py | 旧模块保守迁移和原件补充 |
| application/maintenance.py | 完整性检查、备份与恢复 |
| adapters/storage.py | 数据隔离、SQLite、追加事件记录 |
| adapters/legacy.py | 保留旧表格原文并识别配套文件对象差异 |
| adapters/export.py | 从同一份方案生成文档和运行清单 |
| domain/certificate_contracts.py | 证书提取、核验、补充问题及机构证据契约 |
| domain/certificate_review.py | 证书检查、覆盖、结果与范围比较 |
| application/certificates.py | 固定提取、原页核验、补充发现和复核记录 |
| application/certificate_reviews.py | 固定基准与多证书审查的编排 |
| adapters/pdf.py | 本地文字、OCR、页面坐标及PDF批注 |
| adapters/certificate_export.py | 同一审查快照生成PDF、HTML和问题清单 |
| adapters/server.py、web/ | 本地HTTP适配及Windows XP / Luna操作界面 |
| domain/campaign_contracts.py | 实验室设置、批次、固定引用、工作簿与智能体结果契约 |
| application/campaigns.py、console.py | 年度批次组织、固定引用、索引与分页投影 |
| adapters/spreadsheet.py、application/workbooks.py | 标准库只读XLSX适配、可配置映射与候选方案整理 |
| application/intelligence.py | 固定上下文包及候选字段、结果与问题接入 |
| application/document_templates.py | 确认模板、任务、统一分析/发布入口及固定结果 |
| application/confirmation_quality.py | 自身诊断、证书项目及原页映射核对 |
| domain/confirmation_basis.py | 方法依据、验收要求与器具参考角色的纯检查 |
| domain/confirmation_reasoning.py、confirmation_uncertainty.py | 技术判断关系、用途限制及U路径复算 |
| application/laboratory_summary.py | 固定结果的当前Excel投影及历史导出保护 |

## 数据隔离

共享参考库仅接收明确允许共用的标准和CNAS资料。每个实验室拥有独立业务数据库和对象存储；设备、方案、确认与输出只在选定实验室中查询。相同资产编号可以在不同实验室分别使用。

原件路径在读取时检查所属对象存储范围。恢复校验备份清单、文件摘要和数据库所属实验室，拒绝覆盖现有数据。该本地隔离不替代未来联网产品的账号认证和权限控制。

## 版本关系

设备配置摘要包含单调递增的配置版本，配置变更使依赖旧配置的方案不能自动用于当前基准。方案修订保存完整内容快照。新版本确认后，旧确认版本标为已替代，但旧基准仍可按标识读取用于历史追溯。

固定基准保存方案摘要、设备配置摘要、规则版本、原件摘要和确认记录。自动编译要求具体用户确认和覆盖就绪；缺少判定规则时仍可建立覆盖基准，但结果判定状态明确为未就绪。

## 证书接口与边界

第二阶段已实现证书审查和PDF批注。`acceptance.v1.json`允许基础审查不绑定基准；参数比对要求有效确认基准。周期、趋势和异常影响继续未启用。

证书数据库通过追加表迁移，保留第一阶段事实。提取、证据、审查、补充问题及复核均保存摘要；新证据产生新审查，不修改旧结论。原件存储仍归所属实验室。坐标采用旋转后页面的0至1比例，预览与批注使用同一坐标约定。

智能体提出的语义问题通过候选问题入口接入，始终为待核实。确定性数值比较仅消费实际原页核验记录；不会以候选OCR值代替输入。范围附件先支持人工／智能体辅助接入，官方自动下载和独立模型服务未部署。具体边界见[证书工作台指南](certificate-guide.md)。

实现依据为本会话确认的[迁移改造方案](https://chatgpt.com/space/page_2b31ea4f77388191931928144aff1919)。此本地说明记录当前实现，未将项目内候选数据视为技术批准。



## 报告工作台服务（0.4）

`domain/recognition.py`负责候选分组及可解释匹配，`adapters/recognition.py`按PDF字段标签和版面提取身份线索；两者不写实际核验或确认表。`application/report_workspace.py`提供批次候选目录、追加式关联、自动基础审查及六类诊断投影；正式技术比较继续调用原有审查服务。`application/report_intake.py`使用有界线程池处理报告并持久化真实进度；可选PDF/OCR工具仍通过适配层使用。`adapters/report_archive.py`保存只读原件副本与关联索引。界面分为通用工具`app.js`、批次与方案`console.js`、报告交互`reports.js`。

模型识别与本地诊断规则分开。当前支持PDF文字、Tesseract与PaddleOCR-VL-1.6候选提取；已有项目智能体上下文及候选结果入口保留，通用大模型技术诊断服务未接入。规则提取、设备归类、资料对应澄清、技术确认、原页核验和最终技术批准分别管理。周期、趋势、异常影响仍为预留。

PDF适配器的读取、预览和批注在本地独立进程中执行，JSON行管道返回结果及逐页进度，操作由程序固定指定，文件内容不能指定程序操作。后台同时运行最多2份报告，界面预览使用独立的短任务，不共享MuPDF线程状态。这样处理是因为[PyMuPDF官方文档](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)说明其不支持多线程并发操作。无响应任务有页面级等待超时；失败或中断均保留已归档原件。


`application/diagnostic_report.py`在原页右下方提供方案与证书对照诊断视图，直接引用方案行或固定基准与证书原页，不维护独立可编辑参数。候选相似文字只提供导航线索；数值符合性继续复用固定审查结果。人工登记、补充问题及任务历史的界面入口已移除，日常操作为选择报告、开始诊断、查看对照与原页、导出副本。

证书页扫描反馈由`web/scan.js`统一管理。诊断状态优先于导入队列轮询；失败、切换批次及过期回调都不能留下扫描层或覆盖下一次任务。扫描仅表达处理状态，不模拟技术判断进度。


## 逐点视图与导航（0.5）

`domain/point_matrix.py` 将明确离散点、部件及工况与固定PDF表格投影为候选结果；点位匹配使用精确十进制与兼容单位，未知范围、复杂比例、不同工况和重复点分支保留歧义。`application/point_comparison.py` 固定引用来源摘要，输出 `point-matrix.v1`，不建立第二份可编辑限值，也不向核验或技术结论表写入自动识别值。温度各特性分行，表级不确定度保留适用范围提示。

`application/workspace_catalog.py` 使用 `meta.workspace_role` 区分实际实验室、历史迁移及样本档案；未分类空间不自动显示为实际实验室。新建实验室明确保存角色，迁移程序明确保存档案角色。目录与原件留存，工作台对档案仅提供读取及下载。`storage_overview` 展示各模块使用的真实表、目录、记录数与结构检查，不要求每个菜单对应一个可编辑文件夹。

界面增加 `matrix.js` 与 `storage.js`；前者管理紧凑／完整列、参数与状态筛选及原页定位，后者管理资料档案、目录关联、台账与机构范围列表。业务入口继续共享同一应用服务，工作实验室读写固定在当前批次，设备台账跨年度延续。

## 方案编制与报告诊断隔离（0.8.0）

两条线路通过固定版本只读引用衔接。识别更正不写方案，方案变更不重写报告。固定快照、状态、完整输出和增量迁移见[两条线路与诊断升级](two-lane-diagnosis.md)。

## 文件分层（0.9.0）

输入、处理中间文件、方案输出和证书输出统一经`adapters/layout.py`路由。数据库与原件对象保持稳定；年度批次使用固定引用而非复制技术来源。详细路径、旧目录兼容和整理命令见[文件存储契约](storage-layout.md)。

## 聊天式证书自身诊断（0.10.0）

聊天技能调用统一CLI，直接使用应用层；原件读取及批注仍由适配器执行。`domain/self_diagnosis.py`只做候选字段、有限自动线索和明确公式复算；`application/self_diagnosis.py`固定输入、校验证据、追加导出，不导入任何方案、基准或候选设备目录服务。记录契约为self-diagnosis-record.v1。已有方案编制、方案符合性诊断及HTML均保持独立。操作与能力边界见[聊天诊断契约](chat-self-diagnosis.md)。
