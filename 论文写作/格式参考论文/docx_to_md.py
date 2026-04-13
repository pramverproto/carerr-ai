"""
将 docx 论文转换为 Markdown 格式的脚本
用法: python docx_to_md.py
"""

from docx import Document
import re
import os

INPUT_FILE = "(以此篇为大论文写作标准模版)云计算独立任务及关联任务调度算法研究_张晓磊.docx"
OUTPUT_FILE = "云计算独立任务及关联任务调度算法研究_张晓磊.md"

# 样式 -> Markdown 标题级别映射
HEADING_MAP = {
    "Title": 1,
    "Heading 1": 1,
    "Heading 2": 2,
    "Heading 3": 3,
    "Heading 4": 4,
    "Heading 5": 5,
}


def is_figure_or_table_caption(text: str) -> bool:
    """判断是否为图/表标题"""
    return bool(re.match(r"^(图|表|Fig\.|Table)\s*\d", text.strip()))


def is_formula_label(text: str) -> bool:
    """判断是否为公式编号，如 (3.1)"""
    return bool(re.match(r"^\s*（\d+\.\d+）\s*$|^\s*\(\d+\.\d+\)\s*$", text.strip()))


def convert(input_path: str, output_path: str):
    doc = Document(input_path)
    lines: list[str] = []
    prev_was_list = False
    list_counter = 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            # 保留空行用于分段
            if lines and lines[-1] != "":
                lines.append("")
            prev_was_list = False
            continue

        style_name = para.style.name if para.style else "Normal"

        # ---------- 标题 ----------
        if style_name in HEADING_MAP:
            level = HEADING_MAP[style_name]
            lines.append("")
            lines.append(f"{'#' * level} {text}")
            lines.append("")
            prev_was_list = False
            list_counter = 0
            continue

        # ---------- 列表项 ----------
        if style_name == "List Paragraph":
            if not prev_was_list:
                list_counter = 0
                lines.append("")  # 列表前空行
            list_counter += 1
            # 用有序列表
            lines.append(f"{list_counter}. {text}")
            prev_was_list = True
            continue

        # ---------- 图/表标题 ----------
        if is_figure_or_table_caption(text):
            lines.append("")
            lines.append(f"**{text}**")
            lines.append("")
            prev_was_list = False
            list_counter = 0
            continue

        # ---------- 公式编号 ----------
        if is_formula_label(text):
            # 追加到上一行末尾
            if lines:
                lines[-1] = lines[-1].rstrip() + f"  {text}"
            continue

        # ---------- 普通正文 ----------
        if prev_was_list:
            lines.append("")  # 列表后空行
        lines.append(text)
        prev_was_list = False
        list_counter = 0

    # 后处理：合并连续空行
    merged: list[str] = []
    for line in lines:
        if line == "" and merged and merged[-1] == "":
            continue
        merged.append(line)

    md_text = "\n".join(merged).strip() + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    print(f"转换完成: {output_path}")
    print(f"共 {len(merged)} 行")


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, INPUT_FILE)
    output_path = os.path.join(script_dir, OUTPUT_FILE)
    convert(input_path, output_path)
