"""The observer must not leak ctypes type metadata into the measured process."""
import ctypes
import gc
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows process counters')
def test_sampler_reuses_ctypes_pointer_types_and_reports_private_bytes():
    from tools.measure_creative_resources import resources
    resources()
    gc.collect()
    before = len(ctypes._pointer_type_cache)
    for _ in range(181):
        sample = resources()
    gc.collect()
    assert len(ctypes._pointer_type_cache) == before
    assert sample['private_bytes'] > 0
    assert sample['working_set'] > 0
