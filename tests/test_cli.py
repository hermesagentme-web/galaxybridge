import pytest
from galaxybridge.cli import main, build_parser


def test_frame_command_needs_no_hardware(capsys):
    assert main(['buds', 'frame']) == 0
    assert capsys.readouterr().out.startswith('FC 0B')


def test_removed_pass_command_is_not_exposed():
    with pytest.raises(SystemExit):
        build_parser().parse_args(['pass', 'status'])
