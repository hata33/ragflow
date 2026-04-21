# do_handle_task() 标准文档解析流程详解

> **文件位置**: [`../../rag/svr/task_executor.py:1187`](../../rag/svr/task_executor.py)
> **核心功能**: 处理单个文档解析任务，执行分块、向量化、入库等核心流程

---

## 🎯 概述

`do_handle_task()` 是 RAGFlow 文档解析系统的**核心执行引擎**，负责实际处理从 Redis 队列中取出的文档解析任务。

本文档专门描述**标准文档解析流程**（分块 → 嵌入 → 入库），不涉及 DataFlow、GraphRAG、RAPTOR 等特殊任务类型。

---

## 📋 标准解析流程总览

```
do_handle_task(task)
    │
    ├─→ 【前置处理】
    │   ├─ 提取任务参数
    │   ├─ 检查任务是否取消
    │   ├─ 绑定嵌入模型
    │   └─ 初始化知识库索引
    │
    └─→ 【标准解析流程】
        ├─ build_chunks()      # 文档分块
        ├─ embedding()         # 向量嵌入
        ├─ build_TOC()         # 可选：生成目录
        └─ insert_chunks()     # 写入文档存储
```

---

## 1️⃣ 入口与路由判断

**文件位置**: [`../../rag/svr/task_executor.py:1187-1214`](../../rag/svr/task_executor.py:1187-1214)

```python
@timeout(60 * 60 * 3, 1)  # 3小时超时保护
async def do_handle_task(task):
    """处理单个任务的核心逻辑"""
    
    task_type = task.get("task_type", "")
    
    # ========== 特殊任务路由 ==========
    
    # 路径A: Memory 任务
    if task_type == "memory":
        await handle_save_to_memory_task(task)
        return
    
    # 路径B: DataFlow 任务（Canvas 调试模式）
    if task_type == "dataflow" and task.get("doc_id", "") == CANVAS_DEBUG_DOC_ID:
        await run_dataflow(task)
        return
    
    # ========== 标准文档解析流程入口 ==========
    # 以下代码处理所有其他任务类型，包括标准解析
```

**关键点**:
- 函数签名使用 `@timeout` 装饰器，3小时超时保护
- 首先检查 `task_type`，特殊任务直接返回
- 标准解析流程继续向下执行

---

## 2️⃣ 提取任务参数

**文件位置**: [`../../rag/svr/task_executor.py:1227-1250`](../../rag/svr/task_executor.py:1227-1250)

```python
    # ========== 提取任务关键参数 ==========
    task_id = task["id"]                          # 任务ID
    task_from_page = task["from_page"]            # 起始页
    task_to_page = task["to_page"]                # 结束页
    task_tenant_id = task["tenant_id"]            # 租户ID
    task_embedding_id = task["embd_id"]           # 嵌入模型ID
    task_language = task["language"]              # 语言
    
    # LLM ID 优先级：解析器配置 > 任务配置，知识库级 > 文档级
    doc_task_llm_id = task["parser_config"].get("llm_id") or task["llm_id"]
    kb_task_llm_id = task['kb_parser_config'].get("llm_id") or task["llm_id"]
    task['llm_id'] = kb_task_llm_id               # 使用知识库级LLM
    
    task_dataset_id = task["kb_id"]               # 知识库ID
    task_doc_id = task["doc_id"]                  # 文档ID
    task_document_name = task["name"]             # 文档名称
    task_parser_config = task["parser_config"]    # 解析器配置
    task_start_ts = timer()                       # 开始时间（计时用）
    
    # 用于在后台线程中执行 TOC 生成
    toc_thread = None
    executor = concurrent.futures.ThreadPoolExecutor()
    
    # 构建进度回调函数，绑定任务 ID 和页码范围
    progress_callback = partial(set_progress, task_id, task_from_page, task_to_page)
```

**关键参数说明**:

| 参数 | 说明 | 来源 |
|-----|------|-----|
| `task_id` | 任务唯一标识 | Redis 消息 |
| `task_from_page` | 起始页码 | PDF/Excel 分片使用 |
| `task_to_page` | 结束页码 | PDF/Excel 分片使用 |
| `task_tenant_id` | 租户ID | 从文档关联获取 |
| `task_embedding_id` | 嵌入模型ID | 知识库配置 |
| `task_language` | 文档语言 | 知识库配置 |
| `task_parser_config` | 解析器配置 | 包含分块、LLM等配置 |

**进度回调函数**:
```python
progress_callback = partial(set_progress, task_id, task_from_page, task_to_page)
# 等价于: lambda prog, msg: set_progress(task_id, task_from_page, task_to_page, prog, msg)
```

---

## 3️⃣ 检查任务取消状态

**文件位置**: [`../../rag/svr/task_executor.py:1250-1253`](../../rag/svr/task_executor.py:1250-1253)

```python
    # ========== 检查任务是否已取消 ==========
    task_canceled = has_canceled(task_id)
    if task_canceled:
        progress_callback(-1, msg="Task has been canceled.")
        return
```

**取消机制**:
- `has_canceled()` 检查 Redis 中的 `{task_id}-cancel` 键
- 如果键存在，说明任务被取消，直接返回
- 取消标志由 `/document/run` 接口的 `run: "2"` 参数设置

**相关文件**: [`../../api/db/services/task_service.py:517`](../../api/db/services/task_service.py)

```python
def has_canceled(task_id):
    """检查任务是否已被取消"""
    try:
        if REDIS_CONN.get(f"{task_id}-cancel"):
            logging.info(f"Task: {task_id} has been canceled")
            return True
    except Exception as e:
        logging.exception(e)
    return False
```

---

## 4️⃣ 绑定嵌入模型

**文件位置**: [`../../rag/svr/task_executor.py:1256-1269`](../../rag/svr/task_executor.py:1256-1269)

```python
    # ========== 步骤1: 绑定嵌入模型 ==========
    try:
        # 获取嵌入模型配置
        if task_embedding_id:
            # 使用指定的嵌入模型
            embd_model_config = get_model_config_by_type_and_name(
                task_tenant_id,      # 租户ID
                LLMType.EMBEDDING,    # 模型类型：嵌入
                task_embedding_id     # 模型ID
            )
        else:
            # 使用租户默认嵌入模型
            embd_model_config = get_tenant_default_model_by_type(
                task_tenant_id,      # 租户ID
                LLMType.EMBEDDING     # 模型类型：嵌入
            )
        
        # 创建 LLMBundle 实例
        embedding_model = LLMBundle(
            task_tenant_id,        # 租户ID
            embd_model_config,     # 模型配置
            lang=task_language     # 语言
        )
        
        # 验证模型可用性：编码测试向量
        vts, _ = await embedding_model.encode(["ok"])
        vector_size = len(vts[0])  # 获取向量维度
        
    except Exception as e:
        error_message = f'Fail to bind embedding model: {str(e)}'
        progress_callback(-1, msg=error_message)
        logging.exception(error_message)
        raise
```

