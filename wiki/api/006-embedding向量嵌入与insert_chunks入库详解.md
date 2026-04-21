# embedding() 向量嵌入与 insert_chunks() 入库详解

> **核心文件**: [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py)
> **调用锚点**: [`../../task_executor.py:1403`](../../task_executor.py) — `do_handle_task()` 中调用 `embedding()`
> **上游文档**: [003-do_handle_task-标准文档解析流程.md](./003-do_handle_task-标准文档解析流程.md)

---

## 🎯 概述

本文档详细分析 RAGFlow 标准文档解析流程中的**向量嵌入（embedding）**和**文档存储入库（insert_chunks）**两个阶段，覆盖完整方法调用栈、数据流转和并发控制机制。

**处理阶段在 `do_handle_task()` 中的位置**：

```
do_handle_task(task)
    ├─ build_chunks()      ← 004/005 文档已覆盖
    ├─ embedding()         ← 本文第一节
    ├─ build_TOC()         ← 可选，后台线程
    └─ insert_chunks()     ← 本文第二节
```

---

## 方法调用栈清单

以 [`../../task_executor.py:1403`](../../task_executor.py) 为锚点，从入口到存储引擎的完整链路：

```
main() :1661
 └─ task_manager() :1650
     └─ handle_task() :1491
         ├─ collect() :211
         └─ do_handle_task(task) :1187
             ├─ init_kb() :687
             ├─ build_chunks() :304
             ├─ embedding() :704                    ← 【锚点 1403】
             │   ├─ mdl.encode() (LLMBundle)       ← 模型编码
             │   │   └─ self.mdl.encode()           ← 底层嵌入模型
             │   └─ q_<dim>_vec 赋值 :785           ← 向量写入 chunk
             └─ insert_chunks() :1096               ← 入库
                 └─ docStoreConn.insert() :296       ← ES/Infinity 写入
```

---

## 1️⃣ embedding() 向量嵌入详解

**文件位置**: [`../../rag/svr/task_executor.py:704-786`](../../rag/svr/task_executor.py:704-786)

### 1.1 函数签名与参数

```python
async def embedding(docs, mdl, parser_config=None, callback=None):
    """对 chunk 列表进行向量嵌入（Embedding）。

    Args:
        docs: chunk 列表（已包含 content_with_weight 等字段）
        mdl: 嵌入模型（LLMBundle 实例）
        parser_config: 解析器配置，包含 filename_embd_weight 等
        callback: 进度回调函数

    Returns:
        (token_count, vector_size) 元组：消耗的 token 数和向量维度
    """
```

### 1.2 调用点

**文件位置**: [`../../rag/svr/task_executor.py:1400-1414`](../../rag/svr/task_executor.py:1400-1414)

```python
start_ts = timer()
try:
    token_count, vector_size = await embedding(
        chunks,                  # ← build_chunks() 的输出
        embedding_model,         # ← LLMBundle 实例
        task_parser_config,      # ← 解析器配置
        progress_callback        # ← 进度回调
    )
except TaskCanceledException:
    raise
except Exception as e:
    error_message = "Generate embedding error:{}".format(str(e))
    progress_callback(-1, error_message)
    logging.exception(error_message)
    token_count = 0
    raise
```

### 1.3 嵌入策略概览

embedding 函数采用**双通道加权混合**策略：

```
                ┌─────────────────────┐
                │   文件名嵌入向量     │  ← 所有 chunk 共享同一文件名向量
                │   (title_embd)      │
                └─────────┬───────────┘
                          │  title_w (默认 0.1)
                          │ ×
                ┌─────────┴───────────┐
                │   加权混合           │  vects = title_w * tts + (1-title_w) * cnts
                │   (weighted merge)  │
                ┌─────────┴───────────┐
                          │ ×
                          │  (1 - title_w) (默认 0.9)
                ┌─────────┴───────────┐
                │   内容嵌入向量       │  ← 每个 chunk 独立的内容向量
                │   (content_embd)    │
                └─────────────────────┘
```

### 1.4 完整流程分步详解

