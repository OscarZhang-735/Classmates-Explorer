"""Guard the production release dependency and permission boundaries."""
import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def test_release_dependencies_and_permissions():
    release = workflow('image.yml')
    assert release['on']['push']['branches'] == ['main']
    assert set(release['on']) == {'push', 'workflow_dispatch'}
    assert release['concurrency'] == {'group': 'production-release', 'cancel-in-progress': 'false'}
    assert release['permissions'] == {'contents': 'read'}
    jobs = release['jobs']
    assert jobs['ci']['uses'] == './.github/workflows/ci.yml'
    assert jobs['publish']['needs'] == 'ci'
    assert jobs['backend']['needs'] == 'publish'
    assert jobs['pages']['needs'] == 'backend'
    assert "needs.backend.outputs.deployed == 'true'" in jobs['pages']['if']
    assert all("github.ref == 'refs/heads/main'" in job['if'] for job in jobs.values())
    assert all('always()' not in job['if'] for job in jobs.values())
    assert jobs['publish']['permissions']['packages'] == 'write'
    assert jobs['backend']['environment'] == 'production'
    assert 'permissions' not in jobs['backend']  # inherits read-only contents
    assert jobs['pages']['permissions']['pages'] == 'write'
    steps = jobs['backend']['steps']
    assert 'data.commit.sha === context.sha' in steps[0]['with']['script']
    assert all(step['if'] == "steps.current.outputs.current == 'true'" for step in steps[1:])
    deploy = next(step for step in steps if step.get('id') == 'deploy')
    assert deploy['env']['IMAGE'] == '${{ needs.publish.outputs.image }}'
    code = deploy['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    ast.parse(code)
    assert code.index('check=True') < code.index("f.write('deployed=true")


def test_pages_only_reusable_and_same_revision():
    pages = workflow('pages.yml')
    assert set(pages['on']) == {'workflow_call'}
    build = pages['jobs']['build']
    assert build['if'] == "github.ref == 'refs/heads/main'"
    assert build['steps'][0]['with']['ref'] == '${{ github.sha }}'
    assert pages['jobs']['deploy']['needs'] == 'build'
    assert pages['jobs']['deploy']['environment']['name'] == 'github-pages'
    steps = pages['jobs']['deploy']['steps']
    assert 'data.commit.sha === context.sha' in steps[0]['with']['script']
    assert steps[1]['if'] == "steps.current.outputs.current == 'true'"
    assert workflow('image.yml')['jobs']['pages']['with']['api_base'] == '${{ vars.PAGES_API_BASE }}'


def test_ci_gate_rejects_failed_cancelled_and_skipped(monkeypatch):
    import json
    import pytest

    gate = workflow('ci.yml')['jobs']['required']
    assert gate['if'] == '${{ always() }}'
    code = gate['steps'][0]['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    for outcome in ('success', 'failure', 'cancelled', 'skipped'):
        monkeypatch.setenv('RESULTS', json.dumps({name: {'result': outcome} for name in gate['needs']}))
        if outcome == 'success':
            exec(code, {})
        else:
            with pytest.raises(SystemExit):
                exec(code, {})
