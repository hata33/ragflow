# Agent 检索集成

> **模块路径**: `agent/tools/retrieval.py`
> **核心类**: `RetrievalParam`, `Retrieval`
> **相关文档**: [000-检索系统总览](./000-检索系统总览.md), [001-核心检索链路](./001-核心检索链路.md), [008-配置参数详解](./008-配置参数详解.md)

---

## 1. 概述

Agent 检索组件是 RAGFlow Agent 工作流系统中的核心工具之一，它将检索能力嵌入到 Agent 的工具调用流程中。与对话场景直接调用 `Dealer.retrieval()` 不同，Agent 检索组件封装了更丰富的预处理和后处理管道，包括动态数据集选择、元数据过滤、跨语言翻译、TOC 增强、父子块解析和知识图谱集成等功能。

### 核心特性

- **双路检索**: 支持知识库检索（`_retrieve_kb`）和记忆检索（`_retrieve_memory`）两种模式
- **动态数据集**: 通过 `@variable` 语法在运行时动态解析数据集 ID
- **元数据过滤**: 三种过滤模式（auto / semi_auto / manual），支持 LLM 驱动的智能过滤
- **跨语言查询**: 自动将查询翻译为多种语言以扩大召回范围
- **后处理管道**: TOC 增强、父子块合并、知识图谱结果的有序串联
- **超时保护**: 通过 `COMPONENT_EXEC_TIMEOUT` 环境变量控制执行超时（默认 12 秒）

### 类继承关系

```
ComponentParamBase (agent/component/base.py)
  └── ToolParamBase (agent/tools/base.py)
        └── RetrievalParam (agent/tools/retrieval.py)    -- 参数定义

ComponentBase (agent/component/base.py)
  └── ToolBase (agent/tools/base.py)
        └── Retrieval (agent/tools/retrieval.py)         -- 检索逻辑
```

---

## 2. RetrievalParam 参数配置

**文件位置**: `agent/tools/retrieval.py:37-84`

