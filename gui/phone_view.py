"""Virtual phone view. Frames update the picture only. Element hits stay put."""

from __future__ import annotations
import customtkinter as ctk
from PIL import Image, ImageTk
from typing import Optional, Callable, List, Tuple
from core.element import UIElement, flatten_elements, pick_element


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
        self.locked_bounds = None
        self.show_all_outlines = True
        self.show_only_tagged = False

        self.crop: Optional[Tuple[int, int, int, int]] = None

        self.scale = 0.35
        self.offset_x = 0
        self.offset_y = 0

        self.canvas = ctk.CTkCanvas(self, bg="#000000", highlightthickness=0, cursor="arrow")
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
        self._tag_ids: List[int] = []
        self._tag_bounds: List[tuple] = []
        self._fit = None

    def set_frame(self, screenshot: Image.Image):
        """New mirror pixels only. Does not touch elements or the selection."""
        if screenshot is self.base_image:
            return
        self.base_image = screenshot
        self._present()

    def set_elements(self, elements: List[UIElement]):
        """New hit targets. Called only when the UI hierarchy changes."""
        self.elements = [el for el in elements if not getattr(el, "ignored", False)]
        self._rebuild_tag_boxes()

    def set_data(self, screenshot: Image.Image, root: UIElement,
                 saved_crop: Optional[Tuple[int, int, int, int]] = None,
                 preserve_selection: bool = False):
        self.crop = None
        if not preserve_selection:
            self.selected = None
        self.set_frame(screenshot)
        self.set_elements(flatten_elements(root))

    def set_selected(self, el: Optional[UIElement]):
        self.selected = el
        self.locked_bounds = el.bounds if el else None
        self._draw_locked_outline()

    def clear_selection(self):
        self.set_selected(None)

    def set_outline_mode(self, show_all: bool = True, only_tagged: bool = False):
        self.show_all_outlines = show_all
        self.show_only_tagged = only_tagged
        self._rebuild_tag_boxes()

    def get_crop(self) -> Optional[Tuple[int, int, int, int]]:
        return self.crop

    def reset_crop(self):
        if not self.base_image:
            return
        w, h = self.base_image.size
        inset = int(min(w, h) * 0.04)
        self.crop = (inset, inset, w - inset, h - inset)
        if self.on_crop_changed:
            self.on_crop_changed(self.crop)

    def _on_canvas_resize(self, event):
        new_size = (event.width, event.height)
        if abs(new_size[0] - self._last_canvas_size[0]) < 3 and abs(new_size[1] - self._last_canvas_size[1]) < 3:
            return
        self._last_canvas_size = new_size
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(40, self._present)

    def _calculate_fit(self):
        if not self.base_image:
            return None
        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        if canvas_w < 40 or canvas_h < 40:
            canvas_w, canvas_h = 280, 320
        img_w, img_h = self.base_image.size
        pad = 4
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

    def _present(self):
        """Swap the picture. Redraw boxes only if the fit changed."""
        if not self.base_image:
            return
        result = self._calculate_fit()
        if result is None:
            return
        new_w, new_h = result
        display = self.base_image.resize((max(1, new_w), max(1, new_h)), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(display)
        if self._img_id is None:
            self._img_id = self.canvas.create_image(
                self.offset_x, self.offset_y, anchor="nw", image=self.photo
            )
        else:
            self.canvas.itemconfig(self._img_id, image=self.photo)
            self.canvas.coords(self._img_id, self.offset_x, self.offset_y)
        fit = (new_w, new_h, self.offset_x, self.offset_y, round(self.scale, 4))
        if fit != self._fit:
            self._fit = fit
            self._place_tag_boxes()
            self._draw_locked_outline()
        elif self._sel_id is not None:
            self.canvas.tag_raise(self._sel_id)

    def _rebuild_tag_boxes(self):
        for item in self._tag_ids:
            self.canvas.delete(item)
        self._tag_ids = []
        self._tag_bounds = []
        if not self.show_only_tagged and not self.show_all_outlines:
            self._draw_locked_outline()
            return
        for el in self.elements:
            tagged = bool(el.tag)
            if self.show_only_tagged and not tagged:
                continue
            if not self.show_all_outlines and not tagged:
                continue
            if not tagged and not self.show_all_outlines:
                continue
            l, t, r, b = el.bounds
            if r <= l or b <= t:
                continue
            color = "#5abeff" if tagged else "#ffaf46"
            self._tag_bounds.append((l, t, r, b))
            self._tag_ids.append(self.canvas.create_rectangle(
                0, 0, 0, 0, outline=color, width=2
            ))
        self._place_tag_boxes()
        self._draw_locked_outline()

    def _place_tag_boxes(self):
        for item, bounds in zip(self._tag_ids, self._tag_bounds):
            l, t, r, b = bounds
            x1, y1 = self._img_to_canvas(l, t)
            x2, y2 = self._img_to_canvas(r, b)
            self.canvas.coords(item, x1, y1, x2, y2)

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

    def _redraw(self):
        self._present()
        self._rebuild_tag_boxes()

    def _hit_handle(self, x: int, y: int) -> Optional[str]:
        return None

    def _event_to_img(self, event) -> Tuple[int, int]:
        """Map a click onto the picture that is actually drawn."""
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        if self._img_id is None or self.base_image is None or self.photo is None:
            return self._canvas_to_img(int(x), int(y))
        coords = self.canvas.coords(self._img_id)
        if len(coords) < 2:
            return self._canvas_to_img(int(x), int(y))
        pw = max(1, int(self.photo.width()))
        ph = max(1, int(self.photo.height()))
        img_w, img_h = self.base_image.size
        ix = (x - float(coords[0])) / pw * img_w
        iy = (y - float(coords[1])) / ph * img_h
        return int(round(ix)), int(round(iy))

    def _on_press(self, event):
        self._drag_handle = None
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        if not self.base_image or not self.elements:
            return
        ix, iy = self._event_to_img(event)
        img_w, img_h = self.base_image.size
        if ix < -20 or iy < -20 or ix > img_w + 20 or iy > img_h + 20:
            return
        slop = max(2, int(4 / max(self.scale, 0.15)))
        chosen = pick_element(self.elements, ix, iy, img_w, img_h, slop=slop)
        if chosen is None:
            return
        self.selected = chosen
        self.locked_bounds = tuple(chosen.bounds)
        self._draw_locked_outline()
        if self.on_element_selected:
            self.on_element_selected(chosen)

    def _on_drag(self, event):
        return

    def _on_release(self, event):
        self._drag_handle = None
