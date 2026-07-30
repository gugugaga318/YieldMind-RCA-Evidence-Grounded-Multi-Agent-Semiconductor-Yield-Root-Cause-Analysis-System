# Semiconductor Yield RCA Multi-Agent System Design Doc

> 目标读者：项目开发者、面试官、后续 Codex 实现任务  
> 项目定位：面向半导体良率异常分析的工业 Multi-Agent 应用  
> 当前版本：MVP-first architecture with realistic Fab data model

## 1. 项目背景

半导体制造中的良率异常（Yield Excursion）通常不是单一数据源可以解释的问题。一个真实的 Yield Engineer 往往需要同时查看：

- MES：Lot / Wafer 制造历史、Process Route、Equipment、Chamber、Recipe、Hold、Alarm
- FDC：设备参数 feature、OOC、趋势漂移、Chamber级异常
- KLA / Defect：缺陷类型、缺陷数量、缺陷分布模式
- Metrology：CD、Overlay、Film Thickness 等量测结果
- WAT：电性测试结果、fail mode、parameter shift
- Engineering Knowledge：历史 RCA、SOP、工程师笔记、设备维修经验

本项目不是单纯的 RAG 问答系统，而是希望模拟一个资深 Yield Engineer 的分析过程：从良率下降开始，逐步定位 affected lots、共同工艺路径、设备/Chamber异常、Recipe变化、FDC漂移、Defect/WAT证据和历史案例，最终生成可追溯的 RCA 报告。

## 2. 项目目标

### 2.1 简历项目目标

项目用于展示 AI Agent 在工业场景中的应用能力，重点突出：

- Planner-based Multi-Agent workflow
- Tool calling and structured reasoning
- Short-term / long-term / equipment memory
- Evidence-based RCA，而不是 LLM 猜测
- 半导体 Fab 数据结构理解
- 面向真实工程流程的系统设计能力

### 2.2 MVP目标

MVP阶段先跑通完整闭环：

```text
Offline Synthetic Fab Dataset
        |
        v
PostgreSQL Analysis DB
        |
        v
Tool Layer
        |
        v
Planner / Supervisor / Specialist Agents
        |
        v
RCA Reasoning
        |
        v
Engineering Report
```

MVP 不追求：

- 完整真实 Fab 规模
- Raw FDC stream
- 视觉模型
- 实时数据接入
- 工业级性能压测
- 批量 Top-1 / Top-3 效果指标

MVP 优先验证：

- 数据可导入
- Tool 可查证据
- Agent 可协作
- RCA 报告可追溯
- golden case 端到端跑通

## 3. 关键设计原则

### 3.1 数据生成器是离线 seed 工具

Synthetic Fab 数据不应该在 FastAPI 后台运行时生成。

正确方式：

```text
scripts/generate_synthetic_fab_data.py
        |
        v
data/seeds/*.csv / *.json
        |
        v
scripts/seed_database.py
        |
        v
PostgreSQL
        |
        v
Agent runtime queries existing data
```

错误方式：

```text
User request
        |
        v
Backend dynamically generates Fab data
        |
        v
Agent analyzes freshly generated data
```

原因：

- Fab数据应是分析对象，不是运行时临时构造物。
- Agent需要面对稳定、可复现、可测试的数据世界。
- Ground Truth需要与seed数据绑定，便于测试。
- 后续接真实Fab系统时，数据生成器可以自然替换为ETL/数据导入流程。

### 3.2 Agent不直接访问数据库

Agent只能调用 Tool Layer。

不推荐：

```python
query_table("process_history")
```

推荐：

```python
analyze_lot_genealogy(failed_lots)
```

Tool代表工程能力，而不是裸数据库访问。

### 3.3 先有最小可用数据，再做Agent

合理顺序：

```text
Domain Model
        |
        v
PostgreSQL Schema
        |
        v
Golden Dataset + Seed
        |
        v
Tool Layer
        |
        v
Specialist Agents
        |
        v
Planner / Supervisor / RCA
```

