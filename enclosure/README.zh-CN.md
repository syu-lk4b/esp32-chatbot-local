# ES3C28P 桌面外壳

[English](README.md) | **中文**

小奇（ES3C28P 2.8" 屏）的桌面外壳：简洁小显示器造型，屏幕后仰 15°，背后放喇叭，底部楔形脚，内置 103450 锂电池位。Blender 参数化建模，Bambu P1S 直接打印。

<p>
  <img src="renders/front.png" width="380" alt="渲染：正面">
  <img src="../docs/images/photo-side.jpg" width="300" alt="实物：PLA 打印">
</p>

## 文件

| 文件 | 说明 |
|---|---|
| `stl/front_bezel.stl` | 前框（已摆好打印方向：正面朝下） |
| `stl/back_shell.stl` | 后壳（已摆好打印方向：背面朝下） |
| `slice/*.3mf` | 已用 P1S 0.4 + Bambu PLA Basic + 0.20mm Standard 切好，可直接在 Bambu Studio 打开发送打印 |
| `gen_enclosure.py` | 参数化建模脚本（尺寸都在文件顶部） |
| `enclosure.blend` | 装配模型（运行脚本后在本地生成，不入库） |
| `renders/` | 预览图 |

## 尺寸

外形 91.0 × 55.0 × 26.0 mm（宽 × 高 × 厚，不含底脚），底脚前端加高 7 mm。侧壁 2.0 mm、后背 2.5 mm。
电池槽 34 × 50 × 10 mm（103450，如 2300mAh 款），出线端朝上靠近 BAT 插座。
打印：前框约 10 分钟 / 3.5 g，后壳约 67 分钟 / 30 g，均无需支撑。

螺丝：M3×25 沉头，螺丝尖距前框正面 1.0 mm、咬合 4.9 mm；改尺寸后脚本会自动校验（不合适会报 `screw length mismatch`）。

## 装配

材料：4 颗 **M3×25 沉头自攻螺丝**（PA 型）、1 个 **SS12D00 小拨动开关**（本体约 8.6×3.6×3.5 mm）、双面胶 / 泡棉胶。

1. 喇叭朝外（锥盆对着网孔）放进后壳右侧的喇叭槽，四周点几滴热熔胶或泡棉胶固定
2. 电池放进左侧电池槽，背面贴双面胶；插头接板子 **BAT** 口（**先核对正负极**）
3. 电源开关：把电池**红线（正极）**剪断，两端分别焊到开关的中间脚和任一侧脚，热缩管包好；开关本体塞进左侧壁 USB-C 上方的卡槽，拨杆从小长孔伸出，点一滴胶固定
4. 喇叭线接板子 **SPEAKER** 口
5. 前框正面朝下放平，把屏幕模块屏幕朝下放进去，四个安装孔对准前框的柱子
6. 盖上后壳（USB-C 开口对准板子 Type-C 一侧），从背面拧 4 颗螺丝：螺丝穿过后壳立柱、电路板，拧进前框柱子。拧到贴合即可，别过紧以免压裂玻璃

背面两个长孔对着 RESET / BOOT 键，用回形针可按。

## 需要实物核对的位置

以下按官方外形图背面尺寸 + 背面实拍照片定位（照片里板子是上下翻转的，USB-C 仍在左侧），开孔已放宽；首次建议**先打前框**（10 分钟）试装：

- 麦克风孔：前框右下 `MIC_X, MIC_Z = (79.5, 10)`
- USB-C 开口：中心高度 `USBC_Z = 23.1`，开口高 14 mm
- RESET / BOOT 长孔：`BUTTON_X = 3.3`，RESET 约 z = 11.6、BOOT 约 z = 36.6

电源开关串在电池线上：**关**时电池完全断开（不耗电，但插 USB 也不会给电池充电）；**开**时插 USB 即边用边充。开关位置参数 `SWITCH_Y, SWITCH_Z`。

不对的话改 `gen_enclosure.py` 顶部参数重新生成：

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P gen_enclosure.py
```

脚本会检查网格是否闭合（`non_manifold_edges` 应为 0）以及与电路板 / 电池 / 喇叭示意体是否干涉（`collisions none`）。

## 参考

板子尺寸来自 [ES3C28P 规格书](https://www.lcdwiki.com/res/ES3C28P/ES3C28P_ES2N28P_Specification_V1.0.pdf) 第 13 页外形图：PCB 86 × 50 × 1.6 mm，安装孔 Ø3.2 × 4（孔距 78 × 42），显示区 58.05 × 43.6 mm，正面叠层 4.3 mm，背面元件最高 4.7 mm。
