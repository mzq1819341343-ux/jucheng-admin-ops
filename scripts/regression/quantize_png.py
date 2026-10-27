#!/usr/bin/env python3
"""help-shots PNG 量化后处理（指令60 A3 手机两图；指令61 A5 扩全八图）。

对指定 PNG 做 256 色调色板量化（PIL median-cut + Floyd–Steinberg 抖动），
全分辨率不变。实测（2026-10-27）：mobile-overview 359,404B → 149,551B
（−58%）、mobile-expenses 402,510B → 154,082B（−62%），目检文字/金色/
红色无色带。降采样方案实测反而因重采样噪声变大或清晰度损失，不采纳。
指令61 A5 桌面六图评估：合计 956,183B → 366,374B（−62%），逐图 PSNR
45.2~54.8dB、MAE ≤ 0.26，目检小字（salary-rule-preview）与细虚线
（dashboard-drill）完好，达标线 = PSNR ≥ 40dB + 降幅 ≥40% + 目检。

指令66 A3：mobile-overview 单图改用 192 色——62 轮超期提醒条插入后
256 色体积 155,284B 偏差 +3.83% 逼近 +5% 上沿；192 色小样（对现行图
再量化）148,093B、PSNR 43.7dB，真管线采纳以 shots 实产体积为准
（达标线：偏差 <+2% 且目检无损，评估表见 docs/运维_性能基线.md）。

用法：python3 quantize_png.py <file.png> [file2.png ...]
逐个文件原地覆盖，打印前后体积。PIL 不可用时由调用方（capture-help-shots）
捕获失败并保留原图。
"""
import os
import sys

from PIL import Image

# 按文件覆盖色数（默认 256）；改名单须经当轮量化评估达标（见文件头）。
COLOR_OVERRIDES = {
    "mobile-overview.png": 192,  # 指令66 A3
}


def quantize(path: str) -> None:
    before = os.path.getsize(path)
    colors = COLOR_OVERRIDES.get(os.path.basename(path), 256)
    im = Image.open(path).convert("RGB")
    im.quantize(colors=colors, method=Image.MEDIANCUT).save(path, optimize=True)
    after = os.path.getsize(path)
    print(f"quantized {os.path.basename(path)} ({colors} colors): {before}B -> {after}B")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        quantize(p)
