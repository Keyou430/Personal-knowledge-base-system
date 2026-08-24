# -*- coding: utf-8 -*-
"""Lightweight filesystem operations for knowledge domains."""

from __future__ import annotations

import os
import shutil
from typing import Any

from config import DOMAINS_DIR, RAW_DIR


def list_domains() -> list[str]:
    if not os.path.exists(DOMAINS_DIR):
        return []
    return sorted(
        name
        for name in os.listdir(DOMAINS_DIR)
        if os.path.isdir(os.path.join(DOMAINS_DIR, name))
    )


def create_domain(domain: str) -> bool:
    path = os.path.join(DOMAINS_DIR, domain)
    if os.path.exists(path):
        return False
    os.makedirs(path, exist_ok=True)
    return True


def delete_domain(domain: str) -> bool:
    path = os.path.join(DOMAINS_DIR, domain)
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path)
    return True


def list_domain_files(domain: str = "默认") -> list[dict[str, Any]]:
    domain_raw_dir = os.path.join(RAW_DIR, domain)
    if not os.path.exists(domain_raw_dir):
        return []

    icon_map = {
        ".pdf": "📄", ".docx": "📝", ".doc": "📝",
        ".pptx": "📊", ".ppt": "📊", ".md": "📋",
        ".txt": "📃", ".jpg": "🖼️", ".jpeg": "🖼️",
        ".png": "🖼️", ".bmp": "🖼️",
    }
    files = []
    for name in sorted(os.listdir(domain_raw_dir)):
        file_path = os.path.join(domain_raw_dir, name)
        if not os.path.isfile(file_path):
            continue
        size = os.path.getsize(file_path)
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        ext = os.path.splitext(name)[1].lower()
        files.append({
            "name": name,
            "icon": icon_map.get(ext, "📎"),
            "size": size_str,
            "ext": ext,
            "path": file_path,
        })
    return files
