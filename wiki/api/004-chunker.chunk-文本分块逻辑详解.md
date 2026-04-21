# chunker.chunk 文本分块逻辑详解

## 📋 文档概述

本文档深入分析 RAGFlow 文档解析核心链路中的文本分块逻辑，聚焦于 `chunker.chunk()` 方法如何将原始文档转换为可供向量检索的文本块。

**核心文件位置**：
- 主入口：[`../../rag/app/naive.py:794-1175`](../../rag/app/naive.py:794-1175)
- 分块算法：[`../../rag/nlp/__init__.py:1070-1127`](../../rag/nlp/__init__.py:1070-1127)
- 分词包装：[`../../rag/nlp/__init__.py:302-327`](../../rag/nlp/__init__.py:302-327)

**调用位置**：
```
D:/Project/ragflow/rag/svr/task_executor.py:461
cks = await thread_pool_exec(chunker.chunk, ...)
```

---

## 🎯 核心流程概览

```
原始文档 → parser解析 → naive_merge分块 → tokenize_chunks包装 → 最终chunks
   ↓           ↓              ↓                  ↓              ↓
 PDF/Docx   sections[]    chunks[]           es_docs[]     cks[]
```

**三个核心阶段**：
1. **解析阶段**：将不同格式文档统一为文本段落列表（sections）
2. **分块阶段**：基于token限制将段落智能合并为文本块（chunks）
3. **包装阶段**：为每个文本块添加分词和权重信息（es_docs）

---

## 1️⃣ 入口函数：chunk()

**文件位置**：[`../../rag/app/naive.py:794`](../../rag/app/naive.py)

### 函数签名
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, 
          lang="Chinese", callback=None, **kwargs):
```

### 核心参数
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `filename` | 文件名（用于格式检测） | - |
| `binary` | 文件二进制数据（从MinIO获取） | None |
| `from_page` | 起始页码 | 0 |
| `to_page` | 结束页码 | 100000 |
| `lang` | 语言（影响分词器选择） | "Chinese" |
| `callback` | 进度回调函数 | None |
| `kb_id` | 知识库ID（从kwargs获取） | - |
| `parser_config` | 解析器配置（从kwargs获取） | - |

### 执行流程

#### 步骤1：初始化解析器
```python
# D:/Project/ragflow/rag/app/naive.py:805
chunker = get_chunker(
    kwargs.get("parser_config", {}),
    kwargs.get("tenant_id", DEFAULT_ID),
    lang=lang
)
```

#### 步骤2：根据文件类型选择解析路径
```python
# D:/Project/ragflow/rag/app/naive.py:817-900
if filename.endswith(".docx"):
    sections, tables = parse_docx(binary, from_page, to_page)
    
elif filename.endswith(".pdf"):
    sections, tables = parse_pdf(
        filename, binary, from_page, to_page, 
        chunker.ocr_enabled, chunker.layout_recognize_method
    )
    
elif filename.endswith(".xlsx") or filename.endswith(".xls"):
    sections = parse_excel(binary)
    
elif filename.endswith(".csv"):
    sections = parse_csv(binary)
    
else:
    # txt, md, html, epub, json 等纯文本格式
    sections = parse_text(filename, binary, from_page, to_page)
```

**关键点**：
- 所有格式最终都转换为 `sections: List[str]` 格式
- `sections` 是按顺序排列的文本段落列表
- PDF/Docx 等格式还会提取 `tables`（表格数据）

#### 步骤3：文本分块（核心算法）
```python
# D:/Project/ragflow/rag/app/naive.py:905
if not sections:
    return []

chunks = naive_merge(
    sections,
    chunk_token_num=chunker.chunk_token_num,
    delimiter=chunker.delimiter,
    overlapped_percent=chunker.overlapped_percent
)
```

**参数说明**：
- `chunk_token_num=512`：单个文本块的token上限（默认值）
- `delimiter="/n。；！？"`：分段分隔符（中英文标点）
- `overlapped_percent=0`：重叠百分比（用于保留上下文）

#### 步骤4：分词包装
```python
# D:/Project/ragflow/rag/app/naive.py:912-928
res = []
doc = {
    "docnm_kwd": filename,
    "title_kwd": filename
}

