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
简历解析模块 (deepdoc.parser.resume)

本模块负责简历（CV）的结构化数据重构与清洗。
主要功能包括：
- 删除冗余的解析中间字段（如 raw_txt、parser_name、inference 等）
- 将各模块数据（教育、工作、证书、项目等）统一为字典列表格式
- 从工作经历和学历经历中提取关键信息聚合到基本信息中
- 计算并填充更新时间、联系人信息等

该模块是简历解析流水线的最后一步，将解析引擎输出的原始结构化数据
整理为可供存储和检索的标准格式。
"""

import datetime


def refactor(cv):
    """
    重构简历数据，清洗冗余字段并聚合关键信息。

    对简历字典执行以下操作：
    1. 删除不需要的中间字段（raw_txt、parser_name 等）
    2. 将 education/work/certificate 等模块统一为 {序号: 内容} 的字典格式
    3. 字段名映射（如 basic_salary_month -> salary_month）
    4. 按时间排序工作经历和学历经历
    5. 提取最早工作时间、管理经验、当前薪资、学校名称等聚合到 basic 中

    Args:
        cv (dict): 原始简历数据字典

    Returns:
        dict: 重构后的简历数据字典
    """
    for n in [
        "raw_txt",
        "parser_name",
        "inference",
        "ori_text",
        "use_time",
        "time_stat",
    ]:
        if n in cv and cv[n] is not None:
            del cv[n]
    cv["is_deleted"] = 0
    # 确保 basic 字典存在
    if "basic" not in cv:
        cv["basic"] = {}
    # 删除 base64 编码的照片字段，避免存储大量图片数据
    if cv["basic"].get("photo2"):
        del cv["basic"]["photo2"]

    # 统一处理各模块数据：将 education/work/certificate 等统一为 {序号: 内容} 的字典格式
    for n in [
        "education",
        "work",
        "certificate",
        "project",
        "language",
        "skill",
        "training",
    ]:
        if n not in cv or cv[n] is None:
            continue
        # 如果是字典格式（来自 JSON），先转为列表
        if isinstance(cv[n], dict):
            cv[n] = [v for _, v in cv[n].items()]
        # 非列表类型直接删除
        if not isinstance(cv[n], list):
            del cv[n]
            continue
        # 清除每条记录中的 external 字段，并重新编号
        vv = []
        for v in cv[n]:
            if "external" in v and v["external"] is not None:
                del v["external"]
            vv.append(v)
        cv[n] = {str(i): vv[i] for i in range(len(vv))}

    basics = [
        ("basic_salary_month", "salary_month"),
        ("expect_annual_salary_from", "expect_annual_salary"),
    ]
    # 字段名映射：将旧字段名重命名为新字段名
    for n, t in basics:
        if cv["basic"].get(n):
            cv["basic"][t] = cv["basic"][n]
            del cv["basic"][n]

    # 按开始时间排序工作经历和学历经历
    work = sorted(
        [v for _, v in cv.get("work", {}).items()],
        key=lambda x: x.get("start_time", ""),
    )
    edu = sorted(
        [v for _, v in cv.get("education", {}).items()],
        key=lambda x: x.get("start_time", ""),
    )

    if work:
        # 提取最早工作开始时间
        cv["basic"]["work_start_time"] = work[0].get("start_time", "")
        # 判断是否有管理经验（任一工作经历标记为 Y 即视为有）
        cv["basic"]["management_experience"] = (
            "Y"
            if any([w.get("management_experience", "") == "Y" for w in work])
            else "N"
        )
        # 取最近一份工作的薪资作为当前年薪
        cv["basic"]["annual_salary"] = work[-1].get("annual_salary_from", "0")

        # 将最近一份工作（最后一条）的关键信息聚合到 basic 中
        for n in [
            "annual_salary_from",
            "annual_salary_to",
            "industry_name",
            "position_name",
            "responsibilities",
            "corporation_type",
            "scale",
            "corporation_name",
        ]:
            cv["basic"][n] = work[-1].get(n, "")

    if edu:
        # 将最近一段学历的学校名称和专业聚合到 basic 中
        for n in ["school_name", "discipline_name"]:
            if n in edu[-1]:
                cv["basic"][n] = edu[-1][n]

    # 更新时间戳
    cv["basic"]["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 确保联系人信息存在，若没有则从 basic.name 取值
    if "contact" not in cv:
        cv["contact"] = {}
    if not cv["contact"].get("name"):
        cv["contact"]["name"] = cv["basic"].get("name", "")
    return cv
