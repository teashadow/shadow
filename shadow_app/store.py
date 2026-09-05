from __future__ import annotations

import difflib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path.home() / ".local" / "share" / "mad" / "shadow"


def _label_dir(label: str) -> Path:
    path = ROOT / label
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _trace_path(label: str) -> Path:
    return _label_dir(label) / f"{_stamp()}.json"


def record_trace(url: str, label: str, prompt: str = "baseline trace request") -> Path:
    started = datetime.now(timezone.utc)
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.post(url, json={"prompt": prompt, "label": label})
    ended = datetime.now(timezone.utc)
    trace = {
        "label": label,
        "timestamp": started.isoformat(),
        "url": url,
        "prompt": prompt,
        "response": response.text,
        "response_time_ms": int((ended - started).total_seconds() * 1000),
        "tool_calls": [],
        "tokens_used": None,
    }
    path = _trace_path(label)
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_traces() -> list[Path]:
    if not ROOT.exists():
        return []
    return sorted(ROOT.glob("*/*.json"))


def load_latest(label: str) -> dict[str, Any]:
    files = sorted((ROOT / label).glob("*.json"))
    if not files:
        raise FileNotFoundError(label)
    return json.loads(files[-1].read_text(encoding="utf-8"))


def diff_traces(left_label: str, right_label: str) -> dict[str, Any]:
    left = load_latest(left_label)
    right = load_latest(right_label)
    if left == right:
        return {"changed": False, "summary": "no changes", "diff": "", "time_delta_ms": 0}
    diff = "\n".join(
        difflib.unified_diff(
            left["response"].splitlines(),
            right["response"].splitlines(),
            fromfile=left_label,
            tofile=right_label,
            lineterm="",
        )
    )
    left_tools = set(left.get("tool_calls", []))
    right_tools = set(right.get("tool_calls", []))
    return {
        "changed": True,
        "summary": "changes detected",
        "diff": diff or "(response differs but no unified diff lines)",
        "new_tools": sorted(right_tools - left_tools),
        "removed_tools": sorted(left_tools - right_tools),
        "time_delta_ms": right.get("response_time_ms", 0) - left.get("response_time_ms", 0),
    }


def export_html(left_label: str, right_label: str) -> Path:
    report = diff_traces(left_label, right_label)
    out = ROOT / f"{left_label}_vs_{right_label}.html"
    # 🔴 Экранируем: diff содержит ответ агента, то есть чужой текст. Без escape ответ,
    # где есть «<script>» (а у агента, который эхоит ввод, он там будет ровно в пробе
    # injection), превратил бы отчёт в исполняемый html. Отчёт о дырах не должен сам быть дырой.
    body = (f"<html><body><h1>shadow diff</h1>"
            f"<p>{html.escape(report['summary'])}</p>"
            f"<pre>{html.escape(report.get('diff', ''))}</pre></body></html>")
    out.write_text(body, encoding="utf-8")
    return out


# ============================================================
#  ДИФФ ПРОГОНОВ БАТАРЕИ — «до/после числом», наш конёк
# ============================================================
# Зачем это, а не diff сырых ответов (доработка Невис 11.08.2026).
# `diff_traces` выше сравнивает текст ответа агента. Для агента на LLM два ответа на один
# запрос различны ВСЕГДА — недетерминизм, ровно то, что `сторож` ловит пробой `repeat`.
# Значит на любом починенном LLM-агенте diff покажет «всё изменилось», и это ложная тревога,
# а не мера. Настоящий «до/после» для диагностики — это разница ВЕРДИКТОВ батареи: какие пробы
# сменили класс. `было провалов 5 → стало 1, починены injection/secret/unicode/badjson,
# регрессий нет` — вот число, которое клиент понимает и которое нельзя подделать красноречием.

# Класс тем хуже, чем он выше: провал важнее внимания, внимание важнее прошедшего.
_ВЕС = {"ПРОВАЛ": 3, "НЕ ПРОВЕРЕНО": 2, "ВНИМАНИЕ": 1, "ПРОШЁЛ": 0}


def _пробы(отчёт: dict[str, Any]) -> dict[str, dict]:
    """id пробы → её запись. Формат — JSON-отчёт `сторож.nim` (ключ 'пробы')."""
    return {п["id"]: п for п in отчёт.get("пробы", [])}


