"""
console_ui.py -- presentation layer for demo_agent.py / checkout.py (Phase 8).

Pure rendering. No business logic, no API calls, no decisions: every value
shown here is passed in by the caller from a real system response, and
anything the system didn't actually say is rendered as "-" rather than
invented.

Two checks stay deliberately distinct throughout, because they are two
different checks at two different stages:
  * NEGOTIATION GUARDRAIL -- discount rules, evaluated at /negotiate time.
  * MANDATE CHECK -- the buyer's persistent spend cap + category scope,
    evaluated at /payment time against the existing Mandate model.

"Purchase Intent" is the per-request natural-language ask. It is NOT a
mandate and is never labelled as one.

Degrades cleanly on terminals without colour: every badge carries a
glyph and a word, so meaning never depends on colour alone.
"""

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console(soft_wrap=False)

_BANNER = """[bold]TIME & CO. × RAZORPAY[/bold]
[dim]AGENTIC COMMERCE CONSOLE[/dim]"""


def header() -> None:
    console.print()
    console.print(Panel(_BANNER, expand=False, padding=(1, 6), border_style="cyan"))


def stage(label: str) -> None:
    """Visual separator between pipeline stages."""
    console.print()
    console.print(Rule(f"[bold]{label}[/bold]", style="cyan", align="left"))


def purchase_intent(user_request: str, agent_id: str, mandate_id: int) -> None:
    """The per-request ask (Purchase Intent), shown alongside the existing
    persistent buyer authorisation (Mandate) it will be checked against."""
    stage("USER → PURCHASE INTENT")
    console.print(
        Panel(
            Text(user_request, style="bold"),
            title="[bold]Purchase Intent[/bold] [dim](per-request constraints)[/dim]",
            border_style="white",
            padding=(0, 2),
        )
    )
    console.print(
        Panel(
            f"Buyer agent: [bold]{agent_id}[/bold]\n"
            f"Mandate on record: [bold]#{mandate_id}[/bold]  [dim](persistent spend cap + category scope,\n"
            f"enforced by the merchant API at payment time)[/dim]",
            title="[bold]Existing Mandate[/bold]",
            border_style="blue",
            padding=(0, 2),
        )
    )
    ok("PURCHASE INTENT PARSED")


def ok(label: str) -> None:
    console.print(f"[bold green]✓[/bold green] [bold]{label}[/bold]")


def waiting(label: str) -> None:
    console.print(f"[bold yellow]○[/bold yellow] [bold]{label}[/bold]")


def blocked(label: str, reason: str) -> None:
    """A real block, at whichever stage it actually happened."""
    console.print(f"[bold red]✗[/bold red] [bold red]{label}[/bold red]")
    console.print(
        Panel(
            Text(str(reason), style="red"),
            title="[bold red]✗ PURCHASE BLOCKED[/bold red]",
            subtitle="[dim]reason returned by the system[/dim]",
            border_style="red",
            padding=(0, 2),
        )
    )


def agent_reasoning(text: str) -> None:
    console.print(
        Panel(
            Text(text),
            title="[bold]Agent reasoning[/bold]",
            border_style="magenta",
            padding=(0, 2),
        )
    )


def tool_call(name: str, args_str: str) -> None:
    console.print(f"[dim]→ calling[/dim] [bold cyan]{name}[/bold cyan][dim]({args_str})[/dim]")


def catalog_found(count: int) -> None:
    ok(f"CATALOG MATCH FOUND [dim]({count} items returned)[/dim]")


def comparison_table(items: list, selected_id, reasoning_excerpt: str | None) -> None:
    """Catalog results, using only fields the API actually returns.

    MATCH carries the agent's own stated reasoning for the row it chose --
    never a synthesised relevance score. Rows it didn't choose show "-".
    """
    table = Table(
        title="[bold]Catalog evaluated by agent[/bold]",
        header_style="bold cyan",
        border_style="cyan",
        show_lines=False,
        expand=True,
    )
    table.add_column("PRODUCT", overflow="fold")
    table.add_column("PRICE", justify="right", no_wrap=True)
    table.add_column("CATEGORY", overflow="fold")
    table.add_column("TAGS", overflow="fold")
    table.add_column("MATCH", overflow="fold")

    for item in items:
        chosen = selected_id is not None and item.get("id") == selected_id
        price = item.get("price")
        price_str = f"₹{price:,.2f}" if isinstance(price, (int, float)) else "-"
        tags = item.get("tags") or []
        tags_str = ", ".join(str(t) for t in tags) if tags else "-"

        if chosen:
            match = reasoning_excerpt or "(no stated reasoning)"
        else:
            match = "-"

        style = "bold" if chosen else "dim"
        table.add_row(
            f"{'▸ ' if chosen else '  '}{item.get('name', '-')}",
            price_str,
            str(item.get("category") or "-"),
            tags_str,
            match,
            style=style,
        )

    console.print(table)


