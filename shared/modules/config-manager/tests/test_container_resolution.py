"""Which containers are ours, and which container is a given service.

Both questions used to be answered by looking at container *names*, which
packaging backlog #7 ruled out everywhere else in the stack:

- ``list_containers`` accepted any container whose name merely contained the
  project name. The default project is ``pro``, so ``prometheus`` and
  ``proxy`` were reported as part of the Smoking Pi stack.
- ``resolve_container_name`` matched the compose *service* label alone, so on
  a host running two editions the first container the daemon listed won -
  and ``POST /restart`` could restart the other edition's SmokePing.

The Docker SDK is never allowed to reach a daemon here: every test hands the
API a fake client whose containers are plain objects.
"""

import pytest

import api as api_module


class FakeContainer:
    def __init__(self, name, project=None, service=None, status='running'):
        self.name = name
        self.status = status
        self.short_id = name[:12]
        self.attrs = {'State': {'Status': status}}
        self.labels = {}
        if project is not None:
            self.labels['com.docker.compose.project'] = project
        if service is not None:
            self.labels['com.docker.compose.service'] = service
        self.restarted = False

    def restart(self, timeout=None):
        self.restarted = True


class FakeContainers:
    def __init__(self, running, stopped=()):
        self.running = list(running)
        self.stopped = list(stopped)
        self.list_calls = []

    def list(self, all=False):
        self.list_calls.append(all)
        return self.running + self.stopped if all else self.running

    def get(self, name):
        for container in self.running + self.stopped:
            if container.name == name:
                return container
        raise AssertionError(f"no such container: {name}")


class FakeClient:
    def __init__(self, containers):
        self.containers = containers


@pytest.fixture()
def fake_docker(monkeypatch):
    """Install a fake ``docker.from_env`` and hand back its container table."""
    def install(running, stopped=()):
        containers = FakeContainers(running, stopped)
        monkeypatch.setattr(api_module.docker, 'from_env',
                            lambda *a, **kw: FakeClient(containers))
        return containers
    return install


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as client:
        yield client


# --- resolve_container_name -------------------------------------------------

def test_service_label_alone_is_not_enough(monkeypatch, fake_docker):
    """Two editions on one host: the project label decides which is ours."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    fake_docker([
        FakeContainer('basic-smokeping-1', project='basic', service='smokeping'),
        FakeContainer('pro-smokeping-1', project='pro', service='smokeping'),
    ])
    assert api_module.resolve_container_name('smokeping') == 'pro-smokeping-1'


def test_order_does_not_decide(monkeypatch, fake_docker):
    """The same two containers listed the other way round still resolve here."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "basic")
    fake_docker([
        FakeContainer('pro-smokeping-1', project='pro', service='smokeping'),
        FakeContainer('basic-smokeping-1', project='basic', service='smokeping'),
    ])
    assert api_module.resolve_container_name('smokeping') == 'basic-smokeping-1'


def test_another_projects_service_is_not_resolved(monkeypatch, fake_docker):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    fake_docker([
        FakeContainer('basic-smokeping-1', project='basic', service='smokeping'),
    ])
    with pytest.raises(Exception, match="not found"):
        api_module.resolve_container_name('smokeping')


def test_unlabeled_container_named_after_the_service_is_not_resolved(
        monkeypatch, fake_docker):
    """A hand-started ``docker run --name smokeping`` is not part of the stack."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    fake_docker([FakeContainer('smokeping')])
    with pytest.raises(Exception, match="not found"):
        api_module.resolve_container_name('smokeping')


def test_fixed_container_name_still_resolves(monkeypatch, fake_docker):
    """Services with an explicit ``container_name`` carry the labels too."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    fake_docker([
        FakeContainer('smokeping-mcp-server', project='pro', service='mcp-server'),
    ])
    assert api_module.resolve_container_name('mcp-server') == 'smokeping-mcp-server'


