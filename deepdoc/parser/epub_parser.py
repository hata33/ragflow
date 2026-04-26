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

import logging
import warnings
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from .html_parser import RAGFlowHtmlParser

# OPF XML namespaces
_OPF_NS = "http://www.idpf.org/2007/opf"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

# Media types that contain readable XHTML content
_XHTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html", "text/xml"}

logger = logging.getLogger(__name__)


class RAGFlowEpubParser:
    """EPUB 解析器。

    它会按照 spine 指定的阅读顺序提取 XHTML 内容，再复用
    `RAGFlowHtmlParser` 完成 HTML 清洗和分块。
    """

    def __call__(self, fnm, binary=None, chunk_token_num=512):
        """读取 EPUB 压缩包，并按阅读顺序汇总正文 section。"""

        if binary is not None:
            if not binary:
                logger.warning(
                    "RAGFlowEpubParser received an empty EPUB binary payload for %r",
                    fnm,
                )
                raise ValueError("Empty EPUB binary payload")
            zf = zipfile.ZipFile(BytesIO(binary))
        else:
            zf = zipfile.ZipFile(fnm)

        try:
            content_items = self._get_spine_items(zf)
            all_sections = []
            html_parser = RAGFlowHtmlParser()

            for item_path in content_items:
                try:
                    html_bytes = zf.read(item_path)
                except KeyError:
                    continue
                if not html_bytes:
                    logger.debug("Skipping empty EPUB content item: %s", item_path)
                    continue
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=UserWarning)
                    sections = html_parser(
                        item_path, binary=html_bytes, chunk_token_num=chunk_token_num
                    )
                all_sections.extend(sections)

            return all_sections
        finally:
            zf.close()

    @staticmethod
    def _get_spine_items(zf):
        """返回 spine 中定义的正文文件路径列表。"""

        # 1. 先从 META-INF/container.xml 中找到 OPF 清单文件位置。
        try:
            container_xml = zf.read("META-INF/container.xml")
        except KeyError:
            return RAGFlowEpubParser._fallback_xhtml_order(zf)

        try:
            container_root = ElementTree.fromstring(container_xml)
        except ElementTree.ParseError:
            logger.warning("Failed to parse META-INF/container.xml; falling back to XHTML order.")
            return RAGFlowEpubParser._fallback_xhtml_order(zf)

        rootfile_el = container_root.find(f".//{{{_CONTAINER_NS}}}rootfile")
        if rootfile_el is None:
            return RAGFlowEpubParser._fallback_xhtml_order(zf)

        opf_path = rootfile_el.get("full-path", "")
        if not opf_path:
            return RAGFlowEpubParser._fallback_xhtml_order(zf)

        # OPF 内的 href 通常相对 OPF 所在目录，因此这里提前记录基路径。
        opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

        # 2. 解析 OPF，读取 manifest 与 spine。
        try:
            opf_xml = zf.read(opf_path)
        except KeyError:
            return RAGFlowEpubParser._fallback_xhtml_order(zf)

        try:
            opf_root = ElementTree.fromstring(opf_xml)
        except ElementTree.ParseError:
            logger.warning("Failed to parse OPF file '%s'; falling back to XHTML order.", opf_path)
            return RAGFlowEpubParser._fallback_xhtml_order(zf)

        # 3. 先建立 `id -> (href, media_type)` 映射，供后续 spine 查找。
        manifest = {}
        for item in opf_root.findall(f".//{{{_OPF_NS}}}item"):
            item_id = item.get("id", "")
            href = item.get("href", "")
            media_type = item.get("media-type", "")
            if item_id and href:
                manifest[item_id] = (href, media_type)

        # 4. 再按 spine 顺序筛出真正参与阅读流的 XHTML 页面。
        spine_items = []
        for itemref in opf_root.findall(f".//{{{_OPF_NS}}}itemref"):
            idref = itemref.get("idref", "")
            if idref not in manifest:
                continue
            href, media_type = manifest[idref]
            if media_type not in _XHTML_MEDIA_TYPES:
                continue
            spine_items.append(opf_dir + href)

        return (
            spine_items if spine_items else RAGFlowEpubParser._fallback_xhtml_order(zf)
        )

    @staticmethod
    def _fallback_xhtml_order(zf):
        """兜底方案：按文件名字典序返回所有 HTML/XHTML 页面。"""

        return sorted(
            n
            for n in zf.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm"))
            and not n.startswith("META-INF/")
        )