**关键流程**:

1. **获取模型配置**
   - 优先使用任务指定的嵌入模型
   - 否则使用租户默认嵌入模型

2. **创建 LLMBundle**
   - 封装嵌入模型的调用接口
   - 支持多种嵌入模型提供商（OpenAI、Azure、本地模型等）

3. **验证模型可用性**
   - 编码测试向量 `["ok"]`
   - 获取向量维度（如 1024、1536 等）
   - 如果失败，设置进度为 -1（错误状态）

**相关文件**:
- [`../../api/db/joint_services/tenant_model_service.py`](../../api/db/joint_services/tenant_model_service.py) - 模型配置获取
- [`../../rag/llm/embeddings.py`](../../rag/llm/embeddings.py) - 嵌入模型实现

---

## 5️⃣ 初始化知识库索引

**文件位置**: [`../../rag/svr/task_executor.py:1272`](../../rag/svr/task_executor.py)

```python
    # ========== 步骤2: 初始化知识库的搜索索引 ==========
    init_kb(task, vector_size)
```

**函数详情**: [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py)

```python
def init_kb(task, vector_size):
    """初始化知识库的搜索索引
    
    确保 Elasticsearch/Infinity 索引存在，并创建必要的映射
    
    Args:
        task: 任务字典
        vector_size: 向量维度
    """
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from rag.nlp import search
    
    # 获取知识库信息
    kb_id = task["kb_id"]
    tenant_id = task["tenant_id"]
    
    # 检查索引是否存在
    index_name = search.index_name(tenant_id)
    if not settings.docStoreConn.index_exist(index_name, kb_id):
        # 创建索引和映射
        settings.docStoreConn.create_index(index_name, kb_id, vector_size)
    
    # 更新知识库的 embd_id（如果需要）
    e, kb = KnowledgebaseService.get_by_id(kb_id)
    if e and kb.embd_id != task.get("embd_id"):
        KnowledgebaseService.update_by_id(kb_id, {"embd_id": task.get("embd_id")})
```

**关键操作**:
1. 检查 Elasticsearch/Infinity 索引是否存在
2. 不存在则创建索引和向量字段映射
3. 确保向量维度正确

---

## 6️⃣ DataFlow 任务路由

**文件位置**: [`../../rag/svr/task_executor.py:1274-1277`](../../rag/svr/task_executor.py:1274-1277)

```python
    # ========== 路由A: DataFlow 任务 ==========
    if task_type[:len("dataflow")] == "dataflow":
        await run_dataflow(task)
        return
```

**说明**: 如果任务类型是 `dataflow` 或 `dataflow_rerun`，走 DataFlow 流程，直接返回。

---

## 7️⃣ 标准文档解析流程

**文件位置**: [`../../rag/svr/task_executor.py:1389-1437`](../../rag/svr/task_executor.py:1389-1437)

这是本文档的核心部分，详细描述标准文档解析的完整流程。

### 7.1 恢复文档级 LLM 配置

```python
    # ========== 路径E: 标准文档解析流程 ==========
    else:
        # 使用文档级 LLM 配置（覆盖知识库级配置）
        task['llm_id'] = doc_task_llm_id
        start_ts = timer()
```

**说明**: 标准解析流程使用文档级的 LLM 配置，而不是知识库级的。

### 7.2 构建文档 Chunks

**文件位置**: [`../../rag/svr/task_executor.py:1393-1398`](../../rag/svr/task_executor.py:1393-1398)

```python
        # ========== 步骤3: 调用 build_chunks 进行文档分块 ==========
        chunks = await build_chunks(task, progress_callback)
        logging.info("Build document {}: {:.2f}s".format(
            task_document_name, 
            timer() - start_ts
        ))
        
        if not chunks:
            progress_callback(1., msg=f"No chunk built from {task_document_name}")
            return
        
        progress_callback(msg="Generate {} chunks".format(len(chunks)))
```

**build_chunks() 详细流程** 见下一节。

### 7.3 生成向量嵌入

**文件位置**: [`../../rag/svr/task_executor.py:1399-1414`](../../rag/svr/task_executor.py:1399-1414)

```python
        # ========== 步骤4: 对 chunks 进行向量嵌入 ==========
        start_ts = timer()
        
        try:
            # 调用 embedding 函数生成向量
            token_count, vector_size = await embedding(
                chunks,                  # chunks 列表
                embedding_model,        # 嵌入模型
                task_parser_config,     # 解析器配置
                progress_callback       # 进度回调
            )
        except TaskCanceledException:
            raise  # 任务被取消，重新抛出
        except Exception as e:
            error_message = "Generate embedding error:{}".format(str(e))
            progress_callback(-1, error_message)
            logging.exception(error_message)
            token_count = 0
            raise
        
        progress_message = "Embedding chunks ({:.2f}s)".format(timer() - start_ts)
        logging.info(progress_message)
        progress_callback(msg=progress_message)
```

**embedding() 详细流程** 见后续章节。

### 7.4 可选：生成 TOC（目录）

**文件位置**: [`../../rag/svr/task_executor.py:1415-1417`](../../rag/svr/task_executor.py:1415-1417)

```python
        # ========== 步骤5: 若启用 TOC 提取，在后台线程中异步生成目录 ==========
        if task["parser_id"].lower() == "naive" /
            and task["parser_config"].get("toc_extraction", False):
            toc_thread = executor.submit(
                build_TOC,           # TOC 生成函数
                task,                # 任务参数
                chunks,              # chunks 数据
                progress_callback    # 进度回调
            )
```

**说明**:
- 仅对 `naive` 解析器且启用 `toc_extraction` 时执行
- 在后台线程中异步执行，不阻塞主流程
- TOC 生成是可选功能

**build_TOC() 函数**: [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py)

```python
def build_TOC(task, chunks, callback):
    """生成文档目录（Table of Contents）
    
    使用 LLM 从文档 chunks 中提取目录结构
    """
    from rag.prompts.generator import run_toc_from_text
    
    # 合并所有 chunks 的内容
    full_text = "/n".join([chunk["content_with_weight"] for chunk in chunks])
    
    # 调用 LLM 生成 TOC
    toc = run_toc_from_text(
        llm_bundle=task["llm_bundle"],
        content=full_text,
        callback=callback
    )
    
    # 保存 TOC 到文档
    DocumentService.update_by_id(task["doc_id"], {"toc": toc})
```

