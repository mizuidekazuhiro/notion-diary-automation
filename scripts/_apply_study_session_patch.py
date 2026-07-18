from pathlib import Path

path = Path("workers/src/index.ts")
text = path.read_text(encoding="utf-8")

import_needle = 'import { ROUTES } from "./http/routes";\n'
import_line = 'import { handleStudySession } from "./application/study_session";\n'
if import_line not in text:
    if text.count(import_needle) != 1:
        raise SystemExit("ROUTES import anchor not found exactly once")
    text = text.replace(import_needle, import_needle + import_line, 1)

route_needle = "        [ROUTES.DAILY_LOG_ENSURE]: () => handleDailyLogEnsure(request, env),\n"
route_line = "        [ROUTES.STUDY_SESSION]: () => handleStudySession(request, env),\n"
if route_line not in text:
    if text.count(route_needle) != 1:
        raise SystemExit("DAILY_LOG_ENSURE route anchor not found exactly once")
    text = text.replace(route_needle, route_needle + route_line, 1)

path.write_text(text, encoding="utf-8")
print("Patched workers/src/index.ts")
