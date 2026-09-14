from lpddr_fail_analyzer.hexutil import parse_hex_dump, parse_int_field


def test_parse_int_field_hex_and_decimal():
    assert parse_int_field("0x1BA5") == 0x1BA5
    assert parse_int_field("7") == 7
    assert parse_int_field(7) == 7
    assert parse_int_field(7.0) == 7


def test_parse_hex_dump_unprefixed():
    assert parse_hex_dump("80") == 0x80
    assert parse_hex_dump("8000800080") == 0x8000800080
    assert parse_hex_dump("0x80") == 0x80
