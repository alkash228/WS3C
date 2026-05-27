# INF

Справочники для WEB_samv:

- `buildings.json` — список зданий
- `contractors.json` — список подрядчиков
- `contracts.json` — договоры подряда
- `api_prompt.json` — общий промпт для SAM API (`/infer/video`)
- `scenarios.json` — сценарии анализатора (цепочка main + linked, только для анализа масок)

Формат `buildings.json`, `contractors.json`, `contracts.json`: JSON-массив строк UTF-8.

Формат `api_prompt.json`: `{ "prompt": "human . vest . ..." }` — один раз на все прогоны.

Формат `scenarios.json`: `scenarios[]` с полями `id`, `title`, `prompt` (цепочка анализатора), `enabled`. При обработке видео: один инференс по `api_prompt.json`, затем включённые сценарии анализируются по очереди, а итог объединяется в единый `analysis/analyzer_result.json`.
