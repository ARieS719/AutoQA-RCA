# UI 批量实测报告（深链路套件）
- 生成时间：2026-10-27T16:39:22
- 总运行 12 次，其中基础设施异常（网关超时/限流，不计入指标）0 次
- 总体成功率（有效轮次口径）75.0%

## 分任务统计
| 任务 | 通过/有效 | 基础设施异常 | 成功率 |
|---|---|---|---|
| ui_settings_brightness_max | 3/3 | 0 | 100.0% |
| ui_settings_network_page | 3/3 | 0 | 100.0% |
| ui_settings_roundtrip_nav | 0/3 | 0 | 0.0% |
| ui_multi_app_switch | 3/3 | 0 | 100.0% |

## 逐次明细
| 任务 | 轮 | 结果 | 耗时(s) | 备注 |
|---|---|---|---|---|
| ui_settings_brightness_max | 1 | ✅ | 17.5 | AI 判定完成 + 独立断言通过 |
| ui_settings_brightness_max | 2 | ✅ | 18.7 | AI 判定完成 + 独立断言通过 |
| ui_settings_brightness_max | 3 | ✅ | 28.9 | AI 判定完成 + 独立断言通过 |
| ui_settings_network_page | 1 | ✅ | 34.7 | AI 判定完成 + 独立断言通过 |
| ui_settings_network_page | 2 | ✅ | 20.1 | AI 判定完成 + 独立断言通过 |
| ui_settings_network_page | 3 | ✅ | 16.8 | AI 判定完成 + 独立断言通过 |
| ui_settings_roundtrip_nav | 1 | ❌ | 97.7 | 触达最大步数 8，熔断 |
| ui_settings_roundtrip_nav | 2 | ❌ | 89.6 | 触达最大步数 8，熔断 |
| ui_settings_roundtrip_nav | 3 | ❌ | 68.0 | 触达最大步数 8，熔断 |
| ui_multi_app_switch | 1 | ✅ | 42.7 | AI 判定完成 + 独立断言通过 |
| ui_multi_app_switch | 2 | ✅ | 66.4 | AI 判定完成 + 独立断言通过 |
| ui_multi_app_switch | 3 | ✅ | 40.3 | AI 判定完成 + 独立断言通过 |

## 失败案例 RCA 结论（测→析闭环）
| 案例 | 责任方 | 根因 | 报告 |
|---|---|---|---|
| ui_settings_roundtrip_nav#1 | 图形模块开发团队 | 系统底层图形缓冲区管理的线程安全问题，导致在频繁界面切换时screencap服务崩溃，进而使测试框架 | ui_settings_roundtrip_nav_20261027_163947.md |
| ui_settings_roundtrip_nav#2 | The development team responsible for the CarSettingActivities and its lifecycle management, particularly the service binding/unbinding logic and view loading mechanisms. | Resource management defect in CarSettingActivities | ui_settings_roundtrip_nav_20261027_164010.md |
| ui_settings_roundtrip_nav#3 | 座舱UI开发团队 | 图形渲染异常（EGL配置失败）和界面状态未正确更新（资源缺失/状态同步问题） | ui_settings_roundtrip_nav_20261027_164036.md |

> 环境：Android Automotive with Google APIs (x86_64) 模拟器。
> roundtrip_nav（Display → BACK → Sound 三级往返导航）0/3 为已知待优化项，见 README 路线图。
> 证据包位于 data_output/ui_runs/<case>/<ts>/，RCA 报告位于 reports/。
