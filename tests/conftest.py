"""Shared fixtures for the skive test suite."""

from unittest.mock import Mock

import pytest

from skive import common


@pytest.fixture(autouse=True)
def _no_eject(request, monkeypatch):
    """Never open a real optical tray from a test.

    Opt out with @pytest.mark.real_eject for tests that inject their own
    `run` into eject_disc.
    """
    if request.node.get_closest_marker("real_eject"):
        return
    monkeypatch.setattr(common, "eject_disc", Mock())
