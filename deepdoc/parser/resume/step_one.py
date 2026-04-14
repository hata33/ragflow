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
简历数据第一步处理模块 (step_one)

本模块负责从原始 JSON 格式的简历内容中提取并结构化各字段数据，
主要用于批量简历数据的 DataFrame 处理（如从数据库导出的简历）。

主要功能：
- 将 JSON 格式的简历内容解析为结构化字段
- 从嵌套的 contact/basic 子对象中提取各字段
- 对学历、地区、行业等字段进行中文映射转换
- 性别、布尔字段等的本地化处理
- 输出符合预定义 FIELDS 格式的字典
"""

import json
from deepdoc.parser.resume.entities import degrees, regions, industries

# 预定义的简历字段列表，格式为 "字段名 类型"
# 用于最终输出的字段规范和排序
FIELDS = [
"address STRING",
"annual_salary int",
"annual_salary_from int",
"annual_salary_to int",
"birth STRING",
"card STRING",
"certificate_obj string",
"city STRING",
"corporation_id int",
"corporation_name STRING",
"corporation_type STRING",
"degree STRING",
"discipline_name STRING",
"education_obj string",
"email STRING",
"expect_annual_salary int",
"expect_city_names string",
"expect_industry_name STRING",
"expect_position_name STRING",
"expect_salary_from int",
"expect_salary_to int",
"expect_type STRING",
"gender STRING",
"industry_name STRING",
"industry_names STRING",
"is_deleted STRING",
"is_fertility STRING",
"is_house STRING",
"is_management_experience STRING",
"is_marital STRING",
"is_oversea STRING",
"language_obj string",
"name STRING",
"nation STRING",
"phone STRING",
"political_status STRING",
"position_name STRING",
"project_obj string",
"responsibilities string",
"salary_month int",
"scale STRING",
"school_name STRING",
"self_remark string",
"skill_obj string",
"title_name STRING",
"tob_resume_id STRING",
"updated_at Timestamp",
"wechat STRING",
"work_obj string",
"work_experience int",
"work_start_time BIGINT"
]

def refactor(df):
    """
    从 DataFrame 中解析 JSON 格式的简历内容并重构为结构化字段。

    处理流程：
    1. 解析 resume_content 列中的 JSON 字符串
    2. 从顶层及 contact/basic 子对象中提取各字段
    3. 将学历 ID 映射为中文名称、地区 ID 映射为地区名称、行业 ID 映射为行业名称
    4. 性别和布尔字段本地化为中文
    5. 清洗特殊字符后输出符合 FIELDS 规范的字典

    Args:
        df (pandas.DataFrame): 包含 resume_content 列的 DataFrame

    Returns:
        dict: 键为 FIELDS 中的字段名，值为对应数据的第一行
    """
    def deal_obj(obj, k, kk):
        """
        从嵌套字典中提取指定子键的值。

        Args:
            obj: 待提取的对象
            k: 第一层键名
            kk: 第二层键名

        Returns:
            str: 提取到的值，失败时返回空字符串
        """
        if not isinstance(obj, type({})):
            return ""
        obj = obj.get(k, {})
        if not isinstance(obj, type({})):
            return ""
        return obj.get(kk, "")

    def loadjson(line):
        """安全解析 JSON 字符串，解析失败时返回空字典。"""
        try:
            return json.loads(line)
        except Exception:
            pass
        return {}

    # 解析 resume_content 列中的 JSON 字符串
    df["obj"] = df["resume_content"].map(lambda x: loadjson(x))
    df.fillna("", inplace=True)

    clms = ["tob_resume_id", "updated_at"]

    def extract(nms, cc=None):
        """
        从解析后的 JSON 对象中批量提取字段到 DataFrame 列。

        Args:
            nms: 要提取的字段名列表
            cc: 可选的子对象键名，如 "contact" 或 "basic"。
                若提供则从 obj[cc][字段名] 提取，否则从 obj[字段名] 提取。
        """
        nonlocal clms
        clms.extend(nms)
        for c in nms:
            if cc:
                # 从嵌套子对象（如 contact、basic）中提取字段
                df[c] = df["obj"].map(lambda x: deal_obj(x, cc, c))
            else:
                # 从顶层对象提取：字典类型序列化为 JSON，其他类型直接转字符串
                df[c] = df["obj"].map(
                    lambda x: json.dumps(
                        x.get(
                            c,
                            {}),
                        ensure_ascii=False) if isinstance(
                        x,
                        type(
                            {})) and (
                        isinstance(
                            x.get(c),
                            type(
                                {})) or not x.get(c)) else str(x).replace(
                                    "None",
                        ""))

    # 提取顶层模块字段（教育、工作、证书、项目、语言、技能）
    extract(["education", "work", "certificate", "project", "language",
             "skill"])
    # 从 contact 子对象提取联系信息
    extract(["wechat", "phone", "is_deleted",
            "name", "tel", "email"], "contact")
    # 从 basic 子对象提取基本信息
    extract(["nation", "expect_industry_name", "salary_month",
             "industry_ids", "is_house", "birth", "annual_salary_from",
             "annual_salary_to", "card",
             "expect_salary_to", "expect_salary_from",
             "expect_position_name", "gender", "city",
             "is_fertility", "expect_city_names",
             "political_status", "title_name", "expect_annual_salary",
             "industry_name", "address", "position_name", "school_name",
             "corporation_id",
             "is_oversea", "responsibilities",
             "work_start_time", "degree", "management_experience",
             "expect_type", "corporation_type", "scale", "corporation_name",
             "self_remark", "annual_salary", "work_experience",
             "discipline_name", "marital", "updated_at"], "basic")

    # 将学历 ID 映射为中文学历名称
    df["degree"] = df["degree"].map(lambda x: degrees.get_name(x))
    # 将地区 ID 映射为地区名称字符串
    df["address"] = df["address"].map(lambda x: " ".join(regions.get_names(x)))
    # 将行业 ID（逗号分隔）映射为行业名称字符串
    df["industry_names"] = df["industry_ids"].map(lambda x: " ".join([" ".join(industries.get_names(i)) for i in
                                                                      str(x).split(",")]))
    clms.append("industry_names")

    def arr2str(a):
        """将数组或字符串中的逗号替换为空格，返回统一的字符串格式。"""
        if not a:
            return ""
        if isinstance(a, list):
            a = " ".join([str(i) for i in a])
        return str(a).replace(",", " ")

    # 性别映射：M -> 男，F -> 女
    df["expect_industry_name"] = df["expect_industry_name"].map(
        lambda x: arr2str(x))
    df["gender"] = df["gender"].map(
        lambda x: "男" if x == 'M' else (
            "女" if x == 'F' else ""))
    # 布尔字段本地化：Y -> 是，N -> 否
    for c in ["is_fertility", "is_oversea", "is_house",
              "management_experience", "marital"]:
        df[c] = df[c].map(
            lambda x: '是' if x == 'Y' else (
                '否' if x == 'N' else ""))
    # 复制字段以兼容不同的字段命名
    df["is_management_experience"] = df["management_experience"]
    df["is_marital"] = df["marital"]
    clms.extend(["is_management_experience", "is_marital"])

    df.fillna("", inplace=True)
    # 若 phone 为空但 tel 不为空，用 tel 填充 phone
    for i in range(len(df)):
        if not df.loc[i, "phone"].strip() and df.loc[i, "tel"].strip():
            df.loc[i, "phone"] = df.loc[i, "tel"].strip()

    # 删除不需要输出的中间列
    for n in ["industry_ids", "management_experience", "marital", "tel"]:
        for i in range(len(clms)):
            if clms[i] == n:
                del clms[i]
                break

    # 去重列名
    clms = list(set(clms))

    # 按列名排序后重新排列 DataFrame
    df = df.reindex(sorted(clms), axis=1)
    # 清洗单元格中的特殊字符：制表符换为空格、换行符转义
    for c in clms:
        df[c] = df[c].map(
            lambda s: str(s).replace(
                "\t",
                " ").replace(
                "\n",
                "\\n").replace(
                "\r",
                "\\n"))
    # 按 FIELDS 定义的字段顺序输出第一行数据为字典
    return dict(zip([n.split()[0] for n in FIELDS], df.values.tolist()[0]))
