# proxy-sleuth 数据保鲜指南

> 行业快速发展，探针和参数数据会过期。本文档说明如何保持检测数据新鲜。

## 数据来源速查

| 数据 | 来源 | 更新频率 | 验证方式 |
|------|------|----------|----------|
| knowledge_probes | 自研 + llm-verify (MIT) | 新模型发布时 | 手动添加探针 |
| HumanEval coding 题 | openai/human-eval (GitHub) | **永不变** (2021 发布) | 题目固定，无需更新 |
| MATH-500 题 | HuggingFaceH4/MATH-500 | **永不变** (固定 benchmark) | `pip install datasets` 后重加载比对 |
| API 参数指纹 | OpenRouter API `/v1/models` | 每月 | `python3 scripts/update_api_params.py check` |
| 统计指纹库 | llm-fingerprint (npm) | 上游发布时 | `npm update -g llm-fingerprint` |
| CC Switch schema | CC Switch 桌面应用 | 大版本更新时 | 手动检查 `discover_providers()` 是否返回 0 |

## 一、自动化检查（建议每月一次）

```bash
cd /path/to/proxy-sleuth
python3 scripts/update_api_params.py check    # API参数指纹变化
npm update -g llm-fingerprint                  # 统计指纹库更新
```

---

## 二、新模型发布 → 手动更新

当 OpenAI、Anthropic、DeepSeek、阿里等发布新的大模型版本时，需要更新三处：

### 2.1 添加知识边界探针

编辑 `data/prompts/knowledge_probes.json`：

```json
{
  "新组名": {
    "description": "新模型的发布时间/定价/独有功能",
    "target_date_range": "2026-08 ~ 2026-09",
    "probes": [
      {
        "id": "新模型ID",
        "question": "针对发布时间/定价/独有功能的问题",
        "keywords": ["关键词1", "关键词2"],
        "weight": 1.0
      }
    ]
  }
}
```

同时在 `src/detectors/knowledge_probes.py` 的 `_should_model_know` 方法中添加新组名匹配。

### 2.2 更新 API 特征检测

编辑 `src/detectors/api_features.py`：
- 如果新模型有独有 API 参数 → 添加 `_check_xxx` 探针
- 如果已有参数被新模型也支持了 → 更新 `model_hint`

### 2.3 更新跟踪列表

编辑 `scripts/update_api_params.py`，在 `TRACKED_FAMILIES` 字典中添加新模型。

---

## 三、能力趋同监控

当前 GPT-5.5 (49.4) 和 DeepSeek V4 (48.3) 差距仅 1.1 分。能力趋同会削弱 capability 层的区分力。

- 定期查看 [llm-stats.com](https://llm-stats.com) 排名
- 如果某对模型差距缩小到 3 分以内 → capability 层无法区分它们
- 此时降低 capability 权重（`src/main.py` `WEIGHTS["capability"]`）或替换更难的新题

---

## 四、CC Switch schema 变化

CC Switch 桌面应用的 SQLite schema 可能随大版本变化。如果 `discover_providers()` 输出 0 但 CC Switch 已配置：

1. 运行以下命令查看新表结构：
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('~/.cc-switch/cc-switch.db')
print(conn.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall())
"
```

2. 编辑 `src/utils/ccswitch.py` 的 `_read_providers` 和 `_find_table` 函数，更新表名/列名

---

## 五、阈值校准

当前所有阈值（MATCH ≥ 0.70, MISMATCH ≤ 0.40 等）是拍脑袋设的，未经过真实数据校准。要校准：

1. 获取真实模型的官方 API key
2. 对每个真实模型跑 10 次 `--mode full`
3. 记录各层得分的均值和方差
4. 调整阈值：MATCH 线应高于真实模型的最低分，MISMATCH 线应低于掺水模型的最高分
