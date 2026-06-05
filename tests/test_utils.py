from pipeline.utils import slugify, timestamp_to_seconds, seconds_to_timestamp


def test_slugify_lowercases():
    assert slugify("Hello World") == "hello_world"


def test_slugify_replaces_spaces():
    assert slugify("a b c") == "a_b_c"


def test_slugify_strips_non_alphanumeric():
    assert slugify("What??! Yeah!!") == "what_yeah"


def test_slugify_collapses_multiple_underscores():
    assert slugify("a   b___c") == "a_b_c"


def test_slugify_strips_leading_trailing_underscores():
    assert slugify("__hello__") == "hello"


def test_timestamp_to_seconds_full_hms():
    assert timestamp_to_seconds("01:30:15.5") == 5415.5


def test_timestamp_to_seconds_ms():
    assert timestamp_to_seconds("02:45") == 165.0


def test_timestamp_to_seconds_float():
    assert timestamp_to_seconds(90.0) == 90.0


def test_timestamp_to_seconds_int():
    assert timestamp_to_seconds(60) == 60.0


def test_seconds_to_timestamp_full():
    assert seconds_to_timestamp(3661.5) == "01:01:01.50"


def test_seconds_to_timestamp_zero():
    assert seconds_to_timestamp(0) == "00:00:00.00"


def test_seconds_to_timestamp_rounds():
    assert seconds_to_timestamp(1.999) == "00:00:02.00"


def test_seconds_to_timestamp_negative_clamps():
    assert seconds_to_timestamp(-5) == "00:00:00.00"
