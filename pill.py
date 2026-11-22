import tkinter as tk
import time
import math
from PIL import Image, ImageDraw, ImageTk

class FloatingPill:
    def __init__(self, on_cancel=None, on_confirm=None):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)

        # Transparency key color on Windows
        self.TRANS_COLOR = "#000001"
        self.root.config(bg=self.TRANS_COLOR)
        self.root.attributes('-transparentcolor', self.TRANS_COLOR)

        # Compact, sleek dimensions
        self.width = 32
        self.height = 108
        self.SCALE = 4  # 4x supersampling for ultra-crisp, buttery smooth anti-aliased curves

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.x = screen_w - self.width - 24
        self.y = (screen_h - self.height) // 2
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

        self.on_cancel = on_cancel
        self.on_confirm = on_confirm

        self.mode = "toggle" # "toggle" (hands-free with buttons) or "ptt" (hold-to-talk, minimal)
        self.state = "idle" # "idle", "recording", "processing"
        self.volume_level = 0.0
        self.smoothed_vol = 0.0
        self.anim_phase = 0.0
        self.hover_cancel = False
        self.hover_confirm = False

        self._drag_start_x = 0
        self._drag_start_y = 0

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=self.TRANS_COLOR,
            highlightthickness=0
        )
        self.canvas.pack(fill='both', expand=True)

        self.canvas.bind('<ButtonPress-1>', self._on_click)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<Motion>', self._on_hover)

        self.photo_img = None
        self._animate()

    def set_mode(self, mode: str):
        self.mode = mode
        new_height = 108 if self.mode == "toggle" else 64
        if new_height != self.height:
            self.height = new_height
            screen_h = self.root.winfo_screenheight()
            self.y = (screen_h - self.height) // 2
            self.canvas.config(height=self.height)
            self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

    def set_state(self, state: str):
        self.state = state

    def _on_click(self, event):
        x, y = event.x, event.y
        if self.mode == "toggle":
            # Top 24px is Cancel ✕
            if y <= 24:
                if self.on_cancel:
                    self.on_cancel()
                return
            # Bottom 26px is Confirm ✓
            if y >= self.height - 26:
                if self.on_confirm:
                    self.on_confirm()
                return
        # Otherwise drag to reposition
        self._drag_start_x = x
        self._drag_start_y = y

    def _on_drag(self, event):
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        self.x += dx
        self.y += dy
        self.root.geometry(f"+{self.x}+{self.y}")

    def _on_hover(self, event):
        if self.mode != "toggle":
            self.hover_cancel = False
            self.hover_confirm = False
            return
        y = event.y
        new_cancel = (y <= 24)
        new_confirm = (y >= self.height - 26)
        if new_cancel != self.hover_cancel or new_confirm != self.hover_confirm:
            self.hover_cancel = new_cancel
            self.hover_confirm = new_confirm

    def set_volume(self, vol: float):
        self.volume_level = max(0.0, min(1.0, vol))

    def _render_pill(self):
        S = self.SCALE
        W = self.width * S
        H = self.height * S
        
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 1. Outer Pill Shape (Deep Obsidian with Brushed Metallic Gold Rim)
        pad = 2 * S
        pill_rect = (pad, pad, W - pad, H - pad)
        radius = (W - 2 * pad) // 2

        # Rounded rectangle with golden accent outline
        draw.rounded_rectangle(
            pill_rect,
            radius=radius,
            fill=(14, 15, 18, 250),           # Deep Obsidian Black
            outline=(180, 142, 55, 235),      # Metallic Gold Rim
            width=max(2, int(1.4 * S))
        )

        cx = W // 2

        # 2. Top: Minimalist '✕' Cancel Icon (ONLY in Hands-Free Toggle Mode)
        if self.mode == "toggle":
            cy = 14 * S
            csz = 3.2 * S
            x_color = (239, 68, 68, 255) if self.hover_cancel else (165, 145, 105, 200)
            draw.line((cx - csz, cy - csz, cx + csz, cy + csz), fill=x_color, width=int(1.2 * S))
            draw.line((cx - csz, cy + csz, cx + csz, cy - csz), fill=x_color, width=int(1.2 * S))

        # 3. Middle Section: Either Rotating Animation (Processing) or Equalizer Bars (Recording)
        mid_y = H // 2
        if self.state == "processing":
            # Rotating Glowing Golden Spinner indicating background transcription
            self.anim_phase += 0.35
            spin_r = 8.5 * S
            spin_box = (cx - spin_r, mid_y - spin_r, cx + spin_r, mid_y + spin_r)
            
            # Subtle background guide ring
            draw.ellipse(spin_box, outline=(55, 45, 22, 140), width=int(1.8 * S))
            
            # Bright spinning golden arc (110 degrees)
            start_deg = (self.anim_phase * 22.0) % 360
            draw.arc(spin_box, start=start_deg, end=start_deg + 110, fill=(245, 180, 45, 255), width=int(2.4 * S))
            
            # Pulsing center golden spark
            dot_r = (2.2 + 0.6 * math.sin(self.anim_phase * 1.5)) * S
            draw.ellipse((cx - dot_r, mid_y - dot_r, cx + dot_r, mid_y + dot_r), fill=(255, 220, 90, 245))
        else:
            # Dynamic Sine-Wave Golden Equalizer Bars
            self.smoothed_vol += (self.volume_level - self.smoothed_vol) * 0.25
            self.anim_phase += 0.20

            num_bars = 7 if self.mode == "toggle" else 5
            bar_spacing = 4.8 * S
            start_y = mid_y - (num_bars // 2) * bar_spacing

            for i in range(num_bars):
                by = int(start_y + i * bar_spacing)
                sine_val = math.sin(self.anim_phase + i * 0.72)
                bar_factor = 0.20 + (0.13 * (1 + sine_val)) + (self.smoothed_vol * 1.55 * (1.1 - abs(i - (num_bars // 2)) * 0.15))
                bar_w = min(19 * S, max(3.5 * S, int(17 * S * bar_factor)))
                
                bx1 = (W - bar_w) // 2
                bx2 = bx1 + bar_w

                gold_r = min(255, int(220 + sine_val * 30))
                gold_g = min(255, int(170 + sine_val * 25))
                gold_b = max(10, int(45 + sine_val * 15))
                bar_color = (gold_r, gold_g, gold_b, 245)

                draw.line((bx1, by, bx2, by), fill=bar_color, width=int(1.8 * S))

        # 4. Bottom: Radiant Gold '✓' Checkmark Emblem (ONLY in Hands-Free Toggle Mode)
        if self.mode == "toggle":
            btn_y = H - 14 * S
            btn_r = 7.0 * S
            btn_bg = (245, 158, 11, 255) if not self.hover_confirm else (251, 191, 36, 255)
            draw.ellipse((cx - btn_r, btn_y - btn_r, cx + btn_r, btn_y + btn_r), fill=btn_bg)

            # Crisp Obsidian Checkmark inside emblem
            chk_color = (14, 15, 18, 255)
            p1 = (cx - 3.0 * S, btn_y + 0.1 * S)
            p2 = (cx - 1.0 * S, btn_y + 2.3 * S)
            p3 = (cx + 3.2 * S, btn_y - 2.3 * S)
            draw.line([p1, p2, p3], fill=chk_color, width=int(1.3 * S), joint="curve")

        # High quality Lanczos downsampling for ultra-smooth edges
        smooth_img = img.resize((self.width, self.height), Image.Resampling.LANCZOS)
        
        # Replace alpha background with transparency key color
        final_img = Image.new("RGB", (self.width, self.height), (0, 0, 1)) # #000001
        final_img.paste(smooth_img, (0, 0), smooth_img)
        return final_img

    def _animate(self):
        try:
            rendered = self._render_pill()
            self.photo_img = ImageTk.PhotoImage(rendered)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self.photo_img)
        except Exception:
            pass
        self.root.after(35, self._animate)

    def show(self):
        self.root.deiconify()

    def hide(self):
        self.root.withdraw()

if __name__ == "__main__":
    pill = FloatingPill()
    pill.set_volume(0.5)
    pill.root.mainloop()
