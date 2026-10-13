# 对官方 hatch-pet 技能的改动

上游：[openai/skills](https://github.com/openai/skills) → `skills/.curated/hatch-pet`
许可证：**Apache License 2.0**（见同目录 `LICENSE.txt`），原作版权归 OpenAI 所有。

官方技能是面向 **Unix + bash + jq** 的 Codex 环境写的。本机是 **Windows 且没有 bash/jq**，
所以直接用会卡在打包那一步。下面三处是为本地环境补的。

## 1. `scripts/mark_job_complete.py`（新增，替代 `jq` 更新 manifest）

官方流程用这段 shell 把生成结果记进 `imagegen-jobs.json`：

```bash
OUTPUT_REL=$(jq -r --arg id "$JOB_ID" '.jobs[] | select(.id == $id) | .output_path' "$RUN_DIR/imagegen-jobs.json")
cp "$SOURCE" "$RUN_DIR/$OUTPUT_REL"
jq --arg id ... '{status: "complete", source_path: $source, completed_at: $at}' ...
```

本机没有 `jq`，改成 Python：复制源图到该任务的 `decoded` 输出路径、把任务标记为 `complete`、
对 `base` 任务额外生成 `references/canonical-base.png`。

用法：

```bash
python mark_job_complete.py --run-dir <运行目录> --job-id <任务id> --source <生成结果的路径>
```

## 2. `scripts/package_pet.py`（新增，替代 shell + jq 打包）

官方用 shell 拼 `pet.json`、只输出 Codex 一种格式。本机这个脚本一次输出**两套**：

- `<CODEX_HOME>/pets/<id>/pet.json` + `spritesheet.webp`（Codex / ChatGPT 桌面版）
- `<APPDATA>/Xiaomi MiMo/pets/<id>/manifest.json` + `spritesheet.webp`（MiMo Desktop 的 Mate 规格）

MiMo manifest 需要 `frameWidth / frameHeight / columns / totalFrames / animations{row,frames,fps,loop}`，
这些都从 `pet_request.json` 与图集规格推导，避免手写不一致。

```bash
python package_pet.py --run-dir <运行目录>            # 两套都输出
python package_pet.py --run-dir <r> --no-mimo         # 只输出 Codex
```

## 3. `scripts/defringe_atlas.py`（新增，解决"蓝边"）

官方流程只有 `extract_strip_frames`（按到色键的距离算 alpha），但**保留了被抠区域的颜色**，
于是半透明边缘上仍残留青色 → 成品在透明背景上会看到一圈蓝边。

这个脚本做反解：`fg = (observed - (1-a) * chroma) / a`，把边缘像素里混进去的色键颜色去掉，
并丢弃反解后仍然接近色键的残点。

```bash
python defringe_atlas.py <frames目录> --chroma "#00FFFF"
python defringe_atlas.py <spritesheet.webp> --chroma "#00FFFF"
```

> 注意：**不要用有损 WebP 重压**去代替这一步，会重新引入"透明像素带 RGB 残差"，
> `validate_atlas.py` 会直接判定失败。

## 4. 流程层面的两条硬规矩（写进了 SKILL.md）

1. **任何重生成之后必须重跑** `compose_atlas.py → validate_atlas.py → defringe_atlas.py →
   make_contact_sheet.py → render_animation_previews.py`，并确认发布出去的图集**真的变了**
   （比对文件哈希）。漏掉这一步会让图集、打包产物、已同步的宠物全部停在旧图，看起来像"修复没生效"。
2. **一行多帧要一次性生成**（3×2 = 6 帧，4×2 = 8 帧）。分两批生成会让道具和细节漂移。
   提示词里要显式写"同色同大小同位置、每帧尺寸锁定"。
