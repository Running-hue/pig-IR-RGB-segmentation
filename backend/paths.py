"""Paths shared by the server and maintenance scripts."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / 'frontend'
CONFIG_DIR = PROJECT_ROOT / 'config'
STATE_DIR = PROJECT_ROOT / '.state'
MODEL_DIR = PROJECT_ROOT / '.models'
