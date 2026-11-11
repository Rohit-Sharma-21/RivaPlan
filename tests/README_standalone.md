# Standalone RivaPlan regression runner

`run_standalone.py` executes the four existing synthetic test batches outside the QGIS desktop interface, using QGIS's own Python environment. It registers the local repository's `provider.py` and the seven algorithm scripts without altering the plugin installed in a QGIS profile.

It requires the following files in the same `tests/` directory:

- `run_smoke_tests.py`
- `run_additional_tests.py`
- `run_generation_qc_tests.py`
- `run_remaining_qc_tests.py`

## Windows

1. Open **OSGeo4W Shell** installed with QGIS.
2. Change directory to your local RivaPlan GitHub repository (the directory containing `provider.py`):

   ```bat
   cd /d "F:\SAS_Drive\workfolder_SAS\paper_writeups\03_qgis_plugins\GitHub\RivaPlan\RivaPlan"
   ```

3. Try the QGIS Python launcher for your installation:

   ```bat
   python-qgis.bat tests\run_standalone.py
   ```

   Some installations use `python-qgis-ltr.bat` instead. If the launcher is not on PATH, find the installed QGIS `bin` folder and invoke its full path (for example `C:\OSGeo4W\bin\python-qgis-ltr.bat`).

On Linux with QGIS Python bindings installed, run `python3 tests/run_standalone.py` from the repository root.

## Results

A successful run prints `STANDALONE RESULT: 16/16 tests passed` and exits with code `0`. A regression failure exits with code `1`; bootstrap problems or missing tests return code `2`. Each test writes only synthetic temporary outputs.

**Important:** These are regression tests, not a measured code coverage report. Successful tests in a desktop QGIS console do not guarantee the same results in a headless environment. Run this separately before configuring GitHub Actions.