#### 步骤 1：准备嵌入文本

**文件位置**: `task_executor.py:728-741`

```python
if parser_config is None:
    parser_config = {}

# 准备文本：tts 为文件名列表，cnts 为内容列表
tts, cnts = [], []
for d in docs:
    tts.append(d.get("docnm_kwd", "Title"))
    # 优先使用问题关键词作为嵌入文本
    c = "/n".join(d.get("question_kwd", []))
    if not c:
        c = d["content_with_weight"]
    # 去除 HTML 表格标签，避免干扰嵌入质量
    c = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", c)
    if not c:
        c = "None"
    cnts.append(c)
```

**文本选取优先级**：

| 优先级 | 字段 | 说明 |
|--------|------|------|
| 1 | `question_kwd` | LLM 生成的问题关键词（若启用 `auto_questions`） |
| 2 | `content_with_weight` | chunk 原始内容（带权重分词） |
| 3 | `"None"` | 兜底空文本 |

**HTML 清理**：移除 `<table>`, `<td>`, `<tr>`, `<th>`, `<caption>` 标签，避免表格标记污染嵌入向量。

---

#### 步骤 2：生成文件名嵌入向量

**文件位置**: `task_executor.py:743-748`

```python
tk_count = 0
# 只编码一次文件名，然后 tile 扩展到所有 chunk
if len(tts) == len(cnts):
    vts, c = await thread_pool_exec(mdl.encode, tts[0:1])
    tts = np.tile(vts[0], (len(cnts), 1))   # 广播复制
    tk_count += c
```

**关键点**：
- 文件名只调用**一次** `mdl.encode()`，通过 `np.tile()` 广播到所有 chunk
- 所有同文档的 chunk 共享同一个文件名嵌入向量
- 通过 `thread_pool_exec` 提交到线程池执行，避免阻塞事件循环

**调用链**：

```
task_executor.py:746  await thread_pool_exec(mdl.encode, tts[0:1])
    ↓
common/misc_utils.py:128  thread_pool_exec(func, *args, **kwargs)
    ↓  loop.run_in_executor(_thread_pool_executor(), func)
api/db/services/llm_service.py:95  LLMBundle.encode(texts)
    ↓  self.mdl.encode(safe_texts)
api/db/services/tenant_llm_service.py:399  TenantLLMService.model_instance() 创建的底层模型
    ↓  具体的嵌入模型（OpenAI / BAAI / 本地模型等）
```

---

#### 步骤 3：分批生成内容嵌入向量

**文件位置**: `task_executor.py:750-767`

```python
@timeout(60)
def batch_encode(txts):
    """批量编码文本为向量，截断到模型最大长度。"""
    nonlocal mdl
    return mdl.encode([truncate(c, mdl.max_length - 10) for c in txts])

# 分批进行内容嵌入，受 embed_limiter 并发控制
cnts_ = np.array([])
for i in range(0, len(cnts), settings.EMBEDDING_BATCH_SIZE):
    async with embed_limiter:
        vts, c = await thread_pool_exec(
            batch_encode,
            cnts[i: i + settings.EMBEDDING_BATCH_SIZE]
        )
    if len(cnts_) == 0:
        cnts_ = vts
    else:
        cnts_ = np.concatenate((cnts_, vts), axis=0)
    tk_count += c
    callback(prog=0.7 + 0.2 * (i + 1) / len(cnts), msg="")
cnts = cnts_
```

**关键配置**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `settings.EMBEDDING_BATCH_SIZE` | 每批编码的 chunk 数量 | 由配置文件决定 |
| `mdl.max_length` | 模型最大 token 长度 | 模型配置（如 512, 8192） |
| `embed_limiter` | 并发信号量 | `MAX_CONCURRENT_CHUNK_BUILDERS`（默认 1） |
| `@timeout(60)` | 单批编码超时 | 60 秒 |

**文本截断策略**：
```python
truncate(c, mdl.max_length - 10)
```
- 每条文本截断到 `模型最大长度 - 10`，留 10 token 安全余量
- 截断函数位于 [`../../common/token_utils.py`](../../common/token_utils.py)

