"""Docker stage identity regressions shared by findings and dependency inventory."""
from pathlib import Path

import pytest

from src.dependency_scanner import _parse_dockerfile
from src.dockerfile_model import from_references, image_version
from src.dockerfile_scanner import _scan_single_dockerfile


def inspect(tmp_path, content):
    path = tmp_path / 'Dockerfile'
    path.write_text(content)
    return _scan_single_dockerfile(path, 'Dockerfile'), _parse_dockerfile(path, tmp_path)


def test_multistage_aliases_are_not_external_images(tmp_path):
    content = (
        'FROM node:22-bookworm-slim AS base\n'
        'FROM base AS builder\n'
        'FROM base AS browser\n'
        'FROM base\n'
    )
    issues, deps = inspect(tmp_path, content)
    assert not [i for i in issues if i['rule'] == 'FROM_LATEST']
    assert [(d.name, d.version) for d in deps] == [('node', '22-bookworm-slim')]
    assert [r.kind for r in from_references(content)] == ['external', 'stage', 'stage', 'stage']


@pytest.mark.parametrize('image,expected,alert', [
    ('alpine', ('alpine', 'latest'), True),
    ('alpine:latest', ('alpine', 'latest'), True),
    ('registry.example:5000/team/node', ('registry.example:5000/team/node', 'latest'), True),
    ('registry.example:5000/team/node:22', ('registry.example:5000/team/node', '22'), False),
    ('node@sha256:' + 'a'*64, ('node', 'sha256:' + 'a'*64), False),
    ('node:latest@sha256:' + 'a'*64, ('node', 'sha256:' + 'a'*64), False),
])
def test_external_images_remain_visible(tmp_path, image, expected, alert):
    issues, deps = inspect(tmp_path, 'FROM ' + image + '\n')
    assert image_version(image) == expected
    assert [(d.name, d.version) for d in deps] == [expected]
    assert bool([i for i in issues if i['rule'] == 'FROM_LATEST']) is alert


def test_platform_case_continuations_and_file_scope(tmp_path):
    content = (
        '# escape=`\n'
        'from --platform=$BUILDPLATFORM `\n'
        ' node:22 AS Base\n'
        'FrOm bAsE aS build\n'
        'FROM scratch\n'
    )
    refs = from_references(content)
    assert [(r.line, r.kind) for r in refs] == [(2, 'external'), (4, 'stage'), (5, 'scratch')]
    assert from_references('FROM base')[0].kind == 'external'
    assert from_references('FROM builder\nFROM node:22 AS builder')[0].kind == 'external'
    issues, deps = inspect(tmp_path, content)
    assert len(deps) == 1
    assert not [i for i in issues if i['rule'] == 'FROM_LATEST']


def test_dynamic_and_heredoc_do_not_invent_dependencies(tmp_path):
    content = 'ARG BASE\nFROM $BASE AS base\nRUN <<EOF\nFROM malicious:latest\nEOF\nFROM base\n'
    issues, deps = inspect(tmp_path, content)
    assert deps == []
    assert not [i for i in issues if i['rule'] == 'FROM_LATEST']
    assert [r.kind for r in from_references(content)] == ['dynamic', 'stage']


def test_user_must_belong_to_final_stage(tmp_path):
    issues, _ = inspect(tmp_path, 'FROM node:22 AS base\nUSER 1000\nFROM base\n')
    assert not [i for i in issues if i['rule'] == 'NO_USER']
    issues, _ = inspect(tmp_path, 'FROM node:22 AS build\nUSER 1000\nFROM node:22\n')
    assert any(i['rule'] == 'NO_USER' for i in issues)
    assert all('runs as root' not in i['description'] for i in issues)


def test_quoted_shift_and_here_strings_do_not_hide_following_from():
    text = "FROM node:22 AS base\nRUN echo '<<EOF'\nRUN cat <<<hello\nFROM ubuntu\n"
    refs = from_references(text)
    assert [ref.image for ref in refs] == ["node:22", "ubuntu"]


def test_quoted_heredoc_delimiter_hides_body_not_next_instruction():
    text = 'FROM node:22 AS base\nRUN cat <<"EOF"\nFROM fake\nEOF\nFROM base AS final\n'
    refs = from_references(text)
    assert [(ref.image, ref.kind) for ref in refs] == [("node:22", "external"), ("base", "stage")]
