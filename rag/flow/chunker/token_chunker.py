#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import random
import re
from copy import deepcopy

from common.float_utils import normalize_overlapped_percent
from common.token_utils import num_tokens_from_string
from rag.flow.base import ProcessBase, ProcessParamBase
from rag.flow.chunker.schema import TokenChunkerFromUpstream
from rag.flow.parser.pdf_chunk_metadata import (
    PDF_POSITIONS_KEY,
    extract_pdf_positions,
    finalize_pdf_chunk,
    restore_pdf_text_previews,
)
from rag.nlp import naive_merge


class TokenChunkerParam(ProcessParamBase):
    """
    TokenChunker组件的参数类，定义了文本切块的相关参数
    """
    def __init__(self):
        super().__init__()
        # 分隔符模式：'token_size'(按token大小切块)、'delimiter'(按分隔符切块)、'one'(整个文本作为一个块)
        self.delimiter_mode = "token_size"
        # 每个块的token大小
        self.chunk_token_size = 512
        # 用于切分文本的主要分隔符列表
        self.delimiters = ["\n"]
        # 块之间的重叠百分比
        self.overlapped_percent = 0
        # 子级分隔符列表，用于进一步分割文本块
        self.children_delimiters = []
        # 表格上下文大小（在表格前后添加的文本token数量）
        self.table_context_size = 0
        # 图片上下文大小（在图片前后添加的文本token数量）
        self.image_context_size = 0

    def check(self):
        """
        检查参数的有效性
        """
        self.check_valid_value(self.delimiter_mode, "Delimiter mode abnormal.", ["token_size", "delimiter", "one"])
        # 标准化分隔符列表，确保只包含字符串类型
        if self.delimiters is None:
            self.delimiters = []
        elif isinstance(self.delimiters, str):
            self.delimiters = [self.delimiters]
        else:
            self.delimiters = [d for d in self.delimiters if isinstance(d, str)]
        self.delimiters = [d for d in self.delimiters if d]

        # 标准化子级分隔符列表，确保只包含字符串类型
        if self.children_delimiters is None:
            self.children_delimiters = []
        elif isinstance(self.children_delimiters, str):
            self.children_delimiters = [self.children_delimiters]
        else:
            self.children_delimiters = [d for d in self.children_delimiters if isinstance(d, str)]
        self.children_delimiters = [d for d in self.children_delimiters if d]

        # 检查各参数的有效性
        self.check_positive_integer(self.chunk_token_size, "Chunk token size.")
        self.check_decimal_float(self.overlapped_percent, "Overlapped percentage: [0, 1)")
        self.check_nonnegative_number(self.table_context_size, "Table context size.")
        self.check_nonnegative_number(self.image_context_size, "Image context size.")

    def get_input_form(self) -> dict[str, dict]:
        return {}


def _compile_delimiter_pattern(delimiters):
    """
    编译分隔符模式，将分隔符列表转换为正则表达式
    
    Args:
        delimiters: 分隔符列表
        
    Returns:
        编译后的正则表达式字符串
    """
    # 提取被反引号包围的自定义分隔符
    raw_delimiters = "".join(delimiter for delimiter in (delimiters or []) if delimiter)
    custom_delimiters = [m.group(1) for m in re.finditer(r"`([^`]+)`", raw_delimiters)]
    if not custom_delimiters:
        return ""
    # 按长度排序以避免较短的分隔符匹配覆盖较长的分隔符
    return "|".join(re.escape(text) for text in sorted(set(custom_delimiters), key=len, reverse=True))


