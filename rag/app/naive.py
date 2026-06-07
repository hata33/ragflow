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
#
"""
朴素文档解析模块

本模块提供了各种文档格式的解析和分块功能，是 RAGFlow 文档处理的核心模块。

主要功能：
- 支持多种文档格式：PDF、DOCX、Excel、TXT、Markdown、HTML、EPUB、JSON等
- 提供多种解析器：DeepDOC、MinerU、Docling、TCADP、PaddleOCR等
- 文档分块（Chunking）：将文档切分为适合检索的小块
- 支持表格、图片、超链接等特殊元素处理
- 支持 RTL（从右到左）文本规范化

支持的解析器：
- deepdoc: 默认的 DeepDOC 解析器
- mineru: MinerU OCR 解析器
- docling: Docling 解析器
- tcadp parser: 腾讯云 TCADP 解析器
- paddleocr: PaddleOCR 解析器
- plaintext: 纯文本解析器

使用场景：
- 知识库文档上传
- 文档预处理
- 文档分块索引
"""

import logging
import re
import os
from functools import reduce
from io import BytesIO
from timeit import default_timer as timer
from docx import Document
from docx.opc.pkgreader import _SerializedRelationships, _SerializedRelationship
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from docx.opc.oxml import parse_xml
from markdown import markdown
from PIL import Image
from common.token_utils import num_tokens_from_string

from common.constants import LLMType, MAXIMUM_PAGE_NUMBER
from api.db.services.llm_service import LLMBundle
from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type, get_model_config_from_provider_instance
from rag.utils.file_utils import extract_embed_file, extract_links_from_pdf, extract_links_from_docx, extract_html
from deepdoc.parser import DocxParser, EpubParser, ExcelParser, HtmlParser, JsonParser, MarkdownElementExtractor, MarkdownParser, PdfParser, TxtParser
from deepdoc.parser.figure_parser import VisionFigureParser, vision_figure_parser_docx_wrapper_naive, vision_figure_parser_pdf_wrapper
from deepdoc.parser.pdf_parser import PlainParser, VisionParser
from deepdoc.parser.docling_parser import DoclingParser
from deepdoc.parser.tcadp_parser import TCADPParser
from common.float_utils import normalize_overlapped_percent
from common.parser_config_utils import normalize_layout_recognizer
from common.text_utils import normalize_arabic_presentation_forms
from rag.nlp import (
    concat_img,
    find_codec,
    naive_merge,
    naive_merge_with_images,
    naive_merge_docx,
    rag_tokenizer,
    tokenize_chunks,
    doc_tokenize_chunks_with_images,
    tokenize_table,
    append_context2table_image4pdf,
    tokenize_chunks_with_images,
)  # noqa: F401


def _is_short_header(text, max_tokens=50):
    """
    Check if text is a short markdown header.
    
    Args:
        text: The text to check
        max_tokens: Maximum tokens for a header to be considered "short"
    
    Returns:
        bool: True if text is a short markdown header, False otherwise
    """
    if not text or not text.strip():
        return False
    
    # Check if it matches markdown header pattern: 1-6 # followed by space
    if not re.match(r"^#{1,6}\s+", text.strip()):
        return False
    
    # Check if token count is below threshold
    return num_tokens_from_string(text) < max_tokens


def _normalize_section_text_for_rtl_presentation_forms(sections):
    """
    规范化从右到左（RTL）语言的文本呈现形式

    该函数用于处理阿拉伯语等从右到左书写的语言，规范化其字符呈现形式。
    支持多种数据结构：元组、列表和字符串。

    Args:
        sections: 文本段落列表，可以是以下格式之一：
            - list[str]: 纯字符串列表
            - list[tuple]: 元组列表（每个元组的第一个元素是文本）
            - list[list]: 列表的列表（每个子列表的第一个元素是文本）

    Returns:
        与输入结构相同的规范化文本列表

    Note:
        - 元组和列表的第一个元素会被规范化，其他元素保持不变
        - 空值（None或空容器）会直接返回，不做处理
    """
    if not sections:
        return sections

    normalized_sections = []
    for section in sections:
        # 处理元组格式：(text, image, table, ...)
        if isinstance(section, tuple):
            if not section:
                normalized_sections.append(section)
                continue
            text = section[0]
            normalized_text = normalize_arabic_presentation_forms(text)
            normalized_sections.append((normalized_text, *section[1:]))
            continue
        # 处理列表格式：[text, image, table, ...]
        if isinstance(section, list):
            if not section:
                normalized_sections.append(section)
                continue
            text = section[0]
            normalized_text = normalize_arabic_presentation_forms(text)
            normalized_sections.append([normalized_text, *section[1:]])
            continue
        # 处理纯字符串格式
        normalized_sections.append(normalize_arabic_presentation_forms(section))

    return normalized_sections


def by_deepdoc(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, lang="Chinese", callback=None, pdf_cls=None, **kwargs):
    callback = callback
    binary = binary
    # 创建解析器实例：优先使用传入的 pdf_cls，否则使用默认的 Pdf 类
    pdf_parser = pdf_cls() if pdf_cls else Pdf()
    # 执行 PDF 解析：提取文本段落和表格
    sections, tables = pdf_parser(filename if not binary else binary, from_page=from_page, to_page=to_page, callback=callback)

    # 使用视觉模型增强表格内容（如图表理解、数据提取等）
    tables = vision_figure_parser_pdf_wrapper(
        tbls=tables,
        sections=sections,
        callback=callback,
        **kwargs,
    )
    return sections, tables, pdf_parser


def by_mineru(
    filename,
    binary=None,
    from_page=0,
    to_page=MAXIMUM_PAGE_NUMBER,
    lang="Chinese",
    callback=None,
    pdf_cls=None,
    parse_method: str = "raw",
    mineru_llm_name: str | None = None,
    tenant_id: str | None = None,
    **kwargs,
):
    """
    使用 MinerU OCR 解析器解析 PDF 文档

    MinerU 是一个基于深度学习的 OCR 解析器，特别适合处理扫描件、
    图片型 PDF 等需要文字识别的场景。

    Args:
        filename: PDF 文件名或路径
        binary: PDF 文件的二进制内容（可选）
        from_page: 起始页码（默认 0）
        to_page: 结束页码（默认 100000）
        lang: 语言设置（默认 "Chinese"）
        callback: 进度回调函数
        pdf_cls: 忽略（保留参数兼容性）
        parse_method: 解析方法，"raw" 或 "auto"（默认 "raw"）
        mineru_llm_name: MinerU 模型名称（可选，未指定时从数据库查询）
        tenant_id: 租户 ID，用于查找模型配置
        **kwargs: 其他参数

    Returns:
        tuple: (sections, tables, pdf_parser) 或 (None, None, None)
            - 成功时返回解析结果
            - 失败时返回三个 None

    Processing Steps:
        1. 如果未指定 mineru_llm_name，从租户配置中查询
        2. 获取 MinerU OCR 模型配置
        3. 创建 LLMBundle 并获取模型实例
        4. 调用模型的 parse_pdf 方法执行 OCR
        5. 返回解析结果或错误信息
    """
    pdf_parser = None
    if tenant_id:
        # 如果未指定模型名称，尝试从租户配置中获取
        if not mineru_llm_name:
            try:
                from api.db.services.tenant_llm_service import TenantLLMService

                # 优先从环境变量获取
                env_name = TenantLLMService.ensure_mineru_from_env(tenant_id)
                # 其次从数据库查询租户配置的 MinerU 模型
                candidates = TenantLLMService.query(tenant_id=tenant_id, llm_factory="MinerU", model_type=LLMType.OCR)
                if candidates:
                    mineru_llm_name = candidates[0].llm_name
                elif env_name:
                    mineru_llm_name = env_name
            except Exception as e:  # best-effort fallback
                logging.warning(f"fallback to env mineru: {e}")

        # 如果找到了 MinerU 模型配置，执行 OCR 解析
        if mineru_llm_name: 
            try:
                ocr_model_config = get_model_config_from_provider_instance(tenant_id, LLMType.OCR, mineru_llm_name)
                ocr_model = LLMBundle(tenant_id=tenant_id, model_config=ocr_model_config, lang=lang)
                # 获取底层模型实例
                pdf_parser = ocr_model.mdl

                # Closes #14869: when the tenant has an IMAGE2TEXT model
                # configured, let the MinerU parser enrich image chunks with
                # VLM-generated semantic descriptions (parity with deepdoc's
                # VisionFigureParser). Best-effort — fall back silently if
                # no vision model is available.
                if "vision_model" not in kwargs:
                    try:
                        vision_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.IMAGE2TEXT)
                        kwargs["vision_model"] = LLMBundle(tenant_id=tenant_id, model_config=vision_model_config, lang=lang)
                    except Exception as vlm_err:
                        logging.info(f"[MinerU] no IMAGE2TEXT model for tenant; skipping image VLM enhancement: {vlm_err}")

                sections, tables = pdf_parser.parse_pdf(
                    filepath=filename,
                    binary=binary,
                    callback=callback,
                    parse_method=parse_method,
                    lang=lang,
                    **kwargs,
                )
                return sections, tables, pdf_parser
            except Exception as e:
                logging.error(f"Failed to parse pdf via LLMBundle MinerU ({mineru_llm_name}): {e}")

    # 未找到 MinerU 模型或解析失败
    if callback:
        callback(-1, "MinerU not found.")
    return None, None, None