### 7.5 插入 Chunks 到文档存储

**文件位置**: [`../../rag/svr/task_executor.py:1419-1437`](../../rag/svr/task_executor.py:1419-1437)

```python
        # ========== 统计 chunk 数量 ==========
        chunk_count = len(set([chunk["id"] for chunk in chunks]))
        start_ts = timer()

        # ========== 封装带取消检查的 chunk 插入逻辑 ==========
        async def _maybe_insert_chunks(_chunks):
            """检查任务是否取消后执行 chunk 插入操作"""
            if has_canceled(task_id):
                progress_callback(-1, msg="Task has been canceled.")
                return False
            insert_result = await insert_chunks(
                task_id,              # 任务ID
                task_tenant_id,       # 租户ID
                task_dataset_id,      # 知识库ID
                _chunks,              # chunks 列表
                progress_callback     # 进度回调
            )
            return bool(insert_result)

        try:
            # ========== 步骤6: 将 chunks 插入文档存储 ==========
            if not await _maybe_insert_chunks(chunks):
                progress_callback(-1, msg="Failed to insert chunks to docStore")
                return

            # ========== 更新文档统计信息 ==========
            chunk_ids = [c["id"] for c in chunks]
            token_count = sum([c.get("word_count", 0) for c in chunks])
            
            DocumentService.update_chunk_num(
                task_id,
                task_dataset_id,
                len(chunk_ids),
                token_count,
                task_parser_config,
                task["name"]
            )
            
            progress_callback(1.0, msg="Finished")
            
        except TaskCanceledException:
            raise
        except Exception as e:
            progress_callback(-1, msg=str(e))
            raise
```

**关键流程**:

1. **取消检查**: 插入前再次检查任务是否取消
2. **批量插入**: 调用 `insert_chunks()` 写入文档存储
3. **更新统计**: 更新文档的 chunk 数量和 token 数量
4. **异常处理**: 处理任务取消和异常情况

---

## 8️⃣ build_chunks() 详解

**文件位置**: [`../../rag/svr/task_executor.py:304-501`](../../rag/svr/task_executor.py:304-501)

```python
@timeout(60 * 80, 1)  # 80分钟超时
async def build_chunks(task, progress_callback):
    """构建文档的 chunk（分块）数据
    
    完整处理流程：
    1. 检查文件大小是否超出限制
    2. 从 MinIO 获取文件二进制数据
    3. 选择对应的解析器（chunker）
    4. 在线程池中执行 chunker.chunk 方法进行分块
    5. 将 chunk 中的图片上传到 MinIO，生成图片 ID
    6. 可选：LLM 自动生成关键词（auto_keywords）
    7. 可选：LLM 自动生成问题（auto_questions）
    8. 可选：LLM 自动生成元数据（enable_metadata）
    9. 可选：对 chunk 进行自动标签分类（tag_kb_ids）
    
    Args:
        task: 任务字典，包含文件信息、解析配置等
        progress_callback: 进度回调函数
    
    Returns:
        docs: 处理后的 chunk 列表
    """
```

---

### 8️⃣1️⃣ build_chunks 完整流程图

```
build_chunks(task, progress_callback)
    │
    ├─→ 【步骤1: 检查文件大小】
    │     └─ if task["size"] > settings.DOC_MAXIMUM_SIZE: return []
    │
    ├─→ 【步骤2: 选择解析器】
    │     └─ chunker = FACTORY[task["parser_id"].lower()]
    │           └─ naive / paper / book / table / picture / ...
    │
    ├─→ 【步骤3: 从 MinIO 获取文件】
    │     ├─ bucket, name = File2DocumentService.get_storage_address(doc_id)
    │     │     └─ bucket: 通常是知识库 ID
    │     │     └─ name: 文件在对象存储中的路径
    │     │
    │     └─ binary = await get_storage_binary(bucket, name)
    │           └─ STORAGE_IMPL.get(bucket, name) → 返回文件二进制数据
    │
    ├─→ 【步骤4: 在线程池中执行 chunker.chunk】
    │     │
    │     ├─ async with chunk_limiter:  # 并发控制信号量
    │     │
    │     └─ cks = await thread_pool_exec(
    │               chunker.chunk,        # 解析器的 chunk 方法 ⬅️ CPU 密集型
    │               task["name"],         # 文件名
    │               binary=binary,        # 文件二进制数据 ⬅️ 从 MinIO 获取
    │               from_page=...,        # 起始页
    │               to_page=...,          # 结束页
    │               lang=...,             # 语言
    │               callback=...,         # 进度回调
    │               kb_id=...,            # 知识库ID
    │               parser_config=...,    # 解析器配置
    │               tenant_id=...         # 租户ID
    │           )
    │         └─ 返回 chunks 列表
    │
    ├─→ 【步骤5: 处理图片 - 并发上传到 MinIO】
    │     └─ for ck in cks:
    │           └─ asyncio.create_task(upload_to_minio(doc, ck))
    │               └─ image2id() → 上传图片 → 生成 img_id
    │
    ├─→ 【步骤6: LLM 增强处理（可选）】
    │     ├─ auto_keywords → keyword_extraction()
    │     ├─ auto_questions → question_proposal()
    │     └─ enable_metadata → gen_metadata()
    │
    └─→ 【返回】
          └─ return docs  # 处理后的 chunks 列表
```

---

### 8️⃣2️⃣ 步骤1: 检查文件大小

**文件位置**: [`../../rag/svr/task_executor.py:324-328`](../../rag/svr/task_executor.py:324-328)

```python
    # ========== 步骤1: 检查文件大小是否超出系统限制 ==========
    if task["size"] > settings.DOC_MAXIMUM_SIZE:
        set_progress(
            task["id"], 
            prog=-1, 
            msg="File size exceeds( <= %dMb )" % (int(settings.DOC_MAXIMUM_SIZE / 1024 / 1024))
        )
        return []  # 返回空列表
```

**说明**:
- 检查文件大小是否超过系统限制
- 超过限制则设置进度为 -1（错误状态）
- 返回空列表，终止处理

---

### 8️⃣3️⃣ 步骤2: 选择解析器

**文件位置**: [`../../rag/svr/task_executor.py:331`](../../rag/svr/task_executor.py)

```python
    # ========== 步骤2: 根据解析器 ID 从工厂映射表获取对应的解析模块 ==========
    chunker = FACTORY[task["parser_id"].lower()]
```

**FACTORY 映射表**: [`../../rag/svr/task_executor.py:86-100`](../../rag/svr/task_executor.py:86-100)