res.extend(tokenize_chunks(
    chunks, 
    doc, 
    lang == "English",
    callback=callback
))

return res
```

---

## 2️⃣ 核心算法：naive_merge()

**文件位置**：[`../../rag/nlp/__init__.py:1070`](../../rag/nlp/__init__.py)

这是整个分块逻辑的核心，负责将长文本按token限制智能切分。

### 函数签名
```python
def naive_merge(sections: str | list, chunk_token_num=128, 
                delimiter="/n。；！？", overlapped_percent=0):
```

### 算法流程图
```
输入: sections = ["段落1", "段落2", "段落3", ...]
              ↓
    ┌─────────────────┐
    │ 按分隔符切分段落  │
    └─────────────────┘
              ↓
    segments = ["句1", "句2", "句3", ...]
              ↓
    ┌─────────────────┐
    │ 逐段合并segments │
    │ 检查token数量    │
    └─────────────────┘
              ↓
    chunk_token_num <= 128?
         ↓ 是          ↓ 否
    完成当前块      添加到当前块
         ↓              ↓
    添加重叠内容  继续合并
         ↓              ↓
    保存chunk ←────────┘
              ↓
输出: chunks = ["chunk1", "chunk2", ...]
```

### 详细实现

#### 步骤1：处理输入和初始化
```python
# D:/Project/ragflow/rag/nlp/__init__.py:1078-1084
if isinstance(sections, str):
    sections = [sections]

# 创建正则表达式匹配分隔符
pat = re.compile(r"([{}])".format(re.escape(delimiter)))
```

#### 步骤2：分段切分
```python
# D:/Project/ragflow/rag/nlp/__init__.py:1087-1095
sections = [pat.sub(r"/1/0", s).split("/0") for s in sections]
# 结果: [["句1", "句2", "/n"], ["句3", "句4", "。"], ...]

# 展平为一级列表
sections = [s for sec in sections for s in sec if s]
# 结果: ["句1", "句2", "/n", "句3", "句4", "。", ...]
```

**关键点**：
- 分隔符（如 `/n`、`。`）本身会被保留为独立元素
- 这确保了文本块的边界是完整的句子

#### 步骤3：智能合并
```python
# D:/Project/ragflow/rag/nlp/__init__.py:1098-1118
chunks = []
chunk = ""
overlap = ""

for sec in sections:
    # 检查是否超出token限制
    if num_tokens_from_string(chunk + sec) > chunk_token_num:
        if chunk:
            chunks.append(chunk)
            # 计算重叠内容
            overlap = calculate_overlap(chunk, overlapped_percent)
            chunk = overlap
    chunk += sec

# 添加最后一个块
if chunk:
    chunks.append(chunk)
```

**重叠计算逻辑**：
```python
# D:/Project/ragflow/rag/nlp/__init__.py:1106
if overlapped_percent > 0:
    tokens = chunk.split()
    overlap_len = int(len(tokens) * overlapped_percent)
    overlap = " ".join(tokens[-overlap_len:]) if overlap_len > 0 else ""
```

**示例**：
```
原文: "这是一段很长的文本..." (假设200 tokens)
chunk_token_num: 128
overlapped_percent: 0.2

Chunk 1: "这是一段很长的文本..." (128 tokens)
Chunk 2: "...文本的末尾20%作为上下文" + "新内容" (128 tokens)
```

#### 步骤4：返回结果
```python
# D:/Project/ragflow/rag/nlp/__init__.py:1120-1127
return chunks
```

---

## 3️⃣ 分词包装：tokenize_chunks()

**文件位置**：[`../../rag/nlp/__init__.py:302`](../../rag/nlp/__init__.py)

### 函数签名
```python
def tokenize_chunks(chunks, doc, eng=False, pdf_parser=None, 
                   child_delimiters_pattern=None):