def _split_text_by_pattern(text, pattern):
    """
    根据给定的模式分割文本
    
    Args:
        text: 要分割的文本
        pattern: 分隔符模式
        
    Returns:
        分割后的文本块列表
    """
    # 如果没有模式，则返回原始文本
    if not pattern:
        return [text or ""]

    # 使用re.split分割文本，保留分隔符
    split_texts = re.split(r"(%s)" % pattern, text or "", flags=re.DOTALL)
    chunks = []
    # 每次处理两个元素：文本段和对应的分隔符
    for i in range(0, len(split_texts), 2):
        chunk = split_texts[i]
        if not chunk:
            continue
        # 如果存在对应的分隔符，将其添加到当前块的末尾
        if i + 1 < len(split_texts):
            chunk += split_texts[i + 1]
        # 只保留非空块
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def _build_json_chunks(json_result, delimiter_pattern):
    """
    将上游JSON结果转换为内部处理的块
    
    Args:
        json_result: 上游JSON结果
        delimiter_pattern: 分隔符模式
        
    Returns:
        内部格式的块列表
    """
    chunks = []
    for item in json_result:
        # 识别文档类型（表格、图片或文本）
        doc_type = str(item.get("doc_type_kwd") or "").strip().lower()
        if doc_type == "table":
            ck_type = "table"
        elif doc_type == "image":
            ck_type = "image"
        else:
            ck_type = "text"

        # 获取文本内容，尝试多个可能的字段
        text = item.get("text")
        if not isinstance(text, str):
            text = item.get("content_with_weight")
        if not isinstance(text, str):
            text = ""

        # 提取PDF位置信息以便后续处理
        preview_positions = extract_pdf_positions(item)
        img_id = item.get("img_id")

        if ck_type == "text":
            # 如果是文本类型，根据分隔符模式进一步分割
            text_segments = _split_text_by_pattern(text, delimiter_pattern) if delimiter_pattern else [text]
            for segment in text_segments:
                if not segment or not segment.strip():
                    continue
                chunks.append(
                    {
                        "text": segment,
                        "doc_type_kwd": "text",
                        "ck_type": "text",
                        PDF_POSITIONS_KEY: deepcopy(preview_positions),
                        "tk_nums": num_tokens_from_string(segment),
                    }
                )
            continue

        # 对于表格和图片类型，创建带上下文槽的块
        chunks.append(
            {
                "text": text or "",
                "doc_type_kwd": ck_type,
                "ck_type": ck_type,
                "img_id": img_id,
                PDF_POSITIONS_KEY: deepcopy(preview_positions),
                "tk_nums": num_tokens_from_string(text or ""),
                "context_above": "",
                "context_below": "",
            }
        )

    return chunks


def _take_sentences(text, need_tokens, from_end=False):
    """
    从文本中提取满足token数的句子
    
    Args:
        text: 源文本
        need_tokens: 需要的token数量
        from_end: 是否从末尾开始提取
        
    Returns:
        满足条件的文本片段
    """
    # 按句子结束标志分割文本
    split_pat = r"([。!?？；！\n]|\. )"
    texts = re.split(split_pat, text or "", flags=re.DOTALL)
    sentences = []
    # 将文本部分与其结束标志配对
    for i in range(0, len(texts), 2):
        sentences.append(texts[i] + (texts[i + 1] if i + 1 < len(texts) else ""))
    
    # 根据from_end参数决定迭代顺序
    iterator = reversed(sentences) if from_end else sentences
    collected = ""
    # 逐步累加句子直到达到所需的token数
    for sentence in iterator:
        collected = sentence + collected if from_end else collected + sentence
        if num_tokens_from_string(collected) >= need_tokens:
            break
    return collected


def _attach_context_to_media_chunks(chunks, table_context_size, image_context_size):
    """
    为表格和图片块添加上下文
    
    Args:
        chunks: 块列表
        table_context_size: 表格上下文大小
        image_context_size: 图片上下文大小
    """
    for i, chunk in enumerate(chunks):
        # 只处理表格和图片类型的块
        if chunk["ck_type"] not in {"table", "image"}:
            continue

        # 根据块类型确定上下文大小
        context_size = image_context_size if chunk["ck_type"] == "image" else table_context_size
        if context_size <= 0:
            continue

        # 初始化上下文预算和存储列表
        remain_above = context_size
        remain_below = context_size
        parts_above = []
        parts_below = []

        # 向前搜索文本块，填充上方上下文
        prev = i - 1
        while prev >= 0 and remain_above > 0:
            prev_chunk = chunks[prev]
            if prev_chunk["ck_type"] == "text":
                if prev_chunk["tk_nums"] >= remain_above:
                    # 如果当前文本块超出剩余预算，则只取所需部分
                    parts_above.insert(0, _take_sentences(prev_chunk["text"], remain_above, from_end=True))
                    remain_above = 0
                    break
                parts_above.insert(0, prev_chunk["text"])
                remain_above -= prev_chunk["tk_nums"]
            prev -= 1

        # 向后搜索文本块，填充下方上下文
        after = i + 1
        while after < len(chunks) and remain_below > 0:
            after_chunk = chunks[after]
            if after_chunk["ck_type"] == "text":
                if after_chunk["tk_nums"] >= remain_below:
                    # 如果当前文本块超出剩余预算，则只取所需部分
                    parts_below.append(_take_sentences(after_chunk["text"], remain_below))
                    remain_below = 0
                    break
                parts_below.append(after_chunk["text"])
                remain_below -= after_chunk["tk_nums"]
            after += 1

        # 将收集到的上下文分配给当前块
        chunk["context_above"] = "".join(parts_above)
        chunk["context_below"] = "".join(parts_below)


