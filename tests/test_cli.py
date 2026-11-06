import pytest

from label_tools.cli import main


def test_verify_cli(root, capsys):
    assert main(["verify", str(root)]) == 0
    assert '"status": "PASS"' in capsys.readouterr().out


def test_empty_export_fails(root):
    with pytest.raises(SystemExit) as result:
        main(["export", str(root)])
    assert result.value.code == 2


def test_bad_port(root):
    with pytest.raises(SystemExit) as result:
        main(["serve", str(root), "--port", "-1"])
    assert result.value.code == 2