```python
FACTORY = {
    "general": naive,                              # 通用解析器（naive 的别名）
    ParserType.NAIVE.value: naive,                 # 通用/手动分块解析器
    ParserType.PAPER.value: paper,                 # 学术论文解析器
    ParserType.BOOK.value: book,                   # 书籍解析器
    ParserType.PRESENTATION.value: presentation,   # PPT/演示文稿解析器
    ParserType.MANUAL.value: manual,               # 使用手册解析器
    ParserType.LAWS.value: laws,                   # 法律文档解析器
    ParserType.QA.value: qa,                       # 问答对解析器
    ParserType.TABLE.value: table,                 # 表格解析器
    ParserType.RESUME.value: resume,               # 简历解析器
    ParserType.PICTURE.value: picture,             # 图片解析器
    ParserType.ONE.value: one,                     # 整文档作为单个 chunk 的解析器
    ParserType.AUDIO.value: audio,                 # 音频解析器
    ParserType.EMAIL.value: email,                 # 邮件解析器
}
```

**解析器位置**: [`../../rag/app/`](../../rag/app/)

---

### 8️⃣4️⃣ 步骤3: 从 MinIO 获取文件

**文件位置**: [`../../rag/svr/task_executor.py:334-348`](../../rag/svr/task_executor.py:334-348)

```python
    # ========== 步骤3: 从 MinIO 获取文件二进制数据 ==========
    try:
        st = timer()
        
        # 获取文件在 MinIO 中的存储地址
        bucket, name = File2DocumentService.get_storage_address(doc_id=task["doc_id"])
        # bucket: 通常是知识库 ID
        # name: 文件在对象存储中的路径/文件名
        
        # 从 MinIO 获取文件二进制数据
        binary = await get_storage_binary(bucket, name)
        
        logging.info("From minio({}) {}/{}".format(
            timer() - st,    # 耗时
            task["location"],  # 文件位置
            task["name"]      # 文件名
        ))
        
    except TimeoutError:
        progress_callback(-1, "Internal server error: Fetch file from minio timeout. Could you try it again?")
        logging.exception("Minio {}/{} got timeout: Fetch file from minio timeout.".format(
            task["location"], task["name"]
        ))
        raise
        
    except Exception as e:
        if re.search("(No such file|not found)", str(e)):
            progress_callback(-1, "Can not find file <%s> from minio. Could you try it again?" % task["name"])
        else:
            progress_callback(-1, "Get file from minio: %s" % str(e).replace("'", ""))
        logging.exception("Chunking {}/{} got exception".format(task["location"], task["name"]))
        raise
```

**get_storage_binary() 函数**: [`../../rag/svr/task_executor.py:298-300`](../../rag/svr/task_executor.py:298-300)

```python
async def get_storage_binary(bucket, name):
    """从对象存储（MinIO）中异步获取文件的二进制数据
    
    Args:
        bucket: 存储桶名称（通常是知识库 ID）
        name: 文件路径/文件名
    
    Returns:
        bytes: 文件的二进制数据
    """
    return await thread_pool_exec(settings.STORAGE_IMPL.get, bucket, name)
```

**说明**:
1. `File2DocumentService.get_storage_address()`: 获取文件在 MinIO 中的存储位置
2. `get_storage_binary()`: 从 MinIO 获取文件二进制数据
3. 使用 `thread_pool_exec` 在线程池中执行，避免阻塞事件循环
4. 处理各种异常情况（超时、文件不存在等）

---

### 8️⃣5️⃣ 步骤4: 在线程池中执行 chunker.chunk

**文件位置**: [`../../rag/svr/task_executor.py:350-371`](../../rag/svr/task_executor.py:350-371)

这是整个 `build_chunks` 的**核心步骤**，体现了你提到的关键流程：

```python
    # ========== 步骤4: 调用解析器进行文档分块，受 chunk_limiter 并发控制 ==========
    try:
        # 使用并发控制信号量，限制同时进行的文档解析任务数量
        async with chunk_limiter:
            
            # 在线程池中执行 chunker.chunk 方法
            cks = await thread_pool_exec(
                chunker.chunk,                    # 要执行的函数（CPU 密集型）
                task["name"],                     # 文件名
                binary=binary,                     # 文件二进制数据 ⬅️ 从 MinIO 获取
                from_page=task["from_page"],      # 起始页
                to_page=task["to_page"],          # 结束页
                lang=task["language"],            # 语言
                callback=progress_callback,       # 进度回调
                kb_id=task["kb_id"],              # 知识库ID
                parser_config=task["parser_config"],  # 解析器配置
                tenant_id=task["tenant_id"],      # 租户ID
            )
        
        logging.info("Chunking({}) {}/{} done".format(
            timer() - st,    # 总耗时
            task["location"],  # 文件位置
            task["name"]      # 文件名
        ))
        
    except TaskCanceledException:
        raise  # 任务被取消，重新抛出
        
    except Exception as e:
        progress_callback(-1, "Internal server error while chunking: %s" % str(e).replace("'", ""))
        logging.exception("Chunking {}/{} got exception".format(task["location"], task["name"]))
        raise
```

---

#### 🔍 关键点详解

##### 1️⃣ chunk_limiter 并发控制

```python
async with chunk_limiter:
    # 限制同时进行的文档解析任务数量
    ...
```

**说明**:
- `chunk_limiter` 是一个 `asyncio.Semaphore` 信号量
- 限制同时进行的文档解析任务数量，防止资源耗尽
- 文档解析是 CPU 密集型操作，需要控制并发

**定义位置**: [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py)

```python
chunk_limiter = asyncio.Semaphore(settings.CHUNK_CONCURRENCY)  # 默认值通常为 4-8
```

##### 2️⃣ thread_pool_exec 线程池执行

```python
cks = await thread_pool_exec(
    chunker.chunk,      # 同步函数
    task["name"],
    binary=binary,
    ...
)
```

**为什么使用线程池？**

1. **CPU 密集型任务**: 文档解析（特别是 PDF 解析）是 CPU 密集型操作
2. **避免阻塞事件循环**: 在线程池中执行可以避免阻塞 asyncio 事件循环
3. **提高并发能力**: 允许同时处理多个文档解析任务

**thread_pool_exec 实现**: [`../../common/misc_utils.py`](../../common/misc_utils.py)

```python
async def thread_pool_exec(func, *args, **kwargs):
    """在线程池中执行同步函数，返回异步结果
    
    Args:
        func: 要执行的同步函数
        *args: 位置参数
        **kwargs: 关键字参数
    
    Returns:
        函数的返回值
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))
```