### 参数一览

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `similarity_threshold` | float | `0.2` | 相似度过滤阈值，低于此值的检索结果被丢弃 |
| `keywords_similarity_weight` | float | `0.5` | 关键词相似度权重（注意：内部传给检索引擎时取反为向量权重，详见[第 7 节](#7-权重映射)） |
| `top_n` | int | `8` | 最终返回的检索结果数量 |
| `top_k` | int | `1024` | 向量检索阶段返回的候选数量 |
| `dataset_ids` | list[str] | `[]` | 数据集 ID 列表，支持 `@variable` 动态引用 |
| `kb_ids` | list[str] | `[]` | 已废弃，为向后兼容保留 |
| `memory_ids` | list[str] | `[]` | 记忆库 ID 列表，用于记忆检索模式 |
| `kb_vars` | list | `[]` | 知识库变量列表 |
| `rerank_id` | str | `""` | 重排序模型 ID，为空则不使用外部重排序 |
| `empty_response` | str | `""` | 检索无结果时的默认输出内容 |
| `use_kg` | bool | `False` | 是否启用知识图谱检索 |
| `cross_languages` | list | `[]` | 跨语言翻译的目标语言列表 |
| `toc_enhance` | bool | `False` | 是否启用 TOC 目录增强检索 |
| `meta_data_filter` | dict | `{}` | 元数据过滤配置，包含过滤模式和条件 |

### ToolMeta 定义

`RetrievalParam` 通过 `meta` 属性定义了工具的元信息，供 LLM Function Calling 使用：

```python
self.meta: ToolMeta = {
    "name": "search_my_dateset",
    "description": "This tool can be utilized for relevant content searching in the datasets.",
    "parameters": {
        "query": {
            "type": "string",
            "description": "The keywords to search the dataset. The keywords should be the most important words/terms(includes synonyms) from the original request.",
            "default": "",
            "required": True
        }
    }
}
```

### 参数校验

`check()` 方法在组件初始化时被调用，执行以下校验：

```python
def check(self):
    self.check_decimal_float(self.similarity_threshold, "[Retrieval] Similarity threshold")
    self.check_decimal_float(self.keywords_similarity_weight, "[Retrieval] Keyword similarity weight")
    self.check_positive_number(self.top_n, "[Retrieval] Top N")
```

---

## 3. 检索流程

`_retrieve_kb` 方法是知识库检索的核心，其完整流程如下：

**文件位置**: `agent/tools/retrieval.py:94-264`

### 流程图

```
输入查询文本 (query_text)
       │
       ▼
┌──────────────────────────┐
│ 1. 数据集 ID 解析         │  处理 @variable 动态引用
│    _dataset_ids → kb_ids │  合并静态 ID 和动态解析的 ID
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 2. 嵌入模型验证           │  所有知识库必须使用相同的嵌入模型
│    assert embd_nms == 1  │  否则抛出异常
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 3. 模型初始化             │  初始化嵌入模型和重排序模型
│    embd_mdl, rerank_mdl  │  通过 LLMBundle 包装
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 4. 查询变量替换           │  将 {component_id@output} 替换为实际值
│    get_input_elements_    │
│    from_text + string_    │
│    format                 │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 5. 元数据过滤             │  可选步骤，三种模式
│    apply_meta_data_filter │  → doc_ids 列表
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 6. 跨语言翻译             │  可选步骤
│    cross_languages()      │  将查询翻译为多种语言
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 7. 核心检索               │  调用 settings.retriever.retrieval()
│    Dealer.retrieval()     │  传入向量权重 = 1 - keywords_similarity_weight
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 8. TOC 增强               │  可选步骤 (toc_enhance=True)
│    retrieval_by_toc()     │  基于目录结构补充检索结果
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 9. 父子块解析             │  始终执行
│    retrieval_by_children()│  补充子块的父块内容
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 10. 知识图谱检索          │  可选步骤 (use_kg=True)
│    kg_retriever.retrieval()│ 结果插入到 chunks 列表头部
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 11. 输出格式化            │  清理内部字段，格式化为文本输出
│    kb_prompt() + JSON     │  设置 formalized_content 和 json 输出
└──────────────────────────┘
```

### 查询预处理

在核心检索之前，查询文本经历以下预处理：

```python
# 步骤 1: 变量替换 - 将 {component_id@output} 模式的引用替换为上游组件的输出值
vars = self.get_input_elements_from_text(query_text)
vars = {k: o["value"] for k, o in vars.items()}
query = self.string_format(query_text, vars)

# 步骤 2: 去除 "user:" 前缀（如果查询来自聊天上下文）
query = re.sub(r"^user[:：\s]*", "", query, flags=re.IGNORECASE)
```

---

## 4. 数据集解析

### 静态与动态数据集 ID

`_dataset_ids` 属性提供了向后兼容的数据集 ID 获取方式：

```python
@property
def _dataset_ids(self):
    """Get dataset IDs with backward compatibility for kb_ids."""
    return self._param.dataset_ids or getattr(self._param, "kb_ids", None) or []
```

优先使用 `dataset_ids`，如果为空则回退到已废弃的 `kb_ids`。

### @variable 动态引用语法

数据集 ID 列表中可以包含 `@variable` 格式的动态引用，系统在运行时将其解析为实际的数据集名称或 ID：

```python
for id in self._dataset_ids:
    if id.find("@") < 0:
        # 静态 ID，直接使用
        kb_ids.append(id)
        continue
    # 动态引用：通过 Canvas 获取变量值
    kb_nm = self._canvas.get_variable_value(id)
    # 变量值可以是列表（多个数据集）或单个值
    kb_nm_list = kb_nm if isinstance(kb_nm, list) else [kb_nm]
    for nm_or_id in kb_nm_list:
        # 先尝试按名称查找，再尝试按 ID 查找
        e, kb = KnowledgebaseService.get_by_name(nm_or_id, self._canvas._tenant_id)
        if not e:
            e, kb = KnowledgebaseService.get_by_id(nm_or_id)
            if not e:
                raise Exception(f"Dataset({nm_or_id}) does not exist.")
        kb_ids.append(kb.id)
```

**解析逻辑**：
1. 检查 ID 是否包含 `@` 字符
2. 如果包含，通过 `canvas.get_variable_value()` 获取变量值
3. 变量值可以是字符串（单个数据集）或列表（多个数据集）
4. 对每个值，先按名称在当前租户下查找知识库，若未找到则按 ID 查找
5. 去重后得到最终的 `filtered_kb_ids`

**示例**：

```
dataset_ids = ["kb_abc123", "@user_selected_kb"]

运行时：
- "kb_abc123" → 直接作为知识库 ID
- "@user_selected_kb" → 从 Canvas 变量中解析为 "产品手册" → 查找名称为 "产品手册" 的知识库 → 获取其 ID
```

### 嵌入模型一致性检查

所有选定的知识库必须使用相同的嵌入模型，否则检索结果无法合并排序：

```python
embd_nms = list(set([kb.embd_id for kb in kbs]))
assert len(embd_nms) == 1, "Knowledge bases use different embedding models."
```

---

## 5. 元数据过滤

元数据过滤允许在检索之前根据文档元数据缩小搜索范围。这是一个可选步骤，仅在 `meta_data_filter` 非空时触发。

**文件位置**: `agent/tools/retrieval.py:136-182`, `common/metadata_utils.py:162-`

### 三种过滤模式

#### 5.1 auto 模式（LLM 驱动）

系统使用 LLM 自动分析用户查询和可用元数据字段，生成过滤条件：

```python
if self._param.meta_data_filter.get("method") in ["auto", "semi_auto"]:
    chat_mdl = LLMBundle(tenant_id, chat_model_config)

doc_ids = await apply_meta_data_filter(
    self._param.meta_data_filter, metas, query, chat_mdl, doc_ids,
    _resolve_manual_filter if method == "manual" else None,
)
```

在 `auto` 模式下，`apply_meta_data_filter` 调用 `gen_meta_filter()` 让 LLM 根据查询和元数据描述自动生成过滤条件。如果过滤后没有匹配的文档，返回 `None`，后续检索将被跳过。

#### 5.2 semi_auto 模式

用户预先选择若干元数据字段，LLM 仅基于这些选定的字段生成过滤条件：

```python
# apply_meta_data_filter 内部逻辑
elif method == "semi_auto":
    selected_keys = []
    constraints = {}
    for item in meta_data_filter.get("semi_auto", []):
        if isinstance(item, str):
            selected_keys.append(item)
        elif isinstance(item, dict):
            # 处理约束条件
            ...
```

#### 5.3 manual 模式

用户直接提供过滤条件，支持变量插值。`_resolve_manual_filter` 函数处理条件值中的变量引用：

```python
def _resolve_manual_filter(flt: dict) -> dict:
    pat = re.compile(self.variable_ref_patt)
    s = flt.get("value", "")
    # 遍历匹配 {component_id@output} 或 {sys.xxx} 格式的变量
    for m in pat.finditer(s):
        key = m.group(1)
        v = self._canvas.get_variable_value(key)
        # 处理不同类型的变量值：
        # - None → 空字符串
        # - partial (生成器函数) → 执行并拼接结果
        # - str → 直接使用
        # - 其他 → JSON 序列化
        ...
```

**变量插值示例**：

```python
# 手动过滤条件
meta_data_filter = {
    "method": "manual",
    "manual": [
        {"name": "department", "comparison_operator": "=", "value": "{user_input@department}"}
    ]
}
# 运行时 {user_input@department} 被替换为上游组件的实际输出值
```

### 元数据获取

过滤前需要获取知识库中所有文档的元数据信息：

```python
metas = DocMetadataService.get_flatted_meta_by_kbs(kb_ids)
```

返回的数据结构为嵌套字典，键为元数据字段名，值为 `{字段值 → doc_ids}` 的映射，支持 `meta_filter()` 函数进行高效过滤。

---

## 6. 跨语言支持

当 `cross_languages` 参数非空时，系统会将查询文本翻译为指定的目标语言，以扩大检索的召回范围。

**文件位置**: `agent/tools/retrieval.py:184-185`

```python
if self._param.cross_languages:
    query = await cross_languages(kbs[0].tenant_id, None, query, self._param.cross_languages)
```

### 工作原理

`cross_languages()` 函数（`rag/prompts/generator.py:325`）使用 LLM 将查询翻译为多种语言，并将翻译结果与原始查询合并。例如：

```
原始查询: "人工智能的应用"
cross_languages: ["en", "ja"]

翻译后查询: "人工智能的应用 applications of artificial intelligence 人工知能の応用"
```

这使得即使知识库中的文档使用不同语言编写，也能被正确召回。

---

## 7. 权重映射

### 用户视角 vs 内部视角

这是一个容易混淆的关键设计点。用户配置的 `keywords_similarity_weight` 表示**关键词（BM25）的权重**，但底层检索引擎 `Dealer.retrieval()` 接收的参数是**向量相似度的权重**（`vector_similarity_weight`）。

### 映射关系

```
keywords_similarity_weight (用户配置)
    ↓
vector_similarity_weight = 1 - keywords_similarity_weight (传给检索引擎)
```

**文件位置**: `agent/tools/retrieval.py:197`

```python
kbinfos = await settings.retriever.retrieval(
    query,
    embd_mdl,
    [kb.tenant_id for kb in kbs],
    filtered_kb_ids,
    1,                              # page
    self._param.top_n,              # page_size
    self._param.similarity_threshold,
    1 - self._param.keywords_similarity_weight,  # ← 注意取反！这是向量权重
    doc_ids=doc_ids,
    aggs=False,
    rerank_mdl=rerank_mdl,
    rank_feature=label_question(query, kbs),
)
```

### 权重对照表

| `keywords_similarity_weight` | 向量权重 (`1 - kw`) | 含义 |
|:---:|:---:|:---|
| `0.0` | `1.0` | 纯向量语义检索 |
| `0.3` | `0.7` | 向量检索为主，关键词为辅 |
| `0.5`（默认） | `0.5` | 向量和关键词各占一半 |
| `0.7` | `0.3` | 关键词检索为主，向量为辅 |
| `1.0` | `0.0` | 纯 BM25 关键词检索 |

### 与对话检索的区别

在对话场景中（`dialog_app.py` / `dialog_service.py`），`keywords_similarity_weight` 参数的含义与此处**一致**——都是用户侧的关键词权重，内部传递给 `Dealer.retrieval()` 时都需要取反。这种统一的设计确保了用户在不同场景下的配置语义一致。

---

## 8. 后处理管道

核心检索完成后，结果会依次经过多个后处理步骤。

### 8.1 TOC 增强

**条件**: `toc_enhance = True`

```python
if self._param.toc_enhance:
    chat_mdl = LLMBundle(tenant_id, chat_model_config)
    cks = await settings.retriever.retrieval_by_toc(
        query, kbinfos["chunks"], [kb.tenant_id for kb in kbs],
        chat_mdl, self._param.top_n
    )
    if cks:
        kbinfos["chunks"] = cks
```

TOC（Table of Contents）增强基于文档的目录结构进行二次检索。它利用已检索到的块所在的章节上下文，补充同一章节中的其他相关内容，从而提供更完整的语义覆盖。

### 8.2 父子块解析

**条件**: 始终执行

```python
kbinfos["chunks"] = settings.retriever.retrieval_by_children(
    kbinfos["chunks"],
    [kb.tenant_id for kb in kbs]
)
```

父子块解析**始终执行**（无需配置开关）。当检索到的块是某个父块的子块时，系统会将子块内容替换为其父块的完整内容，以提供更丰富的上下文信息。

### 8.3 知识图谱检索

**条件**: `use_kg = True`

**文件位置**: `agent/tools/retrieval.py:233-242`

```python
if self._param.use_kg and kbs:
    chat_model_config = get_tenant_default_model_by_type(kbs[0].tenant_id, LLMType.CHAT)
    ck = await settings.kg_retriever.retrieval(
        query, [kb.tenant_id for kb in kbs], filtered_kb_ids, embd_mdl,
        LLMBundle(kbs[0].tenant_id, chat_model_config)
    )
    if ck["content_with_weight"]:
        ck["content"] = ck["content_with_weight"]
        del ck["content_with_weight"]
        kbinfos["chunks"].insert(0, ck)  # 插入到列表头部，优先展示
```

知识图谱检索通过 `settings.kg_retriever` 执行，结果以 `content_with_weight` 字段返回（包含实体和关系信息的加权内容），随后被转换为标准 `content` 字段并插入到结果列表的**头部**（`insert(0, ck)`），确保知识图谱结果在后续处理中获得最高优先级。

### 8.4 后处理顺序

```
核心检索结果
    │
    ├── [条件] TOC 增强 → 可能替换整个 chunks 列表
    │
    ├── [始终] 父子块解析 → 子块扩展为父块
    │
    └── [条件] 知识图谱 → 结果插入到列表头部
```

**重要**: 每个异步步骤之后都会检查取消状态：

```python
if self.check_if_canceled("Retrieval processing"):
    return
```

这确保了当用户取消操作或超时时，检索流程能够及时终止。

### 8.5 输出格式化

检索结果经过清理和格式化后输出：

```python
# 清理内部字段
for ck in kbinfos["chunks"]:
    if "vector" in ck:
        del ck["vector"]
    if "content_ltks" in ck:
        del ck["content_ltks"]

# 空结果处理
if not kbinfos["chunks"]:
    self.set_output("formalized_content", self._param.empty_response)
    return

# 格式化输出
self._canvas.add_reference(kbinfos["chunks"], kbinfos["doc_aggs"])  # 添加引用信息
form_cnt = "\n".join(kb_prompt(kbinfos, 200000, True))              # 文本格式（最大 200K token）
self.set_output("formalized_content", form_cnt)                     # 文本输出
self.set_output("json", json_output)                                # JSON 输出
```

输出同时提供两种格式：
- **formalized_content**: 通过 `kb_prompt()` 格式化的文本内容，供下游 LLM 节点使用
- **json**: 原始块数据列表，供程序化处理使用

---

## 9. 记忆检索

`_retrieve_memory` 是与知识库检索平行的另一条检索路径，用于从用户的历史对话记忆中检索相关内容。

**文件位置**: `agent/tools/retrieval.py:266-300`

### 流程

```
输入查询文本 (query_text)
       │
       ▼
┌──────────────────────────┐
│ 1. 获取记忆列表           │  MemoryService.get_by_ids(memory_ids)
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 2. 验证嵌入模型一致性     │  所有记忆库必须使用相同的嵌入模型
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 3. 查询变量替换           │  同知识库检索的变量替换逻辑
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 4. 构建过滤条件           │  memory_id + 可选的 user_id
│    filter_dict            │  user_id 支持 {variable} 动态引用
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 5. 执行记忆检索           │  memory_message_service.query_message()
│    传入 similarity等参数  │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 6. 格式化输出             │  memory_prompt() 格式化，最大 200K token
└──────────────────────────┘
```

### user_id 动态过滤

记忆检索支持基于用户 ID 的过滤，且 user_id 本身也支持变量引用：

```python
filter_dict: dict = {"memory_id": memory_ids}
if user_id:
    if re.match(r"^{.*}$", user_id):
        # 变量引用，如 "{sys.user_id}" → 从 Canvas 获取实际值
        user_id = self._canvas.get_variable_value(user_id)
    filter_dict["user_id"] = user_id
```

### 与知识库检索的差异

| 特性 | 知识库检索 | 记忆检索 |
|------|-----------|---------|
| 检索目标 | 文档块（chunks） | 对话消息（messages） |
| 检索引擎 | `settings.retriever` | `memory_message_service` |
| 后处理 | TOC、父子块、知识图谱 | 无 |
| 重排序 | 支持外部 rerank 模型 | 不支持 |
| 元数据过滤 | 支持 | 不支持 |
| 跨语言 | 支持 | 不支持 |
| 引用追踪 | 通过 `add_reference()` | 不支持 |
| 输出格式 | `kb_prompt()` | `memory_prompt()` |

---

## 10. 路由逻辑

`_invoke_async` 方法是检索组件的入口，负责将请求路由到对应的检索路径。

**文件位置**: `agent/tools/retrieval.py:302-320`

### 路由决策树

```
_invoke_async(query)
       │
       ▼
  query 为空？ ──是──→ 输出 empty_response，返回
       │
      否
       │
       ▼
  retrieval_from == "dataset"？ ──是──→ _retrieve_kb()
       │
      否
       │
       ▼
  retrieval_from == "memory"？ ──是──→ _retrieve_memory()
       │
      否
       │
       ▼
  _dataset_ids 非空？ ──是──→ _retrieve_kb()
       │
      否
       │
       ▼
  memory_ids 非空？ ──是──→ _retrieve_memory()
       │
      否
       │
       ▼
  输出 empty_response，返回
```

### 路由代码

```python
@timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
async def _invoke_async(self, **kwargs):
    if self.check_if_canceled("Retrieval processing"):
        return
    if not kwargs.get("query"):
        self.set_output("formalized_content", self._param.empty_response)
        return

    # 优先级 1: 显式指定 retrieval_from
    if hasattr(self._param, "retrieval_from") and self._param.retrieval_from == "dataset":
        return await self._retrieve_kb(kwargs["query"])
    elif hasattr(self._param, "retrieval_from") and self._param.retrieval_from == "memory":
        return await self._retrieve_memory(kwargs["query"])
    # 优先级 2: 根据配置的 ID 自动推断
    elif self._dataset_ids:
        return await self._retrieve_kb(kwargs["query"])
    elif hasattr(self._param, "memory_ids") and self._param.memory_ids:
        return await self._retrieve_memory(kwargs["query"])
    # 兜底: 无可用的检索源
    else:
        self.set_output("formalized_content", self._param.empty_response)
        return
```

### 超时控制

检索操作通过 `@timeout` 装饰器实现超时保护：

```python
@timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
```

- 默认超时时间：**12 秒**
- 可通过环境变量 `COMPONENT_EXEC_TIMEOUT` 自定义
- 同时应用于 `_invoke_async` 和同步包装器 `_invoke`

### 同步包装器

`_invoke` 方法提供同步调用入口，内部通过 `asyncio.run()` 执行异步逻辑：

```python
@timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
def _invoke(self, **kwargs):
    return asyncio.run(self._invoke_async(**kwargs))
```

---

## 总结

Agent 检索组件 (`agent/tools/retrieval.py`) 作为 Agent 工作流中的检索工具，在底层检索引擎 (`Dealer.retrieval()`) 的基础上构建了完整的检索管道：

1. **入口路由**: 根据 `retrieval_from` 参数或配置的 ID 列表自动选择知识库检索或记忆检索
2. **知识库检索管道**: 数据集解析 → 嵌入模型验证 → 查询预处理 → 元数据过滤 → 跨语言翻译 → 核心检索 → TOC 增强 → 父子块解析 → 知识图谱 → 输出格式化
3. **关键设计**: `keywords_similarity_weight` 到 `vector_similarity_weight` 的取反映射确保了用户配置语义的一致性
4. **动态能力**: 通过 `@variable` 语法支持运行时动态选择数据集和过滤条件
5. **超时保护**: 默认 12 秒超时，防止长时间运行的检索阻塞整个工作流
