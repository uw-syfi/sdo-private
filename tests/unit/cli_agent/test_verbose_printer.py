"""Tests for the RLM verbose printer."""

from app_operator.cli_agent.rlm.verbose import VerbosePrinter


def test_disabled_printer_is_silent(capsys):
    printer = VerbosePrinter(enabled=False)
    printer.header("model", 5, 0)
    printer.iteration(1, 5)
    printer.llm_call("model", 100)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_does_not_recurse():
    printer = VerbosePrinter(enabled=False)
    printer._print("hello")


def test_verbose_printer_writes_log_file(tmp_path):
    log_file = tmp_path / "rlm_verbose.log"
    printer = VerbosePrinter(enabled=True, log_file=log_file)

    printer.llm_call("test-model", 42)
    printer.final_answer("done")
    printer.close()

    assert log_file.exists()
    content = log_file.read_text()
    assert "test-model" in content
    assert "done" in content


def test_close_is_idempotent(tmp_path):
    printer = VerbosePrinter(enabled=True, log_file=tmp_path / "rlm_verbose.log")
    printer.close()
    printer.close()
