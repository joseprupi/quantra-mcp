from quantra_mcp.__main__ import parse_args


def test_defaults_are_stdio() -> None:
    args = parse_args([])
    assert args.http is False and args.port == 8765 and args.host == "127.0.0.1"


def test_http_flags() -> None:
    args = parse_args(["--http", "--port", "9000", "--host", "0.0.0.0"])
    assert args.http is True and args.port == 9000 and args.host == "0.0.0.0"
