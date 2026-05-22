# Test fixtures

**Do not commit captured GUAP HTML** — it may contain personal data, session tokens, or task titles.

## Default (in repo)

Tests use **synthetic** HTML embedded in `tests/conftest.py` (anonymous subjects, fake task IDs).

## Optional local captures (gitignored)

After `lab-auto auth login`, refresh local-only fixtures:

```bash
python scripts/capture_guap_fixtures.py --task-url "https://pro.guap.ru/inside/student/tasks/<id>"
```

Writes `task_list.html` and `task_detail.html` here. These files are listed in `.gitignore` and are never pushed.

When present, tests prefer local files over synthetic HTML.