**进度计算**：
- 嵌入阶段进度范围：`0.7` ~ `0.9`
- 公式：`0.7 + 0.2 × (已处理数 / 总数)`

---

#### 步骤 4：加权混合文件名向量和内容向量

**文件位置**: `task_executor.py:768-777`

```python
# 文件名嵌入权重：控制文件名向量在最终向量中的占比
filename_embd_weight = parser_config.get("filename_embd_weight", 0.1)
if not filename_embd_weight:
    filename_embd_weight = 0.1
title_w = float(filename_embd_weight)

# 加权混合
if tts.ndim == 2 and cnts.ndim == 2 and tts.shape == cnts.shape:
    vects = title_w * tts + (1 - title_w) * cnts
else:
    vects = cnts   # 维度不匹配时降级为纯内容向量
```

**加权公式**：
```
final_vector = 0.1 × filename_embedding + 0.9 × content_embedding
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `filename_embd_weight` | 0.1 | 文件名向量权重，范围 0~1 |

---

#### 步骤 5：将向量写入 chunk

**文件位置**: `task_executor.py:779-786`

```python
assert len(vects) == len(docs)

# 将混合向量写入每个 chunk 的 q_<dim>_vec 字段
vector_size = 0
for i, d in enumerate(docs):
    v = vects[i].tolist()
    vector_size = len(v)
    d["q_%d_vec" % len(v)] = v

return tk_count, vector_size
```

**向量字段命名规则**：
- 字段名格式：`q_{dimension}_vec`
- 示例：模型输出 1024 维向量 → 字段名为 `q_1024_vec`

| 模型 | 向量维度 | 字段名 |
|------|----------|--------|
| BAAI/bge-large-zh | 1024 | `q_1024_vec` |
| text-embedding-ada-002 | 1536 | `q_1536_vec` |
| BAAI/bge-small-zh | 512 | `q_512_vec` |

**返回值**：
- `tk_count`：消耗的总 token 数
- `vector_size`：向量维度（用于后续创建索引）

---

### 1.5 LLMBundle.encode() 调用链

**文件位置**: `api/db/services/llm_service.py:95-118`

```python
class LLMBundle(LLM4Tenant):
    def encode(self, texts: list):
        # 1. Langfuse 追踪（可选）
        if self.langfuse:
            generation = self.langfuse.start_generation(...)

        # 2. 文本安全截断
        safe_texts = []
        for text in texts:
            token_size = num_tokens_from_string(text)
            if token_size > self.max_length:
                target_len = int(self.max_length * 0.95)
                safe_texts.append(text[:target_len])
            else:
                safe_texts.append(text)

        # 3. 调用底层模型编码
        embeddings, used_tokens = self.mdl.encode(safe_texts)

        # 4. 更新 token 用量
        if self.model_config["llm_factory"] != "Builtin":
            TenantLLMService.increase_usage_by_id(self.model_config["id"], used_tokens)

        # 5. Langfuse 结束追踪
        if self.langfuse:
            generation.update(usage_details={"total_tokens": used_tokens})
            generation.end()

        return embeddings, used_tokens
```

**调用链**：

```
LLMBundle.encode(texts)                    # llm_service.py:95
  ├─ num_tokens_from_string(text)          # token_utils.py — 计算 token 数
  ├─ 截断到 max_length × 0.95              # 二次安全截断
  ├─ self.mdl.encode(safe_texts)           # 底层嵌入模型执行编码
  │   └─ TenantLLMService.model_instance() # tenant_llm_service.py:399
  │       └─ 具体模型实现                    # rag/llm/embeddings_model/
  └─ TenantLLMService.increase_usage_by_id # 更新 token 用量统计
```

**LLM4Tenant 初始化**：

```
api/db/services/tenant_llm_service.py:394-401

class LLM4Tenant:
    def __init__(self, tenant_id, model_config, lang="Chinese", **kwargs):
        self.tenant_id = tenant_id
        self.llm_name = model_config["llm_name"]
        self.model_config = model_config
        self.mdl = TenantLLMService.model_instance(model_config, lang=lang, **kwargs)
        self.max_length = model_config.get("max_tokens", 8192)
