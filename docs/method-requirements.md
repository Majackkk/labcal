# 方法要求与聊天诊断（0.11.0）

任务选择以[当前工作流程](workflow.md)为入口。正式确认可直接在CONF中完成方法覆盖与判断，不强制额外创建METH；只需用途对照时使用本页METH流程。

报告批改副本共用[用户选定的B3输出规则](report-annotations.md)。方法用途不符合只定位提示，不自动删除真实超差数值。

本次为既有系统的增量升级。共用标准事实在参考库，各实验室保存采用关系、设备用途和诊断。HTML未增加入口；聊天技能调用CLI应用层。设备用途诊断与方案履约诊断分别管理，后者仍要求已确认基准。

## 目录及事实

- `data/references/catalog.sqlite`新增method_packages及只含检索元数据的method_requirement_index。原件继续在objects；按原件ID在processing/methods保存原页图及拆解输入。方法包按内容摘要标识、追加保存，不覆盖旧版。
- 实验室lab.sqlite新增method_adoptions、method_uses、method_diagnoses。标准采用不确认设备条件。设备用途通过requirement_ids挑选该设备承担的具体要求，不将整个方法的所有器具要求塞给一台设备。设备配置及确认表不变。
- `processing/certificates/METH_*`保存诊断固定输入，含标准、用途和SELF任务摘要。输出在`outputs/certificates/RUN_*`，包含JSON、说明和有定位的用途不符合批注副本。
- `outputs/summaries/设备校准诊断汇总.xlsx`为每实验室当前唯一主表。`outputs/summaries/RUN_*`保留不可覆盖的版本、来源JSON和摘要。不按年复制技术要求；年度批次是业务字段。

## 操作

```sh
./labcal source add --file STANDARD.pdf --kind test_method_standard --shareable --code '标准号及版本'
./labcal method page --source REF_ID --page 8
./labcal method import --record decomposition.json
./labcal method search --standard '标准号' --edition 年代号 --parameter 参数
./labcal method search --equipment 设备类别
./labcal method show --id METHOD_ID
./labcal method adopt-import --lab LAB_ID --record adoption.json
./labcal method adoptions --lab LAB_ID
./labcal method use-import --lab LAB_ID --record use.json
./labcal method uses --lab LAB_ID --equipment 设备标识
./labcal method-diagnosis prepare --lab LAB_ID --campaign CAM_ID --extractions EXT_ID --uses USE_ID [USE_ID ...]
```

prepare返回self_task_id；使用既有self-diagnosis page/show/finish完成报告内部分析，取self_run_id。每份报告独立任务；多份报告由聊天依次编排，汇总时归入同一实验室表。

```sh
./labcal method-diagnosis show --lab LAB_ID --campaign CAM_ID --task METH_ID
./labcal method-diagnosis finish --lab LAB_ID --campaign CAM_ID --task METH_ID --record observation.json
./labcal summary --lab LAB_ID
```

`finish`默认更新Excel；`--no-summary`适合批量处理中先记录后统一汇总。没有具体位置不画批注；`--no-annotate`关闭批注。自身诊断CLI也可自动刷新同一表，应用服务本身保持独立，且不读取方法或方案。

## 交换结构

以`./labcal schema`生成的method-package.v1、method-adoption.v1、method-use.v1、method-diagnosis-record.v1为准。

要求包含标准原文位置、parameter、requirement_type、origin、condition、range_text及criterion。状态candidate/page_checked/ambiguous，origin explicit/derived/recommendation。只有page_checked且不是recommendation的要求可作为用途判定依据。

criterion的operator为abs_le/le/ge/eq/none。明确数值或expression二选一；公式只支持有限Decimal算术和小整数幂，不执行代码。常数在标准包中，实际输入在用途条件中，二者不能相互覆盖。计算跟踪量纲，不把mm、μm和百分比混算；百分比明确relative_basis。不支持的关系或“精确到”含义保留none并专项分析。公式示例必须使用字符串数值，限值不在Excel中维护。

用途关联保存固定method_id、设备标识、requirement_ids、purpose、use_range、conditions、直接用户证据和可选adoption_id。标准采用是独立事实；缺少设备条件时关联继续candidate。supersedes只替代同实验室同设备同标准的旧记录，旧记录保留。

观察记录绑定task_hash和self_run_id。每个observation固定use_id、requirement_id、extraction_id、point、condition、match、scope、reason、原页evidence及rule/rule_basis。可带value和uncertainty；value是所比较的特性量，不一定是仪器示值。程序验证证据文字或已渲染图摘要，实际语义对应由智能体原页分析支持。

先完成四组自身检查，再进行用途评价；内部未检查、待核实或错误会保留证据缺口。每行结论只覆盖声明范围，未提交项目显示未检查，不自动判不合格。guard_band失败保持“证据不足／待核实”，不等同仪器超差。测量系统、区间增量和单点误差不得互相替代。

communications用于固定智能体组织的编号沟通条目；有新U要求时必须带机构范围原件、页码、适用说明、CMC及k。数值U不能低于同条件同k的CMC；复杂相对CMC或不同k需要专项处理，不能直接换算承诺。适用项目、时间、范围、现场条件须实际阅读原页，字段存在和程序通过不能证明机构资格已核实。

用户要求将沟通内容填写到Excel时，同一实验室使用既有的同一份沟通汇总工作簿，按设备、证书、参数追加，不再为每台设备或每次讨论单独创建工作簿。原有条目保留；更正内容保留版本依据。用户既有的独立沟通表与程序生成的诊断汇总分别识别，不能为追加沟通而绕过汇总摘要保护，或将沟通建议写成已确认技术限值。当前工作簿位置按项目规则及实验室记录查询。

## 汇总、版本与维护

Excel包含设备确认记录、问题清单、方法要求对照、校准机构沟通。设备确认页由confirmation finish登记的共享结果投影，Word与Excel逐格一致；按同批次、同证书及明确设备选择最新正式确认；有据归一显示别名以消除重复，不同证书或同证书中的不同设备各自保留。其余页保留自身及方法诊断范围，每个参数/点位一行；日期和批次来自真实批次。最新用途诊断替代同批次、设备、标准版本及用途的旧检查集，保留其他方法和历史导出。相同任务同记录重复提交复用已有结果，不重复入库。用途版本被替代时旧结果显示待更新，不自动改成新版本判断。

主表是标准库OOXML适配器生成的离线投影，保留十进制原文、筛选与冻结表头，不依赖Codex或JS运行时。生成后使用表格工具做可视核对。检测到主表外部修改时停止覆盖；先由聊天明确需要接收的内容，再更正业务记录重新导出。没有自动导入任意Excel改动的功能。

导出版本与主表摘要由runs和meta跟踪。原件、固定记录和输出摘要纳入storage检查，备份包含共享标准库与实验室数据。自动比较不构成人员技术批准。周期、趋势和异常影响仍预留。
