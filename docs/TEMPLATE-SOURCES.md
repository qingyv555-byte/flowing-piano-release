# 示例音乐：谱面核对与改编边界

核对日期：2026-10-04。生成器为 `tools/generate_builtin_templates.py`；安装资源位于 `src/ai_piano_studio/resources/templates/`。修改生成器后重新生成 JSON、缩略图和元数据，并运行模板测试，不手工只改成品。

这些是供编辑、试听与改编的短示例，不是六首完整原作，也不宣称替代校订乐谱。力度、速度、部分伴奏和装饰音是教学性取舍。测试检查音高、时值、节拍、资源一致性；未做本轮实体声卡听感验收。

| 示例 | 当前范围和依据 | 核对结果/本轮修正 |
|---|---|---|
| 欢乐颂 | C大调主题，16小节；对照 [Mutopia 528](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=528) 的高声部旋律，转调比较 | 主题、附点节奏及桥段吻合；伴奏是项目的稀疏五度简编，不复制SATB和声。保持不变。 |
| 小星星 | C大调传统旋律，六个乐句；[1870年传统旋律谱例](https://fr.wikisource.org/wiki/Chansons_et_rondes_enfantines/Ah_%21_vous_dirai-je%2C_maman) 提供对照 | 采用常见童谣唱法；保持同音重复与句尾长音。以4/4合并分组，不声称等同莫扎特变奏曲或某一首歌谱的每个细节。保持不变。 |
| 致爱丽丝 | 开头主题，带反复；[Mutopia 931](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=931)，Breitkopf & Härtel 1888版，Stelios Samelis排版，标注Public Domain | 原6/8改为3/8；回句D4改为E4；在首小节加一四分音符长度的静默，让两个十六分弱起落在末尾；主题演奏两遍、回A4结束。替换原先跨入后段又突然截断的拼接。 |
| 卡农进行 | 以D–A–B–F♯–G–D–G–A低音循环写成的钢琴示例；[Mutopia卡农旋律谱](https://www.mutopiaproject.org/ftp/PachelbelJ/CanonInD/CanonInD-a4.pdf)作旋律参照 | 属于“进行简编”，三轮低音循环及分解和弦，并非原曲完整三声部卡农。本轮明确标注改编边界，不将简编差异误判成必须复制原作。 |
| G大调小步舞曲 | BWV Anh.114前16小节，Petzold；[Mutopia 75](https://www.mutopiaproject.org/ftp/BachJS/BWVAnh114/anna-magdalena-04/anna-magdalena-04.ly)，Allen Garvin排版，Public Domain | 省略装饰音和反复；第8小节原B4一拍+A4两拍改为A4三拍，避免把倚音变成新的整拍旋律。其余主题和低音逐小节对照。 |
| 月光主题 | 第一乐章前9小节；[Mutopia 276](https://www.mutopiaproject.org/ftp/BeethovenLv/O27/moonlight/moonlight-lys/moonlight1-a4.ly)，Berners 1908版的现代排版 | 原4/4改为原谱2/2；四分音符时值及三连音不变。高声部第5小节末进入、低音与9小节轮廓吻合。原有同音跨声部重叠有谱面依据，未以“消除报警”为由删音。 |

## 时间与兼容性

内部时间单位始终是四分音符拍；3/8每小节1.5拍，2/2每小节4拍。BPM仍计四分音符，不按分母自动倍速。JSON和MIDI均保存真实拍号，界面下拉框复用同一拍号列表。

《致爱丽丝》现在约17.05秒，而不是强制凑到30–34秒。原时长断言改为按乐句计算的精确总拍数；其余示例保留原长度。首小节的静默是现有固定小节网格下的弱起对齐方式，不是新增“首小节不满拍”数据格式。所有示例仍为可编辑音符，已有用户改编工程不被覆盖。

## 来源使用方式

本轮读取谱面进行校对，未把现代排版PDF、MIDI或LilyPond文件打包进应用。上述来源中《致爱丽丝》《小步舞曲》《欢乐颂》排版标注公有领域；月光现代排版为CC BY-SA 2.5（Chris Sawer / Stewart Holmes），卡农现代排版为CC BY 3.0（基于Jim Paterson作品、匿名维护者排版）。链接用于核对，不把所有现代排版材料一概声称为公有领域。项目音源许可另见 `THIRD_PARTY_NOTICES.md`。

本轮回归入口：`tests/unit/test_template_score_audit.py`、`tests/unit/test_builtin_templates.py`、`tests/unit/test_tempo.py`、`tests/unit/test_midi.py` 和 Studio 拍号交互测试。
