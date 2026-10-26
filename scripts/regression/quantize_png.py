#!/usr/bin/env python3
"""手机长图 PNG 量化后处理（指令60 A3）。

对指定 PNG 做 256 色调色板量化（PIL median-cut + Floyd–Steinberg 抖动），
全分辨率不变。实测（2026-10-27）：mobile-overview 359,404B → 149,551B
（−58%）、mobile-expenses 402,510B → 154,082B（−62%），目检文字/金色/
红色无色带。降采样方案实测反而因重采样噪声变大或清晰度损失，不采纳。

用法：python3 quantize_png.py <file.png> [file2.png ...]
逐个文件原地覆盖，打印前后体积。PIL 不可用时由调用方（capture-help-shots）
捕获失败并保留原图。
"""
import os
import sys

from PIL import Image


def quantize(path: str) -> None:
    before = os.path.getsize(path)
    im = Image.open(path).convert("RGB")
    im.quantize(colors=256, method=Image.MEDIANCUT).save(path, optimize=True)
    after = os.path.getsize(path)
    print(f"quantized {os.path.basename(path)}: {before}B -> {after}B")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        quantize(p)