没有数据，Agent只能空转。第一版数据不需要大，但必须闭环完整。

## 4. Fab Information Model

系统采用简化但真实感足够的 Fab Information Model。

```text
Technology
    |
    v
Product
    |
    v
Route
    |
    v
Operation
    |
    v
Equipment Capability
    |
    v
Equipment
    |
    v
Chamber / Station / Component
    |
    v
Sensor / FDC Feature
```

### 4.1 Technology Node 选择

讨论中考虑过 40nm 和 90nm。

最终建议：

- 文档中可设定为 `40nm Logic CMOS`，因为更适合展示复杂工艺和工业AI应用。
- MVP不需要真实模拟数百个step，可以只生成critical operations。
- 如果只做几十个step却声称完整40nm flow，会显得不真实；因此应表述为：

```text
The synthetic route references a 40nm logic CMOS manufacturing flow, while MVP data generation focuses on selected critical operations for RCA demonstration.
```

### 4.2 Process Flow 粒度

不建议只写：

```text
PHOTO -> ETCH -> CMP -> WAT
```

这太像教学流程，不像MES路线。

建议使用层级化 route：

```text
Wafer Preparation
STI Isolation
Well Formation
Gate Formation
Source / Drain
Silicide
Contact
BEOL Metal / Via
Passivation
WAT
```

MVP重点关注 30-50 个 critical operations，而不是生成完整600步。

示例 critical operations：

```text
STI Lithography
STI Etch
STI CMP
Well Implant
Gate Lithography
Poly Etch
Source/Drain Implant
Contact Etch
W CMP
Cu CMP
Via Etch
Passivation Etch
WAT
```

### 4.3 CMP机台不能通用化

CMP必须按工艺段和材料区分能力，不能把所有CMP抽象成通用 `CMP01`。

应区分：

```text
STI CMP: oxide / isolation
ILD CMP: inter-layer dielectric
IMD CMP: inter-metal dielectric
W CMP: tungsten plug / contact
Cu CMP: copper BEOL
```

示例设备：

```text
CMP_STI_01
CMP_STI_02
CMP_ILD_01
CMP_IMD_01
CMP_W_01
CMP_CU_01
CMP_CU_02
CMP_CU_03
```

这样Agent输出才能达到工程粒度：

```text
Root Cause: CMP_CU03_CH02 slurry delivery degradation
```

而不是笼统地说：

```text
Root Cause: CMP tool issue
```

## 5. Equipment Hierarchy

真实Fab问题经常发生在单个 chamber，而不是整台 equipment。

系统应支持：

```text
Factory
  |
Area
  |
Module
  |
Equipment
  |
Chamber / Station
  |
Component
  |
Sensor
```

### 5.1 Etch / CVD / PVD

这些设备通常具有明确 chamber：

```text
ETCH_POLY_02
  |
  +-- ETCH_POLY_02_CH01
  +-- ETCH_POLY_02_CH02
  +-- ETCH_POLY_02_CH03
```

异常示例：

```text
ETCH_POLY_02_CH03 RF power drift
```

### 5.2 CMP

CMP可以抽象为 station / head / platen / chamber-like unit：

```text
CMP_CU03
  |
  +-- CMP_CU03_CH01
  +-- CMP_CU03_CH02
```

MVP中可以统一称为 chamber，便于数据建模和Tool复用。

### 5.3 Lithography

Litho通常不是典型chamber结构，更关注 stage、alignment、focus、laser energy 等。MVP可先做到 equipment-level，未来扩展到 submodule-level。

## 6. MES Data Model

MES是整个分析系统的制造历史骨架。

### 6.1 核心表

```text
lot_master
wafer_master
process_route
process_history
equipment_master
equipment_capability
chamber_master
recipe_master
recipe_parameter
recipe_history
hold_history
equipment_event
pm_history
```

MVP可以先实现其中最核心表：

```text
lot_master
wafer_master
process_route
operation_master
process_history
equipment_master
equipment_capability
chamber_master
recipe_master
recipe_history
hold_history
```

