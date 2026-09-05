from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .banner import SHADOW_BANNER
from .store import (
    diff_reports,
    diff_traces,
    export_html,
    export_reports_html,
    list_traces,
    record_trace,
)

console = Console()


def _banner() -> None:
    console.print(f"[bold blue]{SHADOW_BANNER}[/bold blue]")


class BannerGroup(click.Group):
    def get_help(self, ctx: click.Context) -> str:
        _banner()
        return super().get_help(ctx)


@click.group(cls=BannerGroup)
def main() -> None:
    """MAD trace diff utility."""


@main.command("record")
@click.argument("url")
@click.option("--label", required=True)
@click.option("--prompt", default="baseline trace request", show_default=True)
def record_cmd(url: str, label: str, prompt: str) -> None:
    path = record_trace(url, label, prompt=prompt)
    console.print(f"[green]Recorded[/green] {path}")


@main.command("list")
def list_cmd() -> None:
    table = Table(title="shadow traces")
    table.add_column("Label")
    table.add_column("File")
    for path in list_traces():
        table.add_row(path.parent.name, str(path))
    console.print(table)


@main.command("diff")
@click.argument("left_label")
@click.argument("right_label")
def diff_cmd(left_label: str, right_label: str) -> None:
    report = diff_traces(left_label, right_label)
    if not report["changed"]:
        console.print("no changes")
        return
    console.print(report["summary"])
    if report.get("diff"):
        console.print(report["diff"])
    console.print(f"time delta ms: {report['time_delta_ms']}")


@main.command("export")
@click.argument("left_label")
@click.argument("right_label")
@click.option("--html", "as_html", is_flag=True)
def export_cmd(left_label: str, right_label: str, as_html: bool) -> None:
    if not as_html:
        raise click.ClickException("use --html")
    path: Path = export_html(left_label, right_label)
    console.print(path)


@main.command("battery-diff")
@click.argument("before_json", type=click.Path(exists=True))
@click.argument("after_json", type=click.Path(exists=True))
@click.option("--html", "html_out", type=click.Path(), default=None,
              help="также сохранить клиентский HTML-отчёт по этому пути")
def battery_diff_cmd(before_json: str, after_json: str, html_out: str | None) -> None:
    """До/после ЧИСЛОМ: сравнить два прогона батареи (JSON-отчёты `сторож`).

    Не diff сырых ответов (у LLM они различны всегда — шум), а разница ВЕРДИКТОВ:
    какие пробы починены, какие деградировали.
    """
    d = diff_reports(before_json, after_json)
    цель = (d.get("цель") or {}).get("имя", "агент")
    console.print(f"[bold]Диагностика: {цель}[/bold]")
    console.print(f"провалов: [red]{d['провалов_было']}[/red] → "
                  f"[green]{d['провалов_стало']}[/green] · "
                  f"внимания: {d['внимания_было']} → {d['внимания_стало']}")

    table = Table(show_header=True)
    table.add_column(""); table.add_column("проба"); table.add_column("вердикт")
    table.add_column("что видит код")
    for с in d["починены"]:
        table.add_row("[green]✔[/green]", с["id"], f"{с['было']} → {с['стало']}",
                      с.get("детектор_после", ""))
    for с in d["регрессии"]:
        table.add_row("[red]🔴[/red]", с["id"], f"{с['было']} → {с['стало']}",
                      с.get("детектор_после", ""))
    for с in d["появились"]:
        table.add_row("[yellow]+[/yellow]", с["id"], f"новая: {с['класс']}", "")
    for с in d["пропали"]:
        table.add_row("[yellow]−[/yellow]", с["id"], f"пропала: была {с['класс']}", "")
    if d["починены"] or d["регрессии"] or d["появились"] or d["пропали"]:
        console.print(table)
    else:
        console.print("ни одна проба не сменила класс")

    if d["стало_лучше"]:
        console.print("[bold green]вердикт: стало лучше[/bold green]")
    elif d["есть_регрессии"]:
        console.print("[bold red]вердикт: есть регрессии — смотреть[/bold red]")
    else:
        console.print("[bold]вердикт: без улучшения[/bold]")

    if html_out:
        path = export_reports_html(before_json, after_json, html_out)
        console.print(f"HTML-отчёт: {path}")

    # код возврата: регрессия — это провал доводки, batery-diff можно ставить в CI
    if d["есть_регрессии"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
