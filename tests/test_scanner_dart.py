"""Tests for the dependency-free Dart scanner."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import DependencyType, SymbolType
from scanner.dart import DartScanner


@pytest.fixture
def scanner():
    return DartScanner("mobile")


DART_SOURCE = """import 'package:flutter/widgets.dart';
import '../data/client.dart';

/// Opens the command center.
void openCommandCenter(String projectId) {
  runApp(CommandCenter(projectId: projectId));
}

/// Main command center widget.
class CommandCenter extends StatelessWidget {
  const CommandCenter({required this.projectId});

  final String projectId;

  /// Builds the command center.
  @override
  Widget build(BuildContext context) {
    return Text(projectId);
  }

  String get label => 'Command center';
}

enum RunState { queued, running, complete }
"""


def test_supported_extension(scanner):
    assert scanner.supported_extensions == [".dart"]


def test_scans_flutter_types_functions_methods_and_getters(scanner):
    symbols, _dependencies = scanner.scan_file(Path("lib/main.dart"), DART_SOURCE)
    by_name = {symbol.name: symbol for symbol in symbols}

    assert by_name["openCommandCenter"].symbol_type == SymbolType.FUNCTION
    assert by_name["CommandCenter"].symbol_type == SymbolType.COMPONENT
    assert by_name["CommandCenter.CommandCenter"].symbol_type == SymbolType.METHOD
    assert by_name["CommandCenter.build"].symbol_type == SymbolType.METHOD
    assert by_name["CommandCenter.label"].metadata == {"getter": True}
    assert by_name["RunState"].symbol_type == SymbolType.TYPE
    assert by_name["CommandCenter"].summary == "Main command center widget."
    assert by_name["CommandCenter.build"].summary == "Builds the command center."


def test_scans_import_dependencies(scanner):
    _symbols, dependencies = scanner.scan_file(Path("lib/main.dart"), DART_SOURCE)

    imports = [dep.target_id for dep in dependencies if dep.dep_type == DependencyType.IMPORTS]
    assert imports == ["package:flutter/widgets.dart", "../data/client.dart"]


def test_ignores_calls_inside_method_bodies(scanner):
    symbols, _dependencies = scanner.scan_file(Path("lib/main.dart"), DART_SOURCE)
    names = {symbol.name for symbol in symbols}

    assert "runApp" not in names
    assert "Text" not in names