**工作原理**:
```
async def build_chunks():
    ↓
await thread_pool_exec(chunker.chunk, binary, ...)
    ↓
asyncio.get_event_loop().run_in_executor(None, partial(chunker.chunk, binary, ...))
    ↓
线程池执行: chunker.chunk(binary, ...)
    ↓
返回 chunks 列表
```

##### 3️⃣ chunker.chunk() 方法

**不同的解析器有不同的实现**:

**naive 解析器** ([`../../rag/app/naive.py`](../../rag/app/naive.py)):
```python
def chunk(filename, binary, parser_config, from_page, to_page, callback, ...):
    """通用文档分块
    
    1. 根据文件类型选择加载器
    2. 提取文本内容
    3. 按分隔符切分
    4. 返回 chunks 列表
    """
    # 实现细节...
```

**PDF 解析器** ([`../../rag/app/pdf.py`](../../rag/app/pdf.py)):
```python
def chunk(filename, binary, parser_config, from_page, to_page, callback, ...):
    """PDF 文档分块
    
    1. 使用 PyPDF2/pdfplumber 提取页面内容
    2. 根据 layout_recognize 选择布局识别方式
    3. 按页或按内容分块
    4. 返回 chunks 列表
    """
    # 实现细节...
```

**table 解析器** ([`../../rag/app/table.py`](../../rag/app/table.py)):
```python
def chunk(filename, binary, parser_config, from_page, to_page, callback, ...):
    """表格分块
    
    1. 使用 openpyxl 读取 Excel
    2. 按行数范围提取数据
    3. 返回 chunks 列表
    """
    # 实现细节...
```

##### 4️⃣ chunker.chunk() 返回格式

```python
cks = [
    {
        "content_with_weight": "分词后的内容",
        "content_ltks": "长文本分词结果",
        "content_sm_ltks": "短文本分词结果",
        "images": ["base64_img_1", ...],  # 图片列表（base64 编码）
        "image": "...",                    # 首图（base64 编码）
        "word_count": 150,
        "page_num": [1, 2],               # 页码范围
    },
    ...
]
```

---

### 8️⃣6️⃣ 步骤5: 处理图片 - 并发上传到 MinIO

**文件位置**: [`../../rag/svr/task_executor.py:373-427`](../../rag/svr/task_executor.py:373-427)

```python
    # ========== 步骤5: 构建 chunk 文档对象并上传图片到 MinIO ==========
    docs = []
    
    # 每个 chunk 共享的基础文档信息
    doc = {
        "doc_id": task["doc_id"],
        "kb_id": str(task["kb_id"])
    }
    
    if task.get("pagerank"):
        doc[PAGERANK_FLD] = int(task["pagerank"])
    
    st = timer()

    @timeout(60)
    async def upload_to_minio(document, chunk):
        """将单个 chunk 的图片上传到 MinIO 并生成唯一 ID
        
        为 chunk 生成基于内容的 xxhash ID，处理图片上传逻辑：
        - 若 chunk 已有 img_id，直接添加到结果列表
        - 若 chunk 无图片，设置空 img_id
        - 若 chunk 有图片，调用 image2id 上传并获取图片 ID
        """
        try:
            d = copy.deepcopy(document)
            d.update(chunk)
            
            # 生成 chunk ID（基于内容的哈希）
            d["id"] = xxhash.xxh64(
                (chunk["content_with_weight"] + str(d["doc_id"])).encode("utf-8", "surrogatepass")
            ).hexdigest()
            
            d["create_time"] = str(datetime.now()).replace("T", " ")[:19]
            d["create_timestamp_flt"] = datetime.now().timestamp()

            if d.get("img_id"):
                docs.append(d)
                return

            if not d.get("image"):
                _ = d.pop("image", None)
                d["img_id"] = ""
                docs.append(d)
                return
            
            # 上传图片到 MinIO
            await image2id(
                d, 
                partial(settings.STORAGE_IMPL.put, tenant_id=task["tenant_id"]), 
                d["id"], 
                task["kb_id"]
            )
            docs.append(d)
            
        except Exception:
            logging.exception("Saving image of chunk {}/{}/{} got exception".format(
                task["location"], task["name"], d["id"]
            ))
            raise

    # 并发上传所有 chunks 的图片
    tasks = []
    for ck in cks:
        tasks.append(asyncio.create_task(upload_to_minio(doc, ck)))
    
    try:
        await asyncio.gather(*tasks, return_exceptions=False)
    except Exception as e:
        logging.error(f"MINIO PUT({task['name']}) got exception: {e}")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    el = timer() - st
    logging.info("MINIO PUT({}) cost {:.3f} s".format(task["name"], el))
```

**关键点**:
1. **并发上传**: 使用 `asyncio.create_task()` 并发处理所有 chunks
2. **超时控制**: 每个上传任务有 60 秒超时
3. **图片处理**: 将 base64 图片上传到 MinIO，生成 `img_id`
4. **异常处理**: 失败时取消所有任务并抛出异常

---

### 8️⃣7️⃣ 步骤6: LLM 增强处理（可选）

**文件位置**: [`../../rag/svr/task_executor.py:432-501`](../../rag/svr/task_executor.py:432-501)

```python
    # ========== 步骤6: 可选 - 使用 LLM 自动提取每个 chunk 的关键词 ==========
    if task["parser_config"].get("auto_keywords", 0):
        st = timer()
        progress_callback(msg="Start to generate keywords for every chunk ...")
        
        # 获取 Chat 模型配置
        chat_model_config = get_model_config_by_type_and_name(
            task["tenant_id"], 
            LLMType.CHAT, 
            task["llm_id"]
        )
        chat_mdl = LLMBundle(task["tenant_id"], chat_model_config, lang=task["language"])

        async def doc_keyword_extraction(chat_mdl, d, topn):
            """对单个 chunk 提取关键词，优先使用 LLM 缓存"""
            # 检查缓存
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "keywords", {"topn": topn})
            
            if not cached:
                # 检查任务是否取消
                if has_canceled(task["id"]):
                    progress_callback(-1, msg="Task has been canceled.")
                    return
                
                # 受 chat_limiter 并发控制
                async with chat_limiter:
                    cached = await keyword_extraction(chat_mdl, d["content_with_weight"], topn)
                
                # 设置缓存
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "keywords", {"topn": topn})
            
            if cached:
                d["important_kwd"] = cached.split(",")
                d["important_tks"] = rag_tokenizer.tokenize(" ".join(d["important_kwd"]))
            return

        # 并发处理所有 chunks
        tasks = [doc_keyword_extraction(chat_mdl, d, task["parser_config"]["auto_keywords"]) for d in docs]
        await asyncio.gather(*tasks, return_exceptions=False)
        logging.info("Extract keywords({}) done".format(timer() - st))
    
    # 类似的逻辑用于 auto_questions 和 enable_metadata
    ...
```

