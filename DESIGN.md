# LLM Proxy Detector — 中转站模型真伪检测工具 设计文档

> 版本: v1.1 | 日期: 2026-08-05 | 状态: 全部实现 + 行业调研更新

---

## 目录

0. [现有生态调研 & 开发策略](#0-现有生态调研--开发策略)
1. [背景与问题](#1-背景与问题)
2. [中转站技术原理深度分析](#2-中转站技术原理深度分析)
3. [当前模型生态全景 (2026年8月)](#3-当前模型生态全景)
4. [学术研究综述](#4-学术研究综述)
5. [检测方案设计（六层）](#5-检测方案设计)
6. [技术架构](#6-技术架构)
7. [实施计划与里程碑](#7-实施计划与里程碑)
8. [局限性 & 猫鼠博弈](#8-局限性--猫鼠博弈)
9. [附录：参考资源](#9-附录参考资源)

---

## 0. 现有生态调研 & 开发策略

### 0.1 不要重复造轮子

在启动开发前，我们对 GitHub 上所有相关项目做了全面调研。下面是结果：

| 项目 | Stars | 语言 | 定位 | 可复用 |
|------|-------|------|------|:---:|
| **`ToseaAI/llm-fingerprint-detector`** | 9 | TS/JS | "One Token Is Enough" 论文实现，单 token 统计指纹 CLI + 库 | ✅ 集成 |
| **`mintesnot-teshome/llm-verify`** | 13 | Python/FastAPI | 32 个 forensic prompts，身份/能力/指纹检测，诈骗报告生成 | ⚠️ 参考 |
| `lulbitz/llm-con` | 19 | Python | LLM 安全评估框架（含 29 个知识截止探针、模型家族识别） | ⚠️ 参考 |
| 论文官方代码 (Zenodo DOI) | - | Python | 165 个模型采集分析脚本，MIT 许可 | ✅ 参考 |
| `AI45Lab/REEF` | 79 | Python | IP 保护指纹，需模型权重 | ❌ |
| `LUMIA-Group/AWM` | ICLR | Python | 权重矩阵指纹，需模型权重 | ❌ |
| `songquanpeng/one-api` | 36,200 | Go | **中转站核心基础设施**（我们检测的目标） | ❌ 对手 |
| `RockChinQ/free-one-api` | 874 | Python | 逆向工程接口聚合 | ❌ 对手 |

### 0.2 关键发现

**与我们定位最接近的是 `llm-verify` (13⭐)，但它和我们的差异很大：**

| 功能 | llm-verify 已有 | 我们需要 | 决策 |
|------|:---:|:---:|------|
| 身份检测探针 (32 prompts) | ✅ | ✅ | **借鉴设计思路** |
| 知识截止日期不一致检测 | ✅ | ✅ | **借鉴 + 自己扩展到 2026.08 模型** |
| 模型自报身份 vs API 返回名 | ✅ | ✅ | 借鉴 |
| 不同模型名实为同一模型检测 | ✅ | ✅ | 借鉴 |
| 延迟/Token/词汇/格式分析 | ✅ | ✅ | 借鉴 |
| Deep Analysis 一键报告 | ✅ | ✅ | **借鉴思路，改 CLI 实现** |
| 单 token 统计指纹 (论文方法) | ❌ | ✅ | **集成 `llm-fingerprint-detector`** |
| 上下文截断检测 (Needle) | ❌ | ✅ | **自研（核心差异化）** |
| 参数篡改检测 (max_tokens/reasoning) | ❌ | ✅ | **自研（核心差异化）** |
| 混合路由检测 | ❌ | ✅ | **自研（核心差异化）** |
| 2026.08 最新模型专属探针 | ❌ | ✅ | **自研** |
| 中文场景优化 | ❌ | ✅ | **自研** |
| 轻量 CLI (非 Web 服务) | ❌ (FastAPI) | ✅ | **自研框架** |

### 0.3 最终策略：组合 + 扩展

```
llm-proxy-detector
├── 集成层: llm-fingerprint-detector (Node subprocess)  ← 单 token 统计指纹
├── 借鉴层: llm-verify 的 32 forensic prompts 设计思路   ← 身份/能力探针
├── 自研层: 上下文截断 + 参数篡改 + 混合路由检测         ← 核心差异化
└── 自研层: 2026.08 最新模型专属知识探针 + 中文优化      ← 时效性
```

**不基于任何现有项目 fork，而是用 Python 从零搭建轻量 CLI，有选择地集成和借鉴。**

---

## 1. 背景与问题

### 1.1 问题定义

国内大量用户通过"中转站"（也称为中转 API、代理 API）使用 Claude Code、Codex CLI 等工具连接大模型。中转站声称提供 GPT-5.6 Sol、Claude Fable 5 等顶级模型，价格仅为官方价格的 1/10 甚至更低。**但用户无法验证中转站实际调用的是什么模型。**

核心问题：
- 中转站是否将请求转发到声称的模型？
- 是否在"掺水"——对部分请求使用便宜模型（如 DeepSeek、Qwen）替代？
- 是否存在"混合路由"——简单请求用便宜模型，复杂请求用真模型？
- 是否篡改请求参数（截断上下文、降低 reasoning effort 等）来节省成本？

### 1.2 目标用户

- 使用中转站 + Claude Code / Codex CLI 的开发者
- 想验证中转站是否靠谱的普通用户
- API reseller 的竞争对手或审计方

### 1.3 工具定位

**不是学术实验，而是面向真实用户的实用工具。**
- CLI 界面，一键运行
- 输出可读的结论："该接口有 87% 概率是 DeepSeek V4，而非声称的 GPT-5.6 Sol"
- 支持持续更新模型指纹库
- 开源 (MIT)，社区维护

---

## 2. 中转站技术原理深度分析

### 2.0 核心基础设施：one-api 项目 (36,200 Stars)

通过深入分析 `songquanpeng/one-api` 的源码，该项目是中转站生态的核心基础设施。绝大多数中文中转站直接部署此项目或其 fork。

**one-api 的架构本质：**
```
用户 (OpenAI API 格式请求)
  → one-api (Go 后端, 端口 3000)
    → 渠道路由 (负载均衡、优先级、健康检查)
      → 上游渠道 (OpenAI/Azure/Claude/DeepSeek/...)
```

**从源码确认的内建"掺水"机制：**

1. **模型映射 (Model Mapping)**：官方文档明确写道："支持模型映射，重定向用户的请求模型...设置之后会导致请求体被重新构造而非直接透传"。中转站可以将 `gpt-5.6-sol` 的请求静默重定向到 `deepseek-v4`。

2. **分组倍率 (Group Ratio)**：`model/option.go:69`，`ModelRatio` 动态 JSON 配置，中转站可调高"高级模型"的计价倍率。

3. **补全倍率 (Completion Ratio)**：`model/option.go:71`，输出 token 独立定价。

4. **近似 Token 计数 (ApproximateTokenEnabled)**：`model/option.go:38`，开启后估算而非精确计数计费。

5. **预扣额度 (PreConsumedQuota)**：`model/option.go:66`，请求前预扣，失败不一定全额退回。

6. **失败自动重试 (RetryTimes)**：`model/option.go:73`，可先尝试便宜渠道，失败后再用贵渠道。

### 2.1 中转站的六种运作方式与掺水手段

#### 方式一：账号农场 (Account Farming)
```
用户请求 → 中转站 → 批量注册的免费/付费账号 → 官方 API
```
- 通过 `free-one-api` (874 stars) 等工具聚合逆向接口
- **模型版本是真的，但账号来源不合法**
- **可能的掺水**：账号池混入低级别账号（Plus 账号只能调 Luna，但声称是 Sol）

#### 方式二：协议逆向 (Protocol Reverse Engineering)
```
用户请求 → 中转站 → 模拟官方客户端 (Claude Code/Codex CLI) → 官方后端
```
- 逆向 Claude Code CLI / Codex CLI 的私有协议
- 中转站充当"协议转换器"：外部暴露标准 OpenAI API，内部用私有协议调官方后端
- **模型是真的，但用的是客户端配额而非 API 付费**
- **可能的掺水**：客户端配额用完后自动降级到便宜模型

#### 方式三：模型替换 (Model Substitution) — 最常见掺水
```
用户请求 → 中转站(one-api) → 模型映射 → DeepSeek/Qwen API (而非声称的 GPT-5.6)
```
- 技术实现：one-api 的"模型映射"功能，在数据库中配置重定向规则
- 用户发送 `model: "gpt-5.6-sol"` → one-api relay 层改为 `model: "deepseek-v4"`
- 利润：GPT-5.6 Sol 输出 $30/M vs DeepSeek V4 Flash 输出 $0.28/M，差价 **107倍**
- 常见模式：
  - **完全替换**：所有模型都映射到同一个便宜模型
  - **降级映射**：Sol→Terra, Terra→Luna, Luna→DeepSeek (阶梯降级)
  - **同厂商降级**：GPT-5.6 Sol→GPT-5.5, Claude Fable 5→Opus 4.8

#### 方式四：混合路由 (Mixed Routing) — 最隐蔽掺水
```
用户请求 → 中转站 → 复杂度判断 → {
    简单请求 → DeepSeek ($0.28/M)
    复杂请求 → GPT-5.6 Sol ($30/M)
}
```
- 技术实现：one-api 渠道优先级 + fallback 机制
- 判断依据（推测）：输入 token 数、时间窗口、用户等级、对话轮次
- 利润：假设 80% 走 DeepSeek (成本 ~$0.3/M)，20% 走 GPT-5.6 (成本 $30/M)，加权成本 = $6.24/M，按 $30/M 收费，毛利率 **79%**

#### 方式五：上下文截断与参数篡改 — 降智掺水
```
用户请求(100K context) → 中转站 → 截断到 32K → 上游 API (节省 token 成本)
```
- **上下文截断**：在 relay 层裁剪 `messages` 数组，丢弃早期对话轮次；截断 tools/function definitions
- **参数篡改**：one-api 的 `ConvertRequest` 方法可任意修改请求体：
  - `max_tokens` → 如从 4096 改为 1024
  - `temperature` → 强制固定值
  - `reasoning_effort` → 强制设为 low（GPT-5.6 reasoning 是最贵的部分）
  - `tools` → 移除以减少 input tokens
  - `stream` → 关闭流式以批量处理

#### 方式六：模型拼接 (Response Mixing) — 最极端掺水
```
真模型: 生成推理框架 (10% tokens) + 便宜模型: 填充内容 (90% tokens) → 拼接响应
```
- 目前主要在理论阶段，实际应用较少（技术复杂度高）

### 2.2 中转站可操作的技术空间（完整矩阵）

| 篡改点 | 技术实现 | 难度 | 降智影响 | 我们的检测方法 |
|--------|----------|------|----------|----------------|
| **模型替换** | Model Mapping | 低 | 严重 | 知识边界探针 + 统计指纹 |
| **上下文截断** | 裁剪 messages 数组 | 低 | 严重(失忆) | Needle-in-Haystack 测试 |
| **Output token 限制** | 修改 max_tokens | 低 | 中等(不完整) | 长输出 + 标记验证 |
| **Reasoning 降级** | 修改 reasoning_effort | 低 | 严重(推理弱) | 深度推理题 + token 分析 |
| **Temperature 篡改** | 修改 temperature/top_p | 低 | 轻(输出差) | 多次采样一致性 |
| **移除 tools** | 删除 function definitions | 低 | 严重(无法用工具) | Tool calling 测试 |
| **混合路由** | 渠道优先级 + fallback | 中 | 中等(不一致) | 压力切换 + 一致性测试 |
| **System prompt 注入** | relay 层添加/替换 | 低 | 中等(风格伪装) | 用知识/能力测试(非风格) |
| **Token 计费放水** | ApproximateTokenEnabled | 低 | 不影响质量 | 独立 token 计数对比 |
| **超时切断** | RELAY_TIMEOUT | 低 | 中等 | 长时间任务测试 |

### 2.3 中转站无法轻易掩盖的特征

| 特征类型 | 原理 | 抗伪装 | 为什么 |
|----------|------|:---:|--------|
| **知识边界** | 训练截止日期无法伪造 | ⭐⭐⭐⭐⭐ | 便宜模型无法"学会"未来事件 |
| **单 token 概率分布** | 随机数分布是模型固有的 | ⭐⭐⭐⭐⭐ | System prompt 几乎不影响 |
| **独有功能支持** | PTC, ultra 模式等 | ⭐⭐⭐⭐⭐ | 便宜模型不支持 |
| **安全分类器行为** | Fable 5 的 5% fallback | ⭐⭐⭐⭐⭐ | 独有行为，无法模拟 |
| **Tool calling 质量** | 复杂工具调用准确性 | ⭐⭐⭐⭐ | 能力硬指标 |
| **代码执行能力** | 生成的代码能否运行 | ⭐⭐⭐⭐ | 代码正确性无法伪装 |
| **Reasoning token 格式** | 思维链格式和风格 | ⭐⭐⭐⭐ | 各模型格式不同 |
| **长上下文理解** | 128K+ token 检索 | ⭐⭐⭐ | 可被截断掩盖，需配合检测 |
| **多语言一致性** | 中/英能力比例 | ⭐⭐⭐ | 国产模型中文强英文弱 |
| **流式响应时序** | Token 间时间间隔 | ⭐⭐ | 网络延迟干扰大 |

---

## 3. 当前模型生态全景 (2026年8月)

### 3.1 目标模型详情

#### OpenAI 系列

**GPT-5.6 Sol** (2026-07-09 发布，07-30 保持原价)
- 定价: $5 input / $30 output per 1M tokens
- 知识截止: ~2026-06 ~ 07
- Coding Agent Index: 80 (SOTA) | Terminal-Bench 2.1: 88.8%
- 独有功能: Programmatic Tool Calling (PTC), ultra 多 agent 模式
- Reasoning: max/xhigh/high/medium/low 五档
- 行为: 结构化回复、代码注释详细、擅长前端/设计

**GPT-5.6 Terra** (07-30 降至 $2/$12)
- Coding Agent Index: 77.4，性能接近 GPT-5.5 但更便宜

**GPT-5.6 Luna** (07-30 降至 $0.20/$1.20，降幅 80%)
- Coding Agent Index: 74.6，最快最便宜

#### Anthropic 系列

**Claude Fable 5** (2026-06-09 发布)
- 定价: $10/$50 per 1M tokens
- 知识截止: ~2026-05
- SWE-Bench Pro: 80% | Terminal-Bench 2.1: 83.1%
- 极长自主工作能力（可运行数天），极强 vision
- **关键检测特征：约 5% session 触发安全分类器，回退到 Opus 4.8 并明确告知用户**
- 行为: 回复较长有总结习惯、谨慎保守

**Claude Opus 4.8** (前旗舰): $15/$75 per 1M tokens

#### DeepSeek 系列

**DeepSeek V4 Flash** (2026-07-31 发布)
- 定价: $0.14/$0.28 per 1M tokens（缓存命中 $0.0028）
- 1M context，支持 thinking/non-thinking
- 功能: JSON output, Tool Calls, FIM Completion, Anthropic API 兼容

**DeepSeek V4 Pro**: $0.435/$0.87 per 1M tokens

#### 常见替代模型（用于掺水）

**Qwen 系列**: 国产，中文母语级，英文弱于 GPT/Claude，价格极低。论文 "One Token Is Enough" 已验证有中转站用 Qwen 冒充旗舰模型。

**Gemini 3.1 Pro Preview**: Google 模型，某些指标接近 GPT-5.5。

### 3.2 模型价格对比（掺水经济学）

| 模型 | 输入 $/1M | 输出 $/1M | vs GPT-5.6 Sol |
|------|-----------|-----------|:---:|
| GPT-5.6 Sol | $5 | $30 | 基准 |
| Claude Fable 5 | $10 | $50 | 1.7x 更贵 |
| GPT-5.6 Luna | $0.20 | $1.20 | 25x 更便宜 |
| DeepSeek V4 Flash | $0.14 | $0.28 | **107x 更便宜** |
| DeepSeek V4 Flash (cache hit) | $0.0028 | $0.28 | **5000x 更便宜** |

**这就是掺水的经济学动机：用 DeepSeek 冒充 GPT-5.6，利润 100 倍。**

### 3.3 关键区分特征矩阵

| 特征 | GPT-5.6 Sol | Claude Fable 5 | DeepSeek V4 | Qwen |
|------|---------|----------------|-------------|------|
| 知识截止 | 2026-07 | 2026-05 | ~2026-04 | ~2025-Q4 |
| 工具调用质量 | 优秀(PTC) | 优秀 | 良好 | 一般 |
| Coding (AAI) | 80 (SOTA) | 77.2 | 中上 | 中等 |
| 中文质量 | 好 | 好(非母语) | 母语级 | 母语级 |
| 回复风格 | 结构化,友好 | 详细,谨慎 | 直接,少废话 | 中文习惯 |
| 安全分类器 | 有(较弱) | **5% fallback** | 有 | 有 |
| 独有功能 | ultra, PTC | 分类器回退 | thinking模式 | - |
| 协议 | OpenAI | Anthropic | OpenAI+Anthropic | OpenAI |

---

## 4. 学术研究综述

### 4.1 核心论文

#### "One Token Is Enough" (Bruckner, 2026-07) ⭐⭐⭐⭐⭐ 核心参考
- arXiv:2607.10252 | 代码: Zenodo DOI 10.5281/zenodo.21278793 (MIT)
- 只需 1 个 output token 即可做模型指纹
- "name a random number between 1 and 100" 在 4 种语言中
- 165 个模型测试 | 59.5% 准确率 vs 18.4% 随机
- 7.3% equal error rate with full 40-cell battery
- **发现了一个声称是旗舰模型但实际是 Qwen 的端点**
- 开源实现: `ToseaAI/llm-fingerprint-detector` (我们直接集成)

#### "LLMs Have Rhythm" (Alhazbi et al., 2025)
- Inter-token 时间间隔指纹 | 16+10 模型 | 网络延迟干扰大

#### "Hide and Seek" (Iourovitski et al., 2024)
- 进化算法自动发现指纹 prompt | 72% 模型族准确率

### 4.2 检测方法分类

| 方法 | 所需信息 | 准确度 | 抗伪装 | 适用性 |
|------|----------|--------|--------|--------|
| 单 token 分布 | 多次采样(~200次) | 中高 | 高 | ⭐⭐⭐ |
| 知识边界探针 | 单次查询 | 高 | 高 | ⭐⭐⭐⭐⭐ |
| 独有功能检测 | 单次调用 | 高(二值) | 高 | ⭐⭐⭐⭐⭐ |
| 能力基准 | 多次查询 | 高 | 中 | ⭐⭐⭐⭐ |
| 上下文截断检测 | 单次长对话 | 高(二值) | 高 | ⭐⭐⭐⭐ |
| 参数篡改检测 | 多次采样 | 中 | 中高 | ⭐⭐⭐ |
| 风格指纹 | 单次查询 | 中 | 低(prompt可覆盖) | ⭐⭐ |

---

## 5. 检测方案设计

### 5.1 总体策略：六层递进

```
第零层: 参数完整性验证 — 检测中转站是否篡改了请求参数
  ↓
第一层: 上下文截断检测 — 检测中转站是否裁剪了上下文
  ↓
第二层: 接口特征检测 — 快速排除不匹配的模型
  ↓
第三层: 知识边界探针 — 高置信度模型识别（最可靠）
  ↓
第四层: 统计指纹 — 单 token 分布匹配（集成 llm-fingerprint-detector）
  ↓
第五层: 能力基准 — coding/math/中文 实际表现测试
  ↓
第六层: 混合路由检测 — 一致性压力测试
  ↓
综合评分 & 结论报告
```

### 5.2 第零层：参数完整性验证

**目的**：验证中转站是否篡改了请求参数。这是最常见的"降智"原因。

#### 测试项

**1. max_tokens 篡改检测**
- 发送 `max_tokens: 4096`，要求在末尾输出标记 `[END_OF_OUTPUT_VERIFY]`
- 如果响应提前截断 → max_tokens 被改小
- 成本: 1 次请求, ~$0.01

**2. reasoning_effort 篡改检测 (GPT-5.6 专属)**
- 发送 `reasoning_effort: "max"` + 深度推理数学题
- 分析: a) 思维链详细程度 b) 总输出 token 数 c) 回答质量
- max 通常 5000+ tokens，low 通常 <1000 tokens

**3. temperature 篡改检测**
- 发送 `temperature: 2.0` + 要求输出随机数
- 同一 prompt 发 10 次，计算输出多样性（低温度结果高度一致）

**4. tools/function 定义篡改检测**
- 发送 10 个自定义 function 的 tool call 请求
- 如果 tools 被移除，模型会回复文本而非 tool call

**5. system prompt 完整性检测**
- 在 system prompt 中藏密语: 要求以 `[INTEGRITY_CHECK:OK]` 开头
- 检查响应开头是否包含完整标记

### 5.3 第一层：上下文截断检测（Needle-in-Haystack）

**目的**：验证中转站是否截断了长上下文。"降智"的第二大原因。

#### 测试项

**1. 针堆测试**
```
构建 50 轮对话历史，第 5 轮藏入密钥：
  "记住这个密钥：VERIFY_KEY_8a3f2c1e"
第 50 轮: "请复述之前让你记住的密钥"

- 能正确复述 → 上下文 ≥50 轮, 未被截断
- 不知道 → 上下文被截断到 <5 轮
```

**2. 多级压力测试**
- 10 轮、20 轮、50 轮、100 轮召回测试
- 定位精确的截断点
- 估算实际可用 context 上限

**3. Tool 结果保留测试**
- 模拟 Codex CLI / Claude Code 的工具调用流程
- 多工具调用后测试是否还记得第一个工具的结果

### 5.4 第二层：接口特征检测

**目的**：快速识别 API 特征，排除不匹配模型。

| 测试项 | 方法 | 特征价值 |
|--------|------|----------|
| PTC 支持检测 | 发送 PTC 请求 | GPT-5.6 独有，如果支持则极大概率是真 |
| Anthropic API 兼容 | 发送 Anthropic 格式请求 | DeepSeek 支持，GPT 不支持，Claude 原生支持 |
| Tool call 格式 | 标准 tool call 请求 | 格式/参数命名习惯各模型不同 |
| 流式格式 | 流式请求分析 delta 结构 | OpenAI vs Anthropic 格式 |
| Reasoning effort | 非标准 effort 值测试 | 看模型是否接受 |
| 模型名自报 | "What model are you?" | 可被 system prompt 覆盖，仅参考 |

### 5.5 第三层：知识边界探针（最可靠）

**原理**：训练截止日期无法被 system prompt 伪造。这是权重最高的检测方法。

#### 关键时间节点 (2026-08)

| 事件 | 日期 | GPT-5.6 | Claude Fable 5 | DeepSeek V4 | Qwen |
|------|------|:---:|:---:|:---:|:---:|
| GPT-5.6 正式发布 | 07-09 | ✅ | ❌ (~05 截止) | ❌ | ❌ |
| GPT-5.6 Sol 预览 | 06-26 | ✅ | ❌ | ❌ | ❌ |
| Claude Fable 5 发布 | 06-09 | ✅ | ✅ | ❌ | ❌ |
| GPT-5.6 降价 (Luna -80%) | 07-30 | ✅ | ❌ | ❌ | ❌ |
| DeepSeek V4 发布 | 07-31 | ✅ | ❌ | ✅ | ❌ |

#### 探针组 A: GPT-5.6 专属（只有 GPT-5.6 知道）

```python
GPT56_ONLY = [
    "What is the pricing per million tokens for GPT-5.6 Sol?",
    "What are the three model names in OpenAI's GPT-5.6 family?",
    "What is 'ultra' mode in GPT-5.6 and how many agents does it coordinate?",
    "Which models were affected by OpenAI's July 30, 2026 price cuts?",
    "What is Programmatic Tool Calling and which model introduced it?",
]
```

#### 探针组 B: Claude Fable 5 相关

```python
FABLE5 = [
    "When was Claude Fable 5 released and what company created it?",
    "What happens when Claude Fable 5's safety classifiers detect sensitive requests?",
    "What is the Mythos-class model tier and how does Fable 5 relate to it?",
]
```

#### 探针组 C: DeepSeek V4 相关

```python
DEEPSEEK = [
    "When was DeepSeek V4 released and what two variants are available?",
    "What is the pricing for DeepSeek V4 Flash per million tokens?",
    "What special pricing policy is DeepSeek introducing for peak hours?",
]
```

#### 探针组 D: 反向验证（确保不是过旧模型）

```python
REVERSE = [
    "When was GPT-4o first released?",  # May 2024, 所有现代模型都应知道
    "Who won the 2024 US presidential election?",  # 所有2025+模型都应知道
]
```

#### 执行策略
- 每个探针用不同措辞问 3 次 (temperature=0.1)
- 关键词匹配打分 (0/0.5/1)
- 阈值: 声称 GPT-5.6 但专属探针 <60% → 确定假模型
- 成本: 15 探针 × 3 次 = 45 请求, ~5000 tokens, <$0.15

### 5.6 第四层：统计指纹

**策略：集成 `llm-fingerprint-detector` (Node.js CLI)**

不是自己实现 JS 散度计算，而是直接用已发布的成熟工具。

```bash
# 在我们的 Python CLI 中调用
subprocess.run([
    "npx", "llm-fingerprint-detector", "verify",
    "--base-url", target_endpoint,
    "--model", claimed_model,
    "--reference", "bundled/gpt-5.6-sol",
    "--api-key-env", "LLM_DETECT_KEY",
])
```

输出解析后集成到我们的综合评分中：
- `match` (meanJsd ≤ 0.25) → +1.0
- `uncertain` (0.25-0.35) → +0.5
- `mismatch` (> 0.35) → +0.0

同时利用其内置的 11 个模型预置参考指纹（GPT-4o, Claude Sonnet 4.5, DeepSeek-chat, Qwen3 等）。

**我们的附加值**：当 `llm-fingerprint-detector` 报告 mismatch 时，尝试匹配到具体是哪个已知模型（用我们自建的 Python 分析层）。

### 5.7 第五层：能力基准测试

**目的**：用精选高鉴别力题目测试实际能力。

```python
CAPABILITY = {
    "coding": 5 道 (LiveCodeBench/HumanEval 精选),
    "math": 5 道 (GSM8K/MATH 鉴别力最高题),
    "reasoning": 3 道逻辑推理,
    "chinese": 3 道中文理解/生成,
}
```

自动评分：代码题 → Python 沙箱执行；数学题 → 答案正则匹配；中文题 → 关键词评分。

**预期分数 (用于匹配)**：

| 题目组 | GPT-5.6 Sol | Claude Fable 5 | DeepSeek V4 Pro | Qwen Max |
|--------|:---:|:---:|:---:|:---:|
| Coding (5) | 4-5 | 4-5 | 3-4 | 2-3 |
| Math (5) | 4-5 | 4-5 | 3-4 | 3-4 |
| Chinese (3) | 3 | 2-3 | 3 | 3 |

### 5.8 第六层：混合路由检测

**目的**：检测中转站是否按请求复杂度动态切换模型。

#### 混合路由常见触发规则

| 触发条件 | 走便宜模型 | 走真模型 | 原因 |
|----------|:---:|:---:|------|
| 输入 <1000 tokens | ✅ | - | 简单请求 |
| 输入 >10000 tokens | - | ✅ | 复杂请求 |
| 高峰期 (9-12am, 2-6pm BJT) | ✅ | - | 节省成本 |
| 短对话 (<5轮) | ✅ | - | 简单对话 |
| 长对话 (>20轮) | - | ✅ | 需要上下文 |
| 含 system prompt | - | ✅ | "正经"使用 |

#### 检测策略

**1. 压力切换测试**
连续 30 个请求，交替极简单和极困难：
- 如果困难请求正确率比简单请求高 → 可疑！（简单请求走了便宜模型）
- 分析每轮的 token 数、质量、风格一致性

**2. 同题不同表述**
同一道数学题用三种方式问：
- A: 直接用英语（像 API 测试）
- B: 嵌入长对话（像正常使用）
- C: 用简单措辞（像简单请求）
- A 对、B 对、C 错 → C 被路由到便宜模型

**3. 长 session 一致性**
50+ 轮对话，每隔 5 轮注入记忆标记，监控质量变化。
如果风格/能力突然跳变 → 模型被切换。

**4. 高峰 vs 低谷对比**
在 9:00-18:00 和凌晨 2:00-5:00 分别运行同一组测试。
高峰期明显更差 → 高峰期用便宜模型。

### 5.9 综合评分算法

```python
def evaluate(target, claimed_model):
    scores = {
        "param_integrity":   0.05,  # 参数完整性
        "context_truncation": 0.10,  # 上下文截断
        "api_features":      0.10,  # 接口特征
        "knowledge_probes":  0.25,  # 知识边界 (最高权重)
        "statistical_fingerprint": 0.20,  # 统计指纹
        "capability":        0.20,  # 能力基准
        "mixed_routing":     0.10,  # 混合路由
    }

    weighted_score = sum(scores[k] * run_detector(k) for k in scores)
    actual_model = identify_best_match(all_detector_results)

    return {
        "verdict": "MISMATCH" if weighted_score < 0.5 else "MATCH",
        "claimed_model": claimed_model,
        "most_likely_model": actual_model,
        "confidence": weighted_score,
        "red_flags": collect_red_flags(),
        "details": all_raw_scores,
    }
```

---

## 6. 技术架构

### 6.1 项目结构

```
llm-proxy-detector/
├── README.md
├── DESIGN.md                     # 本文件
├── pyproject.toml
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── main.py                   # CLI 入口 (click/typer)
│   ├── config.py                 # 配置管理
│   ├── detectors/
│   │   ├── __init__.py
│   │   ├── param_integrity.py    # 第零层: 参数篡改检测
│   │   ├── context_truncation.py # 第一层: 上下文截断检测
│   │   ├── api_features.py       # 第二层: 接口特征检测
│   │   ├── knowledge_probes.py   # 第三层: 知识边界探针
│   │   ├── statistical.py        # 第四层: 统计指纹 (集成 Node CLI)
│   │   ├── capability.py         # 第五层: 能力基准测试
│   │   └── mixed_routing.py      # 第六层: 混合路由检测
│   ├── analyzers/
│   │   ├── __init__.py
│   │   ├── scorer.py             # 综合评分引擎
│   │   └── reporter.py           # 报告生成 (终端彩色/JSON/HTML)
│   ├── baselines/
│   │   ├── __init__.py
│   │   ├── collector.py          # Baseline 数据采集
│   │   └── data/                 # 预存 baseline (Python pickle/JSON)
│   └── utils/
│       ├── __init__.py
│       ├── api_client.py         # 统一 API 调用 (OpenAI + Anthropic 双协议)
│       └── sandbox.py            # 代码执行沙箱
├── tests/
│   ├── test_detectors/
│   └── test_analyzers/
└── data/
    ├── prompts/                  # JSON prompt 模板库
    │   ├── knowledge_probes.json
    │   ├── statistical_probes.json
    │   └── capability_probes.json
    └── baselines/                # 已知模型参考数据
        ├── gpt-5.6-sol/
        ├── claude-fable-5/
        ├── deepseek-v4-flash/
        └── qwen-max/
```

### 6.2 CLI 设计

```bash
# === 快速检测 (知识边界 + 指纹 + 参数完整性) ===
llm-detect --endpoint https://proxy.example.com/v1 \
           --model gpt-5.6-sol \
           --api-key sk-xxx

# === 完整检测 (所有六层) ===
llm-detect --full \
           --endpoint https://proxy.example.com/v1 \
           --model claude-fable-5

# === 仅检测参数篡改 ===
llm-detect --mode params \
           --endpoint https://proxy.example.com/v1 \
           --model gpt-5.6-sol

# === 仅检测上下文截断 ===
llm-detect --mode context \
           --endpoint https://proxy.example.com/v1

# === 检测混合路由 (需要较长时间) ===
llm-detect --mode routing \
           --endpoint https://proxy.example.com/v1 \
           --model gpt-5.6-sol \
           --peak-hours  # 在高峰期运行

# === 输出格式 ===
llm-detect ... --output json    # JSON
llm-detect ... --output html    # HTML 报告
llm-detect ... --output term    # 彩色终端 (默认)

# === Baseline 采集 ===
llm-detect baseline collect \
           --endpoint https://api.openai.com/v1 \
           --model gpt-5.6-sol \
           --api-key sk-real-key

# === 列出已知 baseline ===
llm-detect baseline list
```

### 6.3 核心数据流

```
User Input (endpoint, claimed_model, api_key)
    │
    ├──> 0: Param Integrity Detector
    │    ├── max_tokens test (1 request)
    │    ├── reasoning_effort test (1 request, if GPT)
    │    ├── temperature test (10 requests)
    │    ├── tools test (1 request)
    │    └── system prompt test (1 request)
    │
    ├──> 1: Context Truncation Detector
    │    ├── 50-round needle test (1 multi-turn session)
    │    └── 10/20/100-round stress tests
    │
    ├──> 2: API Features Detector
    │    ├── PTC support (1 request)
    │    ├── Anthropic format compat (1 request)
    │    └── tool call format (1 request)
    │
    ├──> 3: Knowledge Probe Engine
    │    ├── 15 probes × 3 variants = 45 requests
    │    └── keyword matching + scoring
    │
    ├──> 4: Statistical Fingerprint (subprocess → Node CLI)
    │    ├── 200 single-token requests
    │    └── JS divergence → match/mismatch
    │
    ├──> 5: Capability Benchmark
    │    ├── 5 coding + 5 math + 3 reasoning + 3 Chinese
    │    └── auto-grading
    │
    └──> 6: Mixed Routing Detector
         ├── 30 alternating difficulty requests
         ├── same-question-3-ways test
         └── 50-round consistency test

              ↓
         Scorer → 加权综合 → 结论报告
```

### 6.4 成本估算

| 模式 | 检测层 | 请求数 | 估计 tokens | 成本(GPT-5.6) | 成本(DeepSeek) |
|------|--------|--------|-------------|---------------|----------------|
| **Quick** | 0,2,3,4 | ~260 | ~15K | ~$0.25 | ~$0.005 |
| **Standard** | 0,1,2,3,4 | ~320 | ~30K | ~$0.80 | ~$0.01 |
| **Full** | 全部六层 | ~380 | ~100K | ~$2.50 | ~$0.03 |

- **Quick**: < 2 分钟, <$0.30，适合日常快速验证
- **Standard**: < 4 分钟, <$1，推荐
- **Full**: < 8 分钟, <$3，适合深度调查或纠纷场景

---

## 7. 实施计划与里程碑

### Phase 1: 基础框架 + 知识探针 (Week 1)

**目标**: Python CLI 可用 + 最可靠的知识边界检测

- [ ] 项目脚手架: `pyproject.toml`, `click`/`typer` CLI
- [ ] 统一 API client: 支持 OpenAI + Anthropic 双协议
- [ ] 知识探针题库 v1: 20 道题覆盖 GPT-5.6/Claude/DeepSeek
- [ ] 基础评分 + 终端彩色报告
- [ ] 用真实 API (GPT-5.6/Claude/DeepSeek) 验证探针准确性

**交付物**: `llm-detect --mode knowledge` 可工作

### Phase 2: 参数完整性 + 上下文截断 (Week 1-2)

**目标**: 两个核心差异化检测层

- [ ] 参数篡改检测: max_tokens, reasoning_effort, temperature, tools, system prompt
- [ ] 上下文截断检测: 50轮 needle 测试, 多级压力测试
- [ ] 混合路由初版: 交替难度测试

**交付物**: `llm-detect --mode params`, `--mode context` 可工作

### Phase 3: 统计指纹集成 (Week 2)

**目标**: 集成 `llm-fingerprint-detector`

- [ ] Python subprocess 封装
- [ ] 结果解析 + 集成到评分系统
- [ ] 采集/更新 baseline 数据流程

**交付物**: 统计指纹层集成完成

### Phase 4: 能力基准 + 全面测试 (Week 2-3)

**目标**: 能力基准测试 + 在真实中转站上全面验证

- [ ] 精选 16 道高鉴别力题目
- [ ] 代码执行沙箱
- [ ] 收集各模型 baseline 分数
- [ ] 在 3-5 个真实中转站上跑完整测试
- [ ] 根据结果调优权重和阈值

**交付物**: `llm-detect --full` 完整可用

### Phase 5: 完善 + 发布 (Week 3)

**目标**: 文档 + 打磨 + 开源

- [ ] JSON/HTML 报告导出
- [ ] README + 使用文档
- [ ] 贡献指南
- [ ] CI/CD (GitHub Actions)
- [ ] pip 发布

**交付物**: v1.0 正式发布

### Phase 6: 持续维护

- [ ] 新模型上线 → 更新探针题库 + 采集 baseline
- [ ] 社区反馈 → 优化检测逻辑
- [ ] 中转站反制 → 猫鼠博弈迭代

---

## 8. 局限性 & 猫鼠博弈

### 8.1 做不到的

1. **100% 确定**：混合路由 5% 掺水、模型拼接等高级手段可能漏检
2. **模型拼接检测**：真模型生成框架 + 假模型填充内容，极难检测
3. **精心 prompt 工程的反制**：中转站可在 system prompt 中注入知识来绕过知识边界探针
4. **API 层面限制**：中转站可能限制 logprobs、修改流式格式等

### 8.2 对抗策略

| 中转站反制 | 我们的应对 |
|------------|------------|
| 注入知识到 system prompt | 用多轮独立对话 + 不同角度问同一问题 |
| 对探针返回"不知道" | 比对"不知道"的比例是否异常（真模型该知道的也不知道） |
| 限制采样频率 | 分散时间采样, 用多个 API key |
| 限制 token 输出 | 单 token 探针 (如随机数), 模型切换不影响 |
| 模型混合路由 | 一致性压力测试 + 高峰/低谷对比 |
| 截断 reasoning token | 用功能测试(代码正确性)而非推理质量 |

### 8.3 伦理声明

- 本工具仅用于**消费者权益保护**，帮助用户验证 API 服务真实性
- 不应被用于**攻击中转站**或**绕过服务条款**
- Baseline 采集使用的真实 API 响应应符合各厂商使用条款

---

---

## 附录 B: 行业更新 (2026-08-05)

基于 llm-stats.com 最新排名和 Anthropic 官方公告的实时调研结果。

### 模型能力快速趋同

```
2026-08 排名:
#1  GPT-5.6 Sol      57.2  ($5/$30)
#2  Claude Opus 5    56.5  ($5/$25) ← 7月24日新发布
#3  Claude Fable 5   56.3  ($10/$50)
#5  Kimi K3          55.4  (Moonshot AI)
#7  Qwen3.8 Max      52.5  (阿里)
#11 GPT-5.5          49.4
#13 DeepSeek V4 Flash 48.3 ($0.09/$0.18) ← 仅差1.1分!
#15 GLM-5.2           46.8  (智谱)
```

**关键洞察**：DeepSeek V4 Flash 和 GPT-5.5 能力差距仅 1.1 分，但价格差 55 倍（$0.18 vs $7.78 per 1M output）。这解释了为什么掺水动机如此强烈——用 DeepSeek 冒充 GPT-5.5，用户几乎察觉不到能力差异。

### 对检测策略的影响

1. **能力基准权重应降低** (20% → 10%)：模型趋同，做题分数不再可靠区分
2. **统计指纹权重应提高** (20% → 25%)：不受能力趋同影响
3. **知识边界仍是王牌** (25%)：训练截止日期不会趋同
4. **接口特征变重要**：Claude Opus 5、Kimi K3、Qwen3.8 Max 各有独有协议特征

### 新增可检测特征

| 模型 | 独有特征 |
|------|----------|
| Claude Opus 5 | $5/$25 定价, 比 Fable 5 更少的安全分类器拦截(~85% less) |
| Kimi K3 | 阿里 DashScope 协议, Anthropic 格式兼容 |
| Qwen3.8 Max | 阿里 DashScope, 中文原生优化, 支持 include_reasoning |
| GLM-5.2 | Z.ai API 格式, Anthropic 兼容 |

### API 参数指纹（关键突破 ⭐）

基于 OpenRouter 模型元数据，发现各模型接受的**独有 API 参数**：

| 参数 | GPT-5.6 | Claude | DeepSeek V4 | Qwen3.8 | 区分价值 |
|------|:---:|:---:|:---:|:---:|------|
| `min_p` | ❌ | ❌ | ✅ | ❌ | DeepSeek 独有签名 |
| `top_a` | ❌ | ❌ | ✅ | ❌ | DeepSeek 独有签名 |
| `include_reasoning` | ❌ | ❌ | ✅ | ✅ | GPT/Claude vs 国产 |
| `frequency_penalty` | ❌ | ❌ | ❌ | ✅ | Qwen 独有 |
| `repetition_penalty` | ❌ | ❌ | ✅ | ❌ | DeepSeek 独有 |
| `logit_bias` | ❌ | ❌ | ✅ | ❌ | DeepSeek 独有 |
| `reasoning_effort` | ✅ | ❌ | ✅ | ✅ | **不再独有！** |

**重要修正**：之前认为 `reasoning_effort` 是 GPT 独有参数，但 DeepSeek V4 和 Qwen3.8 都支持它。已更新检测逻辑。

发送 `min_p` 或 `top_a` → 如果接受，模式是 DeepSeek V4。这是最硬证据之一，比能力测试更可靠。

### 竞争态势

- `api-dna.com` — 宣称做 API 模型检测 (域名已挂/502)
- `llm-fingerprint` — npm 包 (我们已 vendor)
- `llm-verify` — FastAPI 检测框架 (13 stars)
- 没有其他成规模的同类开源工具
- **proxy-sleuth 是目前最完整的开源 LLM API 真伪检测工具**

### 9.1 学术论文

| 论文 | 来源 | 价值 |
|------|------|------|
| "One Token Is Enough" (Bruckner, 2026) | arXiv:2607.10252 | ⭐⭐⭐⭐⭐ 核心方法 |
| "LLMs Have Rhythm" (Alhazbi, 2025) | arXiv:2502.20589 | ⭐⭐⭐ 时间特征 |
| "Hide and Seek" (Iourovitski, 2024) | arXiv:2408.02871 | ⭐⭐ 自动探针生成 |
| "AWM" (Zeng, 2025) | ICLR 2026 | ❌ 需权重 |

### 9.2 开源项目（可复用）

| 项目 | URL | 用途 |
|------|-----|------|
| ToseaAI/llm-fingerprint-detector | https://github.com/ToseaAI/llm-fingerprint-detector | 统计指纹 (直接集成) |
| mintesnot-teshome/llm-verify | https://github.com/mintesnot-teshome/llm-verify | 探针设计思路参考 |
| lulbitz/llm-con | https://github.com/lulbitz/llm-con | 知识截止探针参考 |
| 论文官方代码 | Zenodo DOI 10.5281/zenodo.21278793 | 采集脚本参考 |

### 9.3 中转站基础设施（了解对手）

| 项目 | URL | Stars |
|------|-----|-------|
| songquanpeng/one-api | https://github.com/songquanpeng/one-api | 36.2k |
| RockChinQ/free-one-api | https://github.com/RockChinQ/free-one-api | 874 |

### 9.4 模型官方文档

| 模型 | URL |
|------|-----|
| GPT-5.6 | https://openai.com/index/gpt-5-6/ |
| GPT-5.6 降价 | https://openai.com/index/advancing-the-price-performance-frontier-with-gpt-5-6/ |
| GPT-5.6 Sol 预览 | https://openai.com/index/previewing-gpt-5-6-sol/ |
| Claude Fable 5 | https://www.anthropic.com/news/claude-fable-5-mythos-5 |
| DeepSeek 定价 | https://api-docs.deepseek.com/quick_start/pricing |

---

> **下一步**: Phase 1 实施 — 搭建 Python CLI 框架 + 知识探针题库。命令: `llm-detect --mode knowledge --endpoint <URL> --model <MODEL>`
