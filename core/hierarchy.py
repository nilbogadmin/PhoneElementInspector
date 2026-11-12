"""Parse Android UI hierarchy XML into UIElement tree."""

from __future__ import annotations
import re
from pathlib import Path
from typing import Optional
from lxml import etree

from .element import UIElement


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int]:
    """Parse bounds string like '[0,0][1080,2400]' into (l, t, r, b)."""
    if not bounds_str:
        return (0, 0, 0, 0)
    nums = re.findall(r"\d+", bounds_str)
    if len(nums) >= 4:
        return tuple(int(x) for x in nums[:4])  # type: ignore
    return (0, 0, 0, 0)


def node_to_element(node, index: int = 0) -> UIElement:
    """Convert an lxml node to UIElement."""
    attrib = node.attrib

    el = UIElement(
        index=index,
        text=attrib.get("text", ""),
        resource_id=attrib.get("resource-id", ""),
        class_name=attrib.get("class", ""),
        package=attrib.get("package", ""),
        content_desc=attrib.get("content-desc", ""),
        checkable=attrib.get("checkable", "false").lower() == "true",
        checked=attrib.get("checked", "false").lower() == "true",
        clickable=attrib.get("clickable", "false").lower() == "true",
        enabled=attrib.get("enabled", "true").lower() == "true",
        focusable=attrib.get("focusable", "false").lower() == "true",
        focused=attrib.get("focused", "false").lower() == "true",
        scrollable=attrib.get("scrollable", "false").lower() == "true",
        long_clickable=attrib.get("long-clickable", "false").lower() == "true",
        password=attrib.get("password", "false").lower() == "true",
        selected=attrib.get("selected", "false").lower() == "true",
        bounds=parse_bounds(attrib.get("bounds", "")),
    )

    for i, child in enumerate(node):
        el.children.append(node_to_element(child, index=i))

    return el


def parse_hierarchy_xml(xml_content: str | bytes) -> UIElement:
    """Parse every window. The app is usually not the first one."""
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")

    root = etree.fromstring(xml_content)
    if root.tag == "hierarchy" and len(root) > 1:
        windows = [node_to_element(child, index=i) for i, child in enumerate(root)]
        max_r = max((w.bounds[2] for w in windows), default=0)
        max_b = max((w.bounds[3] for w in windows), default=0)
        return UIElement(
            class_name="hierarchy",
            bounds=(0, 0, max_r, max_b),
            clickable=False,
            children=windows,
        )
    if root.tag == "hierarchy" and len(root) == 1:
        return node_to_element(root[0])
    return node_to_element(root)


def load_hierarchy_from_file(path: str | Path) -> UIElement:
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    return parse_hierarchy_xml(content)


def hierarchy_to_xml(root: UIElement) -> str:
    """Optional: convert back to XML (useful for debugging)."""
    def build(node: UIElement) -> etree._Element:
        attrib = {
            "index": str(node.index),
            "text": node.text or "",
            "resource-id": node.resource_id or "",
            "class": node.class_name or "",
            "package": node.package or "",
            "content-desc": node.content_desc or "",
            "checkable": str(node.checkable).lower(),
            "checked": str(node.checked).lower(),
            "clickable": str(node.clickable).lower(),
            "enabled": str(node.enabled).lower(),
            "focusable": str(node.focusable).lower(),
            "focused": str(node.focused).lower(),
            "scrollable": str(node.scrollable).lower(),
            "long-clickable": str(node.long_clickable).lower(),
            "password": str(node.password).lower(),
            "selected": str(node.selected).lower(),
            "bounds": f"[{node.bounds[0]},{node.bounds[1]}][{node.bounds[2]},{node.bounds[3]}]",
        }
        el = etree.Element("node", attrib=attrib)
        for child in node.children:
            el.append(build(child))
        return el

    hierarchy = etree.Element("hierarchy", rotation="0")
    hierarchy.append(build(root))
    return etree.tostring(hierarchy, pretty_print=True, encoding="unicode")
