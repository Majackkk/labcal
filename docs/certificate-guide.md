# 证书审查工作台

报告批改PDF现默认采用[用户选定的B3样式](report-annotations.md)：斜线标记拟替换／删除原文，起点圆点，右侧分色字段卡。聊天式证书自身诊断和方法用途诊断共用该导出器，方案编制及技术判定保持独立。

当前报告工作台版本为0.8.0，使用本地Python服务、项目智能体技能和Windows XP / Luna风格HTML界面。先建立资料接入、问题定位、审查与批注闭环，再按真实设备补齐技术核验。现有证书的工程试跑没有产生设备放行或真实技术批准。

## 操作界面

```bash
./labcal serve --port 8765
```

浏览器打开`http://127.0.0.1:8765`。选择实验室、年度与批次，点击“导入报告”选择一份或多份PDF。系统先归档原件，再逐页识别、自动匹配方案设备并生成基础诊断。匹配到有效的已确认结构化方案时，自动引用其固定基准；没有基准时保留参数覆盖和结果符合性待核实。同设备多份报告可点击“同设备报告联合审查”，只合并相同设备引用的报告。详见[报告工作台操作与边界](report-workbench.md)。

上方是原页与识别信息，下方是诊断报告。点击带播放箭头的“开始诊断”，报告以方案和证书两列逐项展示设备身份、参数、点位、MPE、不确定度及依据，并给出符合、不符合、待确定或不适用。点击报告中的页码定位原页。“导出批注副本”生成批注PDF及相关输出，原件保持留存。

日常界面供个人使用，不提供“登记核验”“补充问题”和“审查记录”模块，不要求填写核验人或复核流程。后台版本与原件数据继续维护。候选文字或相似参数仅用于预对照；缺少确认基准或对应结果时保留待确定，不推定已经通过验收。下列CLI入口供结构化资料接入和高级使用，不是日常界面的前置表单。

## 命令行与智能体入口

```bash
./labcal certificate add --lab LAB_ID --file 原件.pdf
./labcal certificate add --lab LAB_ID --source-id SRC标识
./labcal certificate list --lab LAB_ID
./labcal certificate show --lab LAB_ID --id EXT标识
./labcal review run --lab LAB_ID --extractions EXT标识
./labcal review run --lab LAB_ID --extractions EXT标识1 EXT标识2 --baseline BASE标识
./labcal review show --lab LAB_ID --id CRV标识
./labcal review export --lab LAB_ID --id CRV标识
```

`--no-ocr`可在证书接入时跳过扫描页OCR；跳过或识别失败会记录待核实状态。OCR在本机运行，不上传至模型服务。PDF预览与批注需要PyMuPDF；扫描页还需要Tesseract及`chi_sim+eng`语言包。

实际原页核验：

```bash
./labcal certificate evidence-template --lab LAB_ID --id EXT标识 --output 核验记录.json
./labcal certificate evidence --lab LAB_ID --record 核验记录.json
```

模板保持核验人和时间空白。证据记录绑定提取版本及原件摘要，每个字段或结果带位置。坐标相对旋转后可见页面，取值0至1。声明缺项或结果录入完整需实际核对全部页面，再填写`pages_checked`和`observations_complete`。

例如，方案的`decision_rule.input`为`error`时，结果使用`inputs.error`；报告字段仍可写`reported_fields: ["indication_error", "uncertainty"]`。计量数值使用十进制字符串，单位及百分比基准明确。结果记录标识稳定时，新记录修正该行，旧记录继续保留。

补充语义问题：

```bash
./labcal certificate findings --lab LAB_ID --record 补充问题.json
```

结构见`schemas/certificate-findings.v1.json`。明确提出人、智能体或操作人员来源、问题说明、位置、依据和处理建议。有规范依据时绑定原件标识、摘要及条款位置；未核验的含义与适用性仍待复核。该入口拒绝输入“已证实”或批准字段。

机构资料接入与核验：

```bash
./labcal source add --lab LAB_ID --file 官方范围附件.pdf --kind institution_scope
./labcal institution add --lab LAB_ID --record 范围核验记录.json
./labcal institution list --lab LAB_ID
```

资料必须绑定原件摘要、对应机构、方案类型、有效日期、参数、范围及实际核验人。当前范围不能直接证明历史活动的能力。其他溯源路线、未记录的条件或CMC公式保持待核实，不凭标识或相似项目名推断。

复核与新审查：

```bash
./labcal review resolve --lab LAB_ID --record 问题复核.json
./labcal review run --lab LAB_ID --extractions EXT标识 --baseline BASE标识 --previous-review CRV标识
```

旧基准可查看历史，但方案被替代、设备配置改变或规则版本不一致时，不用于新的当前审查。内部校准、功能核查和使用前检查不会要求用外部证书覆盖。

## 当前边界

- 三项状态为证书资料、方案覆盖和用途判定，均允许待核实。成功完成程序运行不等于通过验收。
- 现有10份历史方案保持候选；真实确认及基准为0。样本工程检查不替代实际实验室归属和用途确认。
- 真实检定样本尚未接入，该分支先用隔离测试件验证。
- 官方机构自动下载、独立模型服务、复杂不确定度计算和CMC公式尚未部署；可通过原件、项目智能体及核验记录继续处理。
- 原件中的数字签署证据保留；批注副本是审查输出，不标示为重新验证过的签署原件。
- 周期、历史趋势和异常影响未启用。

## 0.8.0操作更新

当前布局为左侧原页、右上信息、右下诊断。使用“更正识别”修正报告候选；“导出完整诊断”下载摘要、Excel逐点表及批注。系统前提与证书疑点分开，旧诊断需刷新后用于当前导出。详见[两条线路与诊断升级](two-lane-diagnosis.md)。
