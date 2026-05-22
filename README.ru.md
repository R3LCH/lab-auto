# 🎓 guap-lab-auto

**Неофициальный CLI для студентов ГУАП** — синхронизация заданий с [pro.guap.ru](https://pro.guap.ru), зеркалирование в локальные папки, состояние в YAML, конвертация DOCX → PDF и отправка отчётов через Playwright.

В репозитории есть **[AgentSkills](https://agentskills.io/) skill** (`guap-lab-workflow`): кодинг-агенты ([OpenClaw](https://documentation.openclaw.ai/), [Hermes](https://hermes-agent.nousresearch.com/), Cursor и др.) вызывают **`lab-auto`** в вашем workspace, а не парсят GUAP в браузере сами.

[English](README.md) · [Русский](README.ru.md)

> ⚠️ Не связан с ГУАП. Вёрстка портала может меняться и ломать парсеры. Используйте на свой риск.

| | |
|---|---|
| 📦 **PyPI** | `guap-lab-auto` |
| ⌨️ **Команда** | `lab-auto` |
| 🤖 **Skill для агентов** | `guap-lab-workflow` → `skills/guap-lab-workflow/` |
| 🐍 **Python** | 3.11+ |
| 🌐 **Портал** | `https://pro.guap.ru/inside/student/tasks/` |

---

## ✨ Возможности

- 🔄 **Sync** — список заданий (100 на страницу), обновление `state/works.yaml`, переименование `labs/<предмет>/[STATUS] …/`
- 📥 **Загрузки** — `task.pdf` (задание); `reports/site-report-<id>.pdf` при первом sync, если отчёт на GUAP (`не принят` / `ожидает проверки` / `принят`)
- 🏷️ **Статусы** — метки GUAP → `[UNDONE]` / `[REFACTOR]` / `[SENT]` / `[DONE]` / `[UNKNOWN]`; локально `[REVIEW]` / `[SENTFAILED]`
- 📋 **State** — `works.yaml`, `summary.md`, `needs_review.md`, логи
- 🔐 **Сессия** — Fernet-шифрование Playwright `storage_state`; SSO через `auth login` (видимый браузер)
- 📄 **Convert** — DOCX → PDF (LibreOffice / Word в Windows)
- 📤 **Submit** — загрузка PDF на странице задания после `[REVIEW]`
- 🗄️ **Archive** — `sync --archive` или `archive` / `unarchive` для заданий, пропавших с сайта
- 🤖 **Skill в репозитории** — [`skills/guap-lab-workflow/`](skills/guap-lab-workflow/SKILL.md): команды, структура workspace, правила для агентов

---

## 🤖 Agent skill (`guap-lab-workflow`)

CLI — единственный поддерживаемый интерфейс для автоматизации. Skill описывает **какие подкоманды `lab-auto` запускать**, где лежат `works.yaml` и папки лаб, когда нужен вход человека или явное подтверждение (например, без самостоятельного `submit`).

| Путь | Назначение |
|------|------------|
| [`skills/guap-lab-workflow/SKILL.md`](skills/guap-lab-workflow/SKILL.md) | Основной skill — workflow, требования, правила |
| [`skills/guap-lab-workflow/references/`](skills/guap-lab-workflow/references/) | Шпаргалка по командам, OpenClaw/Hermes |
| [`.agents/skills/guap-lab-workflow/`](.agents/skills/guap-lab-workflow/) | Копия для Cursor и совместимых загрузчиков |
| [`AGENTS.md`](AGENTS.md) | Гайд для агентов/мейнтейнеров репозитория |
| [`docs/integrations/`](docs/integrations/) | Настройка OpenClaw и Hermes |

**Для агентов нужно:** `lab-auto` в `PATH`, Chromium (`playwright install chromium`), настроенный workspace. Skill не заменяет CLI — он им управляет.

### Установка skill

```bash
# Linux / macOS — копия в workspace OpenClaw и ~/.hermes/skills/
./scripts/install-agent-skills.sh

# Windows
.\scripts\install-agent-skills.ps1
```

Или вручную: symlink / копия `skills/guap-lab-workflow/` в каталог skills агента.

| Агент | Вызов после установки |
|-------|------------------------|
| OpenClaw | `/guap-lab-workflow` |
| Hermes | `/guap-lab-workflow` или `hermes -s guap-lab-workflow` |
| Cursor | через `.agents/skills/` или правила проекта на skill |

Примеры конфигов: [`examples/openclaw.json.example`](examples/openclaw.json.example), [`examples/hermes-skills.example.yaml`](examples/hermes-skills.example.yaml).

---

## 🚀 Быстрый старт

### Установка

```bash
pip install guap-lab-auto
playwright install chromium
```

```bash
uv tool install guap-lab-auto
playwright install chromium
```

### Первый запуск

```bash
lab-auto workspace set ~/guap-labs
lab-auto auth login
lab-auto auth check
lab-auto sync
lab-auto status
```

`auth login` открывает видимое окно Chromium; `sync`, `submit` и `auth check` работают headless.

---

## 📁 Workspace

Данные пользователя — вне пакета:

```
workspace/
├── labs/
│   └── <предмет>/
│       └── [SENT] Название лабы [178541]/
│           ├── task.pdf
│           └── reports/
│               └── site-report-5283063.pdf
├── state/
│   ├── works.yaml
│   ├── summary.md
│   └── needs_review.md
├── session/
│   └── storage_state.json    # зашифрован
└── logs/
```

**Корень workspace:** `--root` → `LAB_AUTO_ROOT` → `lab-auto workspace set` → текущая директория.

| Платформа | Конфиг |
|-----------|--------|
| Windows | `%APPDATA%\lab-auto\config.yaml` |
| Linux / macOS | `~/.config/lab-auto/config.yaml` |

Ключ сессии: `%APPDATA%\lab-auto\session.key` или `~/.config/lab-auto/session.key`.

---

## ⌨️ Команды

Справка Typer: `lab-auto --help`, `lab-auto auth --help`, `lab-auto <command> --help`.

**Глобально:** `--root` / `LAB_AUTO_ROOT`, `-v` / `--verbose`.

### 📂 Workspace

| Команда | Описание |
|---------|----------|
| `workspace set <path>` | Сохранить workspace по умолчанию |
| `workspace show` | Активный и сохранённый workspace, путь к конфигу |
| `workspace unset` | Сбросить сохранённый workspace |

### 🔑 Auth

| Команда | Описание |
|---------|----------|
| `auth login` | SSO в видимом браузере; сохранить зашифрованную сессию |
| `auth check` | Headless: открывается ли список заданий |
| `auth import-cookie <file>` | Импорт Playwright storage JSON или списка cookies |
| `auth migrate-session` | Перевод старой plaintext-сессии в encrypted |
| `auth logout` | Удалить `session/storage_state.json` |

### 🔄 Workflow

| Команда | Описание |
|---------|----------|
| `sync` | Список + детали; merge `works.yaml`; скачать недостающие PDF |
| `sync --archive` | Удалённые с сайта → `archived: true`, не выкидывать из state |
| `status` | Активные работы по предметам |
| `status --all` | Включая архив |
| `review <id>` | Локальный статус `[REVIEW]`, переименование папки |
| `convert <id> --docx PATH` | DOCX → PDF (`--output` опционально) |
| `submit <id> --file PATH` | Загрузка PDF; `[SENT]` или `[SENTFAILED]` |
| `archive <id>` | `archived: true`, скрыть из обычного `status` |
| `unarchive <id>` | Вернуть в активный список |

`<id>`: `task-178541` или уникальная часть названия папки.

**Типичная цепочка:** `sync` → отчёт в папке → `review` → `convert` → `submit` → `sync`.

---

## 🏷️ Префиксы папок

| Префикс | GUAP / локально |
|---------|------------------|
| `[UNDONE]` | В колонке статуса `—` (не сдано) |
| `[REFACTOR]` | `не принят` |
| `[SENT]` | `ожидает проверки` |
| `[DONE]` | `принят` |
| `[UNKNOWN]` | Любой другой статус на портале |
| `[REVIEW]` | Только локально — готово к вашей проверке |
| `[SENTFAILED]` | Только локально — последний `submit` не удался |

**ID:** `work_id` = `task-<site_id>`; папка = `[STATUS] <название> [<site_id>]`.

`sync` переименовывает папки при смене статуса. Импорт отчёта с сайта — один раз на работу, если `reports/` пуст и на GUAP `не принят`, `ожидает проверки` или `принят`.

---

## 🔐 Сессия

- Пароль запрашивается в `auth login` и не сохраняется на диск.
- Cookies: `workspace/session/storage_state.json` (encrypted-обёртка).
- `auth migrate-session` — апгрейд старых plaintext-экспортов.

---

## 🧪 Разработка

```bash
git clone https://github.com/R3LCH/lab_auto.git
cd lab_auto
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Unix:    source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
pytest
```

Live-тесты (сеть + сохранённая сессия):

```bash
export LAB_AUTO_ROOT=~/guap-labs
pytest -m live
```

### Fixtures

По умолчанию: синтетический HTML в `tests/conftest.py`. Локальный захват (в `.gitignore`):

```bash
python scripts/capture_guap_fixtures.py --task-url "https://pro.guap.ru/inside/student/tasks/<id>"
```

Не коммитьте `tests/fixtures/task_list.html` и `task_detail.html`. См. [tests/fixtures/README.md](tests/fixtures/README.md).

🤖 Исходники skill: `skills/guap-lab-workflow/` — при изменении CLI смотрите [AGENTS.md](AGENTS.md).

---

## 📜 Лицензия

MIT — [LICENSE](LICENSE).
