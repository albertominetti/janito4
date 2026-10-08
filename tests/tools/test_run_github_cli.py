from janito.tools.system import run_github_cli
from janito.tools.system.run_github_cli import RunGitHubCLI


def test_run_invokes_gh_directly_without_shell(monkeypatch):
    captured = {}

    def fake_stream_execute(command, *args, **kwargs):
        captured["command"] = command
        return 0, ["ok\n"], [], 1

    monkeypatch.setattr(RunGitHubCLI, "_gh_checked", True)
    monkeypatch.setattr(RunGitHubCLI, "_gh_path", r"C:\Program Files\GitHub CLI\gh.exe")
    monkeypatch.setattr(run_github_cli, "stream_execute", fake_stream_execute)

    result = RunGitHubCLI().run('issue list --state open --limit 100 --json number,title')

    assert captured["command"] == [
        r"C:\Program Files\GitHub CLI\gh.exe",
        "issue",
        "list",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title",
    ]
    assert result["success"] is True
