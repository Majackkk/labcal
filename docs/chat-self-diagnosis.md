# 聊天式证书自身诊断

批改副本默认采用用户选定的B3（起点圆点、拟替换／删除斜线、右侧分色字段卡）。具体位置的可选`markup`、结构和输出职责见[报告批改规则](report-annotations.md)。该样式不改变诊断状态或技术结论。

本功能由聊天技能编排，复用存储、PDF适配器与确定性工具；不依赖HTTP或HTML、不读取方案和基准。源码为`domain/self_diagnosis.py`（纯规则和Decimal复算）、`application/self_diagnosis.py`（范围、快照、证据、输出）、既有PDF适配器。没有独立流程引擎、客户硬编码或另一套技术限值。

```sh
./labcal self-diagnosis prepare --lab LAB_ID --campaign CAM_ID --extractions EXT_ID
./labcal self-diagnosis show --lab LAB_ID --campaign CAM_ID --task SELF_ID
./labcal self-diagnosis page --lab LAB_ID --campaign CAM_ID --task SELF_ID --extraction EXT_ID --page 1
./labcal self-diagnosis finish --lab LAB_ID --campaign CAM_ID --task SELF_ID --record record.json
```

prepare固定输入摘要，任务在`processing/certificates/SELF_ID`。报告读取与原件校验仍走既有数据库服务，单份或多份均可。自动仅检查提取页码和明确证书编号线索；结果为候选或“已执行文字检查未发现异常”，不自动宣布证书无误。四组人工智能原页核查仍显示未检查。型号与双编号采用独立标签提取，不回写旧OCR、更正或确认记录。

page按需保存原页图，图像摘要用于视觉转录证据。图像与证据保存在任务目录；程序核对摘要和位置边界，实际阅读由执行智能体负责，不能声称摘要证明已阅读或技术核验。native_text证据必须来自指定区域中的PDF文字层，不能把OCR行冒充文字层。

finish按`self-diagnosis-record.v1`契约验证。检查项状态为未发现异常、确认问题、待核实、无法判断、不适用；未提交组自动补“未检查”。每项说明范围，不用一个绿色总分覆盖未检查内容。已确认问题／未发现异常必须给出原页证据。任何任务外证书、输入摘要冲突、原文与区域不符或缺少视觉图摘要均拒绝。作者始终是实际智能体，不写入人员核验、方案、基准、确认或用途判定。

记录示意（使用实际任务摘要和位置替换示例）：

```json
{
  "task_hash":"实际摘要",
  "author":"Codex 智能体",
  "checks":[{
    "extraction_id":"EXT_ID","group":"identity","item":"首页编号核对",
    "state":"no_anomaly_detected","reason":"说明实际核对范围和结果",
    "basis":"同一报告首页和结果页的信息一致性",
    "suggestion":"本项已检查范围内无需处理",
    "evidence":[{"page":1,"bbox":[0.1,0.1,0.5,0.2],"quote":"原页原文",
      "origin":"visual_page_read","image_sha256":"page命令的实际图像摘要"}]
  }]
}
```

组为identity、numeric、technical、conclusion，filing仅作归档提示。针对没有具体位置的遗漏可用空evidence并标为候选或无法判断，不能虚构缺失位置或确认结论。规范性结论在basis写明适用文件版本、条款与实际来源；无正文时列依据待补。不存在已确认方案也不影响此模式。

可选calculation结构：formula为`indication_minus_reference`、`reference_minus_indication`或`relative_to_reference_percent`；indication、reference、reported、resolution为十进制字符串，unit为结果单位。操作数需预先转到同一单位，第三个公式结果单位为%。resolution表示报告最后一位对应的步长。程序采用半步长区间检查舍入兼容，临界边界保守视为兼容，不据此证明舍入规则完全正确。其他公式不自动计算，不从全量程或规程猜分母。原始输入和公式必须由原页证据及reason支持。

输出在`outputs/certificates/RUN_ID`，含diagnosis.json、diagnosis.md、manifest.json；有定位的内容问题时增加带引线PDF。filing提示不绘制在证书上。没有发现问题则不生成空白批注PDF。可用`--no-annotate`仅输出记录。重复finish产生新的RUN，历史文件不覆盖；runs表与事件只追加导出清单，完整性检查校验其摘要。输出不自动回接为方案或实际核验事实。

实际检查可以多轮补充；每次finish按完整提交记录生成一个版本，后续版本不隐式继承之前检查项。需要延续时明确携带旧项，防止将过期判断混入新记录。处理中失败保留原件；未登记的失败输出目录不视为完成任务，保留供排查。
