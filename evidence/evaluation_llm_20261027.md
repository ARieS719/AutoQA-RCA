# 评测基准报告（llm 模式）
- 生成时间：2026-10-27T18:13:37
- 覆盖案例：9 个 / 共 18 次运行
- 无真相卡跳过：无

## 总体指标
| 指标 | 命中率 |
|---|---|
| 分诊准确率 | 100.0% |
| 根因命中率 | 100.0% |
| 责任方命中率 | 94.4% |
| RAG 命中率 | 100.0% |
| 仲裁正确率 | 100.0% |

## 明细
| 案例 | 轮 | 分诊 | 根因 | 责任 | RAG | 仲裁 |
|---|---|---|---|---|---|---|
| case_1_ui_crash | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_1_ui_crash | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_2_can_congestion | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_2_can_congestion | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_3_network_offline | 1 | ✅ | ✅ | ❌ | ✅ | ✅ |
| case_3_network_offline | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_4_hardware_fault | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_4_hardware_fault | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_5_ui_but_link_ok | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_5_ui_but_link_ok | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_6_field_noisy | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_6_field_noisy | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_1_can_congestion | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_1_can_congestion | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_2_network_offline | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_2_network_offline | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_3_hardware_fault | 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| case_3_hardware_fault | 2 | ✅ | ✅ | ✅ | ✅ | ✅ |

> 口径：命中=关键词包含判定（保守）；离线模式仅分诊可测；token/费用为估算。
> 说明：责任方唯一未命中（case_3_network_offline 第 1 轮）为措辞差异——AI 答
> "Network reliability and command retry mechanism"，语义与答案卡一致但未含关键词，
> 保守口径下判负（详见 18 轮明细原始 JSON：reports/evaluation_*.json）。
