#!/usr/bin/env python3
"""Run independent agent-records unittest classes concurrently."""

from __future__ import annotations

import concurrent.futures
import importlib
import inspect
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULES = sorted("tests." + path.stem for path in
                 (ROOT / "tests").glob("test_agent_records_*.py"))


def discover_classes():
  classes = []
  for module_name in MODULES:
    module = importlib.import_module(module_name)
    for _, candidate in inspect.getmembers(module, inspect.isclass):
      if (issubclass(candidate, unittest.TestCase) and
          candidate.__module__ == module_name and
          unittest.defaultTestLoader.getTestCaseNames(candidate)):
        classes.append(module_name + "." + candidate.__name__)
  return classes


CLASSES = discover_classes()


def worker_count():
  return max(1, os.cpu_count() or 1)


def run(test_class):
  env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
  result = subprocess.run(
    [sys.executable, "-m", "unittest", test_class], cwd=ROOT, env=env,
    capture_output=True, text=True)
  return test_class, result


def main():
  with concurrent.futures.ThreadPoolExecutor(
      max_workers=worker_count()) as pool:
    results = list(pool.map(run, CLASSES))
  for test_class, result in results:
    print("== " + test_class + " ==")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
  return 1 if any(result.returncode for _, result in results) else 0


if __name__ == "__main__":
  sys.exit(main())