**说明**:
1. **auto_keywords**: 自动提取关键词
2. **auto_questions**: 自动生成问题
3. **enable_metadata**: 自动生成元数据
4. **并发处理**: 使用 `asyncio.gather()` 并发处理所有 chunks
5. **缓存机制**: 使用 LLM 缓存避免重复调用
6. **并发控制**: 使用 `chat_limiter` 控制 LLM 调用并发

---

### 8️⃣8️⃣ 返回结果

```python
    return docs  # 处理后的 chunks 列表
```

**docs 数据结构**:

```python
docs = [
    {
        "id": "chunk_id",                      # Chunk ID（xxhash 哈希）
        "doc_id": "doc_id",                    # 文档ID
        "kb_id": "kb_id",                      # 知识库ID
        "content_with_weight": "ltks sm_ltks",  # 带权重的分词结果
        "content_ltks": "长文本分词结果",         # 长文本分词
        "content_sm_ltks": "短文本分词结果",      # 短文本分词
        "img_id": "img_id:xxx",                # 图片ID（如果有）
        "important_kwd": ["关键词1", "关键词2"], # 关键词（如果启用）
        "important_tks": "分词后的关键词",       # 关键词分词
        "create_time": "2026-04-15 14:00:00",   # 创建时间
        "create_timestamp_flt": 1705344000.0,   # 创建时间戳
    },
    ...
]
```

---

## 9️⃣ embedding() 详解

---

## 9️⃣ embedding() 详解

**文件位置**: [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py)

```python
async def embedding(chunks, embedding_model, parser_config, progress_callback):
    """对 chunks 进行向量嵌入
    
    批量处理 chunks，调用嵌入模型生成向量，并附加到 chunk
    
    Args:
        chunks: chunk 列表
        embedding_model: 嵌入模型实例
        parser_config: 解析器配置
        progress_callback: 进度回调函数
    
    Returns:
        tuple: (token_count, vector_size)
    """
```

### 9.1 批量编码

```python
    token_count = 0
    batch_size = 16  # 每批处理 16 个 chunks
    
    # 分批处理
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        
        # 提取文本
        texts = [chunk["content_with_weight"] for chunk in batch]
        
        # 统计 token 数量
        for text in texts:
            token_count += num_tokens_from_string(text)
        
        # 生成向量
        vectors, _ = await embedding_model.encode(texts)
        vector_size = len(vectors[0])
        
        # 将向量附加到 chunk
        for chunk, vector in zip(batch, vectors):
            chunk["q_%d_vec" % vector_size] = vector.tolist()
        
        # 更新进度
        progress_callback(
            msg="Embedding {}/{} chunks".format(i + len(batch), len(chunks))
        )
    
    return token_count, vector_size
```

**关键点**:
1. **批量处理**: 每批 16 个 chunks，提高效率
2. **向量附加**: 向量以 `q_{vector_size}_vec` 为键附加到 chunk
3. **进度更新**: 每批完成后更新进度

**向量字段示例**:
```python
chunk = {
    "id": "chunk_id_1",
    "content": "这是一段文本",
    "q_1024_vec": [0.1, 0.2, ...],  # 1024 维向量
    # 或者
    "q_1536_vec": [0.1, 0.2, ...],  # 1536 维向量
}
```

---

## 🔟 insert_chunks() 详解

**文件位置**: [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py)

```python
async def insert_chunks(task_id, tenant_id, kb_id, chunks, progress_callback):
    """将 chunks 写入文档存储（Elasticsearch/Infinity）
    
    1. 构建插入数据
    2. 批量插入到文档存储
    3. 更新任务进度
    
    Args:
        task_id: 任务ID
        tenant_id: 租户ID
        kb_id: 知识库ID
        chunks: chunk 列表
        progress_callback: 进度回调函数
    
    Returns:
        bool: 是否成功
    """
```

### 10.1 构建插入数据

```python
    # ========== 构建插入数据 ==========
    docs = []
    for chunk in chunks:
        doc = {
            "id": chunk["id"],
            "doc_id": task_id,
            "kb_id": kb_id,
            "content_ltks": chunk.get("content_ltks", ""),
            "content_sm_ltks": chunk.get("content_sm_ltks", ""),
            "content_with_weight": chunk.get("content_with_weight", ""),
        }
        
        # 添加向量字段
        for key in chunk:
            if key.endswith("_vec"):
                doc[key] = chunk[key]
        
        # 添加 LLM 增强字段
        if "keywords_kwd" in chunk:
            doc["keywords_kwd"] = chunk["keywords_kwd"]
        if "questions_kwd" in chunk:
            doc["questions_kwd"] = chunk["questions_kwd"]
        if "metadata_kwd" in chunk:
            doc["metadata_kwd"] = chunk["metadata_kwd"]
        
        docs.append(doc)
```

### 10.2 批量插入

```python
    # ========== 批量插入到文档存储 ==========
    settings.docStoreConn.insert(
        docs,                        # 文档列表
        search.index_name(tenant_id), # 索引名称
        kb_id                        # 知识库ID
    )
```

**docStoreConn 实现**: [`../../rag/nlp/search.py`](../../rag/nlp/search.py)

### 10.3 更新进度

```python
    # ========== 更新任务进度 ==========
    set_progress(task_id, prog=1.0, msg="Finished")
```

---

## 1️⃣1️⃣ DocumentService.update_chunk_num()

**文件位置**: [`../../api/db/services/document_service.py`](../../api/db/services/document_service.py)

```python
@classmethod
def update_chunk_num(cls, doc_id, kb_id, chunk_count, token_count, parser_config, doc_name):
    """更新文档的 chunk 和 token 统计
    
    同时更新：
    1. 文档表的 chunk_num 和 token_num
    2. 知识库表的 chunk_num 和 token_num（累加）
    """
    
    # 更新文档统计
    cls.update_by_id(doc_id, {
        "chunk_num": chunk_count,
        "token_num": token_count,
        "run": TaskStatus.SUCCESS.value,
        "progress": 1.0,
        "progress_msg": "Finished",
    })
    
    # 更新知识库统计
    Knowledgebase.update(
        chunk_num=Knowledgebase.chunk_num + chunk_count,
        token_num=Knowledgebase.token_num + token_count,
    ).where(Knowledgebase.id == kb_id).execute()
```

---

## 📊 完整流程图