说明：

- `process_route` 和 `operation_master` 用于约束产品路线与关键operation，避免数据生成器随意拼接流程。
- `equipment_capability` 是MVP必须保留的表，用于表达不同机台/Chamber支持的工艺段，例如 STI CMP、W CMP、Cu CMP 不可混用。
- `recipe_master` 用于约束 recipe_id、recipe_version 和 module 的关系；`recipe_history` 记录lot实际使用的recipe版本。

### 6.2 process_history

最重要的MES表。

建议字段：

```text
history_id
lot_id
wafer_id
operation_no
operation_name
module
equipment_id
chamber_id
recipe_id
recipe_version
start_time
end_time
operator_id
process_result
```

它回答：

```text
Failed lots经历了什么？
是否共同经过某个operation/equipment/chamber/recipe？
```

### 6.3 Recipe 和 Recipe Version

Recipe是必须保留的。

很多Yield问题不是设备坏，而是：

```text
Recipe version changed
Recipe parameter changed
Recipe qualified chamber changed
```

示例：

```text
CU_CMP_R17 -> CU_CMP_R18
```

Agent应能检查：

- 异常Lot是否集中使用某个recipe version
- recipe change是否发生在yield drop前
- recipe是否只在某些chamber qualified

### 6.4 Hold 和 Hold Comment

Hold信息非常重要，尤其是 `hold_comment`。

它是MES里的非结构化工程经验，常常包含真实工程师线索：

```text
"Scratch defect observed after Cu CMP"
"High leakage after WAT, suspect CMP module"
"Waiting for SEM review"
"Monitor Cu erosion after recipe change"
```

这些内容适合进入：

- MES Agent summary
- Knowledge Agent query
- RCA evidence

### 6.5 Equipment Event / PM

设备维修、PM、alarm也应保留，至少作为后续扩展：

```text
equipment_event
pm_history
```

用于支持 Equipment Memory。

## 7. FDC Data Model

### 7.1 Raw Data 与 Feature Summary 的边界

MVP不处理raw sensor stream。

真实流程：

```text
Raw Sensor Trace
        |
        v
Feature Extraction
        |
        v
FDC Feature Summary
        |
        v
Agent / Tool Analysis
```

MVP直接生成并导入 feature summary。

### 7.2 Feature Extraction 不只是 Mean Shift

讨论中明确：FDC feature不应只包含mean。

可设计特征类型：

```text
Statistical:
  mean, std, min, max, p5, p50, p95

Trend:
  drift, slope, moving average shift

Shape:
  peak, time_to_peak, endpoint_time, auc

OOC:
  ooc_flag, ooc_rule, severity
```

MVP只需实现：

```text
mean_value
baseline_value
delta_from_baseline
std_value
trend_slope
ooc_flag
severity
```

### 7.3 FDC表

MVP表：

```text
fdc_feature
ooc_event
```

建议字段：

```text
feature_id
lot_id
wafer_id
operation_no
equipment_id
chamber_id
recipe_id
recipe_version
parameter_name
baseline_value
observed_value
delta_percent
unit
trend_slope
ooc_flag
severity
timestamp
```

### 7.4 Module-specific Parameters

CMP参数：

```text
slurry_flow
down_force
platen_rpm
carrier_rpm
motor_current
endpoint_time
removal_rate
vibration
```

Etch参数：

```text
rf_power
bias_power
chamber_pressure
gas_flow
esc_voltage
impedance
```

Litho参数：

```text
alignment_error
focus_offset
stage_position_error
laser_energy
temperature
humidity
```

Implant参数：

```text
beam_current
dose
energy
scan_speed
dose_uniformity
```

## 8. Defect / Metrology / WAT Model

MVP使用结构化数据，不做图像分析。

### 8.1 Defect Summary

字段：

```text
defect_id
lot_id
wafer_id
inspection_step
defect_type
defect_count
defect_density
pattern_type
location_region
timestamp
```

示例：

```text
defect_type: scratch
pattern_type: edge_dominant
```

