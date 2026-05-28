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
音频文件解析模块

本模块提供音频文件的语音转文字功能，支持多种音频格式。

支持的音频格式：
- WAV: .da, .wave, .wav
- MP3: .mp3
- AAC: .aac
- FLAC: .flac
- OGG: .ogg, .oggvorbis
- AIFF: .aiff
- AU: .au
- MIDI: .midi
- WMA: .wma
- 其他格式: .realaudio, .vqf, .ape

主要功能：
- 使用 Sequence2Txt LLM 模型进行语音识别
- 自动检测音频格式
- 临时文件处理和清理

使用场景：
- 音频会议记录转录
- 语音笔记处理
- 音频内容索引
"""

import logging
import os
import re
import tempfile

from common.constants import LLMType
from api.db.services.llm_service import LLMBundle
from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
from rag.nlp import rag_tokenizer, tokenize


def chunk(filename, binary, tenant_id, lang, callback=None, **kwargs):
    """
    解析音频文件并转录为文字

    使用 LLM 的语音识别功能将音频内容转换为文字，然后进行分词处理。

    Args:
        filename: 音频文件名
        binary: 音频文件的二进制内容
        tenant_id: 租户 ID，用于获取模型配置
        lang: 语言设置（影响分词策略）
        callback: 进度回调函数
        **kwargs: 其他参数

    Returns:
        list: 包含转录文本的单元素列表，失败时返回空列表

    Processing Steps:
        1. 验证文件扩展名
        2. 创建临时文件
        3. 调用 Sequence2Txt LLM 进行转录
        4. 对转录结果进行分词
        5. 清理临时文件

    Raises:
        RuntimeError: 文件扩展名不支持时
    """
    doc = {"docnm_kwd": filename, "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))}
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])

    # is it English
    is_english = lang.lower() == "english"  # is_english(sections)
    try:
        _, ext = os.path.splitext(filename)
        if not ext:
            raise RuntimeError("No extension detected.")

        if ext not in [".da", ".wave", ".wav", ".mp3", ".aac", ".flac", ".ogg", ".aiff", ".au", ".midi", ".wma",
                       ".realaudio", ".vqf", ".oggvorbis", ".ape"]:
            raise RuntimeError(f"Extension {ext} is not supported yet.")

        tmp_path = ""
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmpf:
            tmpf.write(binary)
            tmpf.flush()
            tmp_path = os.path.abspath(tmpf.name)

        callback(0.1, "USE Sequence2Txt LLM to transcription the audio")
        seq2txt_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.SPEECH2TEXT)
        seq2txt_mdl = LLMBundle(tenant_id, seq2txt_model_config, lang=lang)
        ans = seq2txt_mdl.transcription(tmp_path)
        callback(0.8, "Sequence2Txt LLM respond: %s ..." % ans[:32])

        tokenize(doc, ans, is_english)
        return [doc]
    except Exception as e:
        callback(prog=-1, msg=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as e:
                logging.exception(f"Failed to remove temporary file: {tmp_path}, exception: {e}")
                pass
    return []
