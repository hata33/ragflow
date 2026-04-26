# Sections 数据结构详解

## 概述

`sections` 是文档解析后的中间数据结构，不同格式的文档解析后会产生不同结构的 sections。

## 各格式的 Sections 结构

### 1. PDF 格式

**结构**: `[(text, position_tag), ...]`

```python
# 示例
sections = [
    ("第一段文本", "@@1\t0.0\t100.0\t0.0\t50.0##"),
    ("第二段文本", "@@1\t0.0\t100.0\t50.0\t100.0##"),
    ("第三段文本", "@@2\t0.0\t100.0\t0.0\t50.0##"),
]

# position_tag 格式解析
# "@@页码\t左\t右\t上\t下##"
# 例如 "@@1\t0.0\t100.0\t0.0\t50.0##" 表示:
#   - 第 1 页
#   - x 坐标: 0.0 到 100.0
#   - y 坐标: 0.0 到 50.0
```

**位置标签的用途**:
1. **原文溯源**: 定位答案在原文档中的位置
2. **图片裁剪**: 根据位置精确裁剪对应区域的图片
3. **多模态增强**: 判断图片和文本的关联关系

**代码位置**: `rag/app/naive.py` 第 1524-1535 行

### 2. DOCX 格式

**结构**: `[(text, image, table), ...]`

```python
# 示例
sections = [
    # 纯文本段落
    ("这是段落内容。", None, None),

    # 图片段落
    ("", <PIL.Image.Image object at 0x...>, None),

    # 表格段落
    ("", None, "<table><caption>Table Location: 文档名 > 第一章</caption><tr>...</tr></table>"),
]

# 元素说明:
# - text: 段落文本（图片和表格时为空字符串）
# - image: PIL 图片对象（段落和表格时为 None）
# - table: HTML 格式的表格字符串（非表格时为 None）
```

**三元组设计原因**:
DOCX 解析器需要区分三种元素类型，每种类型有不同的处理逻辑：
- 纯文本: 直接合并到 chunk
- 图片: 独立成块，标记为 `ck_type="image"`
- 表格: 独立成块，标记为 `ck_type="table"`

**代码位置**: `rag/app/naive.py` 第 688-872 行

### 3. Excel/CSV 格式

**结构**: `[(text, ""), ...]`

```python
# 示例
sections = [
    ("姓名\t年龄\t城市", ""),
    ("张三\t25\t北京", ""),
    ("李四\t30\t上海", ""),
]

# 说明:
# - text: 单元格内容或整行数据
# - "": 第二个元素是空字符串（占位符，无实际意义）
```

**代码位置**: `rag/app/naive.py` 第 1588-1600 行

### 4. Markdown 格式

**结构**: `[(text, ""), ...]`

```python
# 示例
sections = [
    ("# 一级标题", ""),
    ("这是段落内容。", ""),
    ("## 二级标题", ""),
    ("**加粗文本**", ""),
]
```

**特殊处理**: Markdown 解析时还会返回 `section_images`，与 sections 一一对应。

**代码位置**: `rag/app/naive.py` 第 1199-1290 行

### 5. TXT 纯文本格式

**结构**: `[(text, ""), ...]`

```python
# 示例
sections = [
    ("第一行内容", ""),
    ("第二行内容", ""),
    ("第三行内容", ""),
]
```

**代码位置**: `rag/app/naive.py` 第 1603-1613 行

### 6. HTML 格式

**结构**: `[(text, ""), ...]`

```python
# 示例
sections = [
    ("<h1>标题</h1>", ""),
    ("<p>段落内容</p>", ""),
]
```

**代码位置**: `rag/app/naive.py` 第 1694-1702 行

## Sections 对比表

| 格式 | 结构 | 位置信息 | 图片支持 | 表格支持 |
|------|------|---------|---------|---------|
| **PDF** | `(text, tag)` | ✅ 完整坐标 | ❌ | ❌ |
| **DOCX** | `(text, img, tbl)` | ❌ | ✅ 独立块 | ✅ 独立块 |
| **Excel** | `(text, "")` | ❌ | ❌ | ❌ |
| **Markdown** | `(text, "")` | ❌ | ✅ 关联 | ✅ 分离 |
| **TXT** | `(text, "")` | ❌ | ❌ | ❌ |
| **HTML** | `(text, "")` | ❌ | ❌ | ❌ |

## 设计思想

### 为什么不统一 sections 格式？

1. **保留格式特性**: 每种格式有其独特的结构特征
   - PDF: 需要精确的位置信息用于图片裁剪
   - DOCX: 需要区分文本、图片、表格三种类型
   - Markdown: 图片和文本紧密关联

2. **避免信息丢失**: 强制统一会导致某些格式的特殊信息丢失

3. **灵活性**: 不同格式可以有不同的处理策略

### 如何处理不同的 sections？

在分块路由层（`chunk()` 函数）根据 sections 结构选择合适的 merge 函数：

```python
# 路由逻辑
if 文件类型 == DOCX:
    chunks, images = naive_merge_docx(sections, ...)
elif 有图片:
    chunks, images = naive_merge_with_images(sections, images, ...)
else:
    chunks = naive_merge(sections, ...)
```

## 相关代码位置

- PDF 解析: `deepdoc/parser/pdf_parser.py`
- DOCX 解析: `rag/app/naive.py` 第 525-916 行
- Markdown 解析: `deepdoc/parser/pdf_parser.py` (MarkdownParser 类)
- 路由逻辑: `rag/app/naive.py` 第 1335-1874 行

## 相关文档

- [001-解析与切片整体架构](./001-解析与切片整体架构.md)
- [003-Naive-Merge函数族详解](./003-Naive-Merge函数族详解.md)
