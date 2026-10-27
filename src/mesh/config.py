"""Settings live in config/*.yaml and data products in products/; code reads them here. Environment variables
only choose directories and endpoints."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml


def repo_root() -> Path:
    env = os.environ.get("MESH_ROOT")
    if env:
        return Path(env)
    for p in Path(__file__).resolve().parents:
        if (p / "config" / "platform.yaml").exists():
            return p
    return Path.cwd()


def config_dir() -> Path:
    return Path(os.environ.get("MESH_CONFIG_DIR", repo_root() / "config"))


def products_dir() -> Path:
    return Path(os.environ.get("MESH_PRODUCTS_DIR", repo_root() / "products"))


def data_dir() -> Path:
    return Path(os.environ.get("MESH_DATA_DIR", repo_root() / "data"))


def reports_dir() -> Path:
    return Path(os.environ.get("MESH_REPORTS_DIR", repo_root() / "reports"))


def model_mode() -> str:
    """'simulated' (default: tests, CI, demo) or 'live' (Bedrock / Anthropic with credentials)."""
    return os.environ.get("MESH_MODEL", "simulated")


@cache
def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((config_dir() / f"{name}.yaml").read_text())
    return data if isinstance(data, dict) else {}


def platform() -> dict[str, Any]:
    return load("platform")
