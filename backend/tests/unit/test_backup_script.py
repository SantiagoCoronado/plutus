"""M7 backup-script guards. Cheap + docker-free: parse the shell script and
assert the safety-critical patterns are present. No live db, no dump, no volumes
touched here — the real backup/restore path is exercised in the M8 live gate."""

import shutil
import subprocess
from pathlib import Path

import pytest

# backend/tests/unit/test_backup_script.py -> repo root -> scripts/backup.sh
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backup.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT.read_text()


def test_script_exists():
    assert SCRIPT.is_file(), f"missing backup script at {SCRIPT}"


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not available")
def test_script_parses_clean():
    # `sh -n` is a syntax check that never executes the script (no db touched)
    result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, f"sh -n failed: {result.stderr}"


def test_uses_custom_format_dump(script_text: str):
    # -Fc (custom format) is the only dump format that survives the TimescaleDB
    # pre_restore()/post_restore() dance
    assert "-Fc" in script_text
    assert "pg_dump" in script_text


def test_atomic_tmp_rename(script_text: str):
    # dump to *.tmp then mv into place: a killed dump never leaves a
    # plausible-looking partial plutus_*.dump
    assert ".tmp" in script_text
    assert "mv " in script_text


def test_prunes_after_14_days(script_text: str):
    # 14-day retention via find -mtime +14 -delete
    assert "-mtime +14" in script_text
    assert "-delete" in script_text


def test_has_one_shot_mode(script_text: str):
    # `backup.sh --now` (used by `make backup-now`) does a single dump + prune
    assert "--now" in script_text


def test_no_forbidden_string(script_text: str):
    # read-only guarantee: the forbidden trading string appears nowhere
    assert "tasa" not in script_text.lower()


def test_never_mutates_the_live_db(script_text: str):
    # a backup only reads; it must never issue destructive SQL against the db
    lowered = script_text.lower()
    for forbidden in ("drop database", "dropdb", "truncate", "delete from"):
        assert forbidden not in lowered, f"backup script must not contain {forbidden!r}"