```

---

### 1.6 并发控制

**文件位置**: `task_executor.py:134-143`

```python
# 并发控制参数
MAX_CONCURRENT_CHUNK_BUILDERS = int(os.environ.get('MAX_CONCURRENT_CHUNK_BUILDERS', "1"))

# 信号量定义
embed_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS)
```

| 信号量 | 默认值 | 环境变量 | 控制范围 |
|--------|--------|----------|----------|
| `embed_limiter` | 1 | `MAX_CONCURRENT_CHUNK_BUILDERS` | 向量嵌入并发批次数 |
| `task_limiter` | 5 | `MAX_CONCURRENT_TASKS` | 最大并发任务数 |
| `chunk_limiter` | 1 | `MAX_CONCURRENT_CHUNK_BUILDERS` | chunk 构建并发数 |

---

### 1.7 embedding 完整流程图

```
embedding(docs, mdl, parser_config, callback)
    │
    ├─→ 【步骤1: 准备嵌入文本】 :728-741
    │     ├─ tts ← docnm_kwd（文件名）
    │     ├─ cnts ← question_kwd 优先，否则 content_with_weight
    │     └─ 清理 HTML 表格标签
    │
    ├─→ 【步骤2: 文件名嵌入】 :743-748
    │     ├─ thread_pool_exec(mdl.encode, tts[0:1])
    │     │     └─ LLMBundle.encode() → mdl.encode()
    │     └─ np.tile() 广播到所有 chunk
    │
    ├─→ 【步骤3: 分批内容嵌入】 :750-767
    │     │
    │     └─ for batch in chunks[::EMBEDDING_BATCH_SIZE]:
    │           ├─ async with embed_limiter:  ← 并发控制
    │           ├─ batch_encode(batch)
    │           │     ├─ truncate(text, max_length-10)  ← 文本截断
    │           │     └─ mdl.encode(truncated_texts)    ← 模型编码
    │           └─ np.concatenate() 拼接批次结果
    │
    ├─→ 【步骤4: 加权混合】 :768-777
    │     └─ vects = 0.1 × tts + 0.9 × cnts
    │
    └─→ 【步骤5: 写入 chunk】 :779-786
          └─ d["q_{dim}_vec"] = vector.tolist()
```

---

## 2️⃣ insert_chunks() 入库详解

**文件位置**: [`../../rag/svr/task_executor.py:1096-1183`](../../rag/svr/task_executor.py:1096-1183)

### 2.1 函数签名与参数

```python
async def insert_chunks(task_id, task_tenant_id, task_dataset_id, chunks, progress_callback):
    """将 chunk 列表批量插入文档存储（Elasticsearch 或 Infinity）。

    Args:
        task_id: 任务 ID
        task_tenant_id: 租户 ID
        task_dataset_id: 知识库 ID
        chunks: chunk 字典列表（已包含 q_<dim>_vec 向量字段）
        progress_callback: 进度回调函数

    Returns:
        True 表示插入成功，False 表示插入被中断
    """
```

### 2.2 调用点

**文件位置**: `task_executor.py:1422-1434`

```python
# 封装带取消检查的 chunk 插入逻辑
async def _maybe_insert_chunks(_chunks):
    """检查任务是否取消后执行 chunk 插入操作。"""
    if has_canceled(task_id):
        progress_callback(-1, msg="Task has been canceled.")
        return False
    insert_result = await insert_chunks(
        task_id, task_tenant_id, task_dataset_id, _chunks, progress_callback
    )
    return bool(insert_result)

# 步骤 6：将 chunks 插入文档存储
if not await _maybe_insert_chunks(chunks):
    return