def by_docling(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, lang="Chinese", callback=None, pdf_cls=None, **kwargs):
    pdf_parser = DoclingParser()
    parse_method = kwargs.get("parse_method", "raw")

    # 检查 Docling 是否已安装
    if not pdf_parser.check_installation():
        if callback:
            callback(-1, "Docling not found.")
        return None, None, pdf_parser

    # 执行 PDF 解析
    sections, tables = pdf_parser.parse_pdf(
        filepath=filename,
        binary=binary,
        callback=callback,
        output_dir=os.environ.get("DOCLING_OUTPUT_DIR", ""),
        delete_output=bool(int(os.environ.get("DOCLING_DELETE_OUTPUT", 1))),
        docling_server_url=os.environ.get("DOCLING_SERVER_URL", ""),
        parse_method=parse_method,
    )
    return sections, tables, pdf_parser


def by_opendataloader(
    filename,
    binary=None,
    from_page=0,
    to_page=MAXIMUM_PAGE_NUMBER,
    lang="Chinese",
    callback=None,
    pdf_cls=None,
    parse_method: str = "raw",
    opendataloader_llm_name: str | None = None,
    tenant_id: str | None = None,
    **kwargs,
):
    if tenant_id:
        if not opendataloader_llm_name:
            try:
                from api.db.services.tenant_llm_service import TenantLLMService

                env_name = TenantLLMService.ensure_opendataloader_from_env(tenant_id)
                candidates = TenantLLMService.query(tenant_id=tenant_id, llm_factory="OpenDataLoader", model_type=LLMType.OCR)
                if candidates:
                    opendataloader_llm_name = candidates[0].llm_name
                elif env_name:
                    opendataloader_llm_name = env_name
            except Exception as e:
                logging.warning(f"fallback to env opendataloader: {e}")

        if opendataloader_llm_name:
            try:
                ocr_model_config = get_model_config_from_provider_instance(tenant_id, LLMType.OCR, opendataloader_llm_name)
                ocr_model = LLMBundle(tenant_id=tenant_id, model_config=ocr_model_config, lang=lang)
                pdf_parser = ocr_model.mdl
                parse_options = {k: kwargs[k] for k in ("hybrid", "image_output", "sanitize") if k in kwargs}
                sections, tables = pdf_parser.parse_pdf(
                    filepath=filename,
                    binary=binary,
                    callback=callback,
                    parse_method=parse_method,
                    **parse_options,
                )
                return sections, tables, pdf_parser
            except Exception as e:
                logging.error(f"Failed to parse pdf via LLMBundle OpenDataLoader ({opendataloader_llm_name}): {e}")

    if callback:
        callback(-1, "OpenDataLoader not found.")
    return None, None, None


def by_tcadp(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, lang="Chinese", callback=None, pdf_cls=None, **kwargs):
    tcadp_parser = TCADPParser()

    # 检查 TCADP 服务是否可用
    if not tcadp_parser.check_installation():
        callback(-1, "TCADP parser not available. Please check Tencent Cloud API configuration.")
        return None, None, tcadp_parser

    # 执行文档解析
    sections, tables = tcadp_parser.parse_pdf(filepath=filename, binary=binary, callback=callback, output_dir=os.environ.get("TCADP_OUTPUT_DIR", ""), file_type="PDF")
    return sections, tables, tcadp_parser


def by_paddleocr(
    filename,
    binary=None,
    from_page=0,
    to_page=MAXIMUM_PAGE_NUMBER,
    lang="Chinese",
    callback=None,
    pdf_cls=None,
    parse_method: str = "raw",
    paddleocr_llm_name: str | None = None,
    tenant_id: str | None = None,
    **kwargs,
):
    """
    使用 PaddleOCR 解析器解析 PDF 文档

    PaddleOCR 是百度开源的 OCR 工具，对中文识别效果较好，
    特别适合处理中文扫描件和图片型 PDF。

    Args:
        filename: PDF 文件名或路径
        binary: PDF 文件的二进制内容（可选）
        from_page: 起始页码（默认 0）
        to_page: 结束页码（默认 100000）
        lang: 语言设置（默认 "Chinese"）
        callback: 进度回调函数
        pdf_cls: 忽略（保留参数兼容性）
        parse_method: 解析方法（默认 "raw"）
        paddleocr_llm_name: PaddleOCR 模型名称（可选，未指定时从数据库查询）
        tenant_id: 租户 ID，用于查找模型配置
        **kwargs: 其他参数

    Returns:
        tuple: (sections, tables, pdf_parser) 或 (None, None, None)
            - 成功时返回解析结果
            - 失败时返回三个 None

    Processing Steps:
        1. 如果未指定 paddleocr_llm_name，从租户配置中查询
        2. 获取 PaddleOCR 模型配置
        3. 创建 LLMBundle 并获取模型实例
        4. 调用模型的 parse_pdf 方法执行 OCR
        5. 返回解析结果或错误信息
    """
    pdf_parser = None
    if tenant_id:
        # 如果未指定模型名称，尝试从租户配置中获取
        if not paddleocr_llm_name:
            try:
                from api.db.services.tenant_llm_service import TenantLLMService

                # 优先从环境变量获取
                env_name = TenantLLSLLMService.ensure_paddleocr_from_env(tenant_id)
                # 其次从数据库查询租户配置的 PaddleOCR 模型
                candidates = TenantLLMService.query(tenant_id=tenant_id, llm_factory="PaddleOCR", model_type=LLMType.OCR)
                if candidates:
                    paddleocr_llm_name = candidates[0].llm_name
                elif env_name:
                    paddleocr_llm_name = env_name
            except Exception as e:  # best-effort fallback
                logging.warning(f"fallback to env paddleocr: {e}")

        # 如果找到了 PaddleOCR 模型配置，执行 OCR 解析
        if paddleocr_llm_name:
            try:
                ocr_model_config = get_model_config_from_provider_instance(tenant_id, LLMType.OCR, paddleocr_llm_name)
                ocr_model = LLMBundle(tenant_id=tenant_id, model_config=ocr_model_config, lang=lang)
                # 获取底层模型实例
                pdf_parser = ocr_model.mdl
                # 执行 PDF OCR 解析
                sections, tables = pdf_parser.parse_pdf(
                    filepath=filename,
                    binary=binary,
                    callback=callback,
                    parse_method=parse_method,
                    **kwargs,
                )
                return sections, tables, pdf_parser
            except Exception as e:
                logging.error(f"Failed to parse pdf via LLMBundle PaddleOCR ({paddleocr_llm_name}): {e}")

        return None, None, None

    # 未找到 PaddleOCR 模型
    if callback:
        callback(-1, "PaddleOCR not found.")
    return None, None, None


