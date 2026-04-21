#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypandoc-binary>=1.13",
# ]
# ///
"""
md2docx.py — 将论文 markdown 转换为 Word docx (基于 uv + pypandoc-binary)

用法:
    uv run md2docx.py                      # 论文.md → 论文.docx
    uv run md2docx.py foo.md               # 转 foo.md
    uv run md2docx.py --render-mmd         # 顺便先把 meimaid-p/*.mmd 渲染成 PNG
    uv run md2docx.py --reference my.docx  # 指定样式模板

也可以直接执行 (脚本顶部有 uv shebang):
    ./md2docx.py
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pypandoc

SCRIPT_DIR = Path(__file__).resolve().parent


def render_mermaid(mmd_dir: Path, png_dir: Path) -> int:
    """增量渲染 .mmd → .png (只渲染新增/修改过的)。返回渲染数量。"""
    mmdc = shutil.which("mmdc")
    if not mmdc:
        npm = shutil.which("npm")
        if not npm:
            print("⚠ 未找到 mmdc 也没有 npm,跳过 Mermaid 渲染", file=sys.stderr)
            return 0
        print("→ 安装 @mermaid-js/mermaid-cli ...")
        subprocess.run([npm, "install", "-g", "@mermaid-js/mermaid-cli"], check=True)
        mmdc = shutil.which("mmdc")
        if not mmdc:
            print("✗ 安装 mmdc 后仍未找到,跳过", file=sys.stderr)
            return 0

    png_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for mmd in sorted(mmd_dir.glob("*.mmd")):
        png = png_dir / f"{mmd.stem}.png"
        if png.exists() and png.stat().st_mtime >= mmd.stat().st_mtime:
            continue
        print(f"  ↻ {mmd.name}")
        try:
            subprocess.run(
                [mmdc, "-i", str(mmd), "-o", str(png),
                 "-b", "white", "-w", "1600", "-s", "2", "--quiet"],
                check=True, capture_output=True,
            )
            count += 1
        except subprocess.CalledProcessError as e:
            print(f"    ⚠ 渲染失败: {e.stderr.decode(errors='ignore')[:200]}",
                  file=sys.stderr)
    print(f"✓ 已渲染 {count} 个 Mermaid 图")
    return count


def convert(input_md: Path, output_docx: Path, reference: Path | None) -> None:
    """调 pypandoc 把 md 转成 docx。"""
    extra_args: list[str] = [
        f"--resource-path={SCRIPT_DIR}",
        "--toc",
        "--toc-depth=3",
        "--highlight-style=tango",
        "--standalone",
    ]
    if reference and reference.exists():
        extra_args.append(f"--reference-doc={reference}")
        print(f"  使用样式模板: {reference.name}")

    # 启用扩展:
    #   pipe_tables          : | 分隔表格
    #   tex_math_dollars     : $...$ / $$...$$ 数学公式
    #   raw_html             : 表格里的 <br/>
    #   yaml_metadata_block  : 文档头 --- 元数据块
    src_format = "markdown+pipe_tables+tex_math_dollars+raw_html+yaml_metadata_block"

    pypandoc.convert_file(
        str(input_md),
        to="docx",
        format=src_format,
        outputfile=str(output_docx),
        extra_args=extra_args,
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="论文 markdown → Word docx 转换工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", nargs="?", default="论文.md",
                   help="输入 md 文件 (默认: 论文.md)")
    p.add_argument("-o", "--output", help="输出 docx 文件 (默认: <input>.docx)")
    p.add_argument("--render-mmd", action="store_true",
                   help="转换前把 meimaid-p/*.mmd 渲染成 PNG")
    p.add_argument("--reference", default="reference.docx",
                   help="Word 样式模板 (存在则自动套用; 默认: reference.docx)")
    args = p.parse_args()

    input_md = (SCRIPT_DIR / args.input).resolve()
    if not input_md.exists():
        print(f"✗ 找不到输入文件: {input_md}", file=sys.stderr)
        return 1

    output_docx = (
        Path(args.output).resolve() if args.output
        else input_md.with_suffix(".docx")
    )
    reference = (SCRIPT_DIR / args.reference).resolve()

    print(f"✓ pandoc: {pypandoc.get_pandoc_version()}")

    if args.render_mmd:
        render_mermaid(SCRIPT_DIR / "meimaid-p", SCRIPT_DIR / "image" / "论文")

    print(f"→ 转换 {input_md.name} → {output_docx.name} ...")
    convert(input_md, output_docx, reference)
    print(f"✓ 完成: {output_docx}")

    print("\n提示:")
    print("  • 中文字体可在 Word 里改成宋体/仿宋,或预先准备 reference.docx")
    print("  • 生成默认样式模板 (改完字体后保存,后续自动套用):")
    print("      uv run --with pypandoc-binary python -c \\")
    print("        \"import pypandoc; pypandoc.convert_text('', 'docx', "
          "format='md', outputfile='reference.docx', "
          "extra_args=['--print-default-data-file=reference.docx'])\"")
    print("  • 顺便渲染 mermaid 图: ./md2docx.py --render-mmd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
