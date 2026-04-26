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

"""解析器共用的小工具函数。"""

from io import BytesIO

from pypdf import PdfReader as pdf2_read

from rag.nlp import find_codec


def get_text(fnm: str, binary=None) -> str:
    """读取文本文件内容，兼容文件路径和二进制输入两种形式。"""

    txt = ""
    if binary is not None:
        encoding = find_codec(binary)
        txt = binary.decode(encoding, errors="ignore")
    else:
        with open(fnm, "r") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                txt += line
    return txt


def extract_pdf_outlines(source):
    """提取 PDF 目录书签。

    返回值中的每一项格式为 `(标题, 层级, 页码)`，页码从 1 开始。
    若 PDF 没有目录或解析失败，则返回空列表。
    """

    try:
        with pdf2_read(source if isinstance(source, str) else BytesIO(source)) as pdf:
            outlines = []

            def dfs(nodes, depth):
                # pypdf 的 outline 结构可能是嵌套列表，这里用 DFS 展平。
                for node in nodes:
                    if isinstance(node, list):
                        dfs(node, depth + 1)
                    else:
                        outlines.append((node["/Title"], depth, pdf.get_destination_page_number(node) + 1))

            dfs(pdf.outline, 0)
            return outlines
    except Exception:
        return []
