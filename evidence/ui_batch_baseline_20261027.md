# UI 批量实测报告（基线套件）
- 生成时间：2026-10-27T16:47:31
- 总运行 15 次，其中基础设施异常（网关超时/限流，不计入指标）0 次
- 总体成功率（有效轮次口径）100.0%

## 分任务统计
| 任务 | 通过/有效 | 基础设施异常 | 成功率 |
|---|---|---|---|
| ui_settings_open | 3/3 | 0 | 100.0% |
| ui_settings_display_deep | 3/3 | 0 | 100.0% |
| ui_contacts_open | 3/3 | 0 | 100.0% |
| ui_connected_devices_page | 3/3 | 0 | 100.0% |
| ui_sound_page | 3/3 | 0 | 100.0% |

## 逐次明细
| 任务 | 轮 | 结果 | 耗时(s) | 备注 |
|---|---|---|---|---|
| ui_settings_open | 1 | ✅ | 16.1 | AI 判定完成 + 独立断言通过 |
| ui_settings_open | 2 | ✅ | 22.4 | AI 判定完成 + 独立断言通过 |
| ui_settings_open | 3 | ✅ | 27.7 | AI 判定完成 + 独立断言通过 |
| ui_settings_display_deep | 1 | ✅ | 27.0 | AI 判定完成 + 独立断言通过 |
| ui_settings_display_deep | 2 | ✅ | 26.5 | AI 判定完成 + 独立断言通过 |
| ui_settings_display_deep | 3 | ✅ | 30.6 | AI 判定完成 + 独立断言通过 |
| ui_contacts_open | 1 | ✅ | 32.6 | AI 判定完成 + 独立断言通过 |
| ui_contacts_open | 2 | ✅ | 23.8 | AI 判定完成 + 独立断言通过 |
| ui_contacts_open | 3 | ✅ | 20.2 | AI 判定完成 + 独立断言通过 |
| ui_connected_devices_page | 1 | ✅ | 36.6 | AI 判定完成 + 独立断言通过 |
| ui_connected_devices_page | 2 | ✅ | 19.9 | AI 判定完成 + 独立断言通过 |
| ui_connected_devices_page | 3 | ✅ | 18.3 | AI 判定完成 + 独立断言通过 |
| ui_sound_page | 1 | ✅ | 32.9 | AI 判定完成 + 独立断言通过 |
| ui_sound_page | 2 | ✅ | 18.5 | AI 判定完成 + 独立断言通过 |
| ui_sound_page | 3 | ✅ | 22.2 | AI 判定完成 + 独立断言通过 |

> 环境：Android Automotive with Google APIs (x86_64) 模拟器。
> 证据包位于 data_output/ui_runs/<case>/<ts>/，RCA 报告位于 reports/。
