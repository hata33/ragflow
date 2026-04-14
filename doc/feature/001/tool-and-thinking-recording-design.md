# RAGFlow 工具调用记录与思考记录方案设计

## 文档信息

| 项目 | 内容 |
|------|------|
| 编号 | 001 |
| 标题 | 工具调用记录与思考记录详细方案 |
| 状态 | 草案 |
| 创建日期 | 2026-04-12 |
| 作者 | RAGFlow Team |

---

## 一、背景与目标

### 1.1 背景

在 RAGFlow 的 Agent 系统中，当用户与 AI Agent 交互时，系统需要：

1. **工具调用记录**：记录 Agent 调用了哪些工具、传递了什么参数、获得了什么结果
2. **思考记录**：记录 Agent 在决策过程中的思考链路，特别是意图识别、路由决策等关键节点

这些记录对于以下场景至关重要：
- **可观测性**：了解 Agent 的决策过程和执行路径
- **调试**：定位 Agent 行为异常的原因
- **审计**：满足合规性要求，记录 AI 决策过程
- **优化**：分析 Agent 行为模式，优化提示词和工作流
- **用户信任**：向用户展示 AI 的思考过程，增加透明度

### 1.2 设计目标

| 目标 | 优先级 | 说明 |
|------|--------|------|
| **完整性** | P0 | 记录所有关键节点信息，不遗漏 |
| **可追溯性** | P0 | 能够按时间、会话、任务维度追溯 |
| **性能** | P0 | 记录过程不影响 Agent 执行性能 |
| **可扩展性** | P1 | 支持新的组件类型和工具类型 |
| **存储效率** | P1 | 合理的存储策略，控制成本 |
| **查询便捷性** | P1 | 支持灵活的查询和过滤 |
| **可视化友好** | P2 | 数据结构易于前端展示 |

---

## 二、系统架构设计

### 2.1 整体架构

```mermaid
flowchart TB
    subgraph Agent执行层
        CAT[Categorize<br/>意图识别]
        SW[Switch<br/>条件分支]
        AGT[Agent<br/>工具调用]
    end

    subgraph Canvas执行协调器
        CV[Canvas<br/>执行协调器]
        PATH[管理执行路径]
        EVT[触发记录事件]
        COORD[协调各组件]
    end

    subgraph 记录采集层
        NODE_START[节点开始事件<br/>节点信息<br/>思考过程]
        NODE_END[节点完成事件<br/>输入输出<br/>执行耗时]
        TOOL_EVT[工具调用事件<br/>工具名称<br/>参数结果]
    end

    subgraph 存储层
        REDIS[(Redis<br/>热数据<br/>会话日志<br/>实时轨迹)]
        MYSQL[(MySQL<br/>持久化<br/>执行历史<br/>统计数据)]
        S3[(对象存储<br/>可选<br/>大对象归档<br/>长期归档)]
    end

    CAT --> CV
    SW --> CV
    AGT --> CV

    CV --> PATH
    CV --> EVT
    CV --> COORD

    EVT --> NODE_START
    EVT --> NODE_END
    EVT --> TOOL_EVT

    NODE_START --> REDIS
    NODE_END --> REDIS
    TOOL_EVT --> REDIS

    REDIS --> MYSQL
    MYSQL --> S3

    style CAT fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style SW fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style AGT fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style CV fill:#43a047,stroke:#1b5e20,color:#ffffff
    style PATH fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style EVT fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style COORD fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style NODE_START fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style NODE_END fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style TOOL_EVT fill:#ec407a,stroke:#ad1457,color:#ffffff
    style REDIS fill:#ffa726,stroke:#ef6c00,color:#000000
    style MYSQL fill:#ffa726,stroke:#ef6c00,color:#000000
    style S3 fill:#ffa726,stroke:#ef6c00,color:#000000
```

### 2.2 数据流设计

