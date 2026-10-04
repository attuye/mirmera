"""Bring specs, diffs and past bugs in as artifacts, instead of pasting them.

- A GitHub pull request: title, description and diff, as a "diff".
- GitHub issues by label, or Jira issues by JQL: one line per issue, as "bug_import".
- A Git diff between two refs of a repository on this computer, as a "diff"
  (for branches not pushed yet; the backend runs where the code is).

Each import returns (type, label, content, source). The content is capped so a
huge diff can't flood the AI; what was cut is said at the end.
"""
import re
import subprocess
from pathlib import Path

from app import models
from app.trackers import TrackerError, get_tracker

MAX_CHARS = 60_000
BODY_CHARS = 300


class ImportProblem(Exception):
    """The import can't be done as asked (bad input). The message says why."""


def _cap(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS] + f"\n[... cut: {len(text) - MAX_CHARS} more characters not imported]"


def _one_line(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= BODY_CHARS else text[:BODY_CHARS - 1] + "…"


def _issue_lines(issues: list[dict]) -> str:
    return "\n".join(f"{i['key']} [{i['status']}] {i['title']}" + (f" — {_one_line(i['body'])}" if i["body"] else "")
                     for i in issues)


def parse_pr(ref: str, default_repo: str | None) -> tuple[str, int]:
    """'42', '#42', 'owner/repo#42' or a pull request address → (repo, number)."""
    ref = ref.strip()
    m = re.match(r"https?://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", ref) or re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.fullmatch(r"#?(\d+)", ref)
    if m and default_repo:
        return default_repo, int(m.group(1))
    if m:
        raise ImportProblem("Set the project's GitHub repo in Settings, or write the pull request as owner/repo#42.")
    raise ImportProblem("Write the pull request as 42, owner/repo#42 or its GitHub address.")


async def github_pr(project: models.Project, ref: str) -> tuple[str, str, str, str]:
    repo, number = parse_pr(ref, project.github_repo)
    pr = await get_tracker("github", project, target=repo).pull_request(number)
    content = f"Pull request #{number} ({pr['state']}): {pr['title']}\n\n{pr['body'].strip()}\n\n{pr['diff']}".strip()
    return "diff", f"PR #{number}: {pr['title']}"[:200], _cap(content), pr["url"]


async def github_issues(project: models.Project, labels: str, limit: int) -> tuple[str, str, str, str]:
    wanted = [l.strip() for l in labels.split(",") if l.strip()]
    if not project.github_repo:
        raise ImportProblem("Set the project's GitHub repo in Settings first.")
    issues = await get_tracker("github", project).list_issues(wanted, limit)
    if not issues:
        raise ImportProblem(f"No issues found in {project.github_repo}" + (f" with the label {', '.join(wanted)}." if wanted else "."))
    label = f"GitHub issues: {project.github_repo}" + (f" ({', '.join(wanted)})" if wanted else "") + f", {len(issues)}"
    return "bug_import", label, _cap(_issue_lines(issues)), f"https://github.com/{project.github_repo}/issues"


async def jira_issues(project: models.Project, jql: str, limit: int) -> tuple[str, str, str, str]:
    if not project.jira_project_key:
        raise ImportProblem("Set the project's Jira project key in Settings first.")
    jql = jql.strip() or f'project = "{project.jira_project_key}" AND issuetype = Bug ORDER BY created DESC'
    issues = await get_tracker("jira", project).list_issues(jql, limit)
    if not issues:
        raise ImportProblem(f"No Jira issues match: {jql}")
    return "bug_import", f"Jira issues: {jql}"[:200] + f", {len(issues)}", _cap(_issue_lines(issues)), jql


SAFE_REF = re.compile(r"^[\w./@{}~^-]+$")


def git_diff(repo_path: str, base: str, head: str) -> tuple[str, str, str, str]:
    """git diff base...head (what head adds since it left base) in a repository on this computer."""
    path = Path(repo_path.strip().strip('"')).expanduser()
    if not path.is_dir():
        raise ImportProblem(f"No folder at {path}. Give the path of a Git repository on the computer running Mirmera.")
    base, head = base.strip() or "main", head.strip() or "HEAD"
    for ref in (base, head):
        if not SAFE_REF.match(ref) or ref.startswith("-"):
            raise ImportProblem(f"'{ref}' is not a branch, tag or commit name.")
    try:
        out = subprocess.run(["git", "-C", str(path), "diff", "--no-color", f"{base}...{head}", "--"],
                             capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ImportProblem("Git isn't installed (or not on PATH) on the computer running Mirmera.")
    except subprocess.TimeoutExpired:
        raise ImportProblem("git diff took longer than a minute; try a narrower range.")
    if out.returncode != 0:
        raise ImportProblem(f"git diff failed: {(out.stderr or '').strip().splitlines()[-1] if out.stderr.strip() else 'unknown error'}")
    if not out.stdout.strip():
        raise ImportProblem(f"No changes between {base} and {head}.")
    return "diff", f"git diff {base}...{head} ({path.name})", _cap(out.stdout), f"{path}:{base}...{head}"


__all__ = ["ImportProblem", "TrackerError", "github_pr", "github_issues", "jira_issues", "git_diff", "parse_pr"]