### 8.2 Metrology

MVP可先不单独建表，后续加入：

```text
CD
Overlay
Film Thickness
Erosion / Dishing
```

### 8.3 WAT Result

字段：

```text
wat_id
lot_id
wafer_id
test_item
parameter_name
measured_value
spec_low
spec_high
pass_fail
fail_mode
timestamp
```

示例：

```text
fail_mode: leakage
```

## 9. Knowledge and Memory Architecture

系统设计三层Memory。

### 9.1 Short-term Memory

当前RCA任务上下文。

实现：

```text
RCAState
```

内容：

```text
user_query
task_plan
affected_lots
process_findings
fdc_findings
defect_findings
knowledge_results
hypotheses
root_cause
report
warnings
```

### 9.2 Long-term Knowledge Memory

工程知识库。

内容：

```text
Historical RCA Cases
SOP
Engineering Notes
Troubleshooting Guides
```

实现：

```text
MVP: PostgreSQL metadata + keyword / tag retrieval
Phase 2: pgvector or Chroma semantic index
```

MVP阶段先用结构化metadata、module标签、defect类型、equipment/chamber和关键词检索实现 `retrieve_similar_case()`。这样可以先跑通RAG Agent接口，同时避免过早引入embedding服务和向量库部署复杂度。pgvector/Chroma作为后续增强。

### 9.3 Equipment Memory

工业场景特色记忆。

记录：

```text
equipment/chamber history
alarm events
PM events
past RCA cases
recurring FDC drift
```

MVP可以通过PostgreSQL表简化实现，不需要复杂自动学习。

## 10. Multi-Agent Architecture

### 10.1 总体结构

```text
User
  |
  v
Planner Agent
  |
  v
Supervisor Agent
  |
  +-----------------------------+
  |             |               |
  v             v               v
MES Agent     FDC Agent     Defect/WAT Agent
  |             |               |
  +-------------+---------------+
                |
                v
        Knowledge Agent
                |
                v
        RCA Reasoning Agent
                |
                v
        Report Generator
```

### 10.2 Planner Agent

职责：

- 理解用户目标
- 生成TaskPlan
- 选择需要调用哪些Agent

不做：

- 查询数据库
- 调用Tool
- 生成最终报告

### 10.3 Supervisor Agent

职责：

- 执行TaskPlan
- 调度Specialist Agents
- 维护RCAState
- 动态路由
- 处理数据缺失、冲突、重试

### 10.4 Specialist Agents

MES Agent：

- affected lots
- commonality
- equipment/chamber concentration
- recipe change
- hold comment

FDC Agent：

- equipment/chamber health
- parameter shift
- OOC
- time causality

Defect/WAT Agent：

- defect pattern
- WAT fail mode
- physical/electrical consistency

Knowledge Agent：

- similar RCA case retrieval
- SOP / engineering note retrieval

### 10.5 RCA Reasoning Agent

进行证据融合。

考虑因素：

```text
MES commonality strength
FDC abnormality strength
Defect/WAT consistency
Historical case similarity
Temporal causality
Missing data penalty
Conflicting evidence penalty
```

MVP可先使用 evidence-based scoring，而不是完整Bayesian。

## 11. Tool Architecture

### 11.1 Tool原则

Tool代表工程能力。

MES Tools：

```text
find_affected_lots()
analyze_lot_genealogy()
check_recipe_change()
search_hold_comment()
```

FDC Tools：

```text
get_equipment_health()
analyze_parameter_shift()
find_ooc_events()
```

Defect/WAT Tools：

```text
summarize_defect_pattern()
analyze_wat_fail_mode()
correlate_defect_and_wat()
```

Knowledge Tools：

```text
retrieve_similar_case()
search_sop()
```

Analytics Tools：

```text
calculate_yield_trend()
compare_failed_vs_normal_lots()
perform_basic_spc_analysis()
```

## 12. RCAState

建议结构：

