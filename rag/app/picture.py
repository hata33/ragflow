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
图片和视频解析模块

本模块提供图片和视频文件的内容提取功能。

支持的文件格式：
- 图片：PNG, JPG, JPEG, GIF, BMP, WebP 等常见格式
- 视频：MP4, MOV, AVI, FLV, MPEG, MPG, WebM, WMV, 3GP, MKV

主要特点：
- OCR 文字识别
- 视觉模型图片描述
- 视频内容理解
- 自动选择最佳处理方式

使用场景：
- 图片内容索引
- 视频转录
- 图像知识库构建
"""

import asyncio
import io
import re

import numpy as np
from PIL import Image

from api.db.services.llm_service import LLMBundle
from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
from common.constants import LLMType
from common.string_utils import clean_markdown_block
from deepdoc.vision import OCR
from rag.nlp import attach_media_context, rag_tokenizer, tokenize

ocr = OCR()

# Gemini supported MIME types
VIDEO_EXTS = [".mp4", ".mov", ".avi", ".flv", ".mpeg", ".mpg", ".webm", ".wmv", ".3gp", ".3gpp", ".mkv"]


def chunk(filename, binary, tenant_id, lang, callback=None, **kwargs):
    """
    解析图片或视频文件

    对于图片：执行 OCR 和视觉模型描述
    对于视频：使用视觉模型进行内容理解

    Args:
        filename: 文件名
        binary: 文件的二进制内容
        tenant_id: 租户 ID，用于获取模型配置
        lang: 语言设置
        callback: 进度回调函数
        **kwargs: 其他配置参数

    Returns:
        list: 包含提取内容的单元素列表，失败时返回空列表

    Processing Steps:
        1. 检测文件类型（视频或图片）
        2. 视频：使用 CV LLM 进行内容理解
        3. 图片：
           - 执行 OCR 文字识别
           - 如果文字较少，使用视觉模型生成描述
           - 合并 OCR 结果和视觉描述
    """
    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename)),
    }
    eng = lang.lower() == "english"

    parser_config = kwargs.get("parser_config", {}) or {}
    image_ctx = max(0, int(parser_config.get("image_context_size", 0) or 0))

    if any(filename.lower().endswith(ext) for ext in VIDEO_EXTS):
        try:
            doc.update(
                {
                    "doc_type_kwd": "video",
                }
            )
            cv_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.IMAGE2TEXT)
            cv_mdl = LLMBundle(tenant_id, model_config=cv_model_config, lang=lang)
            video_prompt = str(parser_config.get("video_prompt", "") or "")
            ans = asyncio.run(
                cv_mdl.async_chat(system="", history=[], gen_conf={}, video_bytes=binary, filename=filename, video_prompt=video_prompt))
            callback(0.8, "CV LLM respond: %s ..." % ans[:32])
            ans += "\n" + ans
            tokenize(doc, ans, eng)
            return [doc]
        except Exception as e:
            callback(prog=-1, msg=str(e))
    else:
        img = Image.open(io.BytesIO(binary)).convert("RGB")
        doc.update(
            {
                "image": img,
                "doc_type_kwd": "image",
            }
        )
        bxs = ocr(np.array(img))
        txt = "\n".join([t[0] for _, t in bxs if t[0]])
        callback(0.4, "Finish OCR: (%s ...)" % txt[:12])
        if (eng and len(txt.split()) > 32) or len(txt) > 32:
            tokenize(doc, txt, eng)
            callback(0.8, "OCR results is too long to use CV LLM.")
            return attach_media_context([doc], 0, image_ctx)

        try:
            callback(0.4, "Use CV LLM to describe the picture.")
            cv_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.IMAGE2TEXT)
            cv_mdl = LLMBundle(tenant_id, model_config=cv_model_config, lang=lang)
            with io.BytesIO() as img_binary:
                img.save(img_binary, format="JPEG")
                img_binary.seek(0)
                ans = cv_mdl.describe(img_binary.read())
            callback(0.8, "CV LLM respond: %s ..." % ans[:32])
            txt += "\n" + ans
            tokenize(doc, txt, eng)
            return attach_media_context([doc], 0, image_ctx)
        except Exception as e:
            callback(prog=-1, msg=str(e))

    return []


def vision_llm_chunk(binary, vision_model, prompt=None, callback=None):
    """
    使用视觉语言模型处理图片

    通过 VLM (Vision Language Model) 将图片转换为 Markdown 文本。

    Args:
        binary: 图片的二进制内容（PIL Image 对象）
        vision_model: 视觉模型实例
        prompt: 可选的自定义提示词
        callback: 进度回调函数

    Returns:
        str: VLM 生成的 Markdown 文本，失败时返回空字符串

    Note:
        - 跳过尺寸过小的图片（小于 11x11 像素）
        - 自动处理 JPEG/PNG 格式转换
    """
    callback = callback or (lambda prog, msg: None)

    img = binary
    txt = ""

    try:
        # Skip tiny crops that fail provider image-size limits.
        if hasattr(img, "size"):
            min_side = 11
            if img.size[0] < min_side or img.size[1] < min_side:
                callback(0.0, f"Skip tiny image for VLM: {img.size[0]}x{img.size[1]}")
                return ""
        with io.BytesIO() as img_binary:
            try:
                img.save(img_binary, format="JPEG")
            except Exception:
                img_binary.seek(0)
                img_binary.truncate()
                img.save(img_binary, format="PNG")

            img_binary.seek(0)
            ans = clean_markdown_block(vision_model.describe_with_prompt(img_binary.read(), prompt))
            txt += "\n" + ans
            return txt

    except Exception as e:
        callback(-1, str(e))

    return ""
