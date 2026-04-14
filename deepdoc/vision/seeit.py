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
视觉结果可视化模块。

提供将检测结果（OCR 文本框、版面布局区域等）绘制到图像上的功能，
用于调试和结果展示。支持在检测框上绘制类别标签和置信度分数。
"""

import logging
import os
import PIL
from PIL import ImageDraw


def save_results(image_list, results, labels, output_dir='output/', threshold=0.5):
    """
    批量保存可视化结果图像。

    将每张图像的检测结果绘制到图像上，并保存到指定输出目录。

    Args:
        image_list (list): PIL Image 对象列表
        results (list[list[dict]]): 每张图像对应的检测结果列表，每个结果包含 type、bbox、score
        labels (list[str]): 标签类别名称列表
        output_dir (str): 输出目录路径，默认为 'output/'
        threshold (float): 置信度过滤阈值，低于此值的检测结果不绘制，默认为 0.5
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    for idx, im in enumerate(image_list):
        im = draw_box(im, results[idx], labels, threshold=threshold)

        out_path = os.path.join(output_dir, f"{idx}.jpg")
        im.save(out_path, quality=95)
        logging.debug("save result to: " + out_path)


def draw_box(im, result, labels, threshold=0.5):
    """
    在图像上绘制检测框及标签。

    Args:
        im (PIL.Image): 输入图像（会被原地修改）
        result (list[dict]): 检测结果列表，每个元素包含 type、bbox（[x1,y1,x2,y2]）、score
        labels (list[str]): 标签类别名称列表，用于为不同类别分配不同颜色
        threshold (float): 置信度过滤阈值，默认为 0.5

    Returns:
        PIL.Image: 绘制了检测框和标签的图像
    """
    # 线条粗细根据图像尺寸自适应
    draw_thickness = min(im.size) // 320
    draw = ImageDraw.Draw(im)
    color_list = get_color_map_list(len(labels))
    clsid2color = {n.lower():color_list[i] for i,n in enumerate(labels)}
    # 过滤掉低置信度的检测结果
    result = [r for r in result if r["score"] >= threshold]

    for dt in result:
        color = tuple(clsid2color[dt["type"]])
        xmin, ymin, xmax, ymax = dt["bbox"]
        draw.line(
            [(xmin, ymin), (xmin, ymax), (xmax, ymax), (xmax, ymin),
             (xmin, ymin)],
            width=draw_thickness,
            fill=color)

        # 在框顶部绘制类别标签和置信度分数
        text = "{} {:.4f}".format(dt["type"], dt["score"])
        tw, th = imagedraw_textsize_c(draw, text)
        draw.rectangle(
            [(xmin + 1, ymin - th), (xmin + tw + 1, ymin)], fill=color)
        draw.text((xmin + 1, ymin - th), text, fill=(255, 255, 255))
    return im


def get_color_map_list(num_classes):
    """
    根据类别数量生成 RGB 颜色映射表。

    使用位运算为每个类别生成不同的颜色，确保各类别之间有较好的视觉区分度。

    Args:
        num_classes (int): 类别数量

    Returns:
        list[list[int]]: RGB 颜色列表，每个元素为 [R, G, B]
    """
    color_map = num_classes * [0, 0, 0]
    for i in range(0, num_classes):
        j = 0
        lab = i
        while lab:
            # 将类别索引的比特位分散到 R/G/B 三个通道
            color_map[i * 3] |= (((lab >> 0) & 1) << (7 - j))
            color_map[i * 3 + 1] |= (((lab >> 1) & 1) << (7 - j))
            color_map[i * 3 + 2] |= (((lab >> 2) & 1) << (7 - j))
            j += 1
            lab >>= 3
    color_map = [color_map[i:i + 3] for i in range(0, len(color_map), 3)]
    return color_map


def imagedraw_textsize_c(draw, text):
    """
    兼容不同 PIL 版本的文本尺寸测量函数。

    PIL 10.0 移除了 textsize 方法，改用 textbbox。此函数根据 PIL 版本自动选择。

    Args:
        draw (PIL.ImageDraw.Draw): 绘图对象
        text (str): 待测量的文本

    Returns:
        tuple[int, int]: 文本的 (宽度, 高度)
    """
    if int(PIL.__version__.split('.')[0]) < 10:
        tw, th = draw.textsize(text)
    else:
        left, top, right, bottom = draw.textbbox((0, 0), text)
        tw, th = right - left, bottom - top

    return tw, th
