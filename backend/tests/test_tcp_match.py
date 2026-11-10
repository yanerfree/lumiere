from app.services.tcp_match import is_match, normalize_hex, validate_match


def test_exact_ignores_surrounding_whitespace_case_sensitive():
    assert is_match("exact", "PING", b"PING\r\n")
    assert not is_match("exact", "PING", b"ping")


def test_contains():
    assert is_match("contains", "PING", b"xxPINGxx")
    assert not is_match("contains", "PING", b"xxPONGxx")


def test_any_matches_everything():
    assert is_match("any", "", b"\x00\x01")


def test_hex_tolerates_0x_and_separators():
    assert normalize_hex("0xFF 0x01") == "ff01"
    assert is_match("hex", "0xFF, 0x01", bytes([0xFF, 1]))
    assert is_match("hex", "FF:01", bytes([0xFF, 1]))


def test_regex_invalid_never_matches():
    assert not is_match("regex", "([", b"abc")


def test_validate():
    assert validate_match("exact", "x") is None
    assert validate_match("any", "") is None
    assert "不支持" in validate_match("fuzzy", "x")
    assert "不能为空" in validate_match("exact", "  ")
    assert "正则" in validate_match("regex", "([")
    assert "十六进制" in validate_match("hex", "ZZ")
    assert "十六进制" in validate_match("hex", "FFF")
    assert validate_match("hex", "0xFF 0x01") is None
