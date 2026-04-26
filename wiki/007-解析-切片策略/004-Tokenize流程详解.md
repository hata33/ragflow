# Tokenize 流程详解

## 概述

Tokenize 是文档处理的最后一步，将 chunks 转换为统一的 Elasticsearch 文档格式，包括分词、位置提取等操作。

## 最终数据结构

所有格式经过 tokenize 后，都会转换为统一的结构：

```python
{
    "content_with_weight": "原始文本内容",
    "content_ltks": ["token1", "token2", ...],      # 粗粒度分词
    "content_sm_ltks": ["tok", "en", ...],          # 细粒度分词
    "docnm_kwd": "文件名.pdf",
    "title_tks": ["文件名", ...],                   # 标题分词
    "title_sm_tks": ["文", "件", "名", ...],        # 标题细粒度分词
    "page_num_int": [1, 2],                        # 页码列表（可选）
    "position_int": [[0, 100, 0, 50, 1]],          # 位置列表（可选）
    "top_int": [0, 50],                            # 顶部位置（可选）
    "image": <PIL.Image>,                          # 图片（可选）
    "doc_type_kwd": "table" | "image",             # 文档类型（可选）
    "mom_with_weight": "原始文本"                   # 使用子分隔符时（可选）
}
```

## 三个 Tokenize 函数

| 函数 | 输入 chunks | 适用场景 | 特殊处理 |
|------|------------|---------|---------|
| `tokenize_chunks()` | `["chunk1", "chunk2", ...]` | 纯文本 | 提取位置和图片 |
| `tokenize_chunks_with_images()` | `chunks` + `images` | 带图片 | 附加图片信息 |
| `doc_tokenize_chunks_with_images()` | `[{"text":..., ...}, ...]` | DOCX | 处理三种类型 |

## 1. tokenize_chunks() - 纯文本分词

### 函数签名

```python
def tokenize_chunks(
    chunks,              # ["chunk1", "chunk2", ...]
    doc,                 # 文档元数据模板
    eng,                 # 是否为英文
    pdf_parser=None,     # PDF 解析器（可选）
    child_delimiters_pattern=None
) -> list:
```

### 核心逻辑

```python
res = []
for ii, ck in enumerate(chunks):
    if len(ck.strip()) == 0:
        continue

    d = copy.deepcopy(doc)

    # 1. 如果有 PDF 解析器，提取图片和位置
    if pdf_parser:
        try:
            d["image"], poss = pdf_parser.crop(ck, need_position=True)
            add_positions(d, poss)
            ck = pdf_parser.remove_tag(ck)  # 移除位置标签
        except NotImplementedError:
            pass
    else:
        add_positions(d, [[ii] * 5])

    # 2. 如果有子分隔符，使用自定义分割
    if child_delimiters_pattern:
        d["mom_with_weight"] = ck
        res.extend(split_with_pattern(d, child_delimiters_pattern, ck, eng))
        continue

    # 3. 标准分词流程
    tokenize(d, ck, eng)
    res.append(d)

return res
```

### 位置提取

```python
# 从位置标签中提取位置信息
def add_positions(d, poss):
    d["page_num_int"] = [p[0] for p in poss]
    d["position_int"] = [[p[1], p[2], p[3], p[4], p[0]] for p in poss]
    d["top_int"] = [p[3] for p in poss]

# poss 格式: [[页码, 左, 右, 上, 下], ...]
# 例如: [[1, 0, 100, 0, 50], [1, 0, 100, 50, 100]]
```

### 代码位置

`rag/nlp/__init__.py` 第 612-679 行

## 2. tokenize_chunks_with_images() - 带图片分词

### 函数签名

```python
def tokenize_chunks_with_images(
    chunks,              # ["chunk1", "chunk2", ...]
    doc,                 # 文档元数据模板
    eng,                 # 是否为英文
    images,              # [img1, img2, ...]
    child_delimiters_pattern=None
) -> list:
```

### 核心逻辑

```python
res = []
for ii, (ck, image) in enumerate(zip(chunks, images)):
    if len(ck.strip()) == 0:
        continue

    d = copy.deepcopy(doc)
    d["image"] = image  # 附加图片信息
    add_positions(d, [[ii] * 5])

    if child_delimiters_pattern:
        d["mom_with_weight"] = ck
        res.extend(split_with_pattern(d, child_delimiters_pattern, ck, eng))
        continue

    tokenize(d, ck, eng)
    res.append(d)

return res
```

### 代码位置

`rag/nlp/__init__.py` 第 743-786 行

## 3. doc_tokenize_chunks_with_images() - DOCX 专用分词

### 函数签名

```python
def doc_tokenize_chunks_with_images(
    chunks,              # [{"text":..., "image":..., "ck_type":...}, ...]
    doc,                 # 文档元数据模板
    eng,                 # 是否为英文
    child_delimiters_pattern=None,
    batch_size=10
) -> list:
```