def by_plaintext(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, callback=None, **kwargs):
    layout_recognizer = (kwargs.get("layout_recognizer") or "").strip()
    # 模式1：纯文本解析（默认）
    if (not layout_recognizer) or (layout_recognizer == "Plain Text"):
        pdf_parser = PlainParser()
    # 模式2：视觉模型解析
    else:
        tenant_id = kwargs.get("tenant_id")
        if not tenant_id:
            raise ValueError("tenant_id is required when using vision layout recognizer")
        vision_model_config = get_model_config_from_provider_instance(tenant_id, LLMType.IMAGE2TEXT, layout_recognizer)
        vision_model = LLMBundle(
            tenant_id,
            model_config=vision_model_config,
            lang=kwargs.get("lang", "Chinese"),
        )
        # 创建视觉解析器
        pdf_parser = VisionParser(vision_model=vision_model, **kwargs)

    # 执行 PDF 解析
    sections, tables = pdf_parser(filename if not binary else binary, from_page=from_page, to_page=to_page, callback=callback)
    return sections, tables, pdf_parser


# PDF 解析器注册表
# 键名为解析器标识（小写），值为对应的解析函数
PARSERS = {
    "deepdoc": by_deepdoc,
    "mineru": by_mineru,
    "docling": by_docling,
    "opendataloader": by_opendataloader,
    "tcadp parser": by_tcadp,
    "paddleocr": by_paddleocr,
    "plaintext": by_plaintext,  # default
}


class Docx(DocxParser):
    """
    DOCX 文档解析器

    继承自 DocxParser，专门用于解析 Microsoft Word (.docx) 格式文档。

    Attributes:
        doc: Document 对象

    Note:
        - 支持提取段落、表格、图片等元素
        - 支持自动识别表格所在的标题层级
        - 支持转换为 Markdown 格式
    """

    def __init__(self):
        pass

    def __clean(self, line):
        """
        清理文本行

        将全角空格（\u3000）替换为半角空格，并去除首尾空白。

        Args:
            line: 待清理的文本行

        Returns:
            str: 清理后的文本行
        """
        line = re.sub(r"\u3000", " ", line).strip()
        return line

    def __get_nearest_title(self, table_index, filename):
        """
        获取表格之前的层级标题结构

        该函数用于找到表格所在位置的标题层级，生成类似
        "文档名 > 一级标题 > 二级标题" 的层级路径。

        Args:
            table_index: 目标表格的索引（从 0 开始）
            filename: 文档文件名，用于生成文档名

        Returns:
            str: 标题层级路径，如 "用户手册 > 第一章 > 1.1 节"
                 如果未找到标题则返回空字符串

        Processing Steps:
            1. 收集文档中的所有段落和表格，保持文档顺序
            2. 定位目标表格的位置
            3. 从表格位置向前搜索最近的标题
            4. 递归查找父级标题，构建完整的标题层级
            5. 格式化为 "文档名 > 标题1 > 标题2" 的形式

        Note:
            - 支持最多 7 级标题（Heading 1-7）
            - 标题样式名称需包含 "Heading" 关键字
        """
        import re
        from docx.text.paragraph import Paragraph

        titles = []
        blocks = []

        # 从文件名提取文档名（去除扩展名）
        doc_name = re.sub(r"\.[a-zA-Z]+$", "", filename)
        if not doc_name:
            doc_name = "Untitled Document"

        # 步骤1：收集文档中的所有块（段落和表格），保持文档顺序
        try:
            for i, block in enumerate(self.doc._element.body):
                if block.tag.endswith("p"):  # 段落
                    p = Paragraph(block, self.doc)
                    blocks.append(("p", i, p))
                elif block.tag.endswith("tbl"):  # 表格
                    blocks.append(("t", i, None))  # 表格对象稍后获取
        except Exception as e:
            logging.error(f"Error collecting blocks: {e}")
            return ""

        # 步骤2：定位目标表格的位置
        target_table_pos = -1
        table_count = 0
        for i, (block_type, pos, _) in enumerate(blocks):
            if block_type == "t":
                if table_count == table_index:
                    target_table_pos = pos
                    break
                table_count += 1

        if target_table_pos == -1:
            return ""  # 未找到目标表格

        # 步骤3：从表格位置向前搜索最近的标题段落
        nearest_title = None
        for i in range(len(blocks) - 1, -1, -1):
            block_type, pos, block = blocks[i]
            if pos >= target_table_pos:  # 跳过表格之后的块
                continue

            if block_type != "p":
                continue

            # 检查是否为标题样式
            if block.style and block.style.name and re.search(r"Heading\s*(\d+)", block.style.name, re.I):
                try:
                    level_match = re.search(r"(\d+)", block.style.name)
                    if level_match:
                        level = int(level_match.group(1))
                        if level <= 7:  # 支持最多 7 级标题
                            title_text = block.text.strip()
                            if title_text:  # 避免空标题
                                nearest_title = (level, title_text)
                                break
                except Exception as e:
                    logging.error(f"Error parsing heading level: {e}")

        # 步骤4：如果找到了最近的标题，递归查找所有父级标题
        if nearest_title:
            # 添加当前标题
            titles.append(nearest_title)
            current_level = nearest_title[0]

            # 查找所有父级标题，允许跨级搜索
            while current_level > 1:
                found = False
                for i in range(len(blocks) - 1, -1, -1):
                    block_type, pos, block = blocks[i]
                    if pos >= target_table_pos:  # 跳过表格之后的块
                        continue

                    if block_type != "p":
                        continue

                    if block.style and re.search(r"Heading\s*(\d+)", block.style.name, re.I):
                        try:
                            level_match = re.search(r"(\d+)", block.style.name)
                            if level_match:
                                level = int(level_match.group(1))
                                # 查找更高层级的标题
                                if level < current_level:
                                    title_text = block.text.strip()
                                    if title_text:  # 避免空标题
                                        titles.append((level, title_text))
                                        current_level = level
                                        found = True
                                        break
                        except Exception as e:
                            logging.error(f"Error parsing parent heading: {e}")

                if not found:  # 如果没有找到父级标题，退出循环
                    break

            # 步骤5：按层级排序（从高到低）
            titles.sort(key=lambda x: x[0])
            # 组织层级结构（从最高级到最低级）
            hierarchy = [doc_name] + [t[1] for t in titles]
            return " > ".join(hierarchy)

        return ""

    def __call__(self, filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER):
        self.doc = Document(filename) if not binary else Document(BytesIO(binary))
        pn = 0  # 当前页码
        lines = []  # 存储所有元素
        last_image = None  # 临时存储未关联的图片
        table_idx = 0  # 表格计数器

        def flush_last_image():
            """
            将未关联的图片输出为独立元素

            当遇到无法与文本关联的图片时，将其作为独立的图片元素添加到结果中。
            """
            nonlocal last_image, lines
            if last_image is not None:
                lines.append({"text": "", "image": last_image, "table": None, "style": "Image"})
                last_image = None

        # 遍历文档中的所有块（段落和表格）
        for block in self.doc._element.body:
            # 检查页码范围
            if pn > to_page:
                break

            # 处理段落
            if block.tag.endswith("p"):
                p = Paragraph(block, self.doc)

                # 只处理指定页码范围内的内容
                if from_page <= pn < to_page:
                    text = p.text.strip()
                    style_name = p.style.name if p.style else ""

                    if text:
                        # 处理说明文字（Caption 样式）
                        if style_name == "Caption":
                            former_image = None

                            # 尝试与上一行的图片关联
                            if lines and lines[-1].get("image") and lines[-1].get("style") != "Caption":
                                former_image = lines[-1].get("image")
                                lines.pop()

                            # 或者与临时存储的图片关联
                            elif last_image is not None:
                                former_image = last_image
                                last_image = None

                            # 添加说明文字元素
                            lines.append(
                                {
                                    "text": self.__clean(text),
                                    "image": former_image if former_image else None,
                                    "table": None,
                                }
                            )

                        # 处理普通段落
                        else:
                            # 先输出未关联的图片
                            flush_last_image()
                            # 添加文本元素
                            lines.append(
                                {
                                    "text": self.__clean(text),
                                    "image": None,
                                    "table": None,
                                }
                            )

                            # 检查段落中是否包含图片
                            current_image = self.get_picture(self.doc, p)
                            if current_image is not None:
                                lines.append(
                                    {
                                        "text": "",
                                        "image": current_image,
                                        "table": None,
                                    }
                                )

                    else:
                        # 空段落，检查是否包含图片
                        current_image = self.get_picture(self.doc, p)
                        if current_image is not None:
                            last_image = current_image

                # 通过 run 中的标签追踪页码变化
                for run in p.runs:
                    xml = run._element.xml
                    if "lastRenderedPageBreak" in xml:
                        pn += 1
                        continue
                    if "w:br" in xml and 'type="page"' in xml:
                        pn += 1

            # 处理表格
            elif block.tag.endswith("tbl"):
                # 检查页码范围
                if pn < from_page or pn > to_page:
                    table_idx += 1
                    continue

                # 先输出未关联的图片
                flush_last_image()

                # 解析表格
                tb = DocxTable(block, self.doc)
                # 获取表格所在的标题层级
                title = self.__get_nearest_title(table_idx, filename)
                html = "<table>"
                if title:
                    html += f"<caption>Table Location: {title}</caption>"

                # 转换表格行为 HTML
                for r in tb.rows:
                    html += "<tr>"
                    col_idx = 0
                    try:
                        while col_idx < len(r.cells):
                            span = 1
                            c = r.cells[col_idx]
                            # 检测合并单元格
                            for j in range(col_idx + 1, len(r.cells)):
                                if c.text == r.cells[j].text:
                                    span += 1
                                    col_idx = j
                                else:
                                    break
                            col_idx += 1
                            html += f"<td>{c.text}</td>" if span == 1 else f"<td colspan='{span}'>{c.text}</td>"
                    except Exception as e:
                        logging.warning(f"Error parsing table, ignore: {e}")
                    html += "</tr>"
                html += "</table>"

                # 添加表格元素
                lines.append({"text": "", "image": None, "table": html})
                table_idx += 1

        # 输出剩余的未关联图片
        flush_last_image()

        # 转换为三元组格式
        new_line = [(line.get("text"), line.get("image"), line.get("table")) for line in lines]

        return new_line

    def to_markdown(self, filename=None, binary=None, inline_images: bool = True):
        """
        This function uses mammoth, licensed under the BSD 2-Clause License.
        """

        import base64
        import uuid

        import mammoth
        from markdownify import markdownify

        docx_file = BytesIO(binary) if binary else open(filename, "rb")

        def _convert_image_to_base64(image):
            try:
                with image.open() as image_file:
                    image_bytes = image_file.read()
                encoded = base64.b64encode(image_bytes).decode("utf-8")
                base64_url = f"data:{image.content_type};base64,{encoded}"

                alt_name = "image"
                alt_name = f"img_{uuid.uuid4().hex[:8]}"

                return {"src": base64_url, "alt": alt_name}
            except Exception as e:
                logging.warning(f"Failed to convert image to base64: {e}")
                return {"src": "", "alt": "image"}

        try:
            if inline_images:
                result = mammoth.convert_to_html(docx_file, convert_image=mammoth.images.img_element(_convert_image_to_base64))
            else:
                result = mammoth.convert_to_html(docx_file)

            html = result.value

            markdown_text = markdownify(html)
            return markdown_text

        finally:
            if not binary:
                docx_file.close()


