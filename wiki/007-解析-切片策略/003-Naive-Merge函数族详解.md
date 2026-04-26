# Naive Merge 函数族详解

## 概述

RAGFlow 提供三个 `naive_merge` 函数来处理不同格式的 sections，每个函数针对特定的数据结构优化。

## 函数对比

| 函数 | 输入 sections | 输出 chunks | 适用场景 | 特殊处理 |
|------|--------------|------------|---------|---------|
| `naive_merge()` | `[(text, tag), ...]` | `["chunk1", "chunk2", ...]` | PDF/纯文本 | 无 |
| `naive_merge_with_images()` | `[(text, tag), ...]` + `images` | `(chunks, images)` | Markdown/PDF(有图) | 拼接图片 |
| `naive_merge_docx()` | `[(text, img, tbl), ...]` | `[{"text":..., "ck_type":...}, ...]` | DOCX | 三种类型 + 上下文 |

## 1. naive_merge() - 纯文本分块

### 函数签名

```python
def naive_merge(
    sections: str | list,
    chunk_token_num=128,
    delimiter="\n。；！？",
    overlapped_percent=0
) -> list:
```

### 输入输出

```python
# 输入
sections = [
    ("第一段。", "@@1\t0.0\t100.0\t0.0\t50.0##"),
    ("第二段。", "@@1\t0.0\t100.0\t50.0\t100.0##"),
    ("第三段。", "@@2\t0.0\t100.0\t0.0\t50.0##"),
]

# 输出
chunks = [
    "第一段。\n第二段。",  # 合并后的块
    "第三段。",           # 另一个块
]
```

### 核心逻辑

```python
# 1. 规范化输入
if isinstance(sections, str):
    sections = [sections]
if isinstance(sections[0], str):
    sections = [(s, "") for s in sections]

# 2. 按分隔符切分
for sec, pos in sections:
    sentences = re.split(f"([{delimiter}])", sec)
    # ...

# 3. 按 token 数量合并
tk_nums[-1] += tnum
if tk_nums[-1] > chunk_token_num:
    # 创建新块
    cks.append(t)

# 4. 处理重叠百分比
if overlapped_percent > 0:
    overlap_len = int(len(cks[-1]) * overlapped_percent / 100)
    t = cks[-1][-overlap_len:] + t
```

### 代码位置

`rag/nlp/__init__.py` 第 1519-1642 行

## 2. naive_merge_with_images() - 带图片分块

### 函数签名

```python
def naive_merge_with_images(
    texts,           # [(text, tag), ...]
    images,          # [img1, img2, ...]
    chunk_token_num=128,
    delimiter="\n。；！？",
    overlapped_percent=0
) -> tuple:
```

### 输入输出

```python
# 输入
texts = [
    ("第一段。", "@@1\t..."),
    ("第二段。", "@@2\t..."),
]
images = [img1, img2]  # 与 texts 一一对应

# 输出
chunks = ["第一段。\n第二段。", ...]
result_images = [concat(img1, img2), ...]  # 同一块的图片被拼接
```

### 图片拼接逻辑

```python
# 同一块中的多张图片会被垂直拼接
if result_images[-1] is None:
    result_images[-1] = image
else:
    # 使用 concat_img 函数拼接
    result_images[-1] = concat_img(result_images[-1], image)

# concat_img 实现 (rag/nlp/__init__.py 第 1789-1869 行)
def concat_img(img1, img2):
    new_width = max(img1.width, img2.width)
    new_height = img1.height + img2.height
    new_image = Image.new('RGB', (new_width, new_height))
    new_image.paste(img1, (0, 0))
    new_image.paste(img2, (0, img1.height))
    return new_image
```

### 代码位置

`rag/nlp/__init__.py` 第 1644-1773 行

## 3. naive_merge_docx() - DOCX 专用分块

### 函数签名

```python
def naive_merge_docx(
    sections,                    # [(text, image, table), ...]
    chunk_token_num=128,
    delimiter="\n。；！？",
    table_context_size=0,
    image_context_size=0
) -> tuple:
```