```

---

### 2.3 完整流程分步详解

#### 步骤 1：处理母 chunk（mother chunks）

**文件位置**: `task_executor.py:1116-1137`

```python
# 处理母 chunk（mom）：包含完整上下文信息的特殊 chunk
mothers = []
mother_ids = set([])
for ck in chunks:
    mom = ck.get("mom") or ck.get("mom_with_weight") or ""
    if not mom:
        continue
    # 基于 mom 内容生成唯一 ID
    id = xxhash.xxh64(mom.encode("utf-8")).hexdigest()
    ck["mom_id"] = id
    if id in mother_ids:
        continue
    mother_ids.add(id)
    # 构建母 chunk 对象（仅保留关键字段）
    mom_ck = copy.deepcopy(ck)
    mom_ck["id"] = id
    mom_ck["content_with_weight"] = mom
    mom_ck["available_int"] = 0
    # 清除无关字段，只保留核心字段
    flds = list(mom_ck.keys())
    for fld in flds:
        if fld not in ["id", "content_with_weight", "doc_id", "docnm_kwd",
                        "kb_id", "available_int", "position_int",
                        "create_timestamp_flt", "page_num_int", "top_int"]:
            del mom_ck[fld]
    mothers.append(mom_ck)
```

**母 chunk（mother chunk）机制**：
- 某些 chunk 包含层级关系，`mom` 字段存储父级完整上下文
- 母 chunk 只保留核心索引字段，不包含向量字段
- `available_int = 0` 标记母 chunk 不可直接检索

| 母 chunk 保留字段 | 说明 |
|-------------------|------|
| `id` | 基于 mom 内容的 xxhash ID |
| `content_with_weight` | 完整上下文内容 |
| `doc_id` | 文档 ID |
| `docnm_kwd` | 文档名关键词 |
| `kb_id` | 知识库 ID |
| `available_int` | 可用性标记（0=不可检索） |
| `position_int` | 位置信息 |
| `create_timestamp_flt` | 创建时间戳 |
| `page_num_int` | 页码信息 |
| `top_int` | 置顶标记 |

---

#### 步骤 2：分批插入母 chunk

**文件位置**: `task_executor.py:1139-1146`

```python
# 先分批插入母 chunk
for b in range(0, len(mothers), settings.DOC_BULK_SIZE):
    await thread_pool_exec(
        settings.docStoreConn.insert,
        mothers[b:b + settings.DOC_BULK_SIZE],
        search.index_name(task_tenant_id),
        task_dataset_id,
    )
    task_canceled = has_canceled(task_id)
    if task_canceled:
        progress_callback(-1, msg="Task has been canceled.")
        return False
```

**调用链**：

```
task_executor.py:1141  thread_pool_exec(settings.docStoreConn.insert, ...)
    ↓
common/misc_utils.py:128  thread_pool_exec(func, *args, **kwargs)
    ↓  loop.run_in_executor()
rag/utils/es_conn.py:296  ESConnection.insert(documents, index_name, knowledgebase_id)
    ↓  构建 bulk operations
    ↓  self.es.bulk(index=index_name, operations=operations)
```

---

#### 步骤 3：分批插入普通 chunk

**文件位置**: `task_executor.py:1148-1183`

```python
# 分批插入普通 chunk
for b in range(0, len(chunks), settings.DOC_BULK_SIZE):
    doc_store_result = await thread_pool_exec(
        settings.docStoreConn.insert,
        chunks[b:b + settings.DOC_BULK_SIZE],
        search.index_name(task_tenant_id),
        task_dataset_id,
    )

    # 检查任务取消
    task_canceled = has_canceled(task_id)
    if task_canceled:
        progress_callback(-1, msg="Task has been canceled.")
        return False

    # 进度更新（每 128 个批次更新一次）
    if b % 128 == 0:
        progress_callback(prog=0.8 + 0.1 * (b + 1) / len(chunks), msg="")

    # 错误检查
    if doc_store_result:
        error_message = f"Insert chunk error: {doc_store_result}, ..."
        progress_callback(-1, msg=error_message)
        raise Exception(error_message)

    # 断点续传：记录已插入的 chunk IDs
    chunk_ids = [chunk["id"] for chunk in chunks[:b + settings.DOC_BULK_SIZE]]
    chunk_ids_str = " ".join(chunk_ids)
    try:
        TaskService.update_chunk_ids(task_id, chunk_ids_str)
    except DoesNotExist:
        # 任务已被删除，回滚已插入的数据
        doc_store_result = await thread_pool_exec(
            settings.docStoreConn.delete,
            {"id": chunk_ids},
            search.index_name(task_tenant_id),
            task_dataset_id,
        )
        # 删除已上传的图片
        tasks = []
        for chunk_id in chunk_ids:
            tasks.append(asyncio.create_task(delete_image(task_dataset_id, chunk_id)))
        # ... 清理逻辑 ...
        return False