```mermaid
flowchart TB
    START([用户请求]) --> RUN[Canvas.run]

    RUN --> NODE_START[节点开始<br/>node_started 事件]
    NODE_START --> REDIS1[Redis 实时]

    RUN --> EXEC[执行组件]
    EXEC --> THOUGHT[获取 thoughts]
    THOUGHT --> T1[Categorize<br/>分类到 A/B/C]
    THOUGHT --> T2[Switch<br/>条件判断]
    THOUGHT --> T3[Agent<br/>选择工具 X]

    RUN --> TOOL_CALL[工具调用<br/>tool_use_callback]
    TOOL_CALL --> REDIS2[Redis 工具日志]

    RUN --> NODE_END[节点完成<br/>node_finished 事件]
    NODE_END --> REDIS3

    RUN --> END([会话结束])
    END --> ASYNC[异步持久化]
    ASYNC --> MYSQL[(MySQL 历史)]

    style START fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style RUN fill:#43a047,stroke:#1b5e20,color:#ffffff
    style NODE_START fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style REDIS1 fill:#ffa726,stroke:#ef6c00,color:#000000
    style EXEC fill:#1976d2,stroke:#0d47a1,color:#ffffff
    style THOUGHT fill:#1565c0,stroke:#0d47a1,color:#ffffff
    style T1 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style T2 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style T3 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style TOOL_CALL fill:#f57c00,stroke:#e65100,color:#ffffff
    style REDIS2 fill:#ffa726,stroke:#ef6c00,color:#000000
    style NODE_END fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style REDIS3 fill:#ffa726,stroke:#ef6c00,color:#000000
    style END fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style ASYNC fill:#43a047,stroke:#1b5e20,color:#ffffff
    style MYSQL fill:#ffa726,stroke:#ef6c00,color:#000000
```

---

## 三、数据模型设计

### 3.1 核心实体

#### 3.1.1 执行会话 (Execution Session)

```mermaid
classDiagram
    class ExecutionSession {
        +string session_id
        +string conversation_id
        +string task_id
        +string message_id
        +string tenant_id
        +string user_id
        +timestamp created_at
        +enum status
        +object inputs
        +object globals
    }

    note for ExecutionSession "会话唯一标识\n所属对话\n任务 ID\n消息 ID\n租户 ID\n用户 ID\n创建时间\n状态: running/completed/failed/canceled\n输入参数\n全局变量"
```

#### 3.1.2 节点执行记录 (Node Execution)

```mermaid
classDiagram
    class NodeExecution {
        +string execution_id
        +string session_id
        +string node_id
        +string node_name
        +string node_type
        +string parent_node_id
        +integer sequence
        +timestamp started_at
        +timestamp completed_at
        +float elapsed_time
        +object inputs
        +object outputs
        +string error
        +string thoughts
        +enum thoughts_type
        +string[] next_nodes
        +string branch_selected
    }

    note for NodeExecution "执行记录 ID\n所属会话\n节点 ID\n节点名称\n节点类型\n父节点 ID\n执行顺序\n开始时间\n完成时间\n执行耗时\n节点输入\n节点输出\n错误信息\n思考过程\n思考类型\n下一步节点列表\n选择的分支"
```

#### 3.1.3 工具调用记录 (Tool Call)

```mermaid
classDiagram
    class ToolCall {
        +string tool_call_id
        +string session_id
        +string node_execution_id
        +string agent_path
        +string tool_name
        +string tool_type
        +string tool_category
        +timestamp called_at
        +object arguments
        +integer arguments_size
        +object result
        +integer result_size
        +enum result_type
        +string error_message
        +float elapsed_time
        +object context
    }

    note for ToolCall "工具调用 ID\n所属会话\n所属节点执行\nAgent 调用链路径\n工具名称\n工具类型\n工具分类\n调用时间\n调用参数\n参数大小\n调用结果\n结果大小\n结果类型: success/error/timeout\n错误信息\n执行耗时\n额外上下文"
```

### 3.2 数据关系

```mermaid
erDiagram
    CONVERSATION ||--o{ MESSAGE : has
    MESSAGE ||--|| SESSION : creates
    SESSION ||--o{ NODE_EXECUTION : contains
    NODE_EXECUTION ||--o{ TOOL_CALL : includes
    SESSION ||--o{ THINKING_RECORD : has
```

---

## 四、记录策略

### 4.1 分级记录策略

根据重要性和频率，采用分级记录：

```mermaid
flowchart LR
    DATA[数据产生] --> L0[L0 实时<br/>节点开始/完成<br/>工具调用]
    L0 --> REDIS[Redis<br/>10分钟<br/>100%]

    DATA --> L1[L1 热数据<br/>最近N天完整记录]
    L1 --> MYSQL1[MySQL<br/>7-30天<br/>100%]

    DATA --> L2[L2 温数据<br/>压缩关键信息]
    L2 --> MYSQL2[MySQL<br/>90-180天<br/>聚合]

    DATA --> L3[L3 冷数据<br/>归档数据]
    L3 --> S3[对象存储<br/>永久<br/>按需]

    style DATA fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style L0 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style REDIS fill:#ffa726,stroke:#ef6c00,color:#000000
    style L1 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style MYSQL1 fill:#ffa726,stroke:#ef6c00,color:#000000
    style L2 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style MYSQL2 fill:#ffa726,stroke:#ef6c00,color:#000000
    style L3 fill:#f57c00,stroke:#e65100,color:#ffffff
    style S3 fill:#ffa726,stroke:#ef6c00,color:#000000
```

