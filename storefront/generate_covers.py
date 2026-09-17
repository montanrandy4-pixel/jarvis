from PIL import Image, ImageDraw, ImageFont
import os

W = H = 1540
OUT = "/tmp/claude-0/-home-user-jarvis/dbca886a-fb30-5c46-8b16-1438ed059ce7/scratchpad/covers"
os.makedirs(OUT, exist_ok=True)

FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
def f(path, size): return ImageFont.truetype(path, size)

INK    = (21, 23, 28)
PANEL  = (30, 33, 40)
LINE   = (44, 48, 57)
CREAM  = (244, 241, 234)
MUTED  = (138, 143, 154)

CREAM_BG    = (240, 236, 228)
CREAM_PANEL = (231, 226, 215)
CREAM_LINE  = (214, 207, 193)
CREAM_MUTED = (122, 116, 104)

ACCENT = {
    "contracts": (224, 122, 95),
    "client":    (95, 180, 156),
    "money":     (110, 147, 214),
    "marketing": (217, 164, 65),
    "bundle":    (176, 124, 196),
}

def wrap(draw, text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def tracked(draw, xy, text, font, fill, spacing=6):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x

# ---------- wireframe motifs ----------
def m_doc(d, x, y, w, h, a, pn, ln):
    d.rounded_rectangle([x, y, x+w, y+h], 18, fill=pn)
    d.rectangle([x, y, x+w, y+10], fill=a)
    cx, cw = x+46, w-92
    cy = y+52
    d.rounded_rectangle([cx, cy, cx+int(cw*0.52), cy+26], 8, fill=a)
    cy += 58
    fracs = [1.0, .93, .97, .66, .88, .95, .52, .91, .78, .96, .61, .85]
    i = 0
    bottom = y+h-112
    while cy < bottom - 20:
        if i and i % 7 == 0:
            cy += 16
            if cy > bottom - 40: break
            d.rounded_rectangle([cx, cy, cx+int(cw*0.34), cy+20], 6, fill=a)
            cy += 46
            i += 1
            continue
        d.rounded_rectangle([cx, cy, cx+int(cw*fracs[i % len(fracs)]), cy+13], 6, fill=ln)
        cy += 32
        i += 1
    sy = y+h-78
    d.rounded_rectangle([cx, sy, cx+150, sy+44], 10, outline=a, width=3)
    d.rounded_rectangle([cx+cw-200, sy+20, cx+cw, sy+26], 3, fill=ln)

def m_sheet(d, x, y, w, h, a, pn, ln):
    d.rounded_rectangle([x, y, x+w, y+h], 18, fill=pn)
    ox, oy = x+20, y+20
    iw, ih = w-40, h-40
    cols, hdr = 5, 58
    cw_ = iw//cols
    d.rectangle([ox, oy, ox+cw_*cols, oy+hdr], fill=a)
    for c in range(cols):
        d.rounded_rectangle([ox+c*cw_+14, oy+hdr//2-6, ox+c*cw_+14+int((cw_-28)*.6), oy+hdr//2+6],
                            5, fill=tuple(int(v*0.55) for v in a))
    rows = max(6, (ih-hdr)//92)
    rh = (ih-hdr)//rows
    highlight = {(2, cols-1), (5, cols-1)}
    for r in range(rows):
        ry = oy+hdr+r*rh
        if r:
            d.line([ox, ry, ox+cw_*cols, ry], fill=ln, width=1)
        for c in range(cols):
            cx0 = ox+c*cw_
            pad = 14
            my = ry+rh//2
            if (r, c) in highlight:
                d.rounded_rectangle([cx0+pad, my-7, cx0+cw_-pad, my+7], 6, fill=a)
            else:
                frac = .78 if (r+c) % 3 else .45
                d.rounded_rectangle([cx0+pad, my-6, cx0+pad+int((cw_-2*pad)*frac), my+6], 5, fill=ln)
    for c in range(1, cols):
        d.line([ox+c*cw_, oy+hdr, ox+c*cw_, oy+hdr+rh*rows], fill=ln, width=2)

def m_kanban(d, x, y, w, h, a, pn, ln):
    cols, gap = 3, 26
    cw_ = (w - gap*(cols-1))//cols
    patterns = [[128, 96, 96, 128, 96], [128, 150, 96, 128], [96, 128, 96, 150, 96]]
    for c in range(cols):
        cx0 = x + c*(cw_+gap)
        d.rounded_rectangle([cx0, y, cx0+cw_, y+h], 16, fill=pn)
        d.rounded_rectangle([cx0+18, y+18, cx0+18+int(cw_*0.5), y+34], 8, fill=a if c == 0 else ln)
        cy = y+58
        pat = patterns[c]
        k = 0
        while cy < y+h-60:
            hh = pat[k % len(pat)]
            if cy+hh > y+h-18:
                hh = (y+h-18) - cy
                if hh < 70: break
            d.rounded_rectangle([cx0+16, cy, cx0+cw_-16, cy+hh], 12,
                                fill=tuple(min(255, v+10) for v in pn))
            d.rounded_rectangle([cx0+32, cy+20, cx0+32+int((cw_-64)*.8), cy+32], 5, fill=ln)
            d.rounded_rectangle([cx0+32, cy+44, cx0+32+int((cw_-64)*.5), cy+54], 5, fill=ln)
            if hh > 120:
                d.ellipse([cx0+32, cy+74, cx0+56, cy+98], fill=a)
            cy += hh+16
            k += 1

def m_calendar(d, x, y, w, h, a, pn, ln):
    d.rounded_rectangle([x, y, x+w, y+h], 18, fill=pn)
    d.rectangle([x, y, x+w, y+58], fill=a)
    cols, rows = 7, 5
    hw = (w-48)//cols
    for c in range(cols):
        hx = x+24+c*hw
        d.rounded_rectangle([hx+hw//2-22, y+23, hx+hw//2+22, y+35], 5,
                            fill=tuple(int(v*0.55) for v in a))
    cw_ = (w-48)//cols; ch_ = (h-100)//rows
    ox, oy = x+24, y+78
    marks = {(0,1),(0,4),(1,0),(1,3),(2,2),(2,5),(3,1),(3,4),(4,0),(4,3),(1,6),(3,6)}
    for r in range(rows):
        for c in range(cols):
            cx0, cy0 = ox+c*cw_, oy+r*ch_
            d.rounded_rectangle([cx0+5, cy0+5, cx0+cw_-5, cy0+ch_-5], 8,
                                fill=(*[min(255, v+9) for v in pn],))
            if (r, c) in marks:
                d.rounded_rectangle([cx0+14, cy0+ch_//2-6, cx0+cw_-14, cy0+ch_//2+6], 5,
                                    fill=a if (r+c) % 2 == 0 else ln)

def m_mail(d, x, y, w, h, a, pn, ln):
    n = 6
    gap = 18
    bh = (h - gap*(n-1))//n
    for i in range(n):
        by = y + i*(bh+gap)
        d.rounded_rectangle([x, by, x+w, by+bh], 14, fill=pn)
        d.ellipse([x+26, by+bh//2-22, x+26+44, by+bh//2+22], fill=a if i == 0 else LINE if pn == PANEL else ln)
        tx = x+92
        d.rounded_rectangle([tx, by+bh//2-26, tx+int((w-150)*.42), by+bh//2-14], 5, fill=ln)
        d.rounded_rectangle([tx, by+bh//2-2, tx+int((w-150)*.86), by+bh//2+9], 5, fill=ln)
        d.rounded_rectangle([tx, by+bh//2+21, tx+int((w-150)*.60), by+bh//2+32], 5, fill=ln)
        if i == 0:
            d.rounded_rectangle([x+w-60, by+bh//2-8, x+w-30, by+bh//2+8], 6, fill=a)

def m_stack(d, x, y, w, h, a, pn, ln):
    offs = [(0, 0), (34, 34), (68, 68)]
    for i, (dx, dy) in enumerate(offs):
        cx0, cy0 = x+dx, y+dy
        cw_, ch_ = w-68, h-68
        shade = tuple(min(255, int(pn[k] * (0.82 + 0.09*i))) for k in range(3))
        d.rounded_rectangle([cx0, cy0, cx0+cw_, cy0+ch_], 18, fill=shade,
                            outline=a if i == len(offs)-1 else None, width=3)
        if i == len(offs)-1:
            px, py = cx0+44, cy0+48
            d.rounded_rectangle([px, py, px+int(cw_*0.46), py+24], 8, fill=a)
            py += 56
            fracs = [.92, .74, .86, .58, .80, .94, .67, .88, .55, .90]
            j = 0
            bottom = cy0+ch_-46
            while py < bottom - 18:
                if j and j % 5 == 0:
                    py += 14
                    if py > bottom - 34: break
                    d.rounded_rectangle([px, py, px+int(cw_*0.30), py+18], 6, fill=a)
                    py += 40
                    j += 1
                    continue
                d.rounded_rectangle([px, py, px+int((cw_-88)*fracs[j % len(fracs)]), py+13], 6, fill=ln)
                py += 32
                j += 1

MOTIF = {"doc": m_doc, "sheet": m_sheet, "kanban": m_kanban,
         "calendar": m_calendar, "mail": m_mail, "stack": m_stack}

def card(fn, title, eyebrow, sub, accent_key, motif, light=False):
    a = ACCENT[accent_key]
    bg, pn, ln, fg, mu = (CREAM_BG, CREAM_PANEL, CREAM_LINE, INK, CREAM_MUTED) if light \
                         else (INK, PANEL, LINE, CREAM, MUTED)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # faint background grid
    grid = tuple(int(bg[k] + ((255-bg[k]) if not light else -18) * (0.045 if not light else 1)) for k in range(3))
    for gx in range(0, W, 56): d.line([gx, 0, gx, H], fill=grid, width=1)
    for gy in range(0, H, 56): d.line([0, gy, W, gy], fill=grid, width=1)

    M = 96
    d.rectangle([0, 0, W, 10], fill=a)

    fe = f(FB, 25)
    tracked(d, (M, M+6), eyebrow.upper(), fe, a, spacing=5)

    ft = f(FB, 82)
    lines = wrap(d, title, ft, W-2*M)
    if len(lines) > 3:
        ft = f(FB, 68); lines = wrap(d, title, ft, W-2*M)
    ty = M+76
    for L in lines:
        d.text((M, ty), L, font=ft, fill=fg)
        ty += int(ft.size*1.18)

    fs = f(FR, 31)
    d.text((M, ty+14), sub, font=fs, fill=mu)

    top = ty + 14 + 31 + 68
    mh = H - top - 150
    MOTIF[motif](d, M, top, W-2*M, mh, a, pn, ln)

    by = H - 104
    d.line([M, by-34, W-M, by-34], fill=ln, width=2)
    fw = f(FB, 27)
    endx = tracked(d, (M, by), "SOLO STACK", fw, fg, spacing=4)
    fb_ = f(FR, 25)
    tag = "DIGITAL DOWNLOAD"
    tw = d.textlength(tag, font=fb_) + len(tag)*3
    tracked(d, (W-M-tw, by+2), tag, fb_, a, spacing=3)

    img.save(os.path.join(OUT, fn), "PNG", optimize=True)
    return fn

ITEMS = [
 ("ss-contract-pack.png","The Freelance Contract Pack","Contracts & Legal","7 agreements · Google Docs, Word, PDF","contracts","doc",False),
 ("ss-proposal-kit.png","The Proposal Kit","Sales & Proposals","4 templates · three-tier pricing · scripts","contracts","doc",False),
 ("ss-onboarding-notion.png","Client Onboarding System","Notion System","Notion duplicate · intake form · setup video","client","kanban",False),
 ("ss-crm-notion.png","Freelance CRM & Pipeline Tracker","Notion System","6-stage pipeline · forecast dashboard","client","kanban",False),
 ("ss-scope-kit.png","Scope Creep Defense Kit","Client Systems","15 scripts · change request form · policies","client","doc",False),
 ("ss-invoice-system.png","Invoice & Payment Chase System","Money & Pricing","5 invoices · aging tracker · 5-email sequence","money","sheet",False),
 ("ss-rate-calculator.png","Rate Calculator & Pricing Toolkit","Money & Pricing","Sheets & Excel · fully formula-driven","money","sheet",False),
 ("ss-tax-tracker.png","Tax & Expense Tracker","Money & Pricing","18 deduction categories · quarterly calculator","money","sheet",False),
 ("ss-portfolio-pack.png","Portfolio & Case Study Pack","Marketing & Growth","3 layouts · Canva & Figma · interview script","marketing","doc",False),
 ("ss-outreach-scripts.png","Cold Outreach Scripts","Marketing & Growth","50 emails · 8 scenarios · follow-up cadence","marketing","mail",False),
 ("ss-content-calendar.png","90-Day Content Calendar","Marketing & Growth","90 prompts · Notion & Sheets · 30 hooks","marketing","calendar",False),
 ("ss-bundle-launch.png","The Launch Kit","Bundle · Save $20","3 templates · start freelancing this week","bundle","stack",True),
 ("ss-bundle-getpaid.png","The Get Paid Bundle","Bundle · Save $28","Contract + proposal + invoice system","bundle","stack",True),
 ("ss-bundle-complete.png","The Complete Solo Stack","Bundle · Save $211","All 11 templates · lifetime updates","bundle","stack",True),
]

for it in ITEMS:
    print(card(*it))
