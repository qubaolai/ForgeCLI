# Repository Guidelines

## Project Structure & Module Organization

ForgeCLI is currently in a design-first MVP phase. The root contains `README.md`, `LICENSE`, `.gitignore`, and planning/design material under `docs/`.

Key documentation:

- `docs/01-overview-design.md`: product and architecture overview.
- `docs/02-detailed-design.md`: module boundaries, domain model, storage, tools, and workflow design.
- `docs/03-delivery-plan.md`: phase goals and milestones.
- `docs/04-engineering-standards.md`: engineering process and quality rules.
- `docs/roadmap/mvp/<date>/README.md`: daily MVP development goals and acceptance criteria.

When implementation begins, follow the planned structure in `docs/02-detailed-design.md`: `src/forgecli` plus `tests`. Create only the directories needed for the current MVP phase.

## Build, Test, and Development Commands

No executable Python package exists yet. Until `pyproject.toml` is added, documentation-only changes require no build.

Planned commands once the project is initialized:

- `forge --help`: verify the CLI entrypoint.
- `poetry install`: install project dependencies.
- `poetry run pytest`: run tests.
- `poetry run ruff check .`: run lint checks.
- `poetry run ruff format .`: format Python code.
- `poetry run mypy src/forgecli`: run type checks.

Document any command changes in `docs/roadmap` and the relevant design document.

## Coding Style & Naming Conventions

Target Python 3.13+ with Poetry. Use typed Python, small modules, and clear domain names. Keep `domain` independent of CLI, SDK, filesystem, and MCP implementations. Use `snake_case` for modules/functions, `PascalCase` for classes, and explicit names such as `SessionService`, `ToolRuntime`, and `ModePolicyResolver`.

Do not place tool orchestration directly in `infrastructure`; use the dedicated `tools` layer described in the detailed design.

## Testing Guidelines

Use `pytest` for unit, integration, and E2E tests. Domain logic should be tested without external services. Mock model providers, MCP servers, shell execution, and filesystem side effects where practical. Name tests `test_<behavior>.py` and keep fixtures under `tests/fixtures`.

MVP quality targets are defined in `docs/04-engineering-standards.md`.

## Commit & Pull Request Guidelines

The current history only contains `Initial commit`; use Conventional Commits going forward, for example `docs: add mvp roadmap` or `feat: add session event store`.

PRs should include a short summary, linked issue or roadmap date, test results, affected docs, and any design deviations. Security-sensitive changes must call out approval, storage, or command-execution impact.

## Agent-Specific Instructions

Follow the daily plan in `docs/roadmap/mvp`. Prefer guidance, review, and validation over unrequested implementation. Keep changes scoped to the current phase and update docs when behavior or architecture decisions change. Bare `forge` activates an interactive workspace conversation; slash commands like `/config` should reuse application services instead of duplicating CLI-layer business logic.