```

**进度计算**：
- 插入阶段进度范围：`0.8` ~ `0.9`
- 公式：`0.8 + 0.1 × (已处理数 / 总数)`

**断点续传机制**：
- 每批插入成功后，将 chunk_ids 写入 `TaskService`
- 如果任务中断重启，可以从断点处继续插入
- 如果任务已被删除，自动回滚已插入数据和图片

---

### 2.4 ESConnection.insert() 详解

**文件位置**: `rag/utils/es_conn.py:296-333`

```python
class ESConnection(ESConnectionBase):
    def insert(self, documents: list[dict], index_name: str, knowledgebase_id: str = None) -> list[str]:
        # 构建 bulk 操作列表
        operations = []
        for d in documents:
            assert "_id" not in d
            assert "id" in d
            d_copy = copy.deepcopy(d)
            d_copy["kb_id"] = knowledgebase_id
            meta_id = d_copy.get("id", "")
            operations.append({"index": {"_index": index_name, "_id": meta_id}})
            operations.append(d_copy)

        # 重试机制：最多尝试 ATTEMPT_TIME 次
        res = []
        for _ in range(ATTEMPT_TIME):
            try:
                res = []
                r = self.es.bulk(
                    index=index_name,
                    operations=operations,
                    refresh=False,        # 不立即刷新，提高性能
                    timeout="60s"         # 单次请求 60 秒超时
                )
                # 检查是否有错误
                if re.search(r"False", str(r["errors"]), re.IGNORECASE):
                    return res    # 成功，返回空列表

                # 收集错误信息
                for item in r["items"]:
                    for action in ["create", "delete", "index", "update"]:
                        if action in item and "error" in item[action]:
                            res.append(str(item[action]["_id"]) + ":" + str(item[action]["error"]))
                return res
            except ConnectionTimeout:
                self.logger.exception("ES request timeout")
                time.sleep(3)
                self._connect()    # 重连
                continue
            except Exception as e:
                res.append(str(e))
                self.logger.warning("ESConnection.insert got exception: " + str(e))

        return res
```

**关键特性**：

| 特性 | 实现 |
|------|------|
| 批量写入 | Elasticsearch Bulk API |
| 重试机制 | `ATTEMPT_TIME` 次重试 |
| 超时处理 | 60 秒超时 + 自动重连 |
| 错误收集 | 逐条收集 `_id:error` 对 |
| 刷新策略 | `refresh=False`，延迟刷新提高吞吐 |

---

### 2.5 存储引擎切换

**文件位置**: `common/settings.py:260-283`

```python
DOC_ENGINE = os.environ.get("DOC_ENGINE", "elasticsearch").lower()

if DOC_ENGINE == "elasticsearch":
    docStoreConn = rag.utils.es_conn.ESConnection()
elif DOC_ENGINE == "infinity":
    docStoreConn = rag.utils.infinity_conn.InfinityConnection()
elif DOC_ENGINE == "opensearch":
    docStoreConn = rag.utils.opensearch_conn.OSConnection()
elif DOC_ENGINE in ("oceanbase", "ob"):
    docStoreConn = rag.utils.ob_conn.OBConnection()