| 级别 | 记录内容 | 存储位置 | 保留时间 | 记录频率 |
|------|----------|----------|----------|----------|
| **L0 - 实时** | 节点开始/完成、工具调用 | Redis | 10分钟 | 100% |
| **L1 - 热数据** | 最近N天的完整记录 | MySQL | 7-30天 | 100% |
| **L2 - 温数据** | 压缩后的关键信息 | MySQL | 90-180天 | 聚合 |
| **L3 - 冷数据** | 归档数据 | 对象存储 | 永久 | 按需 |

### 4.2 记录时机

```mermaid
sequenceDiagram
    participant Comp as 组件
    participant Canvas as Canvas
    participant Redis as Redis
    participant CB as Callback

    Comp->>Canvas: 节点开始
    Canvas->>Redis: node_started 事件<br/>包含 thoughts
    Note over Redis 实时存储

    Comp->>Canvas: 工具调用
    Canvas->>CB: tool_use_callback
    CB->>Redis: 工具调用日志
    Note over Redis 实时存储

    Comp->>Canvas: 节点完成
    Canvas->>Redis: node_finished 事件
    Note over Redis 实时存储
```

### 4.3 内容采样策略

对于大对象（如长文本、图片等），采用采样策略：

```mermaid
flowchart TB
    DATA[大对象数据] --> CHECK{数据大小?}

    CHECK -->|文本| TEXT[文本采样]
    CHECK -->|图片| IMG[图片采样]
    CHECK -->|列表| LIST[列表采样]

    TEXT --> T_MAX[max_length 2000]
    TEXT --> T_STRAT[strategy head_tail]
    TEXT --> T_HEAD[head_ratio 0.3]
    TEXT --> T_TAIL[tail_ratio 0.3]

    IMG --> I_SIZE[max_size 1024]
    IMG --> I_FMT[format thumbnail]
    IMG --> I_QUAL[quality 0.7]

    LIST --> L_COUNT[max_count 10]
    LIST --> L_STRAT[strategy first_last]

    T_MAX --> OUT[输出采样结果]
    T_STRAT --> OUT
    T_HEAD --> OUT
    T_TAIL --> OUT
    I_SIZE --> OUT
    I_FMT --> OUT
    I_QUAL --> OUT
    L_COUNT --> OUT
    L_STRAT --> OUT

    style DATA fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style CHECK fill:#f57c00,stroke:#e65100,color:#ffffff
    style TEXT fill:#43a047,stroke:#1b5e20,color:#ffffff
    style IMG fill:#43a047,stroke:#1b5e20,color:#ffffff
    style LIST fill:#43a047,stroke:#1b5e20,color:#ffffff
    style T_MAX fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style T_STRAT fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style T_HEAD fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style T_TAIL fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style I_SIZE fill:#ec407a,stroke:#ad1457,color:#ffffff
    style I_FMT fill:#ec407a,stroke:#ad1457,color:#ffffff
    style I_QUAL fill:#ec407a,stroke:#ad1457,color:#ffffff
    style L_COUNT fill:#ffa726,stroke:#ef6c00,color:#000000
    style L_STRAT fill:#ffa726,stroke:#ef6c00,color:#000000
    style OUT fill:#43a047,stroke:#1b5e20,color:#ffffff
```

---

## 五、存储方案

### 5.1 Redis 存储（实时数据）

```mermaid
flowchart LR
    KEY[Key 设计] --> K1[session<br/>{task_id}-session]
    KEY --> K2[logs<br/>{task_id}-{message_id}-logs]
    KEY --> K3[trace<br/>{task_id}-{message_id}-trace]
    KEY --> K4[thinking<br/>{task_id}-{message_id}-thinking]

    K1 --> T1[Hash<br/>TTL 600s]
    K2 --> T2[List<br/>TTL 600s]
    K3 --> T3[Stream<br/>TTL 600s]
    K4 --> T4[Hash<br/>TTL 600s]

    T1 --> F1[status<br/>started_at<br/>current_node]
    T2 --> F2[event_type<br/>timestamp<br/>data]
    T3 --> F3[node_id<br/>event<br/>timestamp]
    T4 --> F4[thoughts<br/>timestamp]

    style KEY fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style K1 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style K2 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style K3 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style K4 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style T1 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style T2 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style T3 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style T4 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style F1 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style F2 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style F3 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style F4 fill:#ec407a,stroke:#ad1457,color:#ffffff
```

