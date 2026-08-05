# proxy-sleuth 维护手册

> 如何随着行业快速发展保持工具数据新鲜和检测准确

## 一、定期检查清单

```bash
# 每月 1 号跑一次
cd /path/to/proxy-sleuth
python3 scripts/update_api_params.py check        # API参数指纹
npm update -g llm-fingerprint                      # 统计指纹库
```

---

## 二、什么时候需要手动更新

### 2.1 新模型发布

当 OpenAI、Anthropic、DeepSeek、阿里等发布新的大模型版本时：

**步骤 1：收集信息**
- 去官方博客/公告页查看：模型名称、发布时间、定价、新功能、训练截止日期

**步骤 2：添加知识边界探针**
- 编辑 `data/prompts/knowledge_probes.json`
- 在对应模型组下添加新探针：
```json
{
  "id": "新模型ID",
  "question": "针对新模型发布时间/定价/独有功能的问题",
  "keywords": ["关键词1", "关键词2"],
  "weight": 1.0
}
```
- 如果是一个全新的模型，添加整个 `probe_groups` 新组

**步骤 3：更新 API 特征检测**
- 编辑 `src/detectors/api_features.py`
- 如果新模型有独有 API 参数，添加对应的 `_check_xxx` 探针
- 如果已有参数被新模型也支持了，更新 `model_hint`

**步骤 4：更新自检脚本的跟踪列表**
- 编辑 `scripts/update_api_params.py`
- 在 `TRACKED_FAMILIES` 字典中添加新模型

### 2.2 能力趋同

当前 GPT-5.5 (49.4) 和 DeepSeek V4 (48.3) 差距仅 1.1 分。

- 每季度检查一次 llm-stats.com 的排名
- 如果某对模型的能力差距缩小到 3 分以内，capability 层将不再能区分它们
- 此时应降低 capability 权重（`src/main.py` 第 215 行）或替换更难的新题

### 2.3 CC Switch 大版本更新

CC Switch 是桌面应用，其 SQLite schema 可能随大版本变化。

**检测方式：**
```bash
python3 -c "from src.utils.ccswitch import discover_providers; print(len(discover_providers()))"
# 如果输出 0 但 CC Switch 已配置 → schema 变了
```

**修复方式：**
1. 运行 `python3 -c "import sqlite3; conn=sqlite3.connect('~/.cc-switch/cc-switch.db'); print(conn.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall())"` 查看新表名
2. 编辑 `src/utils/ccswitch.py` 的 `_read_providers` 函数更新表名/列名

### 2.4 阈值校准

当前所有阈值（0.70/0.40/0.30等）都是拍脑袋设的。要校准：

1. 获取真实模型的官方 API key（GPT-5.6、Claude Fable 5、DeepSeek V4）
2. 对每个真实模型跑 10 次 `--mode full`
3. 记录各层得分的均值和方差
4. 调整阈值：MATCH 阈应高于真实模型的最低分，MISMATCH 阈应低于掺水模型的最高分

---

## 三、技术债务清单

| 问题 | 影响 | 修复难度 |
|------|------|:---:|
| 知识探针公开信息泄漏（所有新模型阅读同一批新闻） | gpt56_only 组对 DeepSeek V4 无效 | 高 — 需要获取非公开信息 |
| capability 层无 Anthropic 协议实测 | 对 Claude Code 中转站效果未知 | 中 — 需要 Anthropic API key |
| mixed_routing 检测未在真实路由代理上验证 | 可能假阳性 | 中 — 需要配置了路由的代理 |
| 上下文截断不测 >50K 深度 | 无法检测中等程度的截断 | 低 — 加 DEEP_ROUNDS=800 即可 |

---

## 四、快速诊断命令

```bash
# 检查 API 参数变化
python3 scripts/update_api_params.py check

# 验证 CC Switch 兼容性
python3 -c "from src.utils.ccswitch import discover_providers; print(f'Found {len(discover_providers())} providers')"

# 验证探针数据加载
python3 -c "import json; d=json.load(open('data/prompts/knowledge_probes.json')); print(f'{sum(len(g[\"probes\"]) for g in d[\"probe_groups\"].values())} probes loaded')"

# 验证统计指纹可用
node vendor/fingerprint/bin/fp.js list 2>&1 | head -1

# 运行完整单元测试
python3 -m pytest tests/ -v
```

---

## 五、发布检查清单

每次发布新版本前：

- [ ] `python3 -m pytest tests/` 全部通过
- [ ] `python3 scripts/update_api_params.py check` 无参数变化警告
- [ ] 新模型已加入 knowledge_probes.json
- [ ] DESIGN.md 的"当前模型生态"章节已更新
- [ ] 版本号在 `pyproject.toml` 和 `src/main.py` 中已更新