### 输入输出

```python
# 输入
sections = [
    ("段落文本", None, None),
    ("", image_obj, None),
    ("", None, "<table>...</table>"),
]

# 输出
chunks = [
    {
        "text": "段落文本",
        "image": None,
        "ck_type": "text",
        "tk_nums": 10,
    },
    {
        "text": "",
        "image": image_obj,
        "ck_type": "image",
        "tk_nums": 0,
        "context_above": "段落文本",
        "context_below": "表格说明",
    },
    {
        "text": "<table>...</table>",
        "image": None,
        "ck_type": "table",
        "tk_nums": 50,
        "context_above": "...",
        "context_below": "...",
    },
]
images = [1]  # 包含图片的 chunk 索引
```

### 三种块类型处理

```python
# 1. 文本块
if ck_type == "text":
    # 可以合并到上一个块
    merged[prev_text_ck]["text"] += current_text

# 2. 图片块 - 独立成块，不合并
if ck_type == "image":
    merged.append(current_chunk)
    image_idxs.append(len(merged) - 1)

# 3. 表格块 - 独立成块，不合并
if ck_type == "table":
    merged.append(current_chunk)
```

### 上下文增强

```python
# 为图片/表格添加前后文本上下文
def _add_context(cks, idx, context_size):
    # 向上查找文本块
    while prev >= 0 and remain_above > 0:
        if cks[prev]["ck_type"] == "text":
            parts_above.insert(0, cks[prev]["text"])

    # 向下查找文本块
    while after < len(cks) and remain_below > 0:
        if cks[after]["ck_type"] == "text":
            parts_below.append(cks[after]["text"])

    cks[idx]["context_above"] = "".join(parts_above)
    cks[idx]["context_below"] = "".join(parts_below)
```

### 代码位置

`rag/nlp/__init__.py` 第 2208-2270 行

## 调用链路

### DOCX 路径

```python
# rag/app/naive.py 第 1479 行
chunks, images = naive_merge_docx(
    sections,  # [("文本", None, None), ...]
    chunk_token_num=128,
    delimiter="\n!?。；！？",
    table_context_size=0,
    image_context_size=0
)

# 第 1488 行：视觉增强
vision_figure_parser_docx_wrapper_naive(chunks=chunks, idx_lst=images, ...)

# 第 1494 行：分词
res.extend(doc_tokenize_chunks_with_images(chunks, doc, is_english, ...))
```

### Markdown/PDF(有图片) 路径

```python
# rag/app/naive.py 第 1830 行
chunks, images = naive_merge_with_images(
    sections,  # [("文本", "@@1\t..."), ...]
    section_images,  # [img1, img2, ...]
    chunk_token_num=128,
    delimiter="\n!?。；！？",
    overlapped_percent=0
)

# 第 1837 行：分词
res.extend(tokenize_chunks_with_images(chunks, doc, is_english, images, ...))
```

### 纯文本路径

```python
# rag/app/naive.py 第 1840 行
chunks = naive_merge(
    sections,  # [("行1", ""), ("行2", ""), ...]
    chunk_token_num=128,
    delimiter="\n!?。；！？",
    overlapped_percent=0
)

# 第 1846 行：分词
res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser, ...))
```

## 设计思想

### 为什么需要三个函数？

1. **数据结构不同**: sections 格式差异大，难以统一处理
2. **处理策略不同**:
   - 纯文本: 简单合并
   - 带图片: 需要拼接图片
   - DOCX: 需要区分类型和添加上下文

3. **性能优化**: 针对特定场景优化，避免不必要的处理

### 如何选择合适的函数？

```python
# 路由决策树
if 文件类型 == "DOCX":
    → naive_merge_docx()
elif 有图片:
    → naive_merge_with_images()
else:
    → naive_merge()
```

## 相关文档

- [001-解析与切片整体架构](./001-解析与切片整体架构.md)
- [002-Sections数据结构详解](./002-Sections数据结构详解.md)
- [004-Tokenize流程详解](./004-Tokenize流程详解.md)