def _merge_text_chunks_by_token_size(chunks, chunk_token_size, overlapped_percent):
    """
    按token大小合并文本块
    
    Args:
        chunks: 块列表
        chunk_token_size: 块的token大小
        overlapped_percent: 重叠百分比
        
    Returns:
        合并后的块列表
    """
    merged = []
    prev_text_idx = -1
    # 计算阈值，当块大小小于此值时可继续合并
    threshold = chunk_token_size * (100 - overlapped_percent) / 100.0

    for chunk in chunks:
        if chunk["ck_type"] != "text":
            # 非文本块直接添加
            merged.append(deepcopy(chunk))
            prev_text_idx = -1
            continue

        current = deepcopy(chunk)
        # 判断是否应开始新的块：之前没有文本块或前一块已超过阈值
        should_start_new = prev_text_idx < 0 or merged[prev_text_idx]["tk_nums"] > threshold
        if should_start_new:
            # 如果需要重叠，则从前一块获取重叠部分
            if prev_text_idx >= 0 and overlapped_percent > 0 and merged[prev_text_idx]["text"]:
                overlapped = merged[prev_text_idx]["text"]
                # 计算重叠起始位置
                overlap_start = int(len(overlapped) * (100 - overlapped_percent) / 100.0)
                current["text"] = overlapped[overlap_start:] + current["text"]
                current["tk_nums"] = num_tokens_from_string(current["text"])
            merged.append(current)
            prev_text_idx = len(merged) - 1
            continue

        # 合并当前文本块到前一个文本块
        if merged[prev_text_idx]["text"] and current["text"]:
            merged[prev_text_idx]["text"] += "\n" + current["text"]
        else:
            merged[prev_text_idx]["text"] += current["text"]
        # 合并PDF位置信息
        merged[prev_text_idx][PDF_POSITIONS_KEY].extend(current.get(PDF_POSITIONS_KEY) or [])
        # 累加token数
        merged[prev_text_idx]["tk_nums"] += current["tk_nums"]

    return merged


def _finalize_json_chunks(chunks):
    """
    将内部块转换为最终的输出格式
    
    Args:
        chunks: 内部格式的块列表
        
    Returns:
        最终输出的块列表
    """
    docs = []
    for chunk in chunks:
        # 合并上下文和文本内容
        text = (chunk.get("context_above") or "") + (chunk.get("text") or "") + (chunk.get("context_below") or "")
        if not text.strip():
            continue

        # 构建最终输出文档
        doc = {
            "text": text,
            "doc_type_kwd": chunk.get("doc_type_kwd", "text"),
        }
        # 如果有PDF位置信息，则复制
        if chunk.get(PDF_POSITIONS_KEY):
            doc[PDF_POSITIONS_KEY] = deepcopy(chunk[PDF_POSITIONS_KEY])
        # 如果有母块引用，则复制
        if chunk.get("mom"):
            doc["mom"] = chunk["mom"]
        # 如果有图片ID，则复制
        if chunk.get("img_id"):
            doc["img_id"] = chunk["img_id"]
        docs.append(finalize_pdf_chunk(doc))

    return docs


