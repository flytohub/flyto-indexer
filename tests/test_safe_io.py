"""Tests for safe I/O utilities (atomic writes)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from safe_io import atomic_write_text, atomic_write_json, atomic_write_lines


class TestAtomicWriteText:

    def test_basic_write(self, tmp_path):
        path = tmp_path / "test.txt"
        atomic_write_text(path, "hello world")
        assert path.read_text() == "hello world"

    def test_overwrite_existing(self, tmp_path):
        path = tmp_path / "test.txt"
        path.write_text("old")
        atomic_write_text(path, "new")
        assert path.read_text() == "new"

    def test_no_temp_file_left_on_success(self, tmp_path):
        path = tmp_path / "test.txt"
        atomic_write_text(path, "content")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "test.txt"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "file.txt"
        atomic_write_text(path, "content")
        assert path.read_text() == "content"

    def test_unicode_content(self, tmp_path):
        path = tmp_path / "unicode.txt"
        content = "你好世界 🌍 café"
        atomic_write_text(path, content)
        assert path.read_text() == content


class TestAtomicWriteJson:

    def test_basic_json(self, tmp_path):
        path = tmp_path / "data.json"
        data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        atomic_write_json(path, data)
        loaded = json.loads(path.read_text())
        assert loaded == data

    def test_unicode_json(self, tmp_path):
        path = tmp_path / "data.json"
        data = {"name": "你好"}
        atomic_write_json(path, data, ensure_ascii=False)
        content = path.read_text()
        assert "你好" in content  # not escaped


class TestAtomicWriteLines:

    def test_basic_lines(self, tmp_path):
        path = tmp_path / "data.jsonl"
        lines = [
            json.dumps({"id": "a", "value": 1}) + "\n",
            json.dumps({"id": "b", "value": 2}) + "\n",
        ]
        atomic_write_lines(path, lines)
        content = path.read_text()
        assert content.count("\n") == 2

    def test_generator_input(self, tmp_path):
        path = tmp_path / "data.jsonl"

        def gen():
            for i in range(5):
                yield f"line {i}\n"

        atomic_write_lines(path, gen())
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 5

    def test_empty_input(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        atomic_write_lines(path, [])
        assert path.read_text() == ""