class Pdf(PdfParser):
    """
    PDF 文档解析器

    继承自 PdfParser，专门用于解析 PDF 格式文档。

    Note:
        - 支持 OCR 文字识别
        - 支持布局分析
        - 支持表格提取
        - 支持图片提取
        - 可选择是否分离表格和图片
    """

    def __init__(self):
        super().__init__()

    def __call__(self, filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, zoomin=3, callback=None, separate_tables_figures=False):
        start = timer()
        first_start = start
        callback(msg="OCR started")
        # 步骤1：OCR 文字识别
        self.__images__(filename if not binary else binary, zoomin, from_page, to_page, callback)
        callback(msg="OCR finished ({:.2f}s)".format(timer() - start))
        logging.info("OCR({}~{}): {:.2f}s".format(from_page, to_page, timer() - start))

        start = timer()
        # 步骤2：布局分析
        self._layouts_rec(zoomin)
        callback(0.63, "Layout analysis ({:.2f}s)".format(timer() - start))

        start = timer()
        # 步骤3：表格分析
        self._table_transformer_job(zoomin)
        callback(0.65, "Table analysis ({:.2f}s)".format(timer() - start))

        start = timer()
        # 步骤4：文本合并
        self._text_merge(zoomin=zoomin)
        callback(0.67, "Text merged ({:.2f}s)".format(timer() - start))

        # 步骤5：根据参数选择提取模式
        if separate_tables_figures:
            # 分别提取表格和图片
            tbls, figures = self._extract_table_figure(True, zoomin, True, True, True)
            self._concat_downward()
            logging.info("layouts cost: {}s".format(timer() - first_start))
            return [(b["text"], self._line_tag(b, zoomin)) for b in self.boxes], tbls, figures
        else:
            # 只提取表格
            tbls = self._extract_table_figure(True, zoomin, True, True)
            self._naive_vertical_merge()
            self._concat_downward()
            # self._final_reading_order_merge()  # 可选：最终阅读顺序合并
            # self._filter_forpages()  # 可选：过滤页面
            logging.info("layouts cost: {}s".format(timer() - first_start))
            return [(b["text"], self._line_tag(b, zoomin)) for b in self.boxes], tbls


