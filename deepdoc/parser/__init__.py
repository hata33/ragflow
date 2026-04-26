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

"""deepdoc.parser 对外暴露的解析器入口。

这里统一导出各类文档解析器，方便上层按文件类型直接选择对应实现，
而不需要关心具体模块路径。
"""

from .docx_parser import RAGFlowDocxParser as DocxParser
from .epub_parser import RAGFlowEpubParser as EpubParser
from .excel_parser import RAGFlowExcelParser as ExcelParser
from .html_parser import RAGFlowHtmlParser as HtmlParser
from .json_parser import RAGFlowJsonParser as JsonParser
from .markdown_parser import MarkdownElementExtractor
from .markdown_parser import RAGFlowMarkdownParser as MarkdownParser
from .pdf_parser import PlainParser
from .pdf_parser import RAGFlowPdfParser as PdfParser
from .ppt_parser import RAGFlowPptParser as PptParser
from .txt_parser import RAGFlowTxtParser as TxtParser

# 控制 `from deepdoc.parser import *` 时允许导出的公共解析器名称。
__all__ = [
    "PdfParser",
    "PlainParser",
    "DocxParser",
    "EpubParser",
    "ExcelParser",
    "PptParser",
    "HtmlParser",
    "JsonParser",
    "MarkdownParser",
    "TxtParser",
    "MarkdownElementExtractor",
]
