# 文件结构与工作台功能对应（0.9.0）

0.11.0新增聊天式方法要求库、标准采用、设备用途诊断及实验室汇总，见[方法要求与用途诊断](method-requirements.md)。下文界面说明继续适用于保留的HTML工作台，新功能由CLI/聊天调用，不增加网页入口。

本文描述当前实现。早期阶段文档保留设计背景；界面位置、OCR及存储检查以本文和 local-ocr.md 为准。当前是支持多个实验室、多年、多批次的本机个人软件。没有真实已确认方案；工程检查不构成技术验收。

## 目录责任（0.9.0）

当前目录树、路径契约、迁移、备份及两条业务线路见[文件存储契约](storage-layout.md)。新增文件使用`inputs`、`processing`、`outputs`三层，分别按批次／线路／任务定位。数据库、对象库和年度批次只读索引保留稳定位置。

根目录早期PDF已核对对象摘要并整理至样本档案的`inputs/legacy-initial`。历史`inbox`为旧任务路径保留；不再接收新上传。历史`runs`和`exports`通过显式迁移整理，文件字节与固定清单不改。

## HTML功能对应

| 页面功能 | 程序服务 | 在用数据／实际范围 |
| --- | --- | --- |
| 实验室选择、设置 | laboratories、workspace_catalog | 各lab.sqlite的meta；仅显式laboratory进入工作导航 |
| 年度、批次、新建、归档 | campaigns | campaigns、campaign_links；批次目录是固定引用投影 |
| 中控台清单、搜索、分页 | console | 当前实验室／批次的摘要查询，正文按需加载 |
| 方案导入、七列清单、点位 | workbooks、spreadsheet、point_matrix | sources、workbook_imports、plan_revisions及只读导入目录 |
| 方案确认、固定基准 | plans | 具体修订、实际确认、baselines；候选不自动确认 |
| 批量报告导入 | report_intake、report_archive | sources、report_jobs；先留存原件再识别、诊断 |
| OCR选择、重新识别 | pdf_process、pdf、paddle_ocr、paddle_worker | certificate_extractions追加快照，每页保存实际引擎 |
| 原页、翻页、定位 | pdf_process、pdf | 当前批次固定提取引用的原件对象，按需渲染 |
| 右上识别信息 | recognition、report_workspace | 固定提取的候选字段／表格，不写实际核验 |
| 右下诊断、逐点对照 | report_workspace、diagnostic_report、point_comparison | 方案／基准、提取、关联及审查快照；不存第二套可编辑限值 |
| 设备对应澄清 | report_workspace | report_associations追加关联，不替代配置确认 |
| 引用已有资料 | console、campaigns | 实验室内既有固定版本加入当前批次引用 |
| 机构范围资料 | certificates、sources | institution_evidence、sources和允许的参考资料；按有效期和项目使用 |
| 设备台账 | laboratories | equipment跨年延续；候选设备组不自动变为真实设备 |
| 批注导出 | certificate_export | runs记清单，outputs/certificates/RUN_ID存副本；批次index列对应导出 |
| 历史／待归属资料 | workspace_catalog | 独立档案空间，仅查看和下载 |
| 数据与文件、完整校验 | workspace_catalog、storage_audit | 只读检查SQLite、摘要、归档、导出与批次投影 |
| 列宽、区域分隔、OCR偏好 | columns、panes、reports | 浏览器localStorage，不是技术事实，也不在业务备份内 |

菜单不需要各建一个可编辑文件夹。诊断是固定资料的投影，设备是跨年数据库对象；强行复制会形成多个限值来源。

## 本次完善

1. 同证书多次OCR保留全部版本，默认清单每份原件只列最新提取，分页按报告数量计算。其他批次仍显示自己的固定版本，旧版可由固定引用及资料库读取。
2. 追加证书版本／批次查询索引，以meta.lookup_schema_version登记，不改变业务内容。
3. 索引JSON和README在临时文件写完后原子替换，避免只留下半截；固定文件仍禁止覆盖。多进程造成的过期索引可通过完整校验发现并重建。
4. 新增storage-audit.v1契约。缺失／变更原件和固定归档列为错误，可重建索引列为警示，资料正文缺口另列待补。完整校验本身不修复或改写资料。
5. 一版早期工作簿缺少查阅归档目录，已用摘要正确的原件和数据库固定payload补齐，没有重新解析或提升确认状态。

```sh
./labcal storage --lab LAB_ID --campaign CAM_ID --deep
./labcal campaign index --lab LAB_ID --id CAM_ID
./labcal doctor --lab LAB_ID
```

## 长期维护约定与边界

- 新实验室用独立LAB_ID，新批次用新CAM_ID。跨年复用明确版本，当前批次不自动消费另一年的最新版本。
- 周期、趋势、异常影响尚未开发。以后追加应用服务、契约及关系表，消费现有equipment、extraction、review的固定ID，不向原文表格塞入新业务。
- 新OCR、通用大模型、机构查询继续接在适配层。模型环境及权重留在独立运行目录，不打包进技术数据；迁到新电脑需单独配置并做推理验证。文件存在不是识别成功或技术核验通过。
- 业务备份包含实验室数据库、对象、批次、导出和共享参考库；代码／模板另存。浏览器偏好、外部模型环境不包含在业务备份中。恢复到空目录，不覆盖在用库；应在任务队列空闲时备份，当前不是跨数据库和文件系统的事务快照。
- 原件、固定版本不自动清理。上传区、工程产物和日志保留策略以后另行制定，当前不静默删除历史资料。
- 当前没有联网账号、多用户权限和云数据库。多人、跨电脑或服务器使用前，需补授权、持久任务调度、备份一致性及容量压测；目录隔离不等于联网租户安全。
- 合成分页测试只代表清单查询，不代表模型吞吐量。若以后证书和方案规模显著增加，再按实测优化物化摘要／缓存，不提前复制技术事实。

## 0.8.0报告更正与固定诊断

`report_corrections`仅追加批次内报告识别更正；`certificate_reviews.payload.report_views`固定页面和导出共用视图。方案编制表、原件和基准不被诊断回写。导出增补完整逐点Excel、CSV、诊断HTML与固定JSON；文件仍在各实验室的`outputs/certificates/RUN_ID`。详见[职责边界](two-lane-diagnosis.md)。