class Markdown(MarkdownParser):
    """
    Markdown 文档解析器

    继承自 MarkdownParser，专门用于解析 Markdown 格式文档。

    Note:
        - 支持提取图片链接
        - 支持提取超链接
        - 支持表格提取
        - 支持图片下载和缓存
        - 支持转换为 HTML
    """

    def md_to_html(self, sections):
        """
        将 Markdown 文本转换为 HTML BeautifulSoup 对象

        Args:
            sections: Markdown 文本，可以是字符串或字符串列表

        Returns:
            BeautifulSoup: HTML 解析对象，失败时返回空列表

        Note:
            - 只处理第一个元素（如果是列表）
            - 使用 markdown 库进行转换
        """
        if not sections:
            return []
        if isinstance(sections, type("")):
            text = sections
        elif isinstance(sections[0], type("")):
            text = sections[0]
        else:
            return []

        from bs4 import BeautifulSoup

        html_content = markdown(text)
        soup = BeautifulSoup(html_content, "html.parser")
        return soup

    def get_hyperlink_urls(self, soup):
        """
        从 HTML 中提取所有超链接 URL

        Args:
            soup: BeautifulSoup HTML 对象

        Returns:
            set: URL 集合，如果 soup 为空则返回空集合
        """
        if soup:
            return set([a.get("href") for a in soup.find_all("a") if a.get("href")])
        return []

    def extract_image_urls_with_lines(self, text):
        """
        从 Markdown 文本中提取图片 URL 及其所在行号

        该函数支持两种格式的图片引用：
        1. Markdown 格式：![alt](url)
        2. HTML 格式：<img src="url">

        Args:
            text: Markdown 文本内容

        Returns:
            list: 图片信息列表，每个元素为 {"url": str, "line": int}
                - url: 图片 URL
                - line: 所在行号（从 0 开始）

        Processing Steps:
            1. 使用正则表达式逐行匹配 Markdown 和 HTML 图片格式
            2. 使用 BeautifulSoup 解析 HTML，查找跨行的图片标签
            3. 根据位置计算图片所在的行号
            4. 去重，避免重复提取
        """
        md_img_re = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
        html_img_re = re.compile(r'src=["\\\']([^"\\\'>\\s]+)', re.IGNORECASE)
        urls = []
        seen = set()
        lines = text.splitlines()

        # 步骤1：逐行匹配图片 URL
        for idx, line in enumerate(lines):
            for url in md_img_re.findall(line):
                if (url, idx) not in seen:
                    urls.append({"url": url, "line": idx})
                    seen.add((url, idx))
            for url in html_img_re.findall(line):
                if (url, idx) not in seen:
                    urls.append({"url": url, "line": idx})
                    seen.add((url, idx))

        # 步骤2：处理跨行的图片标签（HTML 格式）
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(text, "html.parser")
            newline_offsets = [m.start() for m in re.finditer(r"\n", text)] + [len(text)]
            for img_tag in soup.find_all("img"):
                src = img_tag.get("src")
                if not src:
                    continue

                tag_str = str(img_tag)
                pos = text.find(tag_str)
                if pos == -1:
                    # fallback: 直接查找 URL
                    pos = max(text.find(src), 0)

                # 根据位置计算行号
                line_no = 0
                for i, off in enumerate(newline_offsets):
                    if pos <= off:
                        line_no = i
                        break

                if (src, line_no) not in seen:
                    urls.append({"url": src, "line": line_no})
                    seen.add((src, line_no))
        except Exception as e:
            logging.error("Failed to extract image urls: {}".format(e))
            pass

        return urls

    def load_images_from_urls(self, urls, cache=None):
        """
        从 URL 加载图片

        支持本地文件路径和 HTTP/HTTPS URL。
        使用缓存避免重复加载相同的图片。

        Args:
            urls: URL 列表或包含 "url" 键的字典列表
            cache: 图片缓存字典（可选）

        Returns:
            tuple: (images, cache)
                - images: PIL Image 对象列表
                - cache: 更新后的缓存字典

        Note:
            - HTTP 请求超时时间为 30 秒
            - 图片会被转换为 RGB 格式
            - 加载失败的图片在缓存中值为 None
        """
        import requests
        from pathlib import Path

        cache = cache or {}
        images = []

        for url in urls:
            # 从字典或字符串中提取 URL
            url_str = url if isinstance(url, str) else url.get("url")

            if url_str in cache:
                if cache[url_str]:
                    images.append(cache[url_str])
                continue

            img_obj = None
            try:
                # 处理网络图片
                if url_str.startswith(("http://", "https://")):
                    response = requests.get(url_str, stream=True, timeout=30)
                    if response.status_code == 200 and response.headers.get("Content-Type", "").startswith("image/"):
                        img_obj = Image.open(BytesIO(response.content)).convert("RGB")
                # 处理本地图片
                else:
                    local_path = Path(url_str)
                    if local_path.exists():
                        img_obj = Image.open(url_str).convert("RGB")
                    else:
                        logging.warning(f"Local image file not found: {url_str}")
            except Exception as e:
                logging.error(f"Failed to download/open image from {url_str}: {e}")

            cache[url_str] = img_obj
            if img_obj:
                images.append(img_obj)

        return images, cache

    def __call__(self, filename, binary=None, separate_tables=True, delimiter=None, return_section_images=False):
        """
        解析 Markdown 文档

        该方法是 Markdown 解析的核心入口，提取文本、表格和图片。

        Args:
            filename: Markdown 文件名或路径
            binary: Markdown 文件的二进制内容（可选）
            separate_tables: 是否分离表格（默认 True）
            delimiter: 自定义分隔符（可选）
            return_section_images: 是否返回每个段落关联的图片（默认 False）

        Returns:
            tuple:
                如果 return_section_images=True:
                    (sections, tbls, section_images)
                否则:
                    (sections, tbls)
                    - sections: 段落列表，每个元素为 (text, "") 元组
                    - tbls: 表格列表
                    - section_images: 每个段落关联的图片列表

        Processing Steps:
            1. 读取并解码 Markdown 文本
            2. 提取表格和剩余文本
            3. 提取图片 URL 和所在行号
            4. 按分隔符分段（使用 MarkdownElementExtractor）
            5. 为每个段落加载关联的图片
            6. 合并同一段落的多个图片
            7. 转换表格为 HTML 格式
            8. 返回解析结果

        Note:
            - 使用 find_codec 自动检测文件编码
            - 图片会使用 reduce(concat_img, ...) 合并
            - 表格使用 markdown.extensions.tables 扩展
        """
        # 步骤1：读取文件内容
        if binary:
            encoding = find_codec(binary)
            txt = binary.decode(encoding, errors="ignore")
        else:
            with open(filename, "r") as f:
                txt = f.read()

        # 步骤2：提取表格和剩余文本
        remainder, tables = self.extract_tables_and_remainder(f"{txt}\n", separate_tables=separate_tables)

        # 步骤3：提取图片引用
        # 注意：使用完整文本而非 remainder，以获取所有图片
        extractor = MarkdownElementExtractor(txt)
        image_refs = self.extract_image_urls_with_lines(txt)

        # 步骤4：按分隔符分段
        element_sections = extractor.extract_elements(delimiter, include_meta=True)

        # 步骤5-6：为每个段落加载并合并图片
        sections = []
        section_images = []
        image_cache = {}

        for element in element_sections:
            content = element["content"]
            start_line = element["start_line"]
            end_line = element["end_line"]

            # 查找该段落范围内的所有图片
            urls_in_section = [ref["url"] for ref in image_refs if start_line <= ref["line"] <= end_line]

            # 加载图片
            imgs = []
            if urls_in_section:
                imgs, image_cache = self.load_images_from_urls(urls_in_section, image_cache)

            # 合并多个图片
            combined_image = None
            if imgs:
                combined_image = reduce(concat_img, imgs) if len(imgs) > 1 else imgs[0]

            sections.append((content, ""))
            section_images.append(combined_image)

        # 步骤7：转换表格为 HTML
        tbls = []
        for table in tables:
            tbls.append(((None, markdown(table, extensions=["markdown.extensions.tables"])), ""))

        # 步骤8：返回结果
        if return_section_images:
            return sections, tbls, section_images
        return sections, tbls


