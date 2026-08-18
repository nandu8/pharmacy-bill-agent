from conftest import SAMPLES_DIR

from pharmacy_agent.formats.detect import (
    FORMAT_A_CSV,
    FORMAT_B_CSV,
    FORMAT_C_XLS,
    FORMAT_D_PDF,
    detect_format,
)


def _read(name: str) -> bytes:
    return (SAMPLES_DIR / name).read_bytes()


def test_detects_format_a_csv():
    data = _read("PH-26-49832_16-Aug-26_172026215756652.csv")
    assert detect_format(data) == FORMAT_A_CSV


def test_detects_format_b_csv():
    data = _read("PSPH12474.CSV")
    assert detect_format(data) == FORMAT_B_CSV


def test_detects_format_c_xls_all_samples():
    for f in SAMPLES_DIR.glob("*.xls"):
        assert detect_format(f.read_bytes()) == FORMAT_C_XLS, f.name


def test_detects_pdf_all_samples():
    for f in SAMPLES_DIR.glob("*.pdf"):
        assert detect_format(f.read_bytes()) == FORMAT_D_PDF, f.name


def test_does_not_route_xls_to_openpyxl_or_default_engine():
    # Regression guard for the exact failure mode in PRD S7.3: reading a
    # BIFF2 file with no engine, or with engine="openpyxl", must fail --
    # if it ever starts succeeding, our "openpyxl can't read this" premise
    # (and therefore the reason detect_format routes to xlrd) is stale.
    import io

    import pandas as pd
    import pytest

    data = next(SAMPLES_DIR.glob("*.xls")).read_bytes()
    with pytest.raises(Exception):
        pd.read_excel(io.BytesIO(data), engine="openpyxl")
