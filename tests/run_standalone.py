"""Run the 16 RivaPlan synthetic regression checks in a standalone QGIS Python process.

Usage (from a QGIS / OSGeo4W Python environment):
    python tests/run_standalone.py

Run this from the cloned RivaPlan repository or pass --plugin-dir PATH.
Does not require pytest and does not alter the installed QGIS plugin.
"""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import runpy
import sys
import traceback
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

SUITES = (
    ('run_smoke_tests.py', 'results', 'tuples'),
    ('run_additional_tests.py', 'results', 'booleans'),
    ('run_generation_qc_tests.py', 'checks', 'booleans'),
    ('run_remaining_qc_tests.py', 'results', 'booleans'),
)


def find_processing_plugins(qgis_prefix: Path):
    candidates = []
    if qgis_prefix:
        candidates.append(qgis_prefix / 'python' / 'plugins')
    for name in ('QGIS_PREFIX_PATH',):
        env = os.environ.get(name)
        if env:
            candidates.append(Path(env) / 'python' / 'plugins')
    root = os.environ.get('OSGEO4W_ROOT')
    if root:
        for qgis_name in ('qgis-ltr', 'qgis'):
            candidates.append(Path(root) / 'apps' / qgis_name / 'python' / 'plugins')
    candidates.extend([Path('/usr/share/qgis/python/plugins'), Path('/usr/local/share/qgis/python/plugins')])
    for path in candidates:
        if (path / 'processing').is_dir():
            return path
    raise RuntimeError('QGIS Processing Python plugins folder not found. Use QGIS-bundled Python/OSGeo4W Shell.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-dir', type=Path, default=Path(__file__).resolve().parent.parent,
                        help='Repository folder containing provider.py and seven tool scripts')
    parser.add_argument('--prefix', type=Path, default=None,
                        help='Optional QGIS application prefix path, e.g. C:/OSGeo4W/apps/qgis-ltr')
    args = parser.parse_args()
    plugin_dir = args.plugin_dir.expanduser().resolve()
    test_dir = plugin_dir / 'tests'
    for f in ('provider.py', 'generate_centerline.py', 'reverse_line_direction.py'):
        if not (plugin_dir / f).is_file():
            parser.error('Missing {} under {}'.format(f, plugin_dir))
    for filename, _, _ in SUITES:
        if not (test_dir / filename).is_file():
            parser.error('Missing tests/{}'.format(filename))

    # Environment must be configured by the QGIS Python launcher before importing qgis.
    from qgis.core import QgsApplication, Qgis
    from qgis.analysis import QgsNativeAlgorithms

    prefix = args.prefix or (Path(os.environ['QGIS_PREFIX_PATH']) if os.environ.get('QGIS_PREFIX_PATH') else None)
    if prefix is not None:
        QgsApplication.setPrefixPath(str(prefix), True)
    app = QgsApplication([], False)
    app.initQgis()
    print('QGIS:', Qgis.QGIS_VERSION)
    print('Plugin source:', plugin_dir)
    try:
        pplugins = find_processing_plugins(Path(QgsApplication.prefixPath()))
        if str(pplugins) not in sys.path:
            sys.path.insert(0, str(pplugins))
        from processing.core.Processing import Processing
        Processing.initialize()
        registry = QgsApplication.processingRegistry()
        if registry.providerById('native') is None:
            registry.addProvider(QgsNativeAlgorithms())

        # Load local repository sources as a temporary package so relative imports work.
        package_name = '_rivaplan_standalone_tests'
        package = types.ModuleType(package_name)
        package.__path__ = [str(plugin_dir)]
        package.__package__ = package_name
        sys.modules[package_name] = package
        provider_module = importlib.import_module(package_name + '.provider')
        provider = provider_module.RivaPlanProvider()
        if not registry.addProvider(provider):
            raise RuntimeError('Failed to register local RivaPlan Processing provider')

        total = 0
        passed_total = 0
        for filename, result_key, value_type in SUITES:
            print('\n=== {} ==='.format(filename))
            globals_ = runpy.run_path(str(test_dir / filename), run_name='__main__')
            checks = globals_.get(result_key)
            if not isinstance(checks, list):
                raise RuntimeError('{} did not return a list of test results'.format(filename))
            if value_type == 'tuples':
                successes = sum(item[1] == 'PASS' for item in checks)
            else:
                successes = sum(value is True for value in checks)
            print('Verified by runner: {}/{}'.format(successes, len(checks)))
            total += len(checks)
            passed_total += successes
        print('\nSTANDALONE RESULT: {}/{} tests passed'.format(passed_total, total))
        if total != 16:
            print('WARNING: Expected 16 tests; found {}'.format(total))
            return 2
        return 0 if passed_total == total else 1
    finally:
        # Avoid forced QGIS teardown here: some QGIS/GDAL distributions may
        # crash when providers are destroyed before outstanding Python wrappers.
        # The process exits immediately after this script finishes.
        pass


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
