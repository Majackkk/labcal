# 操作指南

后续可以在本项目中直接提出“接入某设备资料”“整理这份候选方案”等请求，由智能体按项目内技能调用程序。本地工具也可由操作人员直接使用，所有命令返回JSON结果。

## 新实验室

```bash
./labcal init
./labcal lab add --id LAB_001 --name 实验室名称
./labcal equipment add --lab LAB_001 --asset-code EQ_001 --name 设备名称 --model 型号 --configuration 实际配置.json
```

配置JSON保存组件、传感器、通道和用途等实际信息。型号可提供的功能不自动成为本实验室实际配置。新设备配置默认为待确认。

## 接入资料和候选方案

```bash
./labcal source add --lab LAB_001 --file 文件路径 --kind manufacturer_manual
./labcal equipment list --lab LAB_001
./labcal plan import-markdown --lab LAB_001 --equipment-id EQ标识 --file 已有方案.md
```

已有Markdown方案会保留原表格字段，未规范化的数值和判据不会自动放行。Word、PDF或其他原件先登记，再由智能体或人员直接读取原件、整理为JSON候选要求。方案流程没有独立模型服务；证书扫描页OCR和批注见[证书工作台指南](certificate-guide.md)。

结构化要求可采用 `schemas/plan.v1.json` 描述的字段。输入可以省略由程序填入的 `schema_version`、`lab_id` 和 `equipment_config_hash`，但应提供设备标识、标题、参数要求及来源标识。

```bash
./labcal plan import --lab LAB_001 --file 方案要求.json
./labcal plan draft --lab LAB_001 --file 从原件整理的候选要求.json
./labcal plan show --lab LAB_001 --id PLAN标识
./labcal plan validate --lab LAB_001 --id PLAN标识
./labcal plan export --lab LAB_001 --id PLAN标识
```

`draft` 不代表参数已核验，它只是明确候选编制来源。原页核验记录需由实际核验人员提供，包含参数标识、原件标识和摘要、方案摘要、原文、页码／位置、核对字段、版本与适用性检查、核验人及时间：

```bash
./labcal plan evidence-review --lab LAB_001 --id PLAN标识 --revision 版本号 --record 原页核验记录.json
```

尚未核对的记录不能由智能体代填。规范主张不能引用历史方案或用户方案充当规范原件；制造商主张也需对应制造商原件。

## 用户确认和基准

迁入方案默认为候选。仅当用户明确确认具体设备配置、方案版本、用途和参数后，才生成并提交确认记录。

```bash
./labcal plan confirmation-template --lab LAB_001 --id PLAN标识 --output 确认记录.json
```

模板中的确认人、时间、直接答复保持空白。由实际直接答复填写，记录答复来源；不能凭“开始搭建系统”等任务指令填入确认。确认结构会锁定方案和设备摘要，配置发生变化时旧记录不能通过。

```bash
./labcal plan confirm --lab LAB_001 --record 确认记录.json
./labcal baseline build --lab LAB_001 --id PLAN标识
```

尚有覆盖相关的证据或对象冲突时，确认会被拒绝。仅影响结果判定的待决事项可明确保留；生成的基准会显示对应判定未就绪。

## 修订与历史

```bash
./labcal equipment update --lab LAB_001 --id EQ标识 --file 配置更新.json
./labcal plan import --lab LAB_001 --plan-id PLAN标识 --file 修订方案.json
./labcal plan diff --lab LAB_001 --id PLAN标识 --before 1 --after 2
./labcal baseline show --lab LAB_001 --id BASE标识
```

配置更新JSON必须包含当前 `expected_config_hash`。导出文档修改后按新候选修订导入，不覆盖数据库。旧基准可以读取用于历史追溯；当前使用需匹配有效确认版本和设备配置。

## 当前候选资料

`LAB_LEGACY` 和 `LAB_CERT_SAMPLES` 仅用于整理已迁移资料，不表示真实实验室身份及设备配置已经确认。查阅迁移报告和待补原件清单后，可按选定设备逐项整理，不必一次性完成所有历史方案。