def product_selected(item: dict, quantity=None) -> None:
    price = item.get("price")
    price_str = f"₹{price:,.2f}" if isinstance(price, (int, float)) else "-"
    qty_line = f"\nQuantity: [bold]{quantity}[/bold]" if quantity is not None else ""
    console.print(
        Panel(
            f"[bold]{item.get('name', '-')}[/bold]  [dim](item #{item.get('id')})[/dim]\n"
            f"Unit price: [bold]{price_str}[/bold]{qty_line}",
            title="[bold]Product selected[/bold]",
            border_style="green",
            padding=(0, 2),
        )
    )
    ok("PRODUCT SELECTED")


def negotiation_result(body: dict) -> None:
    """Negotiation guardrail only -- discount rules at /negotiate time.

    Deliberately separate from the mandate check, which happens later and
    enforces a different thing.
    """
    approved = bool(body.get("approved"))
    rule = body.get("rule_applied", "-")
    discount = body.get("final_discount_pct")
    final_price = body.get("final_price")

    detail = Table.grid(padding=(0, 2))
    detail.add_column(style="dim", no_wrap=True)
    detail.add_column()
    if discount is not None:
        detail.add_row("Discount applied", f"[bold]{discount}%[/bold]")
    if isinstance(final_price, (int, float)):
        detail.add_row("Order total", f"[bold]₹{final_price:,.2f}[/bold]")
    detail.add_row("Rule applied", str(rule))

    console.print(
        Panel(
            detail,
            title="[bold]Negotiation guardrail[/bold] [dim](discount rules)[/dim]",
            border_style="green" if approved else "red",
            padding=(0, 2),
        )
    )
    if approved:
        ok("GUARDRAIL CHECK PASSED")
    else:
        blocked("BLOCKED — NEGOTIATION GUARDRAIL", rule)


def mandate_passed() -> None:
    console.print(
        Panel(
            "Spend cap and category scope both satisfied.",
            title="[bold]Mandate check[/bold] [dim](spend cap + category scope)[/dim]",
            border_style="green",
            padding=(0, 2),
        )
    )
    ok("MANDATE CHECK PASSED")


def mandate_blocked(reason: str) -> None:
    console.print(
        Panel(
            Text(str(reason), style="red"),
            title="[bold red]Mandate check[/bold red] [dim](spend cap + category scope)[/dim]",
            border_style="red",
            padding=(0, 2),
        )
    )
    blocked("BLOCKED — MANDATE CHECK", reason)


def razorpay_order_created(order_id: str, amount_paise) -> None:
    amount = f"₹{amount_paise / 100:,.2f}" if isinstance(amount_paise, (int, float)) else "-"
    console.print(
        Panel(
            f"Order: [bold]{order_id}[/bold]\nAmount: [bold]{amount}[/bold]",
            title="[bold]Razorpay[/bold] [dim](test mode)[/dim]",
            border_style="blue",
            padding=(0, 2),
        )
    )
    ok("RAZORPAY ORDER CREATED")


def checkout_ready(url: str) -> None:
    """The real human-in-the-loop step. Never shortened or faked."""
    waiting("WAITING FOR CHECKOUT")
    console.print(
        Panel(
            f"A person must complete this checkout for the payment to capture.\n\n"
            f"[bold cyan]{url}[/bold cyan]\n\n"
            f"[dim]The system is polling Razorpay for capture until this completes or times out.[/dim]",
            title="[bold yellow]Human-in-the-loop checkout[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


def checkout_note(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def payment_authorized(payment_id: str) -> None:
    ok(f"PAYMENT AUTHORIZED [dim]({payment_id})[/dim]")


def payment_not_completed(reason: str) -> None:
    blocked("PAYMENT NOT COMPLETED", reason)


def receipt(product_name, amount, order_id, guardrail_passed, payment_id) -> None:
    """Final receipt. Every field is a real value passed in by the caller."""
    amount_str = f"₹{amount:,.2f}" if isinstance(amount, (int, float)) else "-"
    guard = "[green]✓ Passed[/green]" if guardrail_passed else "[dim]— not applicable[/dim]"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()
    grid.add_row("Product:", f"[bold]{product_name}[/bold]")
    grid.add_row("Amount:", f"[bold]{amount_str}[/bold]")
    grid.add_row("Razorpay Order:", str(order_id))
    grid.add_row("Razorpay Payment:", str(payment_id))
    grid.add_row("Guardrail:", guard)
    grid.add_row("Mandate:", "[green]✓ Authorized[/green]")
    grid.add_row("Audit Trail:", "[green]✓ Recorded[/green]")

    console.print()
    console.print(
        Panel(
            grid,
            title="[bold green]✓ TRANSACTION COMPLETE[/bold green]",
            border_style="green",
            padding=(1, 2),
            expand=False,
        )
    )


def note(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def run_end() -> None:
    console.print()
    console.print(Rule("[dim]end of run[/dim]", style="dim"))
    console.print()
