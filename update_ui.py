import sys, re
path = r'C:\Users\Admin\.gemini\antigravity\scratch\jarvis\ui.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

new_constants = '''SYSTEM_NAME = "J.A.R.V.I.S"
MODEL_BADGE = "MARK L - STARK INDUSTRIES"

C_BG     = "#000508"
C_PRI    = "#00e5ff"
C_MID    = "#0088aa"
C_DIM    = "#004455"
C_DIMMER = "#001a22"
C_ACC    = "#ff7700"
C_ACC2   = "#ffb700"
C_TEXT   = "#aaffff"
C_PANEL  = "#000a10"
C_GREEN  = "#00ff88"
C_RED    = "#ff3333"
C_MUTED  = "#ff2244"
C_SLEEP  = "#331166"

FONT_MAIN = "Consolas"'''

content = re.sub(r'SYSTEM_NAME = "J.A.R.V.I.S".*?C_SLEEP  = "#5533aa"[^\n]*', new_constants, content, flags=re.DOTALL)
content = content.replace('font=("Courier"', 'font=(FONT_MAIN')

draw_func = '''        # Arka plan grid (HUD Crosses)
        for x in range(0, W, 50):
            for y in range(0, H, 50):
                c.create_line(x-2, y, x+3, y, fill=C_DIMMER, width=1)
                c.create_line(x, y-2, x, y+3, fill=C_DIMMER, width=1)

        # HUD Hex Data Sides
        hud_c = self._ac(0, 229, 255, int(self.halo_a * 0.7))
        for i in range(6):
            # Left panel
            c.create_text(25, FCY - 120 + i * 45, text=f"SYS.{i}: {__import__('random').randint(1000, 9999):04X}", fill=hud_c, font=(FONT_MAIN, 9), anchor="w")
            c.create_line(25, FCY - 110 + i * 45, 80, FCY - 110 + i * 45, fill=hud_c)
            # Right panel
            c.create_text(W - 25, FCY - 120 + i * 45, text=f"NET.{i}: {__import__('random').random():.3f}", fill=hud_c, font=(FONT_MAIN, 9), anchor="e")
            c.create_line(W - 80, FCY - 110 + i * 45, W - 25, FCY - 110 + i * 45, fill=hud_c)

        # Halo halkaları (Inner Core Halos)
        for r in range(int(FW * 0.54), int(FW * 0.28), -22):
            frac = 1.0 - (r - FW * 0.28) / (FW * 0.26)
            ga   = max(0, min(255, int(self.halo_a * 0.1 * frac)))
            if self.muted:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=f"#{ga:02x}0011", width=2)
            else:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=self._ac(0, 229, 255, ga), width=2)

        # Pulse dalgaları
        for pr in self.pulse_r:
            pa = max(0, int(220 * (1.0 - pr / (FW * 0.72))))
            r  = int(pr)
            if self.muted:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=self._ac(255, 34, 68, pa // 3), width=2)
            else:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=self._ac(0, 229, 255, pa), width=2)

        # Dönen halkalar (Iron Man Complex Rotating Rings)
        for idx, (r_frac, w_ring, arc_l, gap, dash_pat) in enumerate([
                (0.47, 3, 110, 75, ()),
                (0.44, 1, 360, 0, (2, 4)), 
                (0.39, 2, 75, 55, ()),
                (0.35, 1, 360, 0, (8, 6)),
                (0.31, 2, 55, 38, ())]):
            ring_r = int(FW * r_frac)
            base_a = self.rings_spin[idx % len(self.rings_spin)]
            a_val  = max(0, min(255, int(self.halo_a * (1.0 - idx * 0.15))))
            col    = self._ac(255, 34, 68, a_val) if self.muted else self._ac(0, 229, 255, a_val)
            
            if dash_pat:
                c.create_oval(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r, outline=col, width=w_ring, dash=dash_pat)
            else:
                for s in range(360 // (arc_l + gap)):
                    start = (base_a + s * (arc_l + gap)) % 360
                    c.create_arc(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r, start=start, extent=arc_l, outline=col, width=w_ring, style="arc")

        # Tarama yayları
        sr      = int(FW * 0.49)
        scan_a  = min(255, int(self.halo_a * 1.5))
        arc_ext = 80 if self.speaking else 45
        scan_col = self._ac(255, 34, 68, scan_a) if self.muted else self._ac(0, 229, 255, scan_a)
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr, start=self.scan_angle, extent=arc_ext, outline=scan_col, width=4, style="arc")
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr, start=self.scan2_angle, extent=arc_ext, outline=self._ac(255, 119, 0, scan_a // 2), width=2, style="arc")

        # Derecelendirme işaretleri
        t_out = int(FW * 0.495)
        t_in  = int(FW * 0.472)
        a_mk  = self._ac(0, 229, 255, 180)
        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            inn = t_in if deg % 15 == 0 else t_in + 6
            w = 2 if deg % 45 == 0 else 1
            c.create_line(FCX + t_out * math.cos(rad), FCY - t_out * math.sin(rad),
                          FCX + inn  * math.cos(rad), FCY - inn  * math.sin(rad),
                          fill=a_mk, width=w)

        # Crosshair HUD
        ch_r = int(FW * 0.52)
        gap  = int(FW * 0.18)
        ch_a = self._ac(0, 229, 255, int(self.halo_a * 0.6))
        for x1, y1, x2, y2 in [
                (FCX - ch_r, FCY, FCX - gap, FCY), (FCX + gap, FCY, FCX + ch_r, FCY),
                (FCX, FCY - ch_r, FCX, FCY - gap), (FCX, FCY + gap, FCX, FCY + ch_r)]:
            c.create_line(x1, y1, x2, y2, fill=ch_a, width=1)
            # Add target dots
            c.create_oval(x1-2, y1-2, x1+2, y1+2, fill=ch_a, outline="")

        # Köşe braketleri (Targeting Brackets)
        blen = 30
        bc   = self._ac(0, 229, 255, 220)
        hl = FCX - int(FW * 0.55); hr = FCX + int(FW * 0.55)
        ht = FCY - int(FW * 0.55); hb = FCY + int(FW * 0.55)
        for bx, by, sdx, sdy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                   (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            c.create_line(bx, by, bx + sdx * blen, by,            fill=bc, width=3)
            c.create_line(bx, by, bx,               by + sdy * blen, fill=bc, width=3)
            c.create_polygon(bx, by, bx + sdx * 8, by, bx, by + sdy * 8, fill=bc, outline="")

        # Yüz / orb (Arc Reactor Core)
        if self._has_face:
            fw = int(FW * self.scale)
            if (self._face_scale_cache is None or abs(self._face_scale_cache[0] - self.scale) > 0.004):
                scaled = self._face_pil.resize((fw, fw), Image.BILINEAR)
                tk_img = ImageTk.PhotoImage(scaled)
                self._face_scale_cache = (self.scale, tk_img)
            c.create_image(FCX, FCY, image=self._face_scale_cache[1])
        else:
            orb_r = int(FW * 0.28 * self.scale)
            orb_color = (255, 34, 68) if self.muted else (0, 180, 255)
            for i in range(8, 0, -1):
                r2   = int(orb_r * i / 8)
                frac = i / 8
                ga   = max(0, min(255, int(self.halo_a * 1.3 * frac)))
                cc = self._ac(int(orb_color[0]*frac + 25*(1-frac)),
                              int(orb_color[1]*frac + 50*(1-frac)),
                              int(orb_color[2]*frac + 60*(1-frac)), ga)
                c.create_oval(FCX-r2, FCY-r2, FCX+r2, FCY+r2, fill=cc, outline="")
            c.create_text(FCX, FCY, text="STARK", fill=self._ac(255, 255, 255, min(255, int(self.halo_a * 2))), font=(FONT_MAIN, 11, "bold"))

        # ── Header ────────────────────────────────────────────────────────────
        HDR = 62
        c.create_rectangle(0, 0, W, HDR, fill="#000a10", outline="")
        c.create_line(0, HDR, W, HDR, fill=C_MID, width=2)
        c.create_text(W // 2, 22, text="STARK INDUSTRIES // J.A.R.V.I.S", fill=C_PRI, font=(FONT_MAIN, 16, "bold"))
        c.create_text(W // 2, 44, text="TACTICAL INTELLIGENCE & HUD INTERFACE", fill=C_MID, font=(FONT_MAIN, 9))
        c.create_text(16, 31, text=MODEL_BADGE, fill=C_DIM, font=(FONT_MAIN, 9), anchor="w")
        c.create_text(W - 16, 31, text=time.strftime("%H:%M:%S"), fill=C_ACC, font=(FONT_MAIN, 14, "bold"), anchor="e")

        # ── Durum göstergesi ──────────────────────────────────────────────────
        sy = FCY + int(FW * 0.6) + 20

        if self.muted:
            stat, sc = "[ ⊘ SYSTEM MUTED ]", C_MUTED
        elif self._jarvis_state == "SLEEPING":
            sym = "[◌]" if self.status_blink else "[○]"
            stat, sc = f"{sym} STANDBY MODE", C_SLEEP
        elif self.speaking:
            stat, sc = "[●] TRANSMITTING", C_ACC
        elif self._jarvis_state == "THINKING":
            sym = "[◈]" if self.status_blink else "[◇]"
            stat, sc = f"{sym} ANALYZING", C_ACC2
        elif self._jarvis_state == "PROCESSING":
            sym = "[▷]" if self.status_blink else "[▶]"
            stat, sc = f"{sym} PROCESSING", C_ACC2
        elif self._jarvis_state == "LISTENING":
            sym = "[●]" if self.status_blink else "[○]"
            stat, sc = f"{sym} LISTENING", C_GREEN
        else:
            sym = "[●]" if self.status_blink else "[○]"
            stat, sc = f"{sym} {self.status_text}", C_PRI

        c.create_text(W // 2, sy, text=stat, fill=sc, font=(FONT_MAIN, 12, "bold"))

        # ── Ses dalgası ───────────────────────────────────────────────────────
        wy = sy + 25
        N, BH, bw = 40, 22, 6
        total_w = N * bw
        wx0 = (W - total_w) // 2
        sleeping_ui = (self._jarvis_state == "SLEEPING")
        for i in range(N):
            if self.muted:
                hb, col = 2, C_MUTED
            elif sleeping_ui:
                hb, col = int(2 + 1.5 * math.sin(t * 0.025 + i * 0.45)), C_SLEEP
            elif self.speaking:
                hb, col = __import__('random').randint(4, BH), C_PRI if hb > BH * 0.6 else C_MID
            else:
                hb, col = int(4 + 3 * math.sin(t * 0.1 + i * 0.5)), C_DIM
            bx = wx0 + i * bw
            c.create_rectangle(bx, wy + BH - hb, bx + bw - 2, wy + BH, fill=col, outline="")

        # ── Footer ────────────────────────────────────────────────────────────
        c.create_rectangle(0, H - 28, W, H, fill="#000a10", outline="")
        c.create_line(0, H - 28, W, H - 28, fill=C_DIM, width=1)
        c.create_text(W - 16, H - 14, fill=C_DIM, font=(FONT_MAIN, 8), text="[F4] OVERRIDE MUTE", anchor="e")
        c.create_text(W // 2, H - 14, fill=C_DIM, font=(FONT_MAIN, 8), text="STARK INDUSTRIES  ·  CLASSIFIED  ·  MARK L")'''

start_idx = content.find('        # Arka plan grid')
end_idx = content.find('    # ── Log')
if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + draw_func + '\n\n' + content[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('UI successfully updated.')