```
do_handle_task(task)
    │
    ├─→ 【前置处理】
    │   │
    │   ├─→ 提取任务参数
    │   │     ├─ task_id, task_from_page, task_to_page
    │   │     ├─ task_tenant_id, task_embedding_id, task_language
    │   │     ├─ task_parser_config
    │   │     └─ progress_callback = partial(set_progress, task_id, from_page, to_page)
    │   │
    │   ├─→ has_canceled(task_id)
    │   │     └─ 检查 Redis: {task_id}-cancel
    │   │
    │   ├─→ 绑定嵌入模型
    │   │     ├─ get_model_config_by_type_and_name(tenant_id, EMBEDDING, embd_id)
    │   │     ├─ LLMBundle(tenant_id, config, lang)
    │   │     └─ embedding_model.encode(["ok"]) → 验证模型
    │   │
    │   └─→ init_kb(task, vector_size)
    │         └─ docStoreConn.create_index(index_name, kb_id, vector_size)
    │
    └─→ 【标准文档解析流程】
        │
        ├─→ build_chunks(task, progress_callback)
        │     │
        │     ├─→ 【步骤1】检查文件大小
        │     │     └─ if task["size"] > settings.DOC_MAXIMUM_SIZE: return []
        │     │
        │     ├─→ 【步骤2】选择解析器
        │     │     └─ chunker = FACTORY[task["parser_id"].lower()]
        │     │           └─ naive / paper / book / table / picture / ...
        │     │
        │     ├─→ 【步骤3】从 MinIO 获取文件
        │     │     ├─ bucket, name = File2DocumentService.get_storage_address(doc_id)
        │     │     └─ binary = await get_storage_binary(bucket, name)
        │     │           └─ STORAGE_IMPL.get(bucket, name) → 返回文件二进制数据
        │     │
        │     ├─→ 【步骤4】在线程池中执行 chunker.chunk ⬅️ 核心步骤
        │     │     │
        │     │     ├─ async with chunk_limiter:  # 并发控制
        │     │     │
        │     │     └─ cks = await thread_pool_exec(
        │     │               chunker.chunk,        # 解析器的 chunk 方法（CPU 密集型）
        │     │               task["name"],         # 文件名
        │     │               binary=binary,        # 文件二进制数据 ⬅️ 从 MinIO 获取
        │     │               from_page=...,        # 起始页
        │     │               to_page=...,          # 结束页
        │     │               lang=...,             # 语言
        │     │               callback=...,         # 进度回调
        │     │               kb_id=...,            # 知识库ID
        │     │               parser_config=...,    # 解析器配置
        │     │               tenant_id=...         # 租户ID
        │     │           )
        │     │         └─ 返回 chunks 列表
        │     │
        │     ├─→ 【步骤5】处理图片 - 并发上传到 MinIO
        │     │     └─ for ck in cks:
        │     │           └─ asyncio.create_task(upload_to_minio(doc, ck))
        │     │               └─ image2id() → 上传图片 → 生成 img_id
        │     │
        │     └─→ 【步骤6】LLM 增强处理（可选）
        │           ├─ auto_keywords → keyword_extraction()
        │           ├─ auto_questions → question_proposal()
        │           └─ enable_metadata → gen_metadata()
        │
        ├─→ embedding(chunks, embedding_model, parser_config, progress_callback)
        │     │
        │     └─→ 批量编码（每批 16 个 chunks）
        │           ├─ texts = [chunk["content_with_weight"] for chunk in batch]
        │           ├─ vectors = embedding_model.encode(texts)
        │           └─ chunk["q_{vector_size}_vec"] = vector.tolist()
        │
        ├─→ build_TOC(task, chunks, progress_callback)  # 可选，后台线程
        │     └─ run_toc_from_text() → TOC
        │
        └─→ insert_chunks(task_id, tenant_id, kb_id, chunks, progress_callback)
              │
              ├─→ 构建插入数据
              │     └─ docs = [{id, doc_id, kb_id, content_ltks, content_sm_ltks, q_1024_vec, ...}]
              │
              ├─→ 批量插入
              │     └─ docStoreConn.insert(docs, index_name, kb_id)
              │
              ├─→ set_progress(task_id, prog=1.0, msg="Finished")
              │
              └─→ DocumentService.update_chunk_num(doc_id, kb_id, chunk_count, token_count)
                    ├─ 更新文档: chunk_num, token_num, run=SUCCESS
                    └─ 更新知识库: chunk_num += chunk_count, token_num += token_count
```

---

## 🔑 核心要点总结

### build_chunks 的关键执行流程

1. **从 MinIO 获取文件** 
   - 使用 `File2DocumentService.get_storage_address()` 获取存储地址
   - 使用 `get_storage_binary()` 从 MinIO 获取文件二进制数据
   - 在线程池中执行，避免阻塞事件循环

2. **选择对应的解析器**
   - 根据 `parser_id` 从 `FACTORY` 映射表获取解析器
   - 支持多种解析器：naive、paper、book、table、picture 等

3. **在线程池中执行 chunker.chunk**
   - 使用 `async with chunk_limiter:` 控制并发
   - 使用 `await thread_pool_exec(chunker.chunk, binary=binary, ...)` 执行
   - 将从 MinIO 获取的 `binary` 数据传递给解析器
   - 解析器在独立的线程中执行，不阻塞事件循环

4. **处理图片**
   - 并发上传所有 chunks 的图片到 MinIO
   - 使用 `asyncio.create_task()` 并发处理
   - 生成 `img_id` 并替换原始 base64 图片

5. **LLM 增强处理（可选）**
   - 支持自动提取关键词、生成问题、生成元数据
   - 使用 LLM 缓存避免重复调用
   - 受 `chat_limiter` 并发控制

### 为什么使用线程池执行 chunker.chunk？

1. **CPU 密集型任务**: 文档解析（特别是 PDF 解析）需要大量 CPU 计算
2. **避免阻塞事件循环**: 在线程池中执行可以避免阻塞 asyncio 事件循环
3. **提高并发能力**: 允许同时处理多个文档解析任务
4. **资源控制**: 通过 `chunk_limiter` 信号量控制并发数量

---

## 🔑 关键数据结构

### Task 数据结构

```python
task = {
    "id": "task_id",                    # 任务ID
    "doc_id": "doc_id",                # 文档ID
    "kb_id": "kb_id",                  # 知识库ID
    "tenant_id": "tenant_id",          # 租户ID
    "name": "document.pdf",            # 文档名称
    "type": "pdf",                     # 文档类型
    "parser_id": "naive",              # 解析器类型
    "parser_config": {                 # 解析器配置
        "chunk_token_num": 512,
        "delimiter": "/n",
        "layout_recognize": "DeepDOC",
        "auto_keywords": 1,
        "auto_questions": 0,
        "enable_metadata": False,
        "toc_extraction": False,
    },
    "kb_parser_config": {              # 知识库级解析器配置
        "llm_id": "llm_id",
    },
    "embd_id": "embd_id",              # 嵌入模型ID
    "language": "English",             # 语言
    "llm_id": "llm_id",                # LLM 模型ID
    "from_page": 0,                    # 起始页
    "to_page": 100000000,              # 结束页
    "task_type": "",                   # 任务类型（空字符串表示标准解析）
}
```

