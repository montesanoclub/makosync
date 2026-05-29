"""URL normalization (field-bug fix).

The operator may type a bare host, a base URL, or paste the full ingest
endpoint — with or without a trailing slash. All must resolve to the same
correct POST target, never a slashless or doubled path.
"""

from __future__ import annotations

import pytest

from makosync.client import HEAT_PATH, IngestClient, normalize_base_url

BASE = "https://makosmeets.com"


@pytest.mark.parametrize("raw", [
    "https://makosmeets.com",
    "https://makosmeets.com/",
    "https://makosmeets.com///",
    "makosmeets.com",                                  # no scheme
    "  https://makosmeets.com  ",                      # whitespace
    "https://makosmeets.com/api/live-results/ingest",  # pasted full endpoint
    "https://makosmeets.com/api/live-results/ingest/", # full endpoint + slash
    "https://makosmeets.com/api/live-results/ingest/file/",  # pasted file endpoint
])
def test_normalizes_to_base(raw):
    assert normalize_base_url(raw) == BASE


def test_client_builds_correct_heat_url():
    # Whatever was typed, the heat POST must hit exactly base + HEAT_PATH.
    for raw in (BASE, BASE + "/", BASE + "/api/live-results/ingest"):
        c = IngestClient(raw)
        assert f"{c.base_url}{HEAT_PATH}" == "https://makosmeets.com/api/live-results/ingest/"


def test_empty_stays_empty():
    assert normalize_base_url("") == ""
    assert normalize_base_url("   ") == ""