### 5.2 MySQL 存储（持久化）

```mermaid
erDiagram
    AGENT_SESSIONS {
        varchar id PK
        varchar conversation_id
        varchar task_id
        varchar message_id
        varchar tenant_id
        varchar user_id
        enum status
        timestamp started_at
        timestamp completed_at
        float elapsed_time
        json inputs
        json outputs
    }

    AGENT_NODE_EXECUTIONS {
        varchar id PK
        varchar session_id FK
        varchar node_id
        varchar node_name
        varchar node_type
        int sequence
        timestamp started_at
        timestamp completed_at
        float elapsed_time
        json inputs
        json outputs
        text thoughts
        text error
        json next_nodes
    }

    AGENT_TOOL_CALLS {
        varchar id PK
        varchar session_id FK
        varchar node_execution_id FK
        varchar agent_path
        varchar tool_name
        varchar tool_type
        timestamp called_at
        float elapsed_time
        json arguments
        json result
        enum result_type
        text error_message
    }

    AGENT_SESSIONS ||--o{ AGENT_NODE_EXECUTIONS : "contains"
    AGENT_NODE_EXECUTIONS ||--o{ AGENT_TOOL_CALLS : "includes"
```

### 5.3 归档策略

```mermaid
flowchart TB
    TRIGGER{归档触发} --> TIME[每天<br/>daily]
    TRIGGER --> SIZE[超过10GB<br/>size > 10GB]

    TIME --> ARCH[归档处理]
    SIZE --> ARCH

    ARCH --> SESSION[agent_sessions<br/>保留90天<br/>30天后归档]
    ARCH --> NODE[agent_node_executions<br/>保留90天<br/>30天后归档<br/>压缩存储]
    ARCH --> TOOL[agent_tool_calls<br/>保留180天<br/>60天后归档<br/>生成统计]

    SESSION --> S3[(对象存储)]
    NODE --> S3
    TOOL --> S3

    style TRIGGER fill:#f57c00,stroke:#e65100,color:#ffffff
    style TIME fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style SIZE fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style ARCH fill:#43a047,stroke:#1b5e20,color:#ffffff
    style SESSION fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style NODE fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style TOOL fill:#ec407a,stroke:#ad1457,color:#ffffff
    style S3 fill:#ffa726,stroke:#ef6c00,color:#000000
```

---

## 六、查询与展示方案

### 6.1 查询 API 设计

```mermaid
flowchart TB
    API[查询 API] --> GET1[GET /api/sessions/{session_id}<br/>获取会话详情]
    API --> GET2[GET /api/sessions/{session_id}/trace<br/>获取执行轨迹]
    API --> GET3[GET /api/sessions/{session_id}/tools<br/>获取工具调用]
    API --> GET4[GET /api/sessions/{session_id}/thoughts<br/>获取思考记录]

    GET1 --> R1[session_info<br/>nodes<br/>tool_calls<br/>timeline]
    GET2 --> R2[path<br/>nodes<br/>branches]
    GET3 --> R3[tool_calls<br/>summary]
    GET4 --> R4[thoughts<br/>timeline]

    style API fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style GET1 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style GET2 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style GET3 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style GET4 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style R1 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style R2 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style R3 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style R4 fill:#ffa726,stroke:#ef6c00,color:#000000
```

### 6.2 前端展示设计

