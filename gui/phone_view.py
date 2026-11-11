"""Virtual phone view – always-on focus crop, auto-ignore outside."""

from __future__ import annotations
import customtkinter as ctk
from PIL import Image, ImageTk, ImageDraw
from typing import Optional, Callable, List, Tuple
from core.element import UIElement, flatten_elements


class PhoneView(ctk.CTkFrame):
    HANDLE_SIZE = 16
    CROP_LINE = (100, 180, 255, 240)
    CROP_DIM = (0, 0, 0, 140)

    def __init__(
        self,
        master,
        on_element_selected: Optional[Callable[[UIElement], None]] = None,
        on_crop_changed: Optional[Callable[[Tuple[int, int, int, int]], None]] = None,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_element_selected = on_element_selected
        self.on_crop_changed = on_crop_changed

        self.elements: List[UIElement] = []
        self.selected: Optional[UIElement] = None
        self.locked_bounds = None  # survives refreshes
        self.show_all_outlines = True
        self.show_only_tagged = False

        # Always-on focus crop (image coords)
        self.crop: Optional[Tuple[int, int, int, int]] = None

        self.scale = 0.35
        self.offset_x = 0
        self.offset_y = 0

        self.canvas = ctk.CTkCanvas(self, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.photo = None
        self.base_image: Optional[Image.Image] = None
        self._resize_job = None
        self._last_canvas_size = (0, 0)
        self._drag_handle: Optional[str] = None
        self._img_id = None
        self._sel_id = None

    def set_data(self, screenshot: Image.Image, root: UIElement,
                 saved_crop: Optional[Tuple[int, int, int, int]] = None,
                 preserve_selection: bool = False):
        self.base_image = screenshot.copy()
        self.elements = flatten_elements(root)
        if not preserve_selection:
            self.selected = None
            # do not clear locked_bounds here
        w, h = screenshot.size
        # Crop disabled
        self.crop = None
        self._auto_ignore()
        # Drop ignored elements so we never draw or hit-test them
        self.elements = [el for el in self.elements if not el.ignored]
        self._redraw()

    def set_selected(self, el: Optional[UIElement]):
        self.selected = el
        if el:
            self.locked_bounds = el.bounds
        else:
            self.locked_bounds = None
        self._redraw()

    def set_outline_mode(self, show_all: bool = True, only_tagged: bool = False):
        self.show_all_outlines = show_all
        self.show_only_tagged = only_tagged
        self._redraw()

    def get_crop(self) -> Optional[Tuple[int, int, int, int]]:
        return self.crop

    def reset_crop(self):
        if not self.base_image:
            return
        w, h = self.base_image.size
        inset = int(min(w, h) * 0.04)
        self.crop = (inset, inset, w - inset, h - inset)
        self._auto_ignore()
        self._redraw()
        if self.on_crop_changed:
            self.on_crop_changed(self.crop)

    def _auto_ignore(self):
        """Ignore every element fully outside the crop. Un-ignore those inside."""
        if not self.crop:
            return
        cl, ct, cr, cb = self.crop
        for el in self.elements:
            l, t, r, b = el.bounds
            outside = r < cl or l > cr or b < ct or t > cb
            el.ignored = outside

    def _on_canvas_resize(self, event):
        new_size = (event.width, event.height)
        if abs(new_size[0] - self._last_canvas_size[0]) < 3 and abs(new_size[1] - self._last_canvas_size[1]) < 3:
            return
        self._last_canvas_size = new_size
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(40, self._redraw)

    def _calculate_fit(self):
        if not self.base_image:
            return None
        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        if canvas_w < 40 or canvas_h < 40:
            canvas_w, canvas_h = 380, 720
        img_w, img_h = self.base_image.size
        pad = 8
        self.scale = max(0.15, min((canvas_w - pad) / img_w, (canvas_h - pad) / img_h, 1.0))
        new_w = int(img_w * self.scale)
        new_h = int(img_h * self.scale)
        self.offset_x = max(0, (canvas_w - new_w) // 2)
        self.offset_y = max(0, (canvas_h - new_h) // 2)
        return new_w, new_h

    def _img_to_canvas(self, x: int, y: int) -> Tuple[int, int]:
        return int(x * self.scale) + self.offset_x, int(y * self.scale) + self.offset_y

    def _canvas_to_img(self, x: int, y: int) -> Tuple[int, int]:
        return int((x - self.offset_x) / self.scale), int((y - self.offset_y) / self.scale)

    def _redraw(self):
        if not self.base_image:
            return
        result = self._calculate_fit()
        if result is None:
            return
        new_w, new_h = result

        img = self.base_image.copy().convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        base_draw = ImageDraw.Draw(img)

        # Always draw crop dim + border
        if False and self.crop:
            cl, ct, cr, cb = self.crop
            w, h = img.size
            # Clamp to valid range
            cl = max(0, min(cl, w - 1))
            cr = max(cl + 1, min(cr, w))
            ct = max(0, min(ct, h - 1))
            cb = max(ct + 1, min(cb, h))
            self.crop = (cl, ct, cr, cb)
            draw.rectangle([0, 0, w, ct], fill=self.CROP_DIM)
            if cb < h:
                draw.rectangle([0, cb, w, h], fill=self.CROP_DIM)
            if cl > 0:
                draw.rectangle([0, ct, cl, cb], fill=self.CROP_DIM)
            if cr < w:
                draw.rectangle([cr, ct, w, cb], fill=self.CROP_DIM)
            draw.rectangle([cl, ct, cr, cb], outline=self.CROP_LINE, width=4)

        for el in self.elements:
            if el.ignored:
                continue
            if self.show_only_tagged and not el.tag and el is not self.selected:
                continue
            if not self.show_all_outlines and el is not self.selected:
                continue
            l, t, r, b = el.bounds
            if r <= l or b <= t:
                continue
            if el is self.selected:
                color, width = (90, 220, 160), 4
            elif el.tag:
                color, width = (90, 190, 255), 3
            else:
                color, width = (255, 175, 70), 2
            base_draw.rectangle([l, t, r, b], outline=color, width=width)
            if el.tag:
                tw = len(el.tag) * 9 + 10
                base_draw.rectangle([l, max(0, t - 22), l + tw, t], fill=color)
                base_draw.text((l + 3, max(1, t - 20)), el.tag, fill=(15, 15, 20))

        # Draw locked selection bounds even if element object changed
        if self.locked_bounds:
            l, t, r, b = self.locked_bounds
            base_draw.rectangle([l, t, r, b], outline=(90, 220, 160), width=4)

        composed = Image.alpha_composite(img, overlay).convert("RGB")
        display = composed.resize((new_w, new_h), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(display)
        if self._img_id is None:
            self._img_id = self.canvas.create_image(
                self.offset_x, self.offset_y, anchor="nw", image=self.photo
            )
        else:
            self.canvas.itemconfig(self._img_id, image=self.photo)
            self.canvas.coords(self._img_id, self.offset_x, self.offset_y)
        self._draw_locked_outline()

    def _draw_locked_outline(self):
        if not self.locked_bounds or not self.base_image:
            if self._sel_id is not None:
                self.canvas.delete(self._sel_id)
                self._sel_id = None
            return
        l, t, r, b = self.locked_bounds
        x1, y1 = self._img_to_canvas(l, t)
        x2, y2 = self._img_to_canvas(r, b)
        if self._sel_id is None:
            self._sel_id = self.canvas.create_rectangle(
                x1, y1, x2, y2, outline="#5adca0", width=3
            )
        else:
            self.canvas.coords(self._sel_id, x1, y1, x2, y2)
            self.canvas.itemconfig(self._sel_id, outline="#5adca0", width=3)
        self.canvas.tag_raise(self._sel_id)

    def _draw_handles(self):
        cl, ct, cr, cb = self.crop
        edges = {
            "left":   self._img_to_canvas(cl, (ct + cb) // 2),
            "right":  self._img_to_canvas(cr, (ct + cb) // 2),
            "top":    self._img_to_canvas((cl + cr) // 2, ct),
            "bottom": self._img_to_canvas((cl + cr) // 2, cb),
        }
        hs = self.HANDLE_SIZE // 2
        for name, (cx, cy) in edges.items():
            # soft glow circle
            self.canvas.create_oval(
                cx - hs - 2, cy - hs - 2, cx + hs + 2, cy + hs + 2,
                fill="", outline="#5a9fd4", width=1, tags=("handle", name),
            )
            self.canvas.create_oval(
                cx - hs, cy - hs, cx + hs, cy + hs,
                fill="#7eb8e8", outline="#ffffff", width=2, tags=("handle", name),
            )

    def _hit_handle(self, x: int, y: int) -> Optional[str]:
        if not self.crop:
            return None
        cl, ct, cr, cb = self.crop
        points = {
            "left":   self._img_to_canvas(cl, (ct + cb) // 2),
            "right":  self._img_to_canvas(cr, (ct + cb) // 2),
            "top":    self._img_to_canvas((cl + cr) // 2, ct),
            "bottom": self._img_to_canvas((cl + cr) // 2, cb),
        }
        hs = self.HANDLE_SIZE + 6
        for name, (cx, cy) in points.items():
            if abs(x - cx) <= hs and abs(y - cy) <= hs:
                return name
        return None

    def _on_press(self, event):
        handle = self._hit_handle(event.x, event.y)
        if handle:
            self._drag_handle = handle
            return
        self._drag_handle = None
        if not self.base_image or not self.elements:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        candidates = []
        for el in self.elements:
            if el.ignored:
                continue
            l, t, r, b = el.bounds
            if l <= ix <= r and t <= iy <= b:
                candidates.append(((r - l) * (b - t), el))
        if candidates:
            img_w, img_h = self.base_image.size
            # Hard-reject anything that is basically the full screen border
            real = []
            for area, el in candidates:
                l, t, r, b = el.bounds
                width = r - l
                height = b - t
                if width > img_w * 0.9 or height > img_h * 0.9:
                    continue
                if l <= 2 and t <= 2 and r >= img_w - 2 and b >= img_h - 2:
                    continue
                real.append((area, el))
            if not real:
                self.selected = None
                return  # nothing selectable
            real.sort(key=lambda c: c[0])
            chosen = real[0][1]
            self.selected = chosen
            self.locked_bounds = chosen.bounds
            self._draw_locked_outline()
            if self.on_element_selected:
                self.on_element_selected(chosen)

    def _on_drag(self, event):
        if not self._drag_handle or not self.crop or not self.base_image:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        w, h = self.base_image.size
        cl, ct, cr, cb = list(self.crop)
        min_size = 50
        if self._drag_handle == "left":
            cl = max(0, min(ix, cr - min_size))
        elif self._drag_handle == "right":
            cr = min(w, max(ix, cl + min_size))
        elif self._drag_handle == "top":
            ct = max(0, min(iy, cb - min_size))
        elif self._drag_handle == "bottom":
            cb = min(h, max(iy, ct + min_size))
        self.crop = (cl, ct, cr, cb)
        self._auto_ignore()
        self._redraw()

    def _on_release(self, event):
        if self._drag_handle and self.crop:
            self._auto_ignore()
            if self.on_crop_changed:
                self.on_crop_changed(self.crop)
        self._drag_handle = None
        self._redraw()
