# -*- coding: utf-8 -*-
"""pytest 公共配置：保证以项目根目录导入 src 包。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