```mermaid
flowchart TB
    subgraph 执行时间轴
        START([开始]) --> CAT[分类]
        CAT --> AGT[Agent]
        AGT --> GEN[生成]
        GEN --> END([结束])

        START -.->|思考| T1[选择分支]
        CAT -.->|思考| T2[分类到 A类]
        AGT -.->|工具调用| TOOL[调用搜索 API]
        GEN -.->|思考| T3[生成回答]
        END -.->|完成| TIME[耗时 2.3s]
    end

    subgraph 工具调用详情
        TOOL1[🔍 search_knowledge_base]
        TOOL1 --> P1[参数<br/>query: 如何使用 RAGFlow<br/>top_k: 5]
        P1 --> R1[结果<br/>✓ 找到 3 个相关文档<br/>- RAGFlow 快速入门.pdf 0.92<br/>- Agent 开发指南.md 0.87<br/>- 常见问题 FAQ.md 0.75]
        R1 --> E1[耗时 1.2s]

        TOOL2[🔍 search_internet]
        TOOL2 --> P2[参数<br/>query: 最新 AI 发展趋势]
        P2 --> R2[结果<br/>✗ 超时 10s]
    end

    style START fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style CAT fill:#43a047,stroke:#1b5e20,color:#ffffff
    style AGT fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style GEN fill:#ec407a,stroke:#ad1457,color:#ffffff
    style END fill:#43a047,stroke:#1b5e20,color:#ffffff
    style T1 fill:#ffa726,stroke:#ef6c00,color:#000000
    style T2 fill:#ffa726,stroke:#ef6c00,color:#000000
    style TOOL fill:#ffa726,stroke:#ef6c00,color:#000000
    style T3 fill:#ffa726,stroke:#ef6c00,color:#000000
    style TIME fill:#ffa726,stroke:#ef6c00,color:#000000
    style TOOL1 fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style TOOL2 fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style P1 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style P2 fill:#43a047,stroke:#1b5e20,color:#ffffff
    style R1 fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style R2 fill:#ec407a,stroke:#ad1457,color:#ffffff
    style E1 fill:#ffa726,stroke:#ef6c00,color:#000000
```

### 6.3 可视化选项

```mermaid
mindmap
  root((可视化选项))
    时间轴
      执行流程概览
      实现复杂度: 低
    树状图
      分支决策路径
      实现复杂度: 中
    甘特图
      各节点耗时分析
      实现复杂度: 中
    桑基图
      数据流向
      实现复杂度: 高
    思维导图
      思考链路展示
      实现复杂度: 中
```

---

## 七、性能与扩展性

### 7.1 性能优化策略

```mermaid
flowchart TB
    PERF[性能优化] --> ASYNC[异步写入]
    PERF --> COMP[数据压缩]
    PERF --> CACHE[缓存策略]
    PERF --> IDX[索引优化]

    ASYNC --> A1[消息队列缓冲]
    ASYNC --> A2[批量写入数据库]
    ASYNC --> A3[Redis 先写<br/>异步同步 MySQL]

    COMP --> C1[gzip 压缩 JSON]
    COMP --> C2[大文本单独压缩]
    COMP --> C3[归档时压缩]

    CACHE --> CA1[热点数据缓存]
    CACHE --> CA2[统计数据预计算]
    CACHE --> CA3[查询结果缓存]

    IDX --> I1[租户 ID 分区]
    IDX --> I2[时间范围分区]
    IDX --> I3[复合索引优化]

    style PERF fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style ASYNC fill:#43a047,stroke:#1b5e20,color:#ffffff
    style COMP fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style CACHE fill:#ec407a,stroke:#ad1457,color:#ffffff
    style IDX fill:#ffa726,stroke:#ef6c00,color:#000000
    style A1 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style A2 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style A3 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style C1 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style C2 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style C3 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style CA1 fill:#f06292,stroke:#c2185b,color:#ffffff
    style CA2 fill:#f06292,stroke:#c2185b,color:#ffffff
    style CA3 fill:#f06292,stroke:#c2185b,color:#ffffff
    style I1 fill:#ffcc80,stroke:#ef6c00,color:#000000
    style I2 fill:#ffcc80,stroke:#ef6c00,color:#000000
    style I3 fill:#ffcc80,stroke:#ef6c00,color:#000000
```

### 7.2 扩展性设计

```mermaid
mindmap
  root((扩展性设计))
    插件化组件
      Categorize 意图分类
      Switch 条件分支
      Agent Agent 节点
      Retrieval 检索节点
      Generate 生成节点
      ... 可扩展
    工具类型扩展
      internal 内置工具
      mcp MCP 工具
      api API 工具
      custom 自定义工具
    存储扩展
      redis 实时存储
      mysql 关系存储
      elasticsearch 全文检索
      s3 对象存储
```

---

## 八、安全与隐私

### 8.1 数据安全

