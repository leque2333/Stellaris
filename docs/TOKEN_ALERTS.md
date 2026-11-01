# Token 预警

> 更新：2026-11-01  
> 位置：账单流水页顶部；余额阈值在「添加额度 / 添加金额」弹窗

## 1. 两类预警

### 余额提醒
- 每个币种单独一个阈值
- 主余额、副余额都支持
- 在「添加额度 / 添加金额」弹窗里设置
- 余额低于阈值时提醒

### 动态 Token 上限
- 取最近 N 天（默认 7 天）的每日 token 用量
- 只统计输入 + 输出，不含充值流水
- 上限：

```text
max(平均用量 × 倍率, 平均用量 + 2 × 标准差)
```

- 默认倍率 1.5，最少 3 天样本才启用
- 今日用量超过上限时提醒

## 2. 只提醒一次

状态文件：

```text
data/token_alert_state.json
```

按日期记录已提醒类型：

- 余额提醒每个币种每天一次
- Token 超限每天一次
- 次日自动重新计数

## 3. 配置

文件：

```text
data/token_alerts.json
```

```json
{
  "enabled": true,
  "balance_threshold": 5.0,
  "balance_thresholds": {"USD": 5.0, "CNY": 30.0},
  "lookback_days": 7,
  "limit_multiplier": 1.5,
  "min_samples": 3
}
```

- `balance_thresholds` 按币种覆盖默认值
- `balance_threshold` 是旧字段/兜底
- `lookback_days` 1~30
- `limit_multiplier` 1~5

## 4. 页面

- 账单页顶部：当前余额、今日 token、动态上限、参考天数
- 有预警时显示黄色/红色提示
- 「参考天数」「上限倍率」可在账单页保存
- 「余额阈值」移到充值弹窗，避免两处配置
