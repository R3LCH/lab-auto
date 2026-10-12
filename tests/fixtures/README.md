# Test fixtures

**Do not commit captured GUAP HTML** — it may contain personal data, session tokens, or task titles.

## Default (in repo)

Tests use **synthetic** HTML embedded in `tests/conftest.py` (anonymous subjects, fake task IDs).

`description_only.html` and `description_with_pdf.html` are anonymous description
fixtures. The `h5` / `p.task-description-block` structure was verified against a
local GUAP capture; text and links are synthetic. Richer block/list markup is
covered inline in `test_description.py`. Raw captures remain local and ignored.

`subject_materials.html` is an anonymous reconstruction of the table structure
from a browser-saved `/inside/student/materials` page. It preserves the real
headers, link classes and URL shapes while replacing names, teachers and external
URLs. The raw saved page is never committed.

`timetable_index.html` and `timetable_group_1232.html` preserve the public
`guap.ru/rasp` group selector and lesson structure captured without login.
Teacher names and teacher IDs are replaced with synthetic identities; scripts,
trackers and unrelated navigation are removed. Raw captures remain local.
Group numbers and timetable IDs deliberately differ. Tests cover a lecturer
different from the laboratory teacher, practice fallback and shared-group lessons.

## Optional local captures (gitignored)

After `lab-auto auth login`, refresh local-only fixtures:

```bash
python scripts/capture_guap_fixtures.py --task-url "https://pro.guap.ru/inside/student/tasks/<id>"
```

Writes `task_list.html` and `task_detail.html` here. The capture script opens the list with **perPage=100** so all tasks on one page are included. These files are listed in `.gitignore` and are never pushed.

When present, tests prefer local files over synthetic HTML.