def load_from_xml_v2(baseURI, rels_item_xml):
    """
    从 XML 加载 DOCX 关系列表（修复版本）

    该函数是 python-docx 库的修复版本，用于处理 DOCX 文件中的关系引用。
    修复了原始版本在处理某些 DOCX 文件时出现的 "word/NULL" 错误。

    Args:
        baseURI: 基础 URI，用于解析相对路径
        rels_item_xml: 关系 XML 内容，描述文档中的各种关系（超链接、图片等）

    Returns:
        _SerializedRelationships: 关系列表对象，如果 rels_item_xml 为 None 则返回空集合

    Processing Steps:
        1. 创建空的关系列表对象
        2. 如果提供了 XML 内容，解析 XML
        3. 遍历所有关系元素
        4. 过滤掉无效的关系（NULL 引用、内部引用等）
        5. 将有效关系添加到列表中

    Filtered Relationships:
        - "../NULL": 无效的 NULL 引用
        - "NULL": 无效的 NULL 引用
        - "#": 内部引用（如书签）

    Note:
        该函数用于替换 _SerializedRelationships.load_from_xml，
        修复参考：https://github.com/python-openxml/python-docx/issues/1105#issuecomment-1298075246
    """
    srels = _SerializedRelationships()
    if rels_item_xml is not None:
        rels_elm = parse_xml(rels_item_xml)
        for rel_elm in rels_elm.Relationship_lst:
            # 过滤掉无效的关系引用
            if rel_elm.target_ref in ("../NULL", "NULL") or rel_elm.target_ref.startswith("#"):
                continue
            # 添加有效关系到列表
            srels._srels.append(_SerializedRelationship(baseURI, rel_elm))
    return srels


