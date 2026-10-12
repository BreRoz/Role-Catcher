from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(name: str) -> dict:
    with open(ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config() -> dict:
    return load_yaml("config.yaml")
