#!/usr/bin/env python3
"""
scripts/gen-light-theme.py
──────────────────────────
src/styles/light-theme.css 를 생성한다.

앱의 색상은 Tailwind 임의값 클래스(`bg-[#0b0f1a]`)로 박혀 있어 CSS 변수로
바꿀 수 없다. 그래서 실제로 쓰이는 클래스를 스캔해 `[data-theme="light"]`
아래에서 같은 클래스를 라이트 색으로 재정의한다.

색을 바꾸려면 아래 MAP 만 고치고 이 스크립트를 다시 실행할 것.
    python3 scripts/gen-light-theme.py
"""
import re, pathlib, collections

MAP = {
    # ── 표면: 어두울수록 밝게 ────────────────────────────────────────────
    '#060b14':'#ffffff', '#0b0f1a':'#f6f8fb', '#07101c':'#f6f8fb',
    '#070d18':'#f6f8fb', '#0a0e18':'#f6f8fb', '#0a0f1a':'#f6f8fb',
    '#0d1526':'#f1f4f9', '#0b1220':'#f1f4f9', '#0a1422':'#f1f4f9',
    '#0a1628':'#eef2f8', '#0a1525':'#eef2f8', '#0a1020':'#eef2f8',
    '#0f172a':'#eef2f8', '#0f1724':'#eef2f8', '#0f1e30':'#e9eff8',
    '#111827':'#e9eef6', '#111c2e':'#e4eaf4', '#1a2035':'#e4eaf4',
    '#1a2540':'#e4eaf4', '#1e293b':'#e4eaf4', '#2d3748':'#dce3ee',
    '#0b1a2e':'#e8f0fb', '#1e3a5f':'#cbdcf2',
    # ── 테두리 ───────────────────────────────────────────────────────────
    '#1e2d40':'#dfe5ee', '#2d3f56':'#c9d2e0', '#3d5270':'#aab7c9',
    '#334155':'#c2ccdb', '#2a3444':'#b9c3d2',
    # ── 글자: 밝을수록 진하게 ────────────────────────────────────────────
    '#ffffff':'#ffffff',            # 컬러 버튼 위 흰 글자는 유지
    '#f1f5f9':'#0f172a', '#e2e8f0':'#111827', '#e5e7eb':'#1f2937',
    '#cbd5e1':'#334155', '#94a3b8':'#4b5563', '#7d8ca3':'#55637a',
    '#64748b':'#5b6a80', '#475569':'#4b5563', '#4a5568':'#6b7684',
    '#374151':'#7d8798', '#1f2937':'#374151',
    # ── 강조색: 흰 바탕 대비 확보 ────────────────────────────────────────
    '#3b82f6':'#2563eb', '#60a5fa':'#2563eb', '#2f6fe0':'#1d4ed8',
    '#2563eb':'#1d4ed8', '#1d4ed8':'#1e40af', '#1d5fa0':'#1e40af',
    '#2e75b6':'#1d4ed8',
    '#10b981':'#059669', '#34d399':'#059669', '#059669':'#047857',
    '#ef4444':'#dc2626', '#f87171':'#dc2626', '#dc143c':'#be123c',
    '#dc2626':'#b91c1c', '#b91c1c':'#991b1b',
    '#f59e0b':'#c2740a', '#fbbf24':'#b45309',
    '#9b59b6':'#7e22ce', '#a78bfa':'#7c3aed', '#8b5cf6':'#7c3aed',
    '#c084fc':'#9333ea', '#7c3aed':'#6d28d9', '#8900ff':'#7c3aed',
    '#00e6ff':'#0e7490', '#06b6d4':'#0e7490', '#93c5fd':'#1d4ed8',
    # 브랜드 색(네이버 초록·카카오 노랑)은 그대로 둔다 — 규정된 색이다.
    '#03c75a':'#03c75a', '#fee500':'#fee500',
}

PROP = {
    'bg':'background-color', 'text':'color', 'border':'border-color',
    'fill':'fill', 'stroke':'stroke', 'ring':'--tw-ring-color',
    'divide':'border-color', 'accent':'accent-color',
    'decoration':'text-decoration-color', 'outline':'outline-color',
    'caret':'caret-color', 'shadow':'--tw-shadow-color',
    'from':'--tw-gradient-from', 'to':'--tw-gradient-to', 'via':'--tw-gradient-via',
}
VARIANT = {
    '': '', 'hover:':':hover', 'focus:':':focus',
    'focus-within:':':focus-within', 'group-hover:':'', 'placeholder:':'::placeholder',
}

def esc(cls):
    """'#' 도 반드시 escape — 빠뜨리면 셀렉터가 조용히 무효가 된다."""
    return ''.join('\\' + c if c in '#[]/.:' else c for c in cls)

def rgba(h, pct):
    r, g, b = int(h[1:3],16), int(h[3:5],16), int(h[5:7],16)
    return f"rgb({r} {g} {b} / {pct}%)"

PAT = re.compile(r'((?:[a-z-]+:)*)(bg|text|border|fill|stroke|from|to|via|ring|shadow'
                 r'|decoration|outline|accent|caret|divide|placeholder)-\[(#[0-9a-fA-F]{6})\]((?:/\d+)?)')

def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    found, missing = set(), collections.Counter()
    for f in (root / 'src').rglob('*.tsx'):
        for m in PAT.finditer(f.read_text()):
            found.add((m.group(1), m.group(2), m.group(3).lower(), m.group(4)))

    rules = []
    for variant, prop, hexv, alpha in sorted(found):
        light = MAP.get(hexv)
        if light is None:
            missing[hexv] += 1; continue
        if light == hexv:
            continue
        css_prop, suffix = PROP.get(prop), VARIANT.get(variant)
        if not css_prop or suffix is None:
            continue
        cls = f"{variant}{prop}-[{hexv}]{alpha}"
        value = rgba(light, alpha[1:]) if alpha else light
        sel = f'[data-theme="light"] .{esc(cls)}{suffix}'
        if variant == 'group-hover:':
            sel = f'[data-theme="light"] .group:hover .{esc(cls)}'
        if prop == 'divide':
            sel += ' > :not([hidden]) ~ :not([hidden])'
        rules.append(f"{sel} {{ {css_prop}: {value} !important; }}")

    header = (
        "/*\n"
        " * styles/light-theme.css — 자동 생성 파일. 직접 고치지 말 것.\n"
        " * 색을 바꾸려면 scripts/gen-light-theme.py 의 MAP 을 고치고 다시 실행한다.\n"
        " *\n"
        " * 앱 색상이 Tailwind 임의값 클래스로 박혀 있어 CSS 변수로는 바꿀 수 없다.\n"
        " * 실제로 쓰이는 클래스를 스캔해 [data-theme=\"light\"] 아래에서 재정의한다.\n"
        " */\n\n"
    )
    out = root / 'src/styles/light-theme.css'
    out.write_text(header + "\n".join(rules) + "\n")
    print(f"{out.relative_to(root)}: {len(rules)}개 규칙")
    if missing:
        print("매핑 없는 색상:", ", ".join(f"{c}({n})" for c, n in missing.most_common()))

if __name__ == '__main__':
    main()