```

### 功能说明
将纯文本块包装为 Elasticsearch 文档，添加分词和权重信息。

### 输出结构
```python
{
    "docnm_kwd": "文件名.pdf",
    "title_kwd": "文件名.pdf",
    "content_with_weight": "这是一段文本...",
    "content_ltks": "这 是 一 段 文 本 ...",
    "content_sm_ltks": "这 段 文 本 ...",
    "knowledge_graph_kwd": ["实体1", "实体2"]
}
```

### 字段说明
| 字段 | 说明 | 示例 |
|------|------|------|
| `docnm_kwd` | 文档名称（keyword类型） | "manual.pdf" |
| `title_kwd` | 标题（keyword类型） | "用户手册" |
| `content_with_weight` | 原始内容（带权重） | "重要说明..." |
| `content_ltks` | 长token分词（用于检索） | "重 要 说 明" |
| `content_sm_ltks` | 短token分词（用于高精度） | "说明" |
| `knowledge_graph_kwd` | 知识图谱实体 | ["概念A", "概念B"] |

### 实现逻辑

#### 步骤1：遍历文本块
```python
# D:/Project/ragflow/rag/nlp/__init__.py:309-327
res = []
for ii, ck in enumerate(chunks):
    d = copy.deepcopy(doc)
    tokenize(d, ck, eng)  # 添加分词字段
    res.append(d)
return res
```

#### 步骤2：分词处理
```python
# D:/Project/ragflow/rag/nlp/__init__.py:313
def tokenize(doc, text, eng):
    doc["content_with_weight"] = text
    
    if eng:
        # 英文分词：按空格
        tokens = text.split()
        doc["content_ltks"] = " ".join(tokens)
        doc["content_sm_ltks"] = " ".join([t for t in tokens if len(t) > 1])
    else:
        # 中文分词：使用jieba
        import jieba
        tokens = list(jieba.cut(text))
        doc["content_ltks"] = " ".join(tokens)
        doc["content_sm_ltks"] = " ".join([t for t in tokens if len(t) > 1])
```

---

## 4️⃣ 完整调用链示例

以PDF文档为例的完整流程：

```
1. 用户上传PDF (manual.pdf)
   ↓
2. MinIO存储文件，返回binary
   ↓
3. task_executor.py:461
   cks = await thread_pool_exec(chunker.chunk, "manual.pdf", binary=binary)
   ↓
4. naive.py:794 chunk()函数
   sections, tables = parse_pdf("manual.pdf", binary)
   ↓
5. naive.py:817 解析PDF
   使用PyMuPDF提取文本，按页面分组
   sections = ["第1页内容...", "第2页内容...", ...]
   ↓
6. naive.py:905 调用naive_merge
   chunks = naive_merge(sections, chunk_token_num=512)
   ↓
7. nlp/__init__.py:1070 分块算法
   - 按分隔符切分：segments = ["句1", "句2", ...]
   - 合并segments直到token数接近512
   - 添加重叠内容（overlapped_percent=0.2）
   chunks = ["chunk1 (512t)", "chunk2 (512t)", ...]
   ↓
8. naive.py:912 调用tokenize_chunks
   res = tokenize_chunks(chunks, doc)
   ↓
9. nlp/__init__.py:302 包装分词
   for chunk in chunks:
       es_doc = {
           "docnm_kwd": "manual.pdf",
           "content_with_weight": chunk,
           "content_ltks": jieba.cut(chunk),
           ...
       }
       res.append(es_doc)
   ↓
10. 返回最终结果
    cks = [
        {"docnm_kwd": "manual.pdf", "content_ltks": "...", ...},
        {"docnm_kwd": "manual.pdf", "content_ltks": "...", ...},
        ...
    ]
