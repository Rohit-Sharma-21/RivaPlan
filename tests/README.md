# RivaPlan v2.1 – Centerline generation and QC tests

Run `run_generation_qc_tests.py` **inside QGIS Python Console** after enabling RivaPlan v2.1.

These synthetic checks exercise straight-corridor centerline generation, an abrupt bend flagged by the centerline-migration tool, and rejection of invalid progression-QC limits. The tests use temporary GeoPackages, never modify your original data, and report individual PASS/FAIL lines. A test failing does not establish that the algorithm is defective: inspect the traceback, geometry and assumptions.

This script has been syntax-checked only. It has not been run in QGIS here. The tests are not a coverage report and do not establish cross-version compatibility.
