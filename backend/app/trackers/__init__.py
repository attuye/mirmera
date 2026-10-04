"""Issue trackers: get_tracker(name, project) returns a ready tracker or says
exactly what is missing."""
from app import models
from app.config import settings
from app.trackers.base import (  # noqa: F401
    TRACKER_NAMES, IssueTracker, TrackerError, TrackerIssue, TrackerNotConfigured, UnknownTracker, keywords,
)


def missing_settings(name: str, project: models.Project) -> list[str]:
    """What still has to be set before this project can file to this tracker."""
    if name == "github":
        needed = {"GITHUB_TOKEN in backend/.env": settings.github_token,
                  "the project's GitHub repo": project.github_repo}
    elif name == "jira":
        needed = {"JIRA_BASE_URL in backend/.env": settings.jira_base_url,
                  "JIRA_EMAIL in backend/.env": settings.jira_email,
                  "JIRA_API_TOKEN in backend/.env": settings.jira_api_token,
                  "the project's Jira project key": project.jira_project_key}
    else:
        raise UnknownTracker(f"Unknown tracker '{name}'. Use one of: {', '.join(TRACKER_NAMES)}")
    if settings.mock_trackers:  # only the project target matters for the mock
        needed = {k: v for k, v in needed.items() if k.startswith("the project")}
    return [k for k, v in needed.items() if not v]


def get_tracker(name: str, project: models.Project, transport=None, target: str | None = None) -> IssueTracker:
    """target: a repo or project key other than the project's own (an issue filed before it changed)."""
    missing = missing_settings(name, project)
    if target:  # the project's own target doesn't matter then
        missing = [m for m in missing if not m.startswith("the project")]
    if missing:
        raise TrackerNotConfigured(f"{name} is not set up yet. Missing: {', '.join(missing)}.")
    target = target or (project.github_repo if name == "github" else project.jira_project_key)
    if settings.mock_trackers:
        from app.trackers.mock import MockTracker
        return MockTracker(name, target)
    if name == "github":
        from app.trackers.github import GitHubTracker
        return GitHubTracker(target, settings.github_token, settings.github_api_url, transport=transport)
    from app.trackers.jira import JiraTracker
    return JiraTracker(settings.jira_base_url, settings.jira_email, settings.jira_api_token,
                       target, settings.jira_issue_type, transport=transport)
