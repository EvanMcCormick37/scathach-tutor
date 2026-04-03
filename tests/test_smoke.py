"""
Smoke tests — verify the project can be imported and basic config works.
"""

from typer.testing import CliRunner

from scathach import __version__
from scathach.cli.main import app
from scathach.config import Settings, TimingMode


def test_version_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_settings_defaults() -> None:
    s = Settings()
    assert s.quality_threshold == 7
    assert s.main_timing == TimingMode.UNTIMED
    assert s.review_timing == TimingMode.UNTIMED


def test_settings_env_override(monkeypatch: object) -> None:
    import os
    monkeypatch.setenv("SCATHACH_QUALITY_THRESHOLD", "9")
    monkeypatch.setenv("SCATHACH_MAIN_TIMING", "timed")
    s = Settings()
    assert s.quality_threshold == 9
    assert s.main_timing == TimingMode.TIMED


def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scathach" in result.output.lower()


def test_cli_ingest_missing_file() -> None:
    """ingest with a nonexistent file should exit with code 1."""
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "nonexistent_file.pdf"])
    assert result.exit_code == 1


def test_cli_topics_empty() -> None:
    """topics with an empty DB should print a friendly message and exit 0."""
    runner = CliRunner()
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = f.name
    try:
        result = runner.invoke(app, ["topics"], env={"SCATHACH_DB_PATH": tmp_db})
        assert result.exit_code == 0
        assert "No topics" in result.output
    finally:
        os.unlink(tmp_db)


def test_new_config_defaults() -> None:
    """New settings have the expected defaults."""
    s = Settings()
    assert s.hydra_in_super_review is False
    assert s.open_doc_on_session is False


def test_new_config_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SCATHACH_HYDRA_IN_SUPER_REVIEW", "true")
    monkeypatch.setenv("SCATHACH_OPEN_DOC_ON_SESSION", "true")
    s = Settings()
    assert s.hydra_in_super_review is True
    assert s.open_doc_on_session is True


def test_cli_super_review_no_api_key() -> None:
    """super-review without an API key should exit 1 with a helpful message."""
    runner = CliRunner()
    result = runner.invoke(
        app, ["super-review"],
        env={"SCATHACH_OPENROUTER_API_KEY": ""},
    )
    assert result.exit_code == 1
    assert "API key" in result.output


def test_cli_review_no_api_key() -> None:
    """review without an API key should exit 1."""
    runner = CliRunner()
    result = runner.invoke(
        app, ["review"],
        env={"SCATHACH_OPENROUTER_API_KEY": ""},
    )
    assert result.exit_code == 1
    assert "API key" in result.output


def test_cli_super_review_help() -> None:
    """super-review --help should describe Hydra and difficulty range."""
    runner = CliRunner()
    result = runner.invoke(app, ["super-review", "--help"])
    assert result.exit_code == 0
    assert "3" in result.output or "hydra" in result.output.lower()