```mermaid
flowchart TB
    SEC[数据安全] --> MASK[敏感信息脱敏]
    SEC --> ACCESS[访问控制]
    SEC --> RETAIN[数据保留]

    MASK --> M1[API Keys<br/>只显示前后各 4 位]
    MASK --> M2[用户输入<br/>可选脱敏]
    MASK --> M3[工具参数<br/>根据配置脱敏]
    MASK --> M4[错误信息<br/>过滤敏感路径]

    ACCESS --> A1[租户隔离<br/>只能访问本租户数据]
    ACCESS --> A2[用户权限<br/>基于角色的访问控制]
    ACCESS --> A3[审计日志<br/>记录所有查询操作]

    RETAIN --> R1[最长保留期 365 天]
    RETAIN --> R2[用户可删除<br/>支持手动删除]
    RETAIN --> R3[自动清理<br/>超期自动删除]

    style SEC fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style MASK fill:#43a047,stroke:#1b5e20,color:#ffffff
    style ACCESS fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style RETAIN fill:#ec407a,stroke:#ad1457,color:#ffffff
    style M1 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style M2 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style M3 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style M4 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style A1 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style A2 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style A3 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style R1 fill:#f06292,stroke:#c2185b,color:#ffffff
    style R2 fill:#f06292,stroke:#c2185b,color:#ffffff
    style R3 fill:#f06292,stroke:#c2185b,color:#ffffff
```

### 8.2 合规性考虑

```mermaid
flowchart LR
    COMP[合规性] --> GDPR[GDPR 合规]
    COMP --> AUDIT[审计要求]

    GDPR --> G1[用户数据导出<br/>支持导出个人数据]
    GDPR --> G2[数据删除<br/>支持被遗忘权]
    GDPR --> G3[数据可携带<br/>支持数据导出]

    AUDIT --> AU1[不可篡改<br/>关键记录不可修改]
    AUDIT --> AU2[完整性<br/>记录校验和]
    AUDIT --> AU3[可追溯<br/>完整的审计链]

    style COMP fill:#1a73e8,stroke:#0d47a1,color:#ffffff
    style GDPR fill:#43a047,stroke:#1b5e20,color:#ffffff
    style AUDIT fill:#7e57c2,stroke:#4527a0,color:#ffffff
    style G1 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style G2 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style G3 fill:#66bb6a,stroke:#2e7d32,color:#ffffff
    style AU1 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style AU2 fill:#9575cd,stroke:#5e35b1,color:#ffffff
    style AU3 fill:#9575cd,stroke:#5e35b1,color:#ffffff
```

---

## 九、实施路线图

### 9.1 分阶段实施

```mermaid
gantt
    title 工具调用记录与思考记录实施计划
    dateFormat  YYYY-MM-DD
    section Phase 1
    数据模型设计           :p1-1, 2026-04-12, 3d
    Redis 存储实现        :p1-2, after p1-1, 5d
    基础事件采集          :p1-3, after p1-1, 7d

    section Phase 2
    MySQL 持久化          :p2-1, after p1-3, 5d
    查询 API 开发         :p2-2, after p2-1, 7d
    前端时间轴展示        :p2-3, after p2-2, 5d

    section Phase 3
    工具调用详情展示      :p3-1, after p2-3, 5d
    思考记录可视化        :p3-2, after p3-1, 5d
    性能优化             :p3-3, after p3-2, 5d

    section Phase 4
    数据归档实现          :p4-1, after p3-3, 5d
    高级查询功能          :p4-2, after p4-1, 7d
    统计分析             :p4-3, after p4-2, 5d
```

### 9.2 里程碑

| 阶段 | 交付物 | 验收标准 |
|------|--------|----------|
| **Phase 1** | 基础记录功能 | 能记录节点执行和工具调用 |
| **Phase 2** | 持久化和查询 | 数据持久化，提供查询 API |
| **Phase 3** | 可视化展示 | 前端展示执行轨迹 |
| **Phase 4** | 高级功能 | 归档、统计、分析 |

---

## 十、附录

### 10.1 术语表

| 术语 | 说明 |
|------|------|
| Session | 一次完整的 Agent 执行会话 |
| Node Execution | 单个组件节点的执行记录 |
| Tool Call | 工具调用的详细记录 |
| Thoughts | Agent 在决策过程中的思考记录 |
| Trace | 执行轨迹，记录节点间的流转 |

### 10.2 参考资料

- RAGFlow 架构文档
- Agent 组件设计文档
- OpenAI Tool Calling 规范
- LangChain 可观测性实践

---

## 变更记录

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| 0.1 | 2026-04-12 | RAGFlow Team | 初始版本 |
