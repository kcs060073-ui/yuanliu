# -*- coding: utf-8 -*-
"""字幕切分模块：把脚本文本按风格模板切分为字幕段列表。

切分规则（基于三条成片模板提炼）：
1. 以句号/感叹号/问号/分号/省略号为硬断句符
2. 逗号按意群决定是否拆开（超过 ~14 字且中间逗号停顿自然则拆）
3. 首句为钩子（由风格模板决定钩子类型）
4. 每条字幕时长估算：字数 * 每字时长，再按语速折算
5. 结尾强制追加 CTA
"""
import json
import re
import os

# 每字朗读时长（原始语速 1.0 下的毫秒/字）
#
# 📌 2026-09-03 用户拍板（第三次，最终值）：纯字幕用 **280**，"宁长勿短"。
#    用户实际在剪映里看过 230 版（贴真实语速 211）后决定改回 280——
#    纯字幕视频无配音，280 的慢节奏留足默读时间，观感更稳。
#    校准史（勿回退到更早值）：
#    - 200：早期模板人工展示时长，系统性偏短 30%，已废弃
#    - 280：一版修正，用户认可节奏（嘉定 42.67s）
#    - 230：据配音 SRT 反推真实语速 211 的贴近版（嘉定 37.97s），
#      用户对比后仍选 280 → 最终定 280
MS_PER_CHAR = 250           # 配音(剪映手动朗读)模式基准：/1.09 后约 229ms/字，贴近真实语音
MS_PER_CHAR_NO_TTS = 280    # 纯字幕模式（用户 2026-09-03 拍板）：宁长勿短，慢节奏留足默读时间
MIN_DUR_MS = 1200           # 每条最短 1.2s（原 0.9s 偏短，短句字幕闪太快）

# 标点符号 → 断句
HARD_BREAK = re.compile(r'[。！？；…\n]+')
# 逗号是意群边界（模板中"层高4.9米，一楼配10吨行车"拆成两条）
SOFT_BREAK = re.compile(r'[，,]')
# 顿号不拆（模板中"办公楼、食堂、宿舍"为一条）
WHITESPACE = re.compile(r'\s+')


def _clean(text):
    return WHITESPACE.sub('', text or '').strip()