def diff_reports(before_path: str | Path, after_path: str | Path) -> dict[str, Any]:
    """Сравнить два прогона батареи. Возвращает разницу вердиктов, счётчики и движение.

    Не полагается на порядок и на совпадение набора проб: сравнивает по id, а пробы,
    что есть только в одном прогоне, называет отдельно — молчаливого выпадения быть не должно.
    """
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))
    after = json.loads(Path(after_path).read_text(encoding="utf-8"))
    b, a = _пробы(before), _пробы(after)

    все = sorted(set(b) | set(a))
    починены, регрессии, без_изменений, появились, пропали, менявшие = [], [], [], [], [], []
    for id_ in все:
        if id_ not in b:
            появились.append({"id": id_, "класс": a[id_]["класс"]}); continue
        if id_ not in a:
            пропали.append({"id": id_, "класс": b[id_]["класс"]}); continue
        к_было, к_стало = b[id_]["класс"], a[id_]["класс"]
        строка = {"id": id_, "было": к_было, "стало": к_стало,
                  "детектор_после": a[id_].get("детектор", "")}
        if к_было == к_стало:
            без_изменений.append(id_); continue
        менявшие.append(строка)
        if _ВЕС.get(к_стало, 0) < _ВЕС.get(к_было, 0):
            починены.append(строка)
        else:
            регрессии.append(строка)

    счёт = lambda отч, кл: sum(1 for п in отч.get("пробы", []) if п.get("класс") == кл)
    итог = {
        "цель": after.get("цель", before.get("цель")),
        "провалов_было": счёт(before, "ПРОВАЛ"),
        "провалов_стало": счёт(after, "ПРОВАЛ"),
        "внимания_было": счёт(before, "ВНИМАНИЕ"),
        "внимания_стало": счёт(after, "ВНИМАНИЕ"),
        "починены": починены,
        "регрессии": регрессии,
        "появились": появились,
        "пропали": пропали,
        "без_изменений": len(без_изменений),
        # 🔴 честный вердикт: «лучше» — только если провалов стало меньше И ни одна проба
        # не деградировала. Починить одно, сломав другое, — не улучшение, и мы это не прячем.
        "стало_лучше": (счёт(after, "ПРОВАЛ") < счёт(before, "ПРОВАЛ")) and not регрессии,
        "есть_регрессии": bool(регрессии),
    }
    return итог


def export_reports_html(before_path: str | Path, after_path: str | Path,
                        out: str | Path) -> Path:
    """Клиентский отчёт «до/после» — то, что кладётся на первую страницу с именем его агента."""
    d = diff_reports(before_path, after_path)
    цель = html.escape(str((d.get("цель") or {}).get("имя", "агент")))
    строки = []
    for гр, титул, знак in ((d["починены"], "Починено", "✔"), (d["регрессии"], "Регрессия", "🔴")):
        for с in гр:
            строки.append(f"<tr><td>{знак}</td><td>{html.escape(с['id'])}</td>"
                          f"<td>{html.escape(с['было'])} → {html.escape(с['стало'])}</td>"
                          f"<td>{html.escape(с.get('детектор_после',''))}</td></tr>")
    вердикт = ("стало лучше" if d["стало_лучше"]
               else "есть регрессии — смотреть" if d["есть_регрессии"]
               else "без улучшения")
    body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Диагностика агента — до/после</title></head><body>
<h1>Диагностика: {цель}</h1>
<p><b>Провалов: {d['провалов_было']} → {d['провалов_стало']}</b> ·
внимания: {d['внимания_было']} → {d['внимания_стало']} · вердикт: <b>{html.escape(вердикт)}</b></p>
<table border="1" cellpadding="6" cellspacing="0">
<tr><th></th><th>проба</th><th>вердикт</th><th>что видит код</th></tr>
{''.join(строки) or '<tr><td colspan=4>ни одна проба не сменила класс</td></tr>'}
</table>
<p style="color:#666;font-size:0.9em">Вердикт ставит код, не модель. Чистая батарея не значит
«агент безупречен» — только что он выдержал перечисленные пробы.</p>
</body></html>"""
    Path(out).write_text(body, encoding="utf-8")
    return Path(out)