```

---

## 5️⃣ 关键参数调优指南

### chunk_token_num（默认512）
- **作用**：控制单个文本块的大小
- **影响**：
  - 太小（<128）：上下文碎片化，检索质量下降
  - 太大（>1024）：检索精度降低，计算成本增加
- **推荐值**：
  - 中文文档：512-1024
  - 英文文档：256-512
  - 代码文档：128-256

### delimiter（默认"/n。；！？")
- **作用**：定义句子边界
- **影响**：
  - 过于宽松（如","）：切分过细，语义不完整
  - 过于严格（如"/n/n"）：块过大，可能超出token限制
- **推荐配置**：
  ```python
  中文: "/n。；！？"
  英文: "/n.!?"
  混合: "/n。；！？.!?"
  ```

### overlapped_percent（默认0）
- **作用**：相邻块之间的重叠比例
- **影响**：
  - 0%：无重叠，节省存储
  - 10%-20%：保留上下文，提高检索召回率
  - >30%：冗余度高，存储成本增加
- **推荐值**：0.1-0.2（10%-20%）

---

## 6️⃣ 性能优化要点

### 1. Token计算优化
```python
# 当前实现：每次都重新计算
if num_tokens_from_string(chunk + sec) > chunk_token_num:
    # ...

# 优化建议：缓存计算结果
token_cache = {}
def cached_token_count(text):
    if text not in token_cache:
        token_cache[text] = num_tokens_from_string(text)
    return token_cache[text]
```

### 2. 分词器选择
```python
# 中文场景
import jieba
jieba.set_dictionary("custom_dict.txt")  # 使用领域词典

# 英文场景
import nltk
nltk.download('punkt')  # 下载punkt分词模型
```

### 3. 并行处理
```python
# 对于大文档，可以分段并行处理
from concurrent.futures import ThreadPoolExecutor

def parallel_chunk(sections, chunk_token_num):
    with ThreadPoolExecutor(max_workers=4) as executor:
        # 将sections分成4组
        groups = [sections[i::4] for i in range(4)]
        futures = [executor.submit(naive_merge, g, chunk_token_num) 
                   for g in groups]
        results = [f.result() for f in futures]
    return merge_results(results)
```

---

## 7️⃣ 常见问题排查

### 问题1：文本块为空
**原因**：
- 文档解析失败，sections为空
- 分隔符设置不当，导致所有文本被过滤

**排查**：
```python
# 在naive.py:905前添加日志
logger.info(f"Parsed {len(sections)} sections")
logger.info(f"First section: {sections[0] if sections else 'None'}")
```

### 问题2：文本块过大
**原因**：
- `chunk_token_num` 设置过大
- 分隔符未匹配，导致整个段落作为一个块

**排查**：
```python
# 在naive_merge中添加断言
assert len(chunk) <= chunk_token_num * 1.2, f"Chunk too large: {len(chunk)}"
```

### 问题3：分词结果不准确
**原因**：
- 语言检测错误，使用了错误的分词器
- jieba词典不包含领域词汇

**解决**：
```python
# 添加自定义词典
jieba.load_userdict("domain_words.txt")

# 强制指定语言
doc = {"lang": "zh"}  # 或 "en"
```

---

## 8️⃣ 总结

`chunker.chunk` 的文本分块逻辑是RAGFlow文档处理的核心环节，其设计特点：

1. **格式无关性**：支持PDF、Docx、TXT等多种格式，统一处理为文本块
2. **智能分块**：基于token限制和语义边界（分隔符）的智能切分
3. **上下文保留**：通过重叠机制保持文本块的语义连贯性
4. **检索优化**：多种分词粒度（ltks/sm_ltks）支持不同检索场景

理解这一流程有助于：
- 优化检索质量：调整chunk_token_num和delimiter
- 提升处理性能：识别性能瓶颈并优化
- 定制化需求：针对特定领域调整分块策略

**相关文档**：
- [002-文档解析接口-run.md](./002-文档解析接口-run.md) - API调用流程
- [003-do_handle_task-标准文档解析流程.md](./003-do_handle_task-标准文档解析流程.md) - 完整任务执行流程
