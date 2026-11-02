#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""园区锁定回归测试（2026-09-14 新仓事故后建立）。

事故：界面填了「新仓」，但选片用的是脚本推断 —— 脚本里"嘉兴平湖"的"嘉兴"出现更早，
于是锁成嘉兴园区，成片素材全是嘉兴的，而草稿名和台账都显示"新仓"（误导）。
三条修正都在本文件里钉住：
  ① 界面/命令行填的园区名参与锁定（优先于脚本推断）
  ② 脚本多个地名词命中时，**更具体的词（更长）优先**
  ③ 「沈海高速新仓出口」这类写法能被识别（新仓园区识别词含"新仓出口"）

用法：python tests/test_park_locking.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import park_rules as pr  # noqa: E402

MATERIALS = 'E:/园区素材'

# 真实脚本（2026-09-14 新仓片的字幕逐条还原）
XC_SCRIPT = '\n'.join([
    '环沪工业强镇火热招商中！', '厂房租金3毛起', '享超长免租期！', '嘉兴平湖',
    '环沪第一圈层', '无缝接壤上海金山区！', '沈海高速新仓出口', '5分钟直达园区',
    '智谷天地产业园', '国资全新厂房', '整栋10000平米', '2000平起租灵活分割',
    '可租可售', '主导新材料产业', '配套航空航天生命健康等新兴产业集群',
    '现在入驻', '还能享租金优惠和产业扶持政策', '点击链接', '填写信息', '预约实地考察',
])

# (说明, 脚本, 关键字参数, 期望园区文件夹)
CASES = [
    ('界面填园区名 → 按界面填的锁定（本次事故主因）', XC_SCRIPT, {'park_name': '新仓'}, '新仓园区'),
    ('只给脚本 → 更具体的「新仓出口」赢过「嘉兴」', XC_SCRIPT, {}, '新仓园区'),
    ('--park 优先级最高', XC_SCRIPT, {'park': '嘉兴'}, '嘉兴园区'),
    ('界面名与 --park 冲突时 --park 赢', XC_SCRIPT, {'park': '嘉兴', 'park_name': '新仓'}, '嘉兴园区'),
    ('嘉兴脚本（无新仓字样）→ 嘉兴园区', '嘉兴园区标准厂房火热招商', {}, '嘉兴园区'),
    ('贵阳脚本', '贵阳国资直属园区标准厂房', {}, '贵阳园区'),
    ('嘉定脚本（长词 上海嘉定）', '上海嘉定标准厂房', {}, '嘉定园区'),
    ('乌鲁木齐/两河', '乌鲁木齐两河园区招商', {}, '新疆园区'),
    ('苏州脚本', '苏州工业园区标准厂房', {}, '苏州园区'),
    ('南京脚本', '南京江宁园区', {}, '南京园区'),
    ('德州脚本', '山东德州厂房出租', {}, '德州园区'),
    ('脚本没有任何地名 → 拦停（返回 None）', '标准厂房火热招商中', {}, None),
    ('"全新仓库"不误命中新仓', '现新建有20万方全新标准仓库', {}, None),
]

# 需要园区文件夹真实存在；缺失的直接跳过并提示（换机器时素材库可能没搬全）
def main():
    rules = pr.load()
    parks = rules.get('parks', {})
    exist = {f for f in parks if os.path.isdir(os.path.join(MATERIALS, f))}
    print('语义规则版本 v%s，登记园区 %d 个，素材库实到 %d 个'
          % (rules.get('version'), len(parks), len(exist)))

    errs = []
    for name, script, kw, want in CASES:
        if want and want not in exist:
            print('  SKIP  %-42s （素材库缺 %s）' % (name, want))
            continue
        d, folder, how = pr.resolve_park_dir(MATERIALS, script_text=script, **kw)
        ok = folder == want
        print('  %-4s %-42s → %-8s (%s)' % ('OK' if ok else 'FAIL', name, folder, how))
        if not ok:
            errs.append('%s：得到 %s，期望 %s' % (name, folder, want))

    # 长词优先的排序自检
    hits = pr.match_script_parks(XC_SCRIPT, parks)
    if not hits or hits[0][1] != '新仓出口':
        errs.append('长词优先失效：首命中应为「新仓出口」，实际 %s'
                    % (hits[0][1] if hits else '无'))

    print()
    if errs:
        print('=' * 62)
        print('FAIL —— %d 处不符' % len(errs))
        for e in errs:
            print('  ✗', e)
        print('=' * 62)
        return 1
    print('=' * 62)
    print('PASS —— 园区锁定 %d 条用例全部符合（含界面园区名优先、长词优先）' % len(CASES))
    print('=' * 62)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