def test_stopped_containers_are_resolvable(monkeypatch, fake_docker):
    """Resolving is how a caller asks to restart or inspect a stopped one."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    containers = fake_docker(
        running=[],
        stopped=[FakeContainer('pro-smokeping-1', project='pro',
                               service='smokeping', status='exited')],
    )
    assert api_module.resolve_container_name('smokeping') == 'pro-smokeping-1'
    assert containers.list_calls == [True]


def test_project_name_defaults_when_the_variable_is_absent(
        monkeypatch, fake_docker):
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    fake_docker([FakeContainer('pro-smokeping-1', project='pro',
                               service='smokeping')])
    assert api_module.resolve_container_name('smokeping') == 'pro-smokeping-1'


# --- restart, the path the web admin's button takes -------------------------

def test_restart_reaches_this_editions_smokeping(monkeypatch, fake_docker):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "pro")
    other = FakeContainer('basic-smokeping-1', project='basic',
                          service='smokeping')
    ours = FakeContainer('pro-smokeping-1', project='pro', service='smokeping')
    fake_docker([other, ours])

    result = api_module.api.restart_smokeping()

    assert result['success'] is True
    assert ours.restarted is True
    assert other.restarted is False


# --- list_containers --------------------------------------------------------

def test_substring_matches_are_not_our_stack(client, fake_docker):
    """``pro`` is a substring of plenty of things nobody asked us about."""
    fake_docker([
        FakeContainer('prometheus'),
        FakeContainer('proxy'),
        FakeContainer('nginx-proxy-manager'),
        FakeContainer('pro-smokeping-1', project='pro', service='smokeping'),
    ])
    body = client.get('/api/containers').get_json()
    assert body['success'] is True
    assert body['project'] == 'pro'
    assert [c['name'] for c in body['containers']] == ['pro-smokeping-1']


def test_labeled_members_are_listed_whatever_they_are_called(client, fake_docker):
    fake_docker([
        FakeContainer('smokeping-mcp-server', project='pro', service='mcp-server'),
        FakeContainer('pro-grafana-1', project='pro', service='grafana'),
        FakeContainer('tunnel-grafana'),
        FakeContainer('basic-grafana-1', project='basic', service='grafana'),
    ])
    body = client.get('/api/containers').get_json()
    assert sorted(c['name'] for c in body['containers']) == [
        'pro-grafana-1', 'smokeping-mcp-server',
    ]
    services = {c['name']: c['service'] for c in body['containers']}
    assert services['smokeping-mcp-server'] == 'mcp-server'


# --- the endpoints' contracts ----------------------------------------------

def test_container_name_endpoint_answers_for_our_project(client, fake_docker):
    fake_docker([
        FakeContainer('basic-smokeping-1', project='basic', service='smokeping'),
        FakeContainer('pro-smokeping-1', project='pro', service='smokeping'),
    ])
    body = client.get('/api/containers/smokeping').get_json()
    assert body == {'success': True, 'service': 'smokeping',
                    'container_name': 'pro-smokeping-1'}


def test_container_status_endpoint_answers_for_our_project(client, fake_docker):
    fake_docker([
        FakeContainer('pro-smokeping-1', project='pro', service='smokeping'),
        FakeContainer('basic-smokeping-1', project='basic', service='smokeping'),
    ])
    body = client.get('/api/containers/smokeping/status').get_json()
    assert body['container_name'] == 'pro-smokeping-1'
    assert body['status'] == 'running'


def test_unresolvable_service_is_a_404_without_exception_text(client, fake_docker):
    fake_docker([FakeContainer('basic-smokeping-1', project='basic',
                               service='smokeping')])
    response = client.get('/api/containers/smokeping')
    assert response.status_code == 404
    body = response.get_json()
    assert body['success'] is False
    assert body['error'] == "Container not found or Docker unavailable"
    assert len(body['error_id']) == 8


def test_docker_failure_body_carries_nothing_from_the_exception(
        client, monkeypatch):
    secret = "/var/run/docker.sock: permission denied for user hunter2"

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(api_module.docker, 'from_env', boom)
    response = client.get('/api/containers')
    assert response.status_code == 500
    assert secret not in response.get_data(as_text=True)
    assert "hunter2" not in response.get_data(as_text=True)
    assert len(response.get_json()['error_id']) == 8