```

| 存储引擎 | 环境变量值 | 连接类 | 文件位置 |
|----------|-----------|--------|----------|
| Elasticsearch | `elasticsearch`（默认） | `ESConnection` | [`../../rag/utils/es_conn.py:62`](../../rag/utils/es_conn.py) |
| Infinity | `infinity` | `InfinityConnection` | [`../../rag/utils/infinity_conn.py:30`](../../rag/utils/infinity_conn.py) |
| OpenSearch | `opensearch` | `OSConnection` | [`../../rag/utils/opensearch_conn.py`](../../rag/utils/opensearch_conn.py) |
| OceanBase | `oceanbase` / `ob` | `OBConnection` | [`../../rag/utils/ob_conn.py`](../../rag/utils/ob_conn.py) |

---

### 2.6 索引名称生成

**文件位置**: [`../../rag/nlp/search.py:56`](../../rag/nlp/search.py)

```python
def index_name(uid):
    """根据租户 ID 生成索引名称"""
    return f"ragflow_{uid}"
```

---

### 2.7 insert_chunks 完整流程图

```
insert_chunks(task_id, tenant_id, dataset_id, chunks, callback)
    │
    ├─→ 【步骤1: 处理母 chunk】 :1116-1137
    │     ├─ 提取 mom / mom_with_weight 字段
    │     ├─ xxhash 生成 mom_id
    │     └─ 构建精简的母 chunk 对象（仅保留核心字段）
    │
    ├─→ 【步骤2: 分批插入母 chunk】 :1139-1146
    │     └─ for batch in mothers[::DOC_BULK_SIZE]:
    │           ├─ thread_pool_exec(docStoreConn.insert, batch, idxnm, kb_id)
    │           │     └─ ESConnection.insert()
    │           │           └─ self.es.bulk(operations)
    │           └─ has_canceled() 检查
    │
    └─→ 【步骤3: 分批插入普通 chunk】 :1148-1183
          └─ for batch in chunks[::DOC_BULK_SIZE]:
                ├─ thread_pool_exec(docStoreConn.insert, batch, idxnm, kb_id)
                │     └─ ESConnection.insert()
                │           └─ self.es.bulk(operations)
                ├─ has_canceled() 检查
                ├─ 错误检查（doc_store_result）
                ├─ progress_callback(0.8 + 0.1 × progress)
                └─ TaskService.update_chunk_ids()  ← 断点续传
                      └─ 若任务不存在 → 回滚 + 清理图片
```

---

## 3️⃣ 入库后处理

### 3.1 更新文档统计

**文件位置**: [`../../task_executor.py:1446`](../../task_executor.py)

```python
DocumentService.increment_chunk_num(
    task_doc_id,       # 文档 ID
    task_dataset_id,   # 知识库 ID
    token_count,       # token 消耗数
    chunk_count,       # chunk 数量
    0                  # 占位参数
)
```

### 3.2 TOC chunk 插入（可选）

**文件位置**: `task_executor.py:1451-1456`

```python
# 等待后台 TOC 线程完成，并将 TOC chunk 插入存储
if toc_thread:
    d = toc_thread.result()
    if d:
        if not await _maybe_insert_chunks([d]):
            return
        DocumentService.increment_chunk_num(task_doc_id, task_dataset_id, 0, 1, 0)
```

### 3.3 任务取消时的清理

**文件位置**: `task_executor.py:1470-1488`

```python
finally:
    if has_canceled(task_id):
        try:
            # 检查索引是否存在
            exists = await thread_pool_exec(
                settings.docStoreConn.index_exist,
                search.index_name(task_tenant_id),
                task_dataset_id,
            )
            if exists:
                # 删除该文档的所有已插入 chunk
                await thread_pool_exec(
                    settings.docStoreConn.delete,
                    {"doc_id": task_doc_id},
                    search.index_name(task_tenant_id),
                    task_dataset_id,
                )
        except Exception as e:
            logging.exception(
                f"Remove doc({task_doc_id}) from docStore failed when task({task_id}) canceled, exception: {e}"
            )
