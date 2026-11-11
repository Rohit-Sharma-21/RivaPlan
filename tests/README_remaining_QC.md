# RivaPlan v2.1 – remaining migration QC regression tests

Run `run_remaining_qc_tests.py` from the QGIS Python Console after enabling the v2.1 plugin. It creates synthetic geometries and writes results in a temporary directory.

Tests cover centerline SHIFT_SPIKE and IRREGULAR_PROGRESSION, plus bankline SHIFT_SPIKE and IRREGULAR_PROGRESSION_OLD. They verify that affected measurements are retained as REVIEW.

The script is syntax-checked but has not been runtime-tested in QGIS. A successful result should be `RESULT: 4/4 tests passed`. If a test fails, share its complete traceback and do not count it as passed.