```python
class RCAState:
    user_query: str
    task_plan: list
    current_task: str
    completed_tasks: list

    yield_result: dict
    affected_lots: list

    mes_findings: dict
    fdc_findings: dict
    defect_wat_findings: dict
    knowledge_findings: dict

    evidence: list
    hypotheses: list
    root_cause: str | None
    confidence: float | None
    warnings: list
    report: str | None
```

## 13. Synthetic Fab Dataset Strategy

### 13.1 MVP Golden Case

第一版只需一个闭环案例。

```text
Product: 40N_SOC
Date Range: July
Problem: Yield drop
Root Cause: CMP_CU03_CH02 slurry delivery degradation
```

证据链：

```text
MES:
  affected lots concentrated on operation 6400 Cu CMP
  affected lots concentrated on CMP_CU03_CH02

FDC:
  slurry_flow delta = -12%
  endpoint_time delta = +15%
  OOC events increased

Defect:
  scratch defect increased
  edge dominant pattern

WAT:
  leakage fail increased

Knowledge:
  historical case: slurry pump degradation caused scratch and yield loss
```

### 13.2 后续扩展场景

后期再加入：

```text
1. CMP slurry flow decline
2. Recipe version change
3. Single chamber abnormality
4. Scratch defect + WAT fail
5. MES commonality but no FDC abnormality
6. FDC drift but no yield impact
7. Conflicting evidence
8. Missing / delayed / duplicate data
9. High historical case match
10. Inconclusive root cause
```

## 14. Frontend / Backend Architecture

### 14.1 Backend

技术栈：

```text
Python
FastAPI
LangGraph or equivalent graph orchestration
PostgreSQL
pgvector / Chroma
Pandas / Scikit-learn
```

职责：

- API
- Agent workflow execution
- Tool orchestration
- report generation

不负责：

- runtime synthetic data generation

### 14.2 Frontend

技术栈：

```text
React
TypeScript
ECharts
```

React负责展示，不负责SPC计算。

页面：

```text
RCA Job Page
Yield Trend Panel
Agent Workflow Timeline
Evidence Chain Panel
Root Cause Panel
Report Panel
```

## 15. Multimodal Interface

MVP不做视觉功能，但保留接口。

未来 Vision Agent 支持：

```text
wafer map
SEM image
defect image
```

可识别：

```text
edge ring
scratch
center cluster
random defect
reticle pattern
```

第一版 Defect/WAT Agent 只处理结构化数据。

## 16. MVP Scope

MVP必须完成：

```text
Offline golden dataset generation and seed
PostgreSQL schema
Tool Layer
MES Agent
FDC Agent
Defect/WAT Agent
Knowledge Agent
Planner Agent
Supervisor
RCA Reasoning Agent
Report Generator
Pure Python end-to-end workflow
Basic FastAPI wrapper
Basic React dashboard
```

MVP不做：

```text
Raw FDC
Vision Agent
Real-time Fab integration
Full SPC platform
Large-scale simulation
Strict accuracy benchmarking
Industrial security model
Automatic memory update
```

## 17. 后续优化方向

```text
More synthetic cases
Batch RCA evaluation
Top-1 / Top-3 metrics
Confidence calibration
SPC rules
Raw FDC trace simulation
Vision Agent implementation
Equipment health modeling
Human validation before memory update
Docker Compose
Observability
Security and permissions
```

## 18. 项目价值总结

本项目的核心价值不是“生成一些Fab数据”，也不是“用LLM写一份报告”，而是：

```text
一个能在半导体制造数据世界中，
通过Planner、Tool、Memory和Evidence Fusion，
模拟Yield Engineer RCA流程的工业Agent系统。
```

它的面试亮点：

- Agent架构不是聊天机器人，而是任务规划和工具调用系统。
- 数据模型包含真实Fab关键概念：Lot、Wafer、Operation、Equipment、Chamber、Recipe、Hold Comment。
- RCA结论基于证据链，而不是LLM自由发挥。
- Memory设计贴合工业经验复用。
- MVP路径清楚，先跑通，再增强。