def _char_count(text):
    """字数统计（用于估算字幕停留时长）。

    规则：去掉标点/空格/百分号，汉字每个算 1；**数字串按半数计权**
    （"4000" 读作"四千"≈2 音节，"10.9"≈2 音节），否则数字多的行会被严重低估
    —— 这是早期 185ms/字 系统性偏短 + 数字行虚高到 400ms/字 的根因。
    例："独栋厂房4000-14000平米" → 4+2+2.5+2 = 10.5（旧算法只算 6）。
    """
    t = re.sub(r'[，。；、！？：""\'\'（）()\s.%-]+', '', text or '')
    total = 0
    for run in re.findall(r'[0-9]+', t):
        # 数字串按半数计权（至少 1，避免"5"被算成 0）
        total += max(1, len(run) // 2)
        t = t.replace(run, '', 1)
    total += len(t)  # 剩余汉字/字母
    return total


def _display_text(text, punct=''):
    """生成字幕**显示**文本（2026-09-03 用户定的规则）。

    - `！？` **保留**：设问/感叹的语气全靠它们传达，去掉了观众看不出这是问句；
      这也是切句规则里"以 ！？ 结尾的短句不合并"的原因（保住设问节奏）
    - `、` **删掉**：不出现在字幕里
    - 句号/分号/省略号等句尾标点切句时已分离，字幕惯例也不显示

    只影响字幕显示；给 TTS 的 `tts_text` 仍保留全部标点 —— 朗读需要停顿和语气。
    """
    t = (text or '').replace('、', '')
    tail = ''.join(c for c in (punct or '') if c in '！？!?')
    return t + tail


def split_sentences(script):
    """按硬断句符切句，**保留结尾标点**。

    返回 [(text, punct)]。标点很重要：
    1. 感叹/疑问结尾的短句（如"是的！"）必须独立成段，不能并进上一句，
       否则设问+回答的节奏感就没了
    2. 给 TTS 的文本要带标点，朗读才有停顿和语气（字幕显示则用去标点的版本）

    ⚠️ 换行（\\n）同样是硬断句符：用户手动分行 = 明确的意群边界。
    旧版 regex 只写了 `[。！？；…]+`，漏掉 \\n，换行随后被 `_clean` 的
    `\\s+` 抹平 —— 多行脚本的相邻行会被拼成一整句再按逗号切。
    贵阳案例实测：两行粘成 21 字 / 22 字的字幕（280ms/字 ≈ 6 秒一条），
    整条片子的字幕节奏被拉垮。这里改用常量 HARD_BREAK（本来就含 \\n）。
    """
    parts = []
    toks = re.split('(%s)' % HARD_BREAK.pattern, script)
    for i in range(0, len(toks), 2):
        seg = _clean(toks[i])
        punct = toks[i + 1] if i + 1 < len(toks) else ''
        if seg:
            parts.append((seg, punct))
    return parts


def _split_long_by_comma(sentence, max_chars=15):
    """若句子含逗号，按逗号切分为意群段。

    模板规律：逗号是意群边界（"厂房层高4.9米至10.5米，一楼可配10吨航车"
    拆成两条）。但避免切出过短的碎片（<3字不切，并入前段）。
    返回 [(段, 段末标点)]；若无逗号则返回 [(原句, 原标点)]。
    """
    if isinstance(sentence, tuple):
        sentence, tail_punct = sentence
    else:
        tail_punct = ''
    if not SOFT_BREAK.search(sentence):
        return [(sentence, tail_punct)]
    segs = []
    buf = ''
    for ch in sentence:
        buf += ch
        # 用原始长度判断，不用 _char_count —— 后者会过滤数字，
        # 导致 "500㎡起租" 只算出 3 字、被误当成碎片又合并回去。
        # 阈值按"去掉逗号后的正文字数"算：buf 含逗号，len(buf)>=3 会把
        # "有的，"（正文仅 2 字）也切走，把设问的回答拆成孤立碎片 —— off-by-one。
        if ch in '，,' and len(buf.rstrip('，,')) >= 3:
            segs.append((buf.rstrip('，,'), '，'))
            buf = ''
    if buf:
        segs.append((buf, tail_punct))
    segs = [s for s in segs if s[0]]
    return segs if segs else [(sentence, tail_punct)]


def split_subtitles(script, style_cfg, city_hint='', no_tts=True):
    """主入口：把脚本切分为字幕段列表。

    no_tts=True（默认，纯字幕模式）：不配音，字幕停留 = 朗读时长，
        用 MS_PER_CHAR_NO_TTS(280ms/字)，不除以 speed。用户要求"宁长勿短"。
    no_tts=False（配音模式）：字幕停留 = 朗读时长 / speed，
        用 MS_PER_CHAR(270ms/字)，1.09 倍速后约 248ms/字，贴近真实配音。

    返回 list of dict:
      {"text": str, "chars": int, "src_dur_ms": int, "tgt_dur_ms": int, "speed": float, "role": str}
    role: hook / normal / cta
    """
    sentences = split_sentences(script)
    sub_cfg = style_cfg.get('subtitle', {})
    max_chars = sub_cfg.get('max_chars', 15)

    segs = []
    for s in sentences:
        segs.extend(_split_long_by_comma(s, max_chars=max_chars))

    # 合并过短的碎片：若一段 <4 字且不是钩子，与前面合并。
    # 但**感叹号/问号结尾的短句不合并**——那是语气句（"是的！""真的？"），
    # 并进上一句会把设问+回答的节奏毁掉。
    # 同理，**前一段以 ！？ 等硬标点收尾时，后面的短碎片也不能回并**：
    # 前段已是完整句（设问句），碎片是新句的开头（回答），
    # 回并会 (a) 把问和答熔成一条 (b) 让碎片的 ，/。 覆盖掉前段的 ？，标点丢失。
    # 这种碎片保持独立成段（MIN_DUR_MS 保证不闪太快）。
    merged = []
    i = 0
    while i < len(segs):
        cur, punct = segs[i]
        # 换行同样算"硬断句"：前一段以换行收尾时，后面的短碎片不能回并进来
        prev_hard = bool(merged and merged[-1][1]
                         and any(c in merged[-1][1] for c in '！？!。…\n'))
        if i == 0 and len(segs) > 1 and len(cur) < 4:
            # 首句太短但仍是钩子，保留
            merged.append([cur, punct])
        elif (len(cur) < 4 and merged and not prev_hard
              and punct not in ('！', '？', '!')):
            merged[-1][0] = merged[-1][0] + cur
            merged[-1][1] = punct or merged[-1][1]
        else:
            merged.append([cur, punct])
        i += 1

    # 标记 role（钩子/正文；CTA 单独判断）
    styled = []
    n = len(merged)
    for i, item in enumerate(merged):
        role = 'hook' if i == 0 else 'normal'
        styled.append((item, role))

    # 统一语速；钩子/CTA 保持 1.09
    speed = style_cfg.get('tts', {}).get('normal_speed', 1.09)
    hook_speed = style_cfg.get('tts', {}).get('hook_speed', speed)

    result = []
    for item, role in styled:
        text, punct = item
        chars = _char_count(text)
        # 原始朗读时长 = 字数 * 每字时长
        # no_tts（纯字幕）不配音 → 不加速，直接按朗读速度停留（宁长勿短）
        per_char = MS_PER_CHAR_NO_TTS if no_tts else MS_PER_CHAR
        src_ms = max(chars * per_char, MIN_DUR_MS)
        spd = 1.0 if no_tts else (hook_speed if role == 'hook' else speed)
        tgt_ms = int(src_ms / spd)
        # 换行只是断句标记，不是朗读标点，传给 TTS 前先剔除
        tts_punct = ''.join(c for c in punct if c not in '\n\r')
        result.append({
            # 字幕显示文本：删掉顿号，句尾保留 ！？（规则见 _display_text）
            'text': _display_text(text, punct),
            # 给 TTS 的文本带全部标点，朗读才有停顿和语气（与显示文本规则不同）
            'tts_text': text + (tts_punct if tts_punct not in ('，', ',') else '。'),
            'chars': chars,
            'src_dur_ms': src_ms,
            'tgt_dur_ms': tgt_ms,
            'speed': spd,
            'role': role,
        })

    # 检查结尾是否已经有 CTA（原文自带"点击链接…预约实地考察"时就别再追加，
    # 否则末尾会重复一遍）。判断放宽到末尾三条拼接 + 关键词匹配。
    cta_text = sub_cfg.get('cta', '点击链接 预约实地考察')
    cta_keys = ('点击', '预约', '考察', '联系', '咨询')
    tail = ''.join(r['text'] for r in result[-3:])
    already_cta = any(k in tail for k in cta_keys)
    if not already_cta:
        chars = _char_count(cta_text)
        per_char = MS_PER_CHAR_NO_TTS if no_tts else MS_PER_CHAR
        src_ms = max(chars * per_char, MIN_DUR_MS)
        spd = 1.0 if no_tts else speed
        result.append({
            'text': cta_text,
            'chars': chars,
            'src_dur_ms': src_ms,
            'tgt_dur_ms': int(src_ms / spd),
            'speed': spd,
            'role': 'cta',
        })

    return result


def estimate_duration_ms(subtitles, padding_ms=0):
    """估算总时长 = 所有 tgt_dur 之和 + 段间停顿。"""
    total = sum(s['tgt_dur_ms'] for s in subtitles)
    if len(subtitles) > 1:
        total += (len(subtitles) - 1) * padding_ms
    return total


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    with open('config/default_styles.json', encoding='utf-8') as f:
        cfg = json.load(f)
    # 测试：南京脚本
    script = """真没想到！南京还有租金5块的全新国资厂房！厂房就在南京智能制造产业园，园区位于南京六合主城区，过桥十到十五分钟直通栖霞主城，交通通勤十分便捷。现新建八栋生产厂房，超多户型任选，可整租可分租。厂房层高4.9米至10.5米，一楼可配10吨航车。水电蒸汽配套完善，还配有办公楼、食堂、宿舍等，满足企业生产生活所需。这样的厂房租金仅需4毛。现在入驻园区还能享租金和产业方面的扶持政策。"""
    st = split_subtitles(script, cfg['styles']['3'])
    print(f'切分结果 {len(st)} 条，总时长 {estimate_duration_ms(st)//1000}s:')
    for s in st:
        print(f"  [{s['role']:>4}] {s['text']} ({s['chars']}字, tgt={s['tgt_dur_ms']}ms, spd={s['speed']})")