### Chunk 数据结构

```python
chunk = {
    "id": "chunk_id",                      # Chunk ID
    "content": "原始内容",                   # 原始内容
    "content_with_weight": "ltks sm_ltks",  # 带权重的分词结果
    "content_ltks": "长文本分词结果",         # 长文本分词
    "content_sm_ltks": "短文本分词结果",      # 短文本分词
    "images": ["base64_img_1", ...],      # 图片列表（base64）
    "word_count": 150,                    # 字数
    "page_num": [1, 2],                   # 页码范围
    
    # LLM 增强字段（可选）
    "keywords_kwd": ["关键词1", "关键词2"],  # 关键词
    "questions_kwd": ["问题1", "问题2"],     # 问题
    "metadata_kwd": {"key": "value"},       # 元数据
    
    # 向量字段（embedding 后添加）
    "q_1024_vec": [0.1, 0.2, ...],         # 1024 维向量
    # 或
    "q_1536_vec": [0.1, 0.2, ...],         # 1536 维向量
}
```

### 文档存储数据结构

```python
doc_store_doc = {
    "id": "chunk_id",                      # Chunk ID
    "doc_id": "doc_id",                    # 文档ID
    "kb_id": "kb_id",                      # 知识库ID
    "content_ltks": "长文本分词结果",         # 长文本分词
    "content_sm_ltks": "短文本分词结果",      # 短文本分词
    "content_with_weight": "ltks sm_ltks",  # 带权重的分词结果
    
    # 向量字段
    "q_1024_vec": [0.1, 0.2, ...],         # 向量
    
    # LLM 增强字段
    "keywords_kwd": ["关键词1", "关键词2"],
    "questions_kwd": ["问题1", "问题2"],
    "metadata_kwd": {"key": "value"},
}
```

---

## 📝 重要说明

### 1. 超时保护
- `do_handle_task()`: 3小时超时
- `build_chunks()`: 80分钟超时
- 超时后任务会被标记为失败

### 2. 取消机制
- 任务执行过程中会定期检查取消状态
- 取消标志存储在 Redis: `{task_id}-cancel`
- 取消后会自动清理已写入的数据

### 3. 并发控制

#### chunk_limiter（文档解析并发控制）
```python
async with chunk_limiter:
    cks = await thread_pool_exec(chunker.chunk, binary, ...)
```
- 限制同时进行的文档解析任务数量
- 默认值由 `settings.CHUNK_CONCURRENCY` 控制（通常为 4-8）
- 防止 CPU 资源耗尽

#### chat_limiter（LLM 调用并发控制）
```python
async with chat_limiter:
    cached = await keyword_extraction(chat_mdl, d["content_with_weight"], topn)
```
- 限制同时进行的 LLM 调用数量
- 防止 LLM API 限流或过载

#### kg_limiter（知识图谱并发控制）
```python
async with kg_limiter:
    chunks, token_count = await run_raptor_for_kb(...)
```
- 限制 GraphRAG/RAPTOR 等知识图谱任务的并发
- 这些任务通常消耗大量资源

### 4. 线程池执行

**为什么要使用线程池？**

文档解析（特别是 PDF 解析）是 **CPU 密集型任务**，如果直接在 asyncio 事件循环中执行会阻塞整个事件循环，导致其他任务无法执行。

**解决方案**：使用 `thread_pool_exec()` 在线程池中执行

```python
# 在线程池中执行 CPU 密集型任务
cks = await thread_pool_exec(
    chunker.chunk,      # 同步函数
    binary=binary,      # 文件二进制数据
    ...
)
```

**工作原理**：
```
asyncio 事件循环
    ↓
await thread_pool_exec(chunker.chunk, binary, ...)
    ↓
提交到线程池执行器（ThreadPoolExecutor）
    ↓
线程池线程: chunker.chunk(binary, ...)
    ↓
返回结果到 asyncio 事件循环
```

### 5. 批量处理
- **Embedding**: 每批 16 个 chunks，提高效率
- **Insert**: 全部 chunks 一次性插入
- 批量处理减少网络开销

### 6. 异步执行
- `build_TOC()` 在后台线程中异步执行
- 图片上传使用 `asyncio.create_task()` 并发处理
- 不阻塞主流程

### 7. 错误处理
- 每个步骤都有异常处理
- 失败后设置进度为 -1
- 记录错误信息到 `progress_msg`

### 8. 进度更新
- 通过 `progress_callback` 更新进度
- 进度范围: 0.0 ~ 1.0
- -1 表示错误状态

### 9. 缓存机制
- LLM 调用结果会被缓存
- 使用 `get_llm_cache()` 和 `set_llm_cache()` 管理缓存
- 避免重复调用 LLM，节省成本和时间

### 10. 图片处理
- chunk 中的图片以 base64 编码存储
- 上传到 MinIO 后生成 `img_id`
- 原始 base64 图片被 `img_id` 替换

---

## 🔗 相关文件清单

| 文件 | 说明 |
|-----|------|
| [`../../rag/svr/task_executor.py:1187`](../../rag/svr/task_executor.py) | `do_handle_task()` 主函数 |
| [`../../rag/svr/task_executor.py:304`](../../rag/svr/task_executor.py) | `build_chunks()` 函数 |
| [`../../rag/svr/task_executor.py:298`](../../rag/svr/task_executor.py) | `get_storage_binary()` 函数 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | `embedding()` 函数 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | `insert_chunks()` 函数 |
| [`../../rag/svr/task_executor.py`](../../rag/svr/task_executor.py) | `build_TOC()` 函数 |
| [`../../rag/app/`](../../rag/app/) | 各种解析器实现 |
| [`../../rag/utils/base64_image.py`](../../rag/utils/base64_image.py) | `image2id()` 函数 |
| [`../../rag/prompts/generator.py`](../../rag/prompts/generator.py) | LLM 增强函数 |
| [`../../rag/nlp/search.py`](../../rag/nlp/search.py) | 文档存储接口 |
| [`../../api/db/services/document_service.py`](../../api/db/services/document_service.py) | `DocumentService` |
| [`../../api/db/services/task_service.py:517`](../../api/db/services/task_service.py) | `has_canceled()` 函数 |

---

**更新时间**: 2026-04-15  
**文档版本**: 1.0