```

---

## 4️⃣ 端到端进度分布

整个标准文档解析流程的进度回调值分布：

```
进度值     阶段
────────────────────────────────────
0.0-0.1    build_chunks 前置处理
0.1-0.5    chunker.chunk 文档解析分块
0.5-0.7    LLM 增强（关键词/问题/元数据/标签）
0.7-0.9    embedding 向量嵌入
0.8-0.9    insert_chunks 文档存储入库
0.9-1.0    统计更新 + TOC
1.0        完成
-1         错误状态
```

---

## 5️⃣ 关键数据结构

### embedding 输入的 chunk 结构

```python
chunk = {
    "id": "xxhash_id",                       # chunk ID
    "doc_id": "doc_id",                      # 文档 ID
    "kb_id": "kb_id",                        # 知识库 ID
    "docnm_kwd": "document.pdf",             # 文档名（用于文件名嵌入）
    "content_with_weight": "chunk 内容文本",  # 主内容（用于内容嵌入）
    "question_kwd": ["问题1", "问题2"],       # 问题关键词（优先用于嵌入）
    "img_id": "img_xxx",                     # 图片 ID
    "mom": "完整上下文文本",                   # 母 chunk 内容（可选）
    # ... 其他字段
}
```

### embedding 输出的 chunk 结构

```python
chunk = {
    # ... 上述所有字段保留
    "q_1024_vec": [0.1, -0.2, 0.05, ...],   # 1024 维混合嵌入向量
}
```

### 写入 ES 的文档结构

```python
es_doc = {
    "_index": "ragflow_{tenant_id}",          # 索引名
    "_id": "{chunk_id}",                      # 文档 ID
    "id": "{chunk_id}",                       # 保留为普通字段（用于排序）
    "doc_id": "{doc_id}",                     # 文档 ID
    "kb_id": "{kb_id}",                       # 知识库 ID
    "content_with_weight": "...",             # 内容
    "content_ltks": "...",                    # 长文本分词
    "content_sm_ltks": "...",                 # 短文本分词
    "q_1024_vec": [0.1, -0.2, ...],          # 向量字段（dense_vector 类型）
    "available_int": 1,                       # 可用性标记
    "create_timestamp_flt": 1705344000.0,     # 创建时间戳
    # ... 其他索引字段
}
```

---

## 🔗 相关文件清单

| 文件 | 行号 | 说明 |
|-----|------|------|
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | 704-786 | `embedding()` 函数 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | 1096-1183 | `insert_chunks()` 函数 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | 1403 | `embedding()` 调用锚点 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | 1428 | `insert_chunks()` 调用点 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | 687-701 | `init_kb()` 索引初始化 |
| [`../../api/db/services/llm_service.py`](../../api/db/services/llm_service.py) | 85-118 | `LLMBundle.encode()` |
| [`../../api/db/services/tenant_llm_service.py`](../../api/db/services/tenant_llm_service.py) | 394-401 | `LLM4Tenant.__init__()` |
| [`../../rag/utils/es_conn.py`](../../rag/utils/es_conn.py) | 62 | `ESConnection` 类 |
| [`../../rag/utils/es_conn.py`](../../rag/utils/es_conn.py) | 296-333 | `ESConnection.insert()` |
| [`../../rag/utils/infinity_conn.py`](../../rag/utils/infinity_conn.py) | 30 | `InfinityConnection` 类 |
| [`../../rag/nlp/search.py`](../../rag/nlp/search.py) | 56 | `index_name()` 索引名生成 |
| [`../../common/misc_utils.py`](../../common/misc_utils.py) | 128-133 | `thread_pool_exec()` 线程池执行 |
| [`../../common/settings.py`](../../common/settings.py) | 260-283 | 存储引擎初始化 |
| [`../../common/token_utils.py`](../../common/token_utils.py) | - | `truncate()` / `num_tokens_from_string()` |

---

**相关文档**：
- [003-do_handle_task-标准文档解析流程.md](./003-do_handle_task-标准文档解析流程.md) — 完整任务流程
- [004-chunker.chunk-文本分块逻辑详解.md](./004-chunker.chunk-文本分块逻辑详解.md) — 分块逻辑
- [005-不同文件类型的切片策略.md](./005-不同文件类型的切片策略.md) — 文件类型解析策略
