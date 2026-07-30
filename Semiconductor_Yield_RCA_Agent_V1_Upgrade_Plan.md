# Semiconductor Yield RCA Multi-Agent System V1 Upgrade Plan

## 目标

将当前项目从：

> Multi-Agent Workflow + Simple Knowledge Retrieval

升级为：

> Evidence-driven Industrial RCA Agent System with Advanced RAG Architecture

核心目标：

1. 强化 Agent 工程设计
2. 建立 Evidence 驱动 RCA 流程
3. 升级 Knowledge / RAG 架构
4. 保留未来 Production RAG 演进空间

---

# 一、整体架构目标

## 升级后流程

```text
User
 |
Planner
 |
Supervisor
 |
------------------------------------------------
MES Agent
FDC Agent
Defect/WAT Agent
Knowledge Agent
 |
Evidence Layer
 |
RCA Reasoning Agent
 |
Hypothesis Ranking
 |
Root Cause
 |
Human Approval
 |
Knowledge Update
```

---

# 二、Evidence Architecture（核心）

## 目标

所有 Agent 不直接输出 Root Cause。

统一输出 Evidence。

## Evidence模型

```python
class Evidence:

    evidence_id

    evidence_type

    source_agent

    source_tool

    observation

    entity

    timestamp

    confidence

    metadata
```

## Evidence类型

### MES Evidence

包含：

- Process genealogy
- Equipment exposure
- Recipe
- Hold / Alarm

示例：

```
PROCESS_EXPOSURE

LOT经过:
CMP_CU03

Recipe:
CU_POLISH_V2
```

---

### FDC Evidence

包含：

- Parameter deviation
- SPC
- OOC
- Trend

示例：

```
PARAMETER_DEVIATION

Slurry flow:

120 -> 95

OOC detected
```

---

### Defect/WAT Evidence

包含：

- Defect
- Metrology
- Electrical failure

---

### Knowledge Evidence

包含：

- Historical RCA case
- SOP
- Engineering Note

---

# 三、Agent职责重新定义

## MES Agent

负责：

- Lot genealogy
- Process exposure
- Equipment
- Recipe
- Hold history

不负责 Root Cause。

---

## FDC Agent

负责：

- Parameter analysis
- SPC/OOC
- Trend deviation

不负责 Root Cause。

---

## Defect/WAT Agent

负责：

- Defect
- Metrology
- Electrical failure

不负责 Root Cause。

---

## Knowledge Agent

负责：

- Historical knowledge retrieval
- Engineering knowledge retrieval

不负责 Root Cause decision。

---

## RCA Reasoning Agent

负责：

Evidence → Hypothesis → Validation → Root Cause

---

# 四、Tool Layer设计

原则：

Agent负责推理。

Tool负责确定性能力。

架构：

```
Agent

↓

Tool

↓

Evidence Builder

↓

Evidence
```

例如：

```
analyze_parameter_shift()

↓

EV_FDC_PARAMETER_DEVIATION
```

---

# 五、Knowledge / RAG架构升级

RAG需要保留高级演进空间。

目标：

Production-oriented Industrial RAG。

---

# Knowledge Asset设计

Knowledge包含：

```
Knowledge Asset

 |
 |-- RCA Case
 |
 |-- SOP
 |
 |-- Engineering Note
 |
 |-- Literature
```

---

## Knowledge Asset Schema

```
knowledge_asset

id

type

title

content

module

equipment

recipe

source

version

approval_status

created_by

approved_by
```

---

# 六、RAG升级路线

## Phase 1

保留 Retriever 抽象：

```
Retriever

 |
 |-- KeywordRetriever
 |
 |-- VectorRetriever
 |
 |-- HybridRetriever
```

---

## Phase 2

Chunk + Embedding

流程：

```
Document

↓

Chunk

↓

Embedding

↓

Vector Index
```

支持：

- pgvector
- embedding model
- semantic search

---

## Phase 3

Production Hybrid RAG

```
Query

↓

Metadata Filter

↓

-------------------

BM25       Vector Search

-------------------

↓

RRF Fusion

↓

Reranker

↓

Evidence Builder

↓

Knowledge Evidence
```

---

# 七、两阶段RAG设计

## Stage 1：Evidence-driven Retrieval

输入：

当前 Evidence。

例如：

```
CMP exposure

+

slurry flow decrease

+

Cu residue
```

输出：

Candidate Knowledge。

---

## Stage 2：Hypothesis Validation Retrieval

RCA生成：

```
H1:
Slurry degradation
```

Knowledge Agent验证：

```
Does historical knowledge support H1?
```

输出：

- support
- contradict
- confidence

---

# 八、RCA Reasoning Agent升级

不要：

```
Evidence Score

↓

Root Cause
```

升级：

```
Evidence

↓

Hypothesis Generation

↓

Evidence Validation

↓

Hypothesis Ranking

↓

Decision
```

Hypothesis结构：

```json
{
"id":"H1",

"cause":
"Slurry degradation",

"supporting_evidence":[
"EV_FDC_001",
"EV_KNOWLEDGE_001"
],

"contradicting_evidence":[],

"confidence":0.91
}
```

---

# 九、Memory设计（V1范围）

## 实现

### Short-term Memory

RCAState。

---

### Long-term Knowledge Memory

包含：

- RCA Case
- SOP
- Engineering Note

---

### Knowledge Governance

流程：

```
RCA

↓

Memory Candidate

↓

Engineer Approval

↓

Publish Knowledge

↓

Update Retrieval Index
```

---

# 十、暂不实现

保留设计：

- Evidence Graph
- Adaptive Supervisor
- Memory Router
- Organization Memory
- Neo4j Knowledge Graph

---

# 十一、Evaluation

## RAG Evaluation

- Recall@K
- MRR

## RCA Evaluation

- Root Cause Accuracy
- Evidence Citation Accuracy

## Agent Evaluation

- Tool success rate
- Task completion

---

# 十二、推荐开发顺序

## Step 1

Evidence模型

↓

所有Agent输出Evidence


## Step 2

Tool层调整

↓

Tool生成Evidence


## Step 3

Knowledge Asset设计

↓

支持RCA/SOP/Engineering Note


## Step 4

Retriever抽象

↓

为Embedding/Hybrid预留接口


## Step 5

两阶段RAG


## Step 6

RCA Hypothesis Engine


## Step 7

Evaluation


---

# 给Codex的说明

不要简单增加功能。

目标不是堆技术。

目标：

将项目从：

```
Multi-Agent Workflow
```

升级为：

```
Evidence-driven Industrial RCA Agent
```

核心：

- Evidence 是核心数据结构
- RAG 是 Knowledge Evidence 生成模块
- RCA Agent 负责推理
- Human Approval 保证知识质量

采用演进式重构，不推倒重写。