def _split_chunk_docs_by_children(chunks, pattern):
    """
    使用子分隔符模式进一步分割文本块
    
    Args:
        chunks: 块列表
        pattern: 子分隔符模式
        
    Returns:
        分割后的块列表
    """
    if not pattern:
        return chunks

    docs = []
    for chunk in chunks:
        # 只对文本类型的块进行子级分割
        if chunk.get("doc_type_kwd", "text") != "text":
            docs.append(chunk)
            continue

        # 使用子分隔符模式分割文本
        split_texts = _split_text_by_pattern(chunk.get("text", ""), pattern)

        mom = chunk.get("text", "")  # 记录母块文本
        for text in split_texts:
            if not text.strip():
                continue
            child = deepcopy(chunk)  # 复制原块的所有属性
            child["mom"] = mom  # 设置母块引用
            child["text"] = text  # 设置新文本
            docs.append(child)

    return docs

class TokenChunker(ProcessBase):
    """
    Token切块器组件，将文本按照指定规则分割成块
    """
    component_name = "TokenChunker"

    async def _invoke(self, **kwargs):
        """
        执行文本切块操作
        """
        try:
            from_upstream = TokenChunkerFromUpstream.model_validate(kwargs)
        except Exception as e:
            self.set_output("_ERROR", f"Input error: {str(e)}")
            return

        # 编译主分隔符和子分隔符模式
        delimiter_pattern = _compile_delimiter_pattern(self._param.delimiters)
        custom_pattern = "|".join(re.escape(t) for t in sorted(set(self._param.children_delimiters), key=len, reverse=True))

        self.set_output("output_format", "chunks")
        self.callback(random.randint(1, 5) / 100.0, "Start to split into chunks.")
        overlapped_percent = normalize_overlapped_percent(self._param.overlapped_percent)
        
        # 处理非JSON格式的输入（markdown、text、html）
        if from_upstream.output_format in ["markdown", "text", "html"]:
            payload = getattr(from_upstream, f"{from_upstream.output_format}_result") or ""
            if self._param.delimiter_mode == "one":
                # 如果模式为"one"，整个文本作为一个块
                self.set_output("chunks", [{"text": payload}] if payload.strip() else [])
                self.callback(1, "Done.")
                return
                
            # 根据是否存在分隔符模式选择分割方法
            cks = _split_text_by_pattern(payload, delimiter_pattern) if delimiter_pattern else naive_merge(
                payload,
                self._param.chunk_token_size,
                "",
                overlapped_percent,
            )
            
            # 如果有子分隔符，则对每个块进一步分割
            if custom_pattern:
                docs = []
                for c in cks:
                    if not c.strip():
                        continue
                    for text in _split_text_by_pattern(c, custom_pattern):
                        if not text.strip():
                            continue
                        docs.append({"text": text, "mom": c})  # 记录母块引用
                self.set_output("chunks", docs)
            else:
                self.set_output("chunks", [{"text": c.strip()} for c in cks if c.strip()])

            self.callback(1, "Done.")
            return

        # 处理JSON格式的输入
        json_result = from_upstream.json_result or []
        if self._param.delimiter_mode == "one":
            # 如果模式为"one"，将所有文本合并为一个块
            sections = []
            for item in json_result:
                text = item.get("text")
                if not isinstance(text, str):
                    text = item.get("content_with_weight")
                if isinstance(text, str) and text.strip():
                    sections.append(text)
            merged_text = "\n".join(sections)
            self.set_output("chunks", [{"text": merged_text}] if merged_text.strip() else [])
            self.callback(1, "Done.")
            return
            
        # 对于结构化JSON输入，首先转换为内部块格式
        chunks = _build_json_chunks(json_result, delimiter_pattern)
        # 为媒体块添加上下文
        _attach_context_to_media_chunks(chunks, self._param.table_context_size, self._param.image_context_size)
        # 如果没有主分隔符，则按token大小合并文本块
        if not delimiter_pattern:
            chunks = _merge_text_chunks_by_token_size(chunks, self._param.chunk_token_size, overlapped_percent)

        # 如果有子分隔符，则对文本块进一步分割
        if custom_pattern:
            chunks = _split_chunk_docs_by_children(chunks, custom_pattern)

        # 恢复PDF文本预览
        await restore_pdf_text_previews(chunks, from_upstream, self._canvas)
        # 最终化块格式
        cks = _finalize_json_chunks(chunks)
        self.set_output("chunks", cks)
        self.callback(1, "Done.")