def chunk(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, lang="Chinese", callback=None, **kwargs):
    """
    文档分块函数（核心入口）

    支持的文件格式：docx, pdf, excel, txt, markdown, html, epub, json 等。
    使用朴素方法对文件进行分块：
    1. 使用分隔符将连续文本切分为片段
    2. 将这些片段合并为不超过最大 token 数的块

    Args:
        filename: 文件名或文件路径
        binary: 文件二进制内容（可选）
        from_page: 起始页码（默认 0）
        to_page: 结束页码（默认 100000）
        lang: 语言（默认 "Chinese"）
        callback: 进度回调函数
        **kwargs: 其他配置参数
            - parser_config: 解析器配置
                - chunk_token_num: 块最大 token 数（默认 512）
                - delimiter: 分隔符（默认 "\n!?。；！？"）
                - layout_recognize: 布局识别器
                - analyze_hyperlink: 是否分析超链接
                - table_context_size: 表格上下文大小
                - image_context_size: 图片上下文大小
                - overlapped_percent: 重叠百分比
            - tenant_id: 租户 ID
            - is_root: 是否为根调用

    Returns:
        list: 分块结果列表，每个元素为字典形式

    Note:
        - 自动检测文件格式并选择合适的解析器
        - 支持嵌套文件（如嵌入的文档）
        - 支持超链接提取和递归解析
    """
    # ========== 初始化变量 ==========
    urls = set()  # 提取的超链接 URL 集合
    url_res = []  # 超链接解析结果列表

    lang = lang or "Chinese"
    is_english = lang.lower() == "english"  # is_english(cks)
    parser_config = kwargs.get("parser_config", {"chunk_token_num": 512, "delimiter": "\n!?。；！？", "layout_recognize": "DeepDOC", "analyze_hyperlink": True})

    # 获取解析器配置，提供默认值
    parser_config = kwargs.get("parser_config", {
        "chunk_token_num": 512,           # 块的最大 token 数
        "delimiter": "\n!?。；！？",      # 分句分隔符
        "layout_recognize": "DeepDOC",    # 布局识别器
        "analyze_hyperlink": True        # 是否分析超链接
    })

    # ========== 步骤2：处理子元素分隔符 ==========
    # 子元素分隔符用于处理嵌套内容（如代码块、表格等）
    # 处理转义字符和自定义分隔符（反引号包裹）
    child_deli = (parser_config.get("children_delimiter") or "").encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
    cust_child_deli = re.findall(r"`([^`]+)`", child_deli)  # 提取反引号中的自定义分隔符
    child_deli = "|".join(re.sub(r"`([^`]+)`", "", child_deli))  # 移除反引号标记
    if cust_child_deli:
        cust_child_deli = sorted(set(cust_child_deli), key=lambda x: -len(x))  # 按长度降序排序
        cust_child_deli = "|".join(re.escape(t) for t in cust_child_deli if t)  # 转义特殊字符
        child_deli += cust_child_deli  # 合并自定义分隔符

    # ========== 步骤3：获取上下文配置 ==========
    is_markdown = False  # 标记是否为 Markdown 文件
    # 表格上下文大小：表格前后保留的文本行数
    table_context_size = max(0, int(parser_config.get("table_context_size", 0) or 0))
    # 图片上下文大小：图片描述保留的文本行数
    image_context_size = max(0, int(parser_config.get("image_context_size", 0) or 0))

    # ========== 步骤4：初始化文档元数据 ==========
    # 创建文档基础信息
    doc = {
        "docnm_kwd": filename,  # 文档名称
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))  # 标题分词
    }
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])  # 标题细粒度分词

    res = []  # 最终结果列表
    pdf_parser = None  # PDF 解析器实例
    section_images = None  # 段落关联的图片

    # ========== 步骤5：提取嵌入文件 ==========
    is_root = kwargs.get("is_root", True)  # 是否为根调用（非递归）
    embed_res = []  # 嵌入文件的解析结果

    if is_root:
        # 只在根调用时提取嵌入文件（避免递归时重复提取）
        embeds = []
        if binary is not None:
            embeds = extract_embed_file(binary)  # 从二进制数据中提取嵌入文件
        else:
            raise Exception("Embedding extraction from file path is not supported.")

        # 递归解析每个嵌入文件
        for embed_filename, embed_bytes in embeds:
            try:
                # 递归调用 chunk 函数解析嵌入文件
                sub_res = chunk(embed_filename, binary=embed_bytes, lang=lang, callback=callback, is_root=False, **kwargs) or []
                embed_res.extend(sub_res)
            except Exception as e:
                error_msg = f"Failed to chunk embed {embed_filename}: {e}"
                logging.error(error_msg)
                if callback:
                    callback(0.05, error_msg)
                continue

    # ========== 文件类型路由：根据文件扩展名选择解析策略 ==========

    # ------------------ DOCX 文档处理 ------------------
    if re.search(r"\.docx$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 步骤1：超链接分析（可选）
        if parser_config.get("analyze_hyperlink", False) and is_root:
            urls = extract_links_from_docx(binary)  # 提取文档中的所有超链接
            # 递归解析超链接指向的网页
            for index, url in enumerate(urls):
                html_bytes, metadata = extract_html(url)
                if not html_bytes:
                    continue
                try:
                    # 尝试按原始 URL 解析
                    sub_url_res = chunk(url, html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
                except Exception as e:
                    logging.info(f"Failed to chunk url in registered file type {url}: {e}")
                    # 失败时按 HTML 格式解析
                    sub_url_res = chunk(f"{index}.html", html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
                url_res.extend(sub_url_res)

        # 步骤2：修复 python-docx 的已知问题
        # fix "There is no item named 'word/NULL' in the archive"
        # 参考：https://github.com/python-openxml/python-docx/issues/1105#issuecomment-1298075246
        _SerializedRelationships.load_from_xml = load_from_xml_v2

        # 步骤3：解析 DOCX 结构
        # sections 格式：[(text, image, table), ...]
        sections = Docx()(filename, binary)
        # 规范化 RTL（从右到左）文本
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        # 步骤4：文本分块（DOCX 专用）
        # chunks: 分块列表（每个元素为字典）
        # images: 包含图片的 chunk 索引列表
        chunks, images = naive_merge_docx(
            sections,
            int(parser_config.get("chunk_token_num", 128)),
            parser_config.get("delimiter", "\n!?。；！？"),
            table_context_size,
            image_context_size
        )

        # 步骤5：视觉增强（使用视觉模型理解图片）
        vision_figure_parser_docx_wrapper_naive(chunks=chunks, idx_lst=images, callback=callback, **kwargs)

        callback(0.8, "Finish parsing.")
        st = timer()

        # 步骤6：分词包装
        res.extend(doc_tokenize_chunks_with_images(chunks, doc, is_english, child_delimiters_pattern=child_deli))
        logging.info("naive_merge({}): {}".format(filename, timer() - st))

        # 合并结果
        res.extend(embed_res)
        res.extend(url_res)
        return res

    # ------------------ PDF 文档处理 ------------------
    elif re.search(r"\.pdf$", filename, re.IGNORECASE):
        layout_recognizer, parser_model_name = normalize_layout_recognizer(parser_config.get("layout_recognize", "DeepDOC"))
        opendataloader_llm_name = kwargs.pop("opendataloader_llm_name", None)
        if layout_recognizer == "OpenDataLoader" and parser_model_name:
            opendataloader_llm_name = parser_model_name

        # 步骤2：超链接提取（可选）
        if parser_config.get("analyze_hyperlink", False) and is_root:
            urls = extract_links_from_pdf(binary)  # 提取 PDF 中的超链接

        # 步骤3：确定解析器
        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "PlainText"

        # 从 PARSERS 字典中选择解析器（小写键名）
        name = layout_recognizer.strip().lower()
        parser = PARSERS.get(name, by_plaintext)  # 默认使用纯文本解析器
        callback(0.1, "Start to parse.")

        # 步骤4：调用解析器
        sections, tables, pdf_parser = parser(
            filename=filename,
            binary=binary,
            from_page=from_page,
            to_page=to_page,
            lang=lang,
            callback=callback,
            layout_recognizer=layout_recognizer,
            mineru_llm_name=parser_model_name,
            paddleocr_llm_name=parser_model_name,
            opendataloader_llm_name=opendataloader_llm_name,
            **kwargs,
        )
        # 规范化 RTL 文本
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        # 步骤5：检查解析结果
        if not sections and not tables:
            return []

        # 步骤6：表格和图片上下文增强
        if table_context_size or image_context_size:
            tables = append_context2table_image4pdf(sections, tables, image_context_size)

        if name in ["tcadp", "docling", "mineru", "paddleocr", "opendataloader"]:
            if int(parser_config.get("chunk_token_num", 0)) <= 0:
                parser_config["chunk_token_num"] = 0

        # 步骤8：表格分词
        res = tokenize_table(tables, doc, is_english)
        callback(0.8, "Finish parsing.")

    # ------------------ Excel/CSV 表格处理 ------------------
    elif re.search(r"\.(csv|xlsx?)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 步骤1：检查是否使用 TCADP Parser（腾讯云解析器）
        layout_recognizer = parser_config.get("layout_recognizer", "DeepDOC")
        if layout_recognizer == "TCADP Parser":
            # 使用腾讯云 TCADP 解析器
            table_result_type = parser_config.get("table_result_type", "1")  # 表格输出格式
            markdown_image_response_type = parser_config.get("markdown_image_response_type", "1")  # 图片响应类型
            tcadp_parser = TCADPParser(table_result_type=table_result_type, markdown_image_response_type=markdown_image_response_type)

            if not tcadp_parser.check_installation():
                callback(-1, "TCADP parser not available. Please check Tencent Cloud API configuration.")
                return res

            # 确定文件类型
            file_type = "XLSX" if re.search(r"\.xlsx?$", filename, re.IGNORECASE) else "CSV"

            # 调用 TCADP 解析器
            sections, tables = tcadp_parser.parse_pdf(
                filepath=filename,
                binary=binary,
                callback=callback,
                output_dir=os.environ.get("TCADP_OUTPUT_DIR", ""),
                file_type=file_type
            )
            sections = _normalize_section_text_for_rtl_presentation_forms(sections)
            parser_config["chunk_token_num"] = 0  # 禁用后续分块（表格已是独立行）
            res = tokenize_table(tables, doc, is_english)
            callback(0.8, "Finish parsing.")
        else:
            # 使用默认的 DeepDOC 解析器
            excel_parser = ExcelParser()

            if parser_config.get("html4excel"):
                # 输出 HTML 格式（每 12 行一组）
                sections = [(_, "") for _ in excel_parser.html(binary, 12) if _]
                parser_config["chunk_token_num"] = 0  # 禁用后续分块
            else:
                # 输出纯文本格式（每行独立）
                sections = [(_, "") for _ in excel_parser(binary) if _]

            sections = _normalize_section_text_for_rtl_presentation_forms(sections)

    # ------------------ TXT 纯文本/代码文件处理 ------------------
    elif re.search(r"\.(txt|py|js|java|c|cpp|h|php|go|ts|sh|cs|kt|sql)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 使用纯文本解析器
        sections = TxtParser()(
            filename,
            binary,
            parser_config.get("chunk_token_num", 128),
            parser_config.get("delimiter", "\n!?;。；！？")
        )
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        # 调试输出（开发模式）
        print("\n", "-"*150, "\n")
        print(sections)
        print("\n", "-"*150, "\n")

        callback(0.8, "Finish parsing.")

    # ------------------ Markdown 文档处理 ------------------
    elif re.search(r"\.(md|markdown|mdx)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 步骤1：解析 Markdown 结构
        markdown_parser = Markdown(int(parser_config.get("chunk_token_num", 128)))
        sections, tables, section_images = markdown_parser(
            filename,
            binary,
            separate_tables=False,  # 不分离表格
            delimiter=parser_config.get("delimiter", "\n!?;。；！？"),
            return_section_images=True,  # 返回每个段落关联的图片
        )
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        # 标记为 Markdown（后续使用专用分块逻辑）
        is_markdown = True

        # 步骤2：视觉模型检测（可选）
        try:
            vision_model_config = get_tenant_default_model_by_type(kwargs["tenant_id"], LLMType.IMAGE2TEXT)
            vision_model = LLMBundle(kwargs["tenant_id"], vision_model_config)
            callback(0.2, "Visual model detected. Attempting to enhance figure extraction...")
        except Exception as e:
            logging.warning(f"Failed to detect figure extraction: {e}")
            vision_model = None

        # 步骤3：图片内容增强（使用视觉模型）
        if vision_model:
            # 为每个段落的图片生成描述
            for idx, (section_text, _) in enumerate(sections):
                images = []
                if section_images and len(section_images) > idx and section_images[idx] is not None:
                    images.append(section_images[idx])

                if images and len(images) > 0:
                    # 合并多个图片
                    combined_image = reduce(concat_img, images) if len(images) > 1 else images[0]
                    if section_images:
                        section_images[idx] = combined_image
                    else:
                        section_images = [None] * len(sections)
                        section_images[idx] = combined_image

                    # 使用视觉模型生成图片描述
                    markdown_vision_parser = VisionFigureParser(
                        vision_model=vision_model,
                        figures_data=[((combined_image, ["markdown image"]), [(0, 0, 0, 0, 0)])],
                        **kwargs
                    )
                    boosted_figures = markdown_vision_parser(callback=callback)
                    # 将图片描述追加到段落文本
                    sections[idx] = (
                        section_text + "\n\n" + "\n\n".join([fig[0][1] for fig in boosted_figures]),
                        sections[idx][1]
                    )

        else:
            logging.warning("No visual model detected. Skipping figure parsing enhancement.")

        # 步骤4：超链接提取（可选）
        if parser_config.get("hyperlink_urls", False) and is_root:
            for idx, (section_text, _) in enumerate(sections):
                soup = markdown_parser.md_to_html(section_text)
                hyperlink_urls = markdown_parser.get_hyperlink_urls(soup)
                urls.update(hyperlink_urls)

        # 步骤5：表格分词
        res = tokenize_table(tables, doc, is_english)
        callback(0.8, "Finish parsing.")

    # ------------------ HTML 网页处理 ------------------
    elif re.search(r"\.(htm|html)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 使用 HTML 解析器
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = HtmlParser()(filename, binary, chunk_token_num)
        sections = [(_, "") for _ in sections if _]  # 转换为统一格式
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    # ------------------ EPUB 电子书处理 ------------------
    elif re.search(r"\.epub$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 使用 EPUB 解析器
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = EpubParser()(filename, binary, chunk_token_num)
        sections = [(_, "") for _ in sections if _]  # 转换为统一格式
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    # ------------------ JSON 数据处理 ------------------
    elif re.search(r"\.(json|jsonl|ldjson)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 使用 JSON 解析器
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = JsonParser(chunk_token_num)(binary)
        sections = [(_, "") for _ in sections if _]  # 转换为统一格式
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    # ------------------ DOC 旧版 Word 文档处理 ------------------
    elif re.search(r"\.doc$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # 使用 Apache Tika 解析（需要安装 tika）
        try:
            from tika import parser as tika_parser
        except Exception as e:
            callback(0.8, f"tika not available: {e}. Unsupported .doc parsing.")
            logging.warning(f"tika not available: {e}. Unsupported .doc parsing for {filename}.")
            return []

        # 调用 Tika 解析器
        binary = BytesIO(binary)
        doc_parsed = tika_parser.from_buffer(binary)
        if doc_parsed.get("content", None) is not None:
            sections = doc_parsed["content"].split("\n")
            sections = [(_, "") for _ in sections if _]  # 转换为统一格式
            sections = _normalize_section_text_for_rtl_presentation_forms(sections)
            callback(0.8, "Finish parsing.")
        else:
            error_msg = f"tika.parser got empty content from {filename}."
            callback(0.8, error_msg)
            logging.warning(error_msg)
            return []

    # ------------------ 不支持的文件类型 ------------------
    else:
        raise NotImplementedError("file type not supported yet(pdf, xlsx, doc, docx, txt supported)")

    # ========== 通用分块逻辑 ==========
    # 对于非 DOCX、非表格类型的文件，使用通用分块逻辑
    st = timer()
    overlapped_percent = normalize_overlapped_percent(parser_config.get("overlapped_percent", 0))
    
    if is_markdown:
        merged_chunks = []
        merged_images = []
        chunk_limit = max(0, int(parser_config.get("chunk_token_num", 128)))

        current_text = ""
        current_tokens = 0
        current_image = None

        # 遍历所有段落，合并为不超过 token 限制的块
        for idx, sec in enumerate(sections):
            text = sec[0] if isinstance(sec, tuple) else sec
            sec_tokens = num_tokens_from_string(text)
            sec_image = section_images[idx] if section_images and idx < len(section_images) else None

            # Don't finalize chunk if current_text is a short header (force merge with next section)
            if current_text and not _is_short_header(current_text) and current_tokens + sec_tokens > chunk_limit:
                merged_chunks.append(current_text)
                merged_images.append(current_image)

                # 计算重叠部分（用于保留上下文）
                overlap_part = ""
                if overlapped_percent > 0:
                    overlap_len = int(len(current_text) * overlapped_percent / 100)
                    if overlap_len > 0:
                        overlap_part = current_text[-overlap_len:]

                # 重置当前块为重叠部分
                current_text = overlap_part
                current_tokens = num_tokens_from_string(current_text)
                current_image = current_image if overlap_part else None

            # 追加新段落到当前块
            if current_text:
                current_text += "\n" + text
            else:
                current_text = text
            current_tokens += sec_tokens

            # 合并图片
            if sec_image:
                current_image = concat_img(current_image, sec_image) if current_image else sec_image

        # 添加最后一个块
        if current_text:
            merged_chunks.append(current_text)
            merged_images.append(current_image)

        chunks = merged_chunks
        has_images = merged_images and any(img is not None for img in merged_images)

        # 根据是否有图片选择分词方法
        if has_images:
            res.extend(tokenize_chunks_with_images(chunks, doc, is_english, merged_images, child_delimiters_pattern=child_deli))
        else:
            res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser, child_delimiters_pattern=child_deli))

    # ------------------ 其他格式通用分块逻辑 ------------------
    else:
        # 检查是否所有图片都为空（是则清除）
        if section_images:
            if all(image is None for image in section_images):
                section_images = None

        # 根据是否有图片选择分块方法
        if section_images:
            # 使用带图片的分块算法
            chunks, images = naive_merge_with_images(
                sections,
                section_images,
                int(parser_config.get("chunk_token_num", 128)),
                parser_config.get("delimiter", "\n!?。；！？"),
                overlapped_percent
            )
            res.extend(tokenize_chunks_with_images(chunks, doc, is_english, images, child_delimiters_pattern=child_deli))
        else:
            # 使用标准分块算法
            chunks = naive_merge(
                sections,
                int(parser_config.get("chunk_token_num", 128)),
                parser_config.get("delimiter", "\n!?。；！？"),
                overlapped_percent
            )
            res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser, child_delimiters_pattern=child_deli))

    # ========== 超链接递归解析 ==========
    if urls and parser_config.get("analyze_hyperlink", False) and is_root:
        # 递归解析提取的超链接
        for index, url in enumerate(urls):
            html_bytes, metadata = extract_html(url)
            if not html_bytes:
                continue
            try:
                sub_url_res = chunk(url, html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
            except Exception as e:
                logging.info(f"Failed to chunk url in registered file type {url}: {e}")
                sub_url_res = chunk(f"{index}.html", html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
            url_res.extend(sub_url_res)

    logging.info("naive_merge({}): {}".format(filename, timer() - st))

    # ========== 合并所有结果 ==========
    if embed_res:
        res.extend(embed_res)  # 嵌入文件的解析结果
    if url_res:
        res.extend(url_res)  # 超链接的解析结果

    # 可选：为表格和图片添加上下文
    # if table_context_size or image_context_size:
    #    attach_media_context(res, table_context_size, image_context_size)

    # Attach PDF outline as transient metadata on the first chunk.
    # task_executor.py will extract and persist it as document metadata.
    if res and pdf_parser and getattr(pdf_parser, "outlines", None):
        res[0]["__outline__"] = [
            {"title": title, "depth": depth}
            for title, depth, *_ in pdf_parser.outlines
        ]

    return res


# ========== 命令行入口 ==========
if __name__ == "__main__":
    import sys

    def dummy(prog=None, msg=""):
        """
        空的进度回调函数（用于命令行测试）
        """
        pass

    # 从命令行参数获取文件路径，执行分块
    # 用法：python naive.py <文件路径>
    chunk(sys.argv[1], from_page=0, to_page=10, callback=dummy)