### 核心逻辑

```python
res = []
for ii, ck in enumerate(chunks):
    # 1. 合并上下文信息
    text = ck.get("context_above", "") + ck.get("text") + ck.get("context_below", "")
    if len(text.strip()) == 0:
        continue

    d = copy.deepcopy(doc)

    # 2. 处理图片
    if ck.get("image"):
        d["image"] = ck.get("image")
    add_positions(d, [[ii] * 5])

    # 3. 根据块类型设置处理方式
    if ck.get("ck_type") == "text":
        if child_delimiters_pattern:
            d["mom_with_weight"] = text
            res.extend(split_with_pattern(d, child_delimiters_pattern, text, eng))
            continue
    elif ck.get("ck_type") == "image":
        d["doc_type_kwd"] = "image"
    elif ck.get("ck_type") == "table":
        d["doc_type_kwd"] = "table"

    # 4. 分词
    tokenize(d, text, eng)
    res.append(d)

return res
```

### 代码位置

`rag/nlp/__init__.py` 第 682-740 行

## 核心分词函数

### tokenize()

```python
def tokenize(d, txt, eng):
    """
    为文本添加分词和权重信息
    """
    from . import rag_tokenizer

    d["content_with_weight"] = txt

    # 移除表格标签，避免影响分词
    t = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", txt)

    # 粗粒度分词（用于检索）
    d["content_ltks"] = rag_tokenizer.tokenize(t)

    # 细粒度分词（用于高精度匹配）
    d["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(d["content_ltks"])
```

### 代码位置

`rag/nlp/__init__.py` 第 521-554 行

## 子分隔符处理

### split_with_pattern()

```python
def split_with_pattern(d, pattern: str, content: str, eng) -> list:
    """
    使用正则表达式模式分割文本并分词
    """
    # 验证并编译正则表达式模式
    try:
        compiled_pattern = re.compile(r"(%s)" % pattern, flags=re.DOTALL)
    except re.error as e:
        logging.warning(f"Invalid delimiter regex pattern '{pattern}': {e}")
        dd = copy.deepcopy(d)
        tokenize(dd, content, eng)
        return [dd]

    # 分割文本（保留分隔符）
    txts = [txt for txt in compiled_pattern.split(content)]
    docs = []
    for j in range(0, len(txts), 2):
        txt = txts[j]
        if not txt:
            continue
        # 将分隔符附加到文本块
        if j + 1 < len(txts):
            txt += txts[j + 1]
        dd = copy.deepcopy(d)
        tokenize(dd, txt, eng)
        docs.append(dd)

    return docs
```

### 代码位置

`rag/nlp/__init__.py` 第 557-609 行

## 完整流程示例

### PDF 文档处理流程

```python
# 1. 解析
sections, tables, pdf_parser = by_deepdoc(filename, binary)
# sections = [("段落", "@@1\t0.0\t100.0\t0.0\t50.0##"), ...]

# 2. 分块
chunks = naive_merge(sections, chunk_token_num=128)
# chunks = ["段落1\n段落2", "段落3", ...]

# 3. 分词
res = tokenize_chunks(chunks, doc, is_english, pdf_parser)
# res = [{
#     "content_with_weight": "段落1\n段落2",
#     "content_ltks": [...],
#     "content_sm_ltks": [...],
#     "page_num_int": [1],
#     "position_int": [[0, 100, 0, 50, 1]],
#     "image": <PIL.Image>,
# }, ...]
```

### DOCX 文档处理流程

```python
# 1. 解析
sections = Docx()(filename, binary)
# sections = [("文本", None, None), ("", img, None), ...]

# 2. 分块
chunks, images = naive_merge_docx(sections, ...)
# chunks = [{"text": "文本", "image": None, "ck_type": "text", ...}, ...]

# 3. 分词
res = doc_tokenize_chunks_with_images(chunks, doc, is_english)
# res = [{
#     "content_with_weight": "上下文+文本+上下文",
#     "content_ltks": [...],
#     "content_sm_ltks": [...],
#     "doc_type_kwd": "text",
# }, ...]
```

## 分词器

RAGFlow 使用自定义的分词器 `rag_tokenizer`：

```python
# 粗粒度分词
tokens = rag_tokenizer.tokenize("这是一个测试句子")
# ["这是", "一个", "测试", "句子"]

# 细粒度分词
fine_tokens = rag_tokenizer.fine_grained_tokenize(tokens)
# ["这", "是", "一", "个", "测", "试", "句", "子"]
```

### 代码位置

`rag/nlp/rag_tokenizer.py`

## 相关文档

- [001-解析与切片整体架构](./001-解析与切片整体架构.md)
- [002-Sections数据结构详解](./002-Sections数据结构详解.md)
- [003-Naive-Merge函数族详解](./003-Naive-Merge函数族详解.md)
