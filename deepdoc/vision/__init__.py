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
视觉模块（deepdoc.vision）包初始化文件。

本模块是 RAGFlow 文档视觉处理的核心入口，统一导出以下关键组件：
- OCR：光学字符识别引擎，包含文本检测和文本识别两个子模块
- Recognizer：通用识别器基类，提供预处理/后处理/排序/重叠检测等基础能力
- LayoutRecognizer：基于 YOLOv10 的版面布局识别器，检测文档中的标题、表格、图片等区域
- AscendLayoutRecognizer：基于华为昇腾 NPU 的版面布局识别器
- TableStructureRecognizer：表格结构识别器，用于解析表格的行列和单元格结构
- init_in_out：工具函数，用于批量加载输入文件（图片/PDF）并准备输出路径
"""

import io
import sys
import threading

import pdfplumber

from .ocr import OCR
from .recognizer import Recognizer
from .layout_recognizer import AscendLayoutRecognizer
from .layout_recognizer import LayoutRecognizer4YOLOv10 as LayoutRecognizer
from .table_structure_recognizer import TableStructureRecognizer

# pdfplumber 全局线程锁，防止多线程并发访问 pdfplumber 导致的崩溃问题
LOCK_KEY_pdfplumber = "global_shared_lock_pdfplumber"
if LOCK_KEY_pdfplumber not in sys.modules:
    sys.modules[LOCK_KEY_pdfplumber] = threading.Lock()


def init_in_out(args):
    """
    根据命令行参数初始化输入图像列表和对应的输出文件路径。

    支持的输入形式：
      - 单个图片文件（PNG、JPG 等）
      - 单个 PDF 文件（逐页转换为图像）
      - 包含上述文件的目录（递归遍历）

    Args:
        args: 命令行参数对象，需包含以下属性：
            - inputs (str): 输入文件或目录路径
            - output_dir (str): 输出目录路径

    Returns:
        tuple[list, list]: 返回 (images, outputs) 元组
            - images: PIL Image 对象列表（RGB 模式）
            - outputs: 与 images 一一对应的输出文件路径列表
    """
    import os
    import traceback

    from PIL import Image

    from common.file_utils import traversal_files

    images = []
    outputs = []

    if not os.path.exists(args.output_dir):
        os.mkdir(args.output_dir)

    def pdf_pages(fnm, zoomin=3):
        """
        将 PDF 文件的每一页转换为图像。

        Args:
            fnm (str): PDF 文件路径
            zoomin (int): 缩放倍数，默认为 3（即 216 DPI）
        """
        nonlocal outputs, images
        # 使用线程锁保护 pdfplumber 的并发访问
        with sys.modules[LOCK_KEY_pdfplumber]:
            pdf = pdfplumber.open(fnm)
            images = [p.to_image(resolution=72 * zoomin).annotated for i, p in enumerate(pdf.pages)]

        for i, page in enumerate(images):
            outputs.append(os.path.split(fnm)[-1] + f"_{i}.jpg")
        pdf.close()

    def images_and_outputs(fnm):
        """
        处理单个文件：PDF 调用 pdf_pages 转换，图片直接加载。

        Args:
            fnm (str): 文件路径
        """
        nonlocal outputs, images
        if fnm.split(".")[-1].lower() == "pdf":
            pdf_pages(fnm)
            return
        try:
            with open(fnm, "rb") as fp:
                binary = fp.read()
            images.append(Image.open(io.BytesIO(binary)).convert("RGB"))
            outputs.append(os.path.split(fnm)[-1])
        except Exception:
            traceback.print_exc()

    if os.path.isdir(args.inputs):
        # 输入为目录时递归遍历所有文件
        for fnm in traversal_files(args.inputs):
            images_and_outputs(fnm)
    else:
        images_and_outputs(args.inputs)

    # 将输出文件名拼接上输出目录的完整路径
    for i in range(len(outputs)):
        outputs[i] = os.path.join(args.output_dir, outputs[i])

    return images, outputs


__all__ = [
    "OCR",
    "Recognizer",
    "LayoutRecognizer",
    "AscendLayoutRecognizer",
    "TableStructureRecognizer",
    "init_in_out",
]
