from modules.qt.utils import format_file_size


def test_format_file_size_bytes():
    assert format_file_size(0) == "0 o"
    assert format_file_size(1) == "1 o"
    assert format_file_size(1023) == "1023 o"


def test_format_file_size_kilobytes():
    assert format_file_size(1024) == "1.0 Ko"
    assert format_file_size(1536) == "1.5 Ko"
    assert format_file_size(1024 * 1024 - 1) == "1024.0 Ko"


def test_format_file_size_megabytes():
    assert format_file_size(1024 * 1024) == "1.0 Mo"
    assert format_file_size(int(1.5 * 1024 * 1024)) == "1.5 Mo"


def test_format_file_size_gigabytes():
    assert format_file_size(1024 * 1024 * 1024) == "1.00 Go"
    assert format_file_size(int(2.5 * 1024 * 1024 * 1024)) == "2.50 Go"


def test_format_file_size_terabytes():
    assert format_file_size(1024 * 1024 * 1024 * 1024) == "1.00 To"
    assert format_file_size(int(3.25 * 1024**4)) == "3.25 To"


def test_format_file_size_boundary_just_below_next_unit():
    # Juste en dessous du seuil Mo : reste en Ko
    assert format_file_size(1024 * 1024 - 1) == "1024.0 Ko"
    # Juste en dessous du seuil Go : reste en Mo
    assert format_file_size(1024 * 1024 * 1024 - 1) == "1024.0 Mo"
