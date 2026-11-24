# 数据库结构版本

迁移DDL在 `src/labcal/adapters/storage.py` 单一维护，通过幂等enable方法执行；本目录登记编号和验证，不维护第二套可编辑SQL。模块版本写入meta，不等于应用版本。

| 编号 | meta标记 | 当前值 | 内容 |
| --- | --- | --- | --- |
| 001 | schema_version | 1 | 实验室、原件、设备、方案、确认、基准、事件 |
| 002 | certificate_schema_version | 2 | 提取、核验、审查、问题、复核、机构证据 |
| 003 | campaign_schema_version / intake_schema_version | 3 / 1 | 年度批次、固定引用、工作簿、候选智能体结果、队列、关联 |
| 004 | lookup_schema_version | 1 | 证书多版本列表与批次查询追加索引 |

迁移不覆盖原件或固定payload，不继承旧登记册技术批准；旧项目仅导入原件和候选资料。以后结构变更必须登记版本、更新交换契约（如有）、验证已有库升级及空目录恢复。004仅添加索引，不重建技术记录。


## 文件布局版本2（应用0.9.0）

本次不改变数据库表结构或固定事实。`./labcal organize --lab LAB_ID --apply`显式迁移旧输出至`outputs/<线路>`，旧运行材料至`processing/<线路或legacy>`；旧上传路径保留兼容。每次整理在实验室`processing/maintenance/LAYOUT-*`记录路径与摘要。程序统一路径契约见[文件存储契约](../docs/storage-layout.md)。

应用0.10.0新增聊天诊断交换契约self-diagnosis-record.v1和runs类型certificate_self_diagnosis，无DDL变更；固定输入文件及原页图保存于SELF任务目录。既有方案表、真实人员核验表和确认基准不回写。